"""
Сводка дня в Telegram руководителю (Настройки → Уведомления → «Сводка дня»).

Раз в минуту (из планировщика) проверяем ботов: если включена сводка, у бота задан чат
руководителя (telegram_notify_chat_id), по местному времени уже наступило время отправки
(по умолчанию 20:00) и сегодня сводка ещё не уходила — собираем цифры за последние сутки и отправляем.
Дата последней отправки хранится в bots.daily_report_last_date (после перезапуска не задвоится).
"""
import html
from datetime import datetime, timezone, timedelta

from app.database import supabase
from app.config import BACKEND_URL
from app import pipeline
from app.notifier import send_notification, format_phone
from app.work_hours import to_local, parse_hhmm, WEEKDAYS_SHORT

DEFAULT_REPORT_TIME = "20:00"
# Сколько горячих клиентов перечисляем в сводке
MAX_HOT_IN_REPORT = 5
# Сколько символов «что хочет клиент» показываем
MAX_REASON_LENGTH = 80


# ============================================================
# Чистые функции (без БД) — их проверяют тесты
# ============================================================

def period_start_utc_iso(local_now: datetime) -> str:
    """
    Начало периода сводки — ровно сутки назад (в UTC, ISO). Сводка уходит раз в день в одно
    и то же время, поэтому каждое событие попадает ровно в одну сводку (в том числе вечерние после отправки).
    """
    return (local_now - timedelta(hours=24)).astimezone(timezone.utc).isoformat()


def is_report_due(bot: dict, local_now: datetime) -> bool:
    """Пора ли отправлять сводку: включена, время наступило, сегодня ещё не отправляли."""
    if not bot.get("daily_report_enabled"):
        return False
    if not str(bot.get("telegram_notify_chat_id") or "").strip():
        return False
    report_minute = parse_hhmm(bot.get("daily_report_time") or DEFAULT_REPORT_TIME)
    if report_minute is None:
        report_minute = parse_hhmm(DEFAULT_REPORT_TIME)
    if local_now.hour * 60 + local_now.minute < report_minute:
        return False
    return str(bot.get("daily_report_last_date") or "") != local_now.date().isoformat()


def percent(part: int, total: int) -> int:
    """Целый процент (0, если total = 0)."""
    if not total:
        return 0
    return round(part * 100 / total)


