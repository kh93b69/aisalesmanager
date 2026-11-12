"""
API настроек: боты, подключение Wazzup, города, база знаний, картинки,
команда (менеджеры) и тестовое уведомление в Telegram.

Почти всё здесь — только для владельца бота (owner).
Проверки доступа — через app.access.
"""

import io
import re
import csv
import uuid
import asyncio
import fitz  # pymupdf — для PDF
import openpyxl  # для Excel
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from app.database import supabase
from app.auth import get_current_user
from app.access import require_bot_access, public_bot, check_uuid
from app import wazzup
from app import notifier
from app import pipeline
from app import followups
from app import work_hours
from app import daily_report
# Метка фрагмента «вопрос клиента — ответ руководителя» (раздел «Без ответа»)
from app.routes.questions import QA_PREFIX

router = APIRouter()

# Допустимые модели ИИ и режимы распределения (как в CHECK-ограничениях миграции)
ALLOWED_AI_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]
ALLOWED_ASSIGNMENT_MODES = ["none", "round_robin"]
ALLOWED_BUSINESS_TYPES = ["general", "clinic"]

# Текстовые поля анкеты компании: поле → (название для ошибки, максимальная длина)
PROFILE_TEXT_FIELDS = {
    "company_name": ("Название компании", 100),
    "assistant_name": ("Имя ассистента", 50),
    "business_description": ("Описание компании", 3000),
    "target_action": ("Целевое действие", 500),
    "greeting_message": ("Приветствие", 1000),
    "sales_script": ("Скрипт продаж", 20000),
    "off_hours_message": ("Автоответ в нерабочее время", 1000),
    "payment_details": ("Реквизиты для оплаты", 1000),
}

# Поля-переключатели бота: поле → название для ошибки
BOT_BOOL_FIELDS = {
    "followups_enabled": "Дожимы",
    "work_hours_enabled": "График работы",
    "payment_enabled": "Оплата",
    "daily_report_enabled": "Сводка дня",
}

# Ссылки, которые бот отправляет по ситуации
MAX_LINKS = 30
MAX_LINK_TITLE = 100
MAX_LINK_URL = 500
MAX_LINK_WHEN = 300

# Максимальная длина фрагмента базы знаний для PDF/TXT
CHUNK_MAX_CHARS = 800

# Максимальный размер файла базы знаний (20 МБ) и картинки (10 МБ)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Разрешённые форматы базы знаний
KNOWLEDGE_EXTENSIONS = [".txt", ".pdf", ".csv", ".xlsx"]

# Разрешённые картинки: расширение → content-type (тип берём по расширению, а не от клиента)
IMAGE_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# Сколько строк вставлять/удалять за один запрос к Supabase
DB_BATCH_SIZE = 100

# Supabase отдаёт максимум 1000 строк за запрос — читаем страницами
PAGE_SIZE = 1000

# Префикс картинок в knowledge_chunks
IMAGE_PREFIX = "[IMAGE:"


# ============================================================
# Чистые функции (без БД) — их проверяют тесты
# ============================================================

def normalize_chat_id(value, field_label: str = "Telegram chat_id") -> str | None:
    """
    Приводит Telegram chat_id к строке. Пустое значение → None.
    Допустимо: число (для групп — с минусом) или @username канала.
    Иначе — HTTPException 400.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field_label}: неверный формат")
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d{1,20}", text) or re.fullmatch(r"@[A-Za-z0-9_]{4,64}", text):
        return text
    raise HTTPException(
        status_code=400,
        detail=f"{field_label}: укажите число (для групп — с минусом, например -1001234567890) или @username канала",
    )


def validate_links(raw_links) -> list[dict]:
    """Список ссылок бота: [{"title", "url", "when"}]. Пустые строки пропускаем, ошибки — 400."""
    if raw_links is None:
        return []
    if not isinstance(raw_links, list):
        raise HTTPException(status_code=400, detail="Ссылки — неверный формат")
    if len(raw_links) > MAX_LINKS:
        raise HTTPException(status_code=400, detail=f"Ссылок не больше {MAX_LINKS}")
    links = []
    for item in raw_links:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="Ссылки — неверный формат")
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        url = item.get("url") if isinstance(item.get("url"), str) else ""
        when = item.get("when") if isinstance(item.get("when"), str) else ""
        title, url, when = title.strip(), url.strip(), when.strip()
        if not url and not title:
            continue
        if not (url.startswith("https://") or url.startswith("http://")) or " " in url:
            raise HTTPException(status_code=400, detail=f"Ссылка «{title or url}»: адрес должен начинаться с https://")
        if len(title) > MAX_LINK_TITLE or len(url) > MAX_LINK_URL or len(when) > MAX_LINK_WHEN:
            raise HTTPException(status_code=400, detail=f"Ссылка «{title or url}»: слишком длинный текст")
        links.append({"title": title, "url": url, "when": when})
    return links


def validate_followups(raw_steps) -> list[dict]:
    """Шаги дожима: [{"hours": 1..168, "text": "..."}], не больше 3. Ошибки — 400."""
    if raw_steps is None:
        return []
    if not isinstance(raw_steps, list):
        raise HTTPException(status_code=400, detail="Дожимы — неверный формат")
    if len(raw_steps) > followups.MAX_FOLLOWUP_STEPS:
        raise HTTPException(status_code=400, detail=f"Дожимов не больше {followups.MAX_FOLLOWUP_STEPS}")
    steps = []
    for number, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="Дожимы — неверный формат")
        hours = item.get("hours")
        if isinstance(hours, str) and hours.strip().isdigit():
            hours = int(hours.strip())
        if isinstance(hours, bool) or not isinstance(hours, int) \
                or hours < followups.MIN_FOLLOWUP_HOURS or hours > followups.MAX_FOLLOWUP_HOURS:
            raise HTTPException(status_code=400,
                                detail=f"Дожим №{number}: часы — целое число от {followups.MIN_FOLLOWUP_HOURS} до {followups.MAX_FOLLOWUP_HOURS}")
        text = item.get("text") if isinstance(item.get("text"), str) else ""
        if len(text.strip()) > followups.MAX_FOLLOWUP_TEXT:
            raise HTTPException(status_code=400, detail=f"Дожим №{number}: текст не длиннее {followups.MAX_FOLLOWUP_TEXT} символов")
        steps.append({"hours": hours, "text": text.strip()})
    return steps


def validate_schedule(raw_schedule) -> list[dict]:
    """График работы: ровно 7 дней (пн…вс) {"enabled", "start": "ЧЧ:ММ", "end": "ЧЧ:ММ"}. Ошибки — 400."""
    if not isinstance(raw_schedule, list) or len(raw_schedule) != work_hours.DAYS_IN_WEEK:
        raise HTTPException(status_code=400, detail="График работы — неверный формат (нужно 7 дней)")
    schedule = []
    for index, day in enumerate(raw_schedule):
        day_name = work_hours.WEEKDAYS_SHORT[index]
        if not isinstance(day, dict):
            raise HTTPException(status_code=400, detail="График работы — неверный формат")
        enabled = day.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail=f"График, {day_name}: укажите, рабочий ли день")
        start = day.get("start")
        end = day.get("end")
        if work_hours.parse_hhmm(start) is None or work_hours.parse_hhmm(end) is None:
            raise HTTPException(status_code=400, detail=f"График, {day_name}: время в формате ЧЧ:ММ, например 09:00")
        # Приводим «9:5» к «09:05»
        start_minutes = work_hours.parse_hhmm(start)
        end_minutes = work_hours.parse_hhmm(end)
        schedule.append({
            "enabled": enabled,
            "start": f"{start_minutes // 60:02d}:{start_minutes % 60:02d}",
            "end": f"{end_minutes // 60:02d}:{end_minutes % 60:02d}",
        })
    return schedule


def validate_url(value, title: str) -> str:
    """Ссылка: пусто или адрес с http(s):// без пробелов. Ошибки — 400."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{title} — должно быть текстом")
    url = value.strip()
    if not url:
        return ""
    if not (url.startswith("https://") or url.startswith("http://")) or " " in url:
        raise HTTPException(status_code=400, detail=f"{title}: адрес должен начинаться с https://")
    if len(url) > MAX_LINK_URL:
        raise HTTPException(status_code=400, detail=f"{title}: слишком длинный адрес")
    return url


