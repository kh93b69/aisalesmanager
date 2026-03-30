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


async def send_telegram_photo(chat_id: int, photo_url: str, caption: str = "") -> dict:
    """
    Отправляет картинку в Telegram по URL.

    chat_id — ID чата в Telegram
    photo_url — публичный URL картинки
    caption — подпись к картинке
    """
    url = f"{TELEGRAM_API}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
    }

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload)
        return response.json()


async def set_webhook(webhook_url: str, secret_token: str = "") -> dict:
    """
    Устанавливает webhook для Telegram бота.
    Вызывается один раз при настройке.

    webhook_url — полный URL, например: https://your-app.railway.app/webhook/telegram
    secret_token — секретный токен для проверки подлинности запросов
    """
    url = f"{TELEGRAM_API}/setWebhook"
    payload = {"url": webhook_url}
    if secret_token:
        payload["secret_token"] = secret_token

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload)
        return response.json()
