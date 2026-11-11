"""
Тесты API диалогов (app/routes/dialogs.py): чистые функции + эндпоинты
через TestClient на поддельной базе FakeDB. Отправка клиенту и сохранение
сообщения (messenger / pipeline) подменяются — сети нет.
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Импорт test_access первым — он подставляет фиктивные ключи Supabase
from tests.test_access import FakeDB, sample_tables, ASTANA, ALMATY, MISSING, MGR_ALL, STRANGER
from app import access
from app.auth import get_current_user
from app.routes import dialogs as dialogs_routes


def make_client(monkeypatch, user_id="owner", email="owner@test.kz", tables=None):
    db = FakeDB(tables or sample_tables())
    monkeypatch.setattr(access, "supabase", db)
    monkeypatch.setattr(dialogs_routes, "supabase", db)
    app = FastAPI()
    app.include_router(dialogs_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "email": email}
    return TestClient(app), db


ACCESS_OWNER = {"bot": {"id": "bot1", "user_id": "owner"}, "role": "owner", "city_id": None, "member": None}
ACCESS_MGR_ASTANA = {"bot": {"id": "bot1", "user_id": "owner"}, "role": "manager", "city_id": ASTANA, "member": {}}
ACCESS_MGR_ALL = {"bot": {"id": "bot1", "user_id": "owner"}, "role": "manager", "city_id": None, "member": {}}


# ============================================================
# Чистые функции
# ============================================================

def test_effective_city_filter():
    assert dialogs_routes.effective_city_filter(ACCESS_OWNER, None) is None
    assert dialogs_routes.effective_city_filter(ACCESS_OWNER, "") is None
    assert dialogs_routes.effective_city_filter(ACCESS_OWNER, ALMATY) == ALMATY
    assert dialogs_routes.effective_city_filter(ACCESS_OWNER, "none") == "none"
    assert dialogs_routes.effective_city_filter(ACCESS_MGR_ALL, ALMATY) == ALMATY
    # Менеджер города всегда ограничен своим городом
    assert dialogs_routes.effective_city_filter(ACCESS_MGR_ASTANA, ALMATY) == ASTANA
    assert dialogs_routes.effective_city_filter(ACCESS_MGR_ASTANA, None) == ASTANA
    # Невалидный uuid — 400 (а не 500 от базы)
    with pytest.raises(HTTPException) as error:
        dialogs_routes.effective_city_filter(ACCESS_OWNER, "zzz")
    assert error.value.status_code == 400


def test_filter_dialogs_for_access():
    dialogs = [
        {"id": 1, "bot_id": "bot1", "city_id": ASTANA},
        {"id": 2, "bot_id": "bot1", "city_id": ALMATY},
        {"id": 3, "bot_id": "bot1", "city_id": None},
        {"id": 4, "bot_id": "bot2", "city_id": ASTANA},
    ]
    assert [d["id"] for d in dialogs_routes.filter_dialogs_for_access(ACCESS_MGR_ASTANA, dialogs)] == [1]
    assert [d["id"] for d in dialogs_routes.filter_dialogs_for_access(ACCESS_MGR_ALL, dialogs)] == [1, 2, 3]
    assert [d["id"] for d in dialogs_routes.filter_dialogs_for_access(ACCESS_OWNER, dialogs)] == [1, 2, 3]


def test_clean_search():
    assert dialogs_routes.clean_search(None) == ""
    assert dialogs_routes.clean_search("  7701,(a)*%  ") == "7701 a"
    assert dialogs_routes.clean_search("Айгерим") == "Айгерим"


def test_build_search_filter():
    # Номер из UI с пробелами и плюсом — по chat_id ищем только цифры
    assert dialogs_routes.build_search_filter("+7 701 123") == "chat_id.ilike.*7701123*,contact_name.ilike.*+7 701 123*"
    # Нет цифр — условия по chat_id нет
    assert dialogs_routes.build_search_filter("Айгерим") == "contact_name.ilike.*Айгерим*"
    assert dialogs_routes.build_search_filter("") == ""
    assert dialogs_routes.build_search_filter(None) == ""


def test_build_status_update():
    now = "2026-11-10T10:00:00+00:00"
    assert dialogs_routes.build_status_update({"status": "hot"}, "invoice_sent", now) == {
        "status": "invoice_sent", "invoice_sent_at": now}
    assert dialogs_routes.build_status_update({"status": "invoice_sent"}, "paid", now) == {
        "status": "paid", "paid_at": now, "needs_attention": False}
    assert dialogs_routes.build_status_update({"status": "hot"}, "lost", now) == {
        "status": "lost", "needs_attention": False}
    assert dialogs_routes.build_status_update({"status": "in_progress"}, "hot", now) == {
        "status": "hot", "hot_at": now, "reminder_count": 0}
    # Повторная отметка «Счёт отправлен» не сдвигает время
    same = dialogs_routes.build_status_update(
        {"status": "invoice_sent", "invoice_sent_at": "old"}, "invoice_sent", now)
    assert same == {"status": "invoice_sent"}
    with pytest.raises(HTTPException) as error:
        dialogs_routes.build_status_update({"status": "new"}, "deleted", now)
    assert error.value.status_code == 400


def test_compute_stats():
    cities = [{"id": ASTANA, "name": "Астана"}, {"id": ALMATY, "name": "Алматы"}]
    dialogs = [
        {"status": "paid", "city_id": ASTANA},
        {"status": "hot", "city_id": ASTANA},
        {"status": "invoice_sent", "city_id": ASTANA},
        {"status": "lost", "city_id": ASTANA},
        {"status": "new", "city_id": None},
    ]
    stats = dialogs_routes.compute_stats(dialogs, cities)
    assert stats["total"] == 5
    assert stats["by_status"] == {"new": 1, "in_progress": 0, "hot": 1, "invoice_sent": 1, "paid": 1, "lost": 1}
    assert stats["conversion_paid"] == 20.0
    assert stats["by_city"][0] == {"city_id": ASTANA, "city_name": "Астана", "total": 4, "hot": 1,
                                   "invoice_sent": 1, "paid": 1, "lost": 1, "conversion_paid": 25.0}
    assert stats["by_city"][1]["total"] == 0 and stats["by_city"][1]["conversion_paid"] == 0.0
    assert stats["by_city"][2]["city_id"] is None and stats["by_city"][2]["city_name"] == "Без города"

    empty = dialogs_routes.compute_stats([], [])
    assert empty["total"] == 0 and empty["conversion_paid"] == 0.0 and empty["by_city"] == []


# ============================================================
# Эндпоинты
# ============================================================

def test_list_dialogs_owner_counts_and_order(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    data = client.get("/api/bots/bot1/dialogs").json()
    # Сортировка по last_message_at desc, без сообщений — в конце
    assert [d["id"] for d in data["dialogs"]] == ["d2", "d1", "d3"]
    assert data["dialogs"][1]["city_name"] == "Астана"
    assert data["dialogs"][2]["city_name"] is None
    assert data["counts"]["all"] == 3
    assert data["counts"]["hot"] == 1 and data["counts"]["paid"] == 1 and data["counts"]["new"] == 1
    assert data["counts"]["attention"] == 1 and data["counts"]["invoice_sent"] == 0

    assert [d["id"] for d in client.get("/api/bots/bot1/dialogs?status=hot").json()["dialogs"]] == ["d1"]
    assert [d["id"] for d in client.get("/api/bots/bot1/dialogs?attention=1").json()["dialogs"]] == ["d1"]
    assert [d["id"] for d in client.get("/api/bots/bot1/dialogs?city_id=none").json()["dialogs"]] == ["d3"]
    assert [d["id"] for d in client.get("/api/bots/bot1/dialogs?search=иван").json()["dialogs"]] == ["d2"]
    assert [d["id"] for d in client.get("/api/bots/bot1/dialogs?search=0003").json()["dialogs"]] == ["d3"]
    assert client.get("/api/bots/bot1/dialogs?status=bad").status_code == 400
    assert client.get("/api/bots/bot1/dialogs?city_id=zzz").status_code == 400
    # Поиск по номеру в формате UI
    assert [d["id"] for d in client.get("/api/bots/bot1/dialogs?search=%2B7 701 000 00 02").json()["dialogs"]] == ["d2"]


def test_list_dialogs_manager_city_only(monkeypatch):
    client, db = make_client(monkeypatch, "mgr_astana")
    data = client.get(f"/api/bots/bot1/dialogs?city_id={ALMATY}").json()
    assert [d["id"] for d in data["dialogs"]] == ["d1"]
    assert data["counts"]["all"] == 1 and data["counts"]["paid"] == 0

    client, db = make_client(monkeypatch, STRANGER)
    assert client.get("/api/bots/bot1/dialogs").status_code == 403


def test_messages_and_access(monkeypatch):
    tables = sample_tables()
    tables["messages"] = [
        {"id": "m2", "dialog_id": "d1", "role": "assistant", "content": "Здравствуйте", "created_at": "2"},
        {"id": "m1", "dialog_id": "d1", "role": "user", "content": "Привет", "created_at": "1"},
        {"id": "m3", "dialog_id": "d2", "role": "user", "content": "чужое", "created_at": "1"},
    ]
    client, db = make_client(monkeypatch, "mgr_astana", tables=tables)
    data = client.get("/api/dialogs/d1/messages").json()
    assert [m["id"] for m in data["messages"]] == ["m1", "m2"]
    assert data["dialog"]["bot_id"] == "bot1" and data["dialog"]["city_name"] == "Астана"
    assert client.get("/api/dialogs/d2/messages").status_code == 403
    assert client.get("/api/dialogs/zzz/messages").status_code == 404


def test_toggle_ai(monkeypatch):
    client, db = make_client(monkeypatch, "mgr_astana")
    started = []

    async def fake_reply(bot_id, dialog_id):
        started.append((bot_id, dialog_id))

    monkeypatch.setattr(dialogs_routes.pipeline, "process_dialog_reply", fake_reply)

    data = client.post("/api/dialogs/d1/toggle-ai").json()
    assert data["ai_disabled"] is True and data["ai_paused_reason"] == "manual"
    # Клиент не ждёт ответа (сообщений нет) — ответ ИИ не запускаем
    data = client.post("/api/dialogs/d1/toggle-ai").json()
    assert data["ai_disabled"] is False and data["ai_paused_reason"] is None
    row = [d for d in db.tables["dialogs"] if d["id"] == "d1"][0]
    assert row["needs_attention"] is False
    assert started == []

    # Последнее сообщение — от клиента: при включении ИИ сразу отвечает
    db.tables["messages"] = [
        {"id": "x1", "dialog_id": "d1", "role": "assistant", "content": "a", "created_at": "1"},
        {"id": "x2", "dialog_id": "d1", "role": "user", "content": "ау", "created_at": "2"},
    ]
    client.post("/api/dialogs/d1/toggle-ai")
    client.post("/api/dialogs/d1/toggle-ai")
    assert started == [("bot1", "d1")]
    assert dialogs_routes.background_tasks == set()


def test_toggle_ai_limit(monkeypatch):
    tables = sample_tables()
    tables["dialogs"][0].update({"ai_disabled": True, "ai_paused_reason": "limit"})
    client, db = make_client(monkeypatch, "owner", tables=tables)
    monkeypatch.setattr(dialogs_routes.pipeline, "count_owner_dialogs_this_month", lambda owner_id: 101)
    # Подписку читаем через pipeline (самая свежая, с учётом expires_at) — берём из фейковой БД
    monkeypatch.setattr(
        dialogs_routes.pipeline, "get_owner_subscription",
        lambda owner_id: next((s for s in db.tables.get("subscriptions", []) if s["user_id"] == owner_id), None),
    )

    async def fake_reply(bot_id, dialog_id):
        return None

    monkeypatch.setattr(dialogs_routes.pipeline, "process_dialog_reply", fake_reply)

    # Лимит по умолчанию 100, диалогов 101 — включать нельзя
    response = client.post("/api/dialogs/d1/toggle-ai")
    assert response.status_code == 403
    assert response.json()["detail"] == "Лимит диалогов по подписке исчерпан — увеличьте тариф"

    # Подписка истекла — тоже нельзя, даже с большим лимитом
    db.tables["subscriptions"] = [{"user_id": "owner", "status": "active", "max_dialogs_per_month": 3000,
                                   "expires_at": "2020-01-01T00:00:00+00:00"}]
    assert client.post("/api/dialogs/d1/toggle-ai").status_code == 403

    # Тариф увеличили — можно
    db.tables["subscriptions"] = [{"user_id": "owner", "status": "active", "max_dialogs_per_month": 3000}]
    data = client.post("/api/dialogs/d1/toggle-ai").json()
    assert data["ai_disabled"] is False


def test_set_status(monkeypatch):
    client, db = make_client(monkeypatch, "mgr_astana")
    data = client.post("/api/dialogs/d1/status", json={"status": "invoice_sent"}).json()
    assert data["dialog"]["status"] == "invoice_sent" and data["dialog"]["invoice_sent_at"]
    assert client.post("/api/dialogs/d1/status", json={"status": "bad"}).status_code == 400
    assert client.post("/api/dialogs/d2/status", json={"status": "paid"}).status_code == 403


def test_send_message_success(monkeypatch):
    client, db = make_client(monkeypatch, "mgr_astana", email="astana@test.kz")
    calls = {}

    async def fake_send(bot, dialog, text, from_bot=True):
        calls["send"] = (bot.get("wazzup_api_key"), dialog["id"], text, from_bot)
        # ИИ уже на паузе в момент отправки — бот не ответит параллельно сотруднику
        row = [d for d in db.tables["dialogs"] if d["id"] == "d1"][0]
        calls["paused_before_send"] = row["ai_disabled"] and row["ai_paused_reason"] == "manager_replied"
        return {"ok": True, "message_id": "wz-1", "error": ""}

    def fake_save(dialog_id, role, content, message_type="text", media_url=None,
                  external_id=None, author_name=None, send_error=None):
        calls["save"] = (dialog_id, role, content, external_id, author_name)
        return {"id": "msg1", "dialog_id": dialog_id, "role": role, "content": content}

    monkeypatch.setattr(dialogs_routes.messenger, "send_to_client", fake_send)
    monkeypatch.setattr(dialogs_routes.pipeline, "save_message", fake_save)

    response = client.post("/api/dialogs/d1/send", json={"text": "  Счёт отправлен  "})
    assert response.status_code == 200
    data = response.json()
    # Полный бот (с ключом) передан в messenger, но наружу ключ не ушёл
    assert calls["send"] == ("SECRET_KEY", "d1", "Счёт отправлен", False)
    assert calls["save"] == ("d1", "manager", "Счёт отправлен", "wz-1", "astana@test.kz")
    assert calls["paused_before_send"] is True
    assert "SECRET_KEY" not in response.text
    assert data["message"]["id"] == "msg1"
    assert data["dialog"]["ai_disabled"] is True
    assert data["dialog"]["ai_paused_reason"] == "manager_replied"


def test_send_message_errors(monkeypatch):
    client, db = make_client(monkeypatch, "owner")

    async def fake_send(bot, dialog, text, from_bot=True):
        return {"ok": False, "message_id": "", "error": "Wazzup не подключён"}

    def fake_save(*args, **kwargs):
        raise AssertionError("save_message не должен вызываться при ошибке отправки")

    monkeypatch.setattr(dialogs_routes.messenger, "send_to_client", fake_send)
    monkeypatch.setattr(dialogs_routes.pipeline, "save_message", fake_save)

    response = client.post("/api/dialogs/d1/send", json={"text": "привет"})
    assert response.status_code == 502
    assert "Wazzup не подключён" in response.json()["detail"]
    assert client.post("/api/dialogs/d1/send", json={"text": "   "}).status_code == 400
    # Пауза ИИ при ошибке отправки не откатывается
    row = [d for d in db.tables["dialogs"] if d["id"] == "d1"][0]
    assert row["ai_disabled"] is True and row["ai_paused_reason"] == "manager_replied"


def test_set_city_manager_restrictions(monkeypatch):
    client, db = make_client(monkeypatch, "mgr_astana")
    assert client.post("/api/dialogs/d1/city", json={"city_id": ALMATY}).status_code == 403
    assert client.post("/api/dialogs/d1/city", json={"city_id": None}).status_code == 403
    assert client.post("/api/dialogs/d1/city", json={"city_id": ASTANA}).status_code == 200
    # Менеджер всех городов может переносить
    client, db = make_client(monkeypatch, MGR_ALL)
    assert client.post("/api/dialogs/d3/city", json={"city_id": ALMATY}).status_code == 200
    assert client.post("/api/dialogs/d3/city", json={"city_id": "zzz"}).status_code == 400


def test_set_city_assign_resolve_delete(monkeypatch):
    client, db = make_client(monkeypatch, "owner")
    assert client.post("/api/dialogs/d3/city", json={"city_id": ALMATY}).json()["dialog"]["city_name"] == "Алматы"
    assert client.post("/api/dialogs/d3/city", json={"city_id": None}).json()["dialog"]["city_id"] is None
    assert client.post("/api/dialogs/d3/city", json={"city_id": MISSING}).status_code == 404

    assert client.post("/api/dialogs/d1/assign", json={"user_id": MGR_ALL}).json()["dialog"]["assigned_user_id"] == MGR_ALL
    assert client.post("/api/dialogs/d1/assign", json={"user_id": STRANGER}).status_code == 400
    assert client.post("/api/dialogs/d1/assign", json={"user_id": "not-uuid"}).status_code == 400
    assert client.post("/api/dialogs/d1/assign", json={"user_id": None}).json()["dialog"]["assigned_user_id"] is None

    data = client.post("/api/dialogs/d1/resolve-attention").json()
    assert data["dialog"]["needs_attention"] is False

    manager, _ = make_client(monkeypatch, MGR_ALL, tables=db.tables)
    assert manager.post("/api/dialogs/d1/assign", json={"user_id": MGR_ALL}).status_code == 403
    assert manager.delete("/api/dialogs/d1").status_code == 403

    # make_client подменяет общую базу — снова создаём клиента владельца
    client, db = make_client(monkeypatch, "owner", tables=db.tables)
    db.tables["messages"] = [{"id": "m1", "dialog_id": "d1", "content": "x", "created_at": "1"}]
    assert client.delete("/api/dialogs/d1").json() == {"status": "deleted"}
    assert db.tables["messages"] == []
    assert [d["id"] for d in db.tables["dialogs"]] == ["d2", "d3"]


def test_stats_endpoint(monkeypatch):
    tables = sample_tables()
    # Свежие даты, чтобы попасть в период
    for dialog in tables["dialogs"]:
        dialog["created_at"] = "2999-01-01T00:00:00+00:00"
    tables["dialogs"].append({"id": "old", "bot_id": "bot1", "status": "paid", "city_id": ASTANA,
                              "created_at": "2000-01-01T00:00:00+00:00"})
    client, db = make_client(monkeypatch, "owner", tables=tables)
    stats = client.get("/api/bots/bot1/stats?days=30").json()
    assert stats["total"] == 3
    assert stats["by_status"]["paid"] == 1
    assert [row["city_name"] for row in stats["by_city"]] == ["Астана", "Алматы", "Без города"]
    assert stats["conversion_paid"] == pytest.approx(33.3)

    manager, _ = make_client(monkeypatch, "mgr_astana", tables=tables)
    stats = manager.get("/api/bots/bot1/stats").json()
    assert stats["total"] == 1
    assert [row["city_id"] for row in stats["by_city"]] == [ASTANA]
