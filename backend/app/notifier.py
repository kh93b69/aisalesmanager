"""
Уведомления сотрудникам в Telegram: горячий клиент, напоминания о счёте, нужен человек.

Используется отдельный бот уведомлений (TELEGRAM_NOTIFY_BOT_TOKEN).
Сообщения отправляются с parse_mode="HTML", поэтому ВСЕ пользовательские данные
(имя клиента, текст, название города) экранируем через html.escape.
"""
import asyncio
import html
import re

import httpx

from app.config import TELEGRAM_NOTIFY_BOT_TOKEN, BACKEND_URL

# Telegram не принимает сообщения длиннее 4096 символов
TELEGRAM_MAX_LENGTH = 4096

# Сколько символов пользовательского текста (summary, причина) показываем в уведомлении
MAX_USER_TEXT_LENGTH = 1500

# Транспорт httpx — в тестах можно подставить httpx.MockTransport
_transport = None


def _clean_id(value) -> str:
    """Приводит chat_id к строке без пробелов. None → пустая строка."""
    if value is None:
        return ""
    return str(value).strip()


def _short(text: str, limit: int = MAX_USER_TEXT_LENGTH) -> str:
    """Обрезает слишком длинный текст и добавляет многоточие."""
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _html_to_plain(text_html: str) -> str:
    """Убирает HTML-теги и превращает &lt; и т.п. обратно в символы (для отправки без разметки)."""
    without_tags = re.sub(r"<[^>]+>", "", text_html)
    return html.unescape(without_tags)


