"""Тесты вебхуков и main.py — FastAPI TestClient, обработчики и чтение бота подменены."""
import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._stubs import ensure_all

ensure_all()

from app.routes import webhooks  # noqa: E402

BOT = {"id": "b1", "wazzup_webhook_secret": "s3cret", "wazzup_channel_id": "ch1"}


def make_client(monkeypatch, bot=None):
    """Приложение только с роутером вебхуков (без планировщика) + записанные вызовы."""
    if bot is None:
        bot = BOT
    calls = {"client": [], "manager": [], "delivery": [], "state": []}

    def fake_start_background(func, *args, **kwargs):
        # Вместо фоновой задачи просто записываем, что и с чем было бы запущено
        if func is webhooks.handle_client_message:
            calls["client"].append((args[1], args[2], args[3], kwargs))
        elif func is webhooks.handle_manager_message:
            calls["manager"].append((args[1], args[2], args[3], kwargs))
        elif func is webhooks.handle_delivery_error:
            calls["delivery"].append((args[1], args[2]))

    monkeypatch.setattr(webhooks, "start_background", fake_start_background)
    monkeypatch.setattr(webhooks, "load_bot", lambda bot_id: dict(bot) if bot_id == "b1" else None)
    # Один бот на аккаунт Wazzup (другие номера — в отдельном тесте)
    monkeypatch.setattr(webhooks, "load_account_bots", lambda bot_arg: [bot_arg])
    monkeypatch.setattr(webhooks, "save_channel_state",
                        lambda bot_id, state: calls["state"].append((bot_id, state)))
    app = FastAPI()
    app.include_router(webhooks.router)
    return TestClient(app), calls


def msg(**kwargs):
    base = {"messageId": "m1", "channelId": "ch1", "chatType": "whatsapp", "chatId": "77011234567",
            "type": "text", "status": "inbound", "text": "Здравствуйте", "isEcho": False,
            "contact": {"name": "Аня"}}
    base.update(kwargs)
    return base


def test_unauthorized(monkeypatch):
    client, calls = make_client(monkeypatch)
    for url in ["/webhook/wazzup/b1", "/webhook/wazzup/b1?token=wrong", "/webhook/wazzup/nobot?token=s3cret"]:
        response = client.post(url, json={"messages": [msg()]})
        assert response.status_code == 200
        assert response.json() == {"status": "unauthorized"}
    # Тестовый запрос тоже требует токен
    assert client.post("/webhook/wazzup/b1", json={"test": True}).json()["status"] == "unauthorized"
    assert calls["client"] == []


def test_test_ping(monkeypatch):
    client, _calls = make_client(monkeypatch)
    response = client.post("/webhook/wazzup/b1?token=s3cret", json={"test": True})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_client_and_manager_messages(monkeypatch):
    client, calls = make_client(monkeypatch)
    body = {"messages": [
        msg(),
        msg(messageId="m2", isEcho=True, status="sent", text="Счёт отправила", authorName="Айгерим"),
        msg(messageId="m3", chatType="telegram"),
        msg(messageId="m4", channelId="other"),
        msg(messageId="m5", isEdited=True),
        msg(messageId="m6", isDeleted=True),
        msg(messageId="m7", status="error"),
        msg(messageId="m8", type="audio", text="", contentUri="https://store.wazzup24.com/v.ogg"),
        msg(messageId="m9", type="vcard"),
    ]}
    response = client.post("/webhook/wazzup/b1?token=s3cret", json=body)
    assert response.json() == {"status": "ok"}

    assert [c[3]["external_id"] for c in calls["client"]] == ["m1", "m8", "m9"]
    first = calls["client"][0]
    assert first[0] == "whatsapp" and first[1] == "77011234567" and first[2] == "Здравствуйте"
    assert first[3]["contact_name"] == "Аня"
    assert calls["client"][1][3]["message_type"] == "audio"
    assert calls["client"][1][3]["media_url"] == "https://store.wazzup24.com/v.ogg"
    assert calls["client"][2][3]["message_type"] == "other"

    assert len(calls["manager"]) == 1
    assert calls["manager"][0][2] == "Счёт отправила"
    assert calls["manager"][0][3]["author_name"] == "Айгерим"


