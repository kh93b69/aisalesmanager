"""
Ядро обработки сообщений (pipeline).

Что здесь происходит:
- входящее сообщение клиента сохраняется, ждём паузу (debounce), затем ИИ отвечает;
- сообщение сотрудника (написал сам с телефона/из Wazzup) сохраняется и ставит ИИ на паузу;
- определение города, база знаний города, горячие лиды, уведомления.

Важно: замки (asyncio.Lock) хранятся в памяти процесса — рассчитано на ОДИН инстанс сервера.
"""
import asyncio
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.database import supabase
from app.config import REPLY_DEBOUNCE_SECONDS, TIMEZONE
from app.cities import get_bot_cities, detect_city
from app.rag import get_knowledge_for_city
from app.prompts import build_system_blocks, build_state_note, clean_contact_name
from app.ai_engine import generate_reply
from app.messenger import send_to_client, split_images, was_sent_by_us, IMAGE_TAG_PATTERN
from app.transcribe import transcribe_audio_url
from app.notifier import notify_hot_lead, notify_attention, notify_text, notify_unknown_question

# Сколько последних сообщений отдаём ИИ
HISTORY_LIMIT = 30

# Лимит диалогов в месяц, если у владельца нет подписки
DEFAULT_MAX_DIALOGS = 100

# Статусы, при которых клиент уже «горячий» или дальше — повторно не помечаем
HOT_OR_LATER_STATUSES = ["hot", "invoice_sent", "paid"]

# Сколько раз максимум дополнительно отвечаем, если клиент дописал во время генерации.
# Каждый повторный проход бывает только из-за НОВОГО сообщения клиента, поэтому цикл
# не бесконечный; лимит — просто страховка от флуда.
MAX_EXTRA_PASSES = 5

# Сколько user-сообщений берём в запрос к базе знаний
KB_QUERY_USER_MESSAGES = 3

# За какой период после перезапуска ищем диалоги без ответа (минут)
RECOVERY_WINDOW_MINUTES = 30

# Словарь замков: dialog_id → asyncio.Lock (чтобы не отвечать в один диалог дважды одновременно)
_dialog_locks = {}

# Сколько голосовых сейчас расшифровывается в каждом чате: (bot_id, chat_id) → количество.
# Текстовые сообщения, пришедшие следом, ждут расшифровку — иначе бот ответит без учёта голосового.
_pending_transcriptions = {}
# Сколько максимум ждать расшифровку голосового, секунд
MAX_TRANSCRIPTION_WAIT_SECONDS = 100

# Эхо исходящего, пришедшее так скоро после сообщения клиента, — это автоответ
# (приветствие WhatsApp Business, «сообщение об отсутствии»), а не живой сотрудник
AUTO_REPLY_WINDOW_SECONDS = 15
# Пауза перед проверкой эха: ответ бота успевает сохраниться в БД
ECHO_CHECK_DELAY_SECONDS = 3

# Когда владельцу бота последний раз писали про исчерпанный лимит: bot_id → "ГГГГ-ММ-ДД" (UTC)
_limit_notified = {}

# Названия дней недели и месяцев по-русски (для now_text)
WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


# ============================================================
# Чистые функции (без БД и сети) — их легко тестировать
# ============================================================

def utc_now_iso() -> str:
    """Текущее время в UTC в формате ISO (так храним в БД)."""
    return datetime.now(timezone.utc).isoformat()


def now_text(now: datetime | None = None) -> str:
    """
    Текущие дата и время клиники по-русски, например:
    "вторник, 10 ноября 2026, 14:05 (Asia/Almaty)".
    Параметр now нужен для тестов (любое aware-время, будет переведено в TIMEZONE).
    """
    tz = ZoneInfo(TIMEZONE)
    if now is None:
        now = datetime.now(tz)
    else:
        now = now.astimezone(tz)
    weekday = WEEKDAYS_RU[now.weekday()]
    month = MONTHS_RU[now.month - 1]
    return f"{weekday}, {now.day} {month} {now.year}, {now.strftime('%H:%M')} ({TIMEZONE})"


def build_history(messages: list[dict]) -> list[dict]:
    """
    Превращает сообщения из БД (по возрастанию времени) в историю для Claude.
    - берём последние 30;
    - manager → assistant с префиксом "[Сотрудник]: ";
    - пустые пропускаем;
    - ведущие assistant-реплики отбрасываем (история должна начинаться с user).
    """
    history = []
    for message in messages[-HISTORY_LIMIT:]:
        content = (message.get("content") or "").strip()
        if not content:
            continue
        role = message.get("role")
        if role == "user":
            history.append({"role": "user", "content": content})
        elif role == "assistant":
            history.append({"role": "assistant", "content": content})
        elif role == "manager":
            history.append({"role": "assistant", "content": f"[Сотрудник]: {content}"})

    # Отбрасываем ведущие реплики бота/сотрудника
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history


# Пометка реплик сотрудника в истории для ИИ (см. build_history)
STAFF_PREFIX = "[Сотрудник]:"


def strip_staff_prefix(reply: str) -> str:
    """
    ИИ видит реплики сотрудников с пометкой «[Сотрудник]:» и изредка может повторить её
    в своём ответе. Клиенту такая пометка не нужна — срезаем её в начале ответа.
    """
    text = (reply or "").strip()
    while text.startswith(STAFF_PREFIX):
        text = text[len(STAFF_PREFIX):].strip()
    return text


