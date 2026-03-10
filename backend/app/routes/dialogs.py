from fastapi import APIRouter
from app.database import supabase

router = APIRouter()


@router.get("/api/bots/{bot_id}/dialogs")
def get_dialogs(bot_id: str):
    """Получает список всех диалогов бота."""
    result = supabase.table("dialogs") \
        .select("*") \
        .eq("bot_id", bot_id) \
        .order("updated_at", desc=True) \
        .execute()

    return {"dialogs": result.data}


@router.get("/api/dialogs/{dialog_id}/messages")
def get_messages(dialog_id: str):
    """Получает все сообщения диалога."""
    result = supabase.table("messages") \
        .select("*") \
        .eq("dialog_id", dialog_id) \
        .order("created_at") \
        .execute()

    return {"messages": result.data}


@router.post("/api/dialogs/{dialog_id}/toggle-ai")
def toggle_ai(dialog_id: str):
    """
    Переключает режим "Перехват" — включает/отключает ИИ для диалога.
    """
    # Получаем текущий статус
    dialog = supabase.table("dialogs") \
        .select("ai_disabled") \
        .eq("id", dialog_id) \
        .limit(1) \
        .execute()

    if not dialog.data:
        return {"error": "Диалог не найден"}, 404

    current_status = dialog.data[0].get("ai_disabled", False)

    # Переключаем
    supabase.table("dialogs") \
        .update({"ai_disabled": not current_status}) \
        .eq("id", dialog_id) \
        .execute()

    return {"ai_disabled": not current_status}
