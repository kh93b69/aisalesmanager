"""Тесты ядра обработки (pipeline) — без сети и без реальной БД."""
import asyncio
from datetime import datetime, timezone, timedelta

from tests._stubs import ensure_all

ensure_all()

from app import pipeline  # noqa: E402
from tests._fake_supabase import FakeSupabase  # noqa: E402


# ---------------- build_history ----------------

def test_build_history_roles_and_prefix():
    messages = [
        {"role": "assistant", "content": "Здравствуйте!"},     # ведущая реплика бота — отбросить
        {"role": "manager", "content": "Добрый день"},         # тоже ведущая — отбросить
        {"role": "user", "content": "Привет"},
        {"role": "user", "content": "   "},                     # пустое — пропустить
        {"role": "assistant", "content": "Из какого вы города?"},
        {"role": "manager", "content": "Цена 5000"},
        {"role": "user", "content": "Астана"},
    ]
    history = pipeline.build_history(messages)
    assert history == [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Из какого вы города?"},
        {"role": "assistant", "content": "[Сотрудник]: Цена 5000"},
        {"role": "user", "content": "Астана"},
    ]


def test_build_history_last_30_only():
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
    history = pipeline.build_history(messages)
    assert len(history) == 30
    assert history[0]["content"] == "msg 20"
    assert history[-1]["content"] == "msg 49"


def test_build_history_empty():
    assert pipeline.build_history([]) == []
    assert pipeline.build_history([{"role": "assistant", "content": "x"}]) == []


# ---------------- now_text ----------------

def test_now_text_russian(monkeypatch):
    monkeypatch.setattr(pipeline, "TIMEZONE", "Asia/Almaty")
    # 10 ноября 2026, 09:05 UTC → в Алматы (UTC+5) 14:05, вторник
    moment = datetime(2026, 11, 10, 9, 5, tzinfo=timezone.utc)
    assert pipeline.now_text(moment) == "вторник, 10 ноября 2026, 14:05 (Asia/Almaty)"


def test_now_text_default_works():
    text = pipeline.now_text()
    assert "(" in text and "," in text


# ---------------- describe_message / last_user_text ----------------

def test_describe_message():
    assert pipeline.describe_message("text", " привет ") == "привет"
    assert pipeline.describe_message("audio", "", "хочу записаться") == "[Голосовое сообщение]: хочу записаться"
    assert pipeline.describe_message("audio", "", "") == "[Голосовое сообщение — не удалось расшифровать]"
    assert pipeline.describe_message("audio", "") == "[Голосовое сообщение]"
    assert pipeline.describe_message("image", "") == "[Фото]"
    assert pipeline.describe_message("image", "анализы") == "[Фото] анализы"
    assert pipeline.describe_message("document", "") == "[Документ]"
    assert pipeline.describe_message("video", "") == "[Видео]"
    assert pipeline.describe_message("missing_call", "") == "[Пропущенный звонок]"
    assert pipeline.describe_message("other", "") == "[Вложение]"


def test_last_user_text():
    messages = [
        {"role": "user", "content": "старое"},
        {"role": "assistant", "content": "ответ"},
        {"role": "user", "content": "я из"},
        {"role": "user", "content": "Караганды"},
    ]
    assert pipeline.last_user_text(messages) == "я из\nКараганды"
    assert pipeline.last_user_text([{"role": "assistant", "content": "x"}]) == ""


# ---------------- города ----------------

CITIES = [
    {"id": "c1", "name": "Астана"},
    {"id": "c2", "name": "Алматы"},
]


def test_find_city_helpers():
    assert pipeline.find_city_by_id(CITIES, "c2")["name"] == "Алматы"
    assert pipeline.find_city_by_id(CITIES, None) is None
    assert pipeline.find_city_by_id(CITIES, "zzz") is None
    assert pipeline.find_city_by_name(CITIES, " астана ")["id"] == "c1"
    assert pipeline.find_city_by_name(CITIES, "") is None


# ---------------- round robin ----------------

