"""
Планировщик напоминаний о счёте.

Раз в минуту смотрим горячие диалоги (status='hot'). Если сотрудник не ответил клиенту:
- через N минут после hot_at — напоминание ответственным;
- через 2N минут — эскалация руководителю.
Больше не напоминаем.

Важно: рассчитано на ОДИН инстанс сервера (при нескольких инстансах защищает только
условное обновление reminder_count — уведомление не уйдёт дважды, но опрос будет дублироваться).
"""
import asyncio
from datetime import datetime, timezone

from app.database import supabase
from app.notifier import notify_reminder

# Размер страницы при выборке диалогов (PostgREST отдаёт не больше 1000 строк)
PAGE_SIZE = 500

# Интервал между проверками, секунд
CHECK_INTERVAL_SECONDS = 60

# Минуты до напоминания, если у бота не задано
DEFAULT_REMINDER_MINUTES = 10
# Сколько минут после 2N ещё пытаемся повторить неотправленное напоминание
RETRY_EXTRA_MINUTES = 30


def parse_time(value) -> datetime | None:
    """Разбирает время ISO из БД. None, если пусто или не разобралось."""
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def decide_reminder(reminder_count: int, minutes_passed: float, n: int, manager_replied: bool) -> str:
    """
    Что делать с горячим диалогом:
    "none" — ничего, "reminder" — первое напоминание, "escalation" — эскалация руководителю.
    """
    if manager_replied:
        return "none"
    if not n or n <= 0:
        n = DEFAULT_REMINDER_MINUTES
    if reminder_count == 0 and minutes_passed >= n:
        return "reminder"
    if reminder_count == 1 and minutes_passed >= 2 * n:
        return "escalation"
    return "none"


def manager_replied_after_hot(dialog: dict) -> bool:
    """Сотрудник написал клиенту после того, как диалог стал горячим."""
    hot_at = parse_time(dialog.get("hot_at"))
    manager_at = parse_time(dialog.get("last_manager_message_at"))
    if hot_at is None or manager_at is None:
        return False
    return manager_at > hot_at


def fetch_hot_dialogs() -> list[dict]:
    """Все горячие диалоги, постранично."""
    dialogs = []
    start = 0
    while True:
        result = supabase.table("dialogs") \
            .select("*") \
            .eq("status", "hot") \
            .order("id") \
            .range(start, start + PAGE_SIZE - 1) \
            .execute()
        page = result.data or []
        dialogs.extend(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return dialogs


def load_bot(bot_id: str, cache: dict) -> dict | None:
    """Бот с кэшем на один проход."""
    if bot_id not in cache:
        result = supabase.table("bots").select("*").eq("id", bot_id).limit(1).execute()
        cache[bot_id] = result.data[0] if result.data else None
    return cache[bot_id]


def load_team(bot_id: str, cache: dict) -> list[dict]:
    """Команда бота с кэшем на один проход."""
    if bot_id not in cache:
        result = supabase.table("team_members").select("*").eq("bot_id", bot_id).execute()
        cache[bot_id] = result.data or []
    return cache[bot_id]


def load_city(city_id: str | None, cache: dict) -> dict | None:
    """Город с кэшем на один проход."""
    if not city_id:
        return None
    if city_id not in cache:
        result = supabase.table("cities").select("*").eq("id", city_id).limit(1).execute()
        cache[city_id] = result.data[0] if result.data else None
    return cache[city_id]


async def check_hot_dialogs_once() -> None:
    """Один проход по горячим диалогам."""
    bots_cache = {}
    teams_cache = {}
    cities_cache = {}
    now = datetime.now(timezone.utc)

    for dialog in fetch_hot_dialogs():
        try:
            hot_at = parse_time(dialog.get("hot_at"))
            if hot_at is None:
                continue
            bot = load_bot(dialog["bot_id"], bots_cache)
            if not bot:
                continue

            n = bot.get("invoice_reminder_minutes") or DEFAULT_REMINDER_MINUTES
            minutes_passed = (now - hot_at).total_seconds() / 60
            old_count = dialog.get("reminder_count") or 0
            action = decide_reminder(old_count, minutes_passed, n, manager_replied_after_hot(dialog))
            if action == "none":
                continue

            # Условное обновление: если кто-то уже обновил счётчик — не отправляем повторно
            updated = supabase.table("dialogs") \
                .update({"reminder_count": old_count + 1}) \
                .eq("id", dialog["id"]) \
                .eq("reminder_count", old_count) \
                .execute()
            if not updated.data:
                continue

            escalation = action == "escalation"
            # Реальное время ожидания (в минутах) — его и пишем в уведомлении
            minutes = int(minutes_passed)
            city = load_city(dialog.get("city_id"), cities_cache)
            team = load_team(dialog["bot_id"], teams_cache)
            print(f"SCHEDULER: напоминание о счёте dialog={dialog['id']} escalation={escalation}")
            sent = await notify_reminder(bot, dialog, city, team, minutes, escalation)
            # False — ни одна отправка не удалась: откатываем счётчик, повторим на следующем проходе.
            # Но повторяем только в разумном окне (до 2N + 30 мин от «горячего»): если Telegram
            # не настроен вообще, не долбим БД вечно и не шлём пачку старых напоминаний потом.
            # None (старый notifier без результата) — считаем отправленным.
            retry_window_minutes = 2 * n + RETRY_EXTRA_MINUTES
            if sent is False and minutes_passed <= retry_window_minutes:
                print(f"SCHEDULER: напоминание не отправлено dialog={dialog['id']} — повторим")
                supabase.table("dialogs") \
                    .update({"reminder_count": old_count}) \
                    .eq("id", dialog["id"]) \
                    .eq("reminder_count", old_count + 1) \
                    .execute()
        except Exception as error:
            print(f"SCHEDULER ERROR: диалог {dialog.get('id')}: {error}")


async def run_scheduler_forever() -> None:
    """Бесконечный цикл планировщика (запускается из lifespan в main.py)."""
    print("SCHEDULER: запущен")
    while True:
        try:
            await check_hot_dialogs_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"SCHEDULER ERROR: {error}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
