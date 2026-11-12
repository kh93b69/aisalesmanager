"""
Клиент Supabase с правами service_role (для серверных операций).

Важно про надёжность соединения:
- по умолчанию библиотека ходит в базу по HTTP/2 через одно соединение на весь сервис.
  Наш сервис обращается к базе одновременно из нескольких потоков (asyncio.to_thread, фоновые задачи),
  и такое соединение время от времени рвётся: «httpx.RemoteProtocolError: Server disconnected».
  Из-за этого бот молчал, а в панели появлялось «Нет доступа к этому боту».
- поэтому подменяем HTTP-клиент запросов к таблицам на обычный HTTP/1.1 (пул соединений)
  и при обрыве связи повторяем запрос (см. RetryTransport).
"""
import time

import httpx
from supabase import create_client, ClientOptions
from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

# Таймаут запроса к таблицам, секунд (по умолчанию в библиотеке — 2 минуты)
POSTGREST_TIMEOUT_SECONDS = 15
# Таймаут подключения к базе, секунд
CONNECT_TIMEOUT_SECONDS = 3
# Сколько раз пробуем запрос при обрыве связи (всего попыток)
DB_ATTEMPTS = 3
# Пауза между попытками, секунд (умножается на номер попытки)
DB_RETRY_PAUSE_SECONDS = 0.5
# Через сколько секунд простоя закрывать соединение (раньше, чем его закроет сервер Supabase)
KEEPALIVE_SECONDS = 20

# Методы, которые безопасно повторить, даже если сервер успел выполнить первый запрос:
# чтение, обновление тех же полей теми же значениями, удаление
SAFE_TO_REPEAT_METHODS = ["GET", "HEAD", "PATCH", "DELETE"]


class RetryTransport:
    """
    Транспорт для httpx.Client: отправляет запрос через обычный HTTP/1.1 и при обрыве связи
    повторяет его (до DB_ATTEMPTS раз).
    - Не удалось подключиться (ConnectError/ConnectTimeout) — запрос не ушёл, повторяем любой.
    - Связь оборвалась во время запроса — повторяем только SAFE_TO_REPEAT_METHODS
      (вставку POST не повторяем, чтобы не создать дубль).
    """

    def __init__(self):
        self.transport = httpx.HTTPTransport(
            http2=False,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20,
                                keepalive_expiry=KEEPALIVE_SECONDS),
        )

    def handle_request(self, request):
        for attempt in range(1, DB_ATTEMPTS + 1):
            try:
                return self.transport.handle_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                if attempt == DB_ATTEMPTS:
                    raise
                print(f"DATABASE: нет связи с Supabase ({type(error).__name__}), попытка {attempt + 1}")
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as error:
                if attempt == DB_ATTEMPTS or request.method not in SAFE_TO_REPEAT_METHODS:
                    raise
                print(f"DATABASE: обрыв связи с Supabase ({type(error).__name__}), попытка {attempt + 1}")
            time.sleep(DB_RETRY_PAUSE_SECONDS * attempt)

    def close(self):
        self.transport.close()


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    options=ClientOptions(postgrest_client_timeout=POSTGREST_TIMEOUT_SECONDS, storage_client_timeout=60),
)

# Подменяем HTTP-клиент запросов к таблицам: те же адрес и заголовки (ключ service_role),
# но HTTP/1.1 и повтор при обрыве связи
_old_session = supabase.postgrest.session
supabase.postgrest.session = httpx.Client(
    base_url=_old_session.base_url,
    headers=_old_session.headers,
    # Подключение — не дольше 3 с (иначе при недоступной базе повторы надолго подвесят сервис)
    timeout=httpx.Timeout(POSTGREST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
    follow_redirects=True,
    transport=RetryTransport(),
)
_old_session.close()

# ВНИМАНИЕ: не вызывайте supabase.auth.sign_in_* / set_session на этом клиенте — библиотека
# пересоздаст клиент таблиц со своим HTTP/2 и подмена выше пропадёт (вход — через app/auth.py).


def make_retry_http_client() -> httpx.Client:
    """Такой же надёжный HTTP-клиент (HTTP/1.1 + повтор при обрыве) — для проверки входа в auth.py."""
    return httpx.Client(
        timeout=httpx.Timeout(POSTGREST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        follow_redirects=True,
        transport=RetryTransport(),
    )