def test_pick_round_robin_null_first():
    members = [
        {"id": "m1", "user_id": "u1", "city_id": "c1", "last_assigned_at": "2026-11-10T10:00:00+00:00"},
        {"id": "m2", "user_id": "u2", "city_id": None, "last_assigned_at": None},
        {"id": "m3", "user_id": "u3", "city_id": "c2", "last_assigned_at": None},  # другой город
    ]
    assert pipeline.pick_round_robin(members, "c1")["id"] == "m2"


def test_pick_round_robin_oldest():
    members = [
        {"id": "m1", "city_id": "c1", "last_assigned_at": "2026-11-10T10:00:00+00:00"},
        {"id": "m2", "city_id": None, "last_assigned_at": "2026-11-10T09:00:00Z"},
        {"id": "m3", "city_id": "c1", "last_assigned_at": "2026-11-10T11:00:00+00:00"},
    ]
    assert pipeline.pick_round_robin(members, "c1")["id"] == "m2"


def test_pick_round_robin_unknown_city_and_empty():
    members = [{"id": "m1", "city_id": "c1", "last_assigned_at": None}]
    assert pipeline.pick_round_robin(members, None) is None
    assert pipeline.pick_round_robin([], "c1") is None


# ---------------- лимит подписки ----------------

def test_month_start_iso():
    moment = datetime(2026, 11, 10, 23, 30, tzinfo=timezone.utc)
    assert pipeline.month_start_iso(moment) == "2026-11-01T00:00:00+00:00"


def test_is_limit_exceeded():
    # count уже включает новый диалог
    assert pipeline.is_limit_exceeded(None, 100) is False
    assert pipeline.is_limit_exceeded(None, 101) is True
    assert pipeline.is_limit_exceeded({"status": "active", "max_dialogs_per_month": 3000}, 2500) is False
    assert pipeline.is_limit_exceeded({"status": "active", "max_dialogs_per_month": 5}, 6) is True
    assert pipeline.is_limit_exceeded({"status": "expired", "max_dialogs_per_month": 3000}, 1) is True
    # Истёкшая подписка (expires_at в прошлом) — лимит исчерпан
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    expired = {"status": "active", "max_dialogs_per_month": 3000, "expires_at": "2026-11-01T00:00:00+00:00"}
    valid = {"status": "active", "max_dialogs_per_month": 3000, "expires_at": "2026-12-01T00:00:00Z"}
    assert pipeline.is_limit_exceeded(expired, 1, now) is True
    assert pipeline.is_limit_exceeded(valid, 1, now) is False


def test_should_notify_limit_once_a_day(monkeypatch):
    monkeypatch.setattr(pipeline, "_limit_notified", {})
    assert pipeline.should_notify_limit("b1", "2026-11-10") is True
    assert pipeline.should_notify_limit("b1", "2026-11-10") is False
    assert pipeline.should_notify_limit("b2", "2026-11-10") is True
    assert pipeline.should_notify_limit("b1", "2026-11-11") is True


# ---------------- порядок сообщений и запрос к базе знаний ----------------

def test_has_unanswered_and_user_after():
    messages = [{"id": 1, "role": "user"}, {"id": 2, "role": "user"}, {"id": 3, "role": "assistant"}]
    assert pipeline.has_unanswered_user(messages) is False
    assert pipeline.has_unanswered_user(messages[:2]) is True
    assert pipeline.has_unanswered_user([]) is False
    assert pipeline.has_user_after(messages, 1) is True
    assert pipeline.has_user_after(messages, 2) is False
    assert pipeline.has_user_after(messages, 999) is False


def test_reorder_after_answered():
    messages = [
        {"id": "m1", "role": "user"},
        {"id": "m2", "role": "user"},       # пришло во время генерации
        {"id": "a1", "role": "assistant"},  # ответ на m1
    ]
    ids = [m["id"] for m in pipeline.reorder_after_answered(messages, "m1")]
    assert ids == ["m1", "a1", "m2"]
    assert [m["id"] for m in pipeline.reorder_after_answered(messages, "zzz")] == ["m1", "m2", "a1"]


