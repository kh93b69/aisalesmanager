from fastapi import APIRouter, Depends
from app.database import supabase
from app.auth import get_current_admin

router = APIRouter()


@router.get("/api/admin/users")
def get_users(admin: dict = Depends(get_current_admin)):
    """Получает список всех пользователей с подписками."""
    users = supabase.table("users").select("*").execute()

    # Подгружаем подписки для каждого пользователя
    result = []
    for user in users.data:
        sub = supabase.table("subscriptions") \
            .select("*") \
            .eq("user_id", user["id"]) \
            .limit(1) \
            .execute()

        # Считаем количество ботов и диалогов
        bots = supabase.table("bots") \
            .select("id") \
            .eq("user_id", user["id"]) \
            .execute()

        bot_ids = [b["id"] for b in bots.data]
        dialog_count = 0
        for bot_id in bot_ids:
            dialogs = supabase.table("dialogs") \
                .select("id") \
                .eq("bot_id", bot_id) \
                .execute()
            dialog_count += len(dialogs.data)

        result.append({
            **user,
            "subscription": sub.data[0] if sub.data else None,
            "bots_count": len(bots.data),
            "dialogs_count": dialog_count,
        })

    return {"users": result}


@router.put("/api/admin/users/{user_id}/subscription")
def update_subscription(user_id: str, data: dict, admin: dict = Depends(get_current_admin)):
    """Создаёт или обновляет подписку пользователя."""
    # Проверяем, есть ли подписка
    existing = supabase.table("subscriptions") \
        .select("id") \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()

    safe_data = {
        "plan": data.get("plan", "free"),
        "status": data.get("status", "active"),
        "max_bots": data.get("max_bots", 1),
        "max_dialogs_per_month": data.get("max_dialogs_per_month", 100),
    }

    if data.get("expires_at"):
        safe_data["expires_at"] = data["expires_at"]

    if existing.data:
        # Обновляем
        result = supabase.table("subscriptions") \
            .update(safe_data) \
            .eq("user_id", user_id) \
            .execute()
    else:
        # Создаём
        safe_data["user_id"] = user_id
        result = supabase.table("subscriptions") \
            .insert(safe_data) \
            .execute()

    return {"subscription": result.data[0] if result.data else None}


@router.put("/api/admin/users/{user_id}/role")
def update_role(user_id: str, data: dict, admin: dict = Depends(get_current_admin)):
    """Меняет роль пользователя."""
    role = data.get("role", "user")
    if role not in ("user", "admin"):
        return {"error": "Недопустимая роль"}

    supabase.table("users") \
        .update({"role": role}) \
        .eq("id", user_id) \
        .execute()

    return {"status": "ok", "role": role}


@router.get("/api/admin/stats")
def get_stats(admin: dict = Depends(get_current_admin)):
    """Общая статистика сервиса."""
    users = supabase.table("users").select("id").execute()
    bots = supabase.table("bots").select("id").execute()
    dialogs = supabase.table("dialogs").select("id").execute()
    messages = supabase.table("messages").select("id").execute()

    return {
        "users_count": len(users.data),
        "bots_count": len(bots.data),
        "dialogs_count": len(dialogs.data),
        "messages_count": len(messages.data),
    }


@router.delete("/api/admin/users/{user_id}")
def delete_user(user_id: str, admin: dict = Depends(get_current_admin)):
    """Удаляет пользователя и все его данные."""
    # Нельзя удалить себя
    if user_id == admin["id"]:
        return {"error": "Нельзя удалить свой аккаунт"}

    # Удаляем подписку
    supabase.table("subscriptions").delete().eq("user_id", user_id).execute()

    # Удаляем ботов (и всё связанное)
    bots = supabase.table("bots").select("id").eq("user_id", user_id).execute()
    for bot in bots.data:
        # Удаляем сообщения и диалоги
        dialogs = supabase.table("dialogs").select("id").eq("bot_id", bot["id"]).execute()
        for dialog in dialogs.data:
            supabase.table("messages").delete().eq("dialog_id", dialog["id"]).execute()
        supabase.table("dialogs").delete().eq("bot_id", bot["id"]).execute()
        supabase.table("knowledge_chunks").delete().eq("bot_id", bot["id"]).execute()

    supabase.table("bots").delete().eq("user_id", user_id).execute()
    supabase.table("users").delete().eq("id", user_id).execute()

    return {"status": "deleted"}
