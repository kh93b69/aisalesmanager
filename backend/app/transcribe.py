"""
Расшифровка голосовых сообщений через OpenAI (модель gpt-4o-mini-transcribe).

Язык не фиксируем: клиенты пишут и на русском, и на казахском — модель определит сама.
"""
import httpx

from app.config import OPENAI_API_KEY

OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"

# Максимальный размер аудиофайла (OpenAI принимает до 25 МБ, берём с запасом)
MAX_AUDIO_BYTES = 20 * 1024 * 1024

# Транспорт httpx — в тестах можно подставить httpx.MockTransport
_transport = None


async def _download_audio(url: str) -> bytes:
    """
    Скачивает аудиофайл по ссылке. Возвращает байты или b"" при ошибке / слишком большом файле.
    Скачиваем потоком, чтобы не загружать в память огромный файл целиком.
    """
    try:
        async with httpx.AsyncClient(timeout=30, transport=_transport, follow_redirects=True) as http_client:
            async with http_client.stream("GET", url) as response:
                if response.status_code != 200:
                    print(f"TRANSCRIBE ERROR: не удалось скачать аудио, HTTP {response.status_code}")
                    return b""

                chunks = []
                total_size = 0
                async for chunk in response.aiter_bytes():
                    total_size += len(chunk)
                    if total_size > MAX_AUDIO_BYTES:
                        print("TRANSCRIBE ERROR: аудиофайл больше 20 МБ — пропускаем")
                        return b""
                    chunks.append(chunk)
                return b"".join(chunks)
    except Exception as e:
        print(f"TRANSCRIBE ERROR: ошибка скачивания аудио: {type(e).__name__}: {e}")
        return b""


async def transcribe_audio_url(url: str) -> str:
    """
    Скачивает голосовое по url и отправляет на расшифровку в OpenAI.
    Возвращает текст или "" при любой ошибке (или если OPENAI_API_KEY не задан).
    """
    if not OPENAI_API_KEY:
        print("TRANSCRIBE: OPENAI_API_KEY не задан — расшифровка голосовых отключена")
        return ""
    if not url:
        return ""

    audio_bytes = await _download_audio(url)
    if not audio_bytes:
        return ""

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    # multipart/form-data: сам файл + название модели
    files = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
    form_data = {"model": TRANSCRIBE_MODEL}

    try:
        async with httpx.AsyncClient(timeout=60, transport=_transport) as http_client:
            response = await http_client.post(OPENAI_TRANSCRIBE_URL, headers=headers, files=files, data=form_data)
    except Exception as e:
        print(f"TRANSCRIBE ERROR: ошибка запроса к OpenAI: {type(e).__name__}: {e}")
        return ""

    if response.status_code != 200:
        print(f"TRANSCRIBE ERROR: OpenAI ответил HTTP {response.status_code}: {response.text[:300]}")
        return ""

    try:
        data = response.json()
    except Exception:
        print("TRANSCRIBE ERROR: OpenAI вернул не-JSON ответ")
        return ""

    text = data.get("text") if isinstance(data, dict) else ""
    return (text or "").strip()
