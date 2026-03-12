from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.auth import auth_client, get_current_user
from app.database import supabase

router = APIRouter()


class AuthRequest(BaseModel):
    email: str
    password: str


@router.post("/api/auth/signup")
def signup(data: AuthRequest):
    """Регистрация нового пользователя."""
    try:
        result = auth_client.auth.sign_up({
            "email": data.email,
            "password": data.password,
        })

        if not result.user:
            raise HTTPException(status_code=400, detail="Ошибка регистрации")

        # Создаём запись в таблице users
        supabase.table("users").insert({
            "id": str(result.user.id),
            "email": data.email,
            "role": "user",
        }).execute()

        # Если есть сессия — возвращаем токен сразу
        if result.session:
            return {
                "access_token": result.session.access_token,
                "refresh_token": result.session.refresh_token,
                "user": {
                    "id": str(result.user.id),
                    "email": result.user.email,
                }
            }

        return {
            "message": "Проверьте email для подтверждения",
            "user": {
                "id": str(result.user.id),
                "email": result.user.email,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/auth/login")
def login(data: AuthRequest):
    """Вход пользователя. Возвращает JWT токен."""
    try:
        result = auth_client.auth.sign_in_with_password({
            "email": data.email,
            "password": data.password,
        })

        if not result.session:
            raise HTTPException(status_code=401, detail="Неверный email или пароль")

        # Получаем роль пользователя
        user_data = supabase.table("users") \
            .select("role") \
            .eq("id", str(result.user.id)) \
            .limit(1) \
            .execute()

        role = "user"
        if user_data.data:
            role = user_data.data[0].get("role", "user")

        return {
            "access_token": result.session.access_token,
            "refresh_token": result.session.refresh_token,
            "user": {
                "id": str(result.user.id),
                "email": result.user.email,
                "role": role,
            }
        }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Неверный email или пароль")


@router.get("/api/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    """Возвращает данные текущего пользователя."""
    # Получаем полные данные из таблицы users
    result = supabase.table("users") \
        .select("*") \
        .eq("id", user["id"]) \
        .limit(1) \
        .execute()

    if result.data:
        return {"user": result.data[0]}

    return {"user": user}
