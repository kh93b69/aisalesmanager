"""
График работы бота, ссылка на оплату, сводка дня и несколько номеров Wazzup — без сети.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient

import tests._stubs  # noqa: F401 — фиктивные ключи Supabase
from app import work_hours, pipeline, prompts, ai_engine, daily_report, followups
from app.routes import settings as settings_routes
from app.routes import webhooks
from tests.test_pipeline import make_env, ai, merged

ALMATY = ZoneInfo("Asia/Almaty")


def local(year, month, day, hour, minute=0):
    """Местное время Алматы → aware datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=ALMATY)


def week(start="09:00", end="18:00", days_off=()):
    """График: все дни start–end, кроме days_off (0 = пн)."""
    schedule = []
    for index in range(7):
        schedule.append({"enabled": index not in days_off, "start": start, "end": end})
    return schedule


@pytest.fixture(autouse=True)
def almaty_timezone(monkeypatch):
    monkeypatch.setattr(work_hours, "TIMEZONE", "Asia/Almaty")


# ---------------- график ----------------

def test_parse_hhmm():
    assert work_hours.parse_hhmm("09:30") == 570
    assert work_hours.parse_hhmm("00:00") == 0
    assert work_hours.parse_hhmm("23:59") == 1439
    for bad in ["24:00", "9-30", "", None, "ab:cd", "10:60", 930]:
        assert work_hours.parse_hhmm(bad) is None


def test_almaty_is_utc_plus_5():
    # С 1 марта 2024 Казахстан живёт по UTC+5 — проверяем, что база часовых поясов свежая
    moment = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
    assert work_hours.to_local(moment).hour == 9


def test_day_schedule():
    schedule = week("09:00", "18:00", days_off=(6,))
    # 12.11.2026 — четверг
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 12, 9, 0)) is True
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 12, 17, 59)) is True
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 12, 18, 0)) is False
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 12, 8, 59)) is False
    # 15.11.2026 — воскресенье, выходной
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 15, 12, 0)) is False


def test_night_shift_through_midnight():
    # Бот работает ночью 20:00–08:00 только в пятницу (индекс 4)
    schedule = week("20:00", "08:00", days_off=(0, 1, 2, 3, 5, 6))
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 13, 21, 0)) is True   # пт 21:00
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 14, 7, 59)) is True   # сб 07:59 — хвост пятницы
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 14, 8, 0)) is False
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 13, 7, 0)) is False   # пт утро — четверг выходной
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 14, 21, 0)) is False  # сб вечер — выходной


def test_full_day_when_start_equals_end():
    schedule = week("00:00", "00:00")
    assert work_hours.is_time_in_schedule(schedule, local(2026, 11, 12, 3, 0)) is True


def test_is_bot_working_and_next_start():
    bot = {"work_hours_enabled": True, "work_schedule": week("09:00", "18:00", days_off=(5, 6))}
    friday_evening = local(2026, 11, 13, 19, 0)
    assert work_hours.is_bot_working(bot, friday_evening) is False
    next_start = work_hours.next_work_start(bot, friday_evening)
    assert next_start.weekday() == 0 and next_start.hour == 9  # понедельник 09:00
    assert work_hours.format_local(next_start) == "пн 16.11 09:00"
    # График выключен — бот работает всегда
    assert work_hours.is_bot_working({"work_hours_enabled": False, "work_schedule": []}, friday_evening) is True
    assert work_hours.next_work_start({}, friday_evening) is None


def test_clean_schedule_broken_days_are_off():
    schedule = work_hours.clean_schedule([{"enabled": True, "start": "9", "end": "18:00"}, "мусор"])
    assert len(schedule) == 7
    for day in schedule:
        assert day["enabled"] is False


# ---------------- сообщения-автоответы ----------------

def test_service_messages_are_not_replies():
    auto = {"id": "a", "role": "assistant", "content": "Мы откроемся в 9:00", "author_name": pipeline.OFF_HOURS_AUTHOR}
    messages = [{"id": "u1", "role": "user", "content": "Привет"}, auto]
    assert pipeline.has_unanswered_user(messages) is True
    assert pipeline.is_first_reply(messages) is True
    assert pipeline.build_history(messages) == [{"role": "user", "content": "Привет"}]
    assert pipeline.last_user_text(messages + [{"id": "u2", "role": "user", "content": "Цена?"}]) == "Привет\nЦена?"
    assert pipeline.last_user_message_id(messages) == "u1"


