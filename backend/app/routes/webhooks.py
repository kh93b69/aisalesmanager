import re
import hmac
import hashlib
from fastapi import APIRouter, Request
from app.database import supabase
from app.ai_engine import get_ai_response
from app.rag import search_knowledge
from app.whatsapp import send_whatsapp_message
from app.telegram import send_telegram_message, send_telegram_photo, set_webhook
from app.config import BACKEND_URL, TELEGRAM_BOT_TOKEN, WAHA_API_KEY

router = APIRouter()


def find_bot_images(bot_id: str) -> list:
    """Находит все загруженные картинки бота из knowledge_chunks."""
    try:
        result = supabase.table("knowledge_chunks") \
            .select("content") \
            .eq("bot_id", bot_id) \
            .like("content", "[IMAGE:%") \
            .execute()

        images = []
        for item in result.data:
            content = item["content"]
            url = content.split("]")[0].replace("[IMAGE:", "")
            name = content.split("] ")[-1] if "] " in content else "image"
            images.append({"url": url, "name": name})
        return images
    except Exception:
        return []


async def process_message(bot_id: str, chat_id: str, text: str, channel: str):
    """
    Общая логика обработки входящего сообщения.
    Работает одинаково для WhatsApp, Telegram и других каналов.

    Возвращает ответ ИИ или None если ИИ отключён.
    """
    # Находим бота
    bot_result = supabase.table("bots") \
        .select("*") \
        .eq("id", bot_id) \
        .limit(1) \
        .execute()

    if not bot_result.data:
        return None

    bot = bot_result.data[0]

    # Ищем существующий диалог
    dialog_result = supabase.table("dialogs") \
        .select("*") \
        .eq("bot_id", bot_id) \
        .eq("chat_id", chat_id) \
        .limit(1) \
        .execute()

    # Проверяем режим "Перехват"
    if dialog_result.data and dialog_result.data[0].get("ai_disabled"):
        supabase.table("messages").insert({
            "dialog_id": dialog_result.data[0]["id"],
            "role": "user",
            "content": text,
        }).execute()
        return None

    # Создаём диалог, если его нет
    if not dialog_result.data:
        # Проверяем лимит диалогов по подписке
        user_id = bot.get("user_id")
        if user_id:
            sub = supabase.table("subscriptions") \
                .select("max_dialogs_per_month") \
                .eq("user_id", user_id) \
                .limit(1) \
                .execute()

            max_dialogs = 100  # По умолчанию для бесплатного плана
            if sub.data:
                max_dialogs = sub.data[0].get("max_dialogs_per_month", 100)

            # Считаем текущие диалоги за месяц у всех ботов пользователя
            user_bots = supabase.table("bots").select("id").eq("user_id", user_id).execute()
            total_dialogs = 0
            for ub in user_bots.data:
                d = supabase.table("dialogs").select("id").eq("bot_id", ub["id"]).execute()
                total_dialogs += len(d.data)

            if total_dialogs >= max_dialogs:
                return "Извините, менеджер временно недоступен. Попробуйте позже."

        new_dialog = supabase.table("dialogs").insert({
            "bot_id": bot_id,
            "chat_id": chat_id,
            "channel": channel,
        }).execute()
        dialog_id = new_dialog.data[0]["id"]
    else:
        dialog_id = dialog_result.data[0]["id"]

    # Сохраняем входящее сообщение
    supabase.table("messages").insert({
        "dialog_id": dialog_id,
        "role": "user",
        "content": text,
    }).execute()

    # Получаем историю сообщений для контекста
    history_result = supabase.table("messages") \
        .select("role, content") \
        .eq("dialog_id", dialog_id) \
        .order("created_at") \
        .limit(20) \
        .execute()

    messages = [{"role": m["role"], "content": m["content"]} for m in history_result.data]

    # Ищем релевантный контекст в базе знаний
    knowledge_context = search_knowledge(bot_id, text)

    # Получаем список картинок бота
    bot_images = find_bot_images(bot_id)

    # Добавляем инфо о картинках в промпт
    image_prompt = ""
    if bot_images:
        image_list = "\n".join([f"- {img['name']}: [IMAGE:{img['url']}]" for img in bot_images])
        image_prompt = f"\n\nУ тебя есть картинки, которые можно отправить клиенту. Чтобы отправить картинку, вставь её тег в ответ.\nДоступные картинки:\n{image_list}\n\nВставляй картинку ТОЛЬКО если клиент спрашивает о товаре/услуге, к которой относится картинка."

    # Получаем ответ от ИИ (async)
    ai_response = await get_ai_response(
        system_prompt=bot["system_prompt"] + image_prompt,
        messages=messages,
        knowledge_context=knowledge_context,
    )

    # Сохраняем ответ ИИ
    supabase.table("messages").insert({
        "dialog_id": dialog_id,
        "role": "assistant",
        "content": ai_response,
    }).execute()

    return ai_response


