"""
База знаний бота с учётом города.

Чанки базы знаний лежат в таблице knowledge_chunks:
- city_id = NULL — общая информация для всех городов;
- city_id = <id города> — данные конкретного филиала (адреса, цены, врачи).

Если база города + общая небольшая — отдаём её ЦЕЛИКОМ (стабильный текст → работает
кэширование промпта Claude и ИИ видит всё). Если большая — ищем по ключевым словам.

Логика отбора вынесена в чистые функции (принимают список чанков) — их легко тестировать без БД.
"""

from app.database import supabase
from app.cities import normalize_text

# Если вся база (город + общая) не длиннее этого — отдаём её целиком.
# ~150 тыс. символов ≈ 40–50 тыс. токенов: целиком отданная база стабильна и кэшируется,
# а у Haiku 5.5 это дёшево. Так ИИ видит всё и не теряет нужные факты.
FULL_KB_CHAR_LIMIT = 150000

# Сколько чанков отдаём, если база большая и приходится искать
TOP_CHUNKS_COUNT = 60

# Supabase/PostgREST отдаёт максимум 1000 строк за запрос — читаем страницами
PAGE_SIZE = 1000

# Метка картинки в knowledge_chunks — такие чанки не являются текстом базы знаний
IMAGE_PREFIX = "[IMAGE:"

# Слова, которые не помогают искать (служебные и «разговорные»)
STOP_WORDS = {
    "а", "в", "и", "к", "о", "с", "у", "я", "на", "но", "не", "ни", "по", "за", "из", "до",
    "от", "об", "во", "со", "же", "ли", "бы", "то", "да", "нет", "ну", "вы", "мы", "он",
    "она", "оно", "они", "его", "ее", "их", "мне", "меня", "вас", "вам", "нам", "нас",
    "мой", "моя", "мое", "мои", "ваш", "ваша", "ваше", "ваши", "это", "этот", "эта", "эти",
    "тот", "так", "как", "что", "чем", "кто", "где", "когда", "какой", "какая", "какие",
    "какое", "сколько", "можно", "нужно", "надо", "есть", "был", "была", "было", "будет",
    "или", "если", "для", "при", "про", "под", "над", "без", "уже", "еще", "тоже", "также",
    "очень", "только", "все", "всё", "весь", "вся", "там", "тут", "здесь", "вот", "хочу",
    "хотел", "хотела", "хотим", "подскажите", "скажите", "пожалуйста", "здравствуйте",
    "привет", "добрый", "день", "вечер", "утро", "спасибо", "интересует", "интересно",
    "узнать", "вопрос", "стоит", "ок", "окей", "ага", "угу",
}


def is_text_chunk(chunk: dict) -> bool:
    """True, если чанк — текст базы знаний (не картинка и не пустой)."""
    content = (chunk.get("content") or "").strip()
    if not content:
        return False
    if content.startswith(IMAGE_PREFIX):
        return False
    return True


def make_word_stem(word: str) -> str:
    """
    Очень простая «основа» слова: отрезаем окончание, чтобы «консультация»,
    «консультации» и «консультацию» совпадали. Короткие слова (УЗИ, МРТ) не трогаем.
    """
    if len(word) >= 7:
        return word[:-2]
    if len(word) >= 5:
        return word[:-1]
    return word


def extract_keywords(query: str) -> list[str]:
    """Ключевые слова запроса (основы), без стоп-слов и дублей, в порядке появления."""
    words = normalize_text(query).replace("-", " ").split()
    keywords = []
    for word in words:
        if word in STOP_WORDS:
            continue
        # Короткие слова пропускаем, но цифры (например «3») тоже не ищем — слишком общие
        if len(word) < 3:
            continue
        stem = make_word_stem(word)
        if stem not in keywords:
            keywords.append(stem)
    return keywords


def count_keyword_matches(chunk_text: str, keywords: list[str]) -> int:
    """Сколько разных ключевых слов встречается в тексте чанка."""
    normalized = " " + normalize_text(chunk_text).replace("-", " ")
    matches = 0
    for keyword in keywords:
        # Ищем основу с начала слова — чтобы «узи» не находилось внутри «музыки»
        if " " + keyword in normalized:
            matches += 1
    return matches


