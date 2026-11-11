"""
Отправка сообщения клиенту в нужный канал (WhatsApp через Wazzup или Telegram).

Ответ ИИ может содержать теги картинок [IMAGE:https://...] — их вырезаем из текста
и отправляем картинки отдельными сообщениями после текста.
"""
import re
import time

from app import telegram
from app import wazzup

# Тег картинки в ответе ИИ: [IMAGE:https://...]
IMAGE_TAG_PATTERN = r'\[IMAGE:(https?://[^\]]+)\]'

# Лимит длины одного сообщения в Telegram
TELEGRAM_MAX_LENGTH = 4096

# id сообщений, которые мы сами отправили через Wazzup API: id → время отправки.
# Если Wazzup пришлёт их обратно в вебхуке как исходящие (isEcho), это не ответ сотрудника.
# Хранится в памяти процесса 10 минут (у сервиса 1 реплика).
_sent_message_ids = {}
SENT_ID_TTL_SECONDS = 600


def remember_sent_id(message_id: str) -> None:
    """Запоминает id отправленного нами сообщения (и чистит устаревшие)."""
    if not message_id:
        return
    now = time.time()
    for old_id in list(_sent_message_ids.keys()):
        if now - _sent_message_ids[old_id] > SENT_ID_TTL_SECONDS:
            del _sent_message_ids[old_id]
    _sent_message_ids[message_id] = now


def was_sent_by_us(message_id: str | None) -> bool:
    """True — это сообщение недавно отправили мы сами (бот или сотрудник из панели)."""
    return bool(message_id) and message_id in _sent_message_ids


def split_images(text: str) -> tuple[str, list[str]]:
    """
    Разделяет ответ на чистый текст и список ссылок на картинки.
    Пример: "Вот фото [IMAGE:https://x/1.jpg]" → ("Вот фото", ["https://x/1.jpg"])
    """
    text = text or ""
    image_urls = []
    for url in re.findall(IMAGE_TAG_PATTERN, text):
        url = url.strip()
        if url and url not in image_urls:
            image_urls.append(url)

    clean_text = re.sub(IMAGE_TAG_PATTERN, "", text)
    # Убираем лишние пробелы и пустые строки, оставшиеся после тегов
    clean_text = re.sub(r"[ \t]+\n", "\n", clean_text)
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)
    clean_text = re.sub(r"[ \t]{2,}", " ", clean_text)
    return clean_text.strip(), image_urls


def _split_long_text(text: str, limit: int) -> list[str]:
    """Режет длинный текст на части не длиннее limit (стараемся резать по переносу строки)."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


async def _send_whatsapp(bot: dict, chat_id: str, clean_text: str, image_urls: list[str]) -> dict:
    """Отправка в WhatsApp через Wazzup: сначала текст, затем картинки."""
    api_key = bot.get("wazzup_api_key")
    channel_id = bot.get("wazzup_channel_id")
    if not api_key or not channel_id:
        return {"ok": False, "message_id": "", "error": "Wazzup не подключён"}

    message_id = ""
    errors = []

    if clean_text:
        result = await wazzup.send_text(api_key, channel_id, chat_id, clean_text)
        if result.get("ok"):
            message_id = result.get("message_id", "")
            remember_sent_id(message_id)
        else:
            errors.append(f"текст: {result.get('error', '')}")

    for url in image_urls:
        result = await wazzup.send_file(api_key, channel_id, chat_id, url)
        if result.get("ok"):
            remember_sent_id(result.get("message_id", ""))
            if not message_id:
                message_id = result.get("message_id", "")
        else:
            errors.append(f"картинка: {result.get('error', '')}")

    return {"ok": len(errors) == 0, "message_id": message_id, "error": "; ".join(errors)}


def _telegram_message_id(response) -> str:
    """Достаёт message_id из ответа Telegram {"ok": true, "result": {"message_id": 123}}."""
    if isinstance(response, dict) and response.get("ok"):
        result = response.get("result") or {}
        if isinstance(result, dict) and result.get("message_id") is not None:
            return str(result["message_id"])
    return ""


def _telegram_error(response) -> str:
    """Текст ошибки из ответа Telegram."""
    if isinstance(response, dict):
        return str(response.get("description") or "ошибка Telegram")
    return "ошибка Telegram"


async def _send_telegram(chat_id: str, clean_text: str, image_urls: list[str]) -> dict:
    """Отправка в Telegram: сначала текст (частями, если длинный), затем картинки."""
    try:
        telegram_chat_id = int(chat_id)
    except (TypeError, ValueError):
        return {"ok": False, "message_id": "", "error": "Некорректный chat_id Telegram"}

    message_id = ""
    errors = []

    if clean_text:
        for part in _split_long_text(clean_text, TELEGRAM_MAX_LENGTH):
            try:
                response = await telegram.send_telegram_message(telegram_chat_id, part)
            except Exception as e:
                errors.append(f"текст: {type(e).__name__}")
                continue
            part_id = _telegram_message_id(response)
            if part_id:
                if not message_id:
                    message_id = part_id
            else:
                errors.append(f"текст: {_telegram_error(response)}")

    for url in image_urls:
        try:
            response = await telegram.send_telegram_photo(telegram_chat_id, url)
        except Exception as e:
            errors.append(f"картинка: {type(e).__name__}")
            continue
        photo_id = _telegram_message_id(response)
        if photo_id:
            if not message_id:
                message_id = photo_id
        else:
            errors.append(f"картинка: {_telegram_error(response)}")

    # Номера сообщений Telegram повторяются в разных чатах — добавляем chat_id, как у входящих
    if message_id:
        message_id = f"tg:{chat_id}:{message_id}"
    return {"ok": len(errors) == 0, "message_id": message_id, "error": "; ".join(errors)}


async def send_to_client(bot: dict, dialog: dict, text: str, from_bot: bool = True) -> dict:
    """
    Отправляет сообщение клиенту в канал диалога.

    bot — строка таблицы bots (нужны wazzup_api_key и wazzup_channel_id для WhatsApp)
    dialog — строка таблицы dialogs (нужны channel и chat_id)
    text — текст ответа, может содержать теги [IMAGE:url]
    from_bot — True, если пишет ИИ; False, если сотрудник из панели (используется для логов)

    Возвращает {"ok": bool, "message_id": str, "error": str}.
    message_id — первого текстового сообщения (если текста нет — первой картинки).
    ok=False, если не ушло хотя бы одно сообщение (текст или картинка); в error — что именно.
    """
    bot = bot or {}
    dialog = dialog or {}
    channel = dialog.get("channel")
    chat_id = str(dialog.get("chat_id") or "").strip()
    sender = "бот" if from_bot else "сотрудник"

    if not chat_id:
        return {"ok": False, "message_id": "", "error": "У диалога нет chat_id"}

    clean_text, image_urls = split_images(text)
    if not clean_text and not image_urls:
        return {"ok": False, "message_id": "", "error": "Пустое сообщение"}

    try:
        if channel == "whatsapp":
            result = await _send_whatsapp(bot, chat_id, clean_text, image_urls)
        elif channel == "telegram":
            result = await _send_telegram(chat_id, clean_text, image_urls)
        else:
            result = {"ok": False, "message_id": "", "error": f"Неизвестный канал: {channel}"}
    except Exception as e:
        # Страховка: наружу исключения не выпускаем
        result = {"ok": False, "message_id": "", "error": f"Ошибка отправки: {type(e).__name__}"}

    if not result["ok"]:
        print(f"MESSENGER ERROR: ({sender}) {channel} диалог {dialog.get('id')}: {result['error']}")
    return result
