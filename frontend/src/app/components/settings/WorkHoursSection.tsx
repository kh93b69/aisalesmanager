"use client";

// Настройки → График работы: в какие дни и часы бот пишет клиентам.
// Вне графика бот молчит (или один раз отправляет автоответ) и отвечает, как только начнётся рабочее время.

import { useEffect, useState } from "react";
import { apiPut, apiRequest, Bot, errorText, WorkDay, WorkStatus } from "../api";

type Props = {
  bot: Bot;
  onBotUpdated: (bot: Bot) => void;
};

const DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];

// График по умолчанию: пн–сб 09:00–21:00, вс выходной
function defaultSchedule(): WorkDay[] {
  const schedule: WorkDay[] = [];
  for (let i = 0; i < 7; i++) {
    schedule.push({ enabled: i < 6, start: "09:00", end: "21:00" });
  }
  return schedule;
}

// График из бота: если его ещё нет или он неполный — по умолчанию
function scheduleFromBot(bot: Bot): WorkDay[] {
  if (Array.isArray(bot.work_schedule) && bot.work_schedule.length === 7) {
    return bot.work_schedule.map((day) => ({ ...day }));
  }
  return defaultSchedule();
}

// Подпись дня: «ночная смена» до следующего дня или «круглые сутки»
function dayHint(day: WorkDay): string {
  if (!day.enabled) return "выходной";
  if (day.start === day.end) return "круглые сутки";
  if (day.end < day.start) return "через полночь — до утра следующего дня";
  return "";
}

export default function WorkHoursSection({ bot, onBotUpdated }: Props) {
  const [enabled, setEnabled] = useState(Boolean(bot.work_hours_enabled));
  const [schedule, setSchedule] = useState<WorkDay[]>(scheduleFromBot(bot));
  const [offMessage, setOffMessage] = useState(bot.off_hours_message || "");
  const [status, setStatus] = useState<WorkStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");

  const loadStatus = () => {
    apiRequest<WorkStatus>(`/api/bots/${bot.id}/work-status`)
      .then((data) => setStatus(data))
      .catch(() => setStatus(null));
  };

  useEffect(() => {
    setEnabled(Boolean(bot.work_hours_enabled));
    setSchedule(scheduleFromBot(bot));
    setOffMessage(bot.off_hours_message || "");
    setResult("");
    setError("");
    loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bot.id]);

  const updateDay = (index: number, field: keyof WorkDay, value: string | boolean) => {
    const copy = [...schedule];
    copy[index] = { ...copy[index], [field]: value };
    setSchedule(copy);
  };

  // Скопировать часы понедельника на все рабочие дни
  const copyMonday = () => {
    const monday = schedule[0];
    setSchedule(schedule.map((day) => ({ ...day, start: monday.start, end: monday.end })));
  };

  const save = async () => {
    if (enabled && !schedule.some((day) => day.enabled)) {
      setError("Отметьте хотя бы один рабочий день");
      return;
    }
    setSaving(true);
    setError("");
    setResult("");
    try {
      const data = await apiPut<{ bot: Bot }>(`/api/bots/${bot.id}`, {
        work_hours_enabled: enabled,
        work_schedule: schedule,
        off_hours_message: offMessage,
      });
      onBotUpdated(data.bot);
      setResult("Сохранено");
      loadStatus();
    } catch (err) {
      setError(errorText(err));
    }
    setSaving(false);
  };

  return (
    <div className="settings-section">
      <h3>График работы бота</h3>
      <p>Бот пишет клиентам только в эти часы: ответы, дожимы, ссылки на оплату. Если клиент написал
        в нерабочее время — бот не отвечает (или один раз отправляет автоответ ниже), а как только начнётся
        рабочее время, сам ответит на всё, что клиент успел написать.</p>

      {status && (
        <div className="status-box" style={{ marginBottom: 16 }}>
          <div className="status-line">
            <span className={`wa-status-badge ${status.working ? "wa-connected" : "wa-waiting"}`}>
              {status.working ? "● Бот сейчас отвечает" : "● Бот сейчас не отвечает (нерабочее время)"}
            </span>
          </div>
          <div className="status-line text-muted">
            Время бота: {status.now} ({status.timezone}, UTC{status.utc_offset.slice(0, 3)})
            {!status.working && status.next_start ? ` · начнёт отвечать: ${status.next_start}` : ""}
            {!status.schedule_enabled ? " · график выключен — бот отвечает круглосуточно" : ""}
          </div>
        </div>
      )}

      <label className="checkbox-row">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Включить график (без него бот отвечает круглосуточно)
      </label>

      {enabled && (
        <>
          <div className="work-schedule">
            {schedule.map((day, index) => (
              <div key={index} className={`work-day ${day.enabled ? "" : "work-day-off"}`}>
                <label className="checkbox-row work-day-name">
                  <input type="checkbox" checked={day.enabled} onChange={(e) => updateDay(index, "enabled", e.target.checked)} />
                  {DAY_NAMES[index]}
                </label>
                <input
                  className="input work-time"
                  type="time"
                  value={day.start}
                  disabled={!day.enabled}
                  onChange={(e) => updateDay(index, "start", e.target.value)}
                />
                <span>—</span>
                <input
                  className="input work-time"
                  type="time"
                  value={day.end}
                  disabled={!day.enabled}
                  onChange={(e) => updateDay(index, "end", e.target.value)}
                />
                <span className="text-muted work-day-hint">{dayHint(day)}</span>
              </div>
            ))}
          </div>
          <button className="btn btn-sm btn-outline" onClick={copyMonday}>Часы понедельника — на все дни</button>

          <div className="form-group" style={{ marginTop: 16 }}>
            <label>Автоответ в нерабочее время (необязательно)</label>
            <textarea
              className="input"
              value={offMessage}
              maxLength={1000}
              rows={3}
              onChange={(e) => setOffMessage(e.target.value)}
              placeholder={"Например:\nЗдравствуйте! Сейчас мы не на связи, ответим завтра с 9:00 😊"}
            />
            <div className="form-hint">Отправляется один раз, когда клиент пишет в нерабочее время. Пусто — бот просто ответит утром.
              Укажите в тексте ваше время работы — бот сам его не подставляет.</div>
          </div>
        </>
      )}

      <div className="form-actions" style={{ marginTop: 16 }}>
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? "Сохранение…" : "Сохранить"}
        </button>
        {result && <span className="text-success">✓ {result}</span>}
        {error && <span className="text-danger">{error}</span>}
      </div>
    </div>
  );
}
