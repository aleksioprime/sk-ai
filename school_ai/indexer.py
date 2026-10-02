"""
Индексация файлов.

Папка данных (по умолчанию /opt/school-ai/data):

    data/
    ├── catalog.toml        — необязательные названия/типы документов
    ├── documents/          — нормативная база (коллекция "documents")
    │   ├── regulations/
    │   └── ...
    └── reports/            — примеры отчётов (коллекция "reports")

* Коллекция = первая папка под data/ (documents, reports, ...).
* Тип документа = путь подпапки (regulations, ib/myp, ...).
* Файл, который не менялся (тот же SHA256 и та же версия парсера),
  повторно не индексируется.
* Изменённый файл переиндексируется целиком.
"""

import hashlib
import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import db, ollama
from .config import settings
from .parsers import is_supported, parse_file
from .structure import build_chunks, build_sections

# Увеличивайте при изменении парсеров/разбиения — тогда все документы
# переиндексируются при следующем запуске `school-ai index`.
PARSER_VERSION = 2

EMBED_BATCH = 16

_TITLE_WORDS = re.compile(
    r"^(положение|порядок|правила|инструкция|регламент|политика|приказ|устав|"
    r"программа|договор|соглашение|методические|кодекс|план|памятка|руководство|"
    r"регламент|требования|стандарт)\b",
    re.IGNORECASE,
)


@dataclass
class DocumentMeta:
    rel_path: str
    collection: str
    document_type: str | None
    title: str | None
    revision: str | None


def file_hash(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def load_catalog() -> dict:
    path = settings.data_dir / "catalog.toml"
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def describe(path: Path, catalog: dict) -> DocumentMeta:
    path = path.resolve()
    try:
        rel = path.relative_to(settings.data_dir.resolve())
    except ValueError:
        raise ValueError(
            f"Файл {path} лежит вне папки данных {settings.data_dir}. "
            "Положите его в data/documents/... или data/reports/..."
        )
    parts = rel.parts
    collection = parts[0] if len(parts) > 1 else "documents"
    document_type = "/".join(parts[1:-1]) or None

    entry = catalog.get(rel.as_posix(), {})
    return DocumentMeta(
        rel_path=rel.as_posix(),
        collection=entry.get("collection", collection),
        document_type=entry.get("type", document_type),
        title=entry.get("title"),
        revision=entry.get("revision"),
    )


def guess_title(parsed, sections, path: Path) -> str:
    if parsed.title:
        return parsed.title
    # «Положение о ...», «Порядок ...» в начале документа
    for block in parsed.blocks[:15]:
        text = " ".join(block.text.split())
        if _TITLE_WORDS.match(text) and len(text) < 200:
            return text.rstrip(".")
    for section in sections:
        if section.kind == "heading" and section.title:
            return section.title
    return path.stem.replace("_", " ")


def iter_files(paths: list[Path]):
    for path in paths:
        if path.is_dir():
            for file in sorted(path.rglob("*")):
                if file.is_file() and not _ignored(file) and is_supported(file):
                    yield file
        elif path.is_file():
            yield path


def _ignored(path: Path) -> bool:
    return any(part.startswith((".", "~$")) for part in path.parts)


def embedding_text(title: str, heading_path: str, label: str | None, text: str) -> str:
    """Текст, по которому строится вектор: фрагмент + где он находится."""
    lines = [f"Документ: {title}"]
    if heading_path:
        lines.append(f"Раздел: {heading_path}")
    if label:
        lines.append(f"Пункт: {label}")
    lines.append("")
    lines.append(text)
    return "\n".join(lines)


def index_file(path: Path, catalog: dict, force: bool = False, log=print) -> str:
    """Возвращает статус: skipped | indexed."""

    meta = describe(path, catalog)
    checksum = file_hash(path)

    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, file_hash, parser_version, title FROM documents WHERE file_path = %s",
            (meta.rel_path,),
        ).fetchone()
        unchanged = row and row[1] == checksum and row[2] == PARSER_VERSION
        if unchanged and not force and (not meta.title or meta.title == row[3]):
            return "skipped"

    started = time.monotonic()
    parsed = parse_file(path)
    sections = build_sections(parsed)
    chunks = build_chunks(sections)
    title = meta.title or guess_title(parsed, sections, path)

    for warning in parsed.warnings:
        log(f"  ⚠ {warning}")
    if not chunks:
        log("  ⚠ В файле не найден текст — пропускаю.")
        return "empty"

    # Сначала считаем все embeddings (долго), потом одной транзакцией пишем в БД
    texts = [
        embedding_text(title, c.section.heading_path(), c.section.label, c.text)
        for c in chunks
    ]
    vectors = []
    for i in range(0, len(texts), EMBED_BATCH):
        vectors.extend(ollama.embed(texts[i : i + EMBED_BATCH]))
        log(f"\r  embeddings: {len(vectors)}/{len(texts)}", end="", flush=True)
    log("")

    with db.connect() as conn, conn.transaction():
        conn.execute("DELETE FROM documents WHERE file_path = %s", (meta.rel_path,))
        document_id = conn.execute(
            """
            INSERT INTO documents (filename, title, document_type, revision, file_path,
                                   file_hash, collection, parser, parser_version, pages, indexed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
            """,
            (
                path.name, title, meta.document_type, meta.revision, meta.rel_path,
                checksum, meta.collection, parsed.parser, PARSER_VERSION, parsed.pages,
            ),
        ).fetchone()[0]

        section_ids: dict[int, int] = {}
        for s in sections:
            section_ids[s.position] = conn.execute(
                """
                INSERT INTO document_sections (document_id, parent_id, section_number, title,
                                               level, position, kind, heading_path, page)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    document_id,
                    section_ids.get(s.parent.position) if s.parent else None,
                    s.label, s.title, s.depth, s.position, s.kind, s.heading_path(), s.page,
                ),
            ).fetchone()[0]

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO document_chunks (document_id, section_id, chunk_index, section_number,
                                             text, embedding, heading_path, page)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        document_id, section_ids[c.section.position], i, c.section.label,
                        c.text, vector, c.section.heading_path(), c.page,
                    )
                    for i, (c, vector) in enumerate(zip(chunks, vectors), start=1)
                ],
            )

    numbered = sum(1 for s in sections if s.label)
    log(
        f"  «{title}»: разделов {len(sections)} (с номером {numbered}), "
        f"фрагментов {len(chunks)}, {time.monotonic() - started:.0f} с"
    )
    return "indexed"


def prune(existing_files: set[str], log=print) -> int:
    """Удаляет из БД документы, файлов которых больше нет в папке данных."""

    removed = 0
    with db.connect() as conn:
        for doc_id, rel_path, title in conn.execute(
            "SELECT id, file_path, title FROM documents ORDER BY id"
        ).fetchall():
            if rel_path not in existing_files:
                conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
                log(f"Удалён из индекса (файла больше нет): {rel_path} «{title}»")
                removed += 1
        conn.commit()
    return removed