# ---------------- ответы в нерабочее время ----------------

CLOSED_BOT = {"work_hours_enabled": True, "work_schedule": week(days_off=(0, 1, 2, 3, 4, 5, 6)),
              "off_hours_message": "Мы сейчас не работаем, ответим утром 🙏"}


def test_off_hours_no_ai_and_one_auto_reply(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai()], bot_extra=CLOSED_BOT)

    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["generate"] == []  # ИИ не вызывали
    assert calls["send"] == ["Мы сейчас не работаем, ответим утром 🙏"]
    assert calls["saved"][0][2]["author_name"] == pipeline.OFF_HOURS_AUTHOR
    assert merged(calls["updates"]).get("off_hours_since")

    # Клиент дописал ночью — второй автоответ не уходит
    calls["messages"].append({"id": "m2", "role": "user", "content": "Ау?", "author_name": None})
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert len(calls["send"]) == 1
    assert calls["generate"] == []


def test_work_time_starts_bot_answers_and_clears_flag(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": "c1", "ai_disabled": False,
              "off_hours_since": "2026-11-12T18:00:00+00:00"}
    messages = [
        {"id": "m1", "role": "user", "content": "Сколько стоит?"},
        {"id": "a1", "role": "assistant", "content": "Мы откроемся в 9:00", "author_name": pipeline.OFF_HOURS_AUTHOR},
    ]
    calls = make_env(monkeypatch, dialog, [ai(reply="Доброе утро! Стоит 5000 ₸")], messages=messages)

    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["send"] == ["Доброе утро! Стоит 5000 ₸"]
    assert merged(calls["updates"])["off_hours_since"] is None
    # ИИ видит только сообщение клиента, без автоответа
    assert calls["generate"][0] == [{"role": "user", "content": "Сколько стоит?"}]


def test_work_time_ends_during_generation(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(reply="Ответ")])
    answers = iter([True, False])
    monkeypatch.setattr(pipeline, "is_bot_working", lambda bot, now=None: next(answers))

    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert len(calls["generate"]) == 1
    assert calls["send"] == []  # рабочее время закончилось, пока ИИ думал
    assert merged(calls["updates"]).get("off_hours_since")


def test_answer_after_off_hours_once(monkeypatch):
    monkeypatch.setattr(pipeline, "_off_hours_task", None)
    waiting = [{"id": "d1", "bot_id": "open"}, {"id": "d2", "bot_id": "closed"}]
    monkeypatch.setattr(pipeline, "find_working_bot_ids", lambda: ["open"])
    monkeypatch.setattr(pipeline, "find_waiting_off_hours_dialogs",
                        lambda bot_ids: [d for d in waiting if d["bot_id"] in bot_ids])
    answered = []

    async def fake_process(bot_id, dialog_id):
        answered.append(dialog_id)

    monkeypatch.setattr(pipeline, "process_dialog_reply", fake_process)

    async def run():
        await pipeline.answer_after_off_hours_once()
        await pipeline._off_hours_task

    asyncio.run(run())
    assert answered == ["d1"]


def test_followups_respect_schedule():
    now = datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc)  # 10:00 в Алматы
    assert followups.can_send_followup({}, now) is True
    early = datetime(2026, 11, 12, 4, 30, tzinfo=timezone.utc)  # 09:30 — без графика ещё рано
    assert followups.can_send_followup({}, early) is False
    assert followups.can_send_followup(CLOSED_BOT, now) is False
    night_bot = {"work_hours_enabled": True, "work_schedule": week("22:00", "06:00")}
    night = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)  # 23:00 в Алматы
    assert followups.can_send_followup({}, night) is False
    # Даже ночной график не даёт писать дожимы ночью
    assert followups.can_send_followup(night_bot, night) is False


# ---------------- оплата ----------------

