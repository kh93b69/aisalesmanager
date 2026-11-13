"""
Дожимы: напоминания клиенту, который замолчал после ответа бота.

Владелец включает дожимы в настройках и задаёт до 3 шагов: через сколько часов после
предыдущего сообщения написать и что именно (пустой текст — ИИ напишет сам по контексту).

Раз в минуту (из планировщика) ищем диалоги, где:
- дожимы у бота включены;
- статус «новый» или «в работе» (горячими занимается сотрудник), ИИ не на паузе,
  не стоит «нужно внимание»;
- последнее сообщение — от бота (клиент не ответил);
- бот написал клиенту ПОСЛЕ включения дожимов (старым диалогам не пишем);
- прошло нужное число часов (но не намного больше — опоздавшие дожимы не шлём);
- клиент не ждёт от нас ответа на свой вопрос (нет открытого «вопроса без ответа»);
- сейчас рабочее время бота (если график не включён — 10:00–20:00 по TIMEZONE).
За проход (раз в минуту) — не больше MAX_SENDS_PER_PASS дожимов на бота.
Когда клиент отвечает, счётчик дожимов сбрасывается (см. pipeline.handle_client_message).
"""
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.database import supabase
from app.config import TIMEZONE
from app import pipeline
from app.messenger import send_to_client
from app.work_hours import is_bot_working

# Максимум шагов дожима и их параметры (те же ограничения проверяются при сохранении настроек)
MAX_FOLLOWUP_STEPS = 3
MIN_FOLLOWUP_HOURS = 1
MAX_FOLLOWUP_HOURS = 168
MAX_FOLLOWUP_TEXT = 1000

# Если у бота не включён «График работы» — дожимы пишем только с 10:00 до 20:00 (по TIMEZONE)
WORK_START_HOUR = 10
WORK_END_HOUR = 20

# Опоздавший дожим не отправляем: если с ответа бота прошло больше, чем часы шага + это окно
# (например, сервис был выключен или ночь), шаг пропускаем — клиенту не придёт «напоминание» спустя неделю
FOLLOWUP_LATE_HOURS = 16

# Сколько диалогов одного бота проверяем за проход и сколько дожимов максимум отправляем
# за один проход (раз в минуту) — дожимы уходят понемногу, а не пачкой всем сразу
DIALOGS_PER_PASS = 200
MAX_SENDS_PER_PASS = 3


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
    """Дневное ли сейчас время (10:00–20:00) по местному часовому поясу (клиентам ночью не пишем)."""
    local = now.astimezone(ZoneInfo(TIMEZONE))
    return WORK_START_HOUR <= local.hour < WORK_END_HOUR


def can_send_followup(bot: dict, now: datetime) -> bool:
    """Можно ли сейчас писать дожимы: по графику бота, а без графика — днём (10:00–20:00)."""
    if bot.get("work_hours_enabled"):
        return is_bot_working(bot, now)
    return is_work_time(now)


def is_followup_due(dialog: dict, step: dict, now: datetime) -> bool:
    """
    Пора ли этот шаг дожима: с последнего сообщения прошло нужное число часов,
    но не слишком много (не больше часов шага + FOLLOWUP_LATE_HOURS — опоздавшие не шлём).
    """
    last_at = dialog.get("last_message_at")
    if not last_at:
        return False
    try:
        last_time = pipeline.parse_time(last_at)
    except (ValueError, TypeError):
        return False
    passed = now - last_time
    if passed < timedelta(hours=step["hours"]):
        return False
    return passed <= timedelta(hours=step["hours"] + FOLLOWUP_LATE_HOURS)


def followups_start_time(bot: dict) -> datetime | None:
    """
    С какого момента дожимы включены. Дожим получают только клиенты, которым бот написал
    ПОСЛЕ этого момента. None — момент неизвестен (не выполнена миграция 005) — тогда дожимы
    не отправляем вовсе: лучше не написать, чем написать всей базе.
    """
    value = bot.get("followups_enabled_at")
    if not value:
        return None
    try:
        return pipeline.parse_time(value)
    except (ValueError, TypeError):
        return None


def fetch_bots_with_followups() -> list[dict]:
    """Боты, у которых включены дожимы."""
    result = supabase.table("bots").select("*").eq("followups_enabled", True).execute()
    return result.data or []


