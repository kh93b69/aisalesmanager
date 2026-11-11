"use client";

// Окно диалога: шапка (клиент, город, статус, кнопки), блоки «Что хочет клиент» и «Внимание»,
// лента сообщений (опрос раз в 3 секунды) и поле ответа клиенту от сотрудника.

import { useEffect, useRef, useState } from "react";
import {
  AI_PAUSE_LABELS,
  apiDelete,
  apiPost,
  apiRequest,
  City,
  Dialog,
  DialogStatus,
  dialogTitle,
  errorText,
  formatMessageTime,
  formatPhone,
  Message,
  minutesSince,
  STATUS_BADGE,
  STATUS_LABELS,
} from "./api";

type Props = {
  dialogId: string;
  cities: City[];
  isOwner: boolean;
  // Сотрудник с закреплённым городом не может переносить диалог в другой город
  lockedCityId: string | null;
  // Вызывается после любых изменений диалога (чтобы обновить список)
  onChanged: () => void;
  onDeleted: () => void;
  // Кнопка «назад» на телефоне
  onBack: () => void;
};

// Тег картинки, который бот вставляет в ответ (только https)
const IMAGE_TAG = /\[IMAGE:(https:\/\/[^\]]+)\]/g;

// Достаём из текста ссылки на картинки и текст без тегов
function splitImages(content: string): { text: string; images: string[] } {
  const images: string[] = [];
  const text = content.replace(IMAGE_TAG, (_match, url: string) => {
    images.push(url);
    return "";
  });
  return { text: text.trim(), images };
}

// Кнопки смены статуса в зависимости от текущего статуса
function statusActions(status: DialogStatus): { status: DialogStatus; label: string; cls: string }[] {
  if (status === "paid" || status === "lost") {
    return [{ status: "in_progress", label: "↩ Вернуть в работу", cls: "btn-outline" }];
  }
  if (status === "invoice_sent") {
    return [
      { status: "paid", label: "✅ Оплачено", cls: "btn-success" },
      { status: "lost", label: "Потерян", cls: "btn-outline" },
      { status: "in_progress", label: "↩ Вернуть в работу", cls: "btn-outline" },
    ];
  }
  // new, in_progress, hot
  return [
    { status: "invoice_sent", label: "🧾 Счёт отправлен", cls: status === "hot" ? "btn-orange" : "btn-primary" },
    { status: "paid", label: "✅ Оплачено", cls: "btn-outline" },
    { status: "lost", label: "Потерян", cls: "btn-outline" },
  ];
}

// Одно сообщение в ленте
function MessageBubble({ msg }: { msg: Message }) {
  // Теги [IMAGE:url] превращаем в картинки только в ответах бота:
  // у клиента такой текст может оказаться чужой (трекинговой) ссылкой
  const parsed = msg.role === "assistant" ? splitImages(msg.content || "") : { text: msg.content || "", images: [] as string[] };
  const { text, images } = parsed;
  const type = msg.message_type || "text";
  // Ссылку на медиа показываем только если она https (ссылку присылает внешний сервис)
  const mediaUrl = msg.media_url && msg.media_url.startsWith("https://") ? msg.media_url : null;

  let author = "Клиент";
  if (msg.role === "assistant") author = "🤖 ИИ-менеджер";
  if (msg.role === "manager") author = `👤 ${msg.author_name || "Сотрудник"}`;

  return (
    <div className={`message message-${msg.role}`}>
      <div className="message-author">{author}</div>
      <div className="message-bubble">
        {/* Медиа из WhatsApp */}
        {mediaUrl && type === "audio" && (
          <a className="media-link" href={mediaUrl} target="_blank" rel="noreferrer">▶ Голосовое сообщение</a>
        )}
        {mediaUrl && type === "image" && (
          <a href={mediaUrl} target="_blank" rel="noreferrer">
            <img className="message-image" src={mediaUrl} alt="Фото" />
          </a>
        )}
        {mediaUrl && type !== "audio" && type !== "image" && (
          <a className="media-link" href={mediaUrl} target="_blank" rel="noreferrer">
            📎 {type === "video" ? "Видео" : type === "document" ? "Документ" : "Вложение"} — открыть
          </a>
        )}
        {text && <div className="message-text">{text}</div>}
        {/* Картинки, которые отправил бот */}
        {images.map((url) => (
          <a key={url} href={url} target="_blank" rel="noreferrer">
            <img className="message-image" src={url} alt="Картинка" />
          </a>
        ))}
      </div>
      {msg.send_error && <div className="message-error">⚠ Не доставлено клиенту: {msg.send_error}</div>}
      <div className="message-time">{formatMessageTime(msg.created_at)}</div>
    </div>
  );
}

