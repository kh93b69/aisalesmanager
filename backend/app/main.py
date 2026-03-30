import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.config import FRONTEND_URL, BACKEND_URL
from app.routes import webhooks, dialogs, settings, auth, admin

app = FastAPI(title="AI Sales Manager API")

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


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "AI Sales Manager"}


# Раздаём собранный фронтенд из папки static/ (в продакшне)
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.exists(static_dir):
    app.mount("/_next", StaticFiles(directory=os.path.join(static_dir, "_next")), name="next_static")

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        # Пробуем отдать файл из static/
        file_path = os.path.join(static_dir, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        # Иначе отдаём index.html (SPA роутинг)
        return FileResponse(os.path.join(static_dir, "index.html"))
