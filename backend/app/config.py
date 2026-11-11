import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Claude API (Anthropic) — основной ИИ бота
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Модель по умолчанию, если у бота не задана своя
DEFAULT_AI_MODEL = os.getenv("DEFAULT_AI_MODEL", "claude-haiku-5-5")

# OpenAI API — только для расшифровки голосовых сообщений (необязательно)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Telegram: бот-канал для клиентов (как раньше)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# ID бота (из таблицы bots), которому идут сообщения Telegram-канала.
# Задаётся только администратором сервиса — чтобы чужой пользователь не мог «перехватить» канал.
TELEGRAM_BOT_ID = os.getenv("TELEGRAM_BOT_ID", "")
# Telegram: бот для уведомлений сотрудникам. Если не задан — используем TELEGRAM_BOT_TOKEN
TELEGRAM_NOTIFY_BOT_TOKEN = os.getenv("TELEGRAM_NOTIFY_BOT_TOKEN", "") or TELEGRAM_BOT_TOKEN

# Wazzup API
WAZZUP_API_URL = os.getenv("WAZZUP_API_URL", "https://api.wazzup24.com/v3")

# Сколько секунд ждать новых сообщений клиента перед ответом.
# Клиенты часто пишут несколькими сообщениями подряд — отвечаем на все сразу.
REPLY_DEBOUNCE_SECONDS = float(os.getenv("REPLY_DEBOUNCE_SECONDS", "6"))

# Часовой пояс клиники (Казахстан — единое время UTC+5)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Almaty")

# Приложение
APP_ENV = os.getenv("APP_ENV", "development")
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "change-me")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3001")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
