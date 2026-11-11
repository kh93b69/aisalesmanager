"""
Тесты определения города по тексту (app/cities.py).
В БД не ходим — проверяем только чистые функции.
"""
import os

# Импорт app.database создаёт клиента Supabase — подставляем фейковые значения,
# если реальных нет (запросов к БД в этих тестах нет)
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test.test.test")

from app.cities import normalize_text, find_cities_in_text, detect_city  # noqa: E402

# 7 городов клиники (как будут настроены у клиента) + Костанай для проверки «й»
CITIES = [
    {"id": "c1", "name": "Астана", "aliases": "нур-султан, столица"},
    {"id": "c2", "name": "Алматы", "aliases": "алма-ата"},
    {"id": "c3", "name": "Караганда", "aliases": ""},
    {"id": "c4", "name": "Шымкент", "aliases": ""},
    {"id": "c5", "name": "Усть-Каменогорск", "aliases": "Өскемен, УКГ"},
    {"id": "c6", "name": "Павлодар", "aliases": None},
    {"id": "c7", "name": "Актобе", "aliases": ""},
    {"id": "c8", "name": "Атырау", "aliases": ""},
    {"id": "c9", "name": "Костанай", "aliases": "кустанай"},
    {"id": "c10", "name": "Актау", "aliases": ""},
    {"id": "c11", "name": "Семей", "aliases": "семипалатинск"},
]


def names(found):
    """Названия найденных городов — для удобного сравнения."""
    return [city["name"] for city in found]


def test_normalize_text():
    assert normalize_text("Привет!!! Я из Усть-Каменогорска, ЁЖИК.") == "привет я из усть-каменогорска ежик"
    assert normalize_text(" - тире - отдельно -") == "тире отдельно"
    assert normalize_text("Қарағанды") == "караганды"
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


def test_nominative_and_cases():
    cases = {
        "Астана": "Астана",
        "я в Астане": "Астана",
        "из Астаны пишу": "Астана",
        "Алматы": "Алматы",
        "живу в алматы": "Алматы",
        "Караганда": "Караганда",
        "в Караганде есть филиал?": "Караганда",
        "доволен Карагандой": "Караганда",
        "из Караганды": "Караганда",
        "Шымкент": "Шымкент",
        "в Шымкенте": "Шымкент",
        "Усть-Каменогорск": "Усть-Каменогорск",
        "я в Усть-Каменогорске": "Усть-Каменогорск",
        "усть каменогорск": "Усть-Каменогорск",
        "Павлодар": "Павлодар",
        "в Павлодаре": "Павлодар",
        "Актобе": "Актобе",
        "Атырау": "Атырау",
        "Костанай": "Костанай",
        "в Костанае": "Костанай",
        "АСТАНА!!!": "Астана",
        "Астана?": "Астана",
    }
    for text, expected in cases.items():
        assert names(find_cities_in_text(text, CITIES)) == [expected], text


def test_aliases():
    assert names(find_cities_in_text("я из Нур-Султана", CITIES)) == ["Астана"]
    assert names(find_cities_in_text("Нур-Султан", CITIES)) == ["Астана"]
    assert names(find_cities_in_text("нур султан", CITIES)) == ["Астана"]
    assert names(find_cities_in_text("в Алма-Ате", CITIES)) == ["Алматы"]
    assert names(find_cities_in_text("живу в столице", CITIES)) == ["Астана"]
    assert names(find_cities_in_text("Алма-Ата", CITIES)) == ["Алматы"]
    assert names(find_cities_in_text("Өскемен", CITIES)) == ["Усть-Каменогорск"]
    assert names(find_cities_in_text("укг", CITIES)) == ["Усть-Каменогорск"]
    assert names(find_cities_in_text("в Кустанае", CITIES)) == ["Костанай"]


def test_kazakh_spelling():
    assert names(find_cities_in_text("Қарағандыда", CITIES)) == ["Караганда"]
    assert names(find_cities_in_text("Алматыда тұрамын", CITIES)) == ["Алматы"]


def test_no_false_positives():
    texts = [
        "акт выполненных работ",
        "а сколько стоит приём?",
        "нужен акт и договор",
        "актовый зал",
        "атыр",
        "алмаз",
        "караван",
        "павлин",
        "шум",
        "пришлите прайс",
        "",
        "у меня укус",          # «укг» — короткий алиас, только целое слово
        "астанавите это",       # слово длиннее основы больше чем на 3 буквы
        "павлодарский район",   # прилагательное с длинным окончанием не считаем
    ]
    for text in texts:
        assert find_cities_in_text(text, CITIES) == [], text


def test_short_stem_cities():
    # Короткие основы («акта», «семе») — только точное название или короткие окончания
    assert names(find_cities_in_text("в Актау", CITIES)) == ["Актау"]
    assert names(find_cities_in_text("из Актау", CITIES)) == ["Актау"]
    assert names(find_cities_in_text("Актау", CITIES)) == ["Актау"]
    assert names(find_cities_in_text("Семей", CITIES)) == ["Семей"]
    assert names(find_cities_in_text("Семей, хочу записаться", CITIES)) == ["Семей"]
    assert names(find_cities_in_text("я из Семей", CITIES)) == ["Семей"]
    assert names(find_cities_in_text("в Семее", CITIES)) == ["Семей"]
    assert names(find_cities_in_text("из Семея", CITIES)) == ["Семей"]
    assert names(find_cities_in_text("Семипалатинск", CITIES)) == ["Семей"]


def test_short_stem_false_positives():
    texts = [
        "нужна копия акта",
        "пришлите акты сверки",
        "ошибка в акте",
        "с актами всё в порядке",
        "скидки для наших семей",
        "программа для многодетных семей",
        "семья из трёх человек",
        "актуально ещё?",
    ]
    for text in texts:
        assert find_cities_in_text(text, CITIES) == [], text


def test_order_and_duplicates():
    text = "Был в Караганде, а живу в Астане. Караганда лучше"
    assert names(find_cities_in_text(text, CITIES)) == ["Караганда", "Астана"]
    # Название и алиас одного города — один результат
    assert names(find_cities_in_text("Астана она же столица", CITIES)) == ["Астана"]


def test_empty_cities():
    assert find_cities_in_text("Астана", []) == []


def test_detect_city():
    # Один город — переключаемся
    assert detect_city("я из Шымкента", CITIES, None)["id"] == "c4"
    # Тот же город, что и сейчас — ничего не меняем
    assert detect_city("я в Шымкенте", CITIES, "c4") is None
    # Новый город при текущем — переключаемся
    assert detect_city("а в Павлодаре есть?", CITIES, "c4")["id"] == "c6"
    # Текущий + другой — остаётся один «другой» → переключаемся на него
    assert detect_city("я в Шымкенте, а в Павлодаре есть?", CITIES, "c4")["id"] == "c6"
    # Два других города — неоднозначно
    assert detect_city("Астана или Алматы?", CITIES, None) is None
    # Ничего не упомянуто
    assert detect_city("здравствуйте, сколько стоит узи?", CITIES, "c1") is None
