"""
Веб-приложение School AI (порт 8000).

    GET  /                       — страница: «Документы» (файловый менеджер) и «Проверка ответов»
    GET  /api/me                 — текущий пользователь
    GET  /api/files              — дерево папок и файлов со статусом индексации
    POST /api/files/upload       — загрузить файлы (admin)
    POST /api/files/mkdir        — создать папку (admin)
    POST /api/files/move         — переместить/переименовать (admin)
    POST /api/files/delete       — удалить файл или пустую папку (admin)
    GET  /api/files/download     — скачать файл
    POST /api/ask                — вопрос с диагностикой поиска
    GET  /api/health             — проверка работоспособности (без входа)
    /v1/...                      — OpenAI-совместимый API для Open WebUI (ключ API_KEY)

Вход — через учётные записи Open WebUI (см. auth.py).

В сети страница открывается через Caddy по адресу http://school-ai.local/documents/
(Caddy убирает префикс /documents), поэтому в index.html все адреса относительные.
"""

from importlib import resources

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .. import db, files, ollama
from ..config import settings
from ..rag import ask
from .auth import User, admin_user, current_user
from .openai_api import router as openai_router

app = FastAPI(title="School AI", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(openai_router)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    collection: str | None = "documents"


class PathRequest(BaseModel):
    path: str


class MoveRequest(BaseModel):
    src: str
    dst: str


def _file_errors(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except files.FileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except OSError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ошибка файловой системы: {exc.strerror or exc}")


# ---------------------------------------------------------------------------
# Страница
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # Страница сама проверит вход через /api/me и покажет ссылку на Open WebUI
    return resources.files("school_ai.web").joinpath("static/index.html").read_text()


@app.get("/api/me")
def api_me(request: Request, user: User = Depends(current_user)) -> dict:
    return {"name": user.name, "email": user.email, "role": user.role, "is_admin": user.is_admin}


# ---------------------------------------------------------------------------
# Файлы
# ---------------------------------------------------------------------------

@app.get("/api/files")
def api_files(user: User = Depends(current_user)) -> dict:
    data = files.tree()
    data["can_edit"] = user.is_admin
    data["max_upload_mb"] = files.MAX_UPLOAD_MB
    return data


@app.post("/api/files/upload")
async def api_upload(
    dir: str = Form(...),
    overwrite: bool = Form(False),
    uploads: list[UploadFile] = File(...),
    user: User = Depends(admin_user),
) -> dict:
    saved, errors = [], []
    for upload in uploads:
        try:
            saved.append(
                await run_in_threadpool(files.save_upload, dir, upload.filename, upload.file, overwrite)
            )
        except files.FileError as exc:
            errors.append(f"{upload.filename}: {exc}")
        finally:
            await upload.close()
    return {"saved": saved, "errors": errors}


@app.post("/api/files/mkdir")
def api_mkdir(body: PathRequest, user: User = Depends(admin_user)) -> dict:
    return {"path": _file_errors(files.make_dir, body.path)}


@app.post("/api/files/move")
def api_move(body: MoveRequest, user: User = Depends(admin_user)) -> dict:
    return {"path": _file_errors(files.move, body.src, body.dst)}


@app.post("/api/files/delete")
def api_delete(body: PathRequest, user: User = Depends(admin_user)) -> dict:
    _file_errors(files.delete, body.path)
    return {"ok": True}


@app.get("/api/files/download")
def api_download(path: str = Query(...), user: User = Depends(current_user)):
    target = _file_errors(files.resolve, path)
    if not target.is_file():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Это не файл")
    return FileResponse(target, filename=target.name)


# ---------------------------------------------------------------------------
# Вопросы (служебная проверка — с диагностикой поиска)
# ---------------------------------------------------------------------------

@app.post("/api/ask")
async def api_ask(request: AskRequest, user: User = Depends(current_user)) -> dict:
    collection = None if request.collection in (None, "", "all") else request.collection
    return await run_in_threadpool(ask, request.question.strip(), collection, True, user.email or user.name)


@app.get("/api/health")
def api_health() -> dict:
    result = {"ollama": False, "db": False}
    try:
        ollama.list_models()
        result["ollama"] = True
    except Exception:
        pass
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        result["db"] = True
    except Exception:
        pass
    return result
