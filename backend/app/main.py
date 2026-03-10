from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import FRONTEND_URL
from app.routes import webhooks, dialogs, settings

app = FastAPI(title="AI Sales Manager API")

# Разрешаем запросы с фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем роуты
app.include_router(webhooks.router)
app.include_router(dialogs.router)
app.include_router(settings.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "AI Sales Manager"}
