"""
Входящие вебхуки: Wazzup (WhatsApp) и Telegram.

Вебхук отвечает сразу (Wazzup ждёт ответ не дольше 30 с), а тяжёлая обработка
(ИИ, отправка) идёт в фоне: отдельная asyncio-задача на каждое событие.
"""
import asyncio
import hmac
import hashlib
from fastapi import APIRouter, Request, Depends
from app.database import supabase
from app.telegram import set_webhook
from app.auth import get_current_admin
from app.config import BACKEND_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_ID
from app.pipeline import handle_client_message, handle_manager_message, handle_delivery_error

router = APIRouter()

# Ссылки на запущенные фоновые задачи. Без них asyncio может удалить задачу сборщиком мусора
# до завершения. Задача сама удаляет себя из множества, когда закончится.
_running_tasks = set()

# Типы сообщений Wazzup, которые мы различаем; всё остальное — "other"
KNOWN_MESSAGE_TYPES = ["text", "image", "audio", "video", "document", "missing_call"]


# ============================================================
# Вспомогательные функции
# ============================================================

async def run_safely(func, *args, **kwargs) -> None:
    """Выполняет обработчик и ловит любую ошибку, чтобы фоновая задача не падала молча."""
    try:
        await func(*args, **kwargs)
    except Exception as error:
        print(f"WEBHOOK TASK ERROR: {getattr(func, '__name__', 'task')}: {error}")


def start_background(func, *args, **kwargs) -> None:
    """
    Запускает обработку параллельно (asyncio.create_task), не дожидаясь её.
    BackgroundTasks у FastAPI выполняет задачи по очереди — сообщения разных клиентов
    из одного вебхука ждали бы друг друга, поэтому здесь отдельная задача на каждое событие.
    """
    task = asyncio.create_task(run_safely(func, *args, **kwargs))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)

def load_bot(bot_id: str) -> dict | None:
    """Читает бота из БД (None если нет или ошибка)."""
    try:
        result = supabase.table("bots").select("*").eq("id", bot_id).limit(1).execute()
        if result.data:
            return result.data[0]
    except Exception as error:
        print(f"WAZZUP WEBHOOK ERROR: не удалось прочитать бота: {error}")
    return None


def is_token_valid(token: str, secret: str | None) -> bool:
    """Сравнение токена из URL с секретом бота (защита от подбора по времени)."""
    if not token or not secret:
        return False
    return hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8"))


def normalize_message_type(wazzup_type: str) -> str:
    """Тип сообщения Wazzup → наш message_type."""
    if wazzup_type in KNOWN_MESSAGE_TYPES:
        return wazzup_type
    return "other"


def parse_wazzup_body(body: dict, bot: dict) -> list[dict]:
    """
    Разбирает тело вебхука Wazzup в список «событий» (чистая функция, без БД и сети).
    Виды событий:
      {"kind": "test"}
      {"kind": "channel_state", "state": str}
      {"kind": "status_error", "message_id": str, "error": ...}
      {"kind": "client", "chat_id", "text", "message_type", "media_url", "external_id", "contact_name"}
      {"kind": "manager", "chat_id", "text", "message_type", "media_url", "external_id", "author_name"}
    """
    events = []
    if not isinstance(body, dict):
        return events

    if body.get("test"):
        events.append({"kind": "test"})

    bot_channel_id = bot.get("wazzup_channel_id")

    # Состояние канала (QR, активен, нет денег...)
    for update in body.get("channelsUpdates") or []:
        if bot_channel_id and update.get("channelId") == bot_channel_id:
            events.append({"kind": "channel_state", "state": update.get("state") or ""})

    # Статусы доставки — интересуют только ошибки
    for status in body.get("statuses") or []:
        if status.get("status") == "error":
            events.append({
                "kind": "status_error",
                "message_id": status.get("messageId") or "",
                "error": status.get("error"),
            })

    # Сообщения. Если канал у бота не выбран — сообщения не принимаем (не создаём диалоги).
    messages = body.get("messages") or []
    if not bot_channel_id:
        messages = []
    for message in messages:
        if message.get("chatType") != "whatsapp":
            continue
        if message.get("channelId") != bot_channel_id:
            continue
        if message.get("isDeleted") or message.get("isEdited"):
            continue
        if message.get("status") == "error":
            continue
        chat_id = str(message.get("chatId") or "").strip()
        if not chat_id:
            continue

        event = {
            "chat_id": chat_id,
            "text": message.get("text") or "",
            "message_type": normalize_message_type(message.get("type") or ""),
            "media_url": message.get("contentUri") or None,
            "external_id": message.get("messageId") or None,
        }
        if message.get("isEcho"):
            # Исходящее не через наш API — написал сотрудник
            event["kind"] = "manager"
            event["author_name"] = message.get("authorName") or None
            events.append(event)
        elif message.get("status") == "inbound":
            contact = message.get("contact") or {}
            event["kind"] = "client"
            event["contact_name"] = contact.get("name") or None
            events.append(event)
    return events


