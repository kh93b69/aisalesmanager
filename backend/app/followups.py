"""
Дожимы: напоминания клиенту, который замолчал после ответа бота.

Владелец включает дожимы в настройках и задаёт до 3 шагов: через сколько часов после
предыдущего сообщения написать и что именно (пустой текст — ИИ напишет сам по контексту).

Раз в минуту (из планировщика) ищем диалоги, где:
- дожимы у бота включены;
- статус «новый» или «в работе» (горячими занимается сотрудник), ИИ не на паузе,
  не стоит «нужно внимание»;
- последнее сообщение — от бота (клиент не ответил);
- прошло нужное число часов и сейчас дневное время (9:00–21:00 по TIMEZONE).
Когда клиент отвечает, счётчик дожимов сбрасывается (см. pipeline.handle_client_message).
"""
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.database import supabase
from app.config import TIMEZONE
from app import pipeline
from app.messenger import send_to_client

# Максимум шагов дожима и их параметры (те же ограничения проверяются при сохранении настроек)
MAX_FOLLOWUP_STEPS = 3
MIN_FOLLOWUP_HOURS = 1
MAX_FOLLOWUP_HOURS = 168
MAX_FOLLOWUP_TEXT = 1000

# Писать клиентам только днём (по местному времени)
WORK_START_HOUR = 9
WORK_END_HOUR = 21

# Не дожимаем диалоги, где последнее сообщение старше недели
MAX_DIALOG_AGE_DAYS = 8

# Сколько диалогов одного бота обрабатываем за проход
DIALOGS_PER_PASS = 50


def clean_followup_steps(raw_steps) -> list[dict]:
    """Шаги дожима из настроек бота: [{"hours": int, "text": str}], только корректные."""
    steps = []
    if not isinstance(raw_steps, list):
        return steps
    for step in raw_steps[:MAX_FOLLOWUP_STEPS]:
        if not isinstance(step, dict):
            continue
        hours = step.get("hours")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)):
            continue
        hours = int(hours)
        if hours < MIN_FOLLOWUP_HOURS or hours > MAX_FOLLOWUP_HOURS:
            continue
        text = step.get("text") if isinstance(step.get("text"), str) else ""
        steps.append({"hours": hours, "text": text.strip()[:MAX_FOLLOWUP_TEXT]})
    return steps


def is_work_time(now: datetime) -> bool:
    """Дневное ли сейчас время по местному часовому поясу (клиентам ночью не пишем)."""
    local = now.astimezone(ZoneInfo(TIMEZONE))
    return WORK_START_HOUR <= local.hour < WORK_END_HOUR


def is_followup_due(dialog: dict, step: dict, now: datetime) -> bool:
    """Прошло ли нужное число часов с последнего сообщения диалога."""
    last_at = dialog.get("last_message_at")
    if not last_at:
        return False
    try:
        last_time = pipeline.parse_time(last_at)
    except (ValueError, TypeError):
        return False
    return now - last_time >= timedelta(hours=step["hours"])


def fetch_bots_with_followups() -> list[dict]:
    """Боты, у которых включены дожимы."""
    result = supabase.table("bots").select("*").eq("followups_enabled", True).execute()
    return result.data or []


def fetch_candidate_dialogs(bot_id: str, max_steps: int, min_hours: int, now: datetime) -> list[dict]:
    """Диалоги бота, которым, возможно, пора отправить дожим (точную проверку делаем потом)."""
    latest = (now - timedelta(hours=min_hours)).isoformat()
    oldest = (now - timedelta(days=MAX_DIALOG_AGE_DAYS)).isoformat()
    result = supabase.table("dialogs") \
        .select("*") \
        .eq("bot_id", bot_id) \
        .in_("status", ["new", "in_progress"]) \
        .eq("ai_disabled", False) \
        .eq("needs_attention", False) \
        .lt("followup_count", max_steps) \
        .lte("last_message_at", latest) \
        .gte("last_message_at", oldest) \
        .order("last_message_at") \
        .limit(DIALOGS_PER_PASS) \
        .execute()
    return result.data or []


