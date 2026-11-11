"""
Тесты уведомлений: выбор получателей (get_recipients) и тексты уведомлений.
В Telegram не ходим — подменяем функцию отправки.
"""
import asyncio
import json

import httpx

from app import notifier


BOT = {"id": "b1", "telegram_notify_chat_id": "-100boss"}
ASTANA = {"id": "c-astana", "name": "Астана", "telegram_chat_id": "-100astana"}
ALMATY = {"id": "c-almaty", "name": "Алматы", "telegram_chat_id": None}
TEAM = [
    {"user_id": "u1", "city_id": "c-astana", "telegram_chat_id": "111"},
    {"user_id": "u2", "city_id": None, "telegram_chat_id": "222"},
    {"user_id": "u3", "city_id": "c-almaty", "telegram_chat_id": "333"},
    {"user_id": "u4", "city_id": "c-astana", "telegram_chat_id": ""},       # без Telegram
    {"user_id": "u5", "city_id": "c-astana", "telegram_chat_id": "-100astana"},  # дубль чата города
]


# ---------- get_recipients ----------

def test_city_known_no_assigned():
    result = notifier.get_recipients(BOT, ASTANA, TEAM, None)
    # чат города + сотрудники Астаны + «все города»; без дублей и пустых; без руководителя
    assert result == ["-100astana", "111", "222"]


def test_city_known_assigned_user():
    result = notifier.get_recipients(BOT, ASTANA, TEAM, "u3")
    assert result == ["-100astana", "333"]


def test_assigned_user_without_telegram_and_no_city_chat_falls_back_to_bot():
    result = notifier.get_recipients(BOT, ALMATY, TEAM, "u4")
    assert result == ["-100boss"]


def test_city_unknown_only_all_city_members():
    result = notifier.get_recipients(BOT, None, TEAM, None)
    assert result == ["222"]


def test_city_without_chat_and_members():
    result = notifier.get_recipients(BOT, ALMATY, TEAM, None)
    assert result == ["222", "333"]


def test_fallback_to_bot_chat_when_nobody():
    result = notifier.get_recipients(BOT, None, [], None)
    assert result == ["-100boss"]

    team_city_only = [{"user_id": "u1", "city_id": "c-astana", "telegram_chat_id": "111"}]
    result = notifier.get_recipients(BOT, None, team_city_only, None)
    assert result == ["-100boss"]


def test_nobody_at_all_returns_empty():
    result = notifier.get_recipients({"telegram_notify_chat_id": None}, None, [], None)
    assert result == []


def test_escalation():
    result = notifier.get_recipients(BOT, ASTANA, TEAM, "u1", escalation=True)
    assert result == ["-100boss", "-100astana"]

    # Город без чата — только руководитель
    result = notifier.get_recipients(BOT, ALMATY, TEAM, None, escalation=True)
    assert result == ["-100boss"]

    # Город неизвестен — только руководитель
    result = notifier.get_recipients(BOT, None, TEAM, None, escalation=True)
    assert result == ["-100boss"]


def test_escalation_dedup_and_fallback():
    same_chat_bot = {"telegram_notify_chat_id": "-100astana"}
    result = notifier.get_recipients(same_chat_bot, ASTANA, TEAM, None, escalation=True)
    assert result == ["-100astana"]

    # Ни руководителя, ни чата города — эскалация уходит обычным получателям, чтобы не потеряться
    result = notifier.get_recipients({}, ALMATY, TEAM, None, escalation=True)
    assert result == ["222", "333"]


def test_numeric_chat_ids_become_strings():
    team = [{"user_id": "u1", "city_id": None, "telegram_chat_id": 12345}]
    result = notifier.get_recipients({"telegram_notify_chat_id": 999}, None, team, None)
    assert result == ["12345"]


# ---------- форматирование ----------

def test_format_phone_and_link():
    assert notifier.format_phone("77011234567") == "+77011234567"
    assert notifier.format_phone("77011234567@c.us") == "+77011234567"
    assert notifier.format_phone("+7 701 123-45-67") == "+77011234567"
    base_url = (notifier.BACKEND_URL or "").rstrip("/")
    assert notifier.dialog_link("d1") == f"{base_url}/?dialog=d1"


def capture_sends():
    """Подменяет send_notification: вместо Telegram складывает (chat_id, text) в список."""
    sent = []

    async def fake_send(chat_id, text_html):
        sent.append((chat_id, text_html))
        return True

    return sent, fake_send


