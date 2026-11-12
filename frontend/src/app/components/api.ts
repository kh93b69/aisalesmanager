// Общий модуль фронтенда: адрес API, запросы с токеном, типы данных и подписи.
// Все компоненты берут отсюда типы и функции, чтобы не дублировать код.

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

// --- Типы данных (совпадают с ответами бэкенда, см. ТЗ 3.14) ---

export type DialogStatus = "new" | "in_progress" | "hot" | "invoice_sent" | "paid" | "lost";

export type AiModel = "claude-haiku-5-5" | "claude-sonnet-5-5";

export type AssignmentMode = "none" | "round_robin";

// Тип бизнеса: general — любой бизнес, clinic — медицинская клиника (правила безопасности)
export type BusinessType = "general" | "clinic";

export type Bot = {
  id: string;
  name: string;
  system_prompt: string;
  ai_model: AiModel;
  // Анкета компании — из неё собирается промпт ИИ
  business_type: BusinessType;
  company_name: string;
  assistant_name: string;
  business_description: string;
  target_action: string;
  // Скрипты: первое сообщение и скрипт продаж (ответы на возражения)
  greeting_message: string;
  sales_script: string;
  // Ссылки, которые бот отправляет по ситуации
  links: BotLink[];
  // Дожимы клиентов, которые замолчали
  followups_enabled: boolean;
  followups: FollowupStep[];
  // График работы: бот пишет клиентам только в это время
  work_hours_enabled: boolean;
  work_schedule: WorkDay[];
  off_hours_message: string;
  // Оплата: ссылка и реквизиты, которые бот отправляет готовому клиенту
  payment_enabled: boolean;
  payment_link: string;
  payment_details: string;
  // Сводка дня руководителю в Telegram
  daily_report_enabled: boolean;
  daily_report_time: string;
  telegram_notify_chat_id: string | null;
  invoice_reminder_minutes: number;
  assignment_mode: AssignmentMode;
  wazzup_connected: boolean;
  wazzup_channel_id: string | null;
  wazzup_channel_phone: string | null;
  wazzup_channel_state: string | null;
  // Роль текущего пользователя в этом боте: владелец или сотрудник
  my_role: "owner" | "manager";
  // Город сотрудника (null — видит все города)
  my_city_id: string | null;
};

// Один день графика работы (массив из 7 дней, с понедельника)
export type WorkDay = {
  enabled: boolean;
  start: string; // "09:00"
  end: string; // "21:00"
};

// Работает ли бот прямо сейчас (ответ /work-status)
export type WorkStatus = {
  now: string;
  timezone: string;
  utc_offset: string;
  schedule_enabled: boolean;
  working: boolean;
  next_start: string | null;
};

// Ссылка для бота: название, адрес и когда её отправлять
export type BotLink = {
  title: string;
  url: string;
  when: string;
};

// Шаг дожима: через сколько часов после последнего сообщения и что написать ("" — ИИ напишет сам)
export type FollowupStep = {
  hours: number;
  text: string;
};

// Вопрос, на который бот не знал ответа (раздел «Без ответа»)
export type QuestionStatus = "open" | "answered" | "dismissed";

export type UnansweredQuestion = {
  id: string;
  bot_id: string;
  dialog_id: string | null;
  city_id: string | null;
  city_name: string | null;
  question: string;
  client_message: string;
  status: QuestionStatus;
  answer: string;
  created_at: string;
  answered_at: string | null;
  contact_name: string | null;
  chat_id: string | null;
};

export type Dialog = {
  id: string;
  bot_id: string;
  chat_id: string;
  channel: string;
  ai_disabled: boolean;
  ai_paused_reason: "manager_replied" | "manual" | "limit" | null;
  created_at: string;
  city_id: string | null;
  city_name: string | null;
  status: DialogStatus;
  contact_name: string | null;
  hot_reason: string | null;
  needs_attention: boolean;
  attention_reason: string | null;
  assigned_user_id: string | null;
  hot_at: string | null;
  invoice_sent_at: string | null;
  paid_at: string | null;
  last_message_at: string | null;
  last_message_preview: string | null;
};