def last_user_text(messages: list[dict]) -> str:
    """Текст последних подряд идущих сообщений клиента (в конце переписки), через перевод строки."""
    parts = []
    for message in reversed(messages):
        if message.get("role") != "user":
            break
        content = (message.get("content") or "").strip()
        if content:
            parts.append(content)
    parts.reverse()
    return "\n".join(parts)


def describe_message(message_type: str, text: str, transcript: str | None = None) -> str:
    """
    Текст сообщения для сохранения в БД и для ИИ.
    Для голосового передаётся transcript (расшифровка; "" — не удалось).
    """
    text = (text or "").strip()
    if message_type == "text":
        return text
    if message_type == "audio":
        if transcript:
            return f"[Голосовое сообщение]: {transcript}"
        if transcript is None:
            # Расшифровку не делали (например, голосовое от сотрудника)
            return "[Голосовое сообщение]"
        return "[Голосовое сообщение — не удалось расшифровать]"
    if message_type == "image":
        label = "[Фото]"
    elif message_type == "document":
        label = "[Документ]"
    elif message_type == "video":
        label = "[Видео]"
    elif message_type == "missing_call":
        return "[Пропущенный звонок]"
    else:
        label = "[Вложение]"
    if text:
        return f"{label} {text}"
    return label


def find_city_by_id(cities: list[dict], city_id: str | None) -> dict | None:
    """Ищет город в списке по id."""
    if not city_id:
        return None
    for city in cities:
        if city.get("id") == city_id:
            return city
    return None


def find_city_by_name(cities: list[dict], name: str) -> dict | None:
    """Ищет город по названию (без учёта регистра и пробелов по краям)."""
    name = (name or "").strip().lower()
    if not name:
        return None
    for city in cities:
        if (city.get("name") or "").strip().lower() == name:
            return city
    return None


def pick_round_robin(members: list[dict], city_id: str | None) -> dict | None:
    """
    Выбор ответственного «по очереди».
    Подходят сотрудники с city_id == городу диалога или city_id = NULL (все города).
    Выбираем того, кому дольше всех ничего не назначали (NULL — самый первый).
    """
    suitable = []
    for member in members:
        member_city = member.get("city_id")
        if member_city is None or (city_id is not None and member_city == city_id):
            suitable.append(member)
    if not suitable:
        return None

    best = None
    for member in suitable:
        if best is None:
            best = member
            continue
        best_time = best.get("last_assigned_at")
        member_time = member.get("last_assigned_at")
        if best_time is None:
            # У лучшего уже NULL — лучше быть не может
            continue
        if member_time is None or parse_time(member_time) < parse_time(best_time):
            best = member
    return best


def parse_time(value: str) -> datetime:
    """Разбирает время ISO из БД в datetime (aware, UTC если пояс не указан)."""
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def month_start_iso(now: datetime | None = None) -> str:
    """Начало текущего календарного месяца (UTC) в ISO."""
    if now is None:
        now = datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()