export default function ChatView({ dialogId, cities, isOwner, lockedCityId, onChanged, onDeleted, onBack }: Props) {
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  // Счётчик для принудительной перезагрузки после действий
  const [reloadKey, setReloadKey] = useState(0);

  const scrollRef = useRef<HTMLDivElement>(null);
  // Пользователь внизу ленты? Если он прокрутил вверх — не дёргаем скролл
  const atBottomRef = useRef(true);
  // Последнее сообщение, до которого уже прокрутили
  const lastMessageIdRef = useRef("");

  // При смене диалога сбрасываем состояние
  useEffect(() => {
    setDialog(null);
    setMessages([]);
    setLoading(true);
    setLoadError("");
    setActionError("");
    setText("");
    atBottomRef.current = true;
    lastMessageIdRef.current = "";
  }, [dialogId]);

  // Загрузка сообщений + опрос каждые 3 секунды
  useEffect(() => {
    let cancelled = false;
    // Предыдущий запрос ещё идёт — новый не отправляем
    let inFlight = false;
    const load = (force: boolean) => {
      if (inFlight) return;
      // Вкладка свёрнута — не опрашиваем сервер зря
      if (!force && document.hidden) return;
      inFlight = true;
      apiRequest<{ messages: Message[]; dialog: Dialog }>(`/api/dialogs/${dialogId}/messages`)
        .then((data) => {
          if (cancelled) return;
          setMessages(data.messages || []);
          if (data.dialog) setDialog(data.dialog);
          setLoadError("");
        })
        .catch((err) => {
          if (!cancelled) setLoadError(errorText(err));
        })
        .finally(() => {
          inFlight = false;
          if (!cancelled) setLoading(false);
        });
    };
    load(true);
    const interval = setInterval(() => load(false), 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [dialogId, reloadKey]);

  // Автоскролл: только если пришло новое сообщение и пользователь внизу
  useEffect(() => {
    const box = scrollRef.current;
    if (!box || messages.length === 0) return;
    const lastId = messages[messages.length - 1].id;
    if (lastId === lastMessageIdRef.current) return;
    const firstTime = lastMessageIdRef.current === "";
    lastMessageIdRef.current = lastId;
    if (firstTime || atBottomRef.current) {
      box.scrollTop = box.scrollHeight;
    }
  }, [messages]);

  const handleScroll = () => {
    const box = scrollRef.current;
    if (!box) return;
    atBottomRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  };

  // Общая обёртка для действий с диалогом
  const runAction = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setActionError("");
    try {
      await action();
      setReloadKey((k) => k + 1);
      onChanged();
    } catch (err) {
      setActionError(errorText(err));
    }
    setBusy(false);
  };

  const changeStatus = (status: DialogStatus) =>
    runAction(async () => {
      const data = await apiPost<{ dialog?: Dialog }>(`/api/dialogs/${dialogId}/status`, { status });
      if (data.dialog) setDialog((prev) => (prev ? { ...prev, ...data.dialog } : prev));
    });

  const toggleAI = () =>
    runAction(async () => {
      const data = await apiPost<{ ai_disabled?: boolean }>(`/api/dialogs/${dialogId}/toggle-ai`);
      if (typeof data.ai_disabled === "boolean") {
        setDialog((prev) => (prev ? { ...prev, ai_disabled: data.ai_disabled as boolean } : prev));
      }
    });

  const changeCity = (cityId: string) =>
    runAction(() => apiPost(`/api/dialogs/${dialogId}/city`, { city_id: cityId || null }));

  const resolveAttention = () =>
    runAction(async () => {
      await apiPost(`/api/dialogs/${dialogId}/resolve-attention`);
      setDialog((prev) => (prev ? { ...prev, needs_attention: false } : prev));
    });

  const deleteDialog = async () => {
    if (!confirm("Удалить диалог вместе со всей перепиской? Это действие нельзя отменить.")) return;
    setBusy(true);
    try {
      await apiDelete(`/api/dialogs/${dialogId}`);
      onDeleted();
    } catch (err) {
      setActionError(errorText(err));
      setBusy(false);
    }
  };

  // Отправка сообщения клиенту от имени сотрудника
  const sendMessage = async () => {
    const value = text.trim();
    if (!value || sending) return;
    setSending(true);
    setActionError("");
    try {
      await apiPost(`/api/dialogs/${dialogId}/send`, { text: value });
      setText("");
      atBottomRef.current = true;
      setReloadKey((k) => k + 1);
      onChanged();
    } catch (err) {
      setActionError(`Сообщение не отправлено: ${errorText(err)}`);
    }
    setSending(false);
  };

  if (loading && !dialog) {
    return <div className="empty-state"><span className="spinner" /> Загружаем диалог…</div>;
  }

  if (!dialog) {
    return (
      <div className="empty-state">
        <div>Не удалось открыть диалог</div>
        {loadError && <div className="alert alert-error">{loadError}</div>}
        <button className="btn btn-outline btn-sm" onClick={onBack}>← К списку</button>
      </div>
    );
  }

  const title = dialogTitle(dialog);
  const phone = formatPhone(dialog.chat_id);
  const waitingMinutes = dialog.status === "hot" ? minutesSince(dialog.hot_at) : null;
  const pauseReason = dialog.ai_paused_reason ? AI_PAUSE_LABELS[dialog.ai_paused_reason] : "";

  return (
    <div className="chat">
      {/* --- Шапка --- */}
      <div className={`chat-header ${dialog.status === "hot" ? "chat-header-hot" : ""}`}>
        <div className="chat-header-row">
          <button className="btn btn-sm btn-outline mobile-only" onClick={onBack}>←</button>
          <div className="chat-client">
            <div className="chat-client-name">{title}</div>
            <div className="chat-client-sub">
              {title !== phone && <span>{phone}</span>}
              <span className={`channel-badge channel-${dialog.channel}`}>{dialog.channel}</span>
              <span className={`badge ${STATUS_BADGE[dialog.status] || "badge-gray"}`}>
                {STATUS_LABELS[dialog.status] || dialog.status}
              </span>
              {waitingMinutes !== null && <span className="wait-chip">ждёт {waitingMinutes} мин</span>}
            </div>
          </div>
          <div className="chat-city">
            <label>Город</label>
            <select
              className="input input-sm"
              value={dialog.city_id || ""}
              disabled={busy || !!lockedCityId}
              onChange={(e) => changeCity(e.target.value)}
            >
              <option value="">— не определён —</option>
              {cities.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="chat-actions">
          {statusActions(dialog.status).map((a) => (
            <button
              key={a.status}
              className={`btn btn-sm ${a.cls}`}
              disabled={busy}
              onClick={() => changeStatus(a.status)}
            >
              {a.label}
            </button>
          ))}
          <div className="chat-actions-spacer" />
          <div className={`ai-toggle ${dialog.ai_disabled ? "off" : "on"}`}>
            <span className="ai-toggle-dot" />
            <span>
              {dialog.ai_disabled ? `ИИ на паузе${pauseReason ? ` — ${pauseReason}` : ""}` : "ИИ отвечает"}
            </span>
            <button className="btn btn-sm btn-outline" disabled={busy} onClick={toggleAI}>
              {dialog.ai_disabled ? "Включить ИИ" : "Поставить на паузу"}
            </button>
          </div>
          {isOwner && (
            <button className="btn btn-sm btn-ghost-danger" disabled={busy} onClick={deleteDialog} title="Удалить диалог">
              Удалить
            </button>
          )}
        </div>
      </div>

      {/* --- Информационные блоки --- */}
      {(dialog.hot_reason || dialog.needs_attention || actionError || loadError) && (
        <div className="chat-notices">
          {dialog.hot_reason && (
            <div className={`notice notice-hot ${dialog.status === "hot" ? "pulse" : ""}`}>
              <div className="notice-title">🔥 Что хочет клиент</div>
              <div className="notice-text">{dialog.hot_reason}</div>
              {dialog.status === "hot" && (
                <div className="notice-hint">Отправьте клиенту счёт и нажмите «Счёт отправлен».</div>
              )}
            </div>
          )}
          {dialog.needs_attention && (
            <div className="notice notice-attention">
              <div className="notice-body">
                <div className="notice-title">⚠️ Нужно внимание менеджера</div>
                <div className="notice-text">{dialog.attention_reason || "ИИ не смог ответить сам"}</div>
              </div>
              <button className="btn btn-sm btn-outline" disabled={busy} onClick={resolveAttention}>
                ✓ Решено
              </button>
            </div>
          )}
          {actionError && <div className="alert alert-error">{actionError}</div>}
          {loadError && <div className="alert alert-error">Не удалось обновить сообщения: {loadError}</div>}
        </div>
      )}

      {/* --- Сообщения --- */}
      <div className="messages-area" ref={scrollRef} onScroll={handleScroll}>
        {messages.length === 0 && <div className="list-placeholder">Сообщений пока нет</div>}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}
      </div>

      {/* --- Поле ответа --- */}
      <div className="composer">
        <textarea
          className="input composer-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // Enter — отправить, Shift+Enter — новая строка
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              sendMessage();
            }
          }}
          placeholder="Написать клиенту в WhatsApp…  (Enter — отправить, Shift+Enter — новая строка)"
          rows={2}
          disabled={sending}
        />
        <button className="btn btn-primary composer-send" disabled={sending || !text.trim()} onClick={sendMessage}>
          {sending ? "Отправка…" : "Отправить"}
        </button>
      </div>
      <div className="composer-hint">
        Сообщение уйдёт клиенту с номера компании. После этого ИИ в диалоге встанет на паузу.
      </div>
    </div>
  );
}