export type DialogCounts = {
  all?: number;
  hot?: number;
  invoice_sent?: number;
  attention?: number;
  paid?: number;
  [key: string]: number | undefined;
};

export type Message = {
  id: string;
  dialog_id: string;
  role: "user" | "assistant" | "manager";
  content: string;
  created_at: string;
  message_type: string | null;
  media_url: string | null;
  author_name: string | null;
  send_error: string | null;
};

export type City = {
  id: string;
  bot_id: string;
  name: string;
  aliases: string;
  address: string;
  telegram_chat_id: string | null;
  sort_order: number;
  chunks_count?: number;
};

export type TeamMember = {
  id: string;
  user_id: string;
  email: string;
  city_id: string | null;
  telegram_chat_id: string | null;
};

export type CityStats = {
  city_id: string | null;
  city_name: string | null;
  total: number;
  hot: number;
  invoice_sent: number;
  paid: number;
  lost: number;
};

export type Stats = {
  total: number;
  by_status: Record<string, number>;
  by_city: CityStats[];
  conversion_paid: number;
};

export type WazzupChannel = {
  channelId: string;
  transport: string;
  plainId: string;
  state: string;
  // Название бота, к которому номер уже подключён (null — свободен)
  used_by?: string | null;
};

export type WazzupStatus = {
  connected: boolean;
  channel_id: string | null;
  phone: string | null;
  state: string | null;
  webhook_ok: boolean;
};

export type BotImage = {
  id: string;
  url: string;
  name: string;
};

export type UserData = {
  id: string;
  email: string;
  role?: string;
};

// --- Запросы к API ---

// fetch с токеном авторизации из localStorage
export function authFetch(url: string, options: RequestInit = {}) {
  const token = localStorage.getItem("access_token");
  const headers: Record<string, string> = {
    ...((options.headers as Record<string, string>) || {}),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  // Для файлов (FormData) браузер сам ставит Content-Type с boundary
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
  }
  return fetch(url, { ...options, headers });
}

// Достаём понятный текст ошибки из ответа FastAPI ({"detail": "..."} или список ошибок валидации)
function getErrorText(data: unknown, status: number): string {
  if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    const detail = obj.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as Record<string, unknown>;
      if (first && typeof first.msg === "string") return `Ошибка данных: ${first.msg}`;
    }
    if (typeof obj.error === "string" && obj.error) return obj.error;
  }
  if (status === 403) return "Нет доступа";
  if (status === 404) return "Не найдено";
  if (status >= 500) return `Ошибка сервера (${status})`;
  return `Ошибка запроса (${status})`;
}

// Выход: чистим токены и отправляем на страницу входа.
// keepLocation=true — после входа вернуть пользователя на текущую страницу (например, ?dialog=...)
export function logoutAndRedirect(keepLocation: boolean = false) {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("user");
  let loginUrl = "/login";
  if (keepLocation) {
    const current = window.location.pathname + window.location.search;
    if (current !== "/" && !current.startsWith("/login")) {
      loginUrl = `/login?next=${encodeURIComponent(current)}`;
    }
  }
  window.location.href = loginUrl;
}

// Общий промис обновления токена: если несколько запросов одновременно получили 401,
// токен обновляется один раз, а остальные ждут этот же промис
let refreshPromise: Promise<RefreshResult> | null = null;

// Результат обновления токена:
// "ok" — новый токен получен; "expired" — сессия истекла, нужно войти заново;
// "unavailable" — нет связи / сервер перезапускается (из аккаунта НЕ выкидываем)
export type RefreshResult = "ok" | "expired" | "unavailable";

