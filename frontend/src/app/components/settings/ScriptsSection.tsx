"use client";

// Настройки → Скрипты и дожимы:
// - приветствие (первое сообщение бота) и скрипт продаж (этапы, ответы на возражения);
// - ссылки, которые бот отправляет по ситуации (каталог, оплата, отзывы, 2ГИС…);
// - дожимы: что написать клиенту, если он замолчал.

import { useEffect, useState } from "react";
import { apiPut, Bot, BotLink, errorText, FollowupStep } from "../api";

type Props = {
  bot: Bot;
  onBotUpdated: (bot: Bot) => void;
};

const MAX_LINKS = 30;
const MAX_FOLLOWUPS = 3;

// Шаги дожима по умолчанию (когда владелец включает дожимы впервые)
const DEFAULT_FOLLOWUPS: FollowupStep[] = [
  { hours: 3, text: "" },
  { hours: 24, text: "" },
];

// Поля шага дожима в форме храним строками (так удобнее редактировать число)
type FollowupForm = { hours: string; text: string };

function toFollowupForm(steps: FollowupStep[] | null | undefined): FollowupForm[] {
  const result: FollowupForm[] = [];
  for (const step of steps || []) {
    result.push({ hours: String(step.hours), text: step.text || "" });
  }
  return result;
}

