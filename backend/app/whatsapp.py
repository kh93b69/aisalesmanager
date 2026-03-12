import httpx
from app.config import WAHA_API_URL, WAHA_API_KEY


def _get_headers():
    """Возвращает заголовки для WAHA API."""
    headers = {"Content-Type": "application/json"}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    return headers


async def start_session(session: str = "default") -> dict:
    """
    Запускает новую сессию WhatsApp в WAHA.
    После запуска нужно отсканировать QR-код.
    """
    url = f"{WAHA_API_URL}/api/sessions/start"
    payload = {
        "name": session,
        "config": {
            "webhooks": [
                {
                    "url": "",  # Будет установлен позже через setup_webhook
                    "events": ["message"]
                }
            ]
        }
    }

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload, headers=_get_headers(), timeout=15)
        return response.json()


async def stop_session(session: str = "default") -> dict:
    """Останавливает сессию WhatsApp."""
    url = f"{WAHA_API_URL}/api/sessions/stop"
    payload = {"name": session}

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload, headers=_get_headers(), timeout=15)
        return response.json()


async def get_session_status(session: str = "default") -> dict:
    """
    Получает статус сессии.
    Возвращает: status (WORKING, SCAN_QR_CODE, STARTING, STOPPED, FAILED)
    """
    url = f"{WAHA_API_URL}/api/sessions/{session}"

    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(url, headers=_get_headers(), timeout=10)
            if response.status_code == 404:
                return {"status": "NOT_FOUND"}
            return response.json()
    except Exception:
        return {"status": "ERROR"}


async def get_qr_code(session: str = "default") -> dict:
    """
    Получает QR-код для сканирования WhatsApp.
    Возвращает QR в формате base64 (data:image/png;base64,...).
    """
    url = f"{WAHA_API_URL}/api/{session}/auth/qr"
    params = {"format": "image"}

    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(url, headers=_get_headers(), params=params, timeout=10)
            if response.status_code == 200:
                # Если вернулся JSON с qr
                if response.headers.get("content-type", "").startswith("application/json"):
                    return response.json()
                # Если вернулось изображение
                import base64
                qr_base64 = base64.b64encode(response.content).decode("utf-8")
                return {"qr": f"data:image/png;base64,{qr_base64}"}
            return {"error": "QR не доступен", "status_code": response.status_code}
    except Exception as e:
        return {"error": str(e)}


async def setup_webhook(session: str, webhook_url: str) -> dict:
    """Настраивает webhook для сессии WAHA."""
    url = f"{WAHA_API_URL}/api/sessions/{session}"
    payload = {
        "config": {
            "webhooks": [
                {
                    "url": webhook_url,
                    "events": ["message"]
                }
            ]
        }
    }

    async with httpx.AsyncClient() as http_client:
        response = await http_client.put(url, json=payload, headers=_get_headers(), timeout=10)
        return response.json()


async def send_whatsapp_message(chat_id: str, text: str, session: str = "default") -> dict:
    """
    Отправляет текстовое сообщение в WhatsApp через WAHA.
    chat_id — ID чата (формат: 79001234567@c.us)
    """
    url = f"{WAHA_API_URL}/api/sendText"
    payload = {
        "chatId": chat_id,
        "text": text,
        "session": session,
    }

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload, headers=_get_headers(), timeout=15)
        return response.json()


async def send_whatsapp_image(chat_id: str, image_url: str, caption: str = "", session: str = "default") -> dict:
    """Отправляет картинку в WhatsApp через WAHA."""
    url = f"{WAHA_API_URL}/api/sendImage"
    payload = {
        "chatId": chat_id,
        "file": {"url": image_url},
        "caption": caption,
        "session": session,
    }

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload, headers=_get_headers(), timeout=15)
        return response.json()