def fetch_candidate_dialogs(bot_id: str, max_steps: int, min_hours: int, max_hours: int,
                            since: datetime, now: datetime) -> list[dict]:
    """
    Диалоги бота, которым, возможно, пора отправить дожим (точную проверку делаем потом):
    последнее сообщение — не раньше включения дожимов (since) и не старше самого длинного шага + окно опоздания.
    """
    latest = (now - timedelta(hours=min_hours)).isoformat()
    oldest_allowed = now - timedelta(hours=max_hours + FOLLOWUP_LATE_HOURS)
    if since > oldest_allowed:
        oldest_allowed = since
    oldest = oldest_allowed.isoformat()
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
    dialogs = []
    for dialog in result.data or []:
        # Клиент ждёт ответа после нерабочего времени — это не «замолчал», дожим не нужен
        if not dialog.get("off_hours_since"):
            dialogs.append(dialog)
    return dialogs


def has_open_question(dialog_id: str) -> bool:
    """Бот обещал клиенту уточнить вопрос у руководителя — клиент ждёт НАС, дожим неуместен."""
    try:
        result = supabase.table("unanswered_questions").select("id") \
            .eq("dialog_id", dialog_id).eq("status", "open").limit(1).execute()
        return bool(result.data)
    except Exception as error:
        print(f"FOLLOWUP ERROR: проверка вопросов без ответа: {error}")
        # Не смогли проверить — не рискуем
        return True


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


async def send_followup(bot: dict, dialog: dict, step: dict) -> bool:
    """
    Отправляет один дожим (под замком диалога, чтобы не столкнуться с обычным ответом).
    True — дожим отправлен.
    """
    lock = pipeline._dialog_locks.setdefault(dialog["id"], asyncio.Lock())
    async with lock:
        messages = pipeline.get_last_messages(dialog["id"])
        # Клиент мог ответить или сотрудник мог написать — тогда дожим не нужен
        # Последнее настоящее сообщение (автоответ нерабочего времени не считается) — от бота
        last = pipeline.last_real_message(messages)
        if not last or last.get("role") != "assistant":
            return False
        # Клиент ждёт ответа руководителя на свой вопрос — напоминать ему нечего
        if has_open_question(dialog["id"]):
            return False
        if not claim_followup(dialog):
            return False

        text = await build_followup_text(bot, dialog, messages, step)
        if not text:
            print(f"FOLLOWUP: дожим не нужен или не сформирован, диалог {dialog['id']}")
            return False

        # Пока ИИ писал текст, могло закончиться рабочее время — не пишем клиенту
        if not can_send_followup(bot, datetime.now(timezone.utc)):
            return False
        # Пока ИИ писал текст, клиент мог ответить или сотрудник — забрать диалог
        fresh = pipeline.get_dialog(dialog["id"])
        if not fresh or fresh.get("ai_disabled") or fresh.get("status") not in ("new", "in_progress"):
            return False
        latest = pipeline.last_real_message(pipeline.get_last_messages(dialog["id"]))
        if not latest or latest.get("role") != "assistant" or fresh.get("off_hours_since"):
            return False

        send_result = await send_to_client(bot, fresh, text, from_bot=True)
        send_error = None
        if not send_result.get("ok"):
            send_error = send_result.get("error") or "неизвестная ошибка"
        pipeline.save_message(dialog["id"], "assistant", text, send_error=send_error,
                              external_id=send_result.get("message_id") or None)
        local_time = pipeline.now_text()
        print(f"FOLLOWUP: дожим №{(dialog.get('followup_count') or 0) + 1} отправлен, диалог {dialog['id']}, "
              f"местное время {local_time}")
        return True


async def check_followups_once(now: datetime | None = None) -> None:
    """Один проход по всем ботам с включёнными дожимами."""
    if now is None:
        now = datetime.now(timezone.utc)

    for bot in fetch_bots_with_followups():
        if not can_send_followup(bot, now):
            continue
        since = followups_start_time(bot)
        if since is None:
            print(f"FOLLOWUP: у бота {bot.get('id')} неизвестно, когда включены дожимы "
                  "(миграция 005 или пересохраните дожимы) — не отправляем")
            continue
        steps = clean_followup_steps(bot.get("followups"))
        if not steps:
            continue
        min_hours = min(step["hours"] for step in steps)
        max_hours = max(step["hours"] for step in steps)
        try:
            dialogs = fetch_candidate_dialogs(bot["id"], len(steps), min_hours, max_hours, since, now)
        except Exception as error:
            print(f"FOLLOWUP ERROR: бот {bot.get('id')}: {error}")
            continue
        sent_count = 0
        for dialog in dialogs:
            if sent_count >= MAX_SENDS_PER_PASS:
                break
            try:
                index = dialog.get("followup_count") or 0
                if index >= len(steps):
                    continue
                step = steps[index]
                if not is_followup_due(dialog, step, now):
                    continue
                if await send_followup(bot, dialog, step):
                    sent_count += 1
            except Exception as error:
                print(f"FOLLOWUP ERROR: диалог {dialog.get('id')}: {error}")
