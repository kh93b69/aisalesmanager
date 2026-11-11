"use client";

// Настройки → Уведомления: чат руководителя, напоминание о счёте, распределение заявок, тест.

import { useEffect, useState } from "react";
import { apiPost, apiPut, AssignmentMode, Bot, errorText } from "../api";

type Props = {
  bot: Bot;
  onBotUpdated: (bot: Bot) => void;
};

export default function NotificationsSection({ bot, onBotUpdated }: Props) {
  const [chatId, setChatId] = useState(bot.telegram_notify_chat_id || "");
  const [minutes, setMinutes] = useState(String(bot.invoice_reminder_minutes || 10));
  const [mode, setMode] = useState<AssignmentMode>(bot.assignment_mode || "none");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  // Тестовое уведомление
  const [testChatId, setTestChatId] = useState(bot.telegram_notify_chat_id || "");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState("");
  const [testError, setTestError] = useState("");

  useEffect(() => {
    setChatId(bot.telegram_notify_chat_id || "");
    setMinutes(String(bot.invoice_reminder_minutes || 10));
    setMode(bot.assignment_mode || "none");
    setTestChatId(bot.telegram_notify_chat_id || "");
    setResult("");
    setError("");
    setTestResult("");
    setTestError("");
  }, [bot.id]);

  const save = async () => {
    const minutesNumber = parseInt(minutes, 10);
    if (!minutesNumber || minutesNumber < 1 || minutesNumber > 240) {
      setError("Минуты до напоминания — число от 1 до 240");
      return;
    }
    setSaving(true);
    setError("");
    setResult("");
    try {
      const data = await apiPut<{ bot: Bot }>(`/api/bots/${bot.id}`, {
        telegram_notify_chat_id: chatId.trim() || null,
        invoice_reminder_minutes: minutesNumber,
        assignment_mode: mode,
      });
      onBotUpdated(data.bot);
      setResult("Сохранено");
    } catch (err) {
      setError(errorText(err));
    }
    setSaving(false);
  };

  const sendTest = async () => {
    if (!testChatId.trim()) {
      setTestError("Укажите chat_id для проверки");
      return;
    }
    setTesting(true);
    setTestResult("");
    setTestError("");
    try {
      const data = await apiPost<{ ok: boolean; error?: string }>(`/api/bots/${bot.id}/test-notification`, {
        chat_id: testChatId.trim(),
      });
      if (data.ok) {
        setTestResult("Отправлено — проверьте Telegram");
      } else {
        setTestError(data.error || "Не дошло. Проверьте chat_id и что бот уведомлений добавлен в чат.");
      }
    } catch (err) {
      setTestError(errorText(err));
    }
    setTesting(false);
  };

  return (
    <div className="settings-section">
      <h3>Уведомления в Telegram</h3>
      <p>
        Когда клиент готов оплатить, бот присылает уведомление «🔥 Нужен счёт» в чат города и ответственным
        сотрудникам. Если счёт не отправлен за N минут — повторное напоминание, через 2N минут — руководителю.
      </p>

      <div className="form-group">
        <label>Чат руководителя (общий, для эскалаций и сбоев)</label>
        <input value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="например, 123456789 или -1001234567890" />
      </div>

      <div className="form-grid-2">
        <div className="form-group">
          <label>Напомнить о неотправленном счёте через, минут</label>
          <input type="number" min={1} max={240} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        </div>
        <div className="form-group">
          <label>Распределение горячих клиентов</label>
          <select value={mode} onChange={(e) => setMode(e.target.value as AssignmentMode)}>
            <option value="none">Всем сотрудникам города сразу</option>
            <option value="round_robin">По очереди — одному ответственному</option>
          </select>
        </div>
      </div>

      <div className="form-actions">
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? "Сохранение…" : "Сохранить"}
        </button>
        {result && <span className="text-success">✓ {result}</span>}
        {error && <span className="text-danger">{error}</span>}
      </div>

      <div className="divider" />

      <div className="form-group">
        <label>Проверить доставку</label>
        <div className="input-row">
          <input value={testChatId} onChange={(e) => setTestChatId(e.target.value)} placeholder="chat_id для проверки" />
          <button className="btn btn-outline" onClick={sendTest} disabled={testing}>
            {testing ? "Отправляем…" : "Тест"}
          </button>
        </div>
        {testResult && <div className="text-success" style={{ marginTop: 8 }}>✓ {testResult}</div>}
        {testError && <div className="text-danger" style={{ marginTop: 8 }}>{testError}</div>}
      </div>

      <div className="hint-box">
        <b>Как узнать chat_id</b>
        <ul>
          <li>Сначала напишите любое сообщение боту уведомлений в Telegram (нажмите «Start») — иначе он не сможет вам писать.</li>
          <li>Личный chat_id: напишите боту <code>@userinfobot</code> — он пришлёт ваш числовой id.</li>
          <li>Группа: добавьте в группу бота уведомлений и <code>@RawDataBot</code> — он покажет id группы.
            У групп id начинается с минуса, например <code>-1001234567890</code>. После этого RawDataBot можно удалить.</li>
        </ul>
      </div>
    </div>
  );
}