def check_final_bot_state(bot: dict, fields: dict) -> None:
    """
    Проверка бота ПОСЛЕ изменений (сохранённые значения + новые): нельзя включить график
    без рабочих дней или оплату без ссылки и реквизитов. Ошибки — 400.
    """
    final = dict(bot)
    final.update(fields)
    if final.get("work_hours_enabled"):
        has_work_day = False
        for day in work_hours.clean_schedule(final.get("work_schedule")):
            if day["enabled"]:
                has_work_day = True
        if not has_work_day:
            raise HTTPException(status_code=400, detail="Отметьте хотя бы один рабочий день")
    if final.get("payment_enabled"):
        if not str(final.get("payment_link") or "").strip() and not str(final.get("payment_details") or "").strip():
            raise HTTPException(status_code=400, detail="Укажите ссылку на оплату или реквизиты")


def validate_bot_update(data: dict) -> dict:
    """
    Белый список полей бота, которые может менять владелец.
    Неизвестные поля молча игнорируются (фронтенд может прислать весь объект бота).
    Неверные значения — HTTPException 400.
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Неверный формат данных")

    clean = {}

    if "name" in data:
        name = data["name"]
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="Название бота не может быть пустым")
        if len(name.strip()) > 100:
            raise HTTPException(status_code=400, detail="Название бота — не длиннее 100 символов")
        clean["name"] = name.strip()

    if "system_prompt" in data:
        prompt = data["system_prompt"]
        if prompt is None:
            prompt = ""
        if not isinstance(prompt, str):
            raise HTTPException(status_code=400, detail="Инструкция для ИИ должна быть текстом")
        if len(prompt) > 50000:
            raise HTTPException(status_code=400, detail="Инструкция для ИИ слишком длинная (максимум 50 000 символов)")
        clean["system_prompt"] = prompt

    if "ai_model" in data:
        if data["ai_model"] not in ALLOWED_AI_MODELS:
            raise HTTPException(status_code=400, detail="Неизвестная модель ИИ")
        clean["ai_model"] = data["ai_model"]

    if "business_type" in data:
        if data["business_type"] not in ALLOWED_BUSINESS_TYPES:
            raise HTTPException(status_code=400, detail="Неизвестный тип бизнеса")
        clean["business_type"] = data["business_type"]

    # Анкета компании: обычные строки ограниченной длины (None → пустая строка)
    for field_name in PROFILE_TEXT_FIELDS:
        if field_name not in data:
            continue
        title, max_length = PROFILE_TEXT_FIELDS[field_name]
        value = data[field_name]
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{title} — должно быть текстом")
        value = value.strip()
        if len(value) > max_length:
            raise HTTPException(status_code=400, detail=f"{title} — не длиннее {max_length} символов")
        clean[field_name] = value

    if "telegram_notify_chat_id" in data:
        clean["telegram_notify_chat_id"] = normalize_chat_id(
            data["telegram_notify_chat_id"], "Чат уведомлений руководителя"
        )

    if "invoice_reminder_minutes" in data:
        minutes = data["invoice_reminder_minutes"]
        # bool — тоже int в Python, поэтому отсекаем его отдельно
        if isinstance(minutes, bool):
            raise HTTPException(status_code=400, detail="Минуты до напоминания — целое число от 1 до 240")
        if isinstance(minutes, str) and minutes.strip().isdigit():
            minutes = int(minutes.strip())
        if isinstance(minutes, float) and minutes.is_integer():
            minutes = int(minutes)
        if not isinstance(minutes, int) or minutes < 1 or minutes > 240:
            raise HTTPException(status_code=400, detail="Минуты до напоминания — целое число от 1 до 240")
        clean["invoice_reminder_minutes"] = minutes

    if "links" in data:
        clean["links"] = validate_links(data["links"])

    for field_name in BOT_BOOL_FIELDS:
        if field_name in data:
            if not isinstance(data[field_name], bool):
                raise HTTPException(status_code=400, detail=f"{BOT_BOOL_FIELDS[field_name]}: укажите, включено ли")
            clean[field_name] = data[field_name]

    if "work_schedule" in data:
        clean["work_schedule"] = validate_schedule(data["work_schedule"])
        has_work_day = False
        for day in clean["work_schedule"]:
            if day["enabled"]:
                has_work_day = True
        if clean.get("work_hours_enabled") and not has_work_day:
            raise HTTPException(status_code=400, detail="Отметьте хотя бы один рабочий день")

    if "payment_link" in data:
        clean["payment_link"] = validate_url(data["payment_link"], "Ссылка на оплату")
    if clean.get("payment_enabled") and "payment_link" in clean and "payment_details" in clean:
        if not clean["payment_link"] and not clean["payment_details"]:
            raise HTTPException(status_code=400, detail="Укажите ссылку на оплату или реквизиты")

    if "daily_report_time" in data:
        minutes = work_hours.parse_hhmm(data["daily_report_time"])
        if minutes is None:
            raise HTTPException(status_code=400, detail="Время сводки — в формате ЧЧ:ММ, например 20:00")
        clean["daily_report_time"] = f"{minutes // 60:02d}:{minutes % 60:02d}"

    if "followups" in data:
        clean["followups"] = validate_followups(data["followups"])

    if "assignment_mode" in data:
        if data["assignment_mode"] not in ALLOWED_ASSIGNMENT_MODES:
            raise HTTPException(status_code=400, detail="Неизвестный режим распределения")
        clean["assignment_mode"] = data["assignment_mode"]

    # telegram_enabled намеренно НЕ в белом списке: Telegram-канал привязывается
    # к боту через переменную окружения TELEGRAM_BOT_ID (иначе любой мог бы его перехватить)

    return clean


def validate_city_data(data: dict, partial: bool = False) -> dict:
    """
    Проверяет поля города. partial=True — для PUT (можно прислать не все поля).
    Возвращает словарь только с разрешёнными полями. Ошибки — HTTPException 400.
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Неверный формат данных")

    clean = {}

    if "name" in data or not partial:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="Укажите название города")
        if len(name.strip()) > 100:
            raise HTTPException(status_code=400, detail="Название города — не длиннее 100 символов")
        clean["name"] = name.strip()

    if "aliases" in data:
        aliases = data["aliases"] or ""
        if not isinstance(aliases, str):
            raise HTTPException(status_code=400, detail="Варианты написания — текст через запятую")
        if len(aliases) > 1000:
            raise HTTPException(status_code=400, detail="Слишком много вариантов написания")
        clean["aliases"] = aliases.strip()

    if "address" in data:
        address = data["address"] or ""
        if not isinstance(address, str):
            raise HTTPException(status_code=400, detail="Адрес должен быть текстом")
        if len(address) > 3000:
            raise HTTPException(status_code=400, detail="Адрес слишком длинный (максимум 3000 символов)")
        clean["address"] = address.strip()

    if "telegram_chat_id" in data:
        clean["telegram_chat_id"] = normalize_chat_id(data["telegram_chat_id"], "Telegram chat_id города")

    if "sort_order" in data:
        sort_order = data["sort_order"]
        if sort_order is None or sort_order == "":
            sort_order = 0
        if isinstance(sort_order, str) and re.fullmatch(r"-?\d+", sort_order.strip()):
            sort_order = int(sort_order.strip())
        if isinstance(sort_order, bool) or not isinstance(sort_order, int):
            raise HTTPException(status_code=400, detail="Порядок сортировки — целое число")
        clean["sort_order"] = sort_order

    return clean