def is_limit_exceeded(subscription: dict | None, dialogs_this_month: int,
                      now: datetime | None = None) -> bool:
    """
    Исчерпан ли лимит диалогов. dialogs_this_month уже включает только что созданный диалог.
    Нет подписки → лимит по умолчанию (100). Подписка не активна или истекла (expires_at
    в прошлом) → лимит исчерпан.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    max_dialogs = DEFAULT_MAX_DIALOGS
    if subscription:
        if subscription.get("status") and subscription.get("status") != "active":
            return True
        if subscription.get("expires_at"):
            try:
                if parse_time(subscription["expires_at"]) < now:
                    return True
            except ValueError:
                print("PIPELINE ERROR: не удалось разобрать subscriptions.expires_at")
        if subscription.get("max_dialogs_per_month") is not None:
            max_dialogs = subscription["max_dialogs_per_month"]
    return dialogs_this_month > max_dialogs


def has_unanswered_user(messages: list[dict]) -> bool:
    """Есть ли сообщение клиента новее последнего ответа бота/сотрудника (т.е. последнее — от клиента)."""
    return bool(messages) and messages[-1].get("role") == "user"


def find_index_by_id(messages: list[dict], message_id) -> int:
    """Позиция сообщения в списке по id (-1, если нет)."""
    for index, message in enumerate(messages):
        if message.get("id") == message_id:
            return index
    return -1


def has_user_after(messages: list[dict], message_id) -> bool:
    """Есть ли сообщения клиента ПОСЛЕ сообщения с данным id."""
    index = find_index_by_id(messages, message_id)
    if index < 0:
        return False
    for message in messages[index + 1:]:
        if message.get("role") == "user":
            return True
    return False


def reorder_after_answered(messages: list[dict], answered_user_id) -> list[dict]:
    """
    Клиент дописал, пока бот генерировал ответ: в БД порядок [M1, M2, ответ на M1].
    Для ИИ переставляем так, чтобы новые сообщения клиента шли после ответа бота:
    [M1, ответ на M1, M2] — тогда ИИ видит, что на M2 ещё не ответили.
    """
    index = find_index_by_id(messages, answered_user_id)
    if index < 0:
        return list(messages)
    head = messages[:index + 1]
    others = []
    users = []
    for message in messages[index + 1:]:
        if message.get("role") == "user":
            users.append(message)
        else:
            others.append(message)
    return head + others + users


def build_kb_query(messages: list[dict]) -> str:
    """
    Запрос к базе знаний: последний ответ бота + последние 3 сообщения клиента.
    Так короткие реплики («да, давайте», «а сколько?») не теряют контекст.
    """
    user_texts = []
    last_bot_text = ""
    for message in reversed(messages):
        content = (message.get("content") or "").strip()
        if not content:
            continue
        role = message.get("role")
        if role == "user" and len(user_texts) < KB_QUERY_USER_MESSAGES:
            user_texts.append(content)
        elif role == "assistant" and not last_bot_text:
            last_bot_text = content
        if len(user_texts) >= KB_QUERY_USER_MESSAGES and last_bot_text:
            break
    user_texts.reverse()
    parts = []
    if last_bot_text:
        parts.append(last_bot_text)
    parts.extend(user_texts)
    return "\n".join(parts)


def describe_wazzup_error(error) -> str:
    """Понятное описание ошибки доставки из вебхука Wazzup (там бывает строка или словарь)."""
    if not error:
        return "неизвестная ошибка"
    if isinstance(error, dict):
        code = error.get("error") or error.get("code") or ""
        description = error.get("description") or error.get("message") or ""
        text = f"{code} {description}".strip()
        return text or "неизвестная ошибка"
    return str(error)


def should_notify_limit(bot_id: str, today: str) -> bool:
    """Уведомлять владельца об исчерпанном лимите не чаще раза в сутки на бота."""
    if _limit_notified.get(bot_id) == today:
        return False
    _limit_notified[bot_id] = today
    return True


# ============================================================
# Работа с БД
# ============================================================

def get_bot(bot_id: str) -> dict | None:
    """Читает бота из БД."""
    try:
        result = supabase.table("bots").select("*").eq("id", bot_id).limit(1).execute()
        if result.data:
            return result.data[0]
    except Exception as error:
        print(f"PIPELINE ERROR: не удалось прочитать бота {bot_id}: {error}")
    return None


def get_dialog(dialog_id: str) -> dict | None:
    """Читает диалог из БД."""
    try:
        result = supabase.table("dialogs").select("*").eq("id", dialog_id).limit(1).execute()
        if result.data:
            return result.data[0]
    except Exception as error:
        print(f"PIPELINE ERROR: не удалось прочитать диалог {dialog_id}: {error}")
    return None


def find_dialog(bot_id: str, channel: str, chat_id: str) -> dict | None:
    """Ищет существующий диалог бота с этим клиентом (без создания)."""
    result = supabase.table("dialogs") \
        .select("*") \
        .eq("bot_id", bot_id) \
        .eq("channel", channel) \
        .eq("chat_id", chat_id) \
        .order("created_at") \
        .limit(1) \
        .execute()
    if result.data:
        return result.data[0]
    return None


def update_dialog(dialog_id: str, fields: dict) -> None:
    """Обновляет поля диалога (ошибку логируем, не падаем)."""
    if not fields:
        return
    try:
        supabase.table("dialogs").update(fields).eq("id", dialog_id).execute()
    except Exception as error:
        print(f"PIPELINE ERROR: не удалось обновить диалог {dialog_id}: {error}")


def get_or_create_dialog(bot: dict, channel: str, chat_id: str) -> tuple[dict | None, bool]:
    """
    Находит диалог или создаёт новый. Возвращает (диалог, создан_ли_сейчас).
    Функция синхронная (без await), поэтому внутри одного процесса два вызова
    не перемешаются и дубль диалога не создастся.
    """
    try:
        dialog = find_dialog(bot["id"], channel, chat_id)
        if dialog:
            return dialog, False
        result = supabase.table("dialogs").insert({
            "bot_id": bot["id"],
            "chat_id": chat_id,
            "channel": channel,
            "status": "new",
            "last_message_at": utc_now_iso(),
        }).execute()
        if result.data:
            print(f"PIPELINE: новый диалог bot={bot['id']} channel={channel}")
            return result.data[0], True
    except Exception as error:
        print(f"PIPELINE ERROR: get_or_create_dialog: {error}")
    return None, False


def external_id_exists(external_id: str | None) -> bool:
    """Есть ли уже сообщение с таким внешним ID (messageId из Wazzup)."""
    if not external_id:
        return False
    try:
        result = supabase.table("messages") \
            .select("id") \
            .eq("external_id", external_id) \
            .limit(1) \
            .execute()
        return bool(result.data)
    except Exception as error:
        print(f"PIPELINE ERROR: проверка external_id: {error}")
        return False


def save_message(dialog_id: str, role: str, content: str, message_type="text", media_url=None,
                 external_id=None, author_name=None, send_error=None) -> dict | None:
    """
    Сохраняет сообщение и обновляет «последнее сообщение» в диалоге.
    Возвращает сохранённую строку или None (дубль по external_id или ошибка).
    """
    if external_id_exists(external_id):
        print(f"PIPELINE: дубль сообщения external_id={external_id} — пропускаем")
        return None

    row = {
        "dialog_id": dialog_id,
        "role": role,
        "content": content,
        "message_type": message_type or "text",
    }
    if media_url:
        row["media_url"] = media_url
    if external_id:
        row["external_id"] = external_id
    if author_name:
        row["author_name"] = author_name
    if send_error:
        row["send_error"] = send_error

    try:
        result = supabase.table("messages").insert(row).execute()
    except Exception as error:
        text = str(error)
        # 23505 — нарушение уникального индекса (одновременно пришёл тот же messageId)
        if "23505" in text or "duplicate" in text.lower():
            print(f"PIPELINE: дубль сообщения (уникальный индекс) external_id={external_id}")
        else:
            print(f"PIPELINE ERROR: не удалось сохранить сообщение: {error}")
        return None

    if not result.data:
        return None
    saved = result.data[0]

    now_iso = saved.get("created_at") or utc_now_iso()
    fields = {
        "last_message_at": now_iso,
        "last_message_preview": (content or "")[:120],
    }
    if role == "manager":
        fields["last_manager_message_at"] = now_iso
    update_dialog(dialog_id, fields)
    return saved


def get_last_messages(dialog_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Последние сообщения диалога по возрастанию времени."""
    result = supabase.table("messages") \
        .select("*") \
        .eq("dialog_id", dialog_id) \
        .order("created_at", desc=True) \
        .limit(limit) \
        .execute()
    messages = result.data or []
    messages.reverse()
    return messages


