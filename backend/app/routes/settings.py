from fastapi import APIRouter, UploadFile, File
from app.database import supabase

router = APIRouter()


@router.get("/api/bots")
def get_bots():
    """Получает список всех ботов."""
    result = supabase.table("bots").select("*").execute()
    return {"bots": result.data}


@router.post("/api/bots")
def create_bot(data: dict):
    """
    Создаёт нового бота.
    data: {name, system_prompt, whatsapp_session}
    """
    result = supabase.table("bots").insert({
        "name": data.get("name", "Новый бот"),
        "system_prompt": data.get("system_prompt", ""),
        "whatsapp_session": data.get("whatsapp_session", "default"),
    }).execute()

    return {"bot": result.data[0]}


@router.put("/api/bots/{bot_id}")
def update_bot(bot_id: str, data: dict):
    """Обновляет настройки бота (промпт, имя и т.д.)."""
    result = supabase.table("bots") \
        .update(data) \
        .eq("id", bot_id) \
        .execute()

    return {"bot": result.data[0]}


@router.post("/api/bots/{bot_id}/knowledge")
async def upload_knowledge(bot_id: str, file: UploadFile = File(...)):
    """
    Загружает файл с базой знаний (текст/PDF).
    Разбивает на чанки и сохраняет в таблицу knowledge_chunks.
    """
    content = await file.read()
    text = content.decode("utf-8", errors="ignore")

    # Разбиваем текст на чанки по ~500 символов
    chunk_size = 500
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size].strip()
        if chunk:
            chunks.append({"bot_id": bot_id, "content": chunk})

    if chunks:
        # Удаляем старые чанки этого бота
        supabase.table("knowledge_chunks") \
            .delete() \
            .eq("bot_id", bot_id) \
            .execute()

        # Вставляем новые
        supabase.table("knowledge_chunks").insert(chunks).execute()

    return {"chunks_count": len(chunks)}