def filter_whatsapp_channels(channels: list) -> list[dict]:
    """Оставляет только каналы WhatsApp и только нужные фронтенду поля."""
    result = []
    for channel in channels or []:
        if not isinstance(channel, dict):
            continue
        if channel.get("transport") != "whatsapp":
            continue
        result.append({
            "channelId": channel.get("channelId"),
            "transport": channel.get("transport"),
            "plainId": channel.get("plainId"),
            "state": channel.get("state"),
        })
    return result


def find_channel(channels: list, channel_id: str) -> dict | None:
    """Ищет канал по channelId в списке каналов Wazzup."""
    for channel in channels or []:
        if isinstance(channel, dict) and channel.get("channelId") == channel_id:
            return channel
    return None


def _split_long_piece(piece: str, max_chars: int) -> list[str]:
    """
    Делит слишком длинный абзац: сначала по предложениям, затем по словам,
    и только в крайнем случае (одно «слово» длиннее лимита) — жёстко.
    """
    sentences = re.split(r"(?<=[.!?…])\s+", piece)
    parts = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Предложение само по себе длиннее лимита — режем по словам
        if len(sentence) > max_chars:
            if current:
                parts.append(current)
                current = ""
            for word in sentence.split():
                # Очень длинное «слово» (например, ссылка) — режем жёстко
                while len(word) > max_chars:
                    if current:
                        parts.append(current)
                        current = ""
                    parts.append(word[:max_chars])
                    word = word[max_chars:]
                if not word:
                    continue
                if not current:
                    current = word
                elif len(current) + 1 + len(word) <= max_chars:
                    current = current + " " + word
                else:
                    parts.append(current)
                    current = word
            continue

        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current = current + " " + sentence
        else:
            parts.append(current)
            current = sentence

    if current:
        parts.append(current)
    return parts


def split_text_into_chunks(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """
    Нарезает текст (PDF/TXT) на фрагменты до max_chars символов.
    Сначала объединяет целые абзацы, длинные абзацы делит по предложениям,
    чтобы не рвать слова и смысл.
    """
    if not text:
        return []

    # Абзацы — разделены пустой строкой
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n\s*\n", text)

    chunks = []
    current = ""

    for paragraph in paragraphs:
        # Внутри абзаца лишние пробелы и переносы схлопываем
        paragraph = re.sub(r"[ \t]+", " ", paragraph)
        paragraph = re.sub(r"\s*\n\s*", "\n", paragraph).strip()
        if not paragraph:
            continue

        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            # Переносы строк внутри длинного абзаца считаем пробелами
            chunks.extend(_split_long_piece(paragraph.replace("\n", " "), max_chars))
            continue

        if not current:
            current = paragraph
        elif len(current) + 2 + len(paragraph) <= max_chars:
            current = current + "\n\n" + paragraph
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)
    return chunks


def parse_city_id_param(city_id) -> str | None:
    """Пустой city_id (или "null", "general") означает общую базу знаний."""
    if city_id is None:
        return None
    text = str(city_id).strip()
    if text == "" or text.lower() in ("null", "none", "general"):
        return None
    return check_uuid(text, "город (city_id)")


def parse_image_content(content: str) -> dict:
    """Разбирает строку "[IMAGE:url] filename" в {"url", "name"}."""
    url = content.split("]")[0].replace(IMAGE_PREFIX, "")
    name = content.split("] ", 1)[1] if "] " in content else "image"
    return {"url": url, "name": name}


def file_extension(filename: str) -> str:
    """Расширение файла в нижнем регистре с точкой: "Прайс.PDF" → ".pdf"."""
    name = (filename or "").lower()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[1]


