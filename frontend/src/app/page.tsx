"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Типы данных
type Bot = {
  id: string;
  name: string;
  system_prompt: string;
};

type Dialog = {
  id: string;
  chat_id: string;
  channel: string;
  ai_disabled: boolean;
  created_at: string;
};

type Message = {
  id: string;
  role: string;
  content: string;
  created_at: string;
};

export default function HomePage() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [selectedBot, setSelectedBot] = useState<Bot | null>(null);
  const [dialogs, setDialogs] = useState<Dialog[]>([]);
  const [selectedDialog, setSelectedDialog] = useState<Dialog | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);

  // Загружаем список ботов при старте
  useEffect(() => {
    fetch(`${API_URL}/api/bots`)
      .then((res) => res.json())
      .then((data) => setBots(data.bots || []))
      .catch((err) => console.error("Ошибка загрузки ботов:", err));
  }, []);

  // Загружаем диалоги при выборе бота
  useEffect(() => {
    if (!selectedBot) return;
    fetch(`${API_URL}/api/bots/${selectedBot.id}/dialogs`)
      .then((res) => res.json())
      .then((data) => setDialogs(data.dialogs || []))
      .catch((err) => console.error("Ошибка загрузки диалогов:", err));
  }, [selectedBot]);

  // Загружаем сообщения при выборе диалога
  useEffect(() => {
    if (!selectedDialog) return;
    fetch(`${API_URL}/api/dialogs/${selectedDialog.id}/messages`)
      .then((res) => res.json())
      .then((data) => setMessages(data.messages || []))
      .catch((err) => console.error("Ошибка загрузки сообщений:", err));
  }, [selectedDialog]);

  // Переключение ИИ (режим "Перехват")
  const toggleAI = async (dialogId: string) => {
    const res = await fetch(`${API_URL}/api/dialogs/${dialogId}/toggle-ai`, {
      method: "POST",
    });
    const data = await res.json();
    setDialogs((prev) =>
      prev.map((d) => (d.id === dialogId ? { ...d, ai_disabled: data.ai_disabled } : d))
    );
  };

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      {/* Боковая панель — список ботов и диалогов */}
      <div style={{ width: 300, borderRight: "1px solid #ddd", padding: 16, overflowY: "auto" }}>
        <h2>Боты</h2>
        {bots.map((bot) => (
          <div
            key={bot.id}
            onClick={() => { setSelectedBot(bot); setSelectedDialog(null); setMessages([]); }}
            style={{
              padding: 10,
              cursor: "pointer",
              background: selectedBot?.id === bot.id ? "#e3f2fd" : "transparent",
              borderRadius: 8,
              marginBottom: 4,
            }}
          >
            {bot.name}
          </div>
        ))}

        {selectedBot && (
          <>
            <h3 style={{ marginTop: 20 }}>Диалоги</h3>
            {dialogs.length === 0 && <p style={{ color: "#999" }}>Нет диалогов</p>}
            {dialogs.map((dialog) => (
              <div
                key={dialog.id}
                onClick={() => setSelectedDialog(dialog)}
                style={{
                  padding: 10,
                  cursor: "pointer",
                  background: selectedDialog?.id === dialog.id ? "#e8f5e9" : "transparent",
                  borderRadius: 8,
                  marginBottom: 4,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <span>{dialog.chat_id}</span>
                <span style={{ fontSize: 12, color: dialog.ai_disabled ? "red" : "green" }}>
                  {dialog.ai_disabled ? "ИИ выкл" : "ИИ вкл"}
                </span>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Основная область — сообщения */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: 16 }}>
        {selectedDialog ? (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h2>Диалог: {selectedDialog.chat_id}</h2>
              <button
                onClick={() => toggleAI(selectedDialog.id)}
                style={{
                  padding: "8px 16px",
                  background: selectedDialog.ai_disabled ? "#4caf50" : "#f44336",
                  color: "white",
                  border: "none",
                  borderRadius: 8,
                  cursor: "pointer",
                }}
              >
                {selectedDialog.ai_disabled ? "Включить ИИ" : "Отключить ИИ"}
              </button>
            </div>

            <div style={{ flex: 1, overflowY: "auto" }}>
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  style={{
                    marginBottom: 12,
                    padding: 12,
                    background: msg.role === "user" ? "#f5f5f5" : "#e3f2fd",
                    borderRadius: 12,
                    maxWidth: "70%",
                    marginLeft: msg.role === "assistant" ? "auto" : 0,
                  }}
                >
                  <div style={{ fontSize: 12, color: "#999", marginBottom: 4 }}>
                    {msg.role === "user" ? "Клиент" : "ИИ Менеджер"}
                  </div>
                  {msg.content}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#999" }}>
            Выберите бота и диалог
          </div>
        )}
      </div>
    </div>
  );
}