def test_notify_hot_lead_text_and_escaping():
    sent, fake_send = capture_sends()
    original = notifier.send_notification
    notifier.send_notification = fake_send
    try:
        dialog = {"id": "d1", "chat_id": "77011234567", "channel": "whatsapp",
                  "contact_name": "Айгерим <script>", "assigned_user_id": None}
        asyncio.run(notifier.notify_hot_lead(BOT, dialog, ASTANA, TEAM, "Приём у гинеколога & УЗИ"))
    finally:
        notifier.send_notification = original

    assert [chat for chat, _ in sent] == ["-100astana", "111", "222"]
    text = sent[0][1]
    assert text.startswith("🔥 <b>Горячий клиент — нужен счёт</b>")
    assert "📍 Астана" in text
    assert "👤 Айгерим &lt;script&gt;" in text
    assert "📞 +77011234567" in text
    assert "💬 Хочет: Приём у гинеколога &amp; УЗИ" in text
    assert "?dialog=d1\">Открыть диалог</a>" in text


def test_notify_reminder_and_escalation():
    sent, fake_send = capture_sends()
    original = notifier.send_notification
    notifier.send_notification = fake_send
    dialog = {"id": "d2", "chat_id": "77019998877", "channel": "whatsapp", "hot_reason": "Хочет МРТ"}
    try:
        asyncio.run(notifier.notify_reminder(BOT, dialog, None, TEAM, 10, escalation=False))
        asyncio.run(notifier.notify_reminder(BOT, dialog, ASTANA, TEAM, 20, escalation=True))
    finally:
        notifier.send_notification = original

    assert sent[0][0] == "222"
    assert "⏰ <b>Клиент ждёт счёт уже 10 мин</b>" in sent[0][1]
    assert "Город не указан" in sent[0][1]
    assert "Имя не указано" in sent[0][1]
    assert "Хочет МРТ" in sent[0][1]
    assert [chat for chat, _ in sent[1:]] == ["-100boss", "-100astana"]
    assert "🚨 <b>Счёт не отправлен 20 мин — проверьте</b>" in sent[1][1]


def test_notify_attention_and_text():
    sent, fake_send = capture_sends()
    original = notifier.send_notification
    notifier.send_notification = fake_send
    dialog = {"id": "d3", "chat_id": "555", "channel": "telegram", "last_message_preview": "Позовите врача"}
    try:
        asyncio.run(notifier.notify_attention(BOT, dialog, None, [], "Просит человека"))
        asyncio.run(notifier.notify_text(BOT, "Лимит исчерпан"))
        asyncio.run(notifier.notify_text({}, "никуда"))
    finally:
        notifier.send_notification = original

    assert sent[0][0] == "-100boss"
    assert "⚠️ <b>Нужен менеджер:</b> Просит человека" in sent[0][1]
    assert "Telegram" in sent[0][1]
    assert "Позовите врача" in sent[0][1]
    assert sent[1] == ("-100boss", "Лимит исчерпан")
    assert len(sent) == 2


def test_long_summary_is_truncated():
    sent, fake_send = capture_sends()
    original = notifier.send_notification
    notifier.send_notification = fake_send
    try:
        asyncio.run(notifier.notify_hot_lead(BOT, {"id": "d"}, None, [], "а" * 10000))
    finally:
        notifier.send_notification = original
    assert len(sent[0][1]) < notifier.TELEGRAM_MAX_LENGTH


# ---------- send_notification (через MockTransport) ----------

def test_send_notification_payload_and_plain_fallback():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        # Первая попытка (HTML) — ошибка разбора разметки, вторая — успех
        if len(requests) == 1:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    original_token = notifier.TELEGRAM_NOTIFY_BOT_TOKEN
    notifier.TELEGRAM_NOTIFY_BOT_TOKEN = "test-token"
    notifier._transport = httpx.MockTransport(handler)
    try:
        ok = asyncio.run(notifier.send_notification("-100", "<b>Привет &amp; пока</b>"))
    finally:
        notifier._transport = None
        notifier.TELEGRAM_NOTIFY_BOT_TOKEN = original_token

    assert ok is True
    assert requests[0]["parse_mode"] == "HTML"
    assert requests[0]["disable_web_page_preview"] is True
    assert requests[0]["chat_id"] == "-100"
    assert "parse_mode" not in requests[1]
    assert requests[1]["text"] == "Привет & пока"