def safe_storage_name(filename: str) -> str:
    """Имя файла для Supabase Storage: только латиница, цифры, точка, дефис, подчёркивание."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "image.jpg")
    name = name.strip("._") or "image.jpg"
    return name[-100:]


# ============================================================
# Вспомогательные функции с БД
# ============================================================

def get_city_of_bot(city_id: str, bot_id: str) -> dict:
    """Загружает город и проверяет, что он принадлежит боту. Иначе 404."""
    try:
        result = supabase.table("cities").select("*").eq("id", city_id).limit(1).execute()
    except Exception:
        raise HTTPException(status_code=404, detail="Город не найден")
    if not result.data or str(result.data[0].get("bot_id")) != str(bot_id):
        raise HTTPException(status_code=404, detail="Город не найден")
    return result.data[0]


def count_text_chunks(bot_id: str, city_id: str | None) -> int:
    """Считает текстовые фрагменты базы знаний (без картинок) для города или общей части."""
    query = supabase.table("knowledge_chunks") \
        .select("id", count="exact", head=True) \
        .eq("bot_id", bot_id) \
        .not_.like("content", IMAGE_PREFIX + "%")
    if city_id:
        query = query.eq("city_id", city_id)
    else:
        query = query.is_("city_id", "null")
    result = query.execute()
    return result.count or 0


def get_text_chunk_ids(bot_id: str, city_id: str | None) -> list[str]:
    """Все id текстовых фрагментов города (или общих) — постранично."""
    ids = []
    start = 0
    while True:
        # Картинки и ответы руководителя на вопросы клиентов ([ВОПРОС-ОТВЕТ]) при замене файла не трогаем
        query = supabase.table("knowledge_chunks") \
            .select("id") \
            .eq("bot_id", bot_id) \
            .not_.like("content", IMAGE_PREFIX + "%") \
            .not_.like("content", QA_PREFIX + "%")
        if city_id:
            query = query.eq("city_id", city_id)
        else:
            query = query.is_("city_id", "null")
        result = query.order("id").range(start, start + PAGE_SIZE - 1).execute()
        rows = result.data or []
        for row in rows:
            ids.append(row["id"])
        if len(rows) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return ids


def find_bot_by_channel(channel_id: str, except_bot_id: str) -> dict | None:
    """Другой бот, к которому уже подключён этот номер (канал Wazzup)."""
    result = supabase.table("bots").select("id, name, user_id") \
        .eq("wazzup_channel_id", channel_id).execute()
    for other in result.data or []:
        if other.get("id") != except_bot_id:
            return other
    return None


def find_bots_by_api_key(api_key: str) -> list[dict]:
    """Боты, подключённые к аккаунту Wazzup с этим ключом (у которых есть секрет вебхука)."""
    result = supabase.table("bots").select("id, user_id, wazzup_webhook_secret") \
        .eq("wazzup_api_key", api_key).execute()
    bots = []
    for item in result.data or []:
        if item.get("wazzup_webhook_secret"):
            bots.append(item)
    return bots


def load_bot(bot_id: str) -> dict:
    """Перечитывает бота из БД (полная строка, с секретами — только для сервера)."""
    result = supabase.table("bots").select("*").eq("id", bot_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Бот не найден")
    return result.data[0]


def update_bot_row(bot_id: str, fields: dict) -> dict:
    """Обновляет поля бота и возвращает обновлённую строку."""
    result = supabase.table("bots").update(fields).eq("id", bot_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Бот не найден")
    return result.data[0]


def get_team_member(member_id: str) -> dict:
    """Загружает запись команды. Нет — 404."""
    try:
        result = supabase.table("team_members").select("*").eq("id", member_id).limit(1).execute()
    except Exception:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    if not result.data:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    return result.data[0]


def check_member_city(city_id, bot_id: str) -> str | None:
    """city_id сотрудника: пусто = все города; иначе город должен быть у этого бота."""
    city_id = parse_city_id_param(city_id)
    if city_id:
        get_city_of_bot(city_id, bot_id)
    return city_id


# ============================================================
# Боты
# ============================================================

@router.get("/api/bots")
def get_bots(user: dict = Depends(get_current_user)):
    """Список ботов: свои (owner) + где я в команде (manager)."""
    own = supabase.table("bots").select("*").eq("user_id", user["id"]).order("created_at").execute()

    bots = []
    own_ids = []
    for bot in own.data or []:
        own_ids.append(bot["id"])
        item = public_bot(bot)
        item["my_role"] = "owner"
        item["my_city_id"] = None
        bots.append(item)

    memberships = supabase.table("team_members").select("*").eq("user_id", user["id"]).execute()
    city_by_bot = {}
    for member in memberships.data or []:
        if member["bot_id"] not in own_ids:
            city_by_bot[member["bot_id"]] = member.get("city_id")

    if city_by_bot:
        team_bots = supabase.table("bots") \
            .select("*") \
            .in_("id", list(city_by_bot.keys())) \
            .order("created_at") \
            .execute()
        for bot in team_bots.data or []:
            item = public_bot(bot)
            item["my_role"] = "manager"
            item["my_city_id"] = city_by_bot.get(bot["id"])
            bots.append(item)

    return {"bots": bots}


@router.post("/api/bots")
def create_bot(data: dict, user: dict = Depends(get_current_user)):
    """Создаёт нового бота для текущего пользователя (с учётом лимита подписки)."""
    # Та же подписка, что проверяет бот при ответах (самая свежая, с учётом срока)
    subscription = pipeline.get_owner_subscription(user["id"])
    max_bots = 1  # Без подписки — один бот
    if subscription:
        if pipeline.is_limit_exceeded(subscription, 0):
            raise HTTPException(status_code=403, detail="Подписка неактивна или истекла")
        max_bots = subscription.get("max_bots") or 1

    current = supabase.table("bots") \
        .select("id", count="exact", head=True) \
        .eq("user_id", user["id"]) \
        .execute()
    if (current.count or 0) >= max_bots:
        raise HTTPException(status_code=403, detail=f"Достигнут лимит ботов ({max_bots}). Обновите подписку.")

    # Имя и промпт проверяем теми же правилами, что и при редактировании
    fields = validate_bot_update({
        "name": data.get("name") or "Новый бот",
        "system_prompt": data.get("system_prompt") or "",
    })

    result = supabase.table("bots").insert({
        "name": fields["name"],
        "system_prompt": fields["system_prompt"],
        "whatsapp_session": "",
        "user_id": user["id"],
        "wazzup_webhook_secret": wazzup.generate_webhook_secret(),
    }).execute()

    bot = public_bot(result.data[0])
    bot["my_role"] = "owner"
    bot["my_city_id"] = None
    return {"bot": bot}


@router.put("/api/bots/{bot_id}")
def update_bot(bot_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Обновляет настройки бота (только владелец, только разрешённые поля)."""
    access = require_bot_access(bot_id, user["id"], owner_only=True)
    fields = validate_bot_update(data)
    if not fields:
        return {"bot": public_bot(access["bot"])}
    check_final_bot_state(access["bot"], fields)
    bot = update_bot_row(bot_id, fields)
    return {"bot": public_bot(bot)}


@router.get("/api/bots/{bot_id}/work-status")
def work_status(bot_id: str, user: dict = Depends(get_current_user)):
    """
    Работает ли бот прямо сейчас по графику + местное время сервера.
    Нужно, чтобы владелец видел: график применяется именно так, как он задумал.
    """
    access = require_bot_access(bot_id, user["id"])
    bot = access["bot"]
    local_now = work_hours.to_local()
    next_start = work_hours.next_work_start(bot)
    return {
        "now": work_hours.format_local(local_now),
        "timezone": str(local_now.tzinfo),
        "utc_offset": local_now.strftime("%z"),
        "schedule_enabled": bool(bot.get("work_hours_enabled")),
        "working": work_hours.is_bot_working(bot),
        "next_start": work_hours.format_local(next_start) if next_start else None,
    }