// Обновить access_token по refresh_token
async function doRefreshTokens(): Promise<RefreshResult> {
  const refreshToken = localStorage.getItem("refresh_token");
  if (!refreshToken) return "expired";
  try {
    const res = await fetch(`${API_URL}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (res.status === 400 || res.status === 401) {
      // Другая вкладка могла уже обновить токены этим же refresh_token — тогда всё в порядке
      if (localStorage.getItem("refresh_token") !== refreshToken) return "ok";
      return "expired";
    }
    if (!res.ok) return "unavailable";
    const data = await res.json();
    if (!data || typeof data.access_token !== "string" || !data.access_token) return "unavailable";
    localStorage.setItem("access_token", data.access_token);
    if (typeof data.refresh_token === "string" && data.refresh_token) {
      localStorage.setItem("refresh_token", data.refresh_token);
    }
    return "ok";
  } catch {
    return "unavailable";
  }
}

export function refreshTokens(): Promise<RefreshResult> {
  if (!refreshPromise) {
    refreshPromise = doRefreshTokens().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

// Запрос к API с разбором JSON. При ошибке бросает Error с текстом из detail.
// При 401 один раз обновляет токен и повторяет запрос.
// path — путь вида "/api/bots"
export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await authFetch(`${API_URL}${path}`, options);
    // Токен истёк — пробуем обновить и повторить запрос один раз
    if (res.status === 401) {
      const refreshed = await refreshTokens();
      if (refreshed === "expired") {
        logoutAndRedirect(true);
        throw new Error("Сессия истекла, войдите заново");
      }
      if (refreshed === "unavailable") {
        throw new Error("Нет связи с сервером. Проверьте интернет.");
      }
      res = await authFetch(`${API_URL}${path}`, options);
    }
  } catch (err) {
    if (err instanceof Error && err.message === "Сессия истекла, войдите заново") throw err;
    throw new Error("Нет связи с сервером. Проверьте интернет.");
  }

  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }

  // Даже после обновления токена 401 — значит сессия недействительна
  if (res.status === 401) {
    logoutAndRedirect(true);
    throw new Error("Сессия истекла, войдите заново");
  }

  if (!res.ok) {
    throw new Error(getErrorText(data, res.status));
  }
  return data as T;
}

// Удобные обёртки для POST/PUT с JSON-телом
export function apiPost<T>(path: string, body: unknown = {}): Promise<T> {
  return apiRequest<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function apiPut<T>(path: string, body: unknown = {}): Promise<T> {
  return apiRequest<T>(path, { method: "PUT", body: JSON.stringify(body) });
}

export function apiDelete<T>(path: string): Promise<T> {
  return apiRequest<T>(path, { method: "DELETE" });
}

// Текст ошибки из пойманного исключения
export function errorText(err: unknown): string {
  if (err instanceof Error) return err.message;
  return "Неизвестная ошибка";
}

// --- Подписи и справочники ---

export const STATUS_LABELS: Record<DialogStatus, string> = {
  new: "Новый",
  in_progress: "В работе",
  hot: "🔥 Нужен счёт",
  invoice_sent: "Счёт отправлен",
  paid: "Оплачено",
  lost: "Потерян",
};

// CSS-класс бейджа для статуса
export const STATUS_BADGE: Record<DialogStatus, string> = {
  new: "badge-gray",
  in_progress: "badge-purple",
  hot: "badge-orange",
  invoice_sent: "badge-blue",
  paid: "badge-green",
  lost: "badge-red",
};

export const AI_PAUSE_LABELS: Record<string, string> = {
  manager_replied: "ответил сотрудник",
  manual: "выключен вручную",
  limit: "исчерпан лимит диалогов по подписке",
};

export const AI_MODELS: { value: AiModel; title: string; description: string }[] = [
  {
    value: "claude-haiku-5-5",
    title: "Claude Haiku 5.5",
    description: "Быстрый и экономный — рекомендуем. ~5–25 ₸ за диалог",
  },
  {
    value: "claude-sonnet-5-5",
    title: "Claude Sonnet 5.5",
    description: "Умнее, но дороже в ~19 раз (~100–150 ₸ за диалог)",
  },
];

export const BUSINESS_TYPES: { value: BusinessType; title: string; description: string }[] = [
  {
    value: "general",
    title: "Обычный бизнес",
    description: "Магазин, услуги, салон, автосервис, обучение — продаёт и ведёт к заказу",
  },
  {
    value: "clinic",
    title: "Медицинская клиника",
    description: "Запись на приём + правила безопасности: без диагнозов, при срочном — скорая 103",
  },
];

// Ответ тестового чата (Настройки → Тест бота)
export type TestChatReply = {
  reply: string;
  // Бот отправил ссылку на оплату
  payment_sent?: boolean;
  city_id: string | null;
  city_name: string | null;
  lead_status: "none" | "hot";
  lead_summary: string;
  need_human: boolean;
  need_human_reason: string;
  client_name: string;
  // Вопрос, на который нет ответа в базе знаний ("" — всё знал)
  unknown_question: string;
  ok: boolean;
  model: string;
  usage: {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_input_tokens?: number;
    cache_creation_input_tokens?: number;
  };
};

// Русские подписи для статусов канала Wazzup
export const WAZZUP_STATES: Record<string, { text: string; cls: string }> = {
  active: { text: "Работает", cls: "wa-connected" },
  init: { text: "Запускается…", cls: "wa-waiting" },
  disabled: { text: "Канал выключен в Wazzup", cls: "wa-disconnected" },
  phoneUnavailable: { text: "Телефон недоступен — проверьте интернет на телефоне", cls: "wa-error" },
  qr: { text: "Нужно отсканировать QR-код в личном кабинете Wazzup", cls: "wa-waiting" },
  openElsewhere: { text: "WhatsApp открыт в другом месте — переподключите в Wazzup", cls: "wa-error" },
  notEnoughMoney: { text: "Не оплачен тариф Wazzup", cls: "wa-error" },
  foreignphone: { text: "QR-код отсканирован с другого номера", cls: "wa-error" },
  unauthorized: { text: "Канал не авторизован — переподключите в Wazzup", cls: "wa-error" },
  waitForPassword: { text: "Ожидает пароль двухфакторной защиты", cls: "wa-waiting" },
  onModeration: { text: "Канал на модерации", cls: "wa-waiting" },
  rejected: { text: "Канал отклонён Wazzup", cls: "wa-error" },
  not_found: { text: "Канал не найден в Wazzup — выберите номер заново", cls: "wa-error" },
};

export function wazzupStateInfo(state: string | null | undefined) {
  if (!state) return { text: "Статус неизвестен", cls: "wa-disconnected" };
  return WAZZUP_STATES[state] || { text: state, cls: "wa-disconnected" };
}

// --- Форматирование ---

// Красивый номер: 77011234567 → +7 701 123 45 67
export function formatPhone(chatId: string): string {
  const digits = chatId.replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("7")) {
    return `+7 ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9, 11)}`;
  }
  if (digits.length >= 10 && digits === chatId) return `+${digits}`;
  return chatId;
}

// Имя клиента или номер
export function dialogTitle(dialog: Dialog): string {
  if (dialog.contact_name && dialog.contact_name !== dialog.chat_id) return dialog.contact_name;
  return formatPhone(dialog.chat_id);
}

function isToday(date: Date) {
  const now = new Date();
  return (
    date.getDate() === now.getDate() &&
    date.getMonth() === now.getMonth() &&
    date.getFullYear() === now.getFullYear()
  );
}

// Время для списка: сегодня — "14:05", иначе — "5 нояб."
export function formatShortTime(dateStr: string | null): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "";
  if (isToday(date)) {
    return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

// Время сообщения: сегодня — "14:05", иначе — "5 нояб., 14:05"
export function formatMessageTime(dateStr: string): string {
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "";
  if (isToday(date)) {
    return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// Сколько минут прошло с даты (для «ждёт счёт N мин»)
export function minutesSince(dateStr: string | null): number | null {
  if (!dateStr) return null;
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return null;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
}

// Процент с одним знаком: 0.1234 → "12.3%"
export function formatPercent(part: number, total: number): string {
  if (!total) return "0%";
  const value = (part / total) * 100;
  return `${Math.round(value * 10) / 10}%`;
}