def find_bot_images(bot_id: str) -> list[dict]:
    """Картинки бота из knowledge_chunks: [{"url", "name"}]."""
    try:
        result = supabase.table("knowledge_chunks") \
            .select("content") \
            .eq("bot_id", bot_id) \
            .like("content", "[IMAGE:%") \
            .order("id") \
            .execute()
        images = []
        for item in result.data or []:
            content = item["content"]
            url = content.split("]")[0].replace("[IMAGE:", "")
            name = content.split("] ")[-1] if "] " in content else "image"
            images.append({"url": url, "name": name})
        return images
    except Exception as error:
        print(f"PIPELINE ERROR: не удалось получить картинки бота: {error}")
        return []


def get_team(bot_id: str) -> list[dict]:
    """Сотрудники бота (team_members)."""
    try:
        result = supabase.table("team_members").select("*").eq("bot_id", bot_id).execute()
        return result.data or []
    except Exception as error:
        print(f"PIPELINE ERROR: не удалось получить команду бота: {error}")
        return []


def mark_member_assigned(member_id: str) -> None:
    """Запоминает время последнего назначения сотрудника (для очереди round_robin)."""
    try:
        supabase.table("team_members") \
            .update({"last_assigned_at": utc_now_iso()}) \
            .eq("id", member_id) \
            .execute()
    except Exception as error:
        print(f"PIPELINE ERROR: обновление last_assigned_at: {error}")


def count_owner_dialogs_this_month(user_id: str) -> int:
    """Сколько диалогов создано в этом месяце по всем ботам владельца (без загрузки самих диалогов)."""
    bots = supabase.table("bots").select("id").eq("user_id", user_id).execute()
    bot_ids = [b["id"] for b in (bots.data or [])]
    if not bot_ids:
        return 0
    result = supabase.table("dialogs") \
        .select("id", count="exact") \
        .in_("bot_id", bot_ids) \
        .gte("created_at", month_start_iso()) \
        .limit(1) \
        .execute()
    return result.count or 0


def get_owner_subscription(user_id: str) -> dict | None:
    """Самая свежая подписка владельца (None — подписки нет)."""
    try:
        sub = supabase.table("subscriptions") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
    except Exception as error:
        # На случай, если в таблице subscriptions нет колонки created_at
        print(f"PIPELINE: подписка без сортировки по created_at ({error})")
        sub = supabase.table("subscriptions") \
            .select("*") \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
    if sub.data:
        return sub.data[0]
    return None


async def apply_subscription_limit(bot: dict, dialog: dict) -> bool:
    """
    Проверка лимита подписки для НОВОГО диалога.
    Если лимит исчерпан — ставит ИИ на паузу, помечает «нужно внимание», уведомляет владельца.
    Возвращает True, если лимит исчерпан.
    """
    user_id = bot.get("user_id")
    if not user_id:
        return False
    try:
        subscription = get_owner_subscription(user_id)
        count = count_owner_dialogs_this_month(user_id)
    except Exception as error:
        # Не смогли проверить — не блокируем клиента
        print(f"PIPELINE ERROR: проверка лимита подписки: {error}")
        return False

    if not is_limit_exceeded(subscription, count):
        return False

    print(f"PIPELINE: лимит диалогов исчерпан bot={bot['id']} count={count}")
    reason = "Лимит диалогов по подписке исчерпан"
    fields = {
        "ai_disabled": True,
        "ai_paused_reason": "limit",
        "needs_attention": True,
        "attention_reason": reason,
    }
    update_dialog(dialog["id"], fields)
    dialog.update(fields)
    today = datetime.now(timezone.utc).date().isoformat()
    if not should_notify_limit(bot["id"], today):
        return True
    try:
        await notify_text(
            bot,
            "⚠️ <b>Лимит диалогов по подписке исчерпан.</b>\n"
            "Новые клиенты сохраняются, но ИИ им не отвечает — ответьте вручную или продлите тариф.",
        )
    except Exception as error:
        print(f"PIPELINE ERROR: notify_text: {error}")
    return True