@router.post("/api/bots/{bot_id}/daily-report/test")
async def daily_report_test(bot_id: str, user: dict = Depends(get_current_user)):
    """Отправить сводку за сегодня прямо сейчас (проверка; дату отправки не меняет)."""
    access = require_bot_access(bot_id, user["id"], owner_only=True)
    bot = access["bot"]
    if not str(bot.get("telegram_notify_chat_id") or "").strip():
        raise HTTPException(status_code=400, detail="Сначала укажите и сохраните чат руководителя")
    sent = await daily_report.send_daily_report(bot, work_hours.to_local())
    if not sent:
        return {"ok": False, "error": "Telegram не принял сообщение — проверьте chat_id и что бот уведомлений добавлен в чат"}
    return {"ok": True}


# ============================================================
# Wazzup
# ============================================================

@router.post("/api/bots/{bot_id}/wazzup/connect")
async def wazzup_connect(bot_id: str, data: dict, user: dict = Depends(get_current_user)):
    """
    Только ПРОВЕРЯЕТ API-ключ Wazzup и возвращает список WhatsApp-каналов.
    В БД ничего не сохраняем: ключ сохранится вместе с каналом в /wazzup/channel,
    чтобы проверка нового ключа не ломала рабочее подключение.
    """
    access = require_bot_access(bot_id, user["id"], owner_only=True)
    # Пустой ключ — берём сохранённый (смена номера без повторного ввода ключа)
    api_key = str(data.get("api_key") or "").strip() or access["bot"].get("wazzup_api_key") or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="Введите API-ключ Wazzup")

    result = await wazzup.get_channels(api_key)
    if not result.get("ok"):
        return {"ok": False, "channels": [], "error": result.get("error") or "Wazzup не принял ключ"}

    channels = filter_whatsapp_channels(result.get("channels"))
    error = ""
    if not channels:
        error = "В аккаунте Wazzup нет WhatsApp-каналов. Добавьте канал в личном кабинете Wazzup."

    # Какие номера уже заняты другими ботами (один номер — один бот)
    for channel in channels:
        other = find_bot_by_channel(channel["channelId"], bot_id)
        channel["used_by"] = None
        if other:
            if other.get("user_id") == user["id"]:
                channel["used_by"] = other.get("name") or "другой бот"
            else:
                channel["used_by"] = "другой аккаунт сервиса"
    return {"ok": True, "channels": channels, "error": error}


@router.post("/api/bots/{bot_id}/wazzup/channel")
async def wazzup_select_channel(bot_id: str, data: dict, user: dict = Depends(get_current_user)):
    """
    Выбирает WhatsApp-канал Wazzup для бота и подключает вебхук.
    Если в теле есть api_key — используем (и сохраняем) его, иначе — сохранённый ключ.
    """
    access = require_bot_access(bot_id, user["id"], owner_only=True)
    bot = access["bot"]
    new_api_key = str(data.get("api_key") or "").strip()
    api_key = new_api_key or bot.get("wazzup_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="Сначала введите и проверьте API-ключ Wazzup")

    channel_id = str(data.get("channel_id") or "").strip()
    if not channel_id:
        raise HTTPException(status_code=400, detail="Выберите канал")

    result = await wazzup.get_channels(api_key)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "Не удалось получить каналы Wazzup", "bot": public_bot(bot)}

    channel = find_channel(filter_whatsapp_channels(result.get("channels")), channel_id)
    if not channel:
        return {"ok": False, "error": "Канал не найден в аккаунте Wazzup", "bot": public_bot(bot)}

    # Один номер — один бот: иначе на одного клиента ответят два бота
    other = find_bot_by_channel(channel_id, bot_id)
    if other:
        name = other.get("name") if other.get("user_id") == user["id"] else "другого аккаунта"
        return {"ok": False, "error": f"Этот номер уже подключён к боту «{name}». Сначала отключите его там.",
                "bot": public_bot(bot)}

    secret = bot.get("wazzup_webhook_secret") or wazzup.generate_webhook_secret()

    # Секрет сохраняем ДО подключения вебхука: Wazzup сразу пришлёт тестовый запрос с токеном
    # Ключ сохраняем только сейчас — вместе с каналом, который в этом аккаунте точно есть
    bot = update_bot_row(bot_id, {
        "wazzup_api_key": api_key,
        "wazzup_channel_id": channel_id,
        "wazzup_channel_phone": channel.get("plainId"),
        "wazzup_channel_state": channel.get("state"),
        "wazzup_webhook_secret": secret,
    })

    hook = await wazzup.set_webhook(api_key, wazzup.build_webhook_url(bot_id, secret))
    # Бот перешёл на другой аккаунт Wazzup — другие номера старого аккаунта не должны остаться без вебхука
    old_key = access["bot"].get("wazzup_api_key")
    if old_key and old_key != api_key:
        await repoint_account_webhook(old_key, user["id"])
    return {"ok": bool(hook.get("ok")), "error": hook.get("error") or "", "bot": public_bot(bot)}


async def repoint_account_webhook(api_key: str, user_id: str) -> None:
    """Направляет вебхук аккаунта Wazzup на любого оставшегося бота этого владельца с этим ключом."""
    for other in find_bots_by_api_key(api_key):
        if other.get("user_id") == user_id:
            result = await wazzup.set_webhook(api_key, wazzup.build_webhook_url(other["id"], other["wazzup_webhook_secret"]))
            if not result.get("ok"):
                print(f"WAZZUP: не удалось перенаправить вебхук на бота {other['id']}: {result.get('error')}")
            return


@router.get("/api/bots/{bot_id}/wazzup/status")
async def wazzup_status(bot_id: str, user: dict = Depends(get_current_user)):
    """Статус подключения Wazzup: свежее состояние канала и проверка вебхука."""
    access = require_bot_access(bot_id, user["id"])
    bot = access["bot"]
    api_key = bot.get("wazzup_api_key")
    channel_id = bot.get("wazzup_channel_id")

    status = {
        "connected": bool(api_key) and bool(channel_id),
        "channel_id": channel_id,
        "phone": bot.get("wazzup_channel_phone"),
        "state": bot.get("wazzup_channel_state"),
        "webhook_ok": False,
    }
    if not api_key or not channel_id:
        return status

    channels_result = await wazzup.get_channels(api_key)
    if channels_result.get("ok"):
        channel = find_channel(channels_result.get("channels"), channel_id)
        fresh_state = channel.get("state") if channel else "not_found"
        if fresh_state != bot.get("wazzup_channel_state"):
            supabase.table("bots").update({"wazzup_channel_state": fresh_state}).eq("id", bot_id).execute()
        status["state"] = fresh_state

    # Вебхук у аккаунта Wazzup один: он может указывать на любого нашего бота с тем же ключом —
    # сообщения всё равно дойдут до нужного бота по номеру (см. webhooks.load_account_bots)
    hook = await wazzup.get_webhook(api_key)
    if hook.get("ok"):
        current_url = (hook.get("data") or {}).get("webhooksUri")
        for account_bot in find_bots_by_api_key(api_key):
            secret = account_bot.get("wazzup_webhook_secret")
            if secret and current_url == wazzup.build_webhook_url(account_bot["id"], secret):
                status["webhook_ok"] = True

    return status