async def _post_send_message(payload: dict) -> dict:
    """
    Делает запрос sendMessage к Telegram.
    Возвращает {"ok": bool, "description": str}. Исключения наружу не выбрасывает.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_NOTIFY_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15, transport=_transport) as http_client:
            response = await http_client.post(url, json=payload)
    except Exception as e:
        # Текст исключения может содержать URL с токеном — печатаем только тип
        return {"ok": False, "description": f"ошибка соединения: {type(e).__name__}"}

    try:
        data = response.json()
    except Exception:
        return {"ok": False, "description": f"HTTP {response.status_code}"}

    if isinstance(data, dict) and data.get("ok"):
        return {"ok": True, "description": ""}

    description = ""
    if isinstance(data, dict):
        description = str(data.get("description") or "")
    return {"ok": False, "description": description or f"HTTP {response.status_code}"}


async def send_notification(chat_id: str, text_html: str) -> bool:
    """
    Отправляет уведомление в Telegram-чат.
    Если текст слишком длинный или Telegram не смог разобрать HTML — отправляем
    тот же текст без разметки, чтобы уведомление не потерялось.
    Возвращает True, если сообщение доставлено в Telegram.
    """
    chat_id = _clean_id(chat_id)
    if not chat_id:
        return False
    if not TELEGRAM_NOTIFY_BOT_TOKEN:
        print("NOTIFIER: TELEGRAM_NOTIFY_BOT_TOKEN не задан — уведомление не отправлено")
        return False

    if len(text_html) <= TELEGRAM_MAX_LENGTH:
        payload = {
            "chat_id": chat_id,
            "text": text_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        result = await _post_send_message(payload)
        if result["ok"]:
            return True
        print(f"NOTIFIER ERROR: chat {chat_id}: {result['description']}")
        # Ошибка разметки — попробуем без HTML. Остальные ошибки (чат не найден, бот заблокирован) — сдаёмся.
        if "parse" not in result["description"].lower():
            return False

    # Запасной вариант: обычный текст без разметки, обрезанный до лимита Telegram
    plain_text = _html_to_plain(text_html)
    if len(plain_text) > TELEGRAM_MAX_LENGTH:
        plain_text = plain_text[:TELEGRAM_MAX_LENGTH - 1] + "…"
    payload = {
        "chat_id": chat_id,
        "text": plain_text,
        "disable_web_page_preview": True,
    }
    result = await _post_send_message(payload)
    if result["ok"]:
        return True
    print(f"NOTIFIER ERROR: chat {chat_id} (без разметки): {result['description']}")
    return False


def _add_unique(recipients: list[str], chat_id) -> None:
    """Добавляет chat_id в список, если он непустой и его там ещё нет."""
    chat_id = _clean_id(chat_id)
    if chat_id and chat_id not in recipients:
        recipients.append(chat_id)


def get_recipients(bot: dict, city: dict | None, team: list[dict], assigned_user_id: str | None,
                   escalation: bool = False) -> list[str]:
    """
    Решает, кому отправить уведомление. Возвращает список уникальных непустых chat_id.

    Обычное уведомление:
      - чат города (если город известен);
      - если есть назначенный сотрудник — только он, иначе все сотрудники этого города
        и сотрудники «всех городов» (city_id = None). Если город неизвестен — только «все города»;
      - общий чат бота (руководитель), ТОЛЬКО если больше никого нет.
    Эскалация: общий чат бота + чат города.
      Если оба пустые — отправляем как обычное уведомление, чтобы оно не потерялось.
    """
    bot = bot or {}
    team = team or []
    recipients = []

    bot_chat_id = bot.get("telegram_notify_chat_id")
    city_chat_id = city.get("telegram_chat_id") if city else None
    city_id = city.get("id") if city else None

    if escalation:
        _add_unique(recipients, bot_chat_id)
        _add_unique(recipients, city_chat_id)
        if recipients:
            return recipients
        # Ни руководителя, ни чата города — падаем в обычную логику ниже

    _add_unique(recipients, city_chat_id)

    if assigned_user_id:
        for member in team:
            if member.get("user_id") == assigned_user_id:
                _add_unique(recipients, member.get("telegram_chat_id"))
    else:
        for member in team:
            member_city_id = member.get("city_id")
            if member_city_id is None:
                # Сотрудник работает со всеми городами
                _add_unique(recipients, member.get("telegram_chat_id"))
            elif city_id is not None and member_city_id == city_id:
                # Сотрудник именно этого города
                _add_unique(recipients, member.get("telegram_chat_id"))

    if not recipients:
        _add_unique(recipients, bot_chat_id)

    return recipients


def dialog_link(dialog_id: str) -> str:
    """Ссылка на диалог в нашей панели."""
    base_url = (BACKEND_URL or "").rstrip("/")
    return f"{base_url}/?dialog={dialog_id}"


def format_phone(chat_id: str) -> str:
    """
    Превращает chat_id WhatsApp в телефон: "77011234567" → "+77011234567".
    Если в chat_id нет цифр — возвращает как есть.
    """
    raw = _clean_id(chat_id)
    digits = re.sub(r"\D", "", raw.split("@")[0])
    if not digits:
        return raw
    return "+" + digits


def _client_lines(dialog: dict, city: dict | None) -> list[str]:
    """Общие строки уведомления: город, имя, телефон (уже экранированные)."""
    dialog = dialog or {}

    if city and city.get("name"):
        city_text = html.escape(str(city["name"]))
    else:
        city_text = "Город не указан"

    name = _clean_id(dialog.get("contact_name"))
    name_text = html.escape(_short(name, 100)) if name else "Имя не указано"

    lines = [f"📍 {city_text}", f"👤 {name_text}"]

    chat_id = _clean_id(dialog.get("chat_id"))
    if dialog.get("channel") == "telegram":
        lines.append(f"✈️ Telegram, chat_id {html.escape(chat_id)}")
    elif chat_id:
        lines.append(f"📞 {html.escape(format_phone(chat_id))}")

    return lines


def _link_line(dialog: dict) -> str:
    """Строка со ссылкой на диалог."""
    link = html.escape(dialog_link(str((dialog or {}).get("id", ""))), quote=True)
    return f'➡️ <a href="{link}">Открыть диалог</a>'


async def _send_to_all(recipients: list[str], text_html: str, what: str) -> bool:
    """
    Отправляет одно уведомление всем получателям ОДНОВРЕМЕННО (asyncio.gather),
    чтобы недоступный Telegram не задерживал отправку на 15 с × число получателей.
    Возвращает True, если хотя бы одна отправка удалась.
    """
    if not recipients:
        print(f"NOTIFIER: нет получателей для уведомления «{what}» — проверьте настройки Telegram")
        return False

    tasks = []
    for chat_id in recipients:
        tasks.append(send_notification(chat_id, text_html))
    # return_exceptions=True — ошибка одного получателя не ломает отправку остальным
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if result is True:
            return True
    return False


async def notify_hot_lead(bot: dict, dialog: dict, city: dict | None, team: list[dict], summary: str) -> bool:
    """Уведомление «🔥 Горячий клиент — нужен счёт». True — если хоть кому-то доставлено."""
    lines = ["🔥 <b>Горячий клиент — нужен счёт</b>"]
    lines += _client_lines(dialog, city)
    if summary and summary.strip():
        lines.append(f"💬 Хочет: {html.escape(_short(summary))}")
    lines.append(_link_line(dialog))
    text_html = "\n".join(lines)

    recipients = get_recipients(bot, city, team, (dialog or {}).get("assigned_user_id"), escalation=False)
    return await _send_to_all(recipients, text_html, "горячий клиент")


async def notify_payment_sent(bot: dict, dialog: dict, city: dict | None, team: list[dict], summary: str) -> bool:
    """«💳 Клиенту отправлена ссылка на оплату» — сотрудник проверяет поступление и отмечает «Оплачено»."""
    lines = ["💳 <b>Клиенту отправлена ссылка на оплату</b>"]
    lines += _client_lines(dialog, city)
    if summary and summary.strip():
        lines.append(f"💬 Оплачивает: {html.escape(_short(summary))}")
    lines.append("Проверьте поступление и отметьте в панели «Оплачено».")
    lines.append(_link_line(dialog))
    text_html = "\n".join(lines)

    recipients = get_recipients(bot, city, team, (dialog or {}).get("assigned_user_id"), escalation=False)
    return await _send_to_all(recipients, text_html, "ссылка на оплату")


async def notify_reminder(bot: dict, dialog: dict, city: dict | None, team: list[dict],
                          minutes: int, escalation: bool) -> bool:
    """
    Напоминание о неотправленном счёте.
    Обычное: «⏰ Клиент ждёт счёт уже N мин». Эскалация руководителю: «🚨 Счёт не отправлен N мин — проверьте».
    Возвращает True, если хоть кому-то доставлено (по False планировщик повторит напоминание).
    """
    if escalation:
        lines = [f"🚨 <b>Счёт не отправлен {minutes} мин — проверьте</b>"]
    else:
        lines = [f"⏰ <b>Клиент ждёт счёт уже {minutes} мин</b>"]
    lines += _client_lines(dialog, city)
    hot_reason = _clean_id((dialog or {}).get("hot_reason"))
    if hot_reason:
        lines.append(f"💬 Хочет: {html.escape(_short(hot_reason))}")
    lines.append(_link_line(dialog))
    text_html = "\n".join(lines)

    recipients = get_recipients(bot, city, team, (dialog or {}).get("assigned_user_id"), escalation=escalation)
    return await _send_to_all(recipients, text_html, "напоминание о счёте")


async def notify_attention(bot: dict, dialog: dict, city: dict | None, team: list[dict], reason: str) -> bool:
    """Уведомление «⚠️ Нужен менеджер: <причина>». True — если хоть кому-то доставлено."""
    reason_text = html.escape(_short(reason, 500)) if reason and reason.strip() else "без указания причины"
    lines = [f"⚠️ <b>Нужен менеджер:</b> {reason_text}"]
    lines += _client_lines(dialog, city)
    preview = _clean_id((dialog or {}).get("last_message_preview"))
    if preview:
        lines.append(f"💬 Последнее сообщение: {html.escape(_short(preview, 300))}")
    lines.append(_link_line(dialog))
    text_html = "\n".join(lines)

    recipients = get_recipients(bot, city, team, (dialog or {}).get("assigned_user_id"), escalation=False)
    return await _send_to_all(recipients, text_html, "нужен менеджер")


async def notify_unknown_question(bot: dict, dialog: dict, city: dict | None, team: list[dict],
                                  question: str) -> bool:
    """
    «❓ Бот не знает ответа» — руководителю (общий чат бота и чат города).
    Руководитель отвечает в панели (Настройки → «Без ответа») — ответ попадает в базу знаний.
    """
    lines = [f"❓ <b>Бот не знает ответа:</b> {html.escape(_short(question, 500))}"]
    lines += _client_lines(dialog, city)
    lines.append("Клиенту сказали, что уточните и вернётесь с ответом.")
    lines.append("Ответьте в панели: Настройки → «❓ Без ответа» — бот запомнит ответ.")
    lines.append(_link_line(dialog))
    text_html = "\n".join(lines)

    recipients = get_recipients(bot, city, team, (dialog or {}).get("assigned_user_id"), escalation=True)
    return await _send_to_all(recipients, text_html, "вопрос без ответа")


async def notify_text(bot: dict, text_html: str) -> bool:
    """Служебное уведомление (сбои отправки, лимиты) — в общий чат бота. True — если доставлено."""
    chat_id = _clean_id((bot or {}).get("telegram_notify_chat_id"))
    if not chat_id:
        print("NOTIFIER: у бота не задан telegram_notify_chat_id — служебное уведомление не отправлено")
        return False
    return await send_notification(chat_id, text_html)
