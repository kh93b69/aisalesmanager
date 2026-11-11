"""Тесты планировщика напоминаний — без сети и без реальной БД."""
import asyncio
from datetime import datetime, timezone, timedelta

from tests._stubs import ensure_all

ensure_all()

from app import scheduler  # noqa: E402
from tests._fake_supabase import FakeSupabase  # noqa: E402


def test_decide_reminder_table():
    # (reminder_count, minutes_passed, N, manager_replied) → действие
    cases = [
        (0, 5, 10, False, "none"),
        (0, 10, 10, False, "reminder"),
        (0, 25, 10, False, "reminder"),       # первое напоминание всегда раньше эскалации
        (1, 15, 10, False, "none"),
        (1, 20, 10, False, "escalation"),
        (2, 500, 10, False, "none"),          # больше не напоминаем
        (0, 100, 10, True, "none"),           # сотрудник уже ответил
        (0, 10, 0, False, "reminder"),        # N не задан → 10 по умолчанию
        (0, 9, None, False, "none"),
    ]
    for count, minutes, n, replied, expected in cases:
        assert scheduler.decide_reminder(count, minutes, n, replied) == expected, (count, minutes, n, replied)


def test_parse_time():
    assert scheduler.parse_time(None) is None
    assert scheduler.parse_time("мусор") is None
    value = scheduler.parse_time("2026-11-10T09:05:00Z")
    assert value == datetime(2026, 11, 10, 9, 5, tzinfo=timezone.utc)
    naive = scheduler.parse_time("2026-11-10T09:05:00")
    assert naive.tzinfo is not None


def test_manager_replied_after_hot():
    assert scheduler.manager_replied_after_hot({
        "hot_at": "2026-11-10T09:00:00+00:00", "last_manager_message_at": "2026-11-10T09:01:00+00:00"}) is True
    assert scheduler.manager_replied_after_hot({
        "hot_at": "2026-11-10T09:00:00+00:00", "last_manager_message_at": "2026-11-10T08:59:00+00:00"}) is False
    assert scheduler.manager_replied_after_hot({"hot_at": "2026-11-10T09:00:00+00:00"}) is False


def test_check_hot_dialogs_once(monkeypatch):
    now = datetime.now(timezone.utc)
    db = {
        "bots": [{"id": "b1", "invoice_reminder_minutes": 10}],
        "cities": [{"id": "c1", "name": "Астана"}],
        "team_members": [],
        "dialogs": [
            # 12 минут, ещё не напоминали → напоминание
            {"id": "d1", "bot_id": "b1", "status": "hot", "city_id": "c1", "reminder_count": 0,
             "hot_at": (now - timedelta(minutes=12)).isoformat()},
            # 25 минут, уже было напоминание → эскалация
            {"id": "d2", "bot_id": "b1", "status": "hot", "city_id": None, "reminder_count": 1,
             "hot_at": (now - timedelta(minutes=25)).isoformat()},
            # сотрудник ответил → ничего
            {"id": "d3", "bot_id": "b1", "status": "hot", "reminder_count": 0,
             "hot_at": (now - timedelta(minutes=30)).isoformat(),
             "last_manager_message_at": (now - timedelta(minutes=1)).isoformat()},
            # рано → ничего
            {"id": "d4", "bot_id": "b1", "status": "hot", "reminder_count": 0,
             "hot_at": (now - timedelta(minutes=3)).isoformat()},
        ],
    }
    monkeypatch.setattr(scheduler, "supabase", FakeSupabase(db))
    sent = []

    async def fake_reminder(bot, dialog, city, team, minutes, escalation):
        sent.append((dialog["id"], city["name"] if city else None, minutes, escalation))

    monkeypatch.setattr(scheduler, "notify_reminder", fake_reminder)
    asyncio.run(scheduler.check_hot_dialogs_once())
    assert sent == [("d1", "Астана", 12, False), ("d2", None, 25, True)]
    counts = {d["id"]: d["reminder_count"] for d in db["dialogs"]}
    assert counts == {"d1": 1, "d2": 2, "d3": 0, "d4": 0}

    # Второй проход сразу — повторно не шлём
    sent.clear()
    asyncio.run(scheduler.check_hot_dialogs_once())
    assert sent == []


def test_reminder_rolled_back_when_not_sent(monkeypatch):
    now = datetime.now(timezone.utc)
    db = {
        "bots": [{"id": "b1", "invoice_reminder_minutes": 10}],
        "cities": [],
        "team_members": [],
        "dialogs": [{"id": "d1", "bot_id": "b1", "status": "hot", "reminder_count": 0,
                     "hot_at": (now - timedelta(minutes=11)).isoformat()}],
    }
    monkeypatch.setattr(scheduler, "supabase", FakeSupabase(db))
    results = [False]

    async def fake_reminder(bot, dialog, city, team, minutes, escalation):
        return results[0]

    monkeypatch.setattr(scheduler, "notify_reminder", fake_reminder)
    asyncio.run(scheduler.check_hot_dialogs_once())
    # Ни одна отправка не прошла — счётчик откатился, повторим позже
    assert db["dialogs"][0]["reminder_count"] == 0

    # None (старый notifier) — считаем отправленным
    results[0] = None
    asyncio.run(scheduler.check_hot_dialogs_once())
    assert db["dialogs"][0]["reminder_count"] == 1
