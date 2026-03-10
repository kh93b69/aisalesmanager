from app.database import supabase


def search_knowledge(bot_id: str, query: str, match_count: int = 3) -> str:
    """
    Ищет релевантные фрагменты в базе знаний бота.

    Используем простой поиск по содержимому (для MVP).
    В будущем можно заменить на векторный поиск с pgvector.

    bot_id — ID бота клиента
    query — текст запроса пользователя
    match_count — сколько фрагментов вернуть
    """
    try:
        # Простой поиск — берём последние чанки бота
        result = supabase.table("knowledge_chunks") \
            .select("content") \
            .eq("bot_id", bot_id) \
            .limit(match_count) \
            .execute()

        if not result.data:
            return ""

        # Склеиваем найденные фрагменты в один контекст
        chunks = [item["content"] for item in result.data]
        return "\n\n".join(chunks)

    except Exception:
        # Если база знаний пуста или ошибка — просто возвращаем пустую строку
        return ""
