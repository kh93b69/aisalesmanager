from fastapi import APIRouter, Depends, HTTPException
from app.database import supabase
from app.auth import get_current_user

router = APIRouter()


def _check_dialog_ownership(dialog_id: str, user_id: str):
    """Проверяет что диалог принадлежит боту текущего пользователя."""
    dialog = supabase.table("dialogs") \
        .select("*, bots(user_id)") \
        .eq("id", dialog_id) \
        .limit(1) \
        .execute()

    if not dialog.data:
        raise HTTPException(status_code=404, detail="Диалог не найден")

    bot_owner = dialog.data[0].get("bots", {}).get("user_id")
    if bot_owner != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа")

    return dialog.data[0]


@router.get("/api/bots/{bot_id}/dialogs")
def get_dialogs(bot_id: str, user: dict = Depends(get_current_user)):
    """Получает список всех диалогов бота."""
    bot = supabase.table("bots").select("id").eq("id", bot_id).eq("user_id", user["id"]).limit(1).execute()
    if not bot.data:
        return {"dialogs": []}

    result = supabase.table("dialogs") \
        .select("*") \
        .eq("bot_id", bot_id) \
        .order("created_at", desc=True) \
        .execute()

    return {"dialogs": result.data}


@router.get("/api/dialogs/{dialog_id}/messages")
def get_messages(dialog_id: str, user: dict = Depends(get_current_user)):
    """Получает все сообщения диалога."""
    _check_dialog_ownership(dialog_id, user["id"])

    result = supabase.table("messages") \
        .select("*") \
        .eq("dialog_id", dialog_id) \
        .order("created_at") \
        .execute()

    return {"messages": result.data}


@router.post("/api/dialogs/{dialog_id}/toggle-ai")
def toggle_ai(dialog_id: str, user: dict = Depends(get_current_user)):
    """Переключает режим 'Перехват' — включает/отключает ИИ для диалога."""
    dialog_data = _check_dialog_ownership(dialog_id, user["id"])

    current_status = dialog_data.get("ai_disabled", False)

    supabase.table("dialogs") \
        .update({"ai_disabled": not current_status}) \
        .eq("id", dialog_id) \
        .execute()

    return {"ai_disabled": not current_status}


@router.delete("/api/dialogs/{dialog_id}")
def delete_dialog(dialog_id: str, user: dict = Depends(get_current_user)):
    """Удаляет диалог и все его сообщения."""
    _check_dialog_ownership(dialog_id, user["id"])

    supabase.table("messages").delete().eq("dialog_id", dialog_id).execute()
    supabase.table("dialogs").delete().eq("id", dialog_id).execute()
    return {"status": "deleted"}
