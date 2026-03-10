import httpx
from app.config import TELEGRAM_BOT_TOKEN

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


async def send_telegram_message(chat_id: int, text: str) -> dict:
    """
    Отправляет сообщение в Telegram.

    chat_id — ID чата в Telegram
    text — текст сообщения
    """
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload)
        return response.json()


async def set_webhook(webhook_url: str) -> dict:
    """
    Устанавливает webhook для Telegram бота.
    Вызывается один раз при настройке.

    webhook_url — полный URL, например: https://your-app.railway.app/webhook/telegram
    """
    url = f"{TELEGRAM_API}/setWebhook"
    payload = {"url": webhook_url}

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload)
        return response.json()
