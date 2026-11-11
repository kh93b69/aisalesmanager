"""
Тестовый чат с ботом прямо из панели (Настройки → Тест бота).

Владелец пишет сообщения как клиент, а бот отвечает ровно так же, как в WhatsApp:
тот же промпт, та же база знаний города, та же модель. Но:
- ничего не сохраняется в БД (диалог живёт только в браузере);
- ничего не отправляется в WhatsApp, уведомления сотрудникам не приходят.
Так можно настроить анкету и базу знаний ещё до подключения номера.
"""

import time
from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user
from app.access import require_bot_access, check_uuid
from app import pipeline

router = APIRouter()

# Ограничения, чтобы тестовый чат не стал дорогим или бесконечным
MAX_TEST_MESSAGES = 40
MAX_TEST_MESSAGE_LENGTH = 4000
# Всего текста истории, который уходит в ИИ (как и в WhatsApp, берём только последние сообщения)
MAX_TEST_HISTORY_CHARS = 20000

# Статусы, которые браузер может передать (после «горячего» ответа бот ведёт себя как в WhatsApp)
ALLOWED_TEST_STATUSES = ["new", "in_progress", "hot"]

# Не больше 60 тестовых ответов в час на одного бота (токены оплачиваем мы)
MAX_TEST_REPLIES_PER_HOUR = 60
HOUR_SECONDS = 3600

# bot_id → список времён (time.time()) последних тестовых ответов. Хранится в памяти процесса
# (у сервиса 1 реплика); после перезапуска счётчик просто начинается заново.
_test_reply_times = {}


def is_rate_limited(bot_id: str, now: float) -> bool:
    """
    True — лимит тестовых ответов за последний час исчерпан.
    Иначе запоминает текущий запрос и возвращает False.
    """
    recent = []
    for moment in _test_reply_times.get(bot_id, []):
        if now - moment < HOUR_SECONDS:
            recent.append(moment)
    if len(recent) >= MAX_TEST_REPLIES_PER_HOUR:
        _test_reply_times[bot_id] = recent
        return True
    recent.append(now)
    _test_reply_times[bot_id] = recent
    return False


def validate_test_messages(raw_messages) -> list[dict]:
    """
    Проверяет историю из браузера и превращает её в историю для Claude.
    Разрешены только роли user и assistant, текст — непустая строка.
    Последнее сообщение должно быть от клиента (user). Ошибки — HTTPException 400.
    """
    if not isinstance(raw_messages, list) or not raw_messages:
        raise HTTPException(status_code=400, detail="Напишите сообщение")
    if len(raw_messages) > MAX_TEST_MESSAGES:
        raise HTTPException(status_code=400,
                            detail=f"Тестовый диалог слишком длинный — начните новый (максимум {MAX_TEST_MESSAGES} сообщений)")

    history = []
    for item in raw_messages:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="Неверный формат сообщения")
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant"):
            raise HTTPException(status_code=400, detail="Неверная роль сообщения")
        if not isinstance(content, str) or not content.strip():
            continue
        if len(content) > MAX_TEST_MESSAGE_LENGTH:
            raise HTTPException(status_code=400,
                                detail=f"Сообщение слишком длинное (максимум {MAX_TEST_MESSAGE_LENGTH} символов)")
        history.append({"role": role, "content": content.strip()})

    # Как в WhatsApp: только последние HISTORY_LIMIT сообщений и не больше MAX_TEST_HISTORY_CHARS текста
    history = history[-pipeline.HISTORY_LIMIT:]
    total_chars = 0
    for message in history:
        total_chars += len(message["content"])
    while len(history) > 1 and total_chars > MAX_TEST_HISTORY_CHARS:
        total_chars -= len(history[0]["content"])
        history.pop(0)

    # История для Claude должна начинаться с сообщения клиента
    while history and history[0]["role"] != "user":
        history.pop(0)
    if not history or history[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="Последнее сообщение должно быть от клиента")
    return history


def last_client_text(history: list[dict]) -> str:
    """Текст последних подряд идущих сообщений клиента (для определения города и поиска по базе)."""
    parts = []
    for message in reversed(history):
        if message["role"] != "user":
            break
        parts.append(message["content"])
    parts.reverse()
    return "\n".join(parts)


@router.post("/api/bots/{bot_id}/test-chat")
async def test_chat(bot_id: str, data: dict, user: dict = Depends(get_current_user)):
    """
    Один ответ бота в тестовом чате.
    Тело: {"messages": [{"role": "user"|"assistant", "content": "..."}], "city_id": "..." | null}
    Ответ: текст бота + служебные поля (город, горячий клиент, нужен ли человек) + токены.
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Неверный формат данных")
    access = require_bot_access(bot_id, user["id"], owner_only=True)
    bot = access["bot"]

    history = validate_test_messages(data.get("messages"))

    # Подписка владельца должна быть активной (иначе тест — это бесплатный ИИ)
    subscription = pipeline.get_owner_subscription(bot.get("user_id"))
    if pipeline.is_limit_exceeded(subscription, 0):
        raise HTTPException(status_code=403, detail="Подписка неактивна или истекла — тест бота недоступен")

    if is_rate_limited(bot["id"], time.time()):
        raise HTTPException(status_code=429,
                            detail=f"Слишком много тестовых сообщений — не больше {MAX_TEST_REPLIES_PER_HOUR} в час. Попробуйте позже")
    client_text = last_client_text(history)

    # Город: из браузера (если уже определён раньше) или по тексту клиента
    cities = pipeline.get_bot_cities(bot["id"])
    city = None
    if data.get("city_id"):
        city = pipeline.find_city_by_id(cities, check_uuid(data["city_id"], "город (city_id)"))
    if city is None:
        city = pipeline.detect_city(client_text, cities, None)

    # Заглушка диалога — только для служебной заметки. Статус приходит из браузера:
    # после «горячего» ответа бот не должен продавать повторно (как в WhatsApp)
    status = data.get("status")
    if status not in ALLOWED_TEST_STATUSES:
        status = "new"
    fake_dialog = {"status": status, "contact_name": None}

    result = await pipeline.build_and_generate(bot, fake_dialog, cities, city, history, client_text)

    # ИИ определил другой город → повтор по базе знаний правильного города (как в WhatsApp)
    detected = pipeline.find_city_by_name(cities, result.get("detected_city") or "")
    current_city_id = city["id"] if city else None
    if detected and detected.get("id") != current_city_id:
        city = detected
        # Повторный вызов ИИ тоже расходует токены — считаем его в лимите
        is_rate_limited(bot["id"], time.time())
        result = await pipeline.build_and_generate(bot, fake_dialog, cities, city, history, client_text)

    return {
        "reply": pipeline.strip_staff_prefix(result.get("reply") or ""),
        "city_id": city["id"] if city else None,
        "city_name": city.get("name") if city else None,
        "lead_status": result.get("lead_status") or "none",
        "lead_summary": result.get("lead_summary") or "",
        "need_human": bool(result.get("need_human")),
        "need_human_reason": result.get("need_human_reason") or "",
        "client_name": result.get("client_name") or "",
        "ok": bool(result.get("ok", True)),
        "model": bot.get("ai_model") or "",
        "usage": result.get("usage") or {},
    }
