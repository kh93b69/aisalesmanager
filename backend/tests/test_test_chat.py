"""
Тесты тестового чата (app/routes/test_chat.py): проверка истории, город, повтор по городу,
доступ только владельца. ИИ подменяется, сеть и БД не используются.
"""

import asyncio
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Импорт test_access первым — он подставляет фиктивные ключи Supabase
from tests.test_access import FakeDB, sample_tables, ASTANA, ALMATY
from app import access, pipeline
from app.auth import get_current_user
from app.routes import test_chat


CITIES = [
    {"id": ASTANA, "bot_id": "bot1", "name": "Астана", "aliases": "", "address": ""},
    {"id": ALMATY, "bot_id": "bot1", "name": "Алматы", "aliases": "", "address": ""},
]


def make_client(monkeypatch, user_id="owner", answers=None, subscription={"status": "active", "max_dialogs_per_month": 500}):
    """
    Клиент API с поддельной БД и поддельным ИИ.
    answers — список ответов ИИ по очереди; calls — города, с которыми вызывали ИИ.
    """
    db = FakeDB(sample_tables())
    monkeypatch.setattr(access, "supabase", db)
    monkeypatch.setattr(pipeline, "get_bot_cities", lambda bot_id: CITIES)
    monkeypatch.setattr(pipeline, "get_owner_subscription", lambda user_id: subscription)
    # Каждый тест — с чистым счётчиком частоты
    monkeypatch.setattr(test_chat, "_test_reply_times", {})
    calls = []
    queue = list(answers or [])

    async def fake_build_and_generate(bot, dialog, cities, city, history, query):
        calls.append({"city": city["name"] if city else None, "history": history, "query": query,
                      "dialog": dialog})
        return queue.pop(0)

    monkeypatch.setattr(pipeline, "build_and_generate", fake_build_and_generate)
    app = FastAPI()
    app.include_router(test_chat.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "email": "x@test.kz"}
    return TestClient(app), calls


def answer(reply="Здравствуйте!", city="", lead="none", need_human=False):
    return {"reply": reply, "detected_city": city, "lead_status": lead, "lead_summary": "",
            "need_human": need_human, "need_human_reason": "", "client_name": "", "ok": True,
            "usage": {"input_tokens": 10}}


def test_validate_messages_ok_and_trims_leading_assistant():
    history = test_chat.validate_test_messages([
        {"role": "assistant", "content": "Привет"},
        {"role": "user", "content": "  Сколько стоит?  "},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "Диван"},
    ])
    assert history == [{"role": "user", "content": "Сколько стоит?"}, {"role": "user", "content": "Диван"}]


