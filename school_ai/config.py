"""
Настройки приложения.

Все значения берутся из файла .env (по умолчанию /opt/school-ai/config/.env).
Другой файл можно указать переменной окружения SCHOOL_AI_ENV.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = os.environ.get("SCHOOL_AI_ENV", "/opt/school-ai/config/.env")

load_dotenv(ENV_FILE)


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # PostgreSQL
    db_host: str = os.environ.get("DB_HOST", "127.0.0.1")
    db_port: int = _int("DB_PORT", 5432)
    db_name: str = os.environ.get("DB_NAME", "school_ai")
    db_user: str = os.environ.get("DB_USER", "school_ai")
    db_password: str = os.environ.get("DB_PASSWORD", "")

    # Ollama
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    chat_model: str = os.environ.get("OLLAMA_CHAT_MODEL", "qwen3:14b")
    embed_model: str = os.environ.get("OLLAMA_EMBED_MODEL", "qwen3-embedding:4b")
    # HNSW в pgvector поддерживает не больше 2000 измерений,
    # поэтому просим у модели укороченный вектор.
    embed_dimensions: int = _int("EMBED_DIMENSIONS", 1024)
    # Размер контекста модели (в токенах). Значение по умолчанию в Ollama
    # слишком маленькое: длинный контекст молча обрезается.
    num_ctx: int = _int("OLLAMA_NUM_CTX", 16384)

    # Где лежат файлы: <data_dir>/documents, <data_dir>/reports
    data_dir: Path = Path(os.environ.get("DATA_DIR", "/opt/school-ai/data"))

    # Веб-интерфейс
    web_host: str = os.environ.get("WEB_HOST", "127.0.0.1")
    web_port: int = _int("WEB_PORT", 8000)
    web_user: str = os.environ.get("WEB_USER", "school")
    web_password: str = os.environ.get("WEB_PASSWORD", "")

    # Ключ для OpenAI-совместимого API (/v1), через который подключается Open WebUI
    api_key: str = os.environ.get("API_KEY", "")


settings = Settings()