def test_send_notification_too_long_and_errors():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    original_token = notifier.TELEGRAM_NOTIFY_BOT_TOKEN
    notifier.TELEGRAM_NOTIFY_BOT_TOKEN = "test-token"
    notifier._transport = httpx.MockTransport(handler)
    try:
        ok = asyncio.run(notifier.send_notification("1", "<b>x</b>" + "я" * 5000))
        empty_chat = asyncio.run(notifier.send_notification("", "текст"))
    finally:
        notifier._transport = None
        notifier.TELEGRAM_NOTIFY_BOT_TOKEN = original_token

    assert ok is True
    assert empty_chat is False
    assert len(requests) == 1
    assert len(requests[0]["text"]) <= notifier.TELEGRAM_MAX_LENGTH
    assert "parse_mode" not in requests[0]


def test_send_notification_chat_not_found_no_retry():
    requests = []

    def handler(request):
        requests.append(1)
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    original_token = notifier.TELEGRAM_NOTIFY_BOT_TOKEN
    notifier.TELEGRAM_NOTIFY_BOT_TOKEN = "test-token"
    notifier._transport = httpx.MockTransport(handler)
    try:
        ok = asyncio.run(notifier.send_notification("1", "текст"))
    finally:
        notifier._transport = None
        notifier.TELEGRAM_NOTIFY_BOT_TOKEN = original_token

    assert ok is False
    assert len(requests) == 1


# ---------- возвращаемое значение notify_* и параллельная отправка ----------

def test_notify_functions_return_bool():
    sent, fake_send = capture_sends()
    original = notifier.send_notification
    notifier.send_notification = fake_send
    dialog = {"id": "d1", "chat_id": "7701"}
    try:
        assert asyncio.run(notifier.notify_hot_lead(BOT, dialog, ASTANA, TEAM, "МРТ")) is True
        assert asyncio.run(notifier.notify_reminder(BOT, dialog, ASTANA, TEAM, 10, escalation=True)) is True
        assert asyncio.run(notifier.notify_attention(BOT, dialog, None, [], "вопрос")) is True
        assert asyncio.run(notifier.notify_text(BOT, "текст")) is True
        # Получателей нет — False
        no_chats_bot = {"telegram_notify_chat_id": None}
        assert asyncio.run(notifier.notify_hot_lead(no_chats_bot, dialog, None, [], "МРТ")) is False
        assert asyncio.run(notifier.notify_reminder(no_chats_bot, dialog, None, [], 10, escalation=True)) is False
        assert asyncio.run(notifier.notify_attention(no_chats_bot, dialog, None, [], "вопрос")) is False
        assert asyncio.run(notifier.notify_text(no_chats_bot, "текст")) is False
    finally:
        notifier.send_notification = original


def test_notify_true_if_at_least_one_delivered():
    async def partly_failing_send(chat_id, text_html):
        if chat_id == "222":
            return True
        if chat_id == "111":
            raise RuntimeError("неожиданная ошибка")
        return False

    async def all_failing_send(chat_id, text_html):
        return False

    original = notifier.send_notification
    try:
        notifier.send_notification = partly_failing_send
        assert asyncio.run(notifier.notify_hot_lead(BOT, {"id": "d"}, ASTANA, TEAM, "x")) is True
        notifier.send_notification = all_failing_send
        assert asyncio.run(notifier.notify_hot_lead(BOT, {"id": "d"}, ASTANA, TEAM, "x")) is False
        assert asyncio.run(notifier.notify_text(BOT, "x")) is False
    finally:
        notifier.send_notification = original


def test_recipients_are_notified_in_parallel():
    # Каждая «отправка» ждёт 0.2 с. Трём получателям последовательно нужно 0.6 с, параллельно — ~0.2 с
    async def slow_send(chat_id, text_html):
        await asyncio.sleep(0.2)
        return True

    async def measure():
        loop = asyncio.get_running_loop()
        start = loop.time()
        ok = await notifier.notify_hot_lead(BOT, {"id": "d"}, ASTANA, TEAM, "x")
        return ok, loop.time() - start

    original = notifier.send_notification
    notifier.send_notification = slow_send
    try:
        ok, elapsed = asyncio.run(measure())
    finally:
        notifier.send_notification = original
    assert ok is True
    assert elapsed < 0.45
