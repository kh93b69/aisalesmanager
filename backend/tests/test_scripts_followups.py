"""
Скрипты (приветствие, скрипт продаж, ссылки), дожимы и вопросы без ответа — без сети.
"""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient

import tests._stubs  # noqa: F401 — фиктивные ключи Supabase
from app import prompts, ai_engine, pipeline, followups
from app.routes import settings as settings_routes
from app.routes import questions as questions_routes
from app.auth import get_current_user
from tests.test_pipeline import make_env, ai, merged


# ---------------- промпт ----------------

def block_1(bot):
    return prompts.build_system_blocks(bot, [], None, "база", [])[0]["text"]


def test_prompt_paragraph_and_truth_rules():
    text = block_1({"company_name": "Мебель Плюс"})
    assert "короткие абзацы" in text
    assert "unknown_question" in text
    assert "НЕ угадывай" in text


def test_prompt_greeting_script_links():
    bot = {
        "greeting_message": "Здравствуйте! Я Алия из «Мебель Плюс» 😊",
        "sales_script": "Возражение «дорого» — расскажи про рассрочку Kaspi.",
        "links": [
            {"title": "Каталог", "url": "https://shop.kz/catalog", "when": "просят каталог"},
            {"title": "", "url": "", "when": "пусто — пропускаем"},
        ],
    }
    text = block_1(bot)
    assert "ПРИВЕТСТВИЕ" in text and "Я Алия" in text
    assert "СКРИПТ ПРОДАЖ" in text and "рассрочку Kaspi" in text
    assert "ССЫЛКИ" in text and "- Каталог: https://shop.kz/catalog — просят каталог" in text
    assert "пусто — пропускаем" not in text


def test_prompt_without_scripts_has_no_sections():
    text = block_1({})
    assert "ПРИВЕТСТВИЕ (первое" not in text
    assert "СКРИПТ ПРОДАЖ" not in text
    assert "ССЫЛКИ\n" not in text


def test_state_note_first_reply():
    note = prompts.build_state_note({"status": "new"}, None, "сейчас", has_cities=False, is_first_reply=True)
    assert "первое сообщение" in note
    note = prompts.build_state_note({"status": "new"}, None, "сейчас", has_cities=False)
    assert "первое сообщение" not in note


# ---------------- ИИ: поле unknown_question ----------------

class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeUsage:
    input_tokens = 1
    output_tokens = 1
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class FakeResponse:
    def __init__(self, text):
        self.content = [FakeBlock(text)]
        self.stop_reason = "end_turn"
        self.usage = FakeUsage()


def test_schema_and_parse_unknown_question():
    schema = ai_engine.build_reply_schema([])
    assert "unknown_question" in schema["required"]
    response = FakeResponse('{"reply": "Уточню у руководителя", "detected_city": "", "lead_status": "none", '
                            '"lead_summary": "", "need_human": false, "need_human_reason": "", '
                            '"client_name": "", "unknown_question": "Есть ли рассрочка?"}')
    result = ai_engine.parse_response(response, [])
    assert result["unknown_question"] == "Есть ли рассрочка?"
    assert ai_engine.make_fallback("x")["unknown_question"] == ""


# ---------------- проверка настроек ----------------

def test_validate_links():
    links = settings_routes.validate_links([
        {"title": " Каталог ", "url": "https://shop.kz/c", "when": "просят каталог"},
        {"title": "", "url": "", "when": ""},
    ])
    assert links == [{"title": "Каталог", "url": "https://shop.kz/c", "when": "просят каталог"}]
    with pytest.raises(HTTPException):
        settings_routes.validate_links([{"title": "Плохая", "url": "javascript:alert(1)"}])
    with pytest.raises(HTTPException):
        settings_routes.validate_links("не список")


