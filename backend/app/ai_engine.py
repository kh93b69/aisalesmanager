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
        full_system_prompt += f"\n\n--- БАЗА ЗНАНИЙ ---\n{knowledge_context}\n--- КОНЕЦ БАЗЫ ЗНАНИЙ ---\n\nИспользуй информацию из базы знаний для ответа. Если в базе знаний нет нужной информации, отвечай на основе общей логики промпта."

    # Формируем сообщения для OpenAI API
    openai_messages = [{"role": "system", "content": full_system_prompt}]
    openai_messages.extend(messages)

    # Запрос к OpenAI API напрямую через httpx (без SDK — меньше зависимостей)
    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": openai_messages,
            "max_tokens": 1024,
        },
        timeout=30,
    )

    data = response.json()
    return data["choices"][0]["message"]["content"]