@router.post("/api/bots/{bot_id}/wazzup/disconnect")
async def wazzup_disconnect(bot_id: str, user: dict = Depends(get_current_user)):
    """
    Отключает Wazzup: забываем ключ и канал.
    Секрет вебхука меняем — старый адрес вебхука перестанет проходить проверку токена.
    """
    access = require_bot_access(bot_id, user["id"], owner_only=True)
    old_bot = access["bot"]
    bot = update_bot_row(bot_id, {
        "wazzup_api_key": None,
        "wazzup_channel_id": None,
        "wazzup_channel_phone": None,
        "wazzup_channel_state": None,
        "wazzup_webhook_secret": wazzup.generate_webhook_secret(),
    })
    # Другие номера этого же аккаунта Wazzup работают через общий вебхук — если он указывал
    # на отключённого бота, переводим его на оставшегося бота аккаунта
    api_key = old_bot.get("wazzup_api_key")
    if api_key:
        await repoint_account_webhook(api_key, user["id"])
    return {"ok": True, "bot": public_bot(bot)}


# ============================================================
# Города
# ============================================================

@router.get("/api/bots/{bot_id}/cities")
def get_cities(bot_id: str, user: dict = Depends(get_current_user)):
    """Список городов бота + число фрагментов базы знаний в каждом."""
    require_bot_access(bot_id, user["id"])
    result = supabase.table("cities") \
        .select("*") \
        .eq("bot_id", bot_id) \
        .order("sort_order") \
        .order("name") \
        .execute()

    cities = []
    for city in result.data or []:
        item = dict(city)
        item["chunks_count"] = count_text_chunks(bot_id, city["id"])
        cities.append(item)
    return {"cities": cities}


@router.post("/api/bots/{bot_id}/cities")
def create_city(bot_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Добавляет город (только владелец)."""
    require_bot_access(bot_id, user["id"], owner_only=True)
    fields = validate_city_data(data, partial=False)
    fields["bot_id"] = bot_id
    try:
        result = supabase.table("cities").insert(fields).execute()
    except Exception as e:
        print(f"SETTINGS ERROR: не удалось создать город: {e}")
        raise HTTPException(status_code=400, detail="Не удалось сохранить город. Возможно, город с таким названием уже есть.")
    city = dict(result.data[0])
    city["chunks_count"] = 0
    return {"city": city}


@router.put("/api/cities/{city_id}")
def update_city(city_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Редактирует город (только владелец бота)."""
    try:
        found = supabase.table("cities").select("*").eq("id", city_id).limit(1).execute()
    except Exception:
        raise HTTPException(status_code=404, detail="Город не найден")
    if not found.data:
        raise HTTPException(status_code=404, detail="Город не найден")
    city = found.data[0]
    require_bot_access(city["bot_id"], user["id"], owner_only=True)

    fields = validate_city_data(data, partial=True)
    if not fields:
        return {"city": city}
    try:
        result = supabase.table("cities").update(fields).eq("id", city_id).execute()
    except Exception as e:
        print(f"SETTINGS ERROR: не удалось обновить город: {e}")
        raise HTTPException(status_code=400, detail="Не удалось сохранить город. Возможно, город с таким названием уже есть.")
    return {"city": result.data[0] if result.data else city}


@router.delete("/api/cities/{city_id}")
def delete_city(city_id: str, user: dict = Depends(get_current_user)):
    """
    Удаляет город. Фрагменты базы знаний этого города удалятся каскадно (FK).
    Сотрудники, закреплённые за городом, удаляются из команды — иначе после
    удаления города они получили бы доступ ко ВСЕМ городам (city_id стал бы NULL).
    """
    try:
        found = supabase.table("cities").select("id, bot_id").eq("id", city_id).limit(1).execute()
    except Exception:
        raise HTTPException(status_code=404, detail="Город не найден")
    if not found.data:
        raise HTTPException(status_code=404, detail="Город не найден")
    require_bot_access(found.data[0]["bot_id"], user["id"], owner_only=True)

    removed = supabase.table("team_members").delete().eq("city_id", city_id).execute()
    supabase.table("cities").delete().eq("id", city_id).execute()
    return {"status": "deleted", "removed_members": len(removed.data or [])}


# ============================================================
# База знаний
# ============================================================

def extract_text_from_pdf(content: bytes) -> str:
    """Извлекает текст из PDF файла."""
    doc = fitz.open(stream=content, filetype="pdf")
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts)


def extract_rows_from_xlsx(content: bytes) -> list:
    """
    Извлекает строки из Excel файла.
    Каждая строка данных — отдельный элемент с заголовками столбцов.
    Формат: "Столбец1: Значение1 | Столбец2: Значение2 | ..."
    """
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    rows_with_headers = []

    for sheet in wb.sheetnames:
        ws = wb[sheet]
        headers = []
        first_row = True

        for row in ws.iter_rows(values_only=True):
            cells = [str(cell).strip() if cell is not None else "" for cell in row]

            # Пропускаем полностью пустые строки
            if not any(cells):
                continue

            if first_row:
                # Первая непустая строка — заголовки
                headers = cells
                first_row = False
                continue

            parts = []
            for i, cell in enumerate(cells):
                if cell:
                    header = headers[i] if i < len(headers) and headers[i] else f"Столбец {i+1}"
                    parts.append(f"{header}: {cell}")

            if parts:
                rows_with_headers.append(" | ".join(parts))

    wb.close()
    return rows_with_headers


def decode_text_file(content: bytes) -> str:
    """
    Текст файла в строку. Сначала UTF-8; если не получилось — Windows-1251
    (так сохраняет CSV/TXT Excel и Блокнот на русской Windows).
    """
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1251", errors="replace")


def detect_csv_delimiter(text: str) -> str:
    """Разделитель CSV: Excel на русской Windows ставит «;», остальные — «,» (или Tab)."""
    sample = text[:5000]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        # Не удалось определить — берём тот, которого в первой строке больше
        first_line = sample.split("\n", 1)[0]
        if first_line.count(";") > first_line.count(","):
            return ";"
        return ","


def extract_rows_from_csv(content: bytes) -> list:
    """Извлекает строки из CSV файла (каждая строка — с заголовками столбцов)."""
    text = decode_text_file(content)
    reader = csv.reader(io.StringIO(text), delimiter=detect_csv_delimiter(text))
    rows_with_headers = []
    headers = []
    first_row = True

    for row in reader:
        cells = [cell.strip() for cell in row]

        if not any(cells):
            continue

        if first_row:
            headers = cells
            first_row = False
            continue

        parts = []
        for i, cell in enumerate(cells):
            if cell:
                header = headers[i] if i < len(headers) and headers[i] else f"Столбец {i+1}"
                parts.append(f"{header}: {cell}")

        if parts:
            rows_with_headers.append(" | ".join(parts))

    return rows_with_headers