def test_build_kb_query():
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "Приём у кардиолога 8000 ₸. Записать?"},
        {"role": "manager", "content": "сотрудник"},
        {"role": "user", "content": "u3"},
        {"role": "user", "content": "да, давайте"},
    ]
    query = pipeline.build_kb_query(messages)
    assert query == "Приём у кардиолога 8000 ₸. Записать?\nu2\nu3\nда, давайте"


def test_describe_wazzup_error():
    assert pipeline.describe_wazzup_error({"error": "BAD_CONTACT", "description": "Нет WhatsApp"}) == \
        "BAD_CONTACT Нет WhatsApp"
    assert pipeline.describe_wazzup_error("CHANNEL_NO_MONEY") == "CHANNEL_NO_MONEY"
    assert pipeline.describe_wazzup_error(None) == "неизвестная ошибка"


def test_is_same_as_bot_text():
    bot_text = "Вот наш прайс [IMAGE:https://x.com/price.png]"
    assert pipeline.is_same_as_bot_text(bot_text, "Вот наш прайс") is True
    assert pipeline.is_same_as_bot_text(bot_text, bot_text) is True
    assert pipeline.is_same_as_bot_text(bot_text, "Другое") is False


# ---------------- process_dialog_reply (всё внешнее подменено) ----------------

