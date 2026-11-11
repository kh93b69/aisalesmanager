"use client";

// Настройки → База знаний: загрузка файла для всех городов или для конкретного города + сводка.

import { useEffect, useRef, useState } from "react";
import { apiRequest, Bot, City, errorText } from "../api";

type Props = {
  bot: Bot;
  cities: City[];
  onCitiesChanged: () => void;
};

type Summary = {
  general: number;
  by_city: Record<string, number>;
};

export default function KnowledgeSection({ bot, cities, onCitiesChanged }: Props) {
  // "" — общая база для всех городов, иначе id города
  const [target, setTarget] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const loadSummary = () => {
    apiRequest<Summary>(`/api/bots/${bot.id}/knowledge/summary`)
      .then((data) => setSummary(data))
      .catch((err) => setError(errorText(err)));
  };

  // Первая загрузка сводки. cancelled — чтобы ответ для старого бота не попал к новому
  useEffect(() => {
    let cancelled = false;
    setTarget("");
    setResult("");
    setError("");
    setSummary(null);
    apiRequest<Summary>(`/api/bots/${bot.id}/knowledge/summary`)
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch((err) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [bot.id]);

  const targetName = target ? cities.find((c) => c.id === target)?.name || "город" : "Общая (для всех городов)";

  const upload = async (file: File) => {
    setUploading(true);
    setResult("");
    setError("");
    const formData = new FormData();
    formData.append("file", file);
    try {
      const data = await apiRequest<{ chunks_count: number; format: string }>(
        `/api/bots/${bot.id}/knowledge?city_id=${encodeURIComponent(target)}`,
        { method: "POST", body: formData }
      );
      setResult(`«${file.name}» загружен в базу «${targetName}»: ${data.chunks_count} фрагментов`);
      loadSummary();
      onCitiesChanged();
    } catch (err) {
      setError(errorText(err));
    }
    setUploading(false);
    // Сбрасываем поле, чтобы можно было загрузить тот же файл ещё раз
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className="settings-section">
      <h3>База знаний</h3>
      <p>
        Загрузите прайс, описание товаров и услуг, условия доставки и оплаты, ответы на частые вопросы.
        Лучше всего Excel/CSV: одна строка — один товар или услуга с ценой. Если городов нет — загружайте в «Общую».
        База города — только для клиентов этого города (адреса, цены, наличие в этом городе).
      </p>

      <div className="form-group">
        <label>Для какой базы загружаем</label>
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">Общая (для всех городов)</option>
          {cities.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>

      <div className={`upload-area ${uploading ? "disabled" : ""}`} onClick={() => !uploading && fileRef.current?.click()}>
        <div style={{ fontSize: 28, marginBottom: 6, opacity: 0.5 }}>{uploading ? "⏳" : "+"}</div>
        <div style={{ fontSize: 14 }}>
          {uploading ? "Загружаем и разбиваем на фрагменты…" : `Загрузить файл в «${targetName}»`}
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>Форматы: TXT, PDF, CSV, XLSX (старый .xls пересохраните в .xlsx)</div>
        <input
          ref={fileRef}
          type="file"
          accept=".txt,.pdf,.csv,.xlsx"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
          }}
        />
      </div>
      <div className="hint">Новый файл заменяет прежнюю базу выбранного города (или общую). Другие города не меняются.</div>

      {result && <div className="alert alert-success" style={{ marginTop: 12 }}>✓ {result}</div>}
      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}

      <div className="kb-summary">
        <div className="kb-summary-title">Что сейчас в базе</div>
        {!summary ? (
          <div className="text-muted"><span className="spinner" /> Загружаем…</div>
        ) : (
          <div className="kb-summary-list">
            <div className="kb-summary-row">
              <span>🌐 Общая</span>
              <b className={summary.general ? "" : "text-muted"}>{summary.general || "пусто"}</b>
            </div>
            {cities.map((c) => {
              const count = summary.by_city?.[c.id] || 0;
              return (
                <div key={c.id} className="kb-summary-row">
                  <span>📍 {c.name}</span>
                  <b className={count ? "" : "text-muted"}>{count || "пусто"}</b>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
