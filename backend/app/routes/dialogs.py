"""
API диалогов: список с фильтрами и счётчиками, сообщения, статусы сделки,
отправка сообщения сотрудником, перехват (вкл/выкл ИИ), смена города,
назначение ответственного и статистика.

Права: владелец — всё; менеджер — только диалоги своего города
(или все, если город у него не задан). Удаление и назначение — только владелец.
"""

import re
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from app.database import supabase
from app.auth import get_current_user
from app.access import require_bot_access, require_dialog_access, can_see_dialog, check_uuid
from app import messenger
from app import pipeline

router = APIRouter()

# Все статусы сделки (как в CHECK-ограничении миграции)
DIALOG_STATUSES = ["new", "in_progress", "hot", "invoice_sent", "paid", "lost"]

# Supabase отдаёт максимум 1000 строк за запрос — читаем страницами
PAGE_SIZE = 1000

# Ограничения списка диалогов
DEFAULT_DIALOGS_LIMIT = 200
MAX_DIALOGS_LIMIT = 500

# Лимит длины сообщения WhatsApp
MAX_MESSAGE_LENGTH = 10000

# Значение фильтра city_id для диалогов без города
NO_CITY = "none"

NO_CITY_NAME = "Без города"

# Ссылки на фоновые задачи (иначе сборщик мусора может удалить незавершённую задачу)
background_tasks = set()


# ============================================================
# Чистые функции (без БД) — их проверяют тесты
# ============================================================

def now_iso() -> str:
    """Текущее время UTC в формате ISO (для полей timestamptz)."""
    return datetime.now(timezone.utc).isoformat()


def effective_city_filter(access: dict, requested_city_id) -> str | None:
    """
    Какой фильтр по городу применить к диалогам.
    Возвращает None (все города), NO_CITY ("none" — только без города) или id города.
    Менеджер с городом ВСЕГДА видит только свой город, что бы ни прислал фронтенд.
    """
    if access["role"] == "manager" and access.get("city_id"):
        return str(access["city_id"])

    if requested_city_id is None:
        return None
    text = str(requested_city_id).strip()
    if text == "":
        return None
    if text.lower() in ("none", "null"):
        return NO_CITY
    return check_uuid(text, "город (city_id)")


def filter_dialogs_for_access(access: dict, dialogs: list[dict]) -> list[dict]:
    """Оставляет только диалоги, которые этот пользователь имеет право видеть."""
    result = []
    for dialog in dialogs:
        if can_see_dialog(access, dialog):
            result.append(dialog)
    return result


def clean_search(search) -> str:
    """
    Очищает строку поиска: убираем символы, которые ломают фильтр PostgREST
    (запятые, точки, скобки, звёздочки, проценты, кавычки), и лишние пробелы.
    """
    if not search:
        return ""
    text = re.sub(r"[,.()*%\"'\\:]", " ", str(search))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:100]


def search_digits(search) -> str:
    """Только цифры из строки поиска: "+7 701 123 45 67" → "77011234567" (chat_id хранится цифрами)."""
    return re.sub(r"\D", "", str(search or ""))[:20]


def build_search_filter(search) -> str:
    """
    Условие поиска для PostgREST or_: по имени — как ввели, по номеру — только цифры.
    Если цифр нет — условие по chat_id не добавляем. Пустой поиск → "".
    """
    conditions = []
    digits = search_digits(search)
    if digits:
        conditions.append(f"chat_id.ilike.*{digits}*")
    name = clean_search(search)
    if name:
        conditions.append(f"contact_name.ilike.*{name}*")
    return ",".join(conditions)


def add_city_names(dialogs: list[dict], city_names: dict) -> list[dict]:
    """Добавляет каждому диалогу поле city_name (или None, если город не задан)."""
    result = []
    for dialog in dialogs:
        item = dict(dialog)
        city_id = item.get("city_id")
        item["city_name"] = city_names.get(str(city_id)) if city_id else None
        result.append(item)
    return result


