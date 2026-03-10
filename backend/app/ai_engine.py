import anthropic
from app.config import ANTHROPIC_API_KEY

# Клиент Claude API
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def get_ai_response(system_prompt: str, messages: list, knowledge_context: str = "") -> str:
    """
    Отправляет сообщение в Claude и получает ответ.

    system_prompt — личность менеджера (из настроек клиента)
    messages — история диалога [{role: "user"/"assistant", content: "..."}]
    knowledge_context — релевантный контекст из базы знаний (RAG)
    """
    # Если есть контекст из базы знаний, добавляем его в системный промпт
    full_system_prompt = system_prompt
    if knowledge_context:
        full_system_prompt += f"\n\n--- БАЗА ЗНАНИЙ ---\n{knowledge_context}\n--- КОНЕЦ БАЗЫ ЗНАНИЙ ---\n\nИспользуй информацию из базы знаний для ответа. Если в базе знаний нет нужной информации, отвечай на основе общей логики промпта."

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=full_system_prompt,
        messages=messages,
    )

    return response.content[0].text
