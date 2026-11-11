"use client";

// Список диалогов: вкладки-фильтры со счётчиками, фильтр по городу, поиск.
// Обновляется сам каждые 5 секунд.

import { useEffect, useState } from "react";
import {
  apiRequest,
  City,
  Dialog,
  DialogCounts,
  dialogTitle,
  errorText,
  formatPhone,
  formatShortTime,
  STATUS_BADGE,
  STATUS_LABELS,
} from "./api";

// Вкладки-фильтры. key — ключ в counts, query — параметры запроса.
const TABS = [
  { key: "all", label: "Все", query: "" },
  { key: "hot", label: "🔥 Нужен счёт", query: "status=hot" },
  { key: "invoice_sent", label: "Счёт отправлен", query: "status=invoice_sent" },
  { key: "attention", label: "⚠️ Внимание", query: "attention=1" },
  { key: "paid", label: "Оплачено", query: "status=paid" },
];

type Props = {
  botId: string;
  cities: City[];
  // Если у сотрудника закреплён город — фильтр по городу не показываем
  lockedCityId: string | null;
  selectedId: string | null;
  onSelect: (dialogId: string) => void;
  // Увеличивается, когда диалог изменили в чате — список перезагружается сразу
  refreshKey: number;
};

export default function DialogList({ botId, cities, lockedCityId, selectedId, onSelect, refreshKey }: Props) {
  const [tab, setTab] = useState("all");
  const [cityFilter, setCityFilter] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [dialogs, setDialogs] = useState<Dialog[]>([]);
  const [counts, setCounts] = useState<DialogCounts>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // При смене бота сбрасываем фильтры
  useEffect(() => {
    setTab("all");
    setCityFilter("");
    setSearchInput("");
    setSearch("");
    setDialogs([]);
    setCounts({});
  }, [botId]);

  // Поиск запускаем через 400 мс после того, как пользователь перестал печатать
  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 400);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // Загрузка диалогов + опрос каждые 5 секунд
  useEffect(() => {
    // Флаг нужен, чтобы ответ старого запроса не перезаписал новый фильтр
    let cancelled = false;
    setLoading(true);

    const params: string[] = ["limit=200"];
    const currentTab = TABS.find((t) => t.key === tab);
    if (currentTab && currentTab.query) params.push(currentTab.query);
    if (cityFilter) params.push(`city_id=${encodeURIComponent(cityFilter)}`);
    if (search) params.push(`search=${encodeURIComponent(search)}`);
    const url = `/api/bots/${botId}/dialogs?${params.join("&")}`;

    // Предыдущий запрос ещё идёт — новый не отправляем (иначе запросы копятся на сервере)
    let inFlight = false;
    const load = (force: boolean) => {
      if (inFlight) return;
      // Вкладка свёрнута — не опрашиваем сервер зря
      if (!force && document.hidden) return;
      inFlight = true;
      apiRequest<{ dialogs: Dialog[]; counts?: DialogCounts }>(url)
        .then((data) => {
          if (cancelled) return;
          setDialogs(data.dialogs || []);
          setCounts(data.counts || {});
          setError("");
        })
        .catch((err) => {
          if (!cancelled) setError(errorText(err));
        })
        .finally(() => {
          inFlight = false;
          if (!cancelled) setLoading(false);
        });
    };

    load(true);
    const interval = setInterval(() => load(false), 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [botId, tab, cityFilter, search, refreshKey]);

  // Текст пустого списка зависит от фильтров
  let emptyText = "Диалогов пока нет. Они появятся, когда клиенты напишут в WhatsApp.";
  if (search) emptyText = "Ничего не найдено. Попробуйте другой номер или имя.";
  else if (tab === "hot") emptyText = "Нет клиентов, которые ждут счёт 👍";
  else if (tab === "attention") emptyText = "Нет диалогов, требующих внимания 👍";
  else if (tab !== "all" || cityFilter) emptyText = "В этом фильтре диалогов нет";

  return (
    <div className="dialog-list">
      <div className="dialog-filters">
        <div className="filter-tabs">
          {TABS.map((t) => {
            const count = counts[t.key];
            return (
              <button
                key={t.key}
                className={`filter-tab ${tab === t.key ? "active" : ""} ${t.key === "hot" && count ? "filter-tab-hot" : ""}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
                {count !== undefined && <span className="filter-count">{count}</span>}
              </button>
            );
          })}
        </div>

        <div className="dialog-search-row">
          <input
            className="input"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Поиск по номеру или имени"
          />
          {!lockedCityId && cities.length > 0 && (
            <select className="input" value={cityFilter} onChange={(e) => setCityFilter(e.target.value)}>
              <option value="">Все города</option>
              {cities.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}
        </div>
      </div>

      <div className="dialog-items">
        {error && <div className="alert alert-error" style={{ margin: 12 }}>{error}</div>}

        {loading && dialogs.length === 0 && !error && (
          <div className="list-placeholder"><span className="spinner" /> Загружаем диалоги…</div>
        )}

        {!loading && dialogs.length === 0 && !error && (
          <div className="list-placeholder">{emptyText}</div>
        )}

        {dialogs.map((d) => {
          const classes = ["dialog-card"];
          if (selectedId === d.id) classes.push("active");
          if (d.status === "hot") classes.push("hot");
          const title = dialogTitle(d);
          const showPhone = title !== formatPhone(d.chat_id);
          return (
            <div key={d.id} className={classes.join(" ")} onClick={() => onSelect(d.id)}>
              <div className="dialog-card-top">
                <div className="dialog-card-title">
                  {d.needs_attention && <span className="attention-icon" title={d.attention_reason || "Нужно внимание"}>⚠️</span>}
                  <span className="dialog-card-name">{title}</span>
                </div>
                <span className="dialog-card-time">{formatShortTime(d.last_message_at || d.created_at)}</span>
              </div>
              <div className="dialog-card-preview">
                {d.last_message_preview || (showPhone ? formatPhone(d.chat_id) : "Нет сообщений")}
              </div>
              <div className="dialog-card-meta">
                <span className={`badge ${STATUS_BADGE[d.status] || "badge-gray"}`}>
                  {STATUS_LABELS[d.status] || d.status}
                </span>
                {d.city_name ? (
                  <span className="city-chip">📍 {d.city_name}</span>
                ) : (
                  <span className="city-chip city-chip-empty">Город не указан</span>
                )}
                {d.ai_disabled && <span className="ai-off-chip" title="ИИ на паузе">⏸ ИИ</span>}
                {d.channel !== "whatsapp" && (
                  <span className={`channel-badge channel-${d.channel}`}>{d.channel}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
