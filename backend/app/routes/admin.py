from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from app.database import supabase
from app.auth import get_current_admin
from app.access import check_uuid

# Допустимые значения подписки
ALLOWED_SUB_STATUSES = ["active", "expired", "cancelled"]
MAX_BOTS_LIMIT = 100
MAX_DIALOGS_LIMIT = 1000000

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
        if bot_ids:
            # count="exact" — считаем на стороне БД (лимит 1000 строк не мешает)
            dialogs = supabase.table("dialogs") \
                .select("id", count="exact", head=True) \
                .in_("bot_id", bot_ids) \
                .execute()
            dialog_count = dialogs.count or 0

        result.append({
            **user,
            "subscription": sub.data[0] if sub.data else None,
            "bots_count": len(bots.data),
            "dialogs_count": dialog_count,
        })

    return {"users": result}


def read_int(value, label: str, minimum: int, maximum: int) -> int:
    """Целое число из формы админки (иначе ошибка 400, а не 500)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label}: введите целое число")
    if number < minimum or number > maximum:
        raise HTTPException(status_code=400, detail=f"{label}: от {minimum} до {maximum}")
    return number


def read_expires_at(value):
    """
    Дата окончания подписки: пусто/None — бессрочно, иначе дата ISO (2026-12-31 или с временем).
    """
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    # Только дата (из календаря админки) — подписка действует до конца этого дня по Казахстану
    if len(text) == 10:
        text = f"{text}T23:59:59+05:00"
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверная дата окончания подписки")
    return text


@router.put("/api/admin/users/{user_id}/subscription")
def update_subscription(user_id: str, data: dict, admin: dict = Depends(get_current_admin)):
    """Создаёт или обновляет подписку пользователя."""
    user_id = check_uuid(user_id, "id пользователя")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Неверный формат данных")

    status = data.get("status", "active")
    if status not in ALLOWED_SUB_STATUSES:
        raise HTTPException(status_code=400, detail="Неверный статус подписки")

    safe_data = {
        "plan": str(data.get("plan") or "free")[:50],
        "status": status,
        "max_bots": read_int(data.get("max_bots", 1), "Ботов", 1, MAX_BOTS_LIMIT),
        "max_dialogs_per_month": read_int(data.get("max_dialogs_per_month", 100), "Диалогов в месяц", 0, MAX_DIALOGS_LIMIT),
        # Пустая дата — подписка бессрочная (так можно и продлить истёкшую: очистить дату)
        "expires_at": read_expires_at(data.get("expires_at")),
    }

    # Проверяем, есть ли подписка
    existing = supabase.table("subscriptions") \
        .select("id") \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()

    if existing.data:
        result = supabase.table("subscriptions") \
            .update(safe_data) \
            .eq("user_id", user_id) \
            .execute()
    else:
        safe_data["user_id"] = user_id
        result = supabase.table("subscriptions") \
            .insert(safe_data) \
            .execute()

    return {"subscription": result.data[0] if result.data else None}


@router.put("/api/admin/users/{user_id}/role")
def update_role(user_id: str, data: dict, admin: dict = Depends(get_current_admin)):
    """Меняет роль пользователя."""
    user_id = check_uuid(user_id, "id пользователя")
    role = data.get("role", "user") if isinstance(data, dict) else None
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    if user_id == admin["id"] and role != "admin":
        raise HTTPException(status_code=400, detail="Нельзя снять права администратора с себя")

    supabase.table("users") \
        .update({"role": role}) \
        .eq("id", user_id) \
        .execute()

    return {"status": "ok", "role": role}


@router.get("/api/admin/stats")
def get_stats(admin: dict = Depends(get_current_admin)):
    """Общая статистика сервиса."""
    # count="exact" + head=True — БД возвращает только количество, без строк
    users = supabase.table("users").select("id", count="exact", head=True).execute()
    bots = supabase.table("bots").select("id", count="exact", head=True).execute()
    dialogs = supabase.table("dialogs").select("id", count="exact", head=True).execute()
    messages = supabase.table("messages").select("id", count="exact", head=True).execute()

    return {
        "users_count": users.count or 0,
        "bots_count": bots.count or 0,
        "dialogs_count": dialogs.count or 0,
        "messages_count": messages.count or 0,
    }


@router.delete("/api/admin/users/{user_id}")
def delete_user(user_id: str, admin: dict = Depends(get_current_admin)):
    """Удаляет пользователя и все его данные."""
    user_id = check_uuid(user_id, "id пользователя")
    # Нельзя удалить себя
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Нельзя удалить свой аккаунт")

    # Удаляем подписку
    supabase.table("subscriptions").delete().eq("user_id", user_id).execute()

    # Удаляем ботов (и всё связанное)
    bots = supabase.table("bots").select("id").eq("user_id", user_id).execute()
    for bot in bots.data:
        # Удаляем сообщения и диалоги порциями (Supabase отдаёт максимум 1000 строк за раз)
        while True:
            dialogs = supabase.table("dialogs").select("id").eq("bot_id", bot["id"]).limit(100).execute()
            if not dialogs.data:
                break
            dialog_ids = [d["id"] for d in dialogs.data]
            supabase.table("messages").delete().in_("dialog_id", dialog_ids).execute()
            supabase.table("dialogs").delete().in_("id", dialog_ids).execute()
        supabase.table("knowledge_chunks").delete().eq("bot_id", bot["id"]).execute()
        delete_bot_image_files(bot["id"])
        # Города и команда бота удалятся каскадно вместе с ботом (FK ON DELETE CASCADE)

    # Пользователь мог быть сотрудником в чужих ботах — убираем его из команд
    supabase.table("team_members").delete().eq("user_id", user_id).execute()

    supabase.table("bots").delete().eq("user_id", user_id).execute()
    supabase.table("users").delete().eq("id", user_id).execute()

    # Удаляем и сам аккаунт входа (Supabase Auth) — иначе человек сможет войти со старым паролем
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception as error:
        print(f"ADMIN ERROR: не удалось удалить аккаунт входа {user_id}: {error}")

    return {"status": "deleted"}


def delete_bot_image_files(bot_id: str) -> None:
    """Удаляет файлы картинок бота из Storage (папка bot_images/{bot_id}). Ошибки — только в лог."""
    try:
        files = supabase.storage.from_("bot_images").list(bot_id)
        paths = []
        for item in files or []:
            if item.get("name"):
                paths.append(f"{bot_id}/{item['name']}")
        if paths:
            supabase.storage.from_("bot_images").remove(paths)
    except Exception as error:
        print(f"ADMIN ERROR: не удалось удалить картинки бота {bot_id}: {error}")