def short_text(text, limit: int = MAX_REASON_LENGTH) -> str:
    """Обрезает текст до limit символов."""
    text = str(text or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def build_report_text(bot: dict, local_now: datetime, data: dict) -> str:
    """
    Текст сводки (HTML для Telegram). data — цифры из collect_report_data:
    new, active, hot, hot_from_new, invoice, paid, followups, hot_waiting, attention,
    questions_open, questions_new, hot_list [{name, city, reason}], cities [{name, new, hot}].
    """
    title = (bot.get("company_name") or bot.get("name") or "бот").strip()
    date_text = f"{WEEKDAYS_SHORT[local_now.weekday()]}, {local_now.day} {pipeline.MONTHS_RU[local_now.month - 1]}"

    lines = [f"📊 <b>Сводка дня — {html.escape(title)}</b>", f"{date_text}, за последние 24 часа", ""]

    if not data["new"] and not data["active"]:
        lines.append("За сутки клиенты не писали.")
    else:
        lines.append(f"📥 Новых обращений: <b>{data['new']}</b>")
        lines.append(f"💬 Диалогов с сообщениями: {data['active']}")
        hot_line = f"🔥 Горячих клиентов: <b>{data['hot']}</b>"
        if data["new"]:
            hot_line += f" (из новых — {percent(data['hot_from_new'], data['new'])}%)"
        lines.append(hot_line)
        lines.append(f"💳 Счетов и ссылок на оплату: {data['invoice']}")
        lines.append(f"✅ Оплачено: {data['paid']}")
        if data["followups"]:
            lines.append(f"🔁 Клиентов получили дожим: {data['followups']}")

    # Что требует внимания прямо сейчас
    todo = []
    if data["hot_waiting"]:
        todo.append(f"⏳ Горячие ждут счёт: <b>{data['hot_waiting']}</b>")
    if data["attention"]:
        todo.append(f"⚠️ Ждут менеджера: <b>{data['attention']}</b>")
    if data["questions_open"]:
        question_line = f"❓ Вопросов без ответа: <b>{data['questions_open']}</b>"
        if data["questions_new"]:
            question_line += f" (новых за сутки: {data['questions_new']})"
        todo.append(question_line + " — ответьте в Настройки → «❓ Без ответа»")
    lines.append("")
    if todo:
        lines.append("<b>Требует внимания:</b>")
        lines += todo
    else:
        lines.append("👍 Всё разобрано — никто не ждёт ответа")

    if data["hot_list"]:
        lines.append("")
        lines.append("<b>Горячие за сутки:</b>")
        for number, item in enumerate(data["hot_list"], start=1):
            line = f"{number}. {html.escape(short_text(item['name'], 40))}"
            if item.get("city"):
                line += f" ({html.escape(item['city'])})"
            if item.get("reason"):
                line += f" — {html.escape(short_text(item['reason']))}"
            lines.append(line)

    if len(data["cities"]) > 1:
        lines.append("")
        lines.append("<b>По городам</b> (новые / горячие):")
        for city in data["cities"]:
            lines.append(f"📍 {html.escape(city['name'])}: {city['new']} / {city['hot']}")

    base_url = (BACKEND_URL or "").rstrip("/")
    if base_url:
        lines.append("")
        lines.append(f'➡️ <a href="{html.escape(base_url, quote=True)}/">Открыть панель</a>')
    return "\n".join(lines)


# ============================================================
# Работа с БД
# ============================================================

def count_rows(table: str, bot_id: str, filters: list[tuple]) -> int:
    """
    Количество строк таблицы бота. filters — список (операция, колонка, значение),
    операции: "eq" и "gte".
    """
    query = supabase.table(table).select("id", count="exact", head=True).eq("bot_id", bot_id)
    for operation, column, value in filters:
        if operation == "eq":
            query = query.eq(column, value)
        elif operation == "gte":
            query = query.gte(column, value)
    return query.execute().count or 0


def collect_report_data(bot: dict, since_iso: str) -> dict:
    """Цифры для сводки: за период (с since_iso) и «на сейчас»."""
    bot_id = bot["id"]
    data = {
        "new": count_rows("dialogs", bot_id, [("gte", "created_at", since_iso)]),
        "active": count_rows("dialogs", bot_id, [("gte", "last_message_at", since_iso)]),
        "hot": count_rows("dialogs", bot_id, [("gte", "hot_at", since_iso)]),
        "hot_from_new": count_rows("dialogs", bot_id, [("gte", "created_at", since_iso),
                                                       ("gte", "hot_at", since_iso)]),
        "invoice": count_rows("dialogs", bot_id, [("gte", "invoice_sent_at", since_iso)]),
        "paid": count_rows("dialogs", bot_id, [("gte", "paid_at", since_iso)]),
        "hot_waiting": count_rows("dialogs", bot_id, [("eq", "status", "hot")]),
        "attention": count_rows("dialogs", bot_id, [("eq", "needs_attention", True)]),
        "questions_open": 0,
        "questions_new": 0,
        "followups": 0,
        "hot_list": [],
        "cities": [],
    }

    # Эти цифры зависят от миграции 003 — без неё просто не показываем
    try:
        data["followups"] = count_rows("dialogs", bot_id, [("gte", "last_followup_at", since_iso)])
        data["questions_open"] = count_rows("unanswered_questions", bot_id, [("eq", "status", "open")])
        data["questions_new"] = count_rows("unanswered_questions", bot_id, [("gte", "created_at", since_iso)])
    except Exception as error:
        print(f"REPORT: часть цифр недоступна: {error}")

    cities = pipeline.get_bot_cities(bot_id)
    city_names = {}
    for city in cities:
        city_names[city["id"]] = city.get("name") or ""

    hot_rows = supabase.table("dialogs") \
        .select("contact_name, chat_id, city_id, hot_reason") \
        .eq("bot_id", bot_id) \
        .gte("hot_at", since_iso) \
        .order("hot_at", desc=True) \
        .limit(MAX_HOT_IN_REPORT) \
        .execute()
    for row in hot_rows.data or []:
        name = (row.get("contact_name") or "").strip() or format_phone(row.get("chat_id") or "")
        data["hot_list"].append({
            "name": name or "Клиент",
            "city": city_names.get(row.get("city_id"), ""),
            "reason": row.get("hot_reason") or "",
        })

    # По городам: новые и горячие за сутки
    if len(cities) > 1:
        rows = supabase.table("dialogs") \
            .select("city_id, created_at, hot_at") \
            .eq("bot_id", bot_id) \
            .gte("last_message_at", since_iso) \
            .limit(1000) \
            .execute()
        since_time = pipeline.parse_time(since_iso)
        for city in cities:
            new_count = 0
            hot_count = 0
            for row in rows.data or []:
                if row.get("city_id") != city["id"]:
                    continue
                if row.get("created_at") and pipeline.parse_time(row["created_at"]) >= since_time:
                    new_count += 1
                if row.get("hot_at") and pipeline.parse_time(row["hot_at"]) >= since_time:
                    hot_count += 1
            if new_count or hot_count:
                data["cities"].append({"name": city.get("name") or "", "new": new_count, "hot": hot_count})
    return data


def claim_report(bot: dict, today: str) -> bool:
    """
    Записывает дату отправки, если сегодня сводку ещё никто не отправил.
    True — этот проход «забрал» отправку (защита от дубля).
    """
    previous = bot.get("daily_report_last_date")
    query = supabase.table("bots").update({"daily_report_last_date": today}).eq("id", bot["id"])
    if previous:
        query = query.eq("daily_report_last_date", previous)
    else:
        query = query.is_("daily_report_last_date", "null")
    result = query.execute()
    return bool(result.data)


async def send_daily_report(bot: dict, local_now: datetime) -> bool:
    """Собирает и отправляет сводку одного бота. True — доставлено."""
    data = collect_report_data(bot, period_start_utc_iso(local_now))
    text = build_report_text(bot, local_now, data)
    return await send_notification(bot.get("telegram_notify_chat_id"), text)


async def check_daily_reports_once(now: datetime | None = None) -> None:
    """Один проход: отправляет сводки ботам, у которых наступило время."""
    local_now = to_local(now)
    result = supabase.table("bots").select("*").eq("daily_report_enabled", True).execute()
    for bot in result.data or []:
        try:
            if not is_report_due(bot, local_now):
                continue
            if not claim_report(bot, local_now.date().isoformat()):
                continue
            sent = await send_daily_report(bot, local_now)
            print(f"REPORT: сводка дня бота {bot['id']} {'отправлена' if sent else 'НЕ доставлена'}")
        except Exception as error:
            print(f"REPORT ERROR: бот {bot.get('id')}: {error}")
