"use client";

// Главная страница панели: авторизация, выбор бота, переключение разделов
// «Диалоги / Статистика / Настройки». Сами разделы — в папке components.

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  apiPost,
  apiRequest,
  Bot,
  City,
  Dialog,
  errorText,
  logoutAndRedirect,
  UserData,
} from "./components/api";
import DialogList from "./components/DialogList";
import ChatView from "./components/ChatView";
import StatsView from "./components/StatsView";
import SettingsView from "./components/SettingsView";

type View = "dialogs" | "stats" | "settings";

// Записываем открытый диалог в адресную строку (?dialog=<id>), чтобы ссылкой можно было поделиться
function setDialogInUrl(dialogId: string | null) {
  const url = new URL(window.location.href);
  if (dialogId) url.searchParams.set("dialog", dialogId);
  else url.searchParams.delete("dialog");
  window.history.replaceState(null, "", url.pathname + url.search);
}

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<UserData | null>(null);
  const [bots, setBots] = useState<Bot[]>([]);
  const [botsLoading, setBotsLoading] = useState(true);
  const [botsError, setBotsError] = useState("");
  const [selectedBotId, setSelectedBotId] = useState<string | null>(null);
  const [cities, setCities] = useState<City[]>([]);
  const [citiesKey, setCitiesKey] = useState(0);
  const [view, setView] = useState<View>("dialogs");
  const [selectedDialogId, setSelectedDialogId] = useState<string | null>(null);
  const [listRefreshKey, setListRefreshKey] = useState(0);
  // Создание бота
  const [showCreateBot, setShowCreateBot] = useState(false);
  const [newBotName, setNewBotName] = useState("");
  const [createError, setCreateError] = useState("");
  const [creating, setCreating] = useState(false);
  // Ссылку ?dialog=<id> обрабатываем один раз
  const deepLinkHandled = useRef(false);

  const selectedBot = bots.find((b) => b.id === selectedBotId) || null;
  const isOwner = selectedBot?.my_role === "owner";

  // Проверка авторизации
  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      // Сохраняем ?dialog=..., чтобы после входа открыть нужный диалог
      logoutAndRedirect(true);
      return;
    }
    const savedUser = localStorage.getItem("user");
    if (savedUser) {
      try {
        setUser(JSON.parse(savedUser));
      } catch {
        localStorage.removeItem("user");
      }
    }
    apiRequest<{ user?: UserData }>("/api/auth/me")
      .then((data) => {
        if (data.user) {
          setUser(data.user);
          localStorage.setItem("user", JSON.stringify(data.user));
        }
      })
      .catch(() => {
        // 401 уже обработан в apiRequest; при сетевой ошибке остаёмся с сохранённым пользователем
      });
  }, [router]);

  // Загрузка ботов
  const userId = user?.id;
  useEffect(() => {
    if (!userId) return;
    setBotsLoading(true);
    apiRequest<{ bots: Bot[] }>("/api/bots")
      .then(async (data) => {
        const list = data.bots || [];
        setBots(list);
        setBotsError("");

        // Открытие диалога по ссылке из Telegram: ?dialog=<id>
        const linkDialogId = new URLSearchParams(window.location.search).get("dialog");
        if (linkDialogId && !deepLinkHandled.current) {
          deepLinkHandled.current = true;
          try {
            const res = await apiRequest<{ dialog: Dialog }>(`/api/dialogs/${encodeURIComponent(linkDialogId)}/messages`);
            const botOfDialog = list.find((b) => b.id === res.dialog?.bot_id);
            if (botOfDialog) {
              setSelectedBotId(botOfDialog.id);
              setView("dialogs");
              setSelectedDialogId(linkDialogId);
              return;
            }
          } catch (err) {
            setBotsError(`Не удалось открыть диалог по ссылке: ${errorText(err)}`);
          }
          // Ссылка не сработала — убираем ?dialog= из адреса, чтобы ошибка не повторялась при обновлении
          setDialogInUrl(null);
        }

        // Иначе — последний выбранный бот или первый в списке
        const savedBotId = localStorage.getItem("selected_bot_id");
        const saved = list.find((b) => b.id === savedBotId);
        setSelectedBotId(saved ? saved.id : list.length > 0 ? list[0].id : null);
      })
      .catch((err) => setBotsError(errorText(err)))
      .finally(() => setBotsLoading(false));
  }, [userId]);

  // Запоминаем выбранного бота
  useEffect(() => {
    if (selectedBotId) localStorage.setItem("selected_bot_id", selectedBotId);
  }, [selectedBotId]);

  // Синхронизируем ?dialog= в адресе с открытым диалогом
  useEffect(() => {
    if (!deepLinkHandled.current && !selectedDialogId) return;
    deepLinkHandled.current = true;
    setDialogInUrl(selectedDialogId);
  }, [selectedDialogId]);

  // Города выбранного бота (нужны списку, чату и настройкам)
  useEffect(() => {
    if (!selectedBotId) {
      setCities([]);
      return;
    }
    let cancelled = false;
    apiRequest<{ cities: City[] }>(`/api/bots/${selectedBotId}/cities`)
      .then((data) => {
        if (!cancelled) setCities(data.cities || []);
      })
      .catch(() => {
        if (!cancelled) setCities([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBotId, citiesKey]);

  // Сотрудник не должен попасть в настройки
  useEffect(() => {
    if (view === "settings" && selectedBot && !isOwner) setView("dialogs");
  }, [view, selectedBot, isOwner]);

  const selectBot = (botId: string) => {
    setSelectedBotId(botId);
    setSelectedDialogId(null);
    setCities([]);
  };

  // Бот обновлён в настройках: сохраняем роль пользователя (её нет в ответе PUT)
  const handleBotUpdated = (updated: Bot) => {
    setBots((prev) =>
      prev.map((b) =>
        b.id === updated.id ? { ...b, ...updated, my_role: b.my_role, my_city_id: b.my_city_id } : b
      )
    );
  };

  const createBot = async () => {
    // Повторный Enter, пока бот создаётся, — не создаём второго
    if (creating) return;
    if (!newBotName.trim()) {
      setCreateError("Введите имя бота");
      return;
    }
    setCreating(true);
    setCreateError("");
    try {
      const data = await apiPost<{ bot: Bot }>("/api/bots", { name: newBotName.trim(), system_prompt: "" });
      const bot: Bot = { ...data.bot, my_role: "owner", my_city_id: null };
      setBots((prev) => [...prev, bot]);
      selectBot(bot.id);
      setView("settings");
      setNewBotName("");
      setShowCreateBot(false);
    } catch (err) {
      setCreateError(errorText(err));
    }
    setCreating(false);
  };

  if (!user) {
    return <div className="empty-state" style={{ height: "100vh" }}><span className="spinner" /> Загрузка…</div>;
  }

  return (
    <div className="app">
      {/* === ВЕРХНЯЯ ПАНЕЛЬ === */}
      <header className="topbar">
        <div className="topbar-logo">
          <span className="logo-mark">AI</span>
          <span className="logo-text">Sales Manager</span>
        </div>

        {bots.length > 0 && (
          <select
            className="input input-sm bot-select"
            value={selectedBotId || ""}
            onChange={(e) => selectBot(e.target.value)}
            title="Выбор бота"
          >
            {bots.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
                {b.wazzup_channel_phone ? ` · +${b.wazzup_channel_phone}` : ""}
                {b.my_role === "manager" ? " (сотрудник)" : ""}
              </option>
            ))}
          </select>
        )}
        <button className="btn btn-sm btn-outline" onClick={() => { setShowCreateBot(true); setCreateError(""); }} title="Создать бота">
          + Бот
        </button>

        {selectedBot && (
          <nav className="topbar-nav">
            <button className={view === "dialogs" ? "active" : ""} onClick={() => setView("dialogs")}>Диалоги</button>
            <button className={view === "stats" ? "active" : ""} onClick={() => setView("stats")}>Статистика</button>
            {isOwner && (
              <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>
                Настройки
                {!selectedBot.wazzup_connected && <span className="dot-warning" title="WhatsApp не подключён" />}
              </button>
            )}
          </nav>
        )}

        <div className="topbar-spacer" />

        {user.role === "admin" && (
          <button className="btn btn-sm btn-outline" onClick={() => router.push("/admin")}>Админ-панель</button>
        )}
        <span className="topbar-user" title={user.email}>{user.email}</span>
        <button className="btn-logout" onClick={() => logoutAndRedirect(false)}>Выйти</button>
      </header>

      {botsError && <div className="alert alert-error app-alert">{botsError}</div>}

      {/* === СОДЕРЖИМОЕ === */}
      <div className="app-body">
        {botsLoading && bots.length === 0 ? (
          <div className="empty-state"><span className="spinner" /> Загружаем ботов…</div>
        ) : !selectedBot ? (
          <div className="empty-state">
            <div className="empty-title">У вас пока нет ботов</div>
            <div>Создайте бота и подключите WhatsApp через Wazzup.</div>
            <button className="btn btn-primary" onClick={() => setShowCreateBot(true)}>+ Создать бота</button>
          </div>
        ) : view === "stats" ? (
          <div className="page-scroll"><StatsView key={selectedBot.id} botId={selectedBot.id} /></div>
        ) : view === "settings" && isOwner ? (
          <div className="page-scroll">
            <SettingsView
              bot={selectedBot}
              cities={cities}
              onBotUpdated={handleBotUpdated}
              onCitiesChanged={() => setCitiesKey((k) => k + 1)}
            />
          </div>
        ) : (
          <div className={`dialogs-layout ${selectedDialogId ? "has-selection" : ""}`}>
            <aside className="dialogs-panel">
              {isOwner && !selectedBot.wazzup_connected && (
                <div className="banner-warning">
                  WhatsApp не подключён.{" "}
                  <button className="link-button" onClick={() => setView("settings")}>Подключить →</button>
                </div>
              )}
              <DialogList
                botId={selectedBot.id}
                cities={cities}
                lockedCityId={selectedBot.my_city_id}
                selectedId={selectedDialogId}
                onSelect={setSelectedDialogId}
                refreshKey={listRefreshKey}
              />
            </aside>
            <main className="chat-panel">
              {selectedDialogId ? (
                <ChatView
                  key={selectedDialogId}
                  dialogId={selectedDialogId}
                  cities={cities}
                  isOwner={isOwner}
                  lockedCityId={selectedBot.my_city_id}
                  onChanged={() => setListRefreshKey((k) => k + 1)}
                  onDeleted={() => {
                    setSelectedDialogId(null);
                    setListRefreshKey((k) => k + 1);
                  }}
                  onBack={() => setSelectedDialogId(null)}
                />
              ) : (
                <div className="empty-state">
                  <div className="empty-icon">💬</div>
                  <div>Выберите диалог слева</div>
                  <div className="text-muted" style={{ fontSize: 13 }}>
                    Горячие клиенты 🔥 подсвечены оранжевым — им нужно отправить счёт
                  </div>
                </div>
              )}
            </main>
          </div>
        )}
      </div>

      {/* === СОЗДАНИЕ БОТА === */}
      {showCreateBot && (
        <div className="modal-overlay" onClick={() => setShowCreateBot(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>Новый бот</h3>
            <div className="form-group">
              <label>Имя бота</label>
              <input
                autoFocus
                value={newBotName}
                onChange={(e) => setNewBotName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && createBot()}
                placeholder="Например: Продажи WhatsApp"
              />
            </div>
            {createError && <div className="alert alert-error" style={{ marginBottom: 12 }}>{createError}</div>}
            <div className="form-actions">
              <button className="btn btn-primary" onClick={createBot} disabled={creating}>
                {creating ? "Создаём…" : "Создать"}
              </button>
              <button className="btn btn-outline" onClick={() => setShowCreateBot(false)}>Отмена</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