PAY_BOT = {"payment_enabled": True, "payment_link": "https://pay.kaspi.kz/pay/abc",
           "payment_details": "Kaspi Gold: +7 701 000 00 00, Айгерим К."}


def test_payment_message_and_prompt_section():
    message = prompts.build_payment_message(PAY_BOT)
    assert "https://pay.kaspi.kz/pay/abc" in message and "Kaspi Gold" in message
    assert prompts.build_payment_message(dict(PAY_BOT, payment_enabled=False)) == ""
    assert prompts.build_payment_message({"payment_enabled": True}) == ""

    with_payment = prompts.build_system_blocks(PAY_BOT, [], None, "база", [])[0]["text"]
    assert "ОПЛАТА" in with_payment and "send_payment=true" in with_payment
    # Саму ссылку ИИ не видит — её добавляет система
    assert "pay.kaspi.kz" not in with_payment
    without = prompts.build_system_blocks({}, [], None, "база", [])[0]["text"]
    assert "ОПЛАТА (" not in without


def test_schema_has_send_payment():
    schema = ai_engine.build_reply_schema([])
    assert "send_payment" in schema["required"]
    assert ai_engine.make_fallback("x")["send_payment"] is False


def test_reply_with_payment_link(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog,
                     [ai(reply="Итого: диван «Бриз», 250 000 ₸.", lead_status="hot", lead_summary="Диван Бриз",
                         send_payment=True)],
                     bot_extra=PAY_BOT)
    payments = []

    async def fake_payment(bot, dialog_arg, city, team, summary):
        payments.append(summary)
        return True

    monkeypatch.setattr(pipeline, "notify_payment_sent", fake_payment)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))

    sent = calls["send"][0]
    assert sent.startswith("Итого: диван «Бриз», 250 000 ₸.")
    assert "https://pay.kaspi.kz/pay/abc" in sent
    fields = merged(calls["updates"])
    assert fields["status"] == "invoice_sent"
    assert fields["invoice_sent_at"] and fields["hot_at"]
    assert payments == ["Диван Бриз"]
    assert calls["hot"] == []  # вместо «нужен счёт» — «ссылка отправлена»


def test_payment_ignored_when_not_configured(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(reply="Оформляем!", lead_status="hot", send_payment=True)])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["send"] == ["Оформляем!"]
    assert merged(calls["updates"])["status"] == "hot"
    assert len(calls["hot"]) == 1


def test_payment_not_marked_when_send_failed(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(reply="Итого", lead_status="hot", send_payment=True)],
                     bot_extra=PAY_BOT, send_ok=False)
    monkeypatch.setattr(pipeline, "notify_payment_sent", lambda *a: None)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert merged(calls["updates"])["status"] == "hot"


def test_state_note_invoice_and_off_hours():
    note = prompts.build_state_note({"status": "invoice_sent", "off_hours_since": "x"}, None, "сейчас", has_cities=False)
    assert "ссылка на оплату" in note and "подождали" in note


# ---------------- настройки ----------------

def test_validate_schedule_and_payment():
    clean = settings_routes.validate_bot_update({
        "work_hours_enabled": True,
        "work_schedule": week("9:05", "18:00"),
        "off_hours_message": "  Ответим утром  ",
        "payment_enabled": True,
        "payment_link": " https://pay.kaspi.kz/pay/abc ",
        "payment_details": "",
        "daily_report_enabled": True,
        "daily_report_time": "8:30",
    })
    assert clean["work_schedule"][0] == {"enabled": True, "start": "09:05", "end": "18:00"}
    assert clean["off_hours_message"] == "Ответим утром"
    assert clean["payment_link"] == "https://pay.kaspi.kz/pay/abc"
    assert clean["daily_report_time"] == "08:30"

    bad_inputs = [
        {"work_schedule": week()[:6]},
        {"work_schedule": week("9", "18:00")},
        {"work_hours_enabled": True, "work_schedule": week(days_off=range(7))},
        {"work_hours_enabled": "да"},
        {"payment_link": "pay.kaspi.kz"},
        {"payment_enabled": True, "payment_link": "", "payment_details": ""},
        {"daily_report_time": "25:00"},
    ]
    for data in bad_inputs:
        with pytest.raises(HTTPException):
            settings_routes.validate_bot_update(data)


