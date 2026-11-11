"""
Клиент Wazzup API v3 — отправка сообщений в WhatsApp и настройка вебхуков.

Документация: https://wazzup24.ru/help/api-ru/
Авторизация: заголовок "Authorization: Bearer <api_key>".
Важно: api_key НИКОГДА не пишем в логи.
"""
import secrets
import uuid

import httpx

from app.config import WAZZUP_API_URL, BACKEND_URL

# Таймаут всех запросов к Wazzup (секунды)
WAZZUP_TIMEOUT = 20

# Транспорт httpx. В обычной работе None (настоящая сеть).
# В тестах сюда подставляется httpx.MockTransport, чтобы не ходить в реальный API.
_transport = None


def _get_headers(api_key: str) -> dict:
    """Возвращает заголовки для запросов к Wazzup."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _parse_error(response: httpx.Response) -> str:
    """
    Достаёт понятный текст ошибки из ответа Wazzup.
    Wazzup отвечает JSON-ом с полем error (иногда description и data).
    Бывает и список ошибок, и вообще не-JSON — обрабатываем всё.
    """
    try:
        data = response.json()
    except Exception:
        text = response.text.strip()[:300]
        if text:
            return f"HTTP {response.status_code}: {text}"
        return f"HTTP {response.status_code}"

    # Иногда приходит список ошибок — берём первую
    if isinstance(data, list) and len(data) > 0:
        data = data[0]

    if isinstance(data, dict):
        error = data.get("error") or data.get("errorCode") or ""
        description = data.get("description") or data.get("message") or ""
        if error and description:
            return f"{error}: {description}"
        if error:
            return str(error)
        if description:
            return str(description)

    return f"HTTP {response.status_code}"


async def _request(method: str, path: str, api_key: str, json_body: dict | None = None) -> dict:
    """
    Выполняет запрос к Wazzup.
    Возвращает {"ok": bool, "status": int, "data": <json или None>, "error": str}.
    Исключения наружу не выбрасывает.
    """
    if not api_key:
        return {"ok": False, "status": 0, "data": None, "error": "Не указан API-ключ Wazzup"}

    url = f"{WAZZUP_API_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=WAZZUP_TIMEOUT, transport=_transport) as http_client:
            response = await http_client.request(method, url, headers=_get_headers(api_key), json=json_body)
    except httpx.TimeoutException:
        print(f"WAZZUP ERROR: таймаут запроса {method} {path}")
        return {"ok": False, "status": 0, "data": None, "error": "Wazzup не ответил вовремя (таймаут)"}
    except Exception as e:
        print(f"WAZZUP ERROR: {method} {path}: {type(e).__name__}: {e}")
        return {"ok": False, "status": 0, "data": None, "error": f"Ошибка соединения с Wazzup: {type(e).__name__}"}

    if response.status_code >= 400:
        error = _parse_error(response)
        print(f"WAZZUP ERROR: {method} {path} → {response.status_code}: {error}")
        return {"ok": False, "status": response.status_code, "data": None, "error": error}

    # Успешный ответ может быть не-JSON (например, просто "ok") — это нормально
    data = None
    try:
        data = response.json()
    except Exception:
        data = None

    return {"ok": True, "status": response.status_code, "data": data, "error": ""}


async def get_channels(api_key: str) -> dict:
    """
    Получает список каналов аккаунта Wazzup.
    Возвращает {"ok": bool, "channels": [...], "error": str}.
    """
    result = await _request("GET", "/channels", api_key)
    if not result["ok"]:
        return {"ok": False, "channels": [], "error": result["error"]}

    data = result["data"]
    if not isinstance(data, list):
        return {"ok": False, "channels": [], "error": "Неожиданный ответ Wazzup на запрос каналов"}

    return {"ok": True, "channels": data, "error": ""}


async def set_webhook(api_key: str, webhook_url: str) -> dict:
    """
    Подключает вебхук Wazzup на наш URL.
    Wazzup сразу пришлёт на этот URL тестовый POST {"test": true} и ждёт ответ 200.
    Возвращает {"ok": bool, "error": str}.
    """
    if len(webhook_url) > 200:
        return {"ok": False, "error": "Адрес вебхука длиннее 200 символов"}

    body = {
        "webhooksUri": webhook_url,
        "subscriptions": {
            "messagesAndStatuses": True,
            "contactsAndDealsCreation": False,
            "channelsUpdates": True,
            "templateStatus": False,
        },
    }
    result = await _request("PATCH", "/webhooks", api_key, body)
    if not result["ok"]:
        error = result["error"]
        # Переводим самые частые ошибки на понятный язык
        if "uriNotValid" in error:
            error = "Wazzup не принял адрес вебхука (uriNotValid)"
        elif "testPostNotPassed" in error:
            error = "Wazzup не смог достучаться до нашего сервера (testPostNotPassed)"
        return {"ok": False, "error": error}

    return {"ok": True, "error": ""}


async def get_webhook(api_key: str) -> dict:
    """
    Получает текущие настройки вебхука Wazzup.
    Возвращает {"ok": bool, "data": {...}, "error": str}.
    """
    result = await _request("GET", "/webhooks", api_key)
    if not result["ok"]:
        return {"ok": False, "data": {}, "error": result["error"]}

    data = result["data"]
    if not isinstance(data, dict):
        data = {}
    return {"ok": True, "data": data, "error": ""}


async def _send_message(api_key: str, body: dict) -> dict:
    """Общая часть отправки сообщения: POST /message."""
    result = await _request("POST", "/message", api_key, body)
    if not result["ok"]:
        return {"ok": False, "message_id": "", "error": result["error"]}

    data = result["data"]
    message_id = ""
    if isinstance(data, dict):
        message_id = str(data.get("messageId") or "")
    return {"ok": True, "message_id": message_id, "error": ""}


async def send_text(api_key: str, channel_id: str, chat_id: str, text: str) -> dict:
    """
    Отправляет текстовое сообщение клиенту в WhatsApp.
    clearUnanswered НЕ передаём: по умолчанию наш ответ сбрасывает «неотвеченные» в Wazzup.
    Возвращает {"ok": bool, "message_id": str, "error": str}.
    """
    if not channel_id:
        return {"ok": False, "message_id": "", "error": "Не выбран канал Wazzup"}

    body = {
        "channelId": channel_id,
        "chatType": "whatsapp",
        "chatId": chat_id,
        # Лимит WhatsApp — 10 000 символов
        "text": text[:10000],
        # Уникальный ID — защита от дублей при повторной отправке
        "crmMessageId": str(uuid.uuid4()),
    }
    return await _send_message(api_key, body)


async def send_file(api_key: str, channel_id: str, chat_id: str, content_uri: str) -> dict:
    """
    Отправляет файл (картинку, документ) клиенту в WhatsApp по публичной ссылке.
    Возвращает {"ok": bool, "message_id": str, "error": str}.
    """
    if not channel_id:
        return {"ok": False, "message_id": "", "error": "Не выбран канал Wazzup"}

    body = {
        "channelId": channel_id,
        "chatType": "whatsapp",
        "chatId": chat_id,
        "contentUri": content_uri,
        "crmMessageId": str(uuid.uuid4()),
    }
    return await _send_message(api_key, body)


def build_webhook_url(bot_id: str, secret: str) -> str:
    """Собирает адрес нашего вебхука для конкретного бота."""
    base_url = (BACKEND_URL or "").rstrip("/")
    return f"{base_url}/webhook/wazzup/{bot_id}?token={secret}"


def generate_webhook_secret() -> str:
    """Генерирует случайный секрет для адреса вебхука."""
    return secrets.token_urlsafe(24)