def test_bot_without_channel_ignores_messages(monkeypatch):
    bot = {"id": "b1", "wazzup_webhook_secret": "s3cret", "wazzup_channel_id": None}
    client, calls = make_client(monkeypatch, bot=bot)
    response = client.post("/webhook/wazzup/b1?token=s3cret", json={"messages": [msg()]})
    assert response.json() == {"status": "ok"}
    assert calls["client"] == []


def test_channel_updates_and_statuses(monkeypatch):
    client, calls = make_client(monkeypatch)
    body = {
        "channelsUpdates": [{"channelId": "ch1", "state": "qr"}, {"channelId": "zzz", "state": "active"}],
        "statuses": [{"messageId": "x", "status": "error", "error": {"error": "BAD_CONTACT"}},
                     {"messageId": "y", "status": "read"}],
    }
    response = client.post("/webhook/wazzup/b1?token=s3cret", json=body)
    assert response.json() == {"status": "ok"}
    assert calls["state"] == [("b1", "qr")]
    assert calls["delivery"] == [("x", {"error": "BAD_CONTACT"})]


def test_parse_wazzup_body_pure():
    events = webhooks.parse_wazzup_body({"test": True, "statuses": [{"status": "error", "messageId": "a"}]}, BOT)
    assert [e["kind"] for e in events] == ["test", "status_error"]
    assert webhooks.parse_wazzup_body("мусор", BOT) == []
    # Бот без канала — сообщения не принимаем
    assert webhooks.parse_wazzup_body({"messages": [msg(channelId="any")]}, {"id": "b1"}) == []


def test_is_token_valid():
    assert webhooks.is_token_valid("abc", "abc") is True
    assert webhooks.is_token_valid("abd", "abc") is False
    assert webhooks.is_token_valid("", "abc") is False
    assert webhooks.is_token_valid("abc", None) is False
    assert webhooks.is_token_valid("ключ", "ключ") is True


def test_start_background_runs_in_parallel_and_catches_errors():
    """Задачи идут параллельно, ошибка в одной не мешает другим, ссылки на задачи чистятся."""
    order = []

    async def slow(name):
        await asyncio.sleep(0.05)
        order.append(name)

    async def broken():
        raise RuntimeError("упал обработчик")

    async def scenario():
        webhooks.start_background(slow, "a")
        webhooks.start_background(broken)
        webhooks.start_background(slow, "b")
        assert len(webhooks._running_tasks) == 3
        await asyncio.sleep(0.1)  # обе медленные задачи успели — значит, шли одновременно

    asyncio.run(scenario())
    assert sorted(order) == ["a", "b"]
    assert len(webhooks._running_tasks) == 0


# ---------------- Telegram ----------------

TELEGRAM_BODY = {"message": {"message_id": 7, "chat": {"id": 123, "type": "private"}, "from": {"first_name": "Иван"}, "text": "Привет"}}


