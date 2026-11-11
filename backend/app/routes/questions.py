"""
Вопросы без ответа (Настройки → «❓ Без ответа»).

Когда в базе знаний нет ответа, бот говорит клиенту, что уточнит у руководителя,
сохраняет вопрос сюда и шлёт уведомление в Telegram (см. pipeline._reply_once).
Владелец отвечает в панели — ответ сразу добавляется в базу знаний (бот «дообучается»)
и, по желанию, отправляется клиенту в WhatsApp.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.database import supabase
from app.auth import get_current_user
from app.access import require_bot_access, check_uuid
from app import pipeline
from app import messenger

router = APIRouter()

# Метка фрагмента базы знаний с ответом руководителя (такие фрагменты не удаляются
# при загрузке нового файла базы знаний)
QA_PREFIX = "[ВОПРОС-ОТВЕТ]"

ALLOWED_QUESTION_STATUSES = ["open", "answered", "dismissed"]
MAX_ANSWER_LENGTH = 3000
QUESTIONS_LIMIT = 200


def build_qa_chunk(question: str, answer: str) -> str:
    """Текст фрагмента базы знаний: вопрос клиента и ответ руководителя."""
    return f"{QA_PREFIX} Вопрос клиента: {question.strip()}\nОтвет: {answer.strip()}"


def load_question(question_id: str, user_id: str) -> tuple[dict, dict]:
    """Вопрос + доступ к его боту (только владелец). Иначе 404/403."""
    question_id = check_uuid(question_id, "id вопроса")
    result = supabase.table("unanswered_questions").select("*").eq("id", question_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    question = result.data[0]
    access = require_bot_access(question["bot_id"], user_id, owner_only=True)
    return question, access


@router.get("/api/bots/{bot_id}/questions")
def list_questions(bot_id: str, status: str = "open", user: dict = Depends(get_current_user)):
    """Список вопросов бота с нужным статусом + количество открытых."""
    require_bot_access(bot_id, user["id"], owner_only=True)
    if status not in ALLOWED_QUESTION_STATUSES:
        raise HTTPException(status_code=400, detail="Неверный статус")

    result = supabase.table("unanswered_questions") \
        .select("*") \
        .eq("bot_id", bot_id) \
        .eq("status", status) \
        .order("created_at", desc=True) \
        .limit(QUESTIONS_LIMIT) \
        .execute()
    questions = result.data or []

    # Имя/номер клиента и название города — чтобы было понятно, кто спрашивал
    dialog_ids = []
    for question in questions:
        if question.get("dialog_id") and question["dialog_id"] not in dialog_ids:
            dialog_ids.append(question["dialog_id"])
    dialogs_by_id = {}
    if dialog_ids:
        dialogs = supabase.table("dialogs").select("id, chat_id, contact_name").in_("id", dialog_ids).execute()
        for dialog in dialogs.data or []:
            dialogs_by_id[dialog["id"]] = dialog
    cities_by_id = {}
    for city in pipeline.get_bot_cities(bot_id):
        cities_by_id[city["id"]] = city.get("name")

    items = []
    for question in questions:
        dialog = dialogs_by_id.get(question.get("dialog_id")) or {}
        item = dict(question)
        item["contact_name"] = dialog.get("contact_name")
        item["chat_id"] = dialog.get("chat_id")
        item["city_name"] = cities_by_id.get(question.get("city_id"))
        items.append(item)

    open_count = supabase.table("unanswered_questions") \
        .select("id", count="exact", head=True) \
        .eq("bot_id", bot_id) \
        .eq("status", "open") \
        .execute()
    return {"questions": items, "open_count": open_count.count or 0}


@router.post("/api/questions/{question_id}/answer")
async def answer_question(question_id: str, data: dict, user: dict = Depends(get_current_user)):
    """
    Ответ руководителя: добавляется в базу знаний (общую или города вопроса).
    Тело: {"answer": "...", "send_to_client": true|false, "city_only": true|false}
    """
    question, access = load_question(question_id, user["id"])
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Неверный формат данных")
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise HTTPException(status_code=400, detail="Напишите ответ")
    answer = answer.strip()
    if len(answer) > MAX_ANSWER_LENGTH:
        raise HTTPException(status_code=400, detail=f"Ответ не длиннее {MAX_ANSWER_LENGTH} символов")
    if question.get("status") == "answered":
        raise HTTPException(status_code=400, detail="На этот вопрос уже ответили")

    bot = access["bot"]

    # 1. Клиенту (по желанию) — от имени сотрудника. ИИ на паузу не ставим: бот продолжит диалог.
    sent_to_client = False
    if data.get("send_to_client") and question.get("dialog_id"):
        dialog = pipeline.get_dialog(question["dialog_id"])
        if dialog:
            result = await messenger.send_to_client(bot, dialog, answer, from_bot=False)
            if not result.get("ok"):
                raise HTTPException(status_code=502,
                                    detail=f"Ответ не отправлен клиенту: {result.get('error') or 'ошибка'}. В базу знаний не сохранён — попробуйте ещё раз")
            pipeline.save_message(dialog["id"], "manager", answer,
                                  external_id=result.get("message_id") or None,
                                  author_name=user.get("email"))
            pipeline.update_dialog(dialog["id"], {"needs_attention": False})
            sent_to_client = True

    # 2. В базу знаний: по умолчанию — общая (для всех городов), можно только для города вопроса
    city_id = question.get("city_id") if data.get("city_only") else None
    chunk = supabase.table("knowledge_chunks").insert({
        "bot_id": bot["id"],
        "city_id": city_id,
        "content": build_qa_chunk(question["question"], answer),
    }).execute()
    chunk_id = chunk.data[0]["id"] if chunk.data else None

    updated = supabase.table("unanswered_questions").update({
        "status": "answered",
        "answer": answer,
        "answered_at": pipeline.utc_now_iso(),
        "knowledge_chunk_id": str(chunk_id) if chunk_id is not None else None,
    }).eq("id", question["id"]).execute()

    return {
        "question": updated.data[0] if updated.data else None,
        "sent_to_client": sent_to_client,
    }


@router.post("/api/questions/{question_id}/dismiss")
def dismiss_question(question_id: str, user: dict = Depends(get_current_user)):
    """Скрыть вопрос (не нужен ответ — например, вопрос не по теме)."""
    question, _access = load_question(question_id, user["id"])
    supabase.table("unanswered_questions").update({"status": "dismissed"}).eq("id", question["id"]).execute()
    return {"status": "dismissed"}


@router.delete("/api/questions/{question_id}")
def delete_question(question_id: str, user: dict = Depends(get_current_user)):
    """Удалить вопрос; если на него отвечали — убрать ответ и из базы знаний."""
    question, _access = load_question(question_id, user["id"])
    chunk_id = question.get("knowledge_chunk_id")
    if chunk_id:
        try:
            supabase.table("knowledge_chunks").delete() \
                .eq("id", chunk_id).eq("bot_id", question["bot_id"]).execute()
        except Exception as error:
            print(f"QUESTIONS ERROR: не удалось удалить ответ из базы знаний: {error}")
    supabase.table("unanswered_questions").delete().eq("id", question["id"]).execute()
    return {"status": "deleted"}
