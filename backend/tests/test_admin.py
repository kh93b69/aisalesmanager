"""Админка: проверка данных подписки (без сети)."""
import pytest
from fastapi import HTTPException

import tests._stubs  # noqa: F401 — фиктивные ключи Supabase
from app.routes import admin


def test_read_int():
    assert admin.read_int("500", "Диалогов", 0, 1000) == 500
    with pytest.raises(HTTPException) as error:
        admin.read_int("abc", "Диалогов", 0, 1000)
    assert error.value.status_code == 400
    with pytest.raises(HTTPException):
        admin.read_int(-1, "Ботов", 1, 100)


def test_read_expires_at():
    assert admin.read_expires_at(None) is None
    assert admin.read_expires_at("") is None
    assert admin.read_expires_at("2026-12-31") == "2026-12-31T23:59:59+05:00"
    assert admin.read_expires_at("2026-12-31T10:00:00+00:00") == "2026-12-31T10:00:00+00:00"
    with pytest.raises(HTTPException):
        admin.read_expires_at("31.12.2026")