# --- Telegram ---

@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Принимает входящие сообщения от Telegram."""
    try:
        # Проверяем секретный токен Telegram (если установлен)
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        expected_secret = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).hexdigest()[:32]
        if TELEGRAM_BOT_TOKEN and secret and secret != expected_secret:
            return {"status": "unauthorized"}

        body = await request.json()

        # Telegram отправляет объект Update
        message = body.get("message")
        if not message:
            return {"status": "ignored"}

        text = message.get("text", "")
        chat_id = str(message["chat"]["id"])

        if not text:
            return {"status": "empty"}

        # Игнорируем команды Telegram (типа /start)
        if text.startswith("/"):
            return {"status": "command_ignored"}

        # Находим бота с привязанным Telegram
        # Ищем бота у которого telegram_enabled = true
        bot_result = supabase.table("bots") \
            .select("*") \
            .eq("telegram_enabled", True) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if not bot_result.data:
            # Fallback: берём последнего обновлённого бота с системным промптом
            bot_result = supabase.table("bots") \
                .select("*") \
                .neq("system_prompt", "") \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()

        if not bot_result.data:
            return {"status": "bot_not_found"}

        bot = bot_result.data[0]

        ai_response = await process_message(bot["id"], chat_id, text, "telegram")

        if ai_response:
            # Проверяем, есть ли в ответе ссылки на картинки [IMAGE:url]
            image_urls = re.findall(r'\[IMAGE:(https?://[^\]]+)\]', ai_response)

            # Убираем теги картинок из текста
            clean_text = re.sub(r'\[IMAGE:https?://[^\]]+\]', '', ai_response).strip()

            # Отправляем текст
            if clean_text:
                await send_telegram_message(int(chat_id), clean_text)

            # Отправляем картинки
            for img_url in image_urls:
                await send_telegram_photo(int(chat_id), img_url)

        return {"status": "ok"}

    except Exception as error:
        import traceback
        print(f"TELEGRAM WEBHOOK ERROR: {traceback.format_exc()}")
        # Не возвращаем traceback клиенту — это дыра в безопасности
        return {"status": "error", "detail": "Internal error"}


@router.post("/api/setup-telegram-webhook")
async def setup_telegram_webhook():
    """Устанавливает webhook Telegram на текущий сервер с секретным токеном."""
    webhook_url = f"{BACKEND_URL}/webhook/telegram"
    secret_token = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).hexdigest()[:32]
    result = await set_webhook(webhook_url, secret_token=secret_token)
    return {"webhook_url": webhook_url, "telegram_response": result}


# --- WhatsApp ---

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Принимает входящие сообщения от WhatsApp (через WAHA)."""
    try:
        # Проверяем API ключ WAHA (если задан)
        if WAHA_API_KEY:
            api_key = request.headers.get("X-Api-Key", "")
            if api_key != WAHA_API_KEY:
                return {"status": "unauthorized"}

        body = await request.json()

        if body.get("event") != "message":
            return {"status": "ignored"}

        message_data = body.get("payload", {})
        chat_id = message_data.get("from", "")
        text = message_data.get("body", "")
        session = body.get("session", "default")

        # Игнорируем свои исходящие сообщения
        if message_data.get("fromMe", False):
            return {"status": "echo_ignored"}

        if not text or not chat_id:
            return {"status": "empty"}

        # Находим бота по сессии WhatsApp (берём последнего обновлённого)
        bot_result = supabase.table("bots") \
            .select("*") \
            .eq("whatsapp_session", session) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if not bot_result.data:
            return {"status": "bot_not_found"}

        bot = bot_result.data[0]

        ai_response = await process_message(bot["id"], chat_id, text, "whatsapp")

        if ai_response:
            # Убираем теги картинок из текста (картинки пока только в Telegram)
            clean_text = re.sub(r'\[IMAGE:https?://[^\]]+\]', '', ai_response).strip()
            if clean_text:
                await send_whatsapp_message(chat_id, clean_text, session)

        return {"status": "ok"}

    except Exception as error:
        import traceback
        print(f"WHATSAPP WEBHOOK ERROR: {traceback.format_exc()}")
        return {"status": "error", "detail": "Internal error"}