def build_status_update(dialog: dict, new_status: str, now: str) -> dict:
    """
    Поля для обновления диалога при смене статуса.
    invoice_sent → invoice_sent_at, paid → paid_at, hot → hot_at и сброс напоминаний,
    paid/lost → снимаем needs_attention.
    """
    if new_status not in DIALOG_STATUSES:
        raise HTTPException(status_code=400, detail="Неизвестный статус")

    fields = {"status": new_status}
    changed = dialog.get("status") != new_status

    if new_status == "invoice_sent" and (changed or not dialog.get("invoice_sent_at")):
        fields["invoice_sent_at"] = now
    if new_status == "paid" and (changed or not dialog.get("paid_at")):
        fields["paid_at"] = now
    if new_status == "hot" and changed:
        # Вручную отметили «горячий» — планировщик начнёт напоминать с нуля
        fields["hot_at"] = now
        fields["reminder_count"] = 0
    if new_status in ("paid", "lost"):
        fields["needs_attention"] = False

    return fields


def compute_stats(dialogs: list[dict], cities: list[dict]) -> dict:
    """
    Статистика по списку диалогов (каждый — dict с status и city_id).
    cities — города, которые показываем (даже с нулями).
    conversion_paid — процент оплаченных от всех (0..100, одна цифра после запятой).
    """
    by_status = {}
    for status in DIALOG_STATUSES:
        by_status[status] = 0

    # Строки по городам в порядке списка cities
    rows = {}
    order = []
    for city in cities:
        key = str(city["id"])
        rows[key] = {"city_id": city["id"], "city_name": city.get("name"), "total": 0,
                     "hot": 0, "invoice_sent": 0, "paid": 0, "lost": 0}
        order.append(key)

    for dialog in dialogs:
        status = dialog.get("status") or "new"
        if status in by_status:
            by_status[status] += 1

        key = str(dialog["city_id"]) if dialog.get("city_id") else NO_CITY
        if key not in rows:
            # Диалоги без города (или с удалённым городом) — отдельной строкой
            if key == NO_CITY:
                rows[key] = {"city_id": None, "city_name": NO_CITY_NAME, "total": 0,
                             "hot": 0, "invoice_sent": 0, "paid": 0, "lost": 0}
            else:
                rows[key] = {"city_id": dialog["city_id"], "city_name": "Удалённый город", "total": 0,
                             "hot": 0, "invoice_sent": 0, "paid": 0, "lost": 0}
            order.append(key)

        row = rows[key]
        row["total"] += 1
        if status in ("hot", "invoice_sent", "paid", "lost"):
            row[status] += 1

    by_city = []
    for key in order:
        row = rows[key]
        row["conversion_paid"] = round(row["paid"] * 100.0 / row["total"], 1) if row["total"] else 0.0
        by_city.append(row)

    total = len(dialogs)
    conversion = round(by_status["paid"] * 100.0 / total, 1) if total else 0.0
    return {"total": total, "by_status": by_status, "by_city": by_city, "conversion_paid": conversion}


# ============================================================
# Вспомогательные функции с БД
# ============================================================

def get_city_names(bot_id: str) -> dict:
    """Словарь {city_id: name} для городов бота."""
    result = supabase.table("cities").select("id, name").eq("bot_id", bot_id).execute()
    names = {}
    for city in result.data or []:
        names[str(city["id"])] = city.get("name")
    return names


def apply_city_filter(query, city_filter: str | None):
    """Добавляет к запросу фильтр по городу (см. effective_city_filter)."""
    if city_filter == NO_CITY:
        return query.is_("city_id", "null")
    if city_filter:
        return query.eq("city_id", city_filter)
    return query


def count_dialogs(bot_id: str, city_filter: str | None, status: str | None = None,
                  attention: bool = False) -> int:
    """Считает диалоги без загрузки строк (count="exact")."""
    query = supabase.table("dialogs").select("id", count="exact", head=True).eq("bot_id", bot_id)
    query = apply_city_filter(query, city_filter)
    if status:
        query = query.eq("status", status)
    if attention:
        query = query.eq("needs_attention", True)
    result = query.execute()
    return result.count or 0