def claim_followup(dialog: dict) -> bool:
    """
    Условно увеличивает счётчик дожимов (если его никто не поменял) — так один и тот же
    дожим не уйдёт дважды. True — этот проход «забрал» дожим.
    """
    old_count = dialog.get("followup_count") or 0
    updated = supabase.table("dialogs") \
        .update({"followup_count": old_count + 1, "last_followup_at": pipeline.utc_now_iso()}) \
        .eq("id", dialog["id"]) \
        .eq("followup_count", old_count) \
        .execute()
    return bool(updated.data)


async def build_followup_text(bot: dict, dialog: dict, messages: list[dict], step: dict) -> str:
    """
    Текст дожима: готовый текст из настроек или ответ ИИ по контексту диалога.
    "" — не отправлять (ИИ не ответил).
    """
    if step["text"]:
        return step["text"]

    history = pipeline.build_history(messages)
    if not history:
        return ""
    # Служебная пометка вместо сообщения клиента: ИИ напишет напоминание (см. правила в prompts.py)
    history.append({"role": "user", "content": f"[Клиент не отвечает {step['hours']} ч]"})

    cities = pipeline.get_bot_cities(bot["id"])
    city = pipeline.find_city_by_id(cities, dialog.get("city_id"))
    result = await pipeline.build_and_generate(bot, dialog, cities, city, history,
                                               pipeline.build_kb_query(messages))
    if not result.get("ok"):
        # Вместо дожима ушла бы заглушка «Спасибо за сообщение!» — лучше промолчать
        return ""
    return pipeline.strip_staff_prefix(result.get("reply") or "")


async def send_followup(bot: dict, dialog: dict, step: dict) -> None:
    """Отправляет один дожим (под замком диалога, чтобы не столкнуться с обычным ответом)."""
    lock = pipeline._dialog_locks.setdefault(dialog["id"], asyncio.Lock())
    async with lock:
        messages = pipeline.get_last_messages(dialog["id"])
        # Клиент мог ответить или сотрудник мог написать — тогда дожим не нужен
        if not messages or messages[-1].get("role") != "assistant":
            return
        if not claim_followup(dialog):
            return

        text = await build_followup_text(bot, dialog, messages, step)
        if not text:
            print(f"FOLLOWUP: дожим не сформирован, диалог {dialog['id']}")
            return

        # Пока ИИ писал текст, клиент мог ответить или сотрудник — забрать диалог
        fresh = pipeline.get_dialog(dialog["id"])
        if not fresh or fresh.get("ai_disabled") or fresh.get("status") not in ("new", "in_progress"):
            return
        latest = pipeline.get_last_messages(dialog["id"])
        if not latest or latest[-1].get("role") != "assistant":
            return

        send_result = await send_to_client(bot, fresh, text, from_bot=True)
        send_error = None
        if not send_result.get("ok"):
            send_error = send_result.get("error") or "неизвестная ошибка"
        pipeline.save_message(dialog["id"], "assistant", text, send_error=send_error,
                              external_id=send_result.get("message_id") or None)
        print(f"FOLLOWUP: дожим №{(dialog.get('followup_count') or 0) + 1} отправлен, диалог {dialog['id']}")


async def check_followups_once(now: datetime | None = None) -> None:
    """Один проход по всем ботам с включёнными дожимами."""
    if now is None:
        now = datetime.now(timezone.utc)
    if not is_work_time(now):
        return

    for bot in fetch_bots_with_followups():
        steps = clean_followup_steps(bot.get("followups"))
        if not steps:
            continue
        min_hours = min(step["hours"] for step in steps)
        try:
            dialogs = fetch_candidate_dialogs(bot["id"], len(steps), min_hours, now)
        except Exception as error:
            print(f"FOLLOWUP ERROR: бот {bot.get('id')}: {error}")
            continue
        for dialog in dialogs:
            try:
                index = dialog.get("followup_count") or 0
                if index >= len(steps):
                    continue
                step = steps[index]
                if not is_followup_due(dialog, step, now):
                    continue
                await send_followup(bot, dialog, step)
            except Exception as error:
                print(f"FOLLOWUP ERROR: диалог {dialog.get('id')}: {error}")
