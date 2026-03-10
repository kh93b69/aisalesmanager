"use client";

import { useEffect, useState, useRef } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

// --- Типы ---

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

// --- Вспомогательные функции ---

function formatTime(dateStr: string) {
  const date = new Date(dateStr);
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function formatDate(dateStr: string) {
  const date = new Date(dateStr);
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// --- Главный компонент ---

export default function Dashboard() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [selectedBot, setSelectedBot] = useState<Bot | null>(null);
  const [dialogs, setDialogs] = useState<Dialog[]>([]);
  const [selectedDialog, setSelectedDialog] = useState<Dialog | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [editPrompt, setEditPrompt] = useState("");
  const [editName, setEditName] = useState("");
  const [saving, setSaving] = useState(false);
  const [knowledgeStatus, setKnowledgeStatus] = useState("");
  const [uploadingImage, setUploadingImage] = useState(false);
  const [botImages, setBotImages] = useState<{id: string; url: string; name: string}[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Загрузка ботов
  useEffect(() => {
    fetch(`${API_URL}/api/bots`)
      .then((r) => r.json())
      .then((data) => {
        setBots(data.bots || []);
        if (data.bots?.length > 0) {
          setSelectedBot(data.bots[0]);
        }
      })
      .catch((err) => console.error("Ошибка загрузки ботов:", err));
  }, []);

  // Загрузка диалогов при выборе бота
  useEffect(() => {
    if (!selectedBot) return;
    setEditPrompt(selectedBot.system_prompt);
    setEditName(selectedBot.name);

    // Загружаем картинки бота
    fetch(`${API_URL}/api/bots/${selectedBot.id}/images`)
      .then((r) => r.json())
      .then((data) => setBotImages(data.images || []))
      .catch(() => setBotImages([]));

    const loadDialogs = () => {
      fetch(`${API_URL}/api/bots/${selectedBot.id}/dialogs`)
        .then((r) => r.json())
        .then((data) => setDialogs(data.dialogs || []))
        .catch((err) => console.error("Ошибка загрузки диалогов:", err));
    };

    loadDialogs();
    const interval = setInterval(loadDialogs, 5000);
    return () => clearInterval(interval);
  }, [selectedBot]);

  // Загрузка сообщений при выборе диалога
  useEffect(() => {
    if (!selectedDialog) return;

    const loadMessages = () => {
      fetch(`${API_URL}/api/dialogs/${selectedDialog.id}/messages`)
        .then((r) => r.json())
        .then((data) => setMessages(data.messages || []))
        .catch((err) => console.error("Ошибка загрузки сообщений:", err));
    };

    loadMessages();
    const interval = setInterval(loadMessages, 3000);
    return () => clearInterval(interval);
  }, [selectedDialog]);

  // Автоскролл к последнему сообщению
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Переключение ИИ
  const toggleAI = async (dialogId: string) => {
    const res = await fetch(`${API_URL}/api/dialogs/${dialogId}/toggle-ai`, { method: "POST" });
    const data = await res.json();
    setDialogs((prev) =>
      prev.map((d) => (d.id === dialogId ? { ...d, ai_disabled: data.ai_disabled } : d))
    );
    if (selectedDialog?.id === dialogId) {
      setSelectedDialog((prev) => prev ? { ...prev, ai_disabled: data.ai_disabled } : null);
    }
  };

  // Сохранение настроек бота
  const saveSettings = async () => {
    if (!selectedBot) return;
    setSaving(true);
    const res = await fetch(`${API_URL}/api/bots/${selectedBot.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: editName, system_prompt: editPrompt }),
    });
    const data = await res.json();
    const updatedBot = data.bot;
    setBots((prev) => prev.map((b) => (b.id === updatedBot.id ? updatedBot : b)));
    setSelectedBot(updatedBot);
    setSaving(false);
  };

  // Загрузка базы знаний
  const uploadKnowledge = async (file: File) => {
    if (!selectedBot) return;
    setKnowledgeStatus("Загружаю...");
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch(`${API_URL}/api/bots/${selectedBot.id}/knowledge`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      setKnowledgeStatus(`Загружено: ${data.chunks_count} фрагментов (${data.format})`);
    } catch {
      setKnowledgeStatus("Ошибка загрузки");
    }
  };

  // Удаление диалога
  const deleteDialog = async (dialogId: string) => {
    await fetch(`${API_URL}/api/dialogs/${dialogId}`, { method: "DELETE" });
    setDialogs((prev) => prev.filter((d) => d.id !== dialogId));
    if (selectedDialog?.id === dialogId) {
      setSelectedDialog(null);
      setMessages([]);
    }
  };

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      {/* === БОКОВАЯ ПАНЕЛЬ === */}
      <div className="sidebar">
        <div className="sidebar-header">
          <h2>AI Sales Manager</h2>
          <button className="btn btn-sm btn-outline" onClick={() => setShowSettings(!showSettings)}>
            {showSettings ? "Диалоги" : "Настройки"}
          </button>
        </div>

        {/* Список ботов */}
        <div style={{ padding: "12px 20px", fontSize: 12, color: "#999", fontWeight: 600, textTransform: "uppercase" }}>
          Боты
        </div>
        {bots.map((bot) => (
          <div
            key={bot.id}
            className={`bot-item ${selectedBot?.id === bot.id ? "active" : ""}`}
            onClick={() => { setSelectedBot(bot); setSelectedDialog(null); setMessages([]); }}
          >
            <div style={{ fontWeight: 500 }}>{bot.name}</div>
            <div style={{ fontSize: 12, color: "#999", marginTop: 2 }}>
              {bot.system_prompt.slice(0, 50)}...
            </div>
          </div>
        ))}

        {/* Список диалогов */}
        {selectedBot && !showSettings && (
          <>
            <div style={{ padding: "12px 20px", fontSize: 12, color: "#999", fontWeight: 600, textTransform: "uppercase", borderTop: "1px solid #e1e4e8" }}>
              Диалоги ({dialogs.length})
            </div>
            <div style={{ flex: 1, overflowY: "auto" }}>
              {dialogs.length === 0 && (
                <div style={{ padding: 20, color: "#999", textAlign: "center" }}>
                  Нет диалогов. Напишите боту в Telegram!
                </div>
              )}
              {dialogs.map((dialog) => (
                <div
                  key={dialog.id}
                  className={`dialog-item ${selectedDialog?.id === dialog.id ? "active" : ""}`}
                  onClick={() => setSelectedDialog(dialog)}
                >
                  <div className="dialog-info">
                    <div className="dialog-chat-id">{dialog.chat_id}</div>
                    <div className="dialog-meta">{dialog.channel} &middot; {formatDate(dialog.created_at)}</div>
                  </div>
                  <span className={`badge ${dialog.ai_disabled ? "badge-red" : "badge-green"}`}>
                    {dialog.ai_disabled ? "ИИ выкл" : "ИИ вкл"}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* === ОСНОВНАЯ ОБЛАСТЬ === */}
      <div className="main-area">
        {showSettings && selectedBot ? (
          /* --- Настройки бота --- */
          <div style={{ padding: 32, maxWidth: 700, overflowY: "auto" }}>
            <h2 style={{ marginBottom: 24 }}>Настройки бота</h2>

            <div className="form-group">
              <label>Имя бота</label>
              <input value={editName} onChange={(e) => setEditName(e.target.value)} />
            </div>

            <div className="form-group">
              <label>Личность менеджера (системный промпт)</label>
              <textarea
                value={editPrompt}
                onChange={(e) => setEditPrompt(e.target.value)}
                rows={10}
                placeholder="Опишите личность, тон общения и цели менеджера..."
              />
            </div>

            <button className="btn btn-primary" onClick={saveSettings} disabled={saving}>
              {saving ? "Сохранение..." : "Сохранить"}
            </button>

            <div style={{ marginTop: 32 }}>
              <h3 style={{ marginBottom: 12 }}>База знаний</h3>
              <p style={{ fontSize: 14, color: "#666", marginBottom: 12 }}>
                Загрузите файл с описанием услуг, ценами и FAQ.
                Поддерживаются: TXT, PDF, CSV, XLSX.
              </p>
              <div
                className="upload-area"
                onClick={() => document.getElementById("file-upload")?.click()}
              >
                <div style={{ fontSize: 32, marginBottom: 8 }}>+</div>
                <div>Нажмите для загрузки файла</div>
                <div style={{ fontSize: 12, color: "#999", marginTop: 4 }}>PDF, TXT, CSV, XLSX</div>
                <input
                  id="file-upload"
                  type="file"
                  accept=".txt,.text,.pdf,.csv,.xlsx,.xls"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) uploadKnowledge(file);
                  }}
                />
              </div>
              {knowledgeStatus && (
                <div style={{ marginTop: 8, fontSize: 13, color: knowledgeStatus.startsWith("Ошибка") ? "#d63031" : "#00b894", fontWeight: 500 }}>
                  {knowledgeStatus}
                </div>
              )}
            </div>

            <div style={{ marginTop: 32 }}>
              <h3 style={{ marginBottom: 12 }}>Картинки для бота</h3>
              <p style={{ fontSize: 14, color: "#666", marginBottom: 12 }}>
                Загрузите картинки (прайс-лист, портфолио и т.д.).
                Бот сможет отправлять их клиентам.
              </p>
              <div
                className="upload-area"
                onClick={() => document.getElementById("image-upload")?.click()}
              >
                <div style={{ fontSize: 32, marginBottom: 8 }}>+</div>
                <div>{uploadingImage ? "Загрузка..." : "Загрузить картинку"}</div>
                <div style={{ fontSize: 12, color: "#999", marginTop: 4 }}>JPG, PNG, WEBP</div>
                <input
                  id="image-upload"
                  type="file"
                  accept="image/*"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file && selectedBot) {
                      setUploadingImage(true);
                      try {
                        const formData = new FormData();
                        formData.append("file", file);
                        const res = await fetch(`${API_URL}/api/bots/${selectedBot.id}/images`, {
                          method: "POST",
                          body: formData,
                        });
                        const data = await res.json();
                        if (data.url) {
                          setBotImages((prev) => [...prev, { id: "", url: data.url, name: data.filename }]);
                        }
                      } catch (err) {
                        console.error("Ошибка загрузки картинки:", err);
                      }
                      setUploadingImage(false);
                    }
                  }}
                />
              </div>

              {/* Список загруженных картинок */}
              {botImages.length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ fontSize: 13, color: "#666", marginBottom: 8 }}>Загруженные картинки:</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {botImages.map((img, i) => (
                      <div key={i} style={{ width: 80, textAlign: "center" }}>
                        <img
                          src={img.url}
                          alt={img.name}
                          style={{ width: 80, height: 80, objectFit: "cover", borderRadius: 8, border: "1px solid #ddd" }}
                        />
                        <div style={{ fontSize: 11, color: "#999", marginTop: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {img.name}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : selectedDialog ? (
          /* --- Диалог с сообщениями --- */
          <>
            <div className="main-header">
              <div>
                <h2>Чат: {selectedDialog.chat_id}</h2>
                <span style={{ fontSize: 12, color: "#999" }}>{selectedDialog.channel}</span>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className={`btn btn-sm ${selectedDialog.ai_disabled ? "btn-success" : "btn-danger"}`}
                  onClick={() => toggleAI(selectedDialog.id)}
                >
                  {selectedDialog.ai_disabled ? "Включить ИИ" : "Отключить ИИ (Перехват)"}
                </button>
                <button
                  className="btn btn-sm btn-outline"
                  onClick={() => { if (confirm("Удалить диалог?")) deleteDialog(selectedDialog.id); }}
                >
                  Удалить
                </button>
              </div>
            </div>

            <div className="messages-area">
              {messages.map((msg) => (
                <div key={msg.id} className={`message message-${msg.role}`}>
                  <div className="message-bubble">{msg.content}</div>
                  <div className="message-time">{formatTime(msg.created_at)}</div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
          </>
        ) : (
          <div className="empty-state">
            {selectedBot ? "Выберите диалог слева" : "Выберите бота"}
          </div>
        )}
      </div>
    </div>
  );
}