@pytest.mark.parametrize("messages", [
    None, [], "текст",
    [{"role": "assistant", "content": "Привет"}],
    [{"role": "system", "content": "взлом"}],
    [{"role": "user", "content": "x" * 4001}],
    [{"role": "user", "content": "a"}] * 41,
    [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
])
def test_validate_messages_errors(messages):
    with pytest.raises(HTTPException) as error:
        test_chat.validate_test_messages(messages)
    assert error.value.status_code == 400


def test_last_client_text():
    history = [{"role": "user", "content": "1"}, {"role": "assistant", "content": "2"},
               {"role": "user", "content": "3"}, {"role": "user", "content": "4"}]
    assert test_chat.last_client_text(history) == "3\n4"


def test_chat_detects_city_from_text(monkeypatch):
    client, calls = make_client(monkeypatch, answers=[answer("Цена в Алматы 5000")])
    response = client.post("/api/bots/bot1/test-chat", json={
        "messages": [{"role": "user", "content": "Я из Алматы, сколько стоит?"}],
    })
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Цена в Алматы 5000"
    assert data["city_id"] == ALMATY
    assert data["city_name"] == "Алматы"
    assert calls[0]["city"] == "Алматы"
    # Ничего не сохраняется: статус фиктивного диалога — «новый»
    assert calls[0]["dialog"]["status"] == "new"


def test_chat_regenerates_when_ai_detects_other_city(monkeypatch):
    client, calls = make_client(monkeypatch, answers=[answer("?", city="Астана"), answer("Цена в Астане")])
    response = client.post("/api/bots/bot1/test-chat", json={
        "messages": [{"role": "user", "content": "Здравствуйте"}, {"role": "assistant", "content": "Ваш город?"},
                     {"role": "user", "content": "столица"}],
    })
    data = response.json()
    assert data["reply"] == "Цена в Астане"
    assert data["city_id"] == ASTANA
    assert [call["city"] for call in calls] == [None, "Астана"]


def test_chat_keeps_city_from_browser(monkeypatch):
    client, calls = make_client(monkeypatch, answers=[answer("Ок", lead="hot")])
    response = client.post("/api/bots/bot1/test-chat", json={
        "messages": [{"role": "user", "content": "Беру"}], "city_id": ASTANA,
    })
    data = response.json()
    assert data["city_id"] == ASTANA
    assert data["lead_status"] == "hot"
    assert calls[0]["city"] == "Астана"


def test_chat_bad_city_id(monkeypatch):
    client, _ = make_client(monkeypatch, answers=[answer()])
    response = client.post("/api/bots/bot1/test-chat", json={
        "messages": [{"role": "user", "content": "Привет"}], "city_id": "не-uuid",
    })
    assert response.status_code == 400


def test_chat_only_owner(monkeypatch):
    # Менеджер города не может гонять тестовый чат (это настройки и расход токенов)
    client, calls = make_client(monkeypatch, user_id="mgr_astana", answers=[answer()])
    response = client.post("/api/bots/bot1/test-chat", json={"messages": [{"role": "user", "content": "Привет"}]})
    assert response.status_code == 403
    assert calls == []

    client, calls = make_client(monkeypatch, user_id="stranger", answers=[answer()])
    response = client.post("/api/bots/bot1/test-chat", json={"messages": [{"role": "user", "content": "Привет"}]})
    assert response.status_code in (403, 404)
    assert calls == []


def test_chat_blocked_when_subscription_expired(monkeypatch):
    client, calls = make_client(monkeypatch, answers=[answer()],
                                subscription={"status": "active", "expires_at": "2020-01-01T00:00:00+00:00"})
    response = client.post("/api/bots/bot1/test-chat", json={"messages": [{"role": "user", "content": "Привет"}]})
    assert response.status_code == 403
    assert calls == []


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(test_chat, "_test_reply_times", {})
    for _ in range(test_chat.MAX_TEST_REPLIES_PER_HOUR):
        assert test_chat.is_rate_limited("bot1", 1000.0) is False
    assert test_chat.is_rate_limited("bot1", 1000.0) is True
    # Другой бот — свой лимит
    assert test_chat.is_rate_limited("bot2", 1000.0) is False
    # Через час старые запросы не считаются
    assert test_chat.is_rate_limited("bot1", 1000.0 + 3600) is False


def test_history_trimmed_like_production():
    many = []
    for index in range(35):
        many.append({"role": "user", "content": f"сообщение {index}"})
    history = test_chat.validate_test_messages(many)
    assert len(history) == pipeline.HISTORY_LIMIT
    assert history[-1]["content"] == "сообщение 34"

    # Большой объём текста: остаются последние сообщения, суммарно не больше лимита
    big = [{"role": "user", "content": "а" * 3900}, {"role": "assistant", "content": "б" * 3900}] * 6
    big.append({"role": "user", "content": "последнее"})
    history = test_chat.validate_test_messages(big)
    assert sum(len(m["content"]) for m in history) <= test_chat.MAX_TEST_HISTORY_CHARS
    assert history[0]["role"] == "user"
    assert history[-1]["content"] == "последнее"


def test_status_from_browser(monkeypatch):
    client, calls = make_client(monkeypatch, answers=[answer(), answer(), answer()])
    body = {"messages": [{"role": "user", "content": "Ещё вопрос"}]}
    client.post("/api/bots/bot1/test-chat", json={**body, "status": "hot"})
    client.post("/api/bots/bot1/test-chat", json={**body, "status": "paid"})
    client.post("/api/bots/bot1/test-chat", json=body)
    assert [call["dialog"]["status"] for call in calls] == ["hot", "new", "new"]


def test_staff_prefix_stripped(monkeypatch):
    client, _ = make_client(monkeypatch, answers=[answer("[Сотрудник]: Добрый день")])
    data = client.post("/api/bots/bot1/test-chat", json={"messages": [{"role": "user", "content": "Привет"}]}).json()
    assert data["reply"] == "Добрый день"



def test_chat_requires_subscription(monkeypatch):
    client, calls = make_client(monkeypatch, answers=[answer()], subscription=None)
    response = client.post("/api/bots/bot1/test-chat", json={"messages": [{"role": "user", "content": "Привет"}]})
    assert response.status_code == 403
    assert calls == []