def test_validate_followups():
    steps = settings_routes.validate_followups([{"hours": 3, "text": ""}, {"hours": "24", "text": " Напоминаем "}])
    assert steps == [{"hours": 3, "text": ""}, {"hours": 24, "text": "Напоминаем"}]
    with pytest.raises(HTTPException):
        settings_routes.validate_followups([{"hours": 0}])
    with pytest.raises(HTTPException):
        settings_routes.validate_followups([{"hours": 1}] * 4)


def test_validate_bot_update_new_fields():
    clean = settings_routes.validate_bot_update({
        "greeting_message": " Привет! ", "sales_script": "Скрипт", "followups_enabled": True,
        "links": [], "followups": [{"hours": 2, "text": ""}],
    })
    assert clean["greeting_message"] == "Привет!"
    assert clean["followups_enabled"] is True
    assert clean["followups"] == [{"hours": 2, "text": ""}]
    with pytest.raises(HTTPException):
        settings_routes.validate_bot_update({"followups_enabled": "да"})


# ---------------- дожимы ----------------

def test_clean_followup_steps():
    steps = followups.clean_followup_steps([{"hours": 3, "text": " a "}, {"hours": 0}, "x", {"hours": True}])
    assert steps == [{"hours": 3, "text": "a"}]
    assert followups.clean_followup_steps(None) == []


def test_is_work_time():
    # TIMEZONE по умолчанию Asia/Almaty (UTC+5)
    assert followups.is_work_time(datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)) is True    # 11:00
    assert followups.is_work_time(datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)) is False  # 23:00


def test_is_followup_due():
    now = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
    dialog = {"last_message_at": (now - timedelta(hours=4)).isoformat()}
    assert followups.is_followup_due(dialog, {"hours": 3, "text": ""}, now) is True
    assert followups.is_followup_due(dialog, {"hours": 5, "text": ""}, now) is False
    assert followups.is_followup_due({}, {"hours": 1, "text": ""}, now) is False


def make_followup_env(monkeypatch, messages, ai_result=None, claim=True):
    sent, saved = [], []
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "ai_disabled": False,
              "followup_count": 0, "city_id": None}
    monkeypatch.setattr(pipeline, "get_last_messages", lambda dialog_id, limit=30: list(messages))
    monkeypatch.setattr(pipeline, "get_dialog", lambda dialog_id: dict(dialog))
    monkeypatch.setattr(pipeline, "get_bot_cities", lambda bot_id: [])
    monkeypatch.setattr(followups, "claim_followup", lambda d: claim)
    # Тест не должен зависеть от того, который сейчас час, и от базы
    monkeypatch.setattr(followups, "can_send_followup", lambda bot, now: True)
    monkeypatch.setattr(followups, "has_open_question", lambda dialog_id: False)
    monkeypatch.setattr(followups, "finish_followups", lambda dialog, total: None)
    generated = []

    async def fake_generate(bot, dialog_arg, cities, city, history, query, is_first_reply=False):
        generated.append(history)
        return ai_result or {"reply": "Подобрать вам диван?", "ok": True}

    monkeypatch.setattr(pipeline, "build_and_generate", fake_generate)

    async def fake_send(bot, dialog_arg, text, from_bot=True):
        sent.append(text)
        return {"ok": True, "message_id": "wz-1", "error": ""}

    monkeypatch.setattr(followups, "send_to_client", fake_send)
    monkeypatch.setattr(pipeline, "save_message", lambda *a, **k: saved.append((a, k)) or {"id": "x"})
    return dialog, sent, saved, generated


def test_followup_ai_text_sent(monkeypatch):
    messages = [{"role": "user", "content": "Сколько стоит диван?"},
                {"role": "assistant", "content": "120 000 тг"}]
    dialog, sent, saved, generated = make_followup_env(monkeypatch, messages)
    asyncio.run(followups.send_followup({"id": "b1"}, dialog, {"hours": 3, "text": ""}))
    assert sent == ["Подобрать вам диван?"]
    assert generated[0][-1] == {"role": "user", "content": "[Клиент не отвечает 3 ч]"}
    assert saved[0][0][1] == "assistant"


