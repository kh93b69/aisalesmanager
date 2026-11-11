"""
Заглушки для модулей других частей проекта, если они ещё не написаны.
Если реальный модуль импортируется — используется он; иначе создаётся пустой модуль
с нужными функциями (их всё равно подменяют в тестах через monkeypatch).
"""
import importlib
import os
import sys
import types

# Тесты никогда не ходят в настоящий Supabase: подставляем фиктивные адрес и ключ
# (load_dotenv не перезаписывает уже заданные переменные окружения).
os.environ["SUPABASE_URL"] = "http://localhost:54321"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.test-signature"
)
os.environ["SUPABASE_ANON_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def _fake_function(*args, **kwargs):
    raise RuntimeError("Заглушка: функция должна быть подменена в тесте")


def ensure_module(name: str, function_names: list[str]) -> None:
    """Импортирует модуль, а если не получилось — кладёт в sys.modules заглушку."""
    try:
        module = importlib.import_module(name)
    except ImportError:
        module = types.ModuleType(name)
        sys.modules[name] = module
        print(f"TEST: модуль {name} не найден — используется заглушка")
    for function_name in function_names:
        if not hasattr(module, function_name):
            setattr(module, function_name, _fake_function)


def ensure_all() -> None:
    """Все модули, от которых зависят pipeline/scheduler/webhooks."""
    ensure_module("app.cities", ["get_bot_cities", "detect_city"])
    ensure_module("app.rag", ["get_knowledge_for_city"])
    ensure_module("app.prompts", ["build_system_blocks", "build_state_note"])
    ensure_module("app.ai_engine", ["generate_reply"])
    ensure_module("app.messenger", ["send_to_client"])
    ensure_module("app.transcribe", ["transcribe_audio_url"])
    ensure_module("app.notifier", ["notify_hot_lead", "notify_attention", "notify_text", "notify_reminder"])