# ---------------- сводка дня ----------------

def test_report_due():
    bot = {"daily_report_enabled": True, "telegram_notify_chat_id": "123", "daily_report_time": "20:00"}
    assert daily_report.is_report_due(bot, local(2026, 11, 12, 19, 59)) is False
    assert daily_report.is_report_due(bot, local(2026, 11, 12, 20, 0)) is True
    assert daily_report.is_report_due(dict(bot, daily_report_last_date="2026-11-12"), local(2026, 11, 12, 21, 0)) is False
    assert daily_report.is_report_due(dict(bot, daily_report_last_date="2026-11-11"), local(2026, 11, 12, 21, 0)) is True
    assert daily_report.is_report_due(dict(bot, telegram_notify_chat_id=""), local(2026, 11, 12, 21, 0)) is False
    assert daily_report.is_report_due(dict(bot, daily_report_enabled=False), local(2026, 11, 12, 21, 0)) is False


def test_period_start_is_24_hours_back():
    # 12.11 20:00 в Алматы = 15:00 UTC → начало периода 11.11 15:00 UTC
    assert daily_report.period_start_utc_iso(local(2026, 11, 12, 20, 0)) == "2026-11-11T15:00:00+00:00"


def report_data(**kwargs):
    data = {"new": 10, "active": 14, "hot": 3, "hot_from_new": 2, "invoice": 2, "paid": 1, "followups": 4,
            "hot_waiting": 1, "attention": 2, "questions_open": 3, "questions_new": 1,
            "hot_list": [{"name": "<Айгерим>", "city": "Алматы", "reason": "Диван «Бриз»"}],
            "cities": [{"name": "Алматы", "new": 6, "hot": 2}, {"name": "Астана", "new": 4, "hot": 1}]}
    data.update(kwargs)
    return data


def test_report_text():
    text = daily_report.build_report_text({"company_name": "Мебель Плюс"}, local(2026, 11, 12, 20, 0), report_data())
    assert "Сводка дня — Мебель Плюс" in text
    assert "чт, 12 ноября" in text
    assert "Новых обращений: <b>10</b>" in text
    assert "из новых — 20%" in text
    assert "Горячие ждут счёт: <b>1</b>" in text
    assert "Вопросов без ответа: <b>3</b>" in text
    assert "&lt;Айгерим&gt;" in text  # имя клиента экранировано
    assert "Астана: 4 / 1" in text

    quiet = daily_report.build_report_text({"name": "Бот"}, local(2026, 11, 12, 20, 0), report_data(
        new=0, active=0, hot_waiting=0, attention=0, questions_open=0, hot_list=[], cities=[]))
    assert "За сутки клиенты не писали" in quiet
    assert "Всё разобрано" in quiet


# ---------------- несколько номеров Wazzup ----------------

def test_webhook_routes_messages_to_bot_of_number(monkeypatch):
    bot_a = {"id": "b1", "wazzup_webhook_secret": "s3cret", "wazzup_channel_id": "ch1", "wazzup_api_key": "k"}
    bot_b = {"id": "b2", "wazzup_webhook_secret": "other", "wazzup_channel_id": "ch2", "wazzup_api_key": "k"}
    started = []

    def fake_start(func, *args, **kwargs):
        started.append((func.__name__, args[0]["id"], kwargs.get("external_id")))

    monkeypatch.setattr(webhooks, "start_background", fake_start)
    monkeypatch.setattr(webhooks, "load_bot", lambda bot_id: dict(bot_a))
    monkeypatch.setattr(webhooks, "load_account_bots", lambda bot: [bot, dict(bot_b)])
    app = FastAPI()
    app.include_router(webhooks.router)
    client = TestClient(app)

    def message(message_id, channel_id):
        return {"messageId": message_id, "channelId": channel_id, "chatType": "whatsapp", "chatId": "7701",
                "type": "text", "status": "inbound", "text": "Привет", "isEcho": False}

    body = {"messages": [message("m1", "ch1"), message("m2", "ch2"), message("m3", "ch3")],
            "statuses": [{"messageId": "x", "status": "error", "error": "BAD"}]}
    response = client.post("/webhook/wazzup/b1?token=s3cret", json=body)
    assert response.json() == {"status": "ok"}
    assert ("handle_client_message", "b1", "m1") in started
    assert ("handle_client_message", "b2", "m2") in started
    assert not any(item[2] == "m3" for item in started)
    # Ошибку доставки обрабатываем один раз
    assert [item for item in started if item[0] == "handle_delivery_error"] == [("handle_delivery_error", "b1", None)]


