"""
Определение города клиента по тексту сообщения.

У клиники один номер WhatsApp на все города, поэтому бот должен понять,
из какого города клиент, и отвечать по базе знаний именно этого города.
Здесь — простая проверка упоминаний города в тексте (без ИИ и внешних библиотек),
с учётом русских падежей: «Астана», «в Астане», «из Астаны», «Карагандой» и т.п.
"""

from app.database import supabase

# Гласные (и й/ь), которые отрезаем с конца названия, чтобы получить основу слова:
# «астана» → «астан», «костанай» → «костана», «актобе» → «актоб».
ENDING_LETTERS = "аяыиеоуьй"

# Минимальная длина основы. Короче — слишком велик риск ложных срабатываний.
MIN_STEM_LENGTH = 4

# Сколько букв может идти после ДЛИННОЙ основы (5+ букв) в слове текста (падежное окончание):
# «астан» + «ой» = «астаной», «караганд» + «ы» = «караганды».
MAX_ENDING_LENGTH = 3

# Основа короче этого — «короткая»: у неё легко найти совпадение с обычными словами
# («акта» от Актау совпадает с «актами»), поэтому для неё разрешены только
# точное название или окончания из короткого списка ниже.
SHORT_STEM_LENGTH = 5

# Падежные окончания для коротких основ: «в Семее», «из Семея», «в Актау».
SHORT_STEM_ENDINGS = ["а", "е", "и", "у", "ы", "ю", "я", "ой", "ем", "ом"]

# Обычные слова, которые совпадают с формой названия города, но городом не являются
# никогда: «копия акта», «акты сверки», «в акте» (Актау не склоняется).
NEVER_CITY_WORDS = ["акта", "акты", "акте"]

# Слова, которые бывают и городом, и обычным словом: «Семей» (город) и «наших семей».
# Такое слово считаем городом, только если оно стоит первым в сообщении
# («Семей», «Семей, хочу записаться») или после слова из CITY_CONTEXT_WORDS («я из Семей»).
CONTEXT_REQUIRED_WORDS = ["семей"]
CITY_CONTEXT_WORDS = ["в", "во", "из", "с", "со", "г", "гор", "город", "городе", "города", "городу"]

# Казахские буквы → похожие русские, чтобы «Қарағанды» и «Өскемен» тоже находились
KAZAKH_LETTERS = {
    "ә": "а",
    "ғ": "г",
    "қ": "к",
    "ң": "н",
    "ө": "о",
    "ұ": "у",
    "ү": "у",
    "һ": "х",
    "і": "и",
}


def normalize_text(text: str) -> str:
    """
    Приводит текст к простому виду для поиска:
    нижний регистр, ё → е, казахские буквы → русские,
    вся пунктуация → пробелы (дефис внутри слова сохраняется: «усть-каменогорск»),
    лишние пробелы убираются.
    """
    if not text:
        return ""

    text = text.lower().replace("ё", "е")

    # Посимвольно: буквы и цифры оставляем, дефис — пока оставляем, остальное — пробел
    letters = []
    for char in text:
        if char in KAZAKH_LETTERS:
            letters.append(KAZAKH_LETTERS[char])
        elif char.isalnum():
            letters.append(char)
        elif char in "-–—":
            letters.append("-")
        else:
            letters.append(" ")
    text = "".join(letters)

    # Дефис оставляем, только если он стоит между двумя буквами/цифрами
    result = []
    for index, char in enumerate(text):
        if char == "-":
            before = text[index - 1] if index > 0 else " "
            after = text[index + 1] if index + 1 < len(text) else " "
            if before.isalnum() and after.isalnum():
                result.append("-")
            else:
                result.append(" ")
        else:
            result.append(char)

    # Схлопываем пробелы
    return " ".join("".join(result).split())


def get_bot_cities(bot_id: str) -> list[dict]:
    """Все города бота, отсортированные по sort_order и названию. При ошибке — []."""
    try:
        result = supabase.table("cities") \
            .select("*") \
            .eq("bot_id", bot_id) \
            .order("sort_order") \
            .order("name") \
            .execute()
        return result.data or []
    except Exception as e:
        print(f"CITIES ERROR: {e}")
        return []


def get_city_variants(city: dict) -> list[str]:
    """
    Все варианты написания города: название + алиасы (через запятую).
    Варианты нормализованы, дефисы заменены пробелами («усть-каменогорск» → «усть каменогорск»),
    чтобы одинаково находить и «Усть-Каменогорск», и «Усть Каменогорск».
    """
    raw_variants = [city.get("name") or ""]
    aliases = city.get("aliases") or ""
    raw_variants.extend(aliases.split(","))

    variants = []
    for raw in raw_variants:
        variant = normalize_text(raw).replace("-", " ").strip()
        if variant and variant not in variants:
            variants.append(variant)
    return variants


