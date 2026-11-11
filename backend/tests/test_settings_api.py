"""
Тесты API настроек (app/routes/settings.py): чистые функции + эндпоинты
через TestClient на поддельной базе FakeDB. Сеть не используется.
"""

import json
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Импорт test_access первым — он подставляет фиктивные ключи Supabase
from tests.test_access import FakeDB, sample_tables, ASTANA, ALMATY, MISSING, MGR_ALL, STRANGER
from app import access
from app.auth import get_current_user
from app.routes import settings as settings_routes


SECRETS = ["SECRET_KEY", "SECRET_HOOK", "NEW_KEY"]


def assert_no_secrets(response):
    """В ответе API не должно быть ни ключа Wazzup, ни секрета вебхука."""
    text = response.text
    assert "wazzup_api_key" not in text
    assert "wazzup_webhook_secret" not in text
    for secret in SECRETS:
        assert secret not in text


def make_client(monkeypatch, user_id="owner", email="owner@test.kz", tables=None):
    db = FakeDB(tables or sample_tables())
    monkeypatch.setattr(access, "supabase", db)
    monkeypatch.setattr(settings_routes, "supabase", db)
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "email": email}
    return TestClient(app), db


# ============================================================
# Чистые функции
# ============================================================

def test_validate_bot_update_whitelist():
    clean = settings_routes.validate_bot_update({
        "name": "  Клиника  ", "system_prompt": "Будь вежлив", "ai_model": "claude-sonnet-5-5",
        "invoice_reminder_minutes": "15", "assignment_mode": "round_robin",
        "telegram_notify_chat_id": " -1001234 ",
        # Эти поля менять через PUT нельзя — должны быть отброшены
        # (telegram_enabled — тоже: Telegram-канал привязывается через env)
        "telegram_enabled": True, "user_id": "hacker", "id": "x", "wazzup_api_key": "k", "wazzup_webhook_secret": "s",
        "wazzup_channel_id": "c",
    })
    assert clean == {
        "name": "Клиника", "system_prompt": "Будь вежлив", "ai_model": "claude-sonnet-5-5",
        "invoice_reminder_minutes": 15, "assignment_mode": "round_robin",
        "telegram_notify_chat_id": "-1001234",
    }


def test_validate_business_profile():
    clean = settings_routes.validate_bot_update({
        "business_type": "general", "company_name": "  Мебель Плюс ", "assistant_name": "Алия",
        "business_description": None, "target_action": "оформить заказ",
    })
    assert clean == {
        "business_type": "general", "company_name": "Мебель Плюс", "assistant_name": "Алия",
        "business_description": "", "target_action": "оформить заказ",
    }
    assert settings_routes.validate_bot_update({"business_type": "clinic"}) == {"business_type": "clinic"}


@pytest.mark.parametrize("data", [
    {"business_type": "casino"},
    {"company_name": 123},
    {"company_name": "x" * 101},
    {"assistant_name": "x" * 51},
    {"business_description": "x" * 3001},
    {"target_action": ["заказ"]},
    {"ai_model": "gpt-4o"},
    {"assignment_mode": "random"},
    {"invoice_reminder_minutes": 0},
    {"invoice_reminder_minutes": 241},
    {"invoice_reminder_minutes": True},
    {"invoice_reminder_minutes": "abc"},
    {"name": "   "},
    {"telegram_notify_chat_id": "мой чат"},
])
def test_validate_bot_update_errors(data):
    with pytest.raises(HTTPException) as error:
        settings_routes.validate_bot_update(data)
    assert error.value.status_code == 400


def test_validate_bot_update_empty_chat_id_is_none():
    assert settings_routes.validate_bot_update({"telegram_notify_chat_id": ""}) == {"telegram_notify_chat_id": None}
    assert settings_routes.normalize_chat_id(123456) == "123456"
    assert settings_routes.normalize_chat_id("@clinic_channel") == "@clinic_channel"