def test_flag_kept_when_generation_fails(monkeypatch):
    # Сбой во время ответа — отметка «ждёт» остаётся, планировщик повторит
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": "c1", "ai_disabled": False,
              "off_hours_since": "2026-11-12T18:00:00+00:00"}
    calls = make_env(monkeypatch, dialog, [ai()])

    async def broken(*args, **kwargs):
        raise RuntimeError("Supabase недоступен")

    monkeypatch.setattr(pipeline, "build_and_generate", broken)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["send"] == []
    assert not any("off_hours_since" in update for update in calls["updates"])


def test_followup_ignores_auto_reply(monkeypatch):
    # Последнее — автоответ, а перед ним вопрос клиента: дожим не отправляем
    messages = [{"id": "u1", "role": "user", "content": "Цена?"},
                {"id": "a1", "role": "assistant", "content": "Ответим утром", "author_name": pipeline.OFF_HOURS_AUTHOR}]
    monkeypatch.setattr(pipeline, "get_last_messages", lambda dialog_id, limit=30: list(messages))
    claimed = []
    monkeypatch.setattr(followups, "claim_followup", lambda dialog: claimed.append(1) or True)
    asyncio.run(followups.send_followup({"id": "b1"}, {"id": "d1"}, {"hours": 3, "text": "Актуально?"}))
    assert claimed == []


def test_final_state_checks():
    with pytest.raises(HTTPException):
        settings_routes.check_final_bot_state({"work_schedule": []}, {"work_hours_enabled": True})
    with pytest.raises(HTTPException):
        settings_routes.check_final_bot_state({"payment_link": ""}, {"payment_enabled": True})
    settings_routes.check_final_bot_state({"payment_link": "https://pay.kaspi.kz/x"}, {"payment_enabled": True})
    settings_routes.check_final_bot_state({"work_schedule": week()}, {"work_hours_enabled": True})


def test_webhook_same_channel_only_once(monkeypatch):
    bot_a = {"id": "b1", "user_id": "u", "wazzup_webhook_secret": "s3cret", "wazzup_channel_id": "ch1", "wazzup_api_key": "k"}
    bot_dup = {"id": "b2", "user_id": "u", "wazzup_webhook_secret": "x", "wazzup_channel_id": "ch1", "wazzup_api_key": "k"}
    started = []
    monkeypatch.setattr(webhooks, "start_background", lambda func, *a, **k: started.append(a[0]["id"]))
    monkeypatch.setattr(webhooks, "load_bot", lambda bot_id: dict(bot_a))
    monkeypatch.setattr(webhooks, "load_account_bots", lambda bot: [bot, dict(bot_dup)])
    app = FastAPI()
    app.include_router(webhooks.router)
    body = {"messages": [{"messageId": "m1", "channelId": "ch1", "chatType": "whatsapp", "chatId": "7701",
                          "type": "text", "status": "inbound", "text": "Привет", "isEcho": False}]}
    TestClient(app).post("/webhook/wazzup/b1?token=s3cret", json=body)
    assert started == ["b1"]


# ---------------- надёжность: сбои связи с базой ----------------

def test_reply_retried_after_database_error(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(reply="Отправляю кейсы")])
    real_get = pipeline.get_last_messages
    failures = {"left": 1}

    def flaky(dialog_id, limit=30):
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("Server disconnected")
        return real_get(dialog_id, limit)

    monkeypatch.setattr(pipeline, "get_last_messages", flaky)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["send"] == ["Отправляю кейсы"]