def build_chunks_from_file(filename: str, content: bytes) -> list[str]:
    """
    Превращает файл в список текстов-фрагментов.
    Таблицы (XLSX/CSV) — строка = фрагмент. PDF/TXT — по абзацам/предложениям до ~800 символов.
    """
    if filename.endswith(".xlsx"):
        return extract_rows_from_xlsx(content)
    if filename.endswith(".csv"):
        return extract_rows_from_csv(content)
    if filename.endswith(".pdf"):
        return split_text_into_chunks(extract_text_from_pdf(content))
    return split_text_into_chunks(decode_text_file(content))


@router.post("/api/bots/{bot_id}/knowledge")
async def upload_knowledge(bot_id: str, city_id: str | None = None,
                           file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """
    Загружает файл базы знаний (TXT, PDF, CSV, XLSX) для города или общий (city_id пустой).
    Заменяются ТОЛЬКО фрагменты этого же города (или только общие). Картинки не трогаем.
    """
    require_bot_access(bot_id, user["id"], owner_only=True)
    city_id = parse_city_id_param(city_id)
    if city_id:
        get_city_of_bot(city_id, bot_id)

    filename = file.filename.lower() if file.filename else ""
    extension = file_extension(filename)
    if extension not in KNOWLEDGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Поддерживаются TXT, PDF, CSV, XLSX")

    # Размер проверяем до чтения (если клиент его сообщил), и ещё раз после
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Файл слишком большой (максимум 20 МБ)")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Файл слишком большой (максимум 20 МБ)")

    try:
        # Разбор PDF/XLSX и нарезка — тяжёлые: выполняем в отдельном потоке,
        # чтобы не блокировать сервер для других запросов
        texts = await asyncio.to_thread(build_chunks_from_file, filename, content)
    except Exception as e:
        print(f"SETTINGS ERROR: не удалось прочитать файл базы знаний: {e}")
        raise HTTPException(status_code=400, detail="Не удалось прочитать файл. Проверьте формат (TXT, PDF, CSV, XLSX).")

    # Строки, начинающиеся с "[IMAGE:", приняли бы за картинку — им тут не место
    chunks = []
    for text in texts:
        text = text.strip()
        if text and not text.startswith(IMAGE_PREFIX):
            chunks.append({"bot_id": bot_id, "city_id": city_id, "content": text})

    if not chunks:
        raise HTTPException(status_code=400, detail="В файле не найден текст")

    # Сначала запоминаем старые фрагменты, вставляем новые и только потом удаляем старые —
    # если вставка упадёт, старая база знаний останется на месте.
    old_ids = get_text_chunk_ids(bot_id, city_id)

    for i in range(0, len(chunks), DB_BATCH_SIZE):
        supabase.table("knowledge_chunks").insert(chunks[i:i + DB_BATCH_SIZE]).execute()

    for i in range(0, len(old_ids), DB_BATCH_SIZE):
        supabase.table("knowledge_chunks").delete().in_("id", old_ids[i:i + DB_BATCH_SIZE]).execute()

    file_format = extension.lstrip(".")
    return {"chunks_count": len(chunks), "format": file_format, "city_id": city_id}


@router.get("/api/bots/{bot_id}/knowledge/summary")
def knowledge_summary(bot_id: str, user: dict = Depends(get_current_user)):
    """Сколько фрагментов базы знаний в общей части и в каждом городе."""
    require_bot_access(bot_id, user["id"], owner_only=True)
    cities = supabase.table("cities").select("id").eq("bot_id", bot_id).execute()

    by_city = {}
    for city in cities.data or []:
        by_city[city["id"]] = count_text_chunks(bot_id, city["id"])

    return {"general": count_text_chunks(bot_id, None), "by_city": by_city}


# ============================================================
# Картинки
# ============================================================

@router.post("/api/bots/{bot_id}/images")
async def upload_image(bot_id: str, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """
    Загружает картинку в Supabase Storage (бакет bot_images).
    Бот сможет отправлять её клиентам тегом [IMAGE:url].
    """
    require_bot_access(bot_id, user["id"], owner_only=True)

    filename = file.filename or "image.jpg"
    content_type = IMAGE_CONTENT_TYPES.get(file_extension(filename))
    if not content_type:
        raise HTTPException(status_code=400, detail="Поддерживаются картинки JPG, PNG, WEBP")

    if file.size is not None and file.size > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Картинка слишком большая (максимум 10 МБ)")
    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Картинка слишком большая (максимум 10 МБ)")

    # Уникальный префикс — чтобы файлы с одинаковым именем не конфликтовали
    storage_path = f"{bot_id}/{uuid.uuid4().hex[:8]}_{safe_storage_name(filename)}"

    # Тип файла — по расширению (клиент может прислать что угодно)
    file_options = {"content-type": content_type}

    try:
        supabase.storage.from_("bot_images").upload(storage_path, content, file_options)
    except Exception as e:
        print(f"SETTINGS ERROR: не удалось загрузить картинку: {e}")
        raise HTTPException(status_code=400, detail="Не удалось загрузить картинку")

    public_url = supabase.storage.from_("bot_images").get_public_url(storage_path)

    # URL храним в knowledge_chunks с пометкой [IMAGE:...] (city_id = NULL)
    result = supabase.table("knowledge_chunks").insert({
        "bot_id": bot_id,
        "content": f"{IMAGE_PREFIX}{public_url}] {filename}",
    }).execute()

    image_id = result.data[0]["id"] if result.data else None
    return {"id": image_id, "url": public_url, "filename": filename}


@router.get("/api/bots/{bot_id}/images")
def get_images(bot_id: str, user: dict = Depends(get_current_user)):
    """Список загруженных картинок бота."""
    require_bot_access(bot_id, user["id"], owner_only=True)
    result = supabase.table("knowledge_chunks") \
        .select("id, content") \
        .eq("bot_id", bot_id) \
        .like("content", IMAGE_PREFIX + "%") \
        .execute()

    images = []
    for item in result.data or []:
        parsed = parse_image_content(item["content"])
        images.append({"id": item["id"], "url": parsed["url"], "name": parsed["name"]})
    return {"images": images}


# Максимальная длина описания картинки
MAX_IMAGE_DESCRIPTION = 300


@router.put("/api/images/{chunk_id}")
def update_image(chunk_id: str, data: dict, user: dict = Depends(get_current_user)):
    """
    Меняет описание картинки: что на ней и когда её отправлять.
    По описанию бот решает, какую картинку отправить клиенту.
    """
    found = supabase.table("knowledge_chunks").select("id, bot_id, content").eq("id", chunk_id).limit(1).execute()
    if not found.data or not str(found.data[0].get("content", "")).startswith(IMAGE_PREFIX):
        raise HTTPException(status_code=404, detail="Картинка не найдена")
    chunk = found.data[0]
    require_bot_access(chunk["bot_id"], user["id"], owner_only=True)

    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="Напишите, что на картинке")
    # Описание — одной строкой (перенос строки сломал бы формат «[IMAGE:url] описание»)
    name = " ".join(name.split())
    if len(name) > MAX_IMAGE_DESCRIPTION:
        raise HTTPException(status_code=400, detail=f"Описание не длиннее {MAX_IMAGE_DESCRIPTION} символов")

    url = parse_image_content(chunk["content"])["url"]
    supabase.table("knowledge_chunks").update({"content": f"{IMAGE_PREFIX}{url}] {name}"}) \
        .eq("id", chunk["id"]).execute()
    return {"id": chunk["id"], "url": url, "name": name}