def save_channel_state(bot_id: str, state: str) -> None:
    """Сохраняет состояние канала Wazzup в боте."""
    try:
        supabase.table("bots").update({"wazzup_channel_state": state}).eq("id", bot_id).execute()
        print(f"WAZZUP WEBHOOK: состояние канала bot={bot_id} → {state}")
    except Exception as error:
        print(f"WAZZUP WEBHOOK ERROR: не удалось сохранить состояние канала: {error}")


# ============================================================
# Wazzup
# ============================================================

@router.post("/webhook/wazzup/{bot_id}")
async def wazzup_webhook(bot_id: str, request: Request):
    """Принимает события Wazzup. Всегда отвечает 200 быстро."""
    try:
        token = request.query_params.get("token", "")
        bot = load_bot(bot_id)
        if not bot or not is_token_valid(token, bot.get("wazzup_webhook_secret")):
            # 200, чтобы Wazzup не ретраил
            return {"status": "unauthorized"}

        try:
            body = await request.json()
        except Exception:
            return {"status": "bad_request"}

        for event in parse_wazzup_body(body, bot):
            kind = event["kind"]
            if kind == "channel_state":
                save_channel_state(bot_id, event["state"])
            elif kind == "status_error":
                print(f"WAZZUP WEBHOOK: ошибка доставки messageId={event['message_id']} error={event['error']}")
                start_background(handle_delivery_error, bot, event["message_id"], event["error"])
            elif kind == "client":
                start_background(
                    handle_client_message, bot, "whatsapp", event["chat_id"], event["text"],
                    message_type=event["message_type"], media_url=event["media_url"],
                    external_id=event["external_id"], contact_name=event["contact_name"],
                )
            elif kind == "manager":
                start_background(
                    handle_manager_message, bot, "whatsapp", event["chat_id"], event["text"],
                    external_id=event["external_id"], author_name=event["author_name"],
                    message_type=event["message_type"], media_url=event["media_url"],
                )
        return {"status": "ok"}
    except Exception as error:
        print(f"WAZZUP WEBHOOK ERROR: {error}")
        return {"status": "error", "detail": "Internal error"}


# ============================================================
# Telegram
# ============================================================

def find_telegram_bot() -> dict | None:
    """
    Бот, к которому привязан Telegram-канал: строго TELEGRAM_BOT_ID из переменных окружения
    (её задаёт только администратор сервиса в Railway).
    Если переменная не задана — Telegram-канал выключен (None). Никаких «запасных» ботов
    и флагов из БД: иначе клиенты могли бы попасть к чужому владельцу.
    """
    if not TELEGRAM_BOT_ID:
        return None
    result = supabase.table("bots").select("*").eq("id", TELEGRAM_BOT_ID).limit(1).execute()
    if result.data:
        return result.data[0]
    return None


def is_telegram_secret_valid(secret: str) -> bool:
    """Заголовок X-Telegram-Bot-Api-Secret-Token должен совпасть с нашим секретом."""
    if not TELEGRAM_BOT_TOKEN:
        # Telegram-бот не настроен — принимать нечего (и проверить подлинность нечем)
        return False
    expected_secret = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).hexdigest()[:32]
    if not secret:
        return False
    return hmac.compare_digest(secret.encode("utf-8"), expected_secret.encode("utf-8"))


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Принимает входящие сообщения от Telegram."""
    try:
        # Проверяем секретный токен Telegram (если установлен)
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not is_telegram_secret_valid(secret):
            return {"status": "unauthorized"}

        body = await request.json()

        # Telegram отправляет объект Update
        message = body.get("message")
        if not message:
            return {"status": "ignored"}

        # Только личные чаты с клиентами. Группы (например, чат уведомлений сотрудников,
        # если в нём тот же бот) клиентскими диалогами не считаем.
        if (message.get("chat") or {}).get("type") != "private":
            return {"status": "not_private_ignored"}

        text = message.get("text", "")
        chat_id = str(message["chat"]["id"])

        if not text:
            return {"status": "empty"}

        # Игнорируем команды Telegram (типа /start)
        if text.startswith("/"):
            return {"status": "command_ignored"}

        bot = find_telegram_bot()
        if not bot:
            return {"status": "bot_not_found"}

        # external_id — защита от повторной доставки того же сообщения Telegram
        external_id = None
        if message.get("message_id") is not None:
            external_id = f"tg:{chat_id}:{message['message_id']}"

        contact_name = (message.get("from") or {}).get("first_name") or None
        start_background(
            handle_client_message, bot, "telegram", chat_id, text,
            external_id=external_id, contact_name=contact_name,
        )
        return {"status": "ok"}

    except Exception:
        import traceback
        print(f"TELEGRAM WEBHOOK ERROR: {traceback.format_exc()}")
        # Не возвращаем traceback клиенту — это дыра в безопасности
        return {"status": "error", "detail": "Internal error"}


@router.post("/api/setup-telegram-webhook")
async def setup_telegram_webhook(admin: dict = Depends(get_current_admin)):
    """Устанавливает webhook Telegram на текущий сервер с секретным токеном (только админ)."""
    webhook_url = f"{BACKEND_URL}/webhook/telegram"
    secret_token = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).hexdigest()[:32]
    result = await set_webhook(webhook_url, secret_token=secret_token)
    return {"webhook_url": webhook_url, "telegram_response": result}
