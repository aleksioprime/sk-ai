"""
OpenAI-совместимый API для Open WebUI.

Open WebUI подключается к http://127.0.0.1:8000/v1 как к «OpenAI» и видит
наши «модели»:

    school-docs     — ответы по нормативным документам (коллекция documents)
    school-reports  — поиск по примерам отчётов (коллекция reports)

За «моделью» стоит весь наш конвейер: поиск → проверка ссылок → контроль
полноты. Ответ возвращается в Markdown с источниками.

Служебные запросы Open WebUI (придумать название чата, теги, подсказки) —
это не вопросы по документам: они передаются напрямую в Ollama.

Доступ — по ключу API_KEY из .env (заголовок Authorization: Bearer ...).
"""

import json
import secrets
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import settings
from ..rag import ask

router = APIRouter(prefix="/v1")

MODELS = {
    "school-docs": {"collection": "documents", "name": "📘 Документы школы"},
    "school-reports": {"collection": "reports", "name": "📝 Примеры отчётов"},
}

# Служебные запросы Open WebUI начинаются с «### Task:»
TASK_MARKER = "### Task:"

CONDENSE_PROMPT = """Перепиши последний вопрос пользователя так, чтобы он был понятен
без предыдущей переписки (подставь, о чём идёт речь). Верни только вопрос, без пояснений.

ПЕРЕПИСКА:
{history}

ПОСЛЕДНИЙ ВОПРОС:
{question}"""


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "API_KEY не задан в .env")
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный API-ключ")


def _text(content) -> str:
    """content бывает строкой или списком частей [{type: text, text: ...}, ...]."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _ollama_chat(messages: list[dict]) -> str:
    response = httpx.post(
        f"{settings.ollama_url}/api/chat",
        json={
            "model": settings.chat_model,
            "stream": False,
            "think": False,
            "messages": messages,
            "options": {"temperature": 0.2, "num_ctx": settings.num_ctx},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def standalone_question(messages: list[dict]) -> str:
    """Уточняющий вопрос («а в каких ещё случаях?») превращаем в самостоятельный."""
    turns = [(m["role"], _text(m.get("content"))) for m in messages if m.get("role") in ("user", "assistant")]
    question = turns[-1][1].strip()
    history = turns[:-1][-6:]
    if not history:
        return question

    lines = []
    for role, text in history:
        # из ответов ассистента берём только начало — без списка источников
        text = text.split("\n\n---")[0][:800]
        lines.append(f"{'Пользователь' if role == 'user' else 'Ассистент'}: {text}")
    try:
        rewritten = _ollama_chat([
            {"role": "user", "content": CONDENSE_PROMPT.format(history="\n".join(lines), question=question)}
        ]).strip()
        return rewritten if 3 < len(rewritten) < 500 else question
    except Exception:
        return question


def to_markdown(result: dict, original_question: str) -> str:
    if not result["found"]:
        text = result["answer"]
        if result["question"] != original_question:
            text += f"\n\n*Искал: «{result['question']}»*"
        return text

    lines = []
    if result["intro"]:
        lines += [result["intro"], ""]

    for i, claim in enumerate(result["claims"], start=1):
        mark = " *(добавлено проверкой полноты)*" if claim["auto_added"] else ""
        lines.append(f"{i}. {claim['text']} — *{claim['source']['citation']}*{mark}")

    if result["note"]:
        lines += ["", result["note"]]

    for warning in result["warnings"]:
        if "добавлены проверкой полноты" not in warning:
            lines += ["", f"> ⚠️ {warning}"]

    # Тексты источников — сворачиваемый блок
    lines += ["", "---", "", "<details>", "<summary>Тексты источников</summary>", ""]
    for source in result["sources"]:
        quote = source["text"].replace("\n", "\n> ")
        lines += [f"**{source['citation']}**", ""]
        if source.get("heading_path"):
            lines += [f"*{source['heading_path']}*", ""]
        lines += [f"> {quote}", ""]
    lines.append("</details>")

    if result["question"] != original_question:
        lines += ["", f"*Вопрос понят как: «{result['question']}»*"]

    return "\n".join(lines)


def _completion(model: str, content: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _stream(model: str, content: str):
    cid = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    def chunk(delta: dict, finish=None) -> str:
        data = {
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield chunk({"role": "assistant"})
    # отдаём абзацами — интерфейс показывает текст постепенно
    for part in content.split("\n"):
        yield chunk({"content": part + "\n"})
    yield chunk({}, finish="stop")
    yield "data: [DONE]\n\n"


@router.get("/models", dependencies=[Depends(require_api_key)])
def list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {"id": mid, "object": "model", "created": 0, "owned_by": "school-ai", "name": m["name"]}
            for mid, m in MODELS.items()
        ],
    }


@router.post("/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model", "school-docs")
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))

    if model not in MODELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Модель {model} не найдена")
    if not messages:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой запрос")

    last_user = next((_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")

    if TASK_MARKER in last_user or any(TASK_MARKER in _text(m.get("content")) for m in messages if m.get("role") == "system"):
        # Служебная задача Open WebUI → напрямую в Ollama
        plain = [{"role": m["role"], "content": _text(m.get("content"))} for m in messages]
        content = await run_in_threadpool(_ollama_chat, plain)
    else:
        user = request.headers.get("x-openwebui-user-email") or request.headers.get("x-openwebui-user-name")
        question = await run_in_threadpool(standalone_question, messages)
        result = await run_in_threadpool(ask, question, MODELS[model]["collection"], True, user)
        content = to_markdown(result, last_user.strip())

    if stream:
        return StreamingResponse(_stream(model, content), media_type="text/event-stream")
    return JSONResponse(_completion(model, content))
