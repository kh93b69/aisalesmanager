from app.database import supabase


def search_knowledge(bot_id: str, query: str, match_count: int = 3) -> str:
    """
    Ищет релевантные фрагменты в базе знаний бота.

    Используем полнотекстовый поиск Supabase (для MVP).
    В будущем можно заменить на векторный поиск с pgvector.

    bot_id — ID бота клиента
    query — текст запроса пользователя
    match_count — сколько фрагментов вернуть
    """
    # Полнотекстовый поиск по таблице knowledge_chunks
    result = supabase.table("knowledge_chunks") \
        .select("content") \
        .eq("bot_id", bot_id) \
        .text_search("content", query) \
        .limit(match_count) \
        .execute()

    if not result.data:
        return ""

    # Склеиваем найденные фрагменты в один контекст
    chunks = [item["content"] for item in result.data]
    return "\n\n".join(chunks)
