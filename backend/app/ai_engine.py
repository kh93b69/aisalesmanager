"""
Генерация ответа клиенту через Claude API (Anthropic SDK).

Модель отвечает строго в JSON по схеме (structured outputs): текст ответа + служебные поля
(город, «горячий» клиент, нужен ли человек и т.д.). Pipeline использует эти поля,
чтобы переключить город, пометить диалог и уведомить сотрудников.
"""

import json

import anthropic

from app.config import ANTHROPIC_API_KEY, DEFAULT_AI_MODEL

# Модели, которые можно выбрать для бота
ALLOWED_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]

# Максимум токенов в ответе (с запасом на короткое адаптивное мышление модели)
MAX_TOKENS = 4000

# Вежливый ответ клиенту, если ИИ не смог ответить (ошибка API, таймаут, отказ)
FALLBACK_REPLY = "Спасибо за сообщение! Сейчас уточню информацию и вернусь к вам 🙏"

# Один клиент на весь модуль. Таймаут 60 с, 2 автоматических повтора при сбоях сети/429/5xx.
# Если ключа нет — клиент всё равно создаётся (с заглушкой), а generate_reply сразу вернёт fallback.
client = anthropic.AsyncAnthropic(
    api_key=ANTHROPIC_API_KEY or "not-configured",
    timeout=60,
    max_retries=2,
)


def build_reply_schema(city_names: list[str]) -> dict:
    """JSON-схема ответа модели. Все поля обязательны, лишние поля запрещены."""
    # Список допустимых городов: названия без дублей + пустая строка («город не назван»)
    city_enum = []
    for name in city_names:
        if name and name not in city_enum:
            city_enum.append(name)
    city_enum.append("")

    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "detected_city": {"type": "string", "enum": city_enum},
            "lead_status": {"type": "string", "enum": ["none", "hot"]},
            "lead_summary": {"type": "string"},
            "need_human": {"type": "boolean"},
            "need_human_reason": {"type": "string"},
            "client_name": {"type": "string"},
        },
        "required": [
            "reply",
            "detected_city",
            "lead_status",
            "lead_summary",
            "need_human",
            "need_human_reason",
            "client_name",
        ],
        "additionalProperties": False,
    }


def choose_model(bot: dict) -> str:
    """Модель бота, если она из разрешённого списка, иначе — модель по умолчанию."""
    model = bot.get("ai_model")
    if model in ALLOWED_MODELS:
        return model
    return DEFAULT_AI_MODEL


def build_request(bot: dict, system_blocks: list[dict], history: list[dict],
                  state_note: str, city_names: list[str]) -> dict:
    """
    Собирает параметры для client.messages.create.

    temperature/top_p/top_k и thinking НЕ передаём — для моделей 5.5 это ошибка 400.
    Служебная заметка (дата, город, статус) идёт ПОСЛЕДНИМ сообщением role="system":
    так меняющиеся данные не ломают кэш системного промпта.
    """
    messages = list(history)
    messages.append({"role": "system", "content": state_note})

    return {
        "model": choose_model(bot),
        "max_tokens": MAX_TOKENS,
        "system": system_blocks,
        "messages": messages,
        "output_config": {
            "effort": "low",
            "format": {
                "type": "json_schema",
                "schema": build_reply_schema(city_names),
            },
        },
    }


def empty_usage() -> dict:
    """Пустая статистика токенов."""
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def extract_usage(response) -> dict:
    """Статистика токенов из ответа API (поля кэша могут быть None)."""
    usage = empty_usage()
    if response.usage is None:
        return usage
    usage["input_tokens"] = response.usage.input_tokens or 0
    usage["output_tokens"] = response.usage.output_tokens or 0
    usage["cache_read_input_tokens"] = response.usage.cache_read_input_tokens or 0
    usage["cache_creation_input_tokens"] = response.usage.cache_creation_input_tokens or 0
    return usage


def make_fallback(reason: str, usage: dict | None = None) -> dict:
    """Результат на случай, когда ИИ не смог ответить: вежливая заглушка + нужен человек."""
    return {
        "reply": FALLBACK_REPLY,
        "detected_city": "",
        "lead_status": "none",
        "lead_summary": "",
        "need_human": True,
        "need_human_reason": reason,
        "client_name": "",
        "ok": False,
        "usage": usage or empty_usage(),
    }


