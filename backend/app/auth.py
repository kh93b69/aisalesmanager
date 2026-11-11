from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, ClientOptions
from app.config import SUPABASE_URL, SUPABASE_ANON_KEY

# Схема авторизации — Bearer token
security = HTTPBearer()

# Отдельный клиент Supabase с anon_key для проверки токенов
# ВАЖНО: auto_refresh_token=False и persist_session=False — клиент общий для всех пользователей.
# Иначе после входа/обновления токена он запоминает сессию последнего пользователя и сам
# «обновляет» её в фоне, из-за чего у этого пользователя ломается refresh-токен (выкидывает из панели).
auth_client = create_client(
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
    options=ClientOptions(auto_refresh_token=False, persist_session=False),
)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Проверяет JWT токен из заголовка Authorization.
    Возвращает данные пользователя: {"id": "uuid", "email": "..."}
    """
    token = credentials.credentials

    try:
        # Supabase SDK сам проверяет и расшифровывает токен
        user_response = auth_client.auth.get_user(token)

        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Недействительный токен")

        return {
            "id": str(user_response.user.id),
            "email": user_response.user.email,
        }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Недействительный токен")


def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    """
    Проверяет что пользователь — администратор.
    """
    from app.database import supabase

    result = supabase.table("users") \
        .select("role") \
        .eq("id", user["id"]) \
        .limit(1) \
        .execute()

    if not result.data or result.data[0].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    user["role"] = "admin"
    return user