def test_forgotten_dialogs_answered_limited_times(monkeypatch):
    monkeypatch.setattr(pipeline, "_forgotten_tries", {})
    monkeypatch.setattr(pipeline, "find_recent_active_dialogs",
                        lambda since, until=None: [{"id": "d1", "bot_id": "b1"}, {"id": "d2", "bot_id": "b1"}])
    histories = {"d1": [{"id": "u1", "role": "user", "content": "Отправьте кейсы"}],
                 "d2": [{"id": "u2", "role": "user", "content": "?"}, {"id": "a2", "role": "assistant", "content": "Ок"}]}
    monkeypatch.setattr(pipeline, "get_last_messages", lambda dialog_id, limit=30: histories[dialog_id])
    answered = []

    async def fake_answer(dialogs):
        answered.extend(d["id"] for d in dialogs)

    monkeypatch.setattr(pipeline, "answer_waiting_dialogs", fake_answer)
    monkeypatch.setattr(pipeline, "_forgotten_task", None)

    async def run_once():
        await pipeline.answer_forgotten_dialogs_once()
        if pipeline._forgotten_task is not None:
            await pipeline._forgotten_task

    for _ in range(5):
        asyncio.run(run_once())
    assert answered == ["d1", "d1", "d1"]  # только клиент без ответа и не больше 3 попыток


class FlakyMessages:
    """Заглушка таблицы messages: первая вставка падает (обрыв связи), но запись при этом сохранилась."""

    def __init__(self, saved_on_first_try):
        self.saved_on_first_try = saved_on_first_try
        self.rows = []
        self.inserts = 0
        self.mode = None
        self.value = None

    def table(self, name):
        return self

    def insert(self, row):
        self.mode = "insert"
        self.value = row
        return self

    def select(self, *args):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.value = value
        return self

    def limit(self, value):
        return self

    def execute(self):
        if self.mode == "insert":
            self.inserts += 1
            if self.inserts == 1:
                if self.saved_on_first_try:
                    self.rows.append(dict(self.value, id="r1"))
                raise RuntimeError("Server disconnected")
            row = dict(self.value, id=f"r{self.inserts}")
            self.rows.append(row)
            return type("R", (), {"data": [row]})()
        found = [r for r in self.rows if r.get("external_id") == self.value]
        return type("R", (), {"data": found})()


def test_insert_message_retried_without_duplicate(monkeypatch):
    monkeypatch.setattr(pipeline, "SAVE_RETRY_PAUSE_SECONDS", 0)
    # Связь оборвалась, но первая вставка успела сохраниться — второй записи не будет
    fake = FlakyMessages(saved_on_first_try=True)
    monkeypatch.setattr(pipeline, "supabase", fake)
    saved = pipeline.insert_message_row({"role": "user", "content": "Привет", "external_id": "wz1"}, "wz1")
    assert saved["id"] == "r1" and fake.inserts == 1 and len(fake.rows) == 1

    # Первая вставка не сохранилась — повтор сохраняет
    fake = FlakyMessages(saved_on_first_try=False)
    monkeypatch.setattr(pipeline, "supabase", fake)
    saved = pipeline.insert_message_row({"role": "user", "content": "Привет", "external_id": "wz2"}, "wz2")
    assert saved["id"] == "r2" and len(fake.rows) == 1


def test_sent_but_not_saved_reply_not_repeated(monkeypatch):
    # Ответ ушёл клиенту, а в базу не записался — второй раз на то же сообщение не отвечаем
    monkeypatch.setattr(pipeline, "_sent_not_saved", set())
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(reply="Ответ")])
    monkeypatch.setattr(pipeline, "save_message", lambda *a, **k: None)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["send"] == ["Ответ"]



# ---------------- дожимы: никакой рассылки по старой базе ----------------

def test_followup_late_step_skipped():
    now = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)
    step = {"hours": 3, "text": ""}
    assert followups.is_followup_due({"last_message_at": (now - timedelta(hours=4)).isoformat()}, step, now) is True
    # Ответ бота был 5 дней назад — дожим «через 3 часа» уже неуместен
    assert followups.is_followup_due({"last_message_at": (now - timedelta(days=5)).isoformat()}, step, now) is False


