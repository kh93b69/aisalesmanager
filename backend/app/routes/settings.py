import io
import csv
import fitz  # pymupdf — для PDF
import openpyxl  # для Excel
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


def extract_text_from_pdf(content: bytes) -> str:
    """Извлекает текст из PDF файла."""
    doc = fitz.open(stream=content, filetype="pdf")
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts)


def extract_text_from_xlsx(content: bytes) -> str:
    """Извлекает текст из Excel файла (все листы)."""
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    text_parts = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        for row in ws.iter_rows(values_only=True):
            # Склеиваем ячейки строки через " | "
            cells = [str(cell) if cell is not None else "" for cell in row]
            line = " | ".join(cells).strip()
            if line and line != " | ".join([""] * len(cells)):
                text_parts.append(line)
    wb.close()
    return "\n".join(text_parts)


def extract_text_from_csv(content: bytes) -> str:
    """Извлекает текст из CSV файла."""
    text = content.decode("utf-8", errors="ignore")
    reader = csv.reader(io.StringIO(text))
    text_parts = []
    for row in reader:
        line = " | ".join(row).strip()
        if line:
            text_parts.append(line)
    return "\n".join(text_parts)


@router.post("/api/bots/{bot_id}/knowledge")
async def upload_knowledge(bot_id: str, file: UploadFile = File(...)):
    """
    Загружает файл с базой знаний.
    Поддерживаемые форматы: TXT, PDF, CSV, XLSX.
    Разбивает на чанки и сохраняет в таблицу knowledge_chunks.
    """
    content = await file.read()
    filename = file.filename.lower() if file.filename else ""

    # Определяем формат и извлекаем текст
    if filename.endswith(".pdf"):
        text = extract_text_from_pdf(content)
    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        text = extract_text_from_xlsx(content)
    elif filename.endswith(".csv"):
        text = extract_text_from_csv(content)
    else:
        # TXT и всё остальное — просто текст
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

    return {"chunks_count": len(chunks), "format": filename.split(".")[-1] if "." in filename else "txt"}


@router.post("/api/bots/{bot_id}/images")
async def upload_image(bot_id: str, file: UploadFile = File(...)):
    """
    Загружает картинку в Supabase Storage для бота.
    Бот сможет отправлять её клиентам.
    """
    content = await file.read()
    filename = file.filename or "image.jpg"

    # Путь в Supabase Storage: bot_images/{bot_id}/{filename}
    storage_path = f"{bot_id}/{filename}"

    # Загружаем в Supabase Storage (бакет bot_images)
    supabase.storage.from_("bot_images").upload(storage_path, content)

    # Получаем публичный URL
    public_url = supabase.storage.from_("bot_images").get_public_url(storage_path)

    # Сохраняем URL в таблицу knowledge_chunks с пометкой [IMAGE]
    supabase.table("knowledge_chunks").insert({
        "bot_id": bot_id,
        "content": f"[IMAGE:{public_url}] {filename}",
    }).execute()

    return {"url": public_url, "filename": filename}


@router.get("/api/bots/{bot_id}/images")
def get_images(bot_id: str):
    """Получает список загруженных картинок бота."""
    result = supabase.table("knowledge_chunks") \
        .select("id, content") \
        .eq("bot_id", bot_id) \
        .like("content", "[IMAGE:%") \
        .execute()

    images = []
    for item in result.data:
        # Парсим URL из формата [IMAGE:url] filename
        content = item["content"]
        url = content.split("]")[0].replace("[IMAGE:", "")
        name = content.split("] ")[-1] if "] " in content else "image"
        images.append({"id": item["id"], "url": url, "name": name})

    return {"images": images}