export default function ScriptsSection({ bot, onBotUpdated }: Props) {
  const [greeting, setGreeting] = useState(bot.greeting_message || "");
  const [script, setScript] = useState(bot.sales_script || "");
  const [links, setLinks] = useState<BotLink[]>(bot.links || []);
  const [followupsEnabled, setFollowupsEnabled] = useState(Boolean(bot.followups_enabled));
  const [steps, setSteps] = useState<FollowupForm[]>(toFollowupForm(bot.followups));
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");

  // Другой бот — другие значения в форме
  useEffect(() => {
    setGreeting(bot.greeting_message || "");
    setScript(bot.sales_script || "");
    setLinks(bot.links || []);
    setFollowupsEnabled(Boolean(bot.followups_enabled));
    setSteps(toFollowupForm(bot.followups));
    setResult("");
    setError("");
  }, [bot.id]);

  // --- Ссылки ---
  const updateLink = (index: number, field: keyof BotLink, value: string) => {
    const copy = [...links];
    copy[index] = { ...copy[index], [field]: value };
    setLinks(copy);
  };
  const addLink = () => setLinks([...links, { title: "", url: "", when: "" }]);
  const removeLink = (index: number) => setLinks(links.filter((_, i) => i !== index));

  // --- Дожимы ---
  const toggleFollowups = (enabled: boolean) => {
    setFollowupsEnabled(enabled);
    if (enabled && steps.length === 0) setSteps(toFollowupForm(DEFAULT_FOLLOWUPS));
  };
  const updateStep = (index: number, field: keyof FollowupForm, value: string) => {
    const copy = [...steps];
    copy[index] = { ...copy[index], [field]: value };
    setSteps(copy);
  };
  const addStep = () => setSteps([...steps, { hours: "48", text: "" }]);
  const removeStep = (index: number) => setSteps(steps.filter((_, i) => i !== index));

  const save = async () => {
    // Проверяем часы дожимов до отправки
    const cleanSteps: FollowupStep[] = [];
    for (let i = 0; i < steps.length; i++) {
      const hours = parseInt(steps[i].hours, 10);
      if (!hours || hours < 1 || hours > 168) {
        setError(`Дожим №${i + 1}: часы — число от 1 до 168`);
        return;
      }
      cleanSteps.push({ hours, text: steps[i].text });
    }
    if (followupsEnabled && cleanSteps.length === 0) {
      setError("Добавьте хотя бы один дожим или выключите дожимы");
      return;
    }
    // Пустые строки ссылок не отправляем
    const cleanLinks = links.filter((link) => link.title.trim() || link.url.trim());

    setSaving(true);
    setError("");
    setResult("");
    try {
      const data = await apiPut<{ bot: Bot }>(`/api/bots/${bot.id}`, {
        greeting_message: greeting,
        sales_script: script,
        links: cleanLinks,
        followups_enabled: followupsEnabled,
        followups: cleanSteps,
      });
      onBotUpdated(data.bot);
      setLinks(data.bot.links || []);
      setResult("Сохранено. Проверьте во вкладке «Тест бота»");
    } catch (err) {
      setError(errorText(err));
    }
    setSaving(false);
  };

  return (
    <>
      <div className="settings-section">
        <h3>Скрипты</h3>
        <p>Готовые формулировки, которые бот использует в разговоре. Бот подстраивает их под ситуацию,
          но не выходит за рамки базы знаний.</p>

        <div className="form-group">
          <label>Приветствие — первое сообщение бота (необязательно)</label>
          <textarea
            value={greeting}
            maxLength={1000}
            rows={3}
            onChange={(e) => setGreeting(e.target.value)}
            placeholder={"Например:\nЗдравствуйте! Меня зовут Алия, я помощник «Мебель Плюс» 😊 Подскажу по ценам, наличию и доставке."}
          />
          <div className="form-hint">Если клиент сразу задал вопрос — бот поздоровается так и сразу ответит на вопрос.</div>
        </div>

        <div className="form-group">
          <label>Скрипт продаж и ответы на возражения (необязательно)</label>
          <textarea
            value={script}
            maxLength={20000}
            rows={10}
            onChange={(e) => setScript(e.target.value)}
            placeholder={"Например:\n1. Выясни, для какой комнаты диван и какой нужен размер.\n2. Предложи 2–3 подходящие модели из каталога.\n3. Пригласи в шоурум или предложи оформить доставку.\n\nВозражения:\n«Дорого» — расскажи про рассрочку Kaspi Red 0-0-12.\n«Подумаю» — предложи прислать фото в интерьере и спроси, что смущает."}
          />
          <div className="form-hint">Этапы разговора, вопросы для выяснения потребности, ответы на «дорого», «подумаю», «у конкурентов дешевле».</div>
        </div>
      </div>

      <div className="settings-section">
        <h3>Ссылки</h3>
        <p>Бот отправит ссылку, когда она к месту: каталог, оплата, запись онлайн, отзывы, адрес в 2ГИС.
          Других ссылок он не придумывает.</p>

        {links.length === 0 && <div className="empty-box">Ссылок пока нет</div>}
        {links.map((link, index) => (
          <div key={index} className="link-row">
            <input
              className="input"
              value={link.title}
              maxLength={100}
              onChange={(e) => updateLink(index, "title", e.target.value)}
              placeholder="Название: Каталог"
            />
            <input
              className="input"
              value={link.url}
              maxLength={500}
              onChange={(e) => updateLink(index, "url", e.target.value)}
              placeholder="https://..."
            />
            <input
              className="input"
              value={link.when}
              maxLength={300}
              onChange={(e) => updateLink(index, "when", e.target.value)}
              placeholder="Когда отправлять: просят каталог или фото"
            />
            <button className="btn btn-sm btn-ghost-danger" onClick={() => removeLink(index)} title="Удалить ссылку">✕</button>
          </div>
        ))}
        <button className="btn btn-sm btn-outline" onClick={addLink} disabled={links.length >= MAX_LINKS} style={{ marginTop: 8 }}>
          + Добавить ссылку
        </button>
      </div>

      <div className="settings-section">
        <h3>Дожимы</h3>
        <p>Если клиент замолчал после ответа бота, бот сам напомнит о себе. Правила:</p>
        <ul className="hint-list">
          <li>только клиентам, которым бот написал <b>после включения</b> дожимов — по старым диалогам рассылки нет;</li>
          <li>только в рабочее время из вкладки «🕘 График работы» (если график выключен — с 10:00 до 20:00);</li>
          <li>не трогает горячих клиентов, диалоги, где отвечает сотрудник, и клиентов, которые ждут ответа руководителя на свой вопрос;</li>
          <li>если разговор уже завершён (клиент попрощался или отказался), ИИ дожим не пишет;</li>
          <li>опоздавший дожим не уходит: например, «через 3 ч» — не позже чем через 19 ч после ответа бота;</li>
          <li>если клиент ответил — дожимы начинаются заново.</li>
        </ul>

        <label className="checkbox-row">
          <input type="checkbox" checked={followupsEnabled} onChange={(e) => toggleFollowups(e.target.checked)} />
          Включить дожимы
        </label>

        {followupsEnabled && (
          <>
            {steps.map((step, index) => (
              <div key={index} className="followup-step">
                <div className="followup-step-head">
                  <b>Дожим №{index + 1}</b>
                  <span>через</span>
                  <input
                    className="input followup-hours"
                    type="number"
                    min={1}
                    max={168}
                    value={step.hours}
                    onChange={(e) => updateStep(index, "hours", e.target.value)}
                  />
                  <span>ч после {index === 0 ? "ответа бота" : "предыдущего дожима"}</span>
                  <button className="btn btn-sm btn-ghost-danger" onClick={() => removeStep(index)} title="Удалить дожим">✕</button>
                </div>
                <textarea
                  className="input"
                  value={step.text}
                  maxLength={1000}
                  rows={2}
                  onChange={(e) => updateStep(index, "text", e.target.value)}
                  placeholder="Пусто — ИИ напишет сам по контексту диалога (рекомендуем). Или свой текст: «Подскажите, вопрос ещё актуален?»"
                />
              </div>
            ))}
            <button className="btn btn-sm btn-outline" onClick={addStep} disabled={steps.length >= MAX_FOLLOWUPS}>
              + Добавить дожим
            </button>
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
    </>
  );
}