def get_text_from_response(response) -> str:
    """Текст первого блока type == "text" (блоки мышления и прочие пропускаем)."""
    for block in response.content or []:
        if block.type == "text":
            return block.text or ""
    return ""


def parse_response(response, city_names: list[str]) -> dict:
    """
    Разбирает ответ Claude в итоговый dict. Чистая функция — тестируется без сети.
    При отказе модели, обрыве по лимиту токенов или битом JSON — fallback.
    """
    usage = extract_usage(response)
    stop_reason = response.stop_reason

    if stop_reason == "refusal":
        return make_fallback("ИИ отказался отвечать", usage)
    if stop_reason == "max_tokens":
        return make_fallback("Ответ ИИ оборвался (лимит токенов)", usage)

    text = get_text_from_response(response)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return make_fallback("ИИ вернул ответ в неверном формате", usage)

    if not isinstance(data, dict):
        return make_fallback("ИИ вернул ответ в неверном формате", usage)

    reply = str(data.get("reply") or "").strip()
    if not reply:
        return make_fallback("ИИ вернул пустой ответ", usage)

    # Город — только из списка городов бота
    detected_city = str(data.get("detected_city") or "").strip()
    if detected_city not in city_names:
        detected_city = ""

    lead_status = data.get("lead_status")
    if lead_status != "hot":
        lead_status = "none"

    need_human = data.get("need_human") is True
    need_human_reason = str(data.get("need_human_reason") or "").strip()
    if need_human and not need_human_reason:
        need_human_reason = "ИИ просит подключить менеджера"
    if not need_human:
        need_human_reason = ""

    return {
        "reply": reply,
        "detected_city": detected_city,
        "lead_status": lead_status,
        "lead_summary": str(data.get("lead_summary") or "").strip(),
        "need_human": need_human,
        "need_human_reason": need_human_reason,
        "client_name": str(data.get("client_name") or "").strip(),
        "ok": True,
        "usage": usage,
    }


async def generate_reply(bot: dict, system_blocks: list[dict], history: list[dict],
                         state_note: str, city_names: list[str]) -> dict:
    """
    Генерирует ответ клиенту.

    history — [{"role": "user"|"assistant", "content": str}], первая и последняя реплики — user.
    Возвращает dict: reply, detected_city, lead_status, lead_summary, need_human,
    need_human_reason, client_name, ok, usage. Никогда не бросает исключений.
    """
    bot_id = bot.get("id")

    if not ANTHROPIC_API_KEY:
        print(f"AI ERROR bot={bot_id}: не задан ANTHROPIC_API_KEY")
        return make_fallback("ИИ не настроен (нет ключа API)")

    if not history:
        return make_fallback("Пустая история диалога")

    request = build_request(bot, system_blocks, history, state_note, city_names)

    try:
        response = await client.messages.create(**request)
    # Порядок важен: сначала частные классы ошибок, потом общие
    except anthropic.RateLimitError as e:
        print(f"AI ERROR bot={bot_id}: превышен лимит запросов: {e}")
        return make_fallback("ИИ перегружен (лимит запросов)")
    except anthropic.APITimeoutError as e:
        print(f"AI ERROR bot={bot_id}: таймаут: {e}")
        return make_fallback("ИИ не ответил вовремя (таймаут)")
    except anthropic.APIConnectionError as e:
        print(f"AI ERROR bot={bot_id}: нет связи с API: {e}")
        return make_fallback("Нет связи с ИИ")
    except anthropic.APIStatusError as e:
        print(f"AI ERROR bot={bot_id}: статус {e.status_code}: {e.message}")
        return make_fallback(f"Ошибка ИИ (код {e.status_code})")
    except Exception as e:
        print(f"AI ERROR bot={bot_id}: непредвиденная ошибка: {e}")
        return make_fallback("Внутренняя ошибка ИИ")

    try:
        result = parse_response(response, city_names)
    except Exception as e:
        print(f"AI ERROR bot={bot_id}: не удалось разобрать ответ: {e}")
        return make_fallback("Не удалось разобрать ответ ИИ")

    usage = result["usage"]
    print(
        f"AI USAGE bot={bot_id} model={request['model']} in={usage['input_tokens']} "
        f"out={usage['output_tokens']} cache_read={usage['cache_read_input_tokens']} "
        f"cache_write={usage['cache_creation_input_tokens']} stop={response.stop_reason}"
    )
    return result