def test_followup_fixed_text_no_ai(monkeypatch):
    messages = [{"role": "user", "content": "Привет"}, {"role": "assistant", "content": "Здравствуйте!"}]
    dialog, sent, saved, generated = make_followup_env(monkeypatch, messages)
    asyncio.run(followups.send_followup({"id": "b1"}, dialog, {"hours": 24, "text": "Актуален ли вопрос?"}))
    assert sent == ["Актуален ли вопрос?"]
    assert generated == []


def test_followup_skipped_when_client_wrote_last(monkeypatch):
    messages = [{"role": "assistant", "content": "Здравствуйте!"}, {"role": "user", "content": "Ещё вопрос"}]
    dialog, sent, saved, generated = make_followup_env(monkeypatch, messages)
    asyncio.run(followups.send_followup({"id": "b1"}, dialog, {"hours": 3, "text": "Напоминаю"}))
    assert sent == []


def test_followup_not_sent_on_ai_error(monkeypatch):
    messages = [{"role": "user", "content": "Цена?"}, {"role": "assistant", "content": "10 000"}]
    dialog, sent, saved, generated = make_followup_env(monkeypatch, messages,
                                                       ai_result={"reply": "Спасибо за сообщение!", "ok": False})
    asyncio.run(followups.send_followup({"id": "b1"}, dialog, {"hours": 3, "text": ""}))
    assert sent == []


def test_followup_not_sent_twice(monkeypatch):
    messages = [{"role": "user", "content": "Цена?"}, {"role": "assistant", "content": "10 000"}]
    dialog, sent, saved, generated = make_followup_env(monkeypatch, messages, claim=False)
    asyncio.run(followups.send_followup({"id": "b1"}, dialog, {"hours": 3, "text": "Напоминаю"}))
    assert sent == []


# ---------------- вопрос без ответа в pipeline ----------------

