"""
Веб-интерфейс и HTTP API.

    GET  /                 — страница с вопросом
    POST /api/ask          — {"question": "...", "collection": "documents"}
    GET  /api/documents    — список документов
    GET  /api/health       — проверка работоспособности
    /v1/...                — OpenAI-совместимый API для Open WebUI (openai_api.py)

Основной интерфейс для пользователей — Open WebUI. Эта страница — простой
служебный интерфейс для администратора и проверки ответов.

Если в .env задан WEB_PASSWORD, страница и /api требуют логин/пароль
(WEB_USER / WEB_PASSWORD, HTTP Basic). /v1 защищён ключом API_KEY.
"""

import secrets
from importlib import resources

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .. import db, ollama
from ..config import settings
from ..rag import ask
from .openai_api import router as openai_router

security = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not settings.web_password:
        return
    ok = credentials is not None and (
        secrets.compare_digest(credentials.username, settings.web_user)
        and secrets.compare_digest(credentials.password, settings.web_password)
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )


app = FastAPI(title="School AI")
app.include_router(openai_router)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    collection: str | None = "documents"


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index() -> str:
    return resources.files("school_ai.web").joinpath("static/index.html").read_text()


@app.post("/api/ask", dependencies=[Depends(require_auth)])
async def api_ask(request: AskRequest) -> dict:
    collection = None if request.collection in (None, "", "all") else request.collection
    # Модель отвечает десятки секунд — не блокируем остальной сервер
    return await run_in_threadpool(ask, request.question.strip(), collection)


@app.get("/api/documents", dependencies=[Depends(require_auth)])
def api_documents() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.collection, d.document_type, d.title, d.filename, d.indexed_at,
                   (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunks
            FROM documents d ORDER BY d.collection, d.title
            """
        ).fetchall()
    keys = ["id", "collection", "document_type", "title", "filename", "indexed_at", "chunks"]
    return [dict(zip(keys, row)) for row in rows]


@app.get("/api/health", dependencies=[Depends(require_auth)])
def api_health() -> dict:
    result = {"ollama": False, "db": False}
    try:
        result["models"] = ollama.list_models()
        result["ollama"] = True
    except Exception as exc:
        result["ollama_error"] = str(exc)
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        result["db"] = True
    except Exception as exc:
        result["db_error"] = str(exc)
    return result
