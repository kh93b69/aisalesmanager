"""
График работы бота (Настройки → «🕘 График работы»).

Если график включён, бот пишет клиентам ТОЛЬКО в рабочее время:
- ответы на сообщения, дожимы, ссылка на оплату — всё проверяется здесь (is_bot_working);
- клиент написал в нерабочее время → бот молчит (или один раз отправляет автоответ «мы ответим утром»),
  а когда начинается рабочее время — отвечает (см. pipeline._reply_once и check_off_hours_once).

Время — местное, по часовому поясу TIMEZONE (по умолчанию Asia/Almaty, UTC+5).
График хранится в bots.work_schedule — 7 дней, с понедельника:
    [{"enabled": true, "start": "09:00", "end": "21:00"}, …]
Если конец раньше начала (например, 20:00–08:00) — смена идёт через полночь и заканчивается
на следующий день. Начало = концу — весь день (круглые сутки).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import TIMEZONE

DAYS_IN_WEEK = 7
DEFAULT_START = "09:00"
DEFAULT_END = "21:00"

# Короткие названия дней недели (понедельник — первый)
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def parse_hhmm(value) -> int | None:
    """«09:30» → 570 (минут от начала суток). Неверный формат → None."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    hours = int(parts[0])
    minutes = int(parts[1])
    if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
        return None
    return hours * 60 + minutes


def default_schedule() -> list[dict]:
    """График по умолчанию: каждый день 09:00–21:00."""
    schedule = []
    for _ in range(DAYS_IN_WEEK):
        schedule.append({"enabled": True, "start": DEFAULT_START, "end": DEFAULT_END})
    return schedule


def clean_schedule(raw) -> list[dict]:
    """
    График из БД → ровно 7 корректных дней. Неверный день считаем выходным
    (лучше промолчать, чем написать клиенту ночью).
    """
    schedule = []
    for index in range(DAYS_IN_WEEK):
        day = None
        if isinstance(raw, list) and index < len(raw) and isinstance(raw[index], dict):
            day = raw[index]
        if day is None:
            schedule.append({"enabled": False, "start": DEFAULT_START, "end": DEFAULT_END})
            continue
        start = day.get("start")
        end = day.get("end")
        enabled = day.get("enabled") is True
        if parse_hhmm(start) is None or parse_hhmm(end) is None:
            enabled = False
            start = DEFAULT_START
            end = DEFAULT_END
        schedule.append({"enabled": enabled, "start": start.strip(), "end": end.strip()})
    return schedule


def to_local(now: datetime | None = None) -> datetime:
    """Текущее (или переданное) время в часовом поясе TIMEZONE."""
    if now is None:
        now = datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(TIMEZONE))


def is_time_in_schedule(schedule: list[dict], local: datetime) -> bool:
    """Попадает ли местное время в график (с учётом смен через полночь)."""
    minute_of_day = local.hour * 60 + local.minute
    today = schedule[local.weekday()]
    yesterday = schedule[(local.weekday() - 1) % DAYS_IN_WEEK]

    # Смена, которая началась сегодня
    if today["enabled"]:
        start = parse_hhmm(today["start"])
        end = parse_hhmm(today["end"])
        if start == end:
            return True
        if start < end and start <= minute_of_day < end:
            return True
        if start > end and minute_of_day >= start:
            return True

    # Хвост вчерашней ночной смены (например, вчера 20:00 – сегодня 08:00)
    if yesterday["enabled"]:
        start = parse_hhmm(yesterday["start"])
        end = parse_hhmm(yesterday["end"])
        if start > end and minute_of_day < end:
            return True
    return False


def is_bot_working(bot: dict, now: datetime | None = None) -> bool:
    """Можно ли боту сейчас писать клиентам. График выключен — работает круглосуточно."""
    if not (bot or {}).get("work_hours_enabled"):
        return True
    schedule = clean_schedule(bot.get("work_schedule"))
    return is_time_in_schedule(schedule, to_local(now))


def next_work_start(bot: dict, now: datetime | None = None) -> datetime | None:
    """
    Когда бот начнёт работать (местное время). None — работает сейчас
    или в графике нет ни одного рабочего дня.
    """
    if is_bot_working(bot, now):
        return None
    schedule = clean_schedule(bot.get("work_schedule"))
    local = to_local(now)
    for offset in range(DAYS_IN_WEEK + 1):
        day_date = (local + timedelta(days=offset)).date()
        day = schedule[day_date.weekday()]
        if not day["enabled"]:
            continue
        start = parse_hhmm(day["start"])
        start_time = datetime(day_date.year, day_date.month, day_date.day,
                              start // 60, start % 60, tzinfo=local.tzinfo)
        if start_time > local:
            return start_time
    return None


def format_local(moment: datetime) -> str:
    """«вт 12.11 14:05» — короткая запись местного времени."""
    return f"{WEEKDAYS_SHORT[moment.weekday()]} {moment.strftime('%d.%m %H:%M')}"