def update_dialog(dialog_id: str, fields: dict) -> dict:
    """Обновляет диалог и возвращает обновлённую строку."""
    result = supabase.table("dialogs").update(fields).eq("id", dialog_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return result.data[0]


def owner_limit_exceeded(owner_id: str) -> bool:
    """Исчерпан ли лимит диалогов владельца (логика — из app.pipeline)."""
    if not owner_id:
        return False
    # Та же подписка, что и в pipeline (самая свежая, с учётом expires_at)
    subscription = pipeline.get_owner_subscription(owner_id)
    return pipeline.is_limit_exceeded(subscription, pipeline.count_owner_dialogs_this_month(owner_id))


def last_message_role(dialog_id: str) -> str | None:
    """Роль последнего сообщения диалога (user / assistant / manager) или None."""
    result = supabase.table("messages") \
        .select("role, created_at") \
        .eq("dialog_id", dialog_id) \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    if not result.data:
        return None
    return result.data[0].get("role")


def start_reply_task(bot_id: str, dialog_id: str) -> None:
    """Запускает ответ ИИ в фоне; ссылку на задачу храним, пока она не завершится."""
    task = asyncio.create_task(pipeline.process_dialog_reply(bot_id, dialog_id))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


def dialog_with_city_name(dialog: dict) -> dict:
    """Диалог + поле city_name."""
    item = dict(dialog)
    item["city_name"] = None
    if dialog.get("city_id"):
        city = supabase.table("cities").select("name").eq("id", dialog["city_id"]).limit(1).execute()
        if city.data:
            item["city_name"] = city.data[0].get("name")
    return item


# ============================================================
# Список диалогов и сообщения
# ============================================================

@router.get("/api/bots/{bot_id}/dialogs")
def get_dialogs(bot_id: str, status: str | None = None, city_id: str | None = None,
                attention: str | None = None, search: str | None = None,
                limit: int = DEFAULT_DIALOGS_LIMIT, user: dict = Depends(get_current_user)):
    """
    Список диалогов бота с фильтрами.
    status — один из DIALOG_STATUSES; city_id — id города или "none" (без города);
    attention=1 — только требующие внимания; search — по номеру или имени.
    counts считаются с учётом города (но без status/attention/search) — для вкладок.
    """
    access = require_bot_access(bot_id, user["id"])
    city_filter = effective_city_filter(access, city_id)

    if status and status not in DIALOG_STATUSES:
        raise HTTPException(status_code=400, detail="Неизвестный статус")

    limit = max(1, min(int(limit or DEFAULT_DIALOGS_LIMIT), MAX_DIALOGS_LIMIT))

    query = supabase.table("dialogs").select("*").eq("bot_id", bot_id)
    query = apply_city_filter(query, city_filter)
    if status:
        query = query.eq("status", status)
    if attention in ("1", "true", "yes"):
        query = query.eq("needs_attention", True)
    search_filter = build_search_filter(search)
    if search_filter:
        query = query.or_(search_filter)

    # postgrest-py не умеет nullslast — передаём направление прямо в имени колонки:
    # получится order=last_message_at.desc.nullslast (диалоги без сообщений — в конце)
    query = query.order("last_message_at.desc.nullslast").order("created_at", desc=True).limit(limit)
    result = query.execute()

    # Дополнительная страховка: менеджеру — только разрешённые диалоги
    dialogs = filter_dialogs_for_access(access, result.data or [])
    dialogs = add_city_names(dialogs, get_city_names(bot_id))

    counts = {}
    total = 0
    for one_status in DIALOG_STATUSES:
        counts[one_status] = count_dialogs(bot_id, city_filter, status=one_status)
        total += counts[one_status]
    counts["all"] = total
    counts["attention"] = count_dialogs(bot_id, city_filter, attention=True)

    return {"dialogs": dialogs, "counts": counts}


@router.get("/api/dialogs/{dialog_id}/messages")
def get_messages(dialog_id: str, user: dict = Depends(get_current_user)):
    """Все сообщения диалога (постранично из БД) + сам диалог с city_name."""
    dialog, access = require_dialog_access(dialog_id, user["id"])

    messages = []
    start = 0
    while True:
        result = supabase.table("messages") \
            .select("*") \
            .eq("dialog_id", dialog_id) \
            .order("created_at") \
            .order("id") \
            .range(start, start + PAGE_SIZE - 1) \
            .execute()
        rows = result.data or []
        messages.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    return {"messages": messages, "dialog": dialog_with_city_name(dialog)}


# ============================================================
# Действия с диалогом
# ============================================================

@router.post("/api/dialogs/{dialog_id}/toggle-ai")
async def toggle_ai(dialog_id: str, user: dict = Depends(get_current_user)):
    """Режим «Перехват»: выключает/включает ИИ в диалоге."""
    dialog, access = require_dialog_access(dialog_id, user["id"])

    new_disabled = not dialog.get("ai_disabled", False)

    # ИИ был выключен из-за лимита подписки — включить можно, только если лимит уже не превышен
    if not new_disabled and dialog.get("ai_paused_reason") == "limit":
        if owner_limit_exceeded(access["bot"].get("user_id")):
            raise HTTPException(status_code=403, detail="Лимит диалогов по подписке исчерпан — увеличьте тариф")

    fields = {"ai_disabled": new_disabled}
    if new_disabled:
        fields["ai_paused_reason"] = "manual"
    else:
        fields["ai_paused_reason"] = None
        fields["needs_attention"] = False

    updated = update_dialog(dialog_id, fields)

    # ИИ включили, а клиент ждёт ответа (последнее сообщение — от него) — отвечаем сразу
    if not new_disabled and last_message_role(dialog_id) == "user":
        start_reply_task(dialog["bot_id"], dialog_id)

    return {
        "ai_disabled": new_disabled,
        "ai_paused_reason": fields["ai_paused_reason"],
        "dialog": dialog_with_city_name(updated),
    }


@router.post("/api/dialogs/{dialog_id}/status")
def set_status(dialog_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Меняет статус сделки (Счёт отправлен, Оплачено, Потерян, Вернуть в работу...)."""
    dialog, access = require_dialog_access(dialog_id, user["id"])
    fields = build_status_update(dialog, data.get("status"), now_iso())
    updated = update_dialog(dialog_id, fields)
    return {"dialog": dialog_with_city_name(updated)}


@router.post("/api/dialogs/{dialog_id}/send")
async def send_message(dialog_id: str, data: dict, user: dict = Depends(get_current_user)):
    """
    Сотрудник пишет клиенту из панели. Сообщение уходит в WhatsApp/Telegram,
    сохраняется как role "manager", ИИ в диалоге ставится на паузу.
    """
    dialog, access = require_dialog_access(dialog_id, user["id"])

    text = str(data.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Введите текст сообщения")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=400, detail="Сообщение слишком длинное (максимум 10 000 символов)")

    # Сотрудник отвечает сам — ставим ИИ на паузу ДО отправки, чтобы бот не ответил параллельно.
    # Причину 'limit' и т.п. не затираем. При ошибке отправки паузу не откатываем.
    fields = {"needs_attention": False}
    if not dialog.get("ai_disabled"):
        fields["ai_disabled"] = True
        fields["ai_paused_reason"] = "manager_replied"
    updated = update_dialog(dialog_id, fields)

    # access["bot"] — полная строка бота (с ключом Wazzup), на фронт она не уходит
    result = await messenger.send_to_client(access["bot"], dialog, text, from_bot=False)
    if not result or not result.get("ok"):
        error = (result or {}).get("error") or "неизвестная ошибка"
        raise HTTPException(status_code=502, detail=f"Не удалось отправить сообщение: {error}")

    message = pipeline.save_message(
        dialog_id,
        "manager",
        text,
        external_id=result.get("message_id") or None,
        author_name=user.get("email"),
    )

    return {"ok": True, "message": message, "dialog": dialog_with_city_name(updated)}


@router.post("/api/dialogs/{dialog_id}/city")
def set_city(dialog_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Ручная смена города диалога (city_id = null — город не задан)."""
    dialog, access = require_dialog_access(dialog_id, user["id"])

    city_id = data.get("city_id")
    if city_id is not None and str(city_id).strip() == "":
        city_id = None
    if city_id:
        city_id = check_uuid(city_id, "город (city_id)")

    # Менеджер, закреплённый за городом, не может увести диалог в другой город
    if access["role"] == "manager" and access.get("city_id"):
        if str(city_id or "") != str(access["city_id"]):
            raise HTTPException(status_code=403, detail="Вы можете работать только с диалогами своего города")

    if city_id:
        try:
            city = supabase.table("cities").select("id, bot_id").eq("id", city_id).limit(1).execute()
        except Exception:
            raise HTTPException(status_code=404, detail="Город не найден")
        if not city.data or str(city.data[0]["bot_id"]) != str(dialog["bot_id"]):
            raise HTTPException(status_code=404, detail="Город не найден")

    updated = update_dialog(dialog_id, {"city_id": city_id})
    return {"dialog": dialog_with_city_name(updated)}


@router.post("/api/dialogs/{dialog_id}/assign")
def assign_dialog(dialog_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Назначает ответственного (только владелец). user_id = null — снять назначение."""
    dialog, access = require_dialog_access(dialog_id, user["id"])
    if access["role"] != "owner":
        raise HTTPException(status_code=403, detail="Назначать ответственного может только владелец")

    assignee = data.get("user_id")
    if assignee is not None and str(assignee).strip() == "":
        assignee = None
    if assignee:
        assignee = check_uuid(assignee, "пользователь (user_id)")
    if assignee and str(assignee) != str(access["bot"].get("user_id")):
        member = supabase.table("team_members") \
            .select("id") \
            .eq("bot_id", dialog["bot_id"]) \
            .eq("user_id", assignee) \
            .limit(1) \
            .execute()
        if not member.data:
            raise HTTPException(status_code=400, detail="Этот пользователь не состоит в команде бота")

    updated = update_dialog(dialog_id, {"assigned_user_id": assignee})
    return {"dialog": dialog_with_city_name(updated)}


@router.post("/api/dialogs/{dialog_id}/resolve-attention")
def resolve_attention(dialog_id: str, user: dict = Depends(get_current_user)):
    """Снимает пометку «Нужно внимание»."""
    dialog, access = require_dialog_access(dialog_id, user["id"])
    updated = update_dialog(dialog_id, {"needs_attention": False, "attention_reason": None})
    return {"dialog": dialog_with_city_name(updated)}


@router.delete("/api/dialogs/{dialog_id}")
def delete_dialog(dialog_id: str, user: dict = Depends(get_current_user)):
    """Удаляет диалог и все его сообщения (только владелец)."""
    dialog, access = require_dialog_access(dialog_id, user["id"])
    if access["role"] != "owner":
        raise HTTPException(status_code=403, detail="Удалять диалоги может только владелец")

    supabase.table("messages").delete().eq("dialog_id", dialog_id).execute()
    supabase.table("dialogs").delete().eq("id", dialog_id).execute()
    return {"status": "deleted"}


# ============================================================
# Статистика
# ============================================================

@router.get("/api/bots/{bot_id}/stats")
def get_stats(bot_id: str, days: int = 30, user: dict = Depends(get_current_user)):
    """Статистика по диалогам, созданным за последние days дней (по городам и статусам)."""
    access = require_bot_access(bot_id, user["id"])
    days = max(1, min(int(days or 30), 365))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    city_filter = effective_city_filter(access, None)

    # Загружаем только нужные колонки, постранично (диалогов может быть тысячи)
    dialogs = []
    start = 0
    while True:
        query = supabase.table("dialogs").select("id, bot_id, status, city_id").eq("bot_id", bot_id)
        query = apply_city_filter(query, city_filter)
        result = query.gte("created_at", since).order("id").range(start, start + PAGE_SIZE - 1).execute()
        rows = result.data or []
        dialogs.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    dialogs = filter_dialogs_for_access(access, dialogs)

    cities_result = supabase.table("cities") \
        .select("id, name") \
        .eq("bot_id", bot_id) \
        .order("sort_order") \
        .order("name") \
        .execute()
    cities = cities_result.data or []
    if city_filter and city_filter != NO_CITY:
        # Менеджер города — только свой город в таблице
        only_mine = []
        for city in cities:
            if str(city["id"]) == city_filter:
                only_mine.append(city)
        cities = only_mine

    stats = compute_stats(dialogs, cities)
    stats["days"] = days
    return stats