def test_unknown_question_saved_and_boss_notified(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    calls = make_env(monkeypatch, dialog, [ai(reply="Уточню у руководителя и вернусь", unknown_question="Есть ли рассрочка?")])
    saved_questions, notified = [], []
    monkeypatch.setattr(pipeline, "save_unanswered_question",
                        lambda bot_id, dialog_id, city_id, q, msg: saved_questions.append((q, city_id)) or True)

    async def fake_unknown(bot, dialog_arg, city, team, question):
        notified.append(question)
        return True

    monkeypatch.setattr(pipeline, "notify_unknown_question", fake_unknown)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert saved_questions == [("Есть ли рассрочка?", "c1")]
    assert notified == ["Есть ли рассрочка?"]
    # Общее уведомление «нужен менеджер» не дублируем
    assert calls["attention"] == []
    assert "рассрочка" in merged(calls["updates"])["attention_reason"]


def test_repeated_unknown_question_not_notified_again(monkeypatch):
    dialog = {"id": "d1", "bot_id": "b1", "status": "in_progress", "city_id": "c1", "ai_disabled": False}
    make_env(monkeypatch, dialog, [ai(unknown_question="Есть ли рассрочка?")])
    notified = []
    monkeypatch.setattr(pipeline, "save_unanswered_question", lambda *a: False)

    async def fake_unknown(*a):
        notified.append(a)
        return True

    monkeypatch.setattr(pipeline, "notify_unknown_question", fake_unknown)
    asyncio.run(pipeline.process_dialog_reply("b1", "d1"))
    assert notified == []


def test_first_reply_flag():
    assert pipeline.is_first_reply([{"role": "user"}]) is True
    assert pipeline.is_first_reply([{"role": "user"}, {"role": "manager"}, {"role": "user"}]) is False


# ---------------- раздел «Без ответа» (API) ----------------

class Store:
    """Мини-БД для API вопросов: только нужные операции."""

    def __init__(self):
        self.questions = [{"id": "11111111-1111-1111-1111-111111111111", "bot_id": "bot1", "dialog_id": "d1",
                           "city_id": "c1", "question": "Есть ли рассрочка?", "status": "open",
                           "knowledge_chunk_id": None}]
        self.chunks = []


def make_questions_client(monkeypatch, owner=True):
    store = Store()

    def fake_load(question_id, user_id):
        if not owner:
            raise HTTPException(status_code=403, detail="Нет доступа")
        for question in store.questions:
            if question["id"] == question_id:
                return question, {"bot": {"id": "bot1"}, "role": "owner"}
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    monkeypatch.setattr(questions_routes, "load_question", fake_load)

    class Table:
        def __init__(self, name):
            self.name = name
            self.row = None
            self.fields = None

        def insert(self, row):
            self.row = row
            return self

        def update(self, fields):
            self.fields = fields
            return self

        def eq(self, *a):
            return self

        def execute(self):
            class R:
                pass
            result = R()
            if self.name == "knowledge_chunks" and self.row:
                self.row["id"] = len(store.chunks) + 1
                store.chunks.append(self.row)
                result.data = [self.row]
            elif self.name == "unanswered_questions" and self.fields:
                store.questions[0].update(self.fields)
                result.data = [store.questions[0]]
            else:
                result.data = []
            return result

    class DB:
        def table(self, name):
            return Table(name)

    monkeypatch.setattr(questions_routes, "supabase", DB())
    app = FastAPI()
    app.include_router(questions_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "owner", "email": "o@test.kz"}
    return TestClient(app), store


def test_answer_question_adds_to_knowledge(monkeypatch):
    client, store = make_questions_client(monkeypatch)
    qid = store.questions[0]["id"]
    response = client.post(f"/api/questions/{qid}/answer", json={"answer": "Да, Kaspi Red на 12 месяцев"})
    assert response.status_code == 200
    assert store.chunks[0]["content"].startswith("[ВОПРОС-ОТВЕТ] Вопрос клиента: Есть ли рассрочка?")
    assert "Kaspi Red" in store.chunks[0]["content"]
    assert store.chunks[0]["city_id"] is None  # по умолчанию — для всех городов
    assert store.questions[0]["status"] == "answered"
    # Повторно ответить нельзя
    assert client.post(f"/api/questions/{qid}/answer", json={"answer": "ещё"}).status_code == 400


def test_answer_question_city_only_and_validation(monkeypatch):
    client, store = make_questions_client(monkeypatch)
    qid = store.questions[0]["id"]
    assert client.post(f"/api/questions/{qid}/answer", json={"answer": "  "}).status_code == 400
    client.post(f"/api/questions/{qid}/answer", json={"answer": "Да", "city_only": True})
    assert store.chunks[0]["city_id"] == "c1"


def test_answer_question_only_owner(monkeypatch):
    client, store = make_questions_client(monkeypatch, owner=False)
    response = client.post(f"/api/questions/{store.questions[0]['id']}/answer", json={"answer": "Да"})
    assert response.status_code == 403
    assert store.chunks == []


def test_followup_skipped_when_client_waits_for_answer(monkeypatch):
    messages = [{"role": "user", "content": "Есть рассрочка?"}, {"role": "assistant", "content": "Уточню у руководителя"}]
    dialog, sent, saved, generated = make_followup_env(monkeypatch, messages)
    monkeypatch.setattr(followups, "has_open_question", lambda dialog_id: True)
    asyncio.run(followups.send_followup({"id": "b1"}, dialog, {"hours": 3, "text": "Напоминаю"}))
    assert sent == []


def test_followup_ai_can_decline(monkeypatch):
    # Разговор завершён — ИИ вернул пустой ответ (parse_response делает из него ok=False) → ничего не уходит
    messages = [{"role": "user", "content": "Спасибо, до свидания"}, {"role": "assistant", "content": "Всего доброго!"}]
    dialog, sent, saved, generated = make_followup_env(monkeypatch, messages, ai_result={"reply": "", "ok": False})
    asyncio.run(followups.send_followup({"id": "b1"}, dialog, {"hours": 3, "text": ""}))
    assert sent == []
