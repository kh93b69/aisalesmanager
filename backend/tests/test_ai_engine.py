"""
Тесты app/ai_engine.py. В Anthropic API не ходим: подменяем client.messages.create
поддельной async-функцией, которая запоминает параметры запроса и возвращает заготовленный ответ.
"""
import os

# app.config читает переменные при первом импорте, а app.database создаёт клиента Supabase.
# Подставляем фейковые значения (в БД эти тесты не ходят), чтобы импорт не падал.
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test.test.test")

import asyncio
import json
from types import SimpleNamespace

import anthropic
import httpx

from app import ai_engine  # noqa: E402

CITY_NAMES = ["Астана", "Алматы"]
SYSTEM = [
    {"type": "text", "text": "правила"},
    {"type": "text", "text": "база", "cache_control": {"type": "ephemeral"}},
]
HISTORY = [{"role": "user", "content": "Здравствуйте, я из Астаны"}]
GOOD_ANSWER = {
    "reply": "Здравствуйте! Чем могу помочь?",
    "detected_city": "Астана",
    "lead_status": "none",
    "lead_summary": "",
    "need_human": False,
    "need_human_reason": "",
    "client_name": "Айгерим",
}


def make_response(text, stop_reason="end_turn", with_thinking=False):
    """Ответ в виде, похожем на объект SDK (content, stop_reason, usage)."""
    content = []
    if with_thinking:
        content.append(SimpleNamespace(type="thinking", text=None, thinking="..."))
    content.append(SimpleNamespace(type="text", text=text))
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=5000,
        cache_creation_input_tokens=None,
    )
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)


def install_fake_create(monkeypatch, response=None, error=None):
    """Подменяет client.messages.create; возвращает список, куда пишутся параметры вызовов."""
    calls = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(ai_engine.client.messages, "create", fake_create)
    monkeypatch.setattr(ai_engine, "ANTHROPIC_API_KEY", "test-key")
    return calls


def run_generate(bot=None):
    return asyncio.run(ai_engine.generate_reply(
        bot or {"id": "b1"}, SYSTEM, HISTORY, "Сейчас: вторник", CITY_NAMES
    ))


def test_request_structure(monkeypatch):
    calls = install_fake_create(monkeypatch, make_response(json.dumps(GOOD_ANSWER)))
    run_generate({"id": "b1", "ai_model": "claude-sonnet-5-5"})

    request = calls[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["max_tokens"] == 4000
    assert request["system"] == SYSTEM
    for forbidden in ("temperature", "top_p", "top_k", "thinking"):
        assert forbidden not in request
    # Служебная заметка — последним сообщением role=system, история не изменена
    assert request["messages"][-1] == {"role": "system", "content": "Сейчас: вторник"}
    assert request["messages"][:-1] == HISTORY
    assert len(HISTORY) == 1

    config = request["output_config"]
    assert config["effort"] == "low"
    assert config["format"]["type"] == "json_schema"
    schema = config["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"].keys())
    assert schema["properties"]["detected_city"]["enum"] == ["Астана", "Алматы", ""]
    assert schema["properties"]["lead_status"]["enum"] == ["none", "hot"]


def test_default_model(monkeypatch):
    calls = install_fake_create(monkeypatch, make_response(json.dumps(GOOD_ANSWER)))
    run_generate({"id": "b1", "ai_model": None})
    assert calls[0]["model"] == ai_engine.DEFAULT_AI_MODEL
    run_generate({"id": "b1", "ai_model": "gpt-4o"})
    assert calls[1]["model"] == ai_engine.DEFAULT_AI_MODEL


def test_schema_without_cities():
    schema = ai_engine.build_reply_schema([])
    assert schema["properties"]["detected_city"]["enum"] == [""]


def test_good_answer(monkeypatch):
    install_fake_create(monkeypatch, make_response(json.dumps(GOOD_ANSWER), with_thinking=True))
    result = run_generate()
    assert result["ok"] is True
    assert result["reply"] == "Здравствуйте! Чем могу помочь?"
    assert result["detected_city"] == "Астана"
    assert result["client_name"] == "Айгерим"
    assert result["need_human"] is False
    assert result["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 5000,
        "cache_creation_input_tokens": 0,
    }


def test_unknown_city_and_status_are_cleaned(monkeypatch):
    answer = dict(GOOD_ANSWER, detected_city="Париж", lead_status="warm", need_human=True, need_human_reason="")
    install_fake_create(monkeypatch, make_response(json.dumps(answer)))
    result = run_generate()
    assert result["detected_city"] == ""
    assert result["lead_status"] == "none"
    assert result["need_human"] is True
    assert result["need_human_reason"] != ""


def test_refusal(monkeypatch):
    install_fake_create(monkeypatch, make_response("", stop_reason="refusal"))
    result = run_generate()
    assert result["ok"] is False
    assert result["need_human"] is True
    assert result["need_human_reason"] == "ИИ отказался отвечать"
    assert result["reply"] == ai_engine.FALLBACK_REPLY


def test_max_tokens(monkeypatch):
    install_fake_create(monkeypatch, make_response('{"reply": "обры', stop_reason="max_tokens"))
    result = run_generate()
    assert result["ok"] is False
    assert result["need_human"] is True


def test_broken_json(monkeypatch):
    install_fake_create(monkeypatch, make_response("это не json"))
    result = run_generate()
    assert result["ok"] is False
    assert result["reply"] == ai_engine.FALLBACK_REPLY
    assert result["lead_status"] == "none"


def test_empty_reply(monkeypatch):
    install_fake_create(monkeypatch, make_response(json.dumps(dict(GOOD_ANSWER, reply="  "))))
    assert run_generate()["ok"] is False


def make_request():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_api_errors(monkeypatch):
    request = make_request()
    errors = [
        anthropic.RateLimitError("лимит", response=httpx.Response(429, request=request), body=None),
        anthropic.APITimeoutError(request=request),
        anthropic.APIConnectionError(request=request),
        anthropic.InternalServerError("ошибка", response=httpx.Response(500, request=request), body=None),
        RuntimeError("что-то сломалось"),
    ]
    reasons = []
    for error in errors:
        install_fake_create(monkeypatch, error=error)
        result = run_generate()
        assert result["ok"] is False
        assert result["need_human"] is True
        assert result["reply"] == ai_engine.FALLBACK_REPLY
        reasons.append(result["need_human_reason"])
    # Таймаут распознан именно как таймаут (а не общая ошибка соединения)
    assert "таймаут" in reasons[1]
    assert "лимит" in reasons[0]
    assert "500" in reasons[3]
    assert len(set(reasons)) == len(reasons)


def test_no_api_key(monkeypatch):
    calls = install_fake_create(monkeypatch, make_response(json.dumps(GOOD_ANSWER)))
    monkeypatch.setattr(ai_engine, "ANTHROPIC_API_KEY", "")
    result = run_generate()
    assert result["ok"] is False
    assert calls == []