def make_env(monkeypatch, dialog, ai_results, bot_extra=None, fresh_dialog=None, send_ok=True,
             messages=None, on_generate=None, cities=None):
    """
    Подменяет все внешние вызовы pipeline. Сообщения диалога — список в памяти:
    save_message дописывает в него, get_last_messages читает из него.
    on_generate(call_number) — вызывается во время генерации (например, «клиент дописал»).
    """
    calls = {"generate": [], "send": [], "saved": [], "updates": [], "hot": [], "attention": [],
             "assigned": [], "knowledge": [], "detect": [], "state_note": []}
    bot = {"id": "b1", "assignment_mode": "none"}
    if bot_extra:
        bot.update(bot_extra)
    if messages is None:
        messages = [{"id": "m1", "role": "user", "content": "Хочу записаться в Алматы"}]
    if cities is None:
        cities = CITIES
    calls["messages"] = messages
    dialog_state = dict(dialog)
    calls["dialog"] = dialog_state

    monkeypatch.setattr(pipeline, "get_bot", lambda bot_id: dict(bot))
    dialog_reads = {"n": 0}

    def fake_get_dialog(dialog_id):
        dialog_reads["n"] += 1
        if dialog_reads["n"] > 1 and fresh_dialog is not None:
            return dict(fresh_dialog)
        return dict(dialog_state)

    monkeypatch.setattr(pipeline, "get_dialog", fake_get_dialog)
    monkeypatch.setattr(pipeline, "get_last_messages", lambda dialog_id, limit=30: list(messages[-limit:]))
    monkeypatch.setattr(pipeline, "get_bot_cities", lambda bot_id: cities)

    def fake_detect(text, cities_arg, current):
        calls["detect"].append(text)
        return pipeline.find_city_by_name(cities_arg, "Алматы") if "Алматы" in text else None

    monkeypatch.setattr(pipeline, "detect_city", fake_detect)

    def fake_knowledge(bot_id, city_id, query):
        calls["knowledge"].append(city_id)
        return f"база {city_id}"

    monkeypatch.setattr(pipeline, "get_knowledge_for_city", fake_knowledge)
    monkeypatch.setattr(pipeline, "find_bot_images", lambda bot_id: [])
    monkeypatch.setattr(pipeline, "build_system_blocks", lambda *a: [{"type": "text", "text": "s"}])

    def fake_state_note(d, c, n, has_cities=True, is_first_reply=False):
        calls["state_note"].append(has_cities)
        return "note"

    monkeypatch.setattr(pipeline, "build_state_note", fake_state_note)

    async def fake_generate(bot_arg, system, history, note, city_names):
        calls["generate"].append(history)
        number = len(calls["generate"])
        if on_generate:
            await on_generate(number)
        return ai_results[min(number - 1, len(ai_results) - 1)]

    monkeypatch.setattr(pipeline, "generate_reply", fake_generate)

    async def fake_send(bot_arg, dialog_arg, text, from_bot=True):
        calls["send"].append(text)
        if send_ok:
            return {"ok": True, "message_id": f"wz-{len(calls['send'])}", "error": ""}
        return {"ok": False, "message_id": "", "error": "CHANNEL_NO_MONEY"}

    monkeypatch.setattr(pipeline, "send_to_client", fake_send)

    def fake_save(dialog_id, role, content, **kwargs):
        calls["saved"].append((role, content, kwargs))
        row = {"id": f"s{len(calls['saved'])}", "role": role, "content": content}
        messages.append(row)
        return row

    monkeypatch.setattr(pipeline, "save_message", fake_save)

    def fake_update(dialog_id, fields):
        calls["updates"].append(fields)
        dialog_state.update(fields)

    monkeypatch.setattr(pipeline, "update_dialog", fake_update)
    monkeypatch.setattr(pipeline, "get_team", lambda bot_id: [
        {"id": "tm1", "user_id": "u1", "city_id": None, "last_assigned_at": None},
    ])
    monkeypatch.setattr(pipeline, "mark_member_assigned", lambda member_id: calls["assigned"].append(member_id))

    async def fake_hot(bot_arg, dialog_arg, city, team, summary):
        calls["hot"].append(summary)
        return True

    async def fake_attention(bot_arg, dialog_arg, city, team, reason):
        calls["attention"].append(reason)
        return True

    monkeypatch.setattr(pipeline, "notify_hot_lead", fake_hot)
    monkeypatch.setattr(pipeline, "notify_attention", fake_attention)

    # Для handle_client_message
    monkeypatch.setattr(pipeline, "REPLY_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(pipeline, "REPLY_RETRY_PAUSE_SECONDS", 0)
    monkeypatch.setattr(pipeline, "external_id_exists", lambda external_id: False)
    monkeypatch.setattr(pipeline, "get_or_create_dialog",
                        lambda bot_arg, channel, chat_id: (dict(dialog_state), False))
    return calls


def ai(**kwargs):
    result = {"reply": "Ответ", "detected_city": "", "lead_status": "none", "lead_summary": "",
              "need_human": False, "need_human_reason": "", "client_name": "", "ok": True}
    result.update(kwargs)
    return result


def merged(updates):
    total = {}
    for item in updates:
        total.update(item)
    return total


def test_reply_hot_lead_with_city_retry(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": "c1", "ai_disabled": False}
    calls = make_env(
        monkeypatch, dialog,
        [ai(detected_city="Алматы"), ai(reply="Записала!", lead_status="hot", lead_summary="УЗИ завтра")],
        bot_extra={"assignment_mode": "round_robin"},
    )
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))

    # Город известен — по тексту не переключаем; переключает ИИ, один повтор по базе нового города
    assert calls["detect"] == []
    assert len(calls["generate"]) == 2
    assert calls["knowledge"] == ["c1", "c2"]
    assert calls["state_note"] == [True, True]
    assert calls["send"] == ["Записала!"]
    fields = merged(calls["updates"])
    assert fields["city_id"] == "c2"
    assert fields["status"] == "hot"
    assert fields["hot_reason"] == "УЗИ завтра"
    assert fields["reminder_count"] == 0
    assert fields["assigned_user_id"] == "u1"
    assert calls["assigned"] == ["tm1"]
    assert calls["hot"] == ["УЗИ завтра"]
    assert calls["attention"] == []
    assert datetime.fromisoformat(fields["hot_at"]).tzinfo is not None
    # external_id ответа = messageId Wazzup
    assert calls["saved"][0][2]["external_id"] == "wz-1"


def test_unknown_city_detected_by_text(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": None, "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai()])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["detect"] == ["Хочу записаться в Алматы"]
    assert calls["knowledge"] == ["c2"]
    assert len(calls["generate"]) == 1


