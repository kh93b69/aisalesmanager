from fastapi import APIRouter, Request
from app.database import supabase
from app.ai_engine import get_ai_response
from app.rag import search_knowledge
from app.whatsapp import send_whatsapp_message
from app.telegram import send_telegram_message, set_webhook
from app.config import BACKEND_URL

router = APIRouter()


def process_message(bot_id: str, chat_id: str, text: str, channel: str):
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

    # Получаем ответ от ИИ
    ai_response = get_ai_response(
        system_prompt=bot["system_prompt"],
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
        body = await request.json()

        # Telegram отправляет объект Update
        message = body.get("message")
        if not message:
            return {"status": "ignored"}

        text = message.get("text", "")
        chat_id = str(message["chat"]["id"])

        if not text:
            return {"status": "empty"}

        # Находим бота по telegram_token (для MVP — берём первого бота)
        bot_result = supabase.table("bots") \
            .select("*") \
            .limit(1) \
            .execute()

        if not bot_result.data:
            return {"status": "bot_not_found"}

        bot = bot_result.data[0]

        ai_response = process_message(bot["id"], chat_id, text, "telegram")

        if ai_response:
            await send_telegram_message(int(chat_id), ai_response)

        return {"status": "ok"}

    except Exception as error:
        # Логируем ошибку и возвращаем детали (для отладки)
        import traceback
        error_details = traceback.format_exc()
        print(f"TELEGRAM WEBHOOK ERROR: {error_details}")
        return {"status": "error", "detail": str(error), "traceback": error_details}


@router.post("/api/setup-telegram-webhook")
async def setup_telegram_webhook():
    """Устанавливает webhook Telegram на текущий сервер."""
    webhook_url = f"{BACKEND_URL}/webhook/telegram"
    result = await set_webhook(webhook_url)
    return {"webhook_url": webhook_url, "telegram_response": result}


# --- WhatsApp ---

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Принимает входящие сообщения от WhatsApp (через WAHA)."""
    body = await request.json()

    if body.get("event") != "message":
        return {"status": "ignored"}

    message_data = body.get("payload", {})
    chat_id = message_data.get("from", "")
    text = message_data.get("body", "")
    session = body.get("session", "default")

    if not text or not chat_id:
        return {"status": "empty"}

    # Находим бота по сессии WhatsApp
    bot_result = supabase.table("bots") \
        .select("*") \
        .eq("whatsapp_session", session) \
        .limit(1) \
        .execute()

    if not bot_result.data:
        return {"status": "bot_not_found"}

    bot = bot_result.data[0]

    ai_response = process_message(bot["id"], chat_id, text, "whatsapp")

    if ai_response:
        await send_whatsapp_message(chat_id, ai_response, session)

    return {"status": "ok"}
