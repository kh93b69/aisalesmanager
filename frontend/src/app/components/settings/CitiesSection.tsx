"use client";

// Настройки → Города: список филиалов (добавить, изменить, удалить).

import { useState } from "react";
import { apiDelete, apiPost, apiPut, Bot, City, errorText } from "../api";

type Props = {
  bot: Bot;
  cities: City[];
  onCitiesChanged: () => void;
};

// Поля формы города
type CityForm = {
  name: string;
  aliases: string;
  address: string;
  telegram_chat_id: string;
  sort_order: string;
};

const EMPTY_FORM: CityForm = { name: "", aliases: "", address: "", telegram_chat_id: "", sort_order: "0" };

function cityToForm(city: City): CityForm {
  return {
    name: city.name,
    aliases: city.aliases || "",
    address: city.address || "",
    telegram_chat_id: city.telegram_chat_id || "",
    sort_order: String(city.sort_order || 0),
  };
}

// Тело запроса для API
function formToBody(form: CityForm) {
  return {
    name: form.name.trim(),
    aliases: form.aliases.trim(),
    address: form.address.trim(),
    telegram_chat_id: form.telegram_chat_id.trim() || null,
    sort_order: parseInt(form.sort_order, 10) || 0,
  };
}

// Форма города (используется и для добавления, и для редактирования)
function CityFields({ form, onChange }: { form: CityForm; onChange: (form: CityForm) => void }) {
  return (
    <div className="city-form">
      <div className="form-group">
        <label>Название</label>
        <input value={form.name} onChange={(e) => onChange({ ...form, name: e.target.value })} placeholder="Астана" />
      </div>
      <div className="form-group">
        <label>Другие варианты написания (через запятую)</label>
        <input
          value={form.aliases}
          onChange={(e) => onChange({ ...form, aliases: e.target.value })}
          placeholder="нур-султан, столица, astana"
        />
      </div>
      <div className="form-group form-group-wide">
        <label>Адрес, телефон, график работы</label>
        <textarea
          rows={3}
          value={form.address}
          onChange={(e) => onChange({ ...form, address: e.target.value })}
          placeholder="пр. Мангилик Ел, 55. Пн–Сб 8:00–20:00"
        />
      </div>
      <div className="form-group">
        <label>Telegram chat_id для уведомлений города</label>
        <input
          value={form.telegram_chat_id}
          onChange={(e) => onChange({ ...form, telegram_chat_id: e.target.value })}
          placeholder="-1001234567890"
        />
      </div>
      <div className="form-group">
        <label>Порядок в списке</label>
        <input
          type="number"
          value={form.sort_order}
          onChange={(e) => onChange({ ...form, sort_order: e.target.value })}
        />
      </div>
    </div>
  );
}

export default function CitiesSection({ bot, cities, onCitiesChanged }: Props) {
  const [newForm, setNewForm] = useState<CityForm>(EMPTY_FORM);
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<CityForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const addCity = async () => {
    if (!newForm.name.trim()) {
      setError("Введите название города");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await apiPost(`/api/bots/${bot.id}/cities`, formToBody(newForm));
      setNewForm(EMPTY_FORM);
      setShowAdd(false);
      onCitiesChanged();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  const startEdit = (city: City) => {
    setEditingId(city.id);
    setEditForm(cityToForm(city));
    setError("");
  };

  const saveEdit = async () => {
    if (!editingId) return;
    if (!editForm.name.trim()) {
      setError("Введите название города");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await apiPut(`/api/cities/${editingId}`, formToBody(editForm));
      setEditingId(null);
      onCitiesChanged();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  const deleteCity = async (city: City) => {
    const ok = confirm(
      `Удалить город «${city.name}»?\n\n` +
        "• База знаний этого города тоже удалится.\n" +
        "• Сотрудники, закреплённые за этим городом, будут удалены из команды.\n" +
        "• Диалоги останутся, но без города."
    );
    if (!ok) return;
    setBusy(true);
    setError("");
    setInfo("");
    try {
      const data = await apiDelete<{ status?: string; removed_members?: number }>(`/api/cities/${city.id}`);
      const removed = data?.removed_members || 0;
      setInfo(
        removed > 0
          ? `Город «${city.name}» удалён. Из команды удалено сотрудников: ${removed}`
          : `Город «${city.name}» удалён`
      );
      onCitiesChanged();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  return (
    <div className="settings-section">
      <h3>Города</h3>
      <p>
        Нужно, только если у вас несколько городов с разными ценами или адресами — тогда бот спросит город
        и будет отвечать только по данным этого города. Работаете в одном городе — оставьте пустым.
        Варианты написания помогают узнать город в любом виде («Нур-Султан», «Astana»).
      </p>

      {cities.length === 0 && !showAdd && (
        <div className="empty-box">Городов нет — бот не спрашивает город и отвечает по общей базе знаний.</div>
      )}

      {cities.length > 0 && (
        <div className="city-list">
          {cities.map((city) =>
            editingId === city.id ? (
              <div key={city.id} className="city-row editing">
                <CityFields form={editForm} onChange={setEditForm} />
                <div className="form-actions">
                  <button className="btn btn-sm btn-primary" onClick={saveEdit} disabled={busy}>
                    {busy ? "Сохранение…" : "Сохранить"}
                  </button>
                  <button className="btn btn-sm btn-outline" onClick={() => setEditingId(null)} disabled={busy}>
                    Отмена
                  </button>
                </div>
              </div>
            ) : (
              <div key={city.id} className="city-row">
                <div className="city-row-main">
                  <div className="city-row-name">
                    📍 {city.name}
                    <span className={`badge ${city.chunks_count ? "badge-green" : "badge-gray"}`}>
                      {city.chunks_count ? `${city.chunks_count} фрагм. базы знаний` : "база знаний пуста"}
                    </span>
                  </div>
                  {city.aliases && <div className="city-row-sub">Также: {city.aliases}</div>}
                  {city.address && <div className="city-row-sub">{city.address}</div>}
                  <div className="city-row-sub">
                    Telegram: {city.telegram_chat_id ? <code>{city.telegram_chat_id}</code> : "не указан"}
                  </div>
                </div>
                <div className="city-row-actions">
                  <button className="btn btn-sm btn-outline" onClick={() => startEdit(city)} disabled={busy}>
                    Изменить
                  </button>
                  <button className="btn btn-sm btn-ghost-danger" onClick={() => deleteCity(city)} disabled={busy}>
                    Удалить
                  </button>
                </div>
              </div>
            )
          )}
        </div>
      )}

      {showAdd ? (
        <div className="city-row editing" style={{ marginTop: 12 }}>
          <CityFields form={newForm} onChange={setNewForm} />
          <div className="form-actions">
            <button className="btn btn-sm btn-primary" onClick={addCity} disabled={busy}>
              {busy ? "Добавляем…" : "Добавить город"}
            </button>
            <button className="btn btn-sm btn-outline" onClick={() => setShowAdd(false)} disabled={busy}>
              Отмена
            </button>
          </div>
        </div>
      ) : (
        <button className="btn btn-sm btn-primary" style={{ marginTop: 12 }} onClick={() => { setShowAdd(true); setError(""); }}>
          + Добавить город
        </button>
      )}

      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}
      {info && <div className="alert alert-success" style={{ marginTop: 12 }}>{info}</div>}
    </div>
  );
}
