"""
Тесты клиента Wazzup. В реальный API не ходим — используем httpx.MockTransport,
который возвращает заранее заданные ответы и запоминает запросы.
"""
import asyncio
import json

import httpx

from app import wazzup


def make_transport(status_code: int, body, captured: list):
    """
    Создаёт поддельный транспорт: каждый запрос сохраняется в captured,
    в ответ отдаётся status_code и body (dict/list → JSON, str → обычный текст).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if isinstance(body, str):
            return httpx.Response(status_code, text=body)
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def run(coro):
    """Запускает асинхронную функцию в тесте."""
    return asyncio.run(coro)


def test_send_text_body_and_headers():
    captured = []
    wazzup._transport = make_transport(201, {"messageId": "m-1", "chatId": "77011234567"}, captured)
    try:
        result = run(wazzup.send_text("secret-key", "chan-1", "77011234567", "Здравствуйте!"))
    finally:
        wazzup._transport = None

    assert result == {"ok": True, "message_id": "m-1", "error": ""}
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == f"{wazzup.WAZZUP_API_URL}/message"
    assert request.headers["Authorization"] == "Bearer secret-key"
    body = json.loads(request.content)
    assert body["channelId"] == "chan-1"
    assert body["chatType"] == "whatsapp"
    assert body["chatId"] == "77011234567"
    assert body["text"] == "Здравствуйте!"
    assert "contentUri" not in body
    assert "clearUnanswered" not in body
    assert len(body["crmMessageId"]) == 36  # uuid4


def test_send_text_unique_crm_message_id():
    captured = []
    wazzup._transport = make_transport(201, {"messageId": "m"}, captured)
    try:
        run(wazzup.send_text("k", "c", "7701", "a"))
        run(wazzup.send_text("k", "c", "7701", "a"))
    finally:
        wazzup._transport = None
    first = json.loads(captured[0].content)["crmMessageId"]
    second = json.loads(captured[1].content)["crmMessageId"]
    assert first != second


def test_send_text_truncates_to_10000():
    captured = []
    wazzup._transport = make_transport(201, {"messageId": "m"}, captured)
    try:
        run(wazzup.send_text("k", "c", "7701", "я" * 12000))
    finally:
        wazzup._transport = None
    assert len(json.loads(captured[0].content)["text"]) == 10000


def test_send_file_uses_content_uri_without_text():
    captured = []
    wazzup._transport = make_transport(201, {"messageId": "f-1"}, captured)
    try:
        result = run(wazzup.send_file("k", "chan", "7701", "https://example.com/a.jpg"))
    finally:
        wazzup._transport = None
    assert result["ok"] is True
    assert result["message_id"] == "f-1"
    body = json.loads(captured[0].content)
    assert body["contentUri"] == "https://example.com/a.jpg"
    assert "text" not in body


def test_send_text_error_with_description():
    captured = []
    wazzup._transport = make_transport(400, {"error": "CHANNEL_NO_MONEY", "description": "Канал не оплачен"}, captured)
    try:
        result = run(wazzup.send_text("k", "c", "7701", "a"))
    finally:
        wazzup._transport = None
    assert result["ok"] is False
    assert result["message_id"] == ""
    assert "CHANNEL_NO_MONEY" in result["error"]
    assert "Канал не оплачен" in result["error"]


def test_error_as_list_and_non_json():
    captured = []
    wazzup._transport = make_transport(400, [{"error": "BAD_CONTACT"}], captured)
    try:
        result = run(wazzup.send_text("k", "c", "7701", "a"))
    finally:
        wazzup._transport = None
    assert result["error"] == "BAD_CONTACT"

    wazzup._transport = make_transport(502, "Bad Gateway", captured)
    try:
        result = run(wazzup.send_text("k", "c", "7701", "a"))
    finally:
        wazzup._transport = None
    assert result["ok"] is False
    assert "502" in result["error"]


def test_network_error_does_not_raise_and_hides_key():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    wazzup._transport = httpx.MockTransport(handler)
    try:
        result = run(wazzup.get_channels("super-secret-key"))
    finally:
        wazzup._transport = None
    assert result["ok"] is False
    assert result["channels"] == []
    assert "super-secret-key" not in result["error"]


def test_empty_api_key_no_request():
    captured = []
    wazzup._transport = make_transport(200, [], captured)
    try:
        result = run(wazzup.get_channels(""))
        send_result = run(wazzup.send_text("k", "", "7701", "a"))
    finally:
        wazzup._transport = None
    assert result["ok"] is False
    assert send_result["ok"] is False
    assert captured == []


def test_get_channels_ok_and_bad_format():
    channels = [{"channelId": "c1", "transport": "whatsapp", "plainId": "77011234567", "state": "active"}]
    captured = []
    wazzup._transport = make_transport(200, channels, captured)
    try:
        result = run(wazzup.get_channels("k"))
    finally:
        wazzup._transport = None
    assert result == {"ok": True, "channels": channels, "error": ""}
    assert captured[0].method == "GET"

    wazzup._transport = make_transport(200, {"strange": True}, captured)
    try:
        result = run(wazzup.get_channels("k"))
    finally:
        wazzup._transport = None
    assert result["ok"] is False


def test_set_webhook_body_and_plain_ok_response():
    captured = []
    # Wazzup может ответить просто строкой "ok" — это успех
    wazzup._transport = make_transport(200, "ok", captured)
    try:
        result = run(wazzup.set_webhook("k", "https://example.com/webhook/wazzup/b1?token=t"))
    finally:
        wazzup._transport = None
    assert result == {"ok": True, "error": ""}
    request = captured[0]
    assert request.method == "PATCH"
    assert str(request.url).endswith("/webhooks")
    body = json.loads(request.content)
    assert body == {
        "webhooksUri": "https://example.com/webhook/wazzup/b1?token=t",
        "subscriptions": {
            "messagesAndStatuses": True,
            "contactsAndDealsCreation": False,
            "channelsUpdates": True,
            "templateStatus": False,
        },
    }


def test_set_webhook_errors():
    captured = []
    wazzup._transport = make_transport(400, {"error": "testPostNotPassed"}, captured)
    try:
        result = run(wazzup.set_webhook("k", "https://example.com/w"))
    finally:
        wazzup._transport = None
    assert result["ok"] is False
    assert "testPostNotPassed" in result["error"]

    # Слишком длинный адрес — даже не отправляем запрос
    captured.clear()
    result = run(wazzup.set_webhook("k", "https://example.com/" + "a" * 300))
    assert result["ok"] is False
    assert captured == []


def test_get_webhook():
    captured = []
    data = {"webhooksUri": "https://x", "subscriptions": {"messagesAndStatuses": True}}
    wazzup._transport = make_transport(200, data, captured)
    try:
        result = run(wazzup.get_webhook("k"))
    finally:
        wazzup._transport = None
    assert result == {"ok": True, "data": data, "error": ""}


def test_build_webhook_url_and_secret():
    url = wazzup.build_webhook_url("bot-123", "abc")
    base_url = (wazzup.BACKEND_URL or "").rstrip("/")
    assert url == f"{base_url}/webhook/wazzup/bot-123?token=abc"

    secret_one = wazzup.generate_webhook_secret()
    secret_two = wazzup.generate_webhook_secret()
    assert secret_one != secret_two
    assert len(secret_one) >= 30
