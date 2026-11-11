"""
Тесты отправки клиенту. Функции Wazzup и Telegram подменяем на поддельные,
которые просто запоминают, что их вызвали.
"""
import asyncio

from app import messenger
from app import telegram
from app import wazzup


BOT = {"id": "b1", "wazzup_api_key": "key", "wazzup_channel_id": "chan"}
WA_DIALOG = {"id": "d1", "channel": "whatsapp", "chat_id": "77011234567"}
TG_DIALOG = {"id": "d2", "channel": "telegram", "chat_id": "123456"}


class Recorder:
    """Хранит список вызовов поддельных функций."""
    def __init__(self):
        self.calls = []


def install_fakes(recorder, text_ok=True, file_ok=True):
    """Подменяет функции отправки и возвращает оригиналы, чтобы потом вернуть их на место."""
    originals = {
        "send_text": wazzup.send_text,
        "send_file": wazzup.send_file,
        "send_telegram_message": telegram.send_telegram_message,
        "send_telegram_photo": telegram.send_telegram_photo,
    }

    async def fake_send_text(api_key, channel_id, chat_id, text):
        recorder.calls.append(("wa_text", api_key, channel_id, chat_id, text))
        if text_ok:
            return {"ok": True, "message_id": "wa-text-1", "error": ""}
        return {"ok": False, "message_id": "", "error": "CHANNEL_NO_MONEY"}

    async def fake_send_file(api_key, channel_id, chat_id, content_uri):
        recorder.calls.append(("wa_file", api_key, channel_id, chat_id, content_uri))
        if file_ok:
            return {"ok": True, "message_id": f"wa-file-{len(recorder.calls)}", "error": ""}
        return {"ok": False, "message_id": "", "error": "BAD_CONTENT"}

    async def fake_tg_message(chat_id, text):
        recorder.calls.append(("tg_text", chat_id, text))
        return {"ok": True, "result": {"message_id": 77}}

    async def fake_tg_photo(chat_id, photo_url, caption=""):
        recorder.calls.append(("tg_photo", chat_id, photo_url))
        return {"ok": True, "result": {"message_id": 78}}

    wazzup.send_text = fake_send_text
    wazzup.send_file = fake_send_file
    telegram.send_telegram_message = fake_tg_message
    telegram.send_telegram_photo = fake_tg_photo
    return originals


def restore(originals):
    wazzup.send_text = originals["send_text"]
    wazzup.send_file = originals["send_file"]
    telegram.send_telegram_message = originals["send_telegram_message"]
    telegram.send_telegram_photo = originals["send_telegram_photo"]


def test_split_images():
    text = "Вот наш кабинет [IMAGE:https://x.com/1.jpg]\n\n\n\nи врач [IMAGE:https://x.com/2.png] [IMAGE:https://x.com/1.jpg]"
    clean, images = messenger.split_images(text)
    assert images == ["https://x.com/1.jpg", "https://x.com/2.png"]
    assert "[IMAGE" not in clean
    assert clean == "Вот наш кабинет\n\nи врач"

    clean, images = messenger.split_images("Без картинок")
    assert clean == "Без картинок"
    assert images == []

    # Не-http ссылки тегом картинки не считаются
    clean, images = messenger.split_images("[IMAGE:ftp://x]")
    assert images == []


def test_whatsapp_text_then_images():
    recorder = Recorder()
    originals = install_fakes(recorder)
    try:
        result = asyncio.run(messenger.send_to_client(
            BOT, WA_DIALOG, "Цены ниже [IMAGE:https://x.com/price.jpg]"))
    finally:
        restore(originals)

    assert result == {"ok": True, "message_id": "wa-text-1", "error": ""}
    assert recorder.calls[0] == ("wa_text", "key", "chan", "77011234567", "Цены ниже")
    assert recorder.calls[1] == ("wa_file", "key", "chan", "77011234567", "https://x.com/price.jpg")
    assert len(recorder.calls) == 2


def test_whatsapp_only_image_message_id_from_file():
    recorder = Recorder()
    originals = install_fakes(recorder)
    try:
        result = asyncio.run(messenger.send_to_client(BOT, WA_DIALOG, "[IMAGE:https://x.com/a.jpg]"))
    finally:
        restore(originals)
    assert result["ok"] is True
    assert result["message_id"] == "wa-file-1"
    assert [call[0] for call in recorder.calls] == ["wa_file"]