def get_stem(variant: str) -> str:
    """
    Основа названия: отрезаем одну последнюю гласную (а, я, ы, и, е, о, у, ь, й),
    если основа остаётся не короче MIN_STEM_LENGTH букв.
    «астана» → «астан», «актау» → «акта», «семей» → «семе», «шымкент» → «шымкент», «укг» → «укг».
    """
    if variant and variant[-1] in ENDING_LETTERS:
        stem = variant[:-1]
        if len(stem.replace(" ", "")) >= MIN_STEM_LENGTH:
            return stem
    return variant


def is_ending_allowed(stem: str, variant: str, ending: str) -> bool:
    """
    Можно ли считать слово «основа + окончание» упоминанием города.

    - Точное название (или алиас) — всегда да.
    - Длинная основа (5+ букв): окончание до MAX_ENDING_LENGTH букв («астаной», «шымкенте»).
    - Короткий вариант (меньше MIN_STEM_LENGTH букв, например «укг») — только точное совпадение.
    - Короткая основа (4 буквы): только окончания из SHORT_STEM_ENDINGS («семее», «актау»).
    """
    if stem + ending == variant:
        return True

    stem_letters = len(stem.replace(" ", ""))
    variant_letters = len(variant.replace(" ", ""))

    if variant_letters < MIN_STEM_LENGTH:
        return False
    if stem_letters >= SHORT_STEM_LENGTH:
        return len(ending) <= MAX_ENDING_LENGTH
    return ending in SHORT_STEM_ENDINGS


def is_word_allowed(word: str, previous_word: str) -> bool:
    """
    Проверка найденного слова на «обычные слова, похожие на город».
    previous_word — слово перед найденным ("" если найденное слово первое в сообщении).
    """
    if word in NEVER_CITY_WORDS:
        return False
    if word in CONTEXT_REQUIRED_WORDS:
        return previous_word == "" or previous_word in CITY_CONTEXT_WORDS
    return True


def find_variant_position(text: str, variant: str) -> int:
    """
    Ищет вариант названия в тексте. text — уже нормализованный, с пробелами вместо дефисов.
    Возвращает позицию первого совпадения или -1.

    Совпадение засчитывается, если:
    - основа начинается с начала слова (перед ней пробел или начало текста);
    - окончание после основы разрешено (см. is_ending_allowed);
    - слово не из списка «обычных слов» (см. is_word_allowed).
    Так «актобе» не найдётся в слове «акт», а «астана» найдётся в «астаной».
    """
    stem = get_stem(variant)
    if not stem:
        return -1

    start = 0
    while True:
        position = text.find(stem, start)
        if position == -1:
            return -1
        start = position + 1

        # Основа должна начинаться с начала слова
        if position > 0 and text[position - 1] != " ":
            continue

        # Окончание — всё, что идёт после основы до конца слова
        end = position + len(stem)
        word_end = text.find(" ", end)
        if word_end == -1:
            word_end = len(text)
        ending = text[end:word_end]
        if not is_ending_allowed(stem, variant, ending):
            continue

        # Для однословных названий проверяем само слово и слово перед ним
        if " " not in variant:
            word = text[position:word_end]
            words_before = text[:position].split()
            previous_word = words_before[-1] if words_before else ""
            if not is_word_allowed(word, previous_word):
                continue

        return position


def find_cities_in_text(text: str, cities: list[dict]) -> list[dict]:
    """
    Возвращает города, упомянутые в тексте: без дублей, в порядке появления в тексте.
    Учитываются название и алиасы города, русские падежные окончания.
    """
    normalized = normalize_text(text).replace("-", " ")
    if not normalized or not cities:
        return []

    # Для каждого города находим самое раннее упоминание любого из вариантов
    found = []  # список пар (позиция, порядковый номер города, город)
    for city_index, city in enumerate(cities):
        best_position = -1
        for variant in get_city_variants(city):
            position = find_variant_position(normalized, variant)
            if position != -1 and (best_position == -1 or position < best_position):
                best_position = position
        if best_position != -1:
            found.append((best_position, city_index, city))

    # Сортируем по позиции в тексте (при равенстве — по порядку в списке городов)
    found.sort(key=lambda item: (item[0], item[1]))

    result = []
    seen_ids = []
    for position, city_index, city in found:
        city_key = str(city.get("id") or city.get("name"))
        if city_key in seen_ids:
            continue
        seen_ids.append(city_key)
        result.append(city)
    return result


def detect_city(text: str, cities: list[dict], current_city_id: str | None) -> dict | None:
    """
    Возвращает город, на который НУЖНО переключить диалог, или None (ничего не менять).

    Если в тексте упомянут ровно один город, отличный от текущего, — это он.
    Если не упомянуто ни одного другого или упомянуто несколько — None
    (неоднозначно, пусть разберётся ИИ).
    """
    mentioned = find_cities_in_text(text, cities)

    others = []
    for city in mentioned:
        if current_city_id is None or str(city.get("id")) != str(current_city_id):
            others.append(city)

    if len(others) == 1:
        return others[0]
    return None