def test_validate_city_data():
    clean = settings_routes.validate_city_data({"name": " Астана ", "aliases": "нур-султан", "sort_order": "2",
                                                "telegram_chat_id": "", "bot_id": "other"})
    assert clean == {"name": "Астана", "aliases": "нур-султан", "sort_order": 2, "telegram_chat_id": None}
    with pytest.raises(HTTPException):
        settings_routes.validate_city_data({"aliases": "x"})
    # При частичном обновлении имя не обязательно
    assert settings_routes.validate_city_data({"address": "ул. Абая 1"}, partial=True) == {"address": "ул. Абая 1"}


def test_split_text_into_chunks_limits_and_words():
    sentence = "Приём терапевта стоит пять тысяч тенге и длится тридцать минут. "
    text = "Короткий абзац.\n\nЕщё абзац.\n\n" + sentence * 40 + "\n\n" + "слово " * 300
    chunks = settings_routes.split_text_into_chunks(text, 800)

    assert chunks
    for chunk in chunks:
        assert len(chunk) <= 800
    # Короткие абзацы объединены в один фрагмент
    assert chunks[0] == "Короткий абзац.\n\nЕщё абзац."
    # Слова не порваны: набор слов совпадает с исходным
    assert " ".join(chunks).split() == text.split()


def test_split_text_very_long_word_and_empty():
    assert settings_routes.split_text_into_chunks("") == []
    assert settings_routes.split_text_into_chunks("   \n\n  ") == []
    chunks = settings_routes.split_text_into_chunks("a" * 2000, 800)
    assert [len(c) for c in chunks] == [800, 800, 400]


def test_filter_whatsapp_channels():
    channels = [
        {"channelId": "1", "transport": "whatsapp", "plainId": "7701", "state": "active", "extra": "x"},
        {"channelId": "2", "transport": "telegram", "plainId": "bot", "state": "active"},
        "мусор",
    ]
    assert settings_routes.filter_whatsapp_channels(channels) == [
        {"channelId": "1", "transport": "whatsapp", "plainId": "7701", "state": "active"}
    ]


def test_parse_city_id_param_and_image():
    assert settings_routes.parse_city_id_param("") is None
    assert settings_routes.parse_city_id_param("null") is None
    assert settings_routes.parse_city_id_param(f" {ASTANA} ") == ASTANA
    assert settings_routes.parse_image_content("[IMAGE:http://x/a.png] прайс.png") == {
        "url": "http://x/a.png", "name": "прайс.png"}


# ============================================================
# Эндпоинты
# ============================================================

