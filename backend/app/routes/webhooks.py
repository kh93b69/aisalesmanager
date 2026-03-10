from fastapi import APIRouter, Request
from app.database import supabase
from app.ai_engine import get_ai_response
from app.rag import search_knowledge
from app.whatsapp import send_whatsapp_message

router = APIRouter()


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Принимает входящие сообщения от WhatsApp (через WAHA).
    Обрабатывает их через ИИ и отправляет ответ обратно.
    """
    body = await request.json()

    # WAHA отправляет разные типы событий, нам нужны только сообщения
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

    # Проверяем, не отключён ли ИИ для этого диалога (режим "Перехват")
    dialog_result = supabase.table("dialogs") \
        .select("*") \
        .eq("bot_id", bot["id"]) \
        .eq("chat_id", chat_id) \
        .limit(1) \
        .execute()

    if dialog_result.data and dialog_result.data[0].get("ai_disabled"):
        # ИИ отключён — просто сохраняем сообщение
        supabase.table("messages").insert({
            "dialog_id": dialog_result.data[0]["id"],
            "role": "user",
            "content": text,
        }).execute()
        return {"status": "ai_disabled"}

    # Создаём диалог, если его нет
    if not dialog_result.data:
        new_dialog = supabase.table("dialogs").insert({
            "bot_id": bot["id"],
            "chat_id": chat_id,
            "channel": "whatsapp",
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
    knowledge_context = search_knowledge(bot["id"], text)

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

    # Отправляем ответ в WhatsApp
    await send_whatsapp_message(chat_id, ai_response, session)

    return {"status": "ok", "response": ai_response}
