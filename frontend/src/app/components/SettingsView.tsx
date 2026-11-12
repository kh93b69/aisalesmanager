"use client";

// Настройки бота (только для владельца): вкладки с разделами.

import { useState } from "react";
import { Bot, City } from "./api";
import GeneralSection from "./settings/GeneralSection";
import WazzupSection from "./settings/WazzupSection";
import CitiesSection from "./settings/CitiesSection";
import KnowledgeSection from "./settings/KnowledgeSection";
import ImagesSection from "./settings/ImagesSection";
import TeamSection from "./settings/TeamSection";
import NotificationsSection from "./settings/NotificationsSection";
import TestChatSection from "./settings/TestChatSection";
import ScriptsSection from "./settings/ScriptsSection";
import QuestionsSection from "./settings/QuestionsSection";
import WorkHoursSection from "./settings/WorkHoursSection";
import PaymentSection from "./settings/PaymentSection";

type Props = {
  bot: Bot;
  cities: City[];
  onBotUpdated: (bot: Bot) => void;
  onCitiesChanged: () => void;
};

const SECTIONS = [
  { key: "wazzup", label: "WhatsApp" },
  { key: "general", label: "О компании и ИИ" },
  { key: "cities", label: "Города" },
  { key: "knowledge", label: "База знаний" },
  { key: "scripts", label: "Скрипты и дожимы" },
  { key: "questions", label: "❓ Без ответа" },
  { key: "hours", label: "🕘 График работы" },
  { key: "payment", label: "💳 Оплата" },
  { key: "images", label: "Картинки" },
  { key: "team", label: "Команда" },
  { key: "notifications", label: "Уведомления" },
  { key: "test", label: "🧪 Тест бота" },
];

export default function SettingsView({ bot, cities, onBotUpdated, onCitiesChanged }: Props) {
  const [section, setSection] = useState("wazzup");

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Настройки бота «{bot.name}»</h1>
          <p>WhatsApp, анкета компании, база знаний, скрипты и дожимы, вопросы без ответа, график работы, оплата, команда и проверка бота</p>
        </div>
      </div>

      <div className="settings-tabs">
        {SECTIONS.map((s) => (
          <button key={s.key} className={section === s.key ? "active" : ""} onClick={() => setSection(s.key)}>
            {s.label}
            {s.key === "wazzup" && !bot.wazzup_connected && <span className="dot-warning" title="Не подключено" />}
          </button>
        ))}
      </div>

      <div className="settings-body" key={bot.id}>
        {section === "wazzup" && <WazzupSection bot={bot} onBotUpdated={onBotUpdated} />}
        {section === "general" && <GeneralSection bot={bot} onBotUpdated={onBotUpdated} />}
        {section === "cities" && <CitiesSection bot={bot} cities={cities} onCitiesChanged={onCitiesChanged} />}
        {section === "knowledge" && <KnowledgeSection bot={bot} cities={cities} onCitiesChanged={onCitiesChanged} />}
        {section === "scripts" && <ScriptsSection bot={bot} onBotUpdated={onBotUpdated} />}
        {section === "questions" && <QuestionsSection bot={bot} />}
        {section === "hours" && <WorkHoursSection bot={bot} onBotUpdated={onBotUpdated} />}
        {section === "payment" && <PaymentSection bot={bot} onBotUpdated={onBotUpdated} />}
        {section === "images" && <ImagesSection bot={bot} />}
        {section === "team" && <TeamSection bot={bot} cities={cities} />}
        {section === "notifications" && <NotificationsSection bot={bot} onBotUpdated={onBotUpdated} />}
        {section === "test" && <TestChatSection bot={bot} cities={cities} />}
      </div>
    </div>
  );
}
