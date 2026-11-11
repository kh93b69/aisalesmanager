"""
Простейшая заглушка клиента Supabase для тестов (данные — словарь таблиц в памяти).
Поддерживает только то, что нужно нашим тестам: select/eq/gte/order/limit/range/update/execute.
"""


class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = {}
        self.update_fields = None
        self.range_start = None
        self.range_end = None
        self.limit_value = None

    def select(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def range(self, start, end):
        self.range_start = start
        self.range_end = end
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def update(self, fields):
        self.update_fields = fields
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        matched = []
        for row in rows:
            ok = True
            for key, value in self.filters.items():
                if row.get(key) != value:
                    ok = False
            if ok:
                matched.append(row)
        if self.update_fields is not None:
            for row in matched:
                row.update(self.update_fields)
            return FakeResult(matched)
        if self.range_start is not None:
            matched = matched[self.range_start:self.range_end + 1]
        if self.limit_value is not None:
            matched = matched[:self.limit_value]
        return FakeResult(matched)


class FakeSupabase:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return FakeQuery(self.db, name)