# ============================================================
# Основная логика
# ============================================================

async def handle_client_message(bot: dict, channel: str, chat_id: str, text: str,
                                message_type: str = "text", media_url: str | None = None,
                                external_id: str | None = None, contact_name: str | None = None) -> None:
    """Входящее сообщение от клиента: сохранить, подождать паузу, ответить ИИ."""
    try:
        # Быстрая проверка дубля до расшифровки голосового (экономим запросы)
        if external_id_exists(external_id):
            print(f"PIPELINE: дубль входящего external_id={external_id} — пропускаем")
            return

        # 1. Текст сообщения с учётом типа
        transcript = None
        if message_type == "audio":
            transcript = ""
            if media_url:
                pending_key = (bot.get("id"), chat_id)
                _pending_transcriptions[pending_key] = _pending_transcriptions.get(pending_key, 0) + 1
                try:
                    transcript = await transcribe_audio_url(media_url)
                except Exception as error:
                    print(f"PIPELINE ERROR: расшифровка голосового: {error}")
                    transcript = ""
                finally:
                    left = _pending_transcriptions.get(pending_key, 1) - 1
                    if left > 0:
                        _pending_transcriptions[pending_key] = left
                    else:
                        _pending_transcriptions.pop(pending_key, None)
        content = describe_message(message_type, text, transcript)
        if not content:
            print("PIPELINE: пустое сообщение клиента — пропускаем")
            return

        # 2. Диалог (+ лимит подписки для нового)
        dialog, created = get_or_create_dialog(bot, channel, chat_id)
        if not dialog:
            return
        if created:
            await apply_subscription_limit(bot, dialog)

        # Имя из профиля WhatsApp — только если имени ещё нет (имя, названное в диалоге, важнее)
        if contact_name and contact_name != chat_id and not dialog.get("contact_name"):
            # Имя профиля задаёт сам клиент — ограничиваем длину
            update_dialog(dialog["id"], {"contact_name": contact_name.strip()[:100]})

        # 3. Сохраняем сообщение (None — дубль)
        saved = save_message(dialog["id"], "user", content, message_type=message_type,
                             media_url=media_url, external_id=external_id)
        if not saved:
            return

        # Клиент ответил — дожимы начинаются заново (если он снова замолчит)
        if dialog.get("followup_count"):
            update_dialog(dialog["id"], {"followup_count": 0})

        # 5. ИИ на паузе — сообщение сохранено, сотрудники увидят его в панели
        if dialog.get("ai_disabled"):
            print(f"PIPELINE: ИИ на паузе в диалоге {dialog['id']} — не отвечаем")
            return

        # 6. Debounce: ждём, вдруг клиент допишет ещё
        await asyncio.sleep(REPLY_DEBOUNCE_SECONDS)
        # Клиент прислал голосовое и сразу текст — ждём расшифровку, чтобы ответить на всё вместе
        await wait_for_transcriptions(bot.get("id"), chat_id)
        messages = get_last_messages(dialog["id"])
        if has_user_after(messages, saved.get("id")):
            # Клиент дописал — ответит задача более нового сообщения
            return

        # 7. Генерация и отправка ответа. Если бот как раз отвечает на предыдущее
        # сообщение, process_dialog_reply дождётся замка и сам решит, нужен ли ещё ответ.
        await process_dialog_reply(bot["id"], dialog["id"])
    except Exception as error:
        import traceback
        print(f"PIPELINE ERROR: handle_client_message: {error}\n{traceback.format_exc()}")


async def wait_for_transcriptions(bot_id: str, chat_id: str) -> None:
    """Ждёт, пока в этом чате расшифруются голосовые (не дольше MAX_TRANSCRIPTION_WAIT_SECONDS)."""
    waited = 0
    while _pending_transcriptions.get((bot_id, chat_id)) and waited < MAX_TRANSCRIPTION_WAIT_SECONDS:
        await asyncio.sleep(1)
        waited += 1


def is_auto_reply(messages: list[dict], now: datetime | None = None) -> bool:
    """
    Похоже ли исходящее сообщение на автоответ: последнее сообщение в диалоге — от клиента
    и пришло меньше AUTO_REPLY_WINDOW_SECONDS секунд назад. Живой сотрудник так быстро не отвечает,
    а приветствие WhatsApp Business / Wazzup уходит сразу.
    """
    if not messages or messages[-1].get("role") != "user":
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        client_time = parse_time(messages[-1].get("created_at"))
    except (ValueError, TypeError):
        return False
    return (now - client_time).total_seconds() < AUTO_REPLY_WINDOW_SECONDS


