from app.database import supabase


def search_knowledge(bot_id: str, query: str, match_count: int = 5) -> str:
    """
    Ищет релевантные фрагменты в базе знаний бота.

    Используем текстовый поиск по ключевым словам из запроса.
    Берём слова длиной >= 3 символов и ищем совпадения через ilike.

    bot_id — ID бота клиента
    query — текст запроса пользователя
    match_count — сколько фрагментов вернуть
    """
    try:
        # Извлекаем ключевые слова из запроса (слова длиной >= 3)
        words = [w.strip(".,!?;:()\"'") for w in query.split()]
        keywords = [w for w in words if len(w) >= 3]

        if not keywords:
            # Если нет ключевых слов — берём последние чанки
            result = supabase.table("knowledge_chunks") \
                .select("content") \
                .eq("bot_id", bot_id) \
                .limit(match_count) \
                .execute()
        else:
            # Ищем чанки, содержащие любое из ключевых слов
            found_chunks = []

            for keyword in keywords[:5]:  # Максимум 5 слов для поиска
                result = supabase.table("knowledge_chunks") \
                    .select("id, content") \
                    .eq("bot_id", bot_id) \
                    .ilike("content", f"%{keyword}%") \
                    .limit(match_count) \
                    .execute()

                if result.data:
                    for item in result.data:
                        # Не добавляем дубликаты
                        if not any(c["id"] == item["id"] for c in found_chunks):
                            found_chunks.append(item)

            if found_chunks:
                # Сортируем: чанки с большим количеством совпадений — выше
                def count_matches(chunk):
                    text_lower = chunk["content"].lower()
                    return sum(1 for kw in keywords if kw.lower() in text_lower)

                found_chunks.sort(key=count_matches, reverse=True)

                # Берём топ N
                chunks = [item["content"] for item in found_chunks[:match_count]]
                return "\n\n".join(chunks)

            # Если ничего не нашли — берём последние чанки как fallback
            result = supabase.table("knowledge_chunks") \
                .select("content") \
                .eq("bot_id", bot_id) \
                .limit(match_count) \
                .execute()

        if not result.data:
            return ""

        chunks = [item["content"] for item in result.data]
        return "\n\n".join(chunks)

    except Exception as e:
        print(f"RAG SEARCH ERROR: {e}")
        return ""
