"""
Тесты прав доступа (app/access.py).

Здесь же лежит маленькая «поддельная» база FakeDB — имитация клиента Supabase
в памяти. Её используют и тесты API (test_settings_api.py, test_dialogs_api.py).
В настоящий Supabase тесты не ходят.
"""

import os
import re

# Фиктивные адреса/ключи — чтобы случайно не обратиться к боевому Supabase
os.environ["SUPABASE_URL"] = "http://localhost:54321"
os.environ["SUPABASE_ANON_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.test-signature"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.test-signature"
)

import pytest
from fastapi import HTTPException

from app import access


# Идентификаторы в формате uuid (API проверяет формат city_id / user_id)
ASTANA = "aaaaaaaa-0000-4000-8000-000000000001"
ALMATY = "aaaaaaaa-0000-4000-8000-000000000002"
MISSING = "aaaaaaaa-0000-4000-8000-0000000000ff"
MGR_ALL = "bbbbbbbb-0000-4000-8000-000000000001"
STRANGER = "bbbbbbbb-0000-4000-8000-000000000002"


# ============================================================
# Поддельная база данных (имитация supabase-py в памяти)
# ============================================================

class FakeResult:
    """Результат .execute(): data и count, как у supabase-py."""

    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def like_to_regex(pattern: str) -> str:
    """SQL LIKE (% и _) и PostgREST-звёздочка (*) → регулярное выражение."""
    result = ""
    for char in pattern:
        if char in ("%", "*"):
            result += ".*"
        elif char == "_":
            result += "."
        else:
            result += re.escape(char)
    return "^" + result + "$"


class FakeQuery:
    """Цепочка запроса: table(...).select(...).eq(...).order(...).execute()."""

    def __init__(self, db, table_name):
        self.db = db
        self.table_name = table_name
        self.action = "select"
        self.payload = None
        self.filters = []          # список функций row -> bool
        self.negate_next = False
        self.orders = []           # [(column, desc, nulls_last)]
        self.limit_value = None
        self.range_value = None
        self.count_mode = None
        self.head = False

    # --- действия ---
    def select(self, *columns, count=None, head=None):
        self.action = "select"
        self.count_mode = count
        self.head = bool(head)
        return self

    def insert(self, payload):
        self.action = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def delete(self):
        self.action = "delete"
        return self

    # --- фильтры ---
    @property
    def not_(self):
        self.negate_next = True
        return self

    def _add_filter(self, check):
        if self.negate_next:
            self.negate_next = False
            self.filters.append(lambda row: not check(row))
        else:
            self.filters.append(check)
        return self

    def eq(self, column, value):
        return self._add_filter(lambda row: str(row.get(column)) == str(value) if row.get(column) is not None else False)

    def is_(self, column, value):
        return self._add_filter(lambda row: row.get(column) is None)

    def in_(self, column, values):
        values = [str(v) for v in values]
        return self._add_filter(lambda row: str(row.get(column)) in values)

    def like(self, column, pattern):
        regex = like_to_regex(pattern)
        return self._add_filter(lambda row: re.match(regex, str(row.get(column) or ""), re.S) is not None)

    def ilike(self, column, pattern):
        regex = like_to_regex(pattern)
        return self._add_filter(
            lambda row: re.match(regex, str(row.get(column) or ""), re.S | re.I) is not None
        )

    def gte(self, column, value):
        return self._add_filter(lambda row: row.get(column) is not None and str(row.get(column)) >= str(value))

    def or_(self, filters):
        # Поддерживаем только формат "col.ilike.*x*,col2.ilike.*x*"
        checks = []
        for part in filters.split(","):
            column, operator, pattern = part.split(".", 2)
            assert operator == "ilike"
            checks.append((column, like_to_regex(pattern)))

        def check(row):
            for column, regex in checks:
                if re.match(regex, str(row.get(column) or ""), re.S | re.I):
                    return True
            return False

        return self._add_filter(check)

    # --- сортировка и страницы ---
    def order(self, column, desc=False, nullsfirst=False):
        nulls_last = False
        if column.endswith(".desc.nullslast"):
            column = column.replace(".desc.nullslast", "")
            desc = True
            nulls_last = True
        self.orders.append((column, desc, nulls_last))
        return self

    def limit(self, size):
        self.limit_value = size
        return self

    def range(self, start, end):
        self.range_value = (start, end)
        return self

    # --- выполнение ---
    def _matches(self, row):
        for check in self.filters:
            if not check(row):
                return False
        return True

    def execute(self):
        self.db.calls.append((self.table_name, self.action))
        rows = self.db.tables.setdefault(self.table_name, [])

        if self.action == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            inserted = []
            for item in items:
                row = dict(item)
                if "id" not in row:
                    self.db.next_id += 1
                    row["id"] = f"{self.table_name}-{self.db.next_id}"
                rows.append(row)
                inserted.append(dict(row))
            return FakeResult(inserted)

        matched = [row for row in rows if self._matches(row)]

        if self.action == "update":
            for row in matched:
                row.update(self.payload)
            return FakeResult([dict(row) for row in matched])

        if self.action == "delete":
            self.db.tables[self.table_name] = [row for row in rows if not self._matches(row)]
            return FakeResult([dict(row) for row in matched])

        # select: сортировка (последний ключ — самый младший)
        for column, desc, nulls_last in reversed(self.orders):
            with_value = [r for r in matched if r.get(column) is not None]
            without = [r for r in matched if r.get(column) is None]
            with_value.sort(key=lambda r: str(r.get(column)), reverse=desc)
            matched = with_value + without if nulls_last else without + with_value

        total = len(matched)
        if self.range_value:
            start, end = self.range_value
            matched = matched[start:end + 1]
        if self.limit_value is not None:
            matched = matched[:self.limit_value]

        count = total if self.count_mode else None
        if self.head:
            return FakeResult([], count)
        return FakeResult([dict(r) for r in matched], count)