def test_known_city_not_switched_by_text(monkeypatch):
    # «Алматы» в тексте, но город уже Астана — по тексту не переключаем
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai()])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["detect"] == []
    assert calls["knowledge"] == ["c1"]
    assert "city_id" not in merged(calls["updates"])


def test_no_cities_state_note_flag(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "city_id": None, "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai()], cities=[])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["state_note"] == [False]


def test_no_second_retry_even_if_city_changes_again(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(detected_city="Алматы"), ai(detected_city="Астана")])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert len(calls["generate"]) == 2


def test_nothing_to_answer_is_safe(monkeypatch):
    # Например, toggle-ai включил ИИ, а последнее сообщение — от бота
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    messages = [{"id": "m1", "role": "user", "content": "x"}, {"id": "a1", "role": "assistant", "content": "y"}]
    calls = make_env(monkeypatch, dialog, [ai()], messages=messages)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["generate"] == []
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["generate"] == []


def test_takeover_during_generation_blocks_send(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    fresh = dict(dialog)
    fresh["ai_disabled"] = True
    calls = make_env(monkeypatch, dialog, [ai()], fresh_dialog=fresh)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["send"] == []
    assert calls["saved"] == []


def test_ai_disabled_skips_generation(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "ai_disabled": True}
    calls = make_env(monkeypatch, dialog, [ai()])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["generate"] == []


def test_need_human_no_spam(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1",
              "ai_disabled": False, "needs_attention": True}
    calls = make_env(monkeypatch, dialog, [ai(need_human=True, need_human_reason="Просит врача")])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["attention"] == []
    assert merged(calls["updates"])["attention_reason"] == "Просит врача"


def test_send_error_marks_attention(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai()], send_ok=False)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    fields = merged(calls["updates"])
    assert fields["needs_attention"] is True
    assert "CHANNEL_NO_MONEY" in fields["attention_reason"]
    assert calls["saved"][0][2]["send_error"] == "CHANNEL_NO_MONEY"
    assert len(calls["attention"]) == 1


def test_already_hot_not_marked_again(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "invoice_sent", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(lead_status="hot", lead_summary="x")])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert calls["hot"] == []
    assert "status" not in merged(calls["updates"])


def test_client_name_overrides_profile_name(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1",
              "ai_disabled": False, "contact_name": "Aigerim WA"}
    calls = make_env(monkeypatch, dialog, [ai(client_name="Айгерим")])
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert merged(calls["updates"])["contact_name"] == "Айгерим"


# ---------------- гонки: клиент дописал, пока бот генерировал ответ ----------------

def test_race_a_message_during_generation_is_answered(monkeypatch):
    """(а) M2 пришло во время генерации ответа на M1 → бот отвечает ещё раз, M2 видно в конце истории."""
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    messages = [{"id": "m1", "role": "user", "content": "Сколько стоит УЗИ?"}]

    async def client_writes_more(call_number):
        if call_number == 1:
            messages.append({"id": "m2", "role": "user", "content": "И есть ли завтра запись?"})

    calls = make_env(monkeypatch, dialog, [ai(reply="5000 ₸"), ai(reply="Да, есть на 10:00")],
                     messages=messages, on_generate=client_writes_more)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))

    assert calls["send"] == ["5000 ₸", "Да, есть на 10:00"]
    second_history = calls["generate"][1]
    assert second_history[-1] == {"role": "user", "content": "И есть ли завтра запись?"}
    assert second_history[-2] == {"role": "assistant", "content": "5000 ₸"}