def test_followups_need_start_time(monkeypatch):
    now = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)  # 11:00 в Алматы
    queried = []
    monkeypatch.setattr(followups, "fetch_bots_with_followups", lambda: [
        {"id": "old", "followups_enabled": True, "followups": [{"hours": 3, "text": "x"}]},
        {"id": "new", "followups_enabled": True, "followups_enabled_at": (now - timedelta(hours=2)).isoformat(),
         "followups": [{"hours": 3, "text": "x"}, {"hours": 24, "text": "y"}]},
    ])

    def fake_fetch(bot_id, max_steps, min_hours, max_hours, since, now_arg):
        queried.append((bot_id, since))
        return []

    monkeypatch.setattr(followups, "fetch_candidate_dialogs", fake_fetch)
    asyncio.run(followups.check_followups_once(now))
    # Бот без момента включения не дожимает никого; у второго — только диалоги после включения
    assert [q[0] for q in queried] == ["new"]
    assert queried[0][1] == now - timedelta(hours=2)


def test_followups_limited_per_pass(monkeypatch):
    now = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)
    bot = {"id": "b1", "followups_enabled": True, "followups_enabled_at": (now - timedelta(days=1)).isoformat(),
           "followups": [{"hours": 3, "text": "Актуально?"}]}
    monkeypatch.setattr(followups, "fetch_bots_with_followups", lambda: [bot])
    dialogs = []
    for number in range(10):
        dialogs.append({"id": f"d{number}", "followup_count": 0,
                        "last_message_at": (now - timedelta(hours=4)).isoformat()})
    monkeypatch.setattr(followups, "fetch_candidate_dialogs", lambda *a: dialogs)
    sent = []

    async def fake_send(bot_arg, dialog, step, total_steps=3):
        sent.append(dialog["id"])
        return True

    monkeypatch.setattr(followups, "send_followup", fake_send)
    asyncio.run(followups.check_followups_once(now))
    assert len(sent) == followups.MAX_SENDS_PER_PASS



# ---------------- старые и опоздавшие сообщения клиентов ----------------

def test_old_client_message_not_answered(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False,
              "off_hours_since": "2026-11-01T18:00:00+00:00"}
    old_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    messages = [{"id": "m1", "role": "user", "content": "Сколько стоит?", "created_at": old_time}]
    calls = make_env(monkeypatch, dialog, [ai(reply="Ответ")], messages=messages)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["send"] == [] and calls["generate"] == []
    fields = merged(calls["updates"])
    assert fields["needs_attention"] is True and fields["off_hours_since"] is None


def test_late_incoming_message_saved_but_not_answered(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(reply="Ответ")], messages=[])
    late = int((datetime.now(timezone.utc) - timedelta(hours=30)).timestamp())
    asyncio.run(pipeline.handle_client_message({"id": "b1"}, "telegram", "777", "Привет", external_id="tg:1", sent_at=late))
    assert calls["saved"][0][1] == "Привет"
    assert calls["send"] == [] and calls["generate"] == []
    assert merged(calls["updates"])["needs_attention"] is True


def test_parse_sent_at():
    assert pipeline.parse_sent_at("2026-11-13T09:00:00.000Z") == datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    assert pipeline.parse_sent_at(0) == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert pipeline.parse_sent_at("мусор") is None and pipeline.parse_sent_at(None) is None


def test_followup_declined_ends_chain(monkeypatch):
    messages = [{"role": "user", "content": "Спасибо, всё"}, {"role": "assistant", "content": "Всего доброго!"}]
    monkeypatch.setattr(pipeline, "get_last_messages", lambda dialog_id, limit=30: list(messages))
    monkeypatch.setattr(pipeline, "get_bot_cities", lambda bot_id: [])
    monkeypatch.setattr(followups, "claim_followup", lambda d: True)
    monkeypatch.setattr(followups, "has_open_question", lambda dialog_id: False)

    async def decline(*args, **kwargs):
        return {"reply": "", "ok": False}

    monkeypatch.setattr(pipeline, "build_and_generate", decline)
    finished = []
    monkeypatch.setattr(followups, "finish_followups", lambda dialog, total: finished.append(total))
    asyncio.run(followups.send_followup({"id": "b1"}, {"id": "d1", "followup_count": 0}, {"hours": 3, "text": ""}, 2))
    assert finished == [2]
