"""Обращения к локальному Ollama: embeddings и генерация ответа."""

import json

import httpx
from pgvector import Vector

from .config import settings

# qwen3-embedding лучше ищет, если к ВОПРОСУ добавить инструкцию.
# К фрагментам документов инструкция не добавляется.
QUERY_INSTRUCTION = (
    "Instruct: Given a question about internal school documents, "
    "retrieve the document passages that answer it\nQuery: "
)


def embed(texts: list[str], is_query: bool = False) -> list[Vector]:
    if is_query:
        texts = [QUERY_INSTRUCTION + t for t in texts]

    response = httpx.post(
        f"{settings.ollama_url}/api/embed",
        json={
            "model": settings.embed_model,
            "input": texts,
            "dimensions": settings.embed_dimensions,
        },
        timeout=600,
    )
    response.raise_for_status()
    return [Vector(v) for v in response.json()["embeddings"]]


def embed_one(text: str, is_query: bool = False) -> Vector:
    return embed([text], is_query=is_query)[0]


def chat_json(system: str, user: str, schema: dict) -> dict:
    """
    Просит модель вернуть JSON строго по схеме (structured outputs Ollama).
    Thinking отключён: для ответа по готовым источникам он только замедляет.
    """

    response = httpx.post(
        f"{settings.ollama_url}/api/chat",
        json={
            "model": settings.chat_model,
            "stream": False,
            "think": False,
            "format": schema,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {
                "temperature": 0.0,
                "num_ctx": settings.num_ctx,
            },
        },
        timeout=600,
    )
    response.raise_for_status()
    data = response.json()
    content = data["message"]["content"]

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {}

    parsed["_meta"] = {
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "llm_seconds": round((data.get("total_duration") or 0) / 1e9, 2),
    }
    return parsed


def list_models() -> list[str]:
    response = httpx.get(f"{settings.ollama_url}/api/tags", timeout=10)
    response.raise_for_status()
    return [m["name"] for m in response.json().get("models", [])]
