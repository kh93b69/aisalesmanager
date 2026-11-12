"""
Права доступа к ботам и диалогам.

Роли:
- owner (владелец) — bots.user_id == user_id. Может всё.
- manager (сотрудник) — есть строка в team_members(bot_id, user_id).
  Видит диалоги своего города (или все, если city_id у него NULL),
  меняет статусы, пишет клиентам, перехватывает диалоги, смотрит статистику.
  Настройки бота менеджеру недоступны.

Чистые функции (decide_access, can_see_dialog, public_bot) не ходят в БД —
их удобно проверять тестами.
"""

import uuid

import httpx
from fastapi import HTTPException
from app.database import supabase

# Текст ошибки, когда связь с базой временно оборвалась
DB_UNAVAILABLE_TEXT = "Связь с базой данных прервалась — повторите через несколько секунд"

# Поля бота, которые НИКОГДА не отдаём на фронтенд
SECRET_BOT_FIELDS = ["wazzup_api_key", "wazzup_webhook_secret"]


def check_uuid(value, label: str = "id") -> str:
    """
    Проверяет, что значение — корректный uuid (иначе БД ответит ошибкой 500).
    Возвращает строку uuid или HTTPException 400.
    """
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Неверный {label}")


def decide_access(bot: dict | None, member: dict | None, user_id: str) -> dict | None:
    """
    Решает, какой доступ у пользователя к боту (без запросов к БД).
    bot — строка из таблицы bots (или None), member — строка team_members (или None).
    Возвращает {"bot", "role", "city_id", "member"} или None, если доступа нет.
    """
    if not bot or not user_id:
        return None

    # Владелец — полный доступ ко всем городам
    if str(bot.get("user_id")) == str(user_id):
        return {"bot": bot, "role": "owner", "city_id": None, "member": None}

    # Сотрудник из команды этого бота
    if member and str(member.get("bot_id")) == str(bot.get("id")) and str(member.get("user_id")) == str(user_id):
        return {"bot": bot, "role": "manager", "city_id": member.get("city_id"), "member": member}

    return None


def can_see_dialog(access: dict, dialog: dict) -> bool:
    """
    Может ли пользователь с этим доступом видеть диалог.
    Владелец и менеджер без города — видят все диалоги бота.
    Менеджер с городом — только диалоги своего города (диалоги без города — нет).
    """
    if not access or not dialog:
        return False
    if str(dialog.get("bot_id")) != str(access["bot"].get("id")):
        return False
    if access["role"] == "owner":
        return True
    if not access.get("city_id"):
        return True
    return str(dialog.get("city_id") or "") == str(access["city_id"])


def public_bot(bot: dict) -> dict:
    """
    Копия бота для фронтенда: без секретов Wazzup.
    Добавляет поле wazzup_connected (есть и API-ключ, и канал).
    """
    result = {}
    for key in bot:
        if key not in SECRET_BOT_FIELDS:
            result[key] = bot[key]
    result["wazzup_connected"] = bool(bot.get("wazzup_api_key")) and bool(bot.get("wazzup_channel_id"))
    return result


def get_bot_access(bot_id: str, user_id: str) -> dict | None:
    """
    Загружает бота и (при необходимости) запись в команде, решает доступ.
    Возвращает {"bot", "role", "city_id", "member"} или None.
    """
    if not bot_id or not user_id:
        return None

    try:
        bot_result = supabase.table("bots").select("*").eq("id", bot_id).limit(1).execute()
    except httpx.HTTPError as e:
        # Связь с базой оборвалась — это не «нет доступа», а временный сбой
        print(f"ACCESS ERROR: база недоступна: {e}")
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE_TEXT)
    except Exception as e:
        # Неверный uuid в адресе (код 22P02) — бота нет. Любая другая ошибка — временный сбой базы.
        if "22P02" in str(e):
            return None
        print(f"ACCESS ERROR: не удалось загрузить бота: {e}")
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE_TEXT)

    if not bot_result.data:
        return None
    bot = bot_result.data[0]

    # Владельцу запись в команде не нужна
    if str(bot.get("user_id")) == str(user_id):
        return decide_access(bot, None, user_id)

    try:
        member_result = supabase.table("team_members") \
            .select("*") \
            .eq("bot_id", bot_id) \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
    except httpx.HTTPError as e:
        print(f"ACCESS ERROR: база недоступна: {e}")
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE_TEXT)
    except Exception as e:
        print(f"ACCESS ERROR: не удалось загрузить команду: {e}")
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE_TEXT)

    member = member_result.data[0] if member_result.data else None
    return decide_access(bot, member, user_id)


def require_bot_access(bot_id: str, user_id: str, owner_only: bool = False) -> dict:
    """
    Как get_bot_access, но без доступа — HTTPException 403.
    owner_only=True — действие разрешено только владельцу (настройки).
    """
    access = get_bot_access(bot_id, user_id)
    if not access:
        raise HTTPException(status_code=403, detail="Нет доступа к этому боту")
    if owner_only and access["role"] != "owner":
        raise HTTPException(status_code=403, detail="Только владелец может менять настройки")
    return access


def require_dialog_access(dialog_id: str, user_id: str) -> tuple[dict, dict]:
    """
    Загружает диалог и проверяет доступ к нему.
    Возвращает (dialog, access). 404 — диалога нет, 403 — нет доступа.
    """
    try:
        result = supabase.table("dialogs").select("*").eq("id", dialog_id).limit(1).execute()
    except Exception as e:
        print(f"ACCESS ERROR: не удалось загрузить диалог: {e}")
        raise HTTPException(status_code=404, detail="Диалог не найден")

    if not result.data:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    dialog = result.data[0]

    access = get_bot_access(dialog.get("bot_id"), user_id)
    if not access:
        raise HTTPException(status_code=403, detail="Нет доступа к этому боту")
    if not can_see_dialog(access, dialog):
        raise HTTPException(status_code=403, detail="Этот диалог относится к другому городу")

    return dialog, access
