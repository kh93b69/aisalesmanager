"use client";

// Настройки → Основное: тип бизнеса, анкета компании, дополнительные инструкции.
// Из анкеты на бэкенде собирается промпт — владельцу не нужно писать его с нуля.

import { useEffect, useState } from "react";
import { apiPut, Bot, BUSINESS_TYPES, BusinessType, errorText } from "../api";

type Props = {
  bot: Bot;
  onBotUpdated: (bot: Bot) => void;
};

export default function GeneralSection({ bot, onBotUpdated }: Props) {
  const [name, setName] = useState(bot.name);
  const [businessType, setBusinessType] = useState<BusinessType>(bot.business_type || "general");
  const [companyName, setCompanyName] = useState(bot.company_name || "");
  const [assistantName, setAssistantName] = useState(bot.assistant_name || "");
  const [description, setDescription] = useState(bot.business_description || "");
  const [targetAction, setTargetAction] = useState(bot.target_action || "");
  const [prompt, setPrompt] = useState(bot.system_prompt || "");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");

  // Другой бот — другие значения в форме
  useEffect(() => {
    setName(bot.name);
    setBusinessType(bot.business_type || "general");
    setCompanyName(bot.company_name || "");
    setAssistantName(bot.assistant_name || "");
    setDescription(bot.business_description || "");
    setTargetAction(bot.target_action || "");
    setPrompt(bot.system_prompt || "");
    setResult("");
    setError("");
  }, [bot.id]);

  const save = async () => {
    if (!name.trim()) {
      setError("Введите имя бота");
      return;
    }
    setSaving(true);
    setError("");
    setResult("");
    try {
      const data = await apiPut<{ bot: Bot }>(`/api/bots/${bot.id}`, {
        name: name.trim(),
        business_type: businessType,
        company_name: companyName,
        assistant_name: assistantName,
        business_description: description,
        target_action: targetAction,
        system_prompt: prompt,
      });
      onBotUpdated(data.bot);
      setResult("Сохранено. Проверьте бота во вкладке «Тест бота»");
    } catch (err) {
      setError(errorText(err));
    }
    setSaving(false);
  };

  // Подсказки зависят от типа бизнеса
  const isClinic = businessType === "clinic";

  return (
    <>
      <div className="settings-section">
        <h3>О компании</h3>
        <p>Заполните анкету — из неё бот поймёт, кто он и что продаёт. Базовые правила (не выдумывать цены,
          вести клиента к заказу, передавать горячих клиентов менеджеру, спрашивать город) уже встроены.</p>

        <div className="form-group">
          <label>Тип бизнеса</label>
          <div className="choice-grid">
            {BUSINESS_TYPES.map((t) => (
              <label key={t.value} className={`choice-card ${businessType === t.value ? "active" : ""}`}>
                <input
                  type="radio"
                  name="business_type"
                  checked={businessType === t.value}
                  onChange={() => setBusinessType(t.value)}
                />
                <div>
                  <div className="choice-title">{t.title}</div>
                  <div className="choice-desc">{t.description}</div>
                </div>
              </label>
            ))}
          </div>
        </div>

        <div className="form-group">
          <label>Название компании</label>
          <input
            value={companyName}
            maxLength={100}
            onChange={(e) => setCompanyName(e.target.value)}
            placeholder={isClinic ? "Например: Клиника «Здоровье»" : "Например: Мебель Плюс"}
          />
        </div>

        <div className="form-group">
          <label>Как зовут ассистента (необязательно)</label>
          <input
            value={assistantName}
            maxLength={50}
            onChange={(e) => setAssistantName(e.target.value)}
            placeholder="Например: Алия"
          />
        </div>

        <div className="form-group">
          <label>Чем занимается компания</label>
          <textarea
            value={description}
            maxLength={3000}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            placeholder={isClinic
              ? "Например: Многопрофильная клиника: терапевт, гинеколог, УЗИ, анализы. Работаем без выходных."
              : "Например: Продаём мягкую мебель от производителя. Доставка по городу 1–3 дня, сборка бесплатно, гарантия 2 года."}
          />
          <div className="form-hint">Коротко: что продаёте, ваши преимущества. Цены и подробности — в «Базе знаний».</div>
        </div>

        <div className="form-group">
          <label>Целевое действие — когда клиент «горячий»</label>
          <input
            value={targetAction}
            maxLength={500}
            onChange={(e) => setTargetAction(e.target.value)}
            placeholder={isClinic
              ? "Например: клиент согласился записаться на приём"
              : "Например: клиент выбрал товар и готов оформить заказ с доставкой"}
          />
          <div className="form-hint">
            В этот момент диалог станет 🔥, а менеджеру придёт уведомление в Telegram.
            {isClinic
              ? "Пусто — «клиент согласился записаться или оплатить приём»."
              : "Пусто — «клиент готов купить: оформить заказ, записаться или оплатить»."}
          </div>
        </div>
      </div>

      <div className="settings-section">
        <h3>ИИ</h3>

        <div className="form-group">
          <label>Имя бота в панели</label>
          <input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} placeholder="Например: Продажи WhatsApp" />
        </div>

        <div className="form-group">
          <label>Дополнительные инструкции (необязательно)</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={8}
            placeholder={"Например:\nОбщайся тепло, но без панибратства.\nЕсли спрашивают про рассрочку — скажи, что есть Kaspi Red на 12 месяцев.\nВсегда предлагай бесплатный замер."}
          />
          <div className="form-hint">Тон, особые сценарии, ответы на частые возражения.</div>
        </div>

        <div className="form-actions">
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