def test_get_bots_owner_and_manager(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    response = client.get("/api/bots")
    assert response.status_code == 200
    assert_no_secrets(response)
    bot = response.json()["bots"][0]
    assert bot["my_role"] == "owner" and bot["wazzup_connected"] is True

    client, db = make_client(monkeypatch, "mgr_astana")
    response = client.get("/api/bots")
    assert_no_secrets(response)
    bot = response.json()["bots"][0]
    assert bot["my_role"] == "manager" and bot["my_city_id"] == ASTANA

    client, db = make_client(monkeypatch, STRANGER)
    assert client.get("/api/bots").json() == {"bots": []}


def test_create_bot_generates_secret(monkeypatch):
    tables = sample_tables()
    tables["subscriptions"] = [{"user_id": "newbie", "max_bots": 1, "status": "active"}]
    client, db = make_client(monkeypatch, "newbie", tables=tables)
    response = client.post("/api/bots", json={"name": "Мой бот"})
    assert response.status_code == 200
    assert_no_secrets(response)
    created = [b for b in db.tables["bots"] if b["user_id"] == "newbie"][0]
    assert created["wazzup_webhook_secret"]
    assert created["wazzup_webhook_secret"] not in response.text

    # Лимит 1 бот — второй создать нельзя
    response = client.post("/api/bots", json={"name": "Ещё"})
    assert response.status_code == 403


def test_update_bot_owner_only_and_whitelist(monkeypatch):
    client, db = make_client(monkeypatch, MGR_ALL)
    response = client.put("/api/bots/bot1", json={"name": "Взлом"})
    assert response.status_code == 403
    assert db.tables["bots"][0]["name"] == "Клиника"

    client, db = make_client(monkeypatch, "owner")
    response = client.put("/api/bots/bot1", json={
        "name": "Новое имя", "user_id": "hacker", "wazzup_api_key": "NEW_KEY", "invoice_reminder_minutes": 20})
    assert response.status_code == 200
    assert_no_secrets(response)
    row = db.tables["bots"][0]
    assert row["name"] == "Новое имя" and row["invoice_reminder_minutes"] == 20
    assert row["user_id"] == "owner" and row["wazzup_api_key"] == "SECRET_KEY"

    response = client.put("/api/bots/bot1", json={"ai_model": "gpt-4o"})
    assert response.status_code == 400


def test_wazzup_connect_and_channel(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    webhook_calls = []

    async def fake_get_channels(api_key):
        return {"ok": api_key == "NEW_KEY", "error": "" if api_key == "NEW_KEY" else "Неверный ключ",
                "channels": [{"channelId": "ch2", "transport": "whatsapp", "plainId": "77015556677", "state": "active"},
                             {"channelId": "tg", "transport": "telegram", "plainId": "x", "state": "active"}]}

    async def fake_set_webhook(api_key, url):
        webhook_calls.append((api_key, url))
        return {"ok": True, "error": ""}

    monkeypatch.setattr(settings_routes.wazzup, "get_channels", fake_get_channels)
    monkeypatch.setattr(settings_routes.wazzup, "set_webhook", fake_set_webhook)

    # Неверный ключ — не сохраняем
    response = client.post("/api/bots/bot1/wazzup/connect", json={"api_key": "BAD"})
    assert response.json()["ok"] is False
    assert db.tables["bots"][0]["wazzup_api_key"] == "SECRET_KEY"

    response = client.post("/api/bots/bot1/wazzup/connect", json={"api_key": "NEW_KEY"})
    data = response.json()
    assert_no_secrets(response)
    assert data["ok"] is True
    assert [c["channelId"] for c in data["channels"]] == ["ch2"]
    # connect только проверяет ключ — рабочее подключение не тронуто
    row = db.tables["bots"][0]
    assert row["wazzup_api_key"] == "SECRET_KEY" and row["wazzup_channel_id"] == "ch1"

    # Канала нет в аккаунте — ничего не сохраняем
    response = client.post("/api/bots/bot1/wazzup/channel", json={"channel_id": "nope", "api_key": "NEW_KEY"})
    assert response.json()["ok"] is False
    assert db.tables["bots"][0]["wazzup_api_key"] == "SECRET_KEY"
    # Неверный новый ключ — тоже ничего не сохраняем
    response = client.post("/api/bots/bot1/wazzup/channel", json={"channel_id": "ch2", "api_key": "BAD"})
    assert response.json()["ok"] is False
    assert db.tables["bots"][0]["wazzup_channel_id"] == "ch1"

    response = client.post("/api/bots/bot1/wazzup/channel", json={"channel_id": "ch2", "api_key": "NEW_KEY"})
    data = response.json()
    assert_no_secrets(response)
    assert data["ok"] is True and data["bot"]["wazzup_connected"] is True
    assert data["bot"]["wazzup_channel_phone"] == "77015556677"
    assert webhook_calls[-1][0] == "NEW_KEY"
    assert db.tables["bots"][0]["wazzup_api_key"] == "NEW_KEY"
    assert webhook_calls[-1][1].endswith("/webhook/wazzup/bot1?token=SECRET_HOOK")

    # Менеджеру подключать Wazzup нельзя
    client2, db2 = make_client(monkeypatch, MGR_ALL)
    assert client2.post("/api/bots/bot1/wazzup/connect", json={"api_key": "NEW_KEY"}).status_code == 403


def test_wazzup_status_and_disconnect(monkeypatch):
    client, db = make_client(monkeypatch, "mgr_astana")

    async def fake_get_channels(api_key):
        return {"ok": True, "error": "", "channels": [{"channelId": "ch1", "transport": "whatsapp", "state": "qr"}]}

    async def fake_get_webhook(api_key):
        return {"ok": True, "error": "", "data": {"webhooksUri": settings_routes.wazzup.build_webhook_url("bot1", "SECRET_HOOK")}}

    monkeypatch.setattr(settings_routes.wazzup, "get_channels", fake_get_channels)
    monkeypatch.setattr(settings_routes.wazzup, "get_webhook", fake_get_webhook)

    response = client.get("/api/bots/bot1/wazzup/status")
    assert_no_secrets(response)
    assert response.json() == {"connected": True, "channel_id": "ch1", "phone": None, "state": "qr", "webhook_ok": True}
    assert db.tables["bots"][0]["wazzup_channel_state"] == "qr"

    assert client.post("/api/bots/bot1/wazzup/disconnect").status_code == 403
    client, db = make_client(monkeypatch, "owner")
    response = client.post("/api/bots/bot1/wazzup/disconnect")
    assert_no_secrets(response)
    assert response.json()["bot"]["wazzup_connected"] is False
    assert db.tables["bots"][0]["wazzup_api_key"] is None
    # Секрет вебхука сменён — старый адрес вебхука больше не пройдёт проверку
    assert db.tables["bots"][0]["wazzup_webhook_secret"] not in ("SECRET_HOOK", None, "")


def test_cities_crud_and_access(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    db.tables["knowledge_chunks"] = [
        {"id": "k1", "bot_id": "bot1", "city_id": ASTANA, "content": "цены Астаны"},
        {"id": "k2", "bot_id": "bot1", "city_id": ASTANA, "content": "врачи Астаны"},
        {"id": "k3", "bot_id": "bot1", "city_id": None, "content": "общее"},
        {"id": "img", "bot_id": "bot1", "city_id": None, "content": "[IMAGE:http://x] a.png"},
    ]
    cities = client.get("/api/bots/bot1/cities").json()["cities"]
    assert [(c["name"], c["chunks_count"]) for c in cities] == [("Астана", 2), ("Алматы", 0)]

    summary = client.get("/api/bots/bot1/knowledge/summary").json()
    assert summary == {"general": 1, "by_city": {ASTANA: 2, ALMATY: 0}}

    response = client.post("/api/bots/bot1/cities", json={"name": "Шымкент", "aliases": "шымкент, чимкент"})
    assert response.status_code == 200 and response.json()["city"]["name"] == "Шымкент"
    assert client.post("/api/bots/bot1/cities", json={"name": ""}).status_code == 400

    assert client.put(f"/api/cities/{ASTANA}", json={"address": "ул. Абая 1"}).json()["city"]["address"] == "ул. Абая 1"
    assert client.put("/api/cities/missing", json={"address": "x"}).status_code == 404

    # Менеджер видит города, но менять не может
    manager, _ = make_client(monkeypatch, "mgr_astana", tables={k: v for k, v in db.tables.items()})
    assert manager.get("/api/bots/bot1/cities").status_code == 200
    assert manager.put(f"/api/cities/{ASTANA}", json={"name": "X"}).status_code == 403
    assert manager.delete(f"/api/cities/{ASTANA}").status_code == 403
    stranger, _ = make_client(monkeypatch, STRANGER)
    assert stranger.get("/api/bots/bot1/cities").status_code == 403

    client, db = make_client(monkeypatch, "owner")
    # Сотрудники удаляемого города удаляются из команды (а не получают доступ ко всем городам)
    db.tables["team_members"].append({"id": "m3", "bot_id": "bot1", "user_id": "u3", "city_id": ALMATY})
    assert client.delete(f"/api/cities/{ALMATY}").json() == {"status": "deleted", "removed_members": 1}
    assert [c["id"] for c in db.tables["cities"]] == [ASTANA]
    assert [m["id"] for m in db.tables["team_members"]] == ["m1", "m2"]


def test_knowledge_upload_replaces_only_same_city(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    db.tables["knowledge_chunks"] = [
        {"id": "k1", "bot_id": "bot1", "city_id": ASTANA, "content": "старые цены Астаны"},
        {"id": "k2", "bot_id": "bot1", "city_id": ALMATY, "content": "цены Алматы"},
        {"id": "k3", "bot_id": "bot1", "city_id": None, "content": "общее"},
        {"id": "img", "bot_id": "bot1", "city_id": None, "content": "[IMAGE:http://x] a.png"},
    ]
    files = {"file": ("prices.txt", "Новые цены Астаны.\n\nВторой абзац.".encode("utf-8"), "text/plain")}
    response = client.post(f"/api/bots/bot1/knowledge?city_id={ASTANA}", files=files)
    assert response.status_code == 200
    assert response.json() == {"chunks_count": 1, "format": "txt", "city_id": ASTANA}

    ids = sorted(row["id"] for row in db.tables["knowledge_chunks"])
    assert "k1" not in ids and {"k2", "k3", "img"} <= set(ids)
    new_rows = [r for r in db.tables["knowledge_chunks"] if r["id"] not in ("k2", "k3", "img")]
    assert new_rows[0]["city_id"] == ASTANA
    assert new_rows[0]["content"].startswith("Новые цены Астаны.")

    # Общая база: заменяет только city_id IS NULL, картинку не трогает
    files = {"file": ("prices.csv", "Услуга,Цена\nУЗИ,8000\nАнализ,2000\n".encode("utf-8"), "text/csv")}
    response = client.post("/api/bots/bot1/knowledge?city_id=", files=files)
    assert response.json() == {"chunks_count": 2, "format": "csv", "city_id": None}
    contents = [r["content"] for r in db.tables["knowledge_chunks"]]
    assert "общее" not in contents
    assert "[IMAGE:http://x] a.png" in contents and "цены Алматы" in contents
    assert "Услуга: УЗИ | Цена: 8000" in contents

    # Город чужого бота / несуществующий — 404
    assert client.post(f"/api/bots/bot1/knowledge?city_id={MISSING}", files=files).status_code == 404
    # Невалидный uuid — 400, а не 500
    assert client.post("/api/bots/bot1/knowledge?city_id=zzz", files=files).status_code == 400
    # Неподдерживаемые форматы
    for name in ("old.xls", "doc.docx", "noext"):
        response = client.post("/api/bots/bot1/knowledge", files={"file": (name, b"data", "application/octet-stream")})
        assert response.status_code == 400
        assert response.json()["detail"] == "Поддерживаются TXT, PDF, CSV, XLSX"
    # Менеджеру загружать нельзя
    manager, _ = make_client(monkeypatch, MGR_ALL)
    assert manager.post("/api/bots/bot1/knowledge", files=files).status_code == 403


def test_images_upload_list_delete(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    # Не картинка / неподдерживаемый формат — 400
    assert client.post("/api/bots/bot1/images", files={"file": ("a.gif", b"GIF", "image/gif")}).status_code == 400
    assert client.post("/api/bots/bot1/images", files={"file": ("a.svg", b"<svg>", "image/png")}).status_code == 400
    too_big = b"0" * (10 * 1024 * 1024 + 1)
    assert client.post("/api/bots/bot1/images", files={"file": ("a.jpg", too_big, "image/jpeg")}).status_code == 400

    # Клиент прислал неверный content-type — берём тип по расширению
    response = client.post("/api/bots/bot1/images", files={"file": ("прайс.png", b"\x89PNG", "text/html")})
    assert response.status_code == 200
    assert db.storage_options[-1] == {"content-type": "image/png"}
    image_id = response.json()["id"]
    images = client.get("/api/bots/bot1/images").json()["images"]
    assert images[0]["name"] == "прайс.png" and images[0]["id"] == image_id
    assert len(db.storage_files) == 1

    manager, _ = make_client(monkeypatch, MGR_ALL, tables=db.tables)
    assert manager.delete(f"/api/images/{image_id}").status_code == 403

    client, db2 = make_client(monkeypatch, "owner", tables=db.tables)
    db2.storage_files = list(db.storage_files)
    assert client.delete(f"/api/images/{image_id}").json() == {"status": "deleted"}
    assert db2.tables["knowledge_chunks"] == []
    assert db2.storage_files == []
    assert client.delete("/api/images/missing").status_code == 404


def test_team_crud(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    members = client.get("/api/bots/bot1/team").json()["members"]
    assert [(m["email"], m["city_id"]) for m in members] == [("astana@test.kz", ASTANA), ("all@test.kz", None)]

    response = client.post("/api/bots/bot1/team", json={"email": "nobody@test.kz"})
    assert response.status_code == 404
    assert "зарегистрируется" in response.json()["detail"]

    # Email без учёта регистра
    response = client.post("/api/bots/bot1/team", json={"email": "new_guy@TEST.kz", "city_id": ALMATY,
                                                       "telegram_chat_id": "12345"})
    assert response.status_code == 200
    member = response.json()["member"]
    assert member["user_id"] == "newbie" and member["city_id"] == ALMATY

    assert client.post("/api/bots/bot1/team", json={"email": "new_guy@test.kz"}).status_code == 400
    assert client.post("/api/bots/bot1/team", json={"email": "owner@test.kz"}).status_code == 400
    assert client.post("/api/bots/bot1/team", json={"email": "all@test.kz", "city_id": MISSING}).status_code == 404
    assert client.post("/api/bots/bot1/team", json={"email": "all@test.kz", "city_id": "zzz"}).status_code == 400

    response = client.put(f"/api/team/{member['id']}", json={"city_id": None, "telegram_chat_id": ""})
    assert response.json()["member"]["city_id"] is None
    assert client.delete(f"/api/team/{member['id']}").json() == {"status": "deleted"}

    manager, _ = make_client(monkeypatch, MGR_ALL)
    assert manager.get("/api/bots/bot1/team").status_code == 403
    assert manager.delete("/api/team/m1").status_code == 403
    assert manager.put("/api/team/m1", json={"city_id": None}).status_code == 403


def test_test_notification(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    sent = []

    async def fake_send(chat_id, text_html):
        sent.append((chat_id, text_html))
        return True

    monkeypatch.setattr(settings_routes.notifier, "send_notification", fake_send)
    assert client.post("/api/bots/bot1/test-notification", json={"chat_id": "-1009"}).json()["ok"] is True
    assert sent[-1] == ("-1009", "✅ Тестовое уведомление AI Sales Manager")
    # Без chat_id — общий чат бота
    client.post("/api/bots/bot1/test-notification", json={})
    assert sent[-1][0] == "-100"

    manager, _ = make_client(monkeypatch, MGR_ALL)
    assert manager.post("/api/bots/bot1/test-notification", json={"chat_id": "1"}).status_code == 403


def test_no_waha_routes_left():
    paths = [route.path for route in settings_routes.router.routes]
    for path in paths:
        assert "/whatsapp/" not in path
    assert "/api/bots/{bot_id}/wazzup/connect" in paths


def test_auth_refresh(monkeypatch):
    from app.routes import auth as auth_routes

    class FakeSession:
        access_token = "new-access"
        refresh_token = "new-refresh"

    class FakeResponse:
        session = FakeSession()

    class FakeAuth:
        def refresh_session(self, refresh_token):
            if refresh_token != "good":
                raise Exception("Invalid Refresh Token")
            return FakeResponse()

    class FakeAuthClient:
        auth = FakeAuth()

    monkeypatch.setattr(auth_routes, "auth_client", FakeAuthClient())
    app = FastAPI()
    app.include_router(auth_routes.router)
    client = TestClient(app)

    response = client.post("/api/auth/refresh", json={"refresh_token": "good"})
    assert response.json() == {"access_token": "new-access", "refresh_token": "new-refresh"}
    assert client.post("/api/auth/refresh", json={"refresh_token": "bad"}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": ""}).status_code == 401
