"use client";

// Настройки → Оплата: когда клиент готов оплатить, бот сам отправляет ссылку на оплату и реквизиты,
// а сотрудникам приходит уведомление «💳 Клиенту отправлена ссылка на оплату».

import { useEffect, useState } from "react";
import { apiPut, Bot, errorText } from "../api";

type Props = {
  bot: Bot;
  onBotUpdated: (bot: Bot) => void;
};

export default function PaymentSection({ bot, onBotUpdated }: Props) {
  const [enabled, setEnabled] = useState(Boolean(bot.payment_enabled));
  const [link, setLink] = useState(bot.payment_link || "");
  const [details, setDetails] = useState(bot.payment_details || "");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setEnabled(Boolean(bot.payment_enabled));
    setLink(bot.payment_link || "");
    setDetails(bot.payment_details || "");
    setResult("");
    setError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bot.id]);

  const save = async () => {
    if (enabled && !link.trim() && !details.trim()) {
      setError("Укажите ссылку на оплату или реквизиты");
      return;
    }
    setSaving(true);
    setError("");
    setResult("");
    try {
      const data = await apiPut<{ bot: Bot }>(`/api/bots/${bot.id}`, {
        payment_enabled: enabled,
        payment_link: link,
        payment_details: details,
      });
      onBotUpdated(data.bot);
      setResult("Сохранено. Проверьте во вкладке «Тест бота»: напишите «беру, как оплатить?»");
    } catch (err) {
      setError(errorText(err));
    }
    setSaving(false);
  };

  // Как сообщение увидит клиент (бот добавляет это в конец своего ответа)
  const previewParts: string[] = [];
  if (link.trim()) previewParts.push("💳 Ссылка для оплаты:\n" + link.trim());
  if (details.trim()) previewParts.push(details.trim());

  return (
    <div className="settings-section">
      <h3>Оплата</h3>
      <p>Когда клиент готов купить, бот подводит итог (что оплачивает и сумму — только если цена есть в базе знаний)
        и сразу отправляет ссылку на оплату. Диалог получает статус «Счёт отправлен», сотрудникам приходит уведомление —
        им остаётся проверить поступление и отметить «Оплачено». Если клиент напишет «оплатил» — бот позовёт сотрудника.</p>
      <p className="text-muted">Без этой настройки бот, как раньше, передаёт готового клиента менеджеру, и счёт отправляет человек.</p>

      <label className="checkbox-row">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Бот сам отправляет ссылку на оплату
      </label>

      <div className="form-group">
        <label>Ссылка на оплату</label>
        <input
          className="input"
          value={link}
          maxLength={500}
          onChange={(e) => setLink(e.target.value)}
          placeholder="https://… — ссылка Kaspi Pay, страница оплаты на сайте и т.п."
        />
        <div className="form-hint">Одна постоянная ссылка, по которой клиент сам вводит сумму, или страница оплаты.
          Бот не создаёт отдельный счёт на каждую сумму — для этого нужна интеграция с банком.</div>
      </div>

      <div className="form-group">
        <label>Реквизиты и инструкция (необязательно)</label>
        <textarea
          className="input"
          value={details}
          maxLength={1000}
          rows={4}
          onChange={(e) => setDetails(e.target.value)}
          placeholder={"Например:\nИли переводом на Kaspi Gold: +7 701 000 00 00 (Айгерим К.)\nПосле оплаты пришлите, пожалуйста, чек сюда 🙏"}
        />
      </div>

      {previewParts.length > 0 && (
        <div className="form-group">
          <label>Так клиент увидит конец сообщения</label>
          <div className="hint-box" style={{ whiteSpace: "pre-wrap" }}>{previewParts.join("\n\n")}</div>
        </div>
      )}

      <div className="form-actions">
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? "Сохранение…" : "Сохранить"}
        </button>
        {result && <span className="text-success">✓ {result}</span>}
        {error && <span className="text-danger">{error}</span>}
      </div>
    </div>
  );
}