def test_whatsapp_not_connected():
    recorder = Recorder()
    originals = install_fakes(recorder)
    try:
        result = asyncio.run(messenger.send_to_client({"id": "b"}, WA_DIALOG, "Привет"))
        result_no_channel = asyncio.run(messenger.send_to_client(
            {"wazzup_api_key": "k", "wazzup_channel_id": ""}, WA_DIALOG, "Привет"))
    finally:
        restore(originals)
    assert result == {"ok": False, "message_id": "", "error": "Wazzup не подключён"}
    assert result_no_channel["error"] == "Wazzup не подключён"
    assert recorder.calls == []


def test_whatsapp_errors():
    recorder = Recorder()
    originals = install_fakes(recorder, text_ok=False)
    try:
        result = asyncio.run(messenger.send_to_client(BOT, WA_DIALOG, "Привет"))
    finally:
        restore(originals)
    assert result["ok"] is False
    assert "CHANNEL_NO_MONEY" in result["error"]

    recorder = Recorder()
    originals = install_fakes(recorder, file_ok=False)
    try:
        result = asyncio.run(messenger.send_to_client(BOT, WA_DIALOG, "Привет [IMAGE:https://x.com/a.jpg]"))
    finally:
        restore(originals)
    # Текст ушёл, картинка нет — ok=False, но message_id текста сохраняем
    assert result["ok"] is False
    assert result["message_id"] == "wa-text-1"
    assert "BAD_CONTENT" in result["error"]


def test_telegram_channel():
    recorder = Recorder()
    originals = install_fakes(recorder)
    try:
        result = asyncio.run(messenger.send_to_client(
            {}, TG_DIALOG, "Здравствуйте [IMAGE:https://x.com/a.jpg]", from_bot=False))
    finally:
        restore(originals)
    assert result == {"ok": True, "message_id": "tg:123456:77", "error": ""}
    assert recorder.calls[0] == ("tg_text", 123456, "Здравствуйте")
    assert recorder.calls[1] == ("tg_photo", 123456, "https://x.com/a.jpg")


def test_telegram_long_text_is_split():
    recorder = Recorder()
    originals = install_fakes(recorder)
    try:
        result = asyncio.run(messenger.send_to_client({}, TG_DIALOG, "а" * 5000))
    finally:
        restore(originals)
    assert result["ok"] is True
    assert len(recorder.calls) == 2
    assert all(len(call[2]) <= messenger.TELEGRAM_MAX_LENGTH for call in recorder.calls)


def test_telegram_error_and_exception():
    originals = {
        "send_text": wazzup.send_text,
        "send_file": wazzup.send_file,
        "send_telegram_message": telegram.send_telegram_message,
        "send_telegram_photo": telegram.send_telegram_photo,
    }

    async def failing_message(chat_id, text):
        return {"ok": False, "description": "Forbidden: bot was blocked by the user"}

    async def raising_photo(chat_id, photo_url, caption=""):
        raise RuntimeError("сеть упала")

    telegram.send_telegram_message = failing_message
    telegram.send_telegram_photo = raising_photo
    try:
        result = asyncio.run(messenger.send_to_client({}, TG_DIALOG, "Привет [IMAGE:https://x.com/a.jpg]"))
    finally:
        restore(originals)
    assert result["ok"] is False
    assert "blocked" in result["error"]
    assert "RuntimeError" in result["error"]


def test_bad_input():
    recorder = Recorder()
    originals = install_fakes(recorder)
    try:
        bad_tg = asyncio.run(messenger.send_to_client({}, {"channel": "telegram", "chat_id": "abc"}, "Привет"))
        empty = asyncio.run(messenger.send_to_client(BOT, WA_DIALOG, "   "))
        unknown = asyncio.run(messenger.send_to_client(BOT, {"channel": "instagram", "chat_id": "1"}, "Привет"))
        no_chat = asyncio.run(messenger.send_to_client(BOT, {"channel": "whatsapp"}, "Привет"))
    finally:
        restore(originals)
    assert bad_tg["ok"] is False
    assert empty["ok"] is False
    assert unknown["ok"] is False
    assert no_chat["ok"] is False
    assert recorder.calls == []