async def handle_manager_message(bot: dict, channel: str, chat_id: str, text: str,
                                 external_id: str | None = None, author_name: str | None = None,
                                 message_type: str = "text", media_url: str | None = None) -> None:
    """Сотрудник написал клиенту сам (с телефона или из Wazzup) → сохранить и поставить ИИ на паузу."""
    try:
        # 1. Наше же сообщение (отправлено ботом или из панели через API) — не сотрудник с телефона
        if was_sent_by_us(external_id):
            return
        content = describe_message(message_type, text)
        if not content:
            return

        # Даём ответу бота сохраниться в БД, затем проверяем дубль и эхо
        await asyncio.sleep(ECHO_CHECK_DELAY_SECONDS)
        if was_sent_by_us(external_id) or external_id_exists(external_id):
            return

        # 2. Защита от эха: бот только что отправил такой же текст
        existing = find_dialog(bot["id"], channel, chat_id)
        if existing and is_bot_echo(existing["id"], content):
            print(f"PIPELINE: эхо ответа бота в диалоге {existing['id']} — пропускаем")
            return

        # Автоответ (приветствие WhatsApp Business и т.п.) сразу после сообщения клиента —
        # не считаем ответом сотрудника, иначе ИИ встанет на паузу и клиент останется без ответа
        if existing and not existing.get("ai_disabled") and is_auto_reply(get_last_messages(existing["id"])):
            print(f"PIPELINE: автоответ в диалоге {existing['id']} — ИИ не ставим на паузу, сообщение не сохраняем")
            return

        # 3. Диалог и сообщение
        dialog, _created = get_or_create_dialog(bot, channel, chat_id)
        if not dialog:
            return
        saved = save_message(dialog["id"], "manager", content, message_type=message_type,
                             media_url=media_url, external_id=external_id, author_name=author_name)
        if not saved:
            return

        # 4. Автопауза ИИ: сотрудник перехватил диалог
        fields = {"needs_attention": False}
        if not dialog.get("ai_disabled"):
            fields["ai_disabled"] = True
            fields["ai_paused_reason"] = "manager_replied"
        update_dialog(dialog["id"], fields)
        print(f"PIPELINE: сотрудник ответил в диалоге {dialog['id']} — ИИ на паузе")
    except Exception as error:
        import traceback
        print(f"PIPELINE ERROR: handle_manager_message: {error}\n{traceback.format_exc()}")


def is_same_as_bot_text(bot_text: str, content: str) -> bool:
    """Совпадает ли текст сообщения с ответом бота (с той же нормализацией, что при отправке)."""
    target = (content or "").strip()
    if (bot_text or "").strip() == target:
        return True
    clean_text = split_images(bot_text or "")[0]
    return (clean_text or "").strip() == target


