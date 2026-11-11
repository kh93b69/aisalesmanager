from supabase import create_client, ClientOptions
from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

# Клиент Supabase с правами service_role (для серверных операций).
# Запросы к БД синхронные: если Supabase «зависнет», ждём не больше 15 секунд
# (по умолчанию — 2 минуты, и всё это время сервис не отвечает на другие запросы).
# Загрузка картинок в Storage — до 60 секунд.
supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    options=ClientOptions(postgrest_client_timeout=15, storage_client_timeout=60),
)
