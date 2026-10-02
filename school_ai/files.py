"""
Операции с файлами папки данных для страницы «Документы».

Все пути — относительные к папке данных (/opt/school-ai/data) и проходят
проверку: выйти за пределы папки нельзя, а файлы можно класть только
внутрь коллекций documents/ и reports/.

Индексацию здесь не запускаем: её делает служба `school-ai watch`,
она замечает изменения в папке за несколько секунд.
"""

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import settings
from .parsers import SUPPORTED_EXTENSIONS, is_supported

COLLECTIONS = ("documents", "reports")
MAX_UPLOAD_MB = 200


class FileError(ValueError):
    """Ошибка, понятная пользователю."""


def resolve(rel: str, must_exist: bool = True) -> Path:
    rel = (rel or "").strip().strip("/")
    root = settings.data_dir.resolve()
    path = (root / rel).resolve()
    if path != root and root not in path.parents:
        raise FileError("Недопустимый путь")
    if any(part.startswith(".") for part in path.relative_to(root).parts):
        raise FileError("Имена, начинающиеся с точки, не допускаются")
    if must_exist and not path.exists():
        raise FileError(f"Не найдено: {rel}")
    return path


def _rel(path: Path) -> str:
    rel = path.relative_to(settings.data_dir.resolve()).as_posix()
    return "" if rel == "." else rel


def _check_writable_dir(path: Path) -> None:
    rel = _rel(path)
    if not rel or rel.split("/")[0] not in COLLECTIONS:
        raise FileError("Файлы и папки можно создавать только внутри documents/ или reports/")


def _clean_name(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    if not name or name in (".", "..") or name.startswith(".") or name.startswith("~$"):
        raise FileError(f"Недопустимое имя: «{name}»")
    if any(ch in name for ch in '/:*?"<>|'):
        raise FileError(f"Имя содержит недопустимые символы: «{name}»")
    return name


def tree() -> dict:
    """Все папки и файлы со статусом индексации."""

    root = settings.data_dir.resolve()
    statuses, documents = {}, {}
    with db.connect() as conn:
        for path, st, message, warnings, updated in conn.execute(
            "SELECT file_path, status, message, warnings, updated_at FROM index_status"
        ).fetchall():
            statuses[path] = {"status": st, "message": message, "warnings": warnings, "updated_at": updated}
        for path, title, chunks in conn.execute(
            """
            SELECT d.file_path, d.title, COUNT(c.id)
            FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id
            GROUP BY d.id
            """
        ).fetchall():
            documents[path] = {"title": title, "chunks": chunks}

    dirs, files = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        base = Path(dirpath)
        rel_dir = _rel(base)
        if rel_dir:
            dirs.append(rel_dir)
        for name in sorted(filenames):
            if name.startswith((".", "~$")) or (not rel_dir and name != "catalog.toml"):
                continue
            path = base / name
            rel = _rel(path)
            st = path.stat()
            info = {
                "path": rel,
                "name": name,
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                "supported": is_supported(path),
            }
            if info["supported"]:
                info.update(statuses.get(rel) or {"status": "waiting", "message": "Ожидает проверки службой индексации"})
                info.update(documents.get(rel) or {})
            files.append(info)

    return {"dirs": dirs, "files": files, "collections": list(COLLECTIONS), "extensions": SUPPORTED_EXTENSIONS}


def save_upload(dir_rel: str, filename: str, stream, overwrite: bool = False) -> str:
    directory = resolve(dir_rel)
    if not directory.is_dir():
        raise FileError("Папка не найдена")
    _check_writable_dir(directory)

    name = _clean_name(filename)
    target = directory / name
    if not is_supported(target):
        raise FileError(f"Формат «{target.suffix}» не поддерживается. Можно: {', '.join(SUPPORTED_EXTENSIONS)}")
    if target.exists() and not overwrite:
        raise FileError(f"Файл «{name}» уже есть в этой папке")

    # Пишем во временный скрытый файл (служба индексации его не видит),
    # потом одним действием переименовываем
    tmp = directory / f".upload-{uuid.uuid4().hex}"
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    raise FileError(f"Файл больше {MAX_UPLOAD_MB} МБ")
                out.write(chunk)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return _rel(target)


def make_dir(rel: str) -> str:
    path = resolve(rel, must_exist=False)
    _check_writable_dir(path)
    _clean_name(path.name)
    if path.exists():
        raise FileError("Такая папка уже существует")
    path.mkdir(parents=True)
    return _rel(path)


def move(src_rel: str, dst_rel: str) -> str:
    src = resolve(src_rel)
    dst = resolve(dst_rel, must_exist=False)
    if _rel(src) in COLLECTIONS:
        raise FileError("Корневые папки documents и reports нельзя перемещать")
    _check_writable_dir(src)
    _check_writable_dir(dst)
    _clean_name(dst.name)
    if src.is_file() and not is_supported(dst):
        raise FileError("Нельзя менять расширение на неподдерживаемое")
    if dst.exists():
        raise FileError("По новому пути уже есть файл или папка")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dst)
    return _rel(dst)


def delete(rel: str) -> None:
    path = resolve(rel)
    if _rel(path) in ("",) + COLLECTIONS:
        raise FileError("Корневые папки удалять нельзя")
    if path.is_dir():
        if any(path.iterdir()):
            raise FileError("Папка не пуста — сначала удалите или перенесите файлы")
        path.rmdir()
    else:
        path.unlink()