def is_bot_echo(dialog_id: str, content: str) -> bool:
    """Есть ли ответ бота с таким же текстом за последние 5 минут."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = supabase.table("messages") \
            .select("content") \
            .eq("dialog_id", dialog_id) \
            .eq("role", "assistant") \
            .gte("created_at", since) \
            .execute()
        for message in result.data or []:
            if is_same_as_bot_text(message.get("content") or "", content):
                return True
    except Exception as error:
        print(f"PIPELINE ERROR: проверка эха: {error}")
    return False


async def handle_delivery_error(bot: dict, external_id: str, error) -> None:
    """
    Wazzup сообщил, что наше сообщение не доставлено (statuses → status "error").
    Записываем ошибку в сообщение и помечаем диалог «нужно внимание».
    """
    try:
        if not external_id:
            return
        error_text = describe_wazzup_error(error)
        result = supabase.table("messages") \
            .select("id, dialog_id") \
            .eq("external_id", external_id) \
            .limit(1) \
            .execute()
        if not result.data:
            print(f"PIPELINE: ошибка доставки для неизвестного сообщения {external_id}: {error_text}")
            return
        message = result.data[0]
        supabase.table("messages").update({"send_error": error_text}).eq("id", message["id"]).execute()

        dialog = get_dialog(message["dialog_id"])
        if not dialog or dialog.get("bot_id") != bot.get("id"):
            return
        print(f"PIPELINE: сообщение не доставлено, диалог {dialog['id']}: {error_text}")
        if dialog.get("needs_attention"):
            # Не спамим: флаг уже стоит
            return
        reason = f"Сообщение не доставлено клиенту: {error_text}"
        fields = {"needs_attention": True, "attention_reason": reason}
        update_dialog(dialog["id"], fields)
        dialog.update(fields)
        city = find_city_by_id(get_bot_cities(bot["id"]), dialog.get("city_id"))
        await notify_attention(bot, dialog, city, get_team(bot["id"]), reason)
    except Exception as error_:
        print(f"PIPELINE ERROR: handle_delivery_error: {error_}")


async def build_and_generate(bot: dict, dialog: dict, cities: list[dict], city: dict | None,
                             history: list[dict], query: str, is_first_reply: bool = False) -> dict:
    """
    Собирает базу знаний и промпт для города и вызывает ИИ.
    is_first_reply — бот ещё ни разу не отвечал в этом диалоге (тогда ИИ начнёт с приветствия).
    """
    city_id = city["id"] if city else None
    knowledge = get_knowledge_for_city(bot["id"], city_id, query)
    images = find_bot_images(bot["id"])
    system_blocks = build_system_blocks(bot, cities, city, knowledge, images)
    dialog_for_note = dict(dialog)
    dialog_for_note["city_id"] = city_id
    state_note = build_state_note(dialog_for_note, city, now_text(), has_cities=bool(cities),
                                  is_first_reply=is_first_reply)
    city_names = [c.get("name") for c in cities if c.get("name")]
    result = await generate_reply(bot, system_blocks, history, state_note, city_names)
    # Отправляем клиенту только картинки самого бота (ссылку «со стороны» ИИ мог взять из сообщения клиента)
    allowed_urls = [image["url"] for image in images]
    result["reply"] = keep_known_images(result.get("reply") or "", allowed_urls)
    return result


def keep_known_images(reply: str, allowed_urls: list[str]) -> str:
    """Убирает из ответа теги [IMAGE:url], если такой картинки нет среди картинок бота."""
    def replace_tag(match):
        if match.group(1).strip() in allowed_urls:
            return match.group(0)
        return ""
    return re.sub(IMAGE_TAG_PATTERN, replace_tag, reply).strip()


async def process_dialog_reply(bot_id: str, dialog_id: str) -> None:
    """
    Генерация ответа ИИ и отправка клиенту (под замком на диалог).
    Безопасно вызывать, когда отвечать не на что — просто ничего не произойдёт.
    Если клиент дописал, пока бот генерировал ответ, — отвечаем ещё раз (не больше MAX_EXTRA_PASSES раз).
    """
    lock = _dialog_locks.setdefault(dialog_id, asyncio.Lock())
    async with lock:
        try:
            answered_id = None
            for attempt in range(1 + MAX_EXTRA_PASSES):
                answered_id = await _reply_once(bot_id, dialog_id, answered_id)
                if not answered_id:
                    break
                messages = get_last_messages(dialog_id)
                if not has_user_after(messages, answered_id):
                    break
                print(f"PIPELINE: клиент дописал во время ответа, диалог {dialog_id} — отвечаем ещё раз")
        except Exception as error:
            import traceback
            print(f"PIPELINE ERROR: process_dialog_reply: {error}\n{traceback.format_exc()}")


async def _reply_once(bot_id: str, dialog_id: str, answered_user_id=None):
    """
    Один ответ ИИ. Вызывается только из process_dialog_reply под замком.
    answered_user_id — id последнего сообщения клиента, на которое уже ответили в этом же
    вызове (None при первом проходе).
    Возвращает id последнего сообщения клиента, на которое ответили, или None.
    """
    # 1. Свежие бот и диалог
    bot = get_bot(bot_id)
    dialog = get_dialog(dialog_id)
    if not bot or not dialog:
        return None
    if dialog.get("ai_disabled"):
        return None

    # 2. Есть ли на что отвечать
    messages = get_last_messages(dialog_id)
    if answered_user_id:
        if not has_user_after(messages, answered_user_id):
            return None
        messages = reorder_after_answered(messages, answered_user_id)
    elif not has_unanswered_user(messages):
        return None
    history = build_history(messages)
    if not history or history[-1]["role"] != "user":
        return None
    last_user_id = messages[-1].get("id")
    kb_query = build_kb_query(messages)
    first_reply = is_first_reply(messages)

    # 3. Город. Если он ещё неизвестен — определяем по тексту клиента.
    # Если уже известен — по тексту НЕ переключаем (ложные срабатывания: «акта» → Актау),
    # переключение только по detected_city от ИИ.
    cities = get_bot_cities(bot_id)
    city_id = dialog.get("city_id")
    city = find_city_by_id(cities, city_id)
    if city is None:
        switch_to = detect_city(last_user_text(messages), cities, None)
        if switch_to:
            city = switch_to
            update_dialog(dialog_id, {"city_id": city["id"]})
            print(f"PIPELINE: город диалога {dialog_id} → {city.get('name')} (по тексту)")

    # 4–5. Промпт и ответ ИИ
    result = await build_and_generate(bot, dialog, cities, city, history, kb_query, first_reply)

    # 6. ИИ определил другой город → один повтор по базе знаний правильного города
    detected = find_city_by_name(cities, result.get("detected_city") or "")
    if detected and detected.get("id") != (city["id"] if city else None):
        city = detected
        update_dialog(dialog_id, {"city_id": city["id"]})
        print(f"PIPELINE: город диалога {dialog_id} → {city.get('name')} (по ИИ), повторная генерация")
        result = await build_and_generate(bot, dialog, cities, city, history, kb_query, first_reply)

    # 7. Сотрудник мог перехватить диалог, пока ИИ думал
    fresh = get_dialog(dialog_id)
    if not fresh or fresh.get("ai_disabled"):
        print(f"PIPELINE: диалог {dialog_id} перехвачен во время генерации — не отправляем")
        return None
    dialog = fresh

    team = get_team(bot_id)
    fields = {}
    attention_reason = ""

    # 8. Отправка клиенту
    reply = strip_staff_prefix(result.get("reply") or "")
    if reply:
        send_result = await send_to_client(bot, dialog, reply, from_bot=True)
        send_error = None
        if not send_result.get("ok"):
            send_error = send_result.get("error") or "неизвестная ошибка"
            print(f"PIPELINE ERROR: не удалось отправить ответ в диалог {dialog_id}: {send_error}")
            attention_reason = f"Не удалось отправить ответ: {send_error}"
        save_message(dialog_id, "assistant", reply, send_error=send_error,
                     external_id=send_result.get("message_id") or None)
    else:
        attention_reason = "ИИ вернул пустой ответ"

    # 9. Статус и имя клиента (имя из диалога точнее, чем имя профиля WhatsApp)
    if dialog.get("status") == "new":
        fields["status"] = "in_progress"
    # Имя, которое ИИ услышал от клиента — только буквы и не длиннее 40 символов
    client_name = clean_contact_name(result.get("client_name"))
    if client_name and client_name != dialog.get("contact_name"):
        fields["contact_name"] = client_name

    # 10. Горячий лид
    became_hot = False
    if result.get("lead_status") == "hot" and dialog.get("status") not in HOT_OR_LATER_STATUSES:
        became_hot = True
        fields["status"] = "hot"
        fields["hot_at"] = utc_now_iso()
        fields["hot_reason"] = result.get("lead_summary") or ""
        fields["reminder_count"] = 0
        fields["needs_attention"] = False
        if bot.get("assignment_mode") == "round_robin" and not dialog.get("assigned_user_id"):
            member = pick_round_robin(team, city["id"] if city else None)
            if member:
                fields["assigned_user_id"] = member.get("user_id")
                mark_member_assigned(member["id"])

    # 11. Вопрос, на который нет ответа в базе знаний: сохраняем (руководитель ответит в панели,
    # ответ попадёт в базу знаний) и сразу уведомляем руководителя
    unknown_question = (result.get("unknown_question") or "").strip()
    unknown_notified = False
    if unknown_question:
        is_new = save_unanswered_question(bot_id, dialog_id, city["id"] if city else None,
                                          unknown_question, last_user_text(messages))
        if not attention_reason:
            attention_reason = f"Бот не знает ответа: {unknown_question}"
        if is_new:
            try:
                await notify_unknown_question(bot, dialog, city, team, unknown_question)
                unknown_notified = True
            except Exception as error:
                print(f"PIPELINE ERROR: notify_unknown_question: {error}")

    # 12. Нужен человек (или ИИ упал / ошибка отправки)
    if not attention_reason and result.get("need_human"):
        attention_reason = result.get("need_human_reason") or "ИИ просит подключить менеджера"
    if not attention_reason and not result.get("ok", True):
        attention_reason = "Ошибка ИИ — отправлен вежливый ответ-заглушка"
    notify_needed = False
    if attention_reason:
        fields["needs_attention"] = True
        fields["attention_reason"] = attention_reason
        # Не спамим: уведомляем только если флаг ещё не стоял
        notify_needed = not dialog.get("needs_attention")

    update_dialog(dialog_id, fields)
    dialog.update(fields)

    if became_hot:
        print(f"PIPELINE: горячий лид в диалоге {dialog_id}")
        try:
            await notify_hot_lead(bot, dialog, city, team, dialog.get("hot_reason") or "")
        except Exception as error:
            print(f"PIPELINE ERROR: notify_hot_lead: {error}")
    if notify_needed and not unknown_notified:
        try:
            await notify_attention(bot, dialog, city, team, attention_reason)
        except Exception as error:
            print(f"PIPELINE ERROR: notify_attention: {error}")

    return last_user_id


def is_first_reply(messages: list[dict]) -> bool:
    """Бот и сотрудники ещё ни разу не писали в этом диалоге (среди загруженных сообщений)."""
    for message in messages:
        if message.get("role") in ("assistant", "manager"):
            return False
    return True


def save_unanswered_question(bot_id: str, dialog_id: str, city_id: str | None,
                             question: str, client_message: str) -> bool:
    """
    Сохраняет вопрос, на который бот не знал ответа. True — сохранён новый вопрос,
    False — такой же открытый вопрос в этом диалоге уже есть (или ошибка БД).
    """
    try:
        existing = supabase.table("unanswered_questions") \
            .select("id, question") \
            .eq("dialog_id", dialog_id) \
            .eq("status", "open") \
            .execute()
        for row in existing.data or []:
            if (row.get("question") or "").strip().lower() == question.strip().lower():
                return False
        supabase.table("unanswered_questions").insert({
            "bot_id": bot_id,
            "dialog_id": dialog_id,
            "city_id": city_id,
            "question": question[:500],
            "client_message": (client_message or "")[:2000],
        }).execute()
        print(f"PIPELINE: вопрос без ответа в диалоге {dialog_id}: {question}")
        return True
    except Exception as error:
        print(f"PIPELINE ERROR: не удалось сохранить вопрос без ответа: {error}")
        return False


def find_recent_active_dialogs(since_iso: str) -> list[dict]:
    """Диалоги с включённым ИИ и сообщениями после since_iso (постранично)."""
    dialogs = []
    start = 0
    page_size = 500
    while True:
        result = supabase.table("dialogs") \
            .select("id, bot_id") \
            .eq("ai_disabled", False) \
            .gte("last_message_at", since_iso) \
            .order("id") \
            .range(start, start + page_size - 1) \
            .execute()
        page = result.data or []
        dialogs.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return dialogs


async def recover_unanswered_dialogs() -> None:
    """
    После перезапуска сервера (деплой) фоновые задачи ответа пропадают.
    Находим диалоги за последние 30 минут, где последнее сообщение — от клиента без ответа,
    и отвечаем на них.
    """
    try:
        since = (datetime.now(timezone.utc) - timedelta(minutes=RECOVERY_WINDOW_MINUTES)).isoformat()
        tasks = []
        for dialog in find_recent_active_dialogs(since):
            last = get_last_messages(dialog["id"], limit=1)
            if has_unanswered_user(last):
                tasks.append(process_dialog_reply(dialog["bot_id"], dialog["id"]))
        print(f"PIPELINE: восстановление после перезапуска — диалогов без ответа: {len(tasks)}")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as error:
        print(f"PIPELINE ERROR: восстановление после перезапуска: {error}")
