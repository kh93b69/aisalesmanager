import httpx
from app.config import OPENAI_API_KEY


def get_ai_response(system_prompt: str, messages: list, knowledge_context: str = "") -> str:
    """
    Отправляет сообщение в OpenAI GPT и получает ответ.

    system_prompt — личность менеджера (из настроек клиента)
    messages — история диалога [{role: "user"/"assistant", content: "..."}]
    knowledge_context — релевантный контекст из базы знаний (RAG)
    """
    # Если есть контекст из базы знаний, добавляем его в системный промпт
    full_system_prompt = system_prompt
    if knowledge_context:
        full_system_prompt += (
            "\n\n--- БАЗА ЗНАНИЙ ---\n"
            f"{knowledge_context}\n"
            "--- КОНЕЦ БАЗЫ ЗНАНИЙ ---\n\n"
            "ВАЖНЫЕ ПРАВИЛА:\n"
            "- Используй ТОЛЬКО данные из базы знаний: цены, названия, характеристики.\n"
            "- Если клиент спрашивает о товаре/услуге из базы — отвечай точными данными.\n"
            "- Если в базе знаний нет информации по вопросу — честно скажи, что нужно уточнить у менеджера.\n"
            "- НЕ придумывай цены, характеристики и факты, которых нет в базе знаний.\n"
            "- Отвечай кратко и по делу, как опытный менеджер по продажам."
        )

    # Формируем сообщения для OpenAI API
    openai_messages = [{"role": "system", "content": full_system_prompt}]
    openai_messages.extend(messages)

    try:
        # Запрос к OpenAI API напрямую через httpx (без SDK — меньше зависимостей)
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "messages": openai_messages,
                "max_tokens": 2048,
                "temperature": 0.7,
            },
            timeout=60,
        )

        data = response.json()

        # Проверяем наличие ошибки в ответе
        if "error" in data:
            print(f"OPENAI API ERROR: {data['error']}")
            return "Извините, произошла техническая ошибка. Попробуйте позже."

        return data["choices"][0]["message"]["content"]

    except httpx.TimeoutException:
        print("OPENAI API TIMEOUT")
        return "Извините, ответ занимает слишком много времени. Попробуйте позже."
    except Exception as e:
        print(f"OPENAI API ERROR: {e}")
        return "Извините, произошла техническая ошибка. Попробуйте позже."
