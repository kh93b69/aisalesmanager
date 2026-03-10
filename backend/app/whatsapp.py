import httpx
from app.config import WAHA_API_URL, WAHA_API_KEY


async def send_whatsapp_message(chat_id: str, text: str, session: str = "default") -> dict:
    """
    Отправляет сообщение в WhatsApp через WAHA API.

    chat_id — ID чата (формат: 79001234567@c.us)
    text — текст сообщения
    session — имя сессии WAHA
    """
    url = f"{WAHA_API_URL}/api/sendText"
    headers = {"Content-Type": "application/json"}

    if WAHA_API_KEY:
        headers["Authorization"] = f"Bearer {WAHA_API_KEY}"

    payload = {
        "chatId": chat_id,
        "text": text,
        "session": session,
    }

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload, headers=headers)
        return response.json()