def telegram_secret(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def test_telegram_webhook(monkeypatch):
    client, calls = make_client(monkeypatch)
    monkeypatch.setattr(webhooks, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(webhooks, "find_telegram_bot", lambda: {"id": "b1"})
    headers = {"X-Telegram-Bot-Api-Secret-Token": telegram_secret("123:abc")}
    assert client.post("/webhook/telegram", json=TELEGRAM_BODY, headers=headers).json() == {"status": "ok"}
    assert calls["client"][0][:3] == ("telegram", "123", "Привет")
    assert calls["client"][0][3]["external_id"] == "tg:123:7"

    body = {"message": dict(TELEGRAM_BODY["message"], text="/start")}
    assert client.post("/webhook/telegram", json=body, headers=headers).json() == {"status": "command_ignored"}

    # Сообщения из групп (например, чат уведомлений сотрудников) клиентскими не считаем
    group = {"message": dict(TELEGRAM_BODY["message"], chat={"id": -100500, "type": "supergroup"})}
    assert client.post("/webhook/telegram", json=group, headers=headers).json() == {"status": "not_private_ignored"}
    assert len(calls["client"]) == 1


def test_telegram_rejected_without_token(monkeypatch):
    """Без TELEGRAM_BOT_TOKEN проверить подлинность нечем — вебхук ничего не принимает."""
    client, calls = make_client(monkeypatch)
    monkeypatch.setattr(webhooks, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(webhooks, "find_telegram_bot", lambda: {"id": "b1"})
    assert client.post("/webhook/telegram", json=TELEGRAM_BODY).json() == {"status": "unauthorized"}
    assert calls["client"] == []


def test_telegram_secret_required(monkeypatch):
    client, calls = make_client(monkeypatch)
    monkeypatch.setattr(webhooks, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(webhooks, "find_telegram_bot", lambda: {"id": "b1"})
    # Без заголовка и с неверным заголовком — отказ
    assert client.post("/webhook/telegram", json=TELEGRAM_BODY).json() == {"status": "unauthorized"}
    bad = {"X-Telegram-Bot-Api-Secret-Token": "wrong"}
    assert client.post("/webhook/telegram", json=TELEGRAM_BODY, headers=bad).json() == {"status": "unauthorized"}
    assert calls["client"] == []


def test_find_telegram_bot_strict(monkeypatch):
    from tests._fake_supabase import FakeSupabase
    db = {"bots": [
        {"id": "b-other", "telegram_enabled": True, "system_prompt": "чужой бот с флагом"},
        {"id": "b-tg", "telegram_enabled": False},
    ]}
    monkeypatch.setattr(webhooks, "supabase", FakeSupabase(db))
    # Без TELEGRAM_BOT_ID канал выключен — флаг telegram_enabled в БД ничего не значит
    monkeypatch.setattr(webhooks, "TELEGRAM_BOT_ID", "")
    assert webhooks.find_telegram_bot() is None
    # TELEGRAM_BOT_ID задаёт администратор в env — берём строго этого бота
    monkeypatch.setattr(webhooks, "TELEGRAM_BOT_ID", "b-tg")
    assert webhooks.find_telegram_bot()["id"] == "b-tg"
    # Несуществующий id — никого (никакого запасного бота)
    monkeypatch.setattr(webhooks, "TELEGRAM_BOT_ID", "b-missing")
    assert webhooks.find_telegram_bot() is None


def test_setup_telegram_webhook_requires_admin(monkeypatch):
    client, _calls = make_client(monkeypatch)
    response = client.post("/api/setup-telegram-webhook")
    assert response.status_code in (401, 403)


# ---------------- main.py ----------------

def test_main_lifespan_starts_scheduler_and_recovery(monkeypatch):
    """Приложение стартует, планировщик и восстановление запускаются через lifespan."""
    from app import main

    started = []

    async def fake_scheduler():
        started.append("scheduler")

    async def fake_recovery():
        started.append("recovery")

    monkeypatch.setattr(main, "run_scheduler_forever", fake_scheduler)
    monkeypatch.setattr(main, "recover_after_start", fake_recovery)
    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
    assert sorted(started) == ["recovery", "scheduler"]


def test_resolve_static_file_blocks_traversal(tmp_path):
    from app import main

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("index")
    (static / "logo.png").write_text("png")
    (tmp_path / "secret.py").write_text("SECRET")

    assert main.resolve_static_file(str(static), "logo.png") == os.path.realpath(static / "logo.png")
    assert main.resolve_static_file(str(static), "../secret.py") is None
    assert main.resolve_static_file(str(static), str(tmp_path / "secret.py")) is None
    assert main.resolve_static_file(str(static), "/etc/passwd") is None
    assert main.resolve_static_file(str(static), "nope.txt") is None
    assert main.resolve_static_file(str(static), "") is None


def test_static_route_does_not_leak_sources():
    from app import main

    if not os.path.exists(main.static_dir):
        return
    with TestClient(main.app) as client:
        for path in ["/..%2Fapp%2Fconfig.py", "/%2Fetc%2Fpasswd", "/../app/config.py"]:
            response = client.get(path)
            assert "load_dotenv" not in response.text
            assert "root:" not in response.text


def test_access_log_hides_token():
    from app import main

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "POST", "/webhook/wazzup/b1?token=SUPERSECRET&x=1", "1.1", 200), None,
    )
    assert main.hide_webhook_token(record) is True
    assert "SUPERSECRET" not in record.getMessage()
    assert "token=***&x=1" in record.getMessage()
    assert main.hide_webhook_token in logging.getLogger("uvicorn.access").filters