class FakeStorageBucket:
    def __init__(self, db):
        self.db = db

    def upload(self, path, content, options=None):
        self.db.storage_files.append(path)
        self.db.storage_options.append(options)
        return {}

    def get_public_url(self, path):
        return f"http://storage.test/bot_images/{path}"

    def remove(self, paths):
        for path in paths:
            if path in self.db.storage_files:
                self.db.storage_files.remove(path)
        return []


class FakeStorage:
    def __init__(self, db):
        self.db = db

    def from_(self, bucket):
        return FakeStorageBucket(self.db)


class FakeDB:
    """Поддельный клиент Supabase: таблицы — это списки словарей."""

    def __init__(self, tables=None):
        self.tables = {}
        for name in tables or {}:
            self.tables[name] = [dict(row) for row in tables[name]]
        self.next_id = 0
        self.calls = []
        self.storage_files = []
        self.storage_options = []
        self.storage = FakeStorage(self)

    def table(self, name):
        return FakeQuery(self, name)


def sample_tables():
    """Типовые данные: владелец, два города, менеджер Астаны, менеджер всех городов."""
    return {
        "bots": [{
            "id": "bot1", "user_id": "owner", "name": "Клиника",
            "wazzup_api_key": "SECRET_KEY", "wazzup_webhook_secret": "SECRET_HOOK",
            "wazzup_channel_id": "ch1", "telegram_notify_chat_id": "-100",
            "created_at": "2026-01-01",
        }],
        "cities": [
            {"id": ASTANA, "bot_id": "bot1", "name": "Астана", "sort_order": 0},
            {"id": ALMATY, "bot_id": "bot1", "name": "Алматы", "sort_order": 1},
        ],
        "team_members": [
            {"id": "m1", "bot_id": "bot1", "user_id": "mgr_astana", "city_id": ASTANA, "created_at": "1"},
            {"id": "m2", "bot_id": "bot1", "user_id": MGR_ALL, "city_id": None, "created_at": "2"},
        ],
        "users": [
            {"id": "owner", "email": "owner@test.kz"},
            {"id": "mgr_astana", "email": "astana@test.kz"},
            {"id": MGR_ALL, "email": "all@test.kz"},
            {"id": "newbie", "email": "New_Guy@test.kz"},
        ],
        "dialogs": [
            {"id": "d1", "bot_id": "bot1", "chat_id": "77010000001", "city_id": ASTANA, "status": "hot",
             "needs_attention": True, "ai_disabled": False, "last_message_at": "2026-11-10T10:00",
             "created_at": "2026-11-09T10:00", "contact_name": "Айгерим"},
            {"id": "d2", "bot_id": "bot1", "chat_id": "77010000002", "city_id": ALMATY, "status": "paid",
             "needs_attention": False, "ai_disabled": False, "last_message_at": "2026-11-10T11:00",
             "created_at": "2026-11-09T11:00", "contact_name": "Иван"},
            {"id": "d3", "bot_id": "bot1", "chat_id": "77010000003", "city_id": None, "status": "new",
             "needs_attention": False, "ai_disabled": False, "last_message_at": None,
             "created_at": "2026-11-09T12:00", "contact_name": None},
        ],
        "messages": [],
        "knowledge_chunks": [],
    }


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDB(sample_tables())
    monkeypatch.setattr(access, "supabase", db)
    return db


# ============================================================
# Чистые функции
# ============================================================

BOT = {"id": "bot1", "user_id": "owner"}