def join_chunks(chunks: list[dict]) -> str:
    """Склеивает тексты чанков через пустую строку."""
    return "\n\n".join((chunk.get("content") or "").strip() for chunk in chunks)


def city_priority(chunk: dict) -> int:
    """0 — чанк привязан к городу (важнее), 1 — общий чанк."""
    if chunk.get("city_id"):
        return 0
    return 1


def select_chunks(chunks: list[dict], query: str,
                  char_limit: int = FULL_KB_CHAR_LIMIT,
                  top_count: int = TOP_CHUNKS_COUNT) -> list[dict]:
    """
    Чистая функция: выбирает, какие чанки отдать ИИ.

    chunks — чанки в стабильном порядке (по id), уже отфильтрованные по городу.
    1. Убираем картинки и пустые.
    2. Если суммарная длина <= char_limit — возвращаем все.
    3. Иначе — top_count чанков с наибольшим числом совпадений ключевых слов запроса
       (при равенстве — сначала чанки города, потом общие, дальше по исходному порядку).
       Итог возвращаем в исходном порядке.
       Если ключевых слов нет или совпадений нет — top_count чанков: сначала чанки
       текущего города, затем общие (внутри каждой группы — исходный порядок).
    """
    text_chunks = [chunk for chunk in chunks if is_text_chunk(chunk)]

    total_length = 0
    for chunk in text_chunks:
        total_length += len(chunk["content"].strip())
    if total_length <= char_limit:
        return text_chunks

    keywords = extract_keywords(query)

    # Считаем совпадения для каждого чанка, запоминаем его исходную позицию
    scored = []
    for index, chunk in enumerate(text_chunks):
        score = count_keyword_matches(chunk["content"], keywords) if keywords else 0
        if score > 0:
            scored.append((score, city_priority(chunk), index))

    if not scored:
        # Ничего не нашли — отдаём данные города в приоритете перед общими
        city_chunks = [chunk for chunk in text_chunks if city_priority(chunk) == 0]
        general_chunks = [chunk for chunk in text_chunks if city_priority(chunk) == 1]
        return (city_chunks + general_chunks)[:top_count]

    # Больше совпадений — выше; при равенстве — чанк города, затем раньше по порядку
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    best_indexes = sorted(item[2] for item in scored[:top_count])
    return [text_chunks[index] for index in best_indexes]


def build_knowledge_text(chunks: list[dict], query: str,
                         char_limit: int = FULL_KB_CHAR_LIMIT,
                         top_count: int = TOP_CHUNKS_COUNT) -> str:
    """Чистая функция: выбранные чанки одним текстом (через пустую строку)."""
    return join_chunks(select_chunks(chunks, query, char_limit, top_count))


def fetch_city_chunks(bot_id: str, city_id: str | None) -> list[dict]:
    """
    Читает из БД все чанки бота для города (city_id = city_id ИЛИ NULL).
    Если city_id None — только общие (city_id IS NULL).
    Читаем страницами по PAGE_SIZE строк, сортировка по id — порядок стабильный.
    Ошибки пробрасываются наружу (их ловит get_knowledge_for_city).
    """
    all_chunks = []
    start = 0
    while True:
        query = supabase.table("knowledge_chunks") \
            .select("id, content, city_id") \
            .eq("bot_id", bot_id)

        if city_id:
            query = query.or_(f"city_id.eq.{city_id},city_id.is.null")
        else:
            query = query.is_("city_id", "null")

        result = query.order("id").range(start, start + PAGE_SIZE - 1).execute()
        page = result.data or []
        all_chunks.extend(page)

        # Неполная страница — значит, это последняя
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    return all_chunks


def get_knowledge_for_city(bot_id: str, city_id: str | None, query: str) -> str:
    """
    Текст базы знаний для ответа клиенту: данные города + общие данные.
    Небольшая база отдаётся целиком, большая — по ключевым словам запроса.
    При ошибке — пустая строка.
    """
    try:
        chunks = fetch_city_chunks(bot_id, city_id)
        return build_knowledge_text(chunks, query)
    except Exception as e:
        print(f"RAG ERROR: bot={bot_id} city={city_id}: {e}")
        return ""


def search_knowledge(bot_id: str, query: str, match_count: int = 5) -> str:
    """Старая функция — оставлена для обратной совместимости (только общая база)."""
    return get_knowledge_for_city(bot_id, None, query)
