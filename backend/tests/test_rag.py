"""
Тесты базы знаний (app/rag.py): отбор чанков — чистые функции,
постраничная выборка — с подменённым клиентом Supabase.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test.test.test")

from app import rag  # noqa: E402


def chunk(chunk_id, content, city_id=None):
    return {"id": chunk_id, "content": content, "city_id": city_id}


def test_small_base_returned_whole_without_images():
    chunks = [
        chunk("1", "Приём терапевта — 8000 тг"),
        chunk("2", "[IMAGE:https://x/1.jpg] прайс.jpg"),
        chunk("3", "   "),
        chunk("4", "Адрес: ул. Абая 1", "city-1"),
    ]
    text = rag.build_knowledge_text(chunks, "любой вопрос")
    assert text == "Приём терапевта — 8000 тг\n\nАдрес: ул. Абая 1"


def test_extract_keywords():
    keywords = rag.extract_keywords("Здравствуйте! Сколько стоит консультация гинеколога и УЗИ?")
    assert "узи" in keywords
    assert "консультац" in keywords
    assert "стои" not in keywords
    assert "здравствуйте" not in keywords
    assert "сколько" not in keywords


def test_big_base_keyword_search_stable_order():
    filler = "x" * 100
    chunks = [chunk(str(i), f"Раздел {i}. {filler}") for i in range(10)]
    chunks[7] = chunk("7", f"УЗИ брюшной полости — 12000 тг. {filler}")
    chunks[2] = chunk("2", f"УЗИ щитовидной железы и консультация эндокринолога. {filler}")
    chunks[5] = chunk("5", f"Консультации гинеколога. {filler}")

    selected = rag.select_chunks(chunks, "консультация и узи", char_limit=500, top_count=2)
    # Чанк 2 — два совпадения; чанки 5 и 7 — по одному, побеждает более ранний (5).
    # Итог — в исходном порядке
    assert [c["id"] for c in selected] == ["2", "5"]


def test_limits():
    assert rag.FULL_KB_CHAR_LIMIT == 150000
    assert rag.TOP_CHUNKS_COUNT == 60


def test_big_base_no_matches_city_first():
    # Чётные — общие, нечётные — чанки города
    chunks = []
    for i in range(10):
        city_id = "city-1" if i % 2 == 1 else None
        chunks.append(chunk(str(i), "текст " * 50, city_id))

    selected = rag.select_chunks(chunks, "абракадабра", char_limit=100, top_count=7)
    assert [c["id"] for c in selected] == ["1", "3", "5", "7", "9", "0", "2"]

    # Пустой запрос (нет ключевых слов) — то же самое
    selected = rag.select_chunks(chunks, "здравствуйте!", char_limit=100, top_count=3)
    assert [c["id"] for c in selected] == ["1", "3", "5"]


def test_big_base_tie_prefers_city():
    filler = "x" * 100
    chunks = [
        chunk("1", f"Цена УЗИ общая {filler}"),
        chunk("2", f"Цена УЗИ в филиале {filler}", "city-1"),
        chunk("3", f"Что-то другое {filler}"),
    ]
    selected = rag.select_chunks(chunks, "узи", char_limit=100, top_count=1)
    assert [c["id"] for c in selected] == ["2"]


def test_keyword_must_start_word():
    # «узи» не должно находиться внутри слова «музыка»
    assert rag.count_keyword_matches("Музыка в холле", ["узи"]) == 0
    assert rag.count_keyword_matches("Цена УЗИ", ["узи"]) == 1


class FakeQuery:
    """Поддельный запрос Supabase: запоминает фильтры и отдаёт страницы из списка строк."""

    def __init__(self, rows, log):
        self.rows = rows
        self.log = log
        self.start = 0
        self.end = 0

    def select(self, columns):
        self.log.append(("select", columns))
        return self

    def eq(self, column, value):
        self.log.append(("eq", column, value))
        return self

    def or_(self, filters):
        self.log.append(("or", filters))
        return self

    def is_(self, column, value):
        self.log.append(("is", column, value))
        return self

    def order(self, column):
        self.log.append(("order", column))
        return self

    def range(self, start, end):
        self.log.append(("range", start, end))
        self.start = start
        self.end = end
        return self

    def execute(self):
        class Result:
            pass
        result = Result()
        result.data = self.rows[self.start:self.end + 1]
        return result


class FakeSupabase:
    def __init__(self, rows):
        self.rows = rows
        self.log = []

    def table(self, name):
        self.log.append(("table", name))
        return FakeQuery(self.rows, self.log)


def test_fetch_pages_over_1000(monkeypatch):
    rows = [chunk(str(i).zfill(5), f"чанк {i}") for i in range(2345)]
    fake = FakeSupabase(rows)
    monkeypatch.setattr(rag, "supabase", fake)

    result = rag.fetch_city_chunks("bot-1", "city-1")
    assert len(result) == 2345
    ranges = [item for item in fake.log if item[0] == "range"]
    assert ranges == [("range", 0, 999), ("range", 1000, 1999), ("range", 2000, 2999)]
    assert ("or", "city_id.eq.city-1,city_id.is.null") in fake.log


def test_fetch_general_only(monkeypatch):
    fake = FakeSupabase([chunk("1", "общая")])
    monkeypatch.setattr(rag, "supabase", fake)
    assert rag.get_knowledge_for_city("bot-1", None, "вопрос") == "общая"
    assert ("is", "city_id", "null") in fake.log
    # Обратная совместимость
    assert rag.search_knowledge("bot-1", "вопрос") == "общая"


def test_error_returns_empty(monkeypatch):
    class BrokenSupabase:
        def table(self, name):
            raise RuntimeError("нет сети")

    monkeypatch.setattr(rag, "supabase", BrokenSupabase())
    assert rag.get_knowledge_for_city("bot-1", "city-1", "вопрос") == ""