def test_decide_access_owner():
    result = access.decide_access(BOT, None, "owner")
    assert result["role"] == "owner"
    assert result["city_id"] is None


def test_decide_access_manager_with_city():
    member = {"bot_id": "bot1", "user_id": "u2", "city_id": ASTANA}
    result = access.decide_access(BOT, member, "u2")
    assert result["role"] == "manager"
    assert result["city_id"] == ASTANA
    assert result["member"] == member


def test_decide_access_stranger_and_wrong_member():
    assert access.decide_access(BOT, None, "u3") is None
    # Запись команды другого бота или другого пользователя не даёт доступа
    assert access.decide_access(BOT, {"bot_id": "bot2", "user_id": "u3", "city_id": None}, "u3") is None
    assert access.decide_access(BOT, {"bot_id": "bot1", "user_id": "u4", "city_id": None}, "u3") is None
    assert access.decide_access(None, None, "owner") is None
    assert access.decide_access(BOT, None, "") is None


def test_can_see_dialog_rules():
    owner = access.decide_access(BOT, None, "owner")
    mgr_city = access.decide_access(BOT, {"bot_id": "bot1", "user_id": "m", "city_id": ASTANA}, "m")
    mgr_all = access.decide_access(BOT, {"bot_id": "bot1", "user_id": "m", "city_id": None}, "m")

    astana = {"bot_id": "bot1", "city_id": ASTANA}
    almaty = {"bot_id": "bot1", "city_id": ALMATY}
    no_city = {"bot_id": "bot1", "city_id": None}
    other_bot = {"bot_id": "bot2", "city_id": ASTANA}

    assert access.can_see_dialog(owner, astana) and access.can_see_dialog(owner, no_city)
    assert access.can_see_dialog(mgr_all, almaty) and access.can_see_dialog(mgr_all, no_city)
    assert access.can_see_dialog(mgr_city, astana)
    assert not access.can_see_dialog(mgr_city, almaty)
    assert not access.can_see_dialog(mgr_city, no_city)
    assert not access.can_see_dialog(owner, other_bot)


def test_public_bot_hides_secrets():
    bot = {"id": "b", "name": "X", "wazzup_api_key": "k", "wazzup_webhook_secret": "s", "wazzup_channel_id": "c"}
    result = access.public_bot(bot)
    assert "wazzup_api_key" not in result
    assert "wazzup_webhook_secret" not in result
    assert result["wazzup_connected"] is True
    assert result["name"] == "X"
    # Исходный словарь не изменился
    assert bot["wazzup_api_key"] == "k"


def test_public_bot_not_connected():
    assert access.public_bot({"wazzup_api_key": "k"})["wazzup_connected"] is False
    assert access.public_bot({"wazzup_channel_id": "c"})["wazzup_connected"] is False
    assert access.public_bot({})["wazzup_connected"] is False


# ============================================================
# Функции с БД (на поддельной базе)
# ============================================================

def test_get_bot_access_roles(fake_db):
    assert access.get_bot_access("bot1", "owner")["role"] == "owner"
    manager = access.get_bot_access("bot1", "mgr_astana")
    assert manager["role"] == "manager" and manager["city_id"] == ASTANA
    assert access.get_bot_access("bot1", STRANGER) is None
    assert access.get_bot_access("missing", "owner") is None


def test_require_bot_access_owner_only(fake_db):
    assert access.require_bot_access("bot1", "owner", owner_only=True)["role"] == "owner"

    with pytest.raises(HTTPException) as error:
        access.require_bot_access("bot1", MGR_ALL, owner_only=True)
    assert error.value.status_code == 403
    assert "владелец" in error.value.detail

    with pytest.raises(HTTPException) as error:
        access.require_bot_access("bot1", STRANGER)
    assert error.value.status_code == 403
    assert error.value.detail == "Нет доступа к этому боту"


def test_require_dialog_access(fake_db):
    dialog, result = access.require_dialog_access("d1", "mgr_astana")
    assert dialog["id"] == "d1" and result["role"] == "manager"

    # Менеджер Астаны не видит Алматы и диалоги без города
    for dialog_id in ("d2", "d3"):
        with pytest.raises(HTTPException) as error:
            access.require_dialog_access(dialog_id, "mgr_astana")
        assert error.value.status_code == 403

    # Менеджер всех городов и владелец — видят
    assert access.require_dialog_access("d3", MGR_ALL)[0]["id"] == "d3"
    assert access.require_dialog_access("d2", "owner")[0]["id"] == "d2"

    with pytest.raises(HTTPException) as error:
        access.require_dialog_access("nope", "owner")
    assert error.value.status_code == 404

    with pytest.raises(HTTPException) as error:
        access.require_dialog_access("d1", STRANGER)
    assert error.value.status_code == 403
