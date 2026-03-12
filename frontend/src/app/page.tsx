"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";

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

type UserData = {
  id: string;
  email: string;
  role?: string;
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

// Обёртка для fetch с авторизацией
function authFetch(url: string, options: RequestInit = {}) {
  const token = localStorage.getItem("access_token");
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> || {}),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  // Не ставим Content-Type для FormData (браузер сам поставит boundary)
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
  }

  return fetch(url, { ...options, headers });
}

// --- Главный компонент ---

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<UserData | null>(null);
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
  const [showCreateBot, setShowCreateBot] = useState(false);
  const [newBotName, setNewBotName] = useState("");
  const [waStatus, setWaStatus] = useState("");
  const [waQr, setWaQr] = useState("");
  const [waLoading, setWaLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Проверка авторизации
  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      router.push("/login");
      return;
    }

    const savedUser = localStorage.getItem("user");
    if (savedUser) {
      setUser(JSON.parse(savedUser));
    }

    // Проверяем токен на сервере
    authFetch(`${API_URL}/api/auth/me`)
      .then((r) => {
        if (!r.ok) {
          localStorage.removeItem("access_token");
          localStorage.removeItem("user");
          router.push("/login");
          return null;
        }
        return r.json();
      })
      .then((data) => {
        if (data?.user) {
          setUser(data.user);
          localStorage.setItem("user", JSON.stringify(data.user));
        }
      })
      .catch(() => {
        router.push("/login");
      });
  }, [router]);

  // Загрузка ботов
  useEffect(() => {
    if (!user) return;

    authFetch(`${API_URL}/api/bots`)
      .then((r) => r.json())
      .then((data) => {
        setBots(data.bots || []);
        if (data.bots?.length > 0) {
          setSelectedBot(data.bots[0]);
        }
      })
      .catch((err) => console.error("Ошибка загрузки ботов:", err));
  }, [user]);

  // Загрузка диалогов при выборе бота
  useEffect(() => {
    if (!selectedBot) return;
    setEditPrompt(selectedBot.system_prompt);
    setEditName(selectedBot.name);

    // Проверяем статус WhatsApp
    setWaStatus("");
    setWaQr("");

    // Загружаем картинки бота
    authFetch(`${API_URL}/api/bots/${selectedBot.id}/images`)
      .then((r) => r.json())
      .then((data) => setBotImages(data.images || []))
      .catch(() => setBotImages([]));

    const loadDialogs = () => {
      authFetch(`${API_URL}/api/bots/${selectedBot.id}/dialogs`)
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
      authFetch(`${API_URL}/api/dialogs/${selectedDialog.id}/messages`)
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

  // Выход
  const logout = () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem("user");
    router.push("/login");
  };

  // Переключение ИИ
  const toggleAI = async (dialogId: string) => {
    const res = await authFetch(`${API_URL}/api/dialogs/${dialogId}/toggle-ai`, { method: "POST" });
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
    const res = await authFetch(`${API_URL}/api/bots/${selectedBot.id}`, {
      method: "PUT",
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
      const res = await authFetch(`${API_URL}/api/bots/${selectedBot.id}/knowledge`, {
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
    await authFetch(`${API_URL}/api/dialogs/${dialogId}`, { method: "DELETE" });
    setDialogs((prev) => prev.filter((d) => d.id !== dialogId));
    if (selectedDialog?.id === dialogId) {
      setSelectedDialog(null);
      setMessages([]);
    }
  };

  // Создание нового бота
  const createBot = async () => {
    if (!newBotName.trim()) return;
    const res = await authFetch(`${API_URL}/api/bots`, {
      method: "POST",
      body: JSON.stringify({ name: newBotName, system_prompt: "" }),
    });
    const data = await res.json();
    if (data.bot) {
      setBots((prev) => [...prev, data.bot]);
      setSelectedBot(data.bot);
      setNewBotName("");
      setShowCreateBot(false);
    }
  };

  // Проверка статуса WhatsApp
  const checkWaStatus = async () => {
    if (!selectedBot) return;
    try {
      const res = await authFetch(`${API_URL}/api/bots/${selectedBot.id}/whatsapp/status`);
      const data = await res.json();
      setWaStatus(data.status || "NOT_FOUND");

      // Если ожидает QR — загружаем QR-код
      if (data.status === "SCAN_QR_CODE") {
        const qrRes = await authFetch(`${API_URL}/api/bots/${selectedBot.id}/whatsapp/qr`);
        const qrData = await qrRes.json();
        if (qrData.qr) {
          setWaQr(qrData.qr);
        }
      } else {
        setWaQr("");
      }
    } catch {
      setWaStatus("ERROR");
    }
  };

  // Подключить WhatsApp
  const connectWhatsApp = async () => {
    if (!selectedBot) return;
    setWaLoading(true);
    try {
      await authFetch(`${API_URL}/api/bots/${selectedBot.id}/whatsapp/start`, { method: "POST" });
      // Ждём немного и проверяем статус
      setTimeout(() => checkWaStatus(), 2000);
    } catch (err) {
      console.error("Ошибка подключения WhatsApp:", err);
    }
    setWaLoading(false);
  };

  // Отключить WhatsApp
  const disconnectWhatsApp = async () => {
    if (!selectedBot) return;
    setWaLoading(true);
    try {
      await authFetch(`${API_URL}/api/bots/${selectedBot.id}/whatsapp/stop`, { method: "POST" });
      setWaStatus("STOPPED");
      setWaQr("");
    } catch (err) {
      console.error("Ошибка отключения WhatsApp:", err);
    }
    setWaLoading(false);
  };

  if (!user) {
    return <div className="empty-state">Загрузка...</div>;
  }

  return (
    <div style={{ display: "flex", height: "100vh" }}>
      {/* === БОКОВАЯ ПАНЕЛЬ === */}
      <div className="sidebar">
        <div className="sidebar-header">
          <h2>AI Sales Manager</h2>
          <div style={{ display: "flex", gap: 4 }}>
            <button className="btn btn-sm btn-outline" onClick={() => setShowSettings(!showSettings)}>
              {showSettings ? "Диалоги" : "Настройки"}
            </button>
          </div>
        </div>

        {/* Инфо о пользователе */}
        <div className="user-info">
          <span>{user.email}</span>
          <button className="btn-logout" onClick={logout}>Выйти</button>
        </div>

        {/* Список ботов */}
        <div className="section-title">
          <span>Боты</span>
          <button className="btn btn-sm btn-outline" onClick={() => setShowCreateBot(!showCreateBot)}>+</button>
        </div>

        {showCreateBot && (
          <div style={{ padding: "8px 20px" }}>
            <input
              value={newBotName}
              onChange={(e) => setNewBotName(e.target.value)}
              placeholder="Имя бота"
              style={{ marginBottom: 8, width: "100%", padding: 8, borderRadius: 6, border: "1px solid #ddd" }}
              onKeyDown={(e) => e.key === "Enter" && createBot()}
            />
            <button className="btn btn-sm btn-primary" onClick={createBot}>Создать</button>
          </div>
        )}

        {bots.map((bot) => (
          <div
            key={bot.id}
            className={`bot-item ${selectedBot?.id === bot.id ? "active" : ""}`}
            onClick={() => { setSelectedBot(bot); setSelectedDialog(null); setMessages([]); }}
          >
            <div style={{ fontWeight: 500 }}>{bot.name}</div>
            <div style={{ fontSize: 12, color: "#999", marginTop: 2 }}>
              {bot.system_prompt ? bot.system_prompt.slice(0, 50) + "..." : "Промпт не задан"}
            </div>
          </div>
        ))}

        {/* Список диалогов */}
        {selectedBot && !showSettings && (
          <>
            <div className="section-title" style={{ borderTop: "1px solid #e1e4e8" }}>
              <span>Диалоги ({dialogs.length})</span>
            </div>
            <div style={{ flex: 1, overflowY: "auto" }}>
              {dialogs.length === 0 && (
                <div style={{ padding: 20, color: "#999", textAlign: "center" }}>
                  Нет диалогов. Напишите боту в мессенджере!
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
                    <div className="dialog-meta">
                      <span className={`channel-badge channel-${dialog.channel}`}>{dialog.channel}</span>
                      &nbsp;{formatDate(dialog.created_at)}
                    </div>
                  </div>
                  <span className={`badge ${dialog.ai_disabled ? "badge-red" : "badge-green"}`}>
                    {dialog.ai_disabled ? "ИИ выкл" : "ИИ вкл"}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}

        {/* Ссылка на админку (если admin) */}
        {user.role === "admin" && (
          <div style={{ padding: "12px 20px", borderTop: "1px solid #e1e4e8" }}>
            <button className="btn btn-sm btn-outline" style={{ width: "100%" }} onClick={() => router.push("/admin")}>
              Админка
            </button>
          </div>
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
                        const res = await authFetch(`${API_URL}/api/bots/${selectedBot.id}/images`, {
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

            {/* --- WhatsApp --- */}
            <div style={{ marginTop: 32 }}>
              <h3 style={{ marginBottom: 12 }}>WhatsApp</h3>
              <p style={{ fontSize: 14, color: "#666", marginBottom: 12 }}>
                Подключите WhatsApp для приёма сообщений от клиентов.
              </p>

              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
                <span style={{
                  padding: "4px 12px",
                  borderRadius: 12,
                  fontSize: 13,
                  fontWeight: 500,
                  background: waStatus === "WORKING" ? "#e6f9f0" : waStatus === "SCAN_QR_CODE" ? "#fff3e0" : "#f5f5f5",
                  color: waStatus === "WORKING" ? "#00b894" : waStatus === "SCAN_QR_CODE" ? "#e17055" : "#999",
                }}>
                  {waStatus === "WORKING" ? "Подключен" :
                   waStatus === "SCAN_QR_CODE" ? "Ожидание QR" :
                   waStatus === "STARTING" ? "Запускается..." :
                   waStatus === "STOPPED" ? "Остановлен" :
                   waStatus === "ERROR" ? "Ошибка" :
                   waStatus === "NOT_FOUND" ? "Не подключен" :
                   waStatus || "Проверьте статус"}
                </span>
                <button className="btn btn-sm btn-outline" onClick={checkWaStatus} disabled={waLoading}>
                  Обновить
                </button>
              </div>

              {waStatus !== "WORKING" && (
                <button className="btn btn-primary" onClick={connectWhatsApp} disabled={waLoading} style={{ marginRight: 8 }}>
                  {waLoading ? "Подключение..." : "Подключить WhatsApp"}
                </button>
              )}

              {waStatus === "WORKING" && (
                <button className="btn btn-danger" onClick={disconnectWhatsApp} disabled={waLoading}>
                  Отключить WhatsApp
                </button>
              )}

              {waQr && (
                <div style={{ marginTop: 16, textAlign: "center" }}>
                  <p style={{ fontSize: 14, color: "#666", marginBottom: 8 }}>
                    Отсканируйте QR-код в WhatsApp (Настройки &rarr; Связанные устройства)
                  </p>
                  <img
                    src={waQr}
                    alt="WhatsApp QR Code"
                    style={{ width: 256, height: 256, border: "1px solid #ddd", borderRadius: 8 }}
                  />
                  <div style={{ marginTop: 8 }}>
                    <button className="btn btn-sm btn-outline" onClick={checkWaStatus}>
                      Проверить подключение
                    </button>
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
                <span style={{ fontSize: 12, color: "#999" }}>
                  <span className={`channel-badge channel-${selectedDialog.channel}`}>{selectedDialog.channel}</span>
                </span>
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
            {selectedBot ? "Выберите диалог слева" : "Выберите бота или создайте нового"}
          </div>
        )}
      </div>
    </div>
  );
}
