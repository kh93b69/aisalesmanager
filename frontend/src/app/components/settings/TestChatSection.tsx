"use client";

// Настройки → Тест бота: переписка с ботом прямо в панели.
// Бот отвечает так же, как в WhatsApp (та же анкета, база знаний, модель),
// но ничего не сохраняется, клиенту ничего не уходит, уведомлений нет.

import { useEffect, useRef, useState } from "react";
import { apiPost, Bot, City, errorText, TestChatReply } from "../api";

type Props = {
  bot: Bot;
  cities: City[];
};

// Одно сообщение тестового чата (живёт только в браузере)
type ChatItem = {
  role: "user" | "assistant";
  content: string;
  // Служебные поля ответа бота — показываем под сообщением
  info?: TestChatReply;
};

// Тег картинки, который бот вставляет в ответ (только https)
const IMAGE_TAG = /\[IMAGE:(https:\/\/[^\]]+)\]/g;

function splitImages(content: string): { text: string; images: string[] } {
  const images: string[] = [];
  const text = content.replace(IMAGE_TAG, (_match, url: string) => {
    images.push(url);
    return "";
  });
  return { text: text.trim(), images };
}

// Подписи под ответом бота: город, горячий клиент, нужен человек, токены
function ReplyInfo({ info }: { info: TestChatReply }) {
  const usage = info.usage || {};
  const inputTotal = (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0) + (usage.cache_creation_input_tokens || 0);
  return (
    <div className="test-chat-info">
      {info.city_name && <span className="test-chat-tag">📍 {info.city_name}</span>}
      {info.lead_status === "hot" && (
        <span className="test-chat-tag test-chat-tag-hot">
          🔥 Горячий клиент{info.lead_summary ? `: ${info.lead_summary}` : ""}
        </span>
      )}
      {info.need_human && (
        <span className="test-chat-tag test-chat-tag-warn">
          ⚠️ Нужен менеджер{info.need_human_reason ? `: ${info.need_human_reason}` : ""}
        </span>
      )}
      {info.unknown_question && (
        <span className="test-chat-tag test-chat-tag-warn">
          ❓ Нет в базе знаний: {info.unknown_question} (в WhatsApp руководителю придёт уведомление)
        </span>
      )}
      {info.client_name && <span className="test-chat-tag">👤 {info.client_name}</span>}
      {!info.ok && <span className="test-chat-tag test-chat-tag-warn">ИИ не ответил — показан запасной текст</span>}
      <span className="test-chat-tokens">
        токены: вход {inputTotal.toLocaleString("ru-RU")}
        {usage.cache_read_input_tokens ? ` (из кэша ${usage.cache_read_input_tokens.toLocaleString("ru-RU")})` : ""}
        , выход {(usage.output_tokens || 0).toLocaleString("ru-RU")}
      </span>
    </div>
  );
}

export default function TestChatSection({ bot, cities }: Props) {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [text, setText] = useState("");
  // Город теста: "" — бот определяет сам (как в WhatsApp)
  const [cityId, setCityId] = useState("");
  // Клиент уже стал «горячим» — дальше бот не продаёт повторно (как в WhatsApp)
  const [isHot, setIsHot] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // Новый бот — новый чат
  useEffect(() => {
    setItems([]);
    setCityId("");
    setIsHot(false);
    setError("");
  }, [bot.id]);

  // Прокрутка вниз при новых сообщениях
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [items, sending]);

  const send = async () => {
    const content = text.trim();
    if (!content || sending) return;
    const withUser: ChatItem[] = [...items, { role: "user", content }];
    setItems(withUser);
    setText("");
    setSending(true);
    setError("");
    try {
      const data = await apiPost<TestChatReply>(`/api/bots/${bot.id}/test-chat`, {
        messages: withUser.map((item) => ({ role: item.role, content: item.content })),
        city_id: cityId || null,
        status: isHot ? "hot" : "in_progress",
      });
      setItems([...withUser, { role: "assistant", content: data.reply, info: data }]);
      // Бот определил город — дальше тестируем в нём (как диалог в WhatsApp)
      if (data.city_id) setCityId(data.city_id);
      if (data.lead_status === "hot") setIsHot(true);
    } catch (err) {
      setError(errorText(err));
      // Сообщение не потеряно: возвращаем его в поле ввода
      setItems(items);
      setText(content);
    }
    setSending(false);
  };

  const reset = () => {
    setItems([]);
    setCityId("");
    setIsHot(false);
    setError("");
  };

  return (
    <div className="settings-section">
      <h3>Тест бота</h3>
      <p>Напишите боту как клиент. Он отвечает так же, как в WhatsApp (анкета, база знаний, модель),
        но переписка нигде не сохраняется, клиенту ничего не уходит, уведомления не приходят.
        Сначала сохраните изменения во вкладках «О компании и ИИ» и «База знаний».</p>

      <div className="test-chat-toolbar">
        {cities.length > 0 && (
          <label className="test-chat-city">
            Город:
            <select className="input input-sm" value={cityId} onChange={(e) => setCityId(e.target.value)} disabled={sending}>
              <option value="">бот спросит сам</option>
              {cities.map((city) => (
                <option key={city.id} value={city.id}>{city.name}</option>
              ))}
            </select>
          </label>
        )}
        <button className="btn btn-outline btn-sm" onClick={reset} disabled={sending || items.length === 0}>
          Начать заново
        </button>
      </div>

      <div className="test-chat chat">
        <div className="messages-area" ref={scrollRef}>
          {items.length === 0 && (
            <div className="list-placeholder">
              Например: «Здравствуйте, сколько стоит?» — и посмотрите, как бот консультирует и когда помечает клиента 🔥
            </div>
          )}
          {items.map((item, index) => {
            const parsed = item.role === "assistant" ? splitImages(item.content) : { text: item.content, images: [] as string[] };
            return (
              <div key={index} className={`message message-${item.role}`}>
                <div className="message-author">{item.role === "assistant" ? "🤖 ИИ-менеджер" : "Вы (клиент)"}</div>
                <div className="message-bubble">
                  {parsed.text && <div className="message-text">{parsed.text}</div>}
                  {parsed.images.map((url) => (
                    <a key={url} href={url} target="_blank" rel="noreferrer">
                      <img className="message-image" src={url} alt="Картинка" />
                    </a>
                  ))}
                </div>
                {item.info && <ReplyInfo info={item.info} />}
              </div>
            );
          })}
          {sending && <div className="list-placeholder">Бот печатает…</div>}
        </div>

        <div className="composer">
          <textarea
            className="input composer-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter — отправить, Shift+Enter — новая строка
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Сообщение от имени клиента…  (Enter — отправить)"
            rows={2}
            disabled={sending}
          />
          <button className="btn btn-primary composer-send" disabled={sending || !text.trim()} onClick={send}>
            {sending ? "…" : "Отправить"}
          </button>
        </div>
      </div>
      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}
    </div>
  );
}