@router.delete("/api/images/{chunk_id}")
def delete_image(chunk_id: str, user: dict = Depends(get_current_user)):
    """Удаляет картинку бота (запись и, по возможности, файл в Storage)."""
    try:
        found = supabase.table("knowledge_chunks").select("id, bot_id, content").eq("id", chunk_id).limit(1).execute()
    except Exception:
        raise HTTPException(status_code=404, detail="Картинка не найдена")
    if not found.data or not str(found.data[0].get("content", "")).startswith(IMAGE_PREFIX):
        raise HTTPException(status_code=404, detail="Картинка не найдена")
    chunk = found.data[0]
    require_bot_access(chunk["bot_id"], user["id"], owner_only=True)

    supabase.table("knowledge_chunks").delete().eq("id", chunk_id).execute()

    # Файл в Storage удаляем «по возможности» — запись уже удалена, это главное
    url = parse_image_content(chunk["content"])["url"]
    marker = "/bot_images/"
    if marker in url:
        path = url.split(marker, 1)[1].split("?")[0]
        try:
            supabase.storage.from_("bot_images").remove([path])
        except Exception as e:
            print(f"SETTINGS ERROR: не удалось удалить файл картинки: {e}")

    return {"status": "deleted"}


# ============================================================
# Команда (менеджеры)
# ============================================================

@router.get("/api/bots/{bot_id}/team")
def get_team(bot_id: str, user: dict = Depends(get_current_user)):
    """Список сотрудников бота с email."""
    require_bot_access(bot_id, user["id"], owner_only=True)
    members = supabase.table("team_members").select("*").eq("bot_id", bot_id).order("created_at").execute()

    user_ids = []
    for member in members.data or []:
        user_ids.append(member["user_id"])

    emails = {}
    if user_ids:
        users = supabase.table("users").select("id, email").in_("id", user_ids).execute()
        for row in users.data or []:
            emails[str(row["id"])] = row.get("email")

    result = []
    for member in members.data or []:
        result.append({
            "id": member["id"],
            "user_id": member["user_id"],
            "email": emails.get(str(member["user_id"]), ""),
            "city_id": member.get("city_id"),
            "telegram_chat_id": member.get("telegram_chat_id"),
        })
    return {"members": result}


@router.post("/api/bots/{bot_id}/team")
def add_team_member(bot_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Добавляет сотрудника по email (он должен быть заранее зарегистрирован)."""
    access = require_bot_access(bot_id, user["id"], owner_only=True)

    email = str(data.get("email") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Укажите email сотрудника")

    city_id = check_member_city(data.get("city_id"), bot_id)
    telegram_chat_id = normalize_chat_id(data.get("telegram_chat_id"), "Telegram chat_id сотрудника")

    # ilike без % — сравнение без учёта регистра; затем точная проверка в Python
    # (символ "_" в ilike означает «любой символ»)
    found = supabase.table("users").select("id, email").ilike("email", email).limit(10).execute()
    found_user = None
    for row in found.data or []:
        if str(row.get("email") or "").lower() == email.lower():
            found_user = row
            break
    if not found_user:
        raise HTTPException(status_code=404, detail="Пользователь с таким email не найден — пусть сначала зарегистрируется")

    if str(found_user["id"]) == str(access["bot"].get("user_id")):
        raise HTTPException(status_code=400, detail="Это владелец бота — у него и так полный доступ")

    existing = supabase.table("team_members") \
        .select("id") \
        .eq("bot_id", bot_id) \
        .eq("user_id", found_user["id"]) \
        .limit(1) \
        .execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Этот сотрудник уже в команде")

    result = supabase.table("team_members").insert({
        "bot_id": bot_id,
        "user_id": found_user["id"],
        "city_id": city_id,
        "telegram_chat_id": telegram_chat_id,
    }).execute()
    member = result.data[0]

    return {"member": {
        "id": member["id"],
        "user_id": member["user_id"],
        "email": found_user.get("email"),
        "city_id": member.get("city_id"),
        "telegram_chat_id": member.get("telegram_chat_id"),
    }}


@router.put("/api/team/{member_id}")
def update_team_member(member_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Меняет город и Telegram chat_id сотрудника."""
    member = get_team_member(member_id)
    require_bot_access(member["bot_id"], user["id"], owner_only=True)

    fields = {}
    if "city_id" in data:
        fields["city_id"] = check_member_city(data.get("city_id"), member["bot_id"])
    if "telegram_chat_id" in data:
        fields["telegram_chat_id"] = normalize_chat_id(data.get("telegram_chat_id"), "Telegram chat_id сотрудника")

    if fields:
        result = supabase.table("team_members").update(fields).eq("id", member_id).execute()
        if result.data:
            member = result.data[0]

    return {"member": {
        "id": member["id"],
        "user_id": member["user_id"],
        "city_id": member.get("city_id"),
        "telegram_chat_id": member.get("telegram_chat_id"),
    }}


@router.delete("/api/team/{member_id}")
def delete_team_member(member_id: str, user: dict = Depends(get_current_user)):
    """Удаляет сотрудника из команды бота."""
    member = get_team_member(member_id)
    require_bot_access(member["bot_id"], user["id"], owner_only=True)
    supabase.table("team_members").delete().eq("id", member_id).execute()
    return {"status": "deleted"}


# ============================================================
# Уведомления
# ============================================================

@router.post("/api/bots/{bot_id}/test-notification")
async def test_notification(bot_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Отправляет тестовое уведомление в Telegram (chat_id из тела или общий чат бота)."""
    access = require_bot_access(bot_id, user["id"], owner_only=True)
    chat_id = normalize_chat_id(data.get("chat_id"), "Telegram chat_id")
    if not chat_id:
        chat_id = access["bot"].get("telegram_notify_chat_id")
    if not chat_id:
        raise HTTPException(status_code=400, detail="Укажите Telegram chat_id для теста")

    ok = await notifier.send_notification(chat_id, "✅ Тестовое уведомление AI Sales Manager")
    error = ""
    if not ok:
        error = "Не удалось отправить. Проверьте chat_id и что бот уведомлений добавлен в чат (и ему написали /start)."
    return {"ok": bool(ok), "error": error}
