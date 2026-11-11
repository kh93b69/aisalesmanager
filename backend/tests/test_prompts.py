"""Тесты сборки системного промпта (app/prompts.py)."""
import os

# app.config читает переменные при первом импорте, а app.database создаёт клиента Supabase.
# Подставляем фейковые значения (в БД эти тесты не ходят), чтобы импорт не падал.
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test.test.test")

from app.prompts import build_system_blocks, build_state_note  # noqa: E402

CITIES = [
    {"id": "c1", "name": "Астана", "address": "ул. Кенесары 10, 9:00–20:00"},
    {"id": "c2", "name": "Алматы", "address": ""},
]
BOT = {"id": "b1", "system_prompt": "Тебя зовут Алия."}
IMAGES = [{"url": "https://cdn/x.jpg", "name": "прайс.jpg"}]


def test_blocks_structure():
    blocks = build_system_blocks(BOT, CITIES, CITIES[0], "Терапевт — 8000 тг", IMAGES)
    assert len(blocks) == 2
    # Кэш на обоих блоках: блок 1 общий для всех городов, блок 1+2 — для пары бот+город
    assert blocks[0]["type"] == "text"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["type"] == "text"
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_block_1_content():
    blocks = build_system_blocks(BOT, CITIES, None, "", IMAGES)
    text = blocks[0]["text"]
    assert "Тебя зовут Алия." in text
    assert "- Астана: ул. Кенесары 10, 9:00–20:00" in text
    assert "- Алматы" in text
    assert "[IMAGE:https://cdn/x.jpg]" in text
    # Обычный бизнес: никаких врачей и скорой
    assert "103" not in text
    assert "клиник" not in text.lower()


def test_block_1_does_not_depend_on_city():
    # Блок 1 одинаковый для любого города — иначе кэш между городами не работает
    first = build_system_blocks(BOT, CITIES, CITIES[0], "база 1", IMAGES)
    second = build_system_blocks(BOT, CITIES, CITIES[1], "база 2", IMAGES)
    assert first[0]["text"] == second[0]["text"]
    assert first[1]["text"] != second[1]["text"]


def test_block_2_content():
    blocks = build_system_blocks(BOT, CITIES, CITIES[0], "Терапевт — 8000 тг", [])
    assert "Текущий город клиента: Астана" in blocks[1]["text"]
    assert "БАЗА ЗНАНИЙ\nТерапевт — 8000 тг" in blocks[1]["text"]

    unknown = build_system_blocks(BOT, CITIES, None, "Общее", [])
    assert "Город неизвестен" in unknown[1]["text"]


def test_no_images_section_when_empty():
    blocks = build_system_blocks({"system_prompt": None}, [], None, "", [])
    assert "КАРТИНКИ" not in blocks[0]["text"]
    assert "[IMAGE:" not in blocks[0]["text"]


def test_state_note():
    note = build_state_note({"status": "in_progress"}, CITIES[0], "вторник, 10 ноября 2026, 14:05")
    assert "вторник, 10 ноября 2026, 14:05" in note
    assert "Астана" in note

    note = build_state_note({"status": "new"}, None, "сейчас")
    assert "уточни город" in note

    # У бота нет городов — про город в заметке ни слова
    note = build_state_note({"status": "new"}, None, "сейчас", has_cities=False)
    assert "город" not in note.lower()

    note = build_state_note({"status": "hot"}, CITIES[0], "сейчас")
    assert "Не продавай повторно" in note
    note = build_state_note({"status": "invoice_sent"}, CITIES[0], "сейчас")
    assert "Не продавай повторно" in note
    note = build_state_note({"status": "paid"}, CITIES[0], "сейчас")
    assert "оплатил" in note


# ============================================================
# Анкета компании и тип бизнеса
# ============================================================

def test_general_business_profile_in_prompt():
    bot = {
        "business_type": "general", "company_name": "Мебель Плюс", "assistant_name": "Алия",
        "business_description": "Продаём диваны и кровати, доставка по городу",
        "target_action": "клиент выбрал товар и готов оформить заказ с доставкой",
    }
    text = build_system_blocks(bot, [], None, "", [])[0]["text"]
    assert "менеджер по продажам компании «Мебель Плюс»" in text
    assert "Тебя зовут Алия." in text
    assert "Продаём диваны и кровати" in text
    assert "ЦЕЛЕВОЕ ДЕЙСТВИЕ" in text
    assert "готов оформить заказ с доставкой" in text
    assert "МЕДИЦИНСКАЯ БЕЗОПАСНОСТЬ" not in text
    assert "Город клиента выяснять не нужно" in text


def test_general_defaults_without_profile():
    # Пустая анкета: роль без названия, целевое действие по умолчанию, лишних разделов нет
    text = build_system_blocks({}, [], None, "", [])[0]["text"]
    assert "менеджер по продажам компании и отвечаешь" in text
    assert "согласился оформить заказ" in text
    assert "Тебя зовут" not in text
    assert "О КОМПАНИИ" not in text
    assert "ИНСТРУКЦИИ ВЛАДЕЛЬЦА" not in text


def test_clinic_mode_adds_medical_rules():
    bot = {"business_type": "clinic", "company_name": "Здоровье"}
    text = build_system_blocks(bot, CITIES, None, "", [])[0]["text"]
    assert "администратор медицинской клиники «Здоровье»" in text
    assert "МЕДИЦИНСКАЯ БЕЗОПАСНОСТЬ" in text
    assert "103" in text
    assert "записаться или оплатить приём" in text


def test_unknown_business_type_is_general():
    text = build_system_blocks({"business_type": "casino"}, [], None, "", [])[0]["text"]
    assert "менеджер по продажам" in text
    assert "103" not in text


def test_profile_does_not_break_city_cache():
    # Анкета — в блоке 1, он одинаковый для любого города
    bot = {"company_name": "Мебель Плюс", "target_action": "заказ"}
    first = build_system_blocks(bot, CITIES, CITIES[0], "база 1", [])
    second = build_system_blocks(bot, CITIES, CITIES[1], "база 2", [])
    assert first[0]["text"] == second[0]["text"]