def test_race_extra_passes_limited(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    messages = [{"id": "m1", "role": "user", "content": "1"}]

    async def always_more(call_number):
        messages.append({"id": f"u{call_number}", "role": "user", "content": f"ещё {call_number}"})

    calls = make_env(monkeypatch, dialog, [ai()], messages=messages, on_generate=always_more)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert len(calls["generate"]) == 1 + pipeline.MAX_EXTRA_PASSES


def test_race_b_task_of_m2_waits_for_lock(monkeypatch):
    """
    (б) Задача M2 ждёт замок, пока бот отвечает на M1. Итог: на M2 ответили ровно один раз
    (повторным проходом задачи M1), задача M2 после замка видит ответ и ничего не дублирует.
    """
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    messages = [{"id": "m1", "role": "user", "content": "Сколько стоит УЗИ?"}]

    async def slow_generation(call_number):
        await asyncio.sleep(0.05)

    calls = make_env(monkeypatch, dialog, [ai(reply="5000 ₸"), ai(reply="Да, есть")],
                     messages=messages, on_generate=slow_generation)

    async def scenario():
        first = asyncio.create_task(pipeline.process_dialog_reply("b1", "d1"))
        await asyncio.sleep(0.01)  # бот уже генерирует ответ на M1
        await pipeline.handle_client_message({"id": "b1"}, "whatsapp", "7701", "Есть запись на завтра?")
        await first

    asyncio.run(scenario())
    assert calls["send"] == ["5000 ₸", "Да, есть"]
    assert len(calls["generate"]) == 2


def test_m2_after_answer_is_answered_by_its_task(monkeypatch):
    """Ответ на M1 уже ушёл, потом пришло M2 → задача M2 сама отвечает."""
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    messages = [{"id": "m1", "role": "user", "content": "Привет"},
                {"id": "a1", "role": "assistant", "content": "Здравствуйте!"}]
    calls = make_env(monkeypatch, dialog, [ai(reply="Конечно")], messages=messages)
    asyncio.run(pipeline.handle_client_message({"id": "b1"}, "whatsapp", "7701", "Можно записаться?"))
    assert calls["send"] == ["Конечно"]


def test_client_message_debounce_newer_message_wins(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai()], messages=[])
    replies = []

    def fake_save(dialog_id, role, content, **kwargs):
        # Сразу после нашего сообщения клиент дописал ещё одно
        calls["messages"].append({"id": "mine", "role": "user", "content": content})
        calls["messages"].append({"id": "newer", "role": "user", "content": "ещё"})
        return {"id": "mine"}

    async def fake_reply(bot_id, dialog_id):
        replies.append(dialog_id)

    monkeypatch.setattr(pipeline, "save_message", fake_save)
    monkeypatch.setattr(pipeline, "process_dialog_reply", fake_reply)
    asyncio.run(pipeline.handle_client_message({"id": "b1"}, "whatsapp", "7701", "привет"))
    assert replies == []


def test_client_message_duplicate_skipped(monkeypatch):
    monkeypatch.setattr(pipeline, "external_id_exists", lambda external_id: True)
    called = []
    monkeypatch.setattr(pipeline, "get_or_create_dialog", lambda *a: called.append(1) or (None, False))
    asyncio.run(pipeline.handle_client_message({"id": "b1"}, "whatsapp", "7701", "x", external_id="e1"))
    assert called == []


def test_client_voice_transcribed(monkeypatch):
    monkeypatch.setattr(pipeline, "external_id_exists", lambda external_id: False)

    async def fake_transcribe(url):
        return "хочу к лору"

    monkeypatch.setattr(pipeline, "transcribe_audio_url", fake_transcribe)
    dialog = {"id": "d1", "bot_id": "b1", "status": "new", "ai_disabled": True}
    monkeypatch.setattr(pipeline, "get_or_create_dialog", lambda bot, channel, chat_id: (dict(dialog), False))
    saved = []
    monkeypatch.setattr(pipeline, "save_message", lambda *a, **k: saved.append(a) or {"id": "m1"})
    asyncio.run(pipeline.handle_client_message({"id": "b1"}, "whatsapp", "7701", "",
                                               message_type="audio", media_url="https://x/v.ogg"))
    assert saved[0][2] == "[Голосовое сообщение]: хочу к лору"


# ---------------- handle_manager_message ----------------

