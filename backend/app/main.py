import os
import re
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.config import FRONTEND_URL, BACKEND_URL
from app.routes import webhooks, dialogs, settings, auth, admin, test_chat
from app.scheduler import run_scheduler_forever
from app.pipeline import recover_unanswered_dialogs

# Через сколько секунд после старта искать диалоги, оставшиеся без ответа из-за перезапуска
# 90 с — чтобы при деплое старый контейнер успел дослать свои ответы (иначе будет двойной ответ)
RECOVERY_DELAY_SECONDS = 90


def hide_secret_tokens(text: str) -> str:
    """Заменяет token=<секрет> в строке на token=*** (чтобы секрет вебхука не попал в логи)."""
    return re.sub(r"token=[^&\s\"']+", "token=***", text)


def hide_webhook_token(record) -> bool:
    """
    Фильтр для логгера uvicorn.access: прячет ?token=... в путях запросов.
    logging принимает обычную функцию как фильтр. Всегда возвращает True (запись не выбрасываем).
    """
    if isinstance(record.msg, str):
        record.msg = hide_secret_tokens(record.msg)
    if isinstance(record.args, tuple):
        new_args = []
        for arg in record.args:
            if isinstance(arg, str):
                new_args.append(hide_secret_tokens(arg))
            else:
                new_args.append(arg)
        record.args = tuple(new_args)
    return True


logging.getLogger("uvicorn.access").addFilter(hide_webhook_token)


async def recover_after_start() -> None:
    """Немного ждём после старта и отвечаем клиентам, которым не ответили из-за перезапуска."""
    await asyncio.sleep(RECOVERY_DELAY_SECONDS)
    await recover_unanswered_dialogs()


@asynccontextmanager
async def lifespan(app):
    """Запуск и остановка фоновых задач вместе с сервером."""
    # Планировщик напоминаний о счёте (горячие клиенты)
    scheduler_task = asyncio.create_task(run_scheduler_forever())
    # Восстановление неотвеченных диалогов после перезапуска (деплой)
    recovery_task = asyncio.create_task(recover_after_start())
    yield
    scheduler_task.cancel()
    recovery_task.cancel()


app = FastAPI(title="AI Sales Manager API", lifespan=lifespan)

# Разрешаем запросы с фронтенда и бэкенда (SPA раздаётся бэкендом)
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
]
if FRONTEND_URL:
    allowed_origins.append(FRONTEND_URL)
if BACKEND_URL:
    allowed_origins.append(BACKEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем API роуты
app.include_router(auth.router)
app.include_router(webhooks.router)
app.include_router(dialogs.router)
app.include_router(settings.router)
app.include_router(admin.router)
app.include_router(test_chat.router)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "AI Sales Manager"}


def resolve_static_file(static_dir: str, path: str) -> str | None:
    """
    Путь к файлу внутри static_dir или None.
    Защита от path traversal: "../app/config.py" или абсолютный путь не выпустят за пределы папки.
    """
    root = os.path.realpath(static_dir)
    real = os.path.realpath(os.path.join(root, path))
    if not real.startswith(root + os.sep):
        return None
    if not os.path.isfile(real):
        return None
    return real


# Раздаём собранный фронтенд из папки static/ (в продакшне)
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.exists(static_dir):
    app.mount("/_next", StaticFiles(directory=os.path.join(static_dir, "_next")), name="next_static")

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        # Next.js static export кладёт страницы как login.html / admin.html,
        # поэтому для "/login" пробуем: сам файл → "login.html" → "login/index.html".
        # Всё — только внутри папки static (resolve_static_file защищает от path traversal).
        candidates = [path, path + ".html", os.path.join(path, "index.html")]
        for candidate in candidates:
            file_path = resolve_static_file(static_dir, candidate)
            if file_path:
                return FileResponse(file_path)
        # Иначе отдаём index.html (главная страница панели)
        return FileResponse(os.path.join(static_dir, "index.html"))