def test_manager_message_pauses_ai(monkeypatch):
    monkeypatch.setattr(pipeline, "ECHO_CHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(pipeline, "get_last_messages", lambda dialog_id: [])
    monkeypatch.setattr(pipeline, "external_id_exists", lambda external_id: False)
    dialog = {"id": "d1", "bot_id": "b1", "ai_disabled": False}
    monkeypatch.setattr(pipeline, "find_dialog", lambda *a: dict(dialog))
    monkeypatch.setattr(pipeline, "is_bot_echo", lambda dialog_id, content: False)
    monkeypatch.setattr(pipeline, "get_or_create_dialog", lambda *a: (dict(dialog), False))
    saved = []
    monkeypatch.setattr(pipeline, "save_message", lambda *a, **k: saved.append((a, k)) or {"id": "m"})
    updates = []
    monkeypatch.setattr(pipeline, "update_dialog", lambda dialog_id, fields: updates.append(fields))
    asyncio.run(pipeline.handle_manager_message({"id": "b1"}, "whatsapp", "7701", "Счёт отправила",
                                                external_id="e5", author_name="Айгерим"))
    assert saved[0][0][1] == "manager"
    assert saved[0][1]["author_name"] == "Айгерим"
    assert updates == [{"needs_attention": False, "ai_disabled": True, "ai_paused_reason": "manager_replied"}]


def test_manager_echo_skipped(monkeypatch):
    monkeypatch.setattr(pipeline, "ECHO_CHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(pipeline, "external_id_exists", lambda external_id: False)
    monkeypatch.setattr(pipeline, "find_dialog", lambda *a: {"id": "d1"})
    monkeypatch.setattr(pipeline, "is_bot_echo", lambda dialog_id, content: True)
    saved = []
    monkeypatch.setattr(pipeline, "save_message", lambda *a, **k: saved.append(a))
    asyncio.run(pipeline.handle_manager_message({"id": "b1"}, "whatsapp", "7701", "Ответ бота"))
    assert saved == []


# ---------------- ошибка доставки (statuses → error) ----------------

def test_handle_delivery_error(monkeypatch):
    db = {
        "messages": [{"id": "msg1", "dialog_id": "d1", "external_id": "wz-9"}],
        "dialogs": [{"id": "d1", "bot_id": "b1", "city_id": "c1", "needs_attention": False}],
    }
    monkeypatch.setattr(pipeline, "supabase", FakeSupabase(db))
    monkeypatch.setattr(pipeline, "get_bot_cities", lambda bot_id: CITIES)
    monkeypatch.setattr(pipeline, "get_team", lambda bot_id: [])
    sent = []

    async def fake_attention(bot, dialog, city, team, reason):
        sent.append((city["name"], reason))
        return True

    monkeypatch.setattr(pipeline, "notify_attention", fake_attention)
    error = {"error": "BAD_CONTACT", "description": "Нет WhatsApp"}
    asyncio.run(pipeline.handle_delivery_error({"id": "b1"}, "wz-9", error))
    assert db["messages"][0]["send_error"] == "BAD_CONTACT Нет WhatsApp"
    assert db["dialogs"][0]["needs_attention"] is True
    assert db["dialogs"][0]["attention_reason"] == "Сообщение не доставлено клиенту: BAD_CONTACT Нет WhatsApp"
    assert sent == [("Астана", "Сообщение не доставлено клиенту: BAD_CONTACT Нет WhatsApp")]

    # Повторная ошибка — без спама
    asyncio.run(pipeline.handle_delivery_error({"id": "b1"}, "wz-9", error))
    assert len(sent) == 1


# ---------------- восстановление после перезапуска ----------------

def test_recover_unanswered_dialogs(monkeypatch):
    monkeypatch.setattr(pipeline, "find_recent_active_dialogs", lambda since: [
        {"id": "d1", "bot_id": "b1"}, {"id": "d2", "bot_id": "b1"},
    ])
    last = {
        "d1": [{"id": "m", "role": "user"}],
        "d2": [{"id": "a", "role": "assistant"}],
    }
    monkeypatch.setattr(pipeline, "get_last_messages", lambda dialog_id, limit=30: last[dialog_id])
    replied = []

    async def fake_reply(bot_id, dialog_id):
        replied.append(dialog_id)

    monkeypatch.setattr(pipeline, "process_dialog_reply", fake_reply)
    asyncio.run(pipeline.recover_unanswered_dialogs())
    assert replied == ["d1"]


def test_utc_now_iso_is_recent():
    value = datetime.fromisoformat(pipeline.utc_now_iso())
    assert abs(datetime.now(timezone.utc) - value) < timedelta(seconds=5)


def test_strip_staff_prefix():
    assert pipeline.strip_staff_prefix("[Сотрудник]: Цена 5000") == "Цена 5000"
    assert pipeline.strip_staff_prefix("  [Сотрудник]:[Сотрудник]: Да ") == "Да"
    assert pipeline.strip_staff_prefix("Обычный ответ [Сотрудник]: внутри") == "Обычный ответ [Сотрудник]: внутри"
    assert pipeline.strip_staff_prefix(None) == ""


# ---------------- автоответы, эхо API, голосовые, картинки ----------------

def test_is_auto_reply():
    now = datetime(2026, 11, 12, 10, 0, 30, tzinfo=timezone.utc)
    fresh_client = [{"role": "user", "created_at": "2026-11-12T10:00:25+00:00"}]
    old_client = [{"role": "user", "created_at": "2026-11-12T09:58:00+00:00"}]
    after_bot = [{"role": "assistant", "created_at": "2026-11-12T10:00:29+00:00"}]
    assert pipeline.is_auto_reply(fresh_client, now) is True
    assert pipeline.is_auto_reply(old_client, now) is False
    assert pipeline.is_auto_reply(after_bot, now) is False
    assert pipeline.is_auto_reply([], now) is False


def test_auto_reply_does_not_pause_ai(monkeypatch):
    monkeypatch.setattr(pipeline, "ECHO_CHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(pipeline, "external_id_exists", lambda external_id: False)
    monkeypatch.setattr(pipeline, "find_dialog", lambda *a: {"id": "d1", "ai_disabled": False})
    monkeypatch.setattr(pipeline, "is_bot_echo", lambda dialog_id, content: False)
    just_now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(pipeline, "get_last_messages", lambda dialog_id: [{"role": "user", "created_at": just_now}])
    saved, updates = [], []
    monkeypatch.setattr(pipeline, "save_message", lambda *a, **k: saved.append(a) or {"id": "m"})
    monkeypatch.setattr(pipeline, "update_dialog", lambda dialog_id, fields: updates.append(fields))
    asyncio.run(pipeline.handle_manager_message({"id": "b1"}, "whatsapp", "7701", "Здравствуйте! Мы скоро ответим"))
    assert saved == [] and updates == []


def test_echo_of_our_api_message_skipped(monkeypatch):
    from app import messenger
    messenger.remember_sent_id("wz-our-1")
    saved = []
    monkeypatch.setattr(pipeline, "save_message", lambda *a, **k: saved.append(a))
    asyncio.run(pipeline.handle_manager_message({"id": "b1"}, "whatsapp", "7701", "[Фото]",
                                                external_id="wz-our-1", message_type="image"))
    assert saved == []


def test_wait_for_transcriptions(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        pipeline._pending_transcriptions.pop(("b1", "7701"), None)

    monkeypatch.setattr(pipeline.asyncio, "sleep", fake_sleep)
    pipeline._pending_transcriptions[("b1", "7701")] = 1
    asyncio.run(pipeline.wait_for_transcriptions("b1", "7701"))
    assert slept == [1]
    assert ("b1", "7701") not in pipeline._pending_transcriptions


def test_keep_known_images():
    allowed = ["https://x.supabase.co/storage/v1/object/public/bot_images/b1/sofa.jpg"]
    reply = (f"Вот диван [IMAGE:{allowed[0]}] и ещё [IMAGE:https://evil.example/track.png]")
    result = pipeline.keep_known_images(reply, allowed)
    assert allowed[0] in result
    assert "evil.example" not in result
