"""
Служба автоматической индексации: `school-ai watch`.

Раз в несколько секунд сравнивает содержимое папки данных с прошлым снимком
(путь → размер и время изменения):

* новый или изменённый файл ждёт, пока перестанет меняться (загрузка
  закончилась), получает статус «в очереди» и индексируется;
* удалённый файл убирается из базы;
* изменился catalog.toml — проверяются все файлы (могли поменяться названия).

При запуске служба один раз проверяет всю папку — так подхватываются
изменения, сделанные, пока она была остановлена.

Работает от пользователя service как системный сервис ru.school-ai.indexer.
"""

import logging
import time
from pathlib import Path

from . import indexer
from .config import settings
from .parsers import is_supported

log = logging.getLogger("school_ai.watcher")

# Сколько секунд файл должен не меняться, чтобы считаться загруженным
STABLE_SECONDS = 8


def _snapshot() -> dict[str, tuple[int, float]]:
    root = settings.data_dir
    snap = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part.startswith((".", "~$")) for part in rel.parts):
            continue
        if path.is_file() and (is_supported(path) or rel.as_posix() == "catalog.toml"):
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            snap[rel.as_posix()] = (st.st_size, st.st_mtime)
    return snap


def _print(*args, end="\n", **_kwargs) -> None:
    """Адаптер: indexer пишет через print-подобную функцию, служба — в лог."""
    text = " ".join(str(a) for a in args).strip("\r")
    if text and "embeddings:" not in text:
        log.info(text)


def watch(interval: float = 5.0) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # не засорять журнал запросами к Ollama
    log.info("Слежу за %s (каждые %.0f с)", settings.data_dir, interval)

    # Полная проверка при старте
    _safe_run(None, prune=True)
    previous = _snapshot()
    pending: dict[str, float] = {}  # путь → когда последний раз менялся

    while True:
        time.sleep(interval)
        try:
            current = _snapshot()
        except Exception as exc:
            log.error("Не удалось прочитать папку данных: %s", exc)
            continue

        now = time.monotonic()
        for rel, info in current.items():
            if previous.get(rel) != info:
                if rel not in pending and rel != "catalog.toml":
                    _queue(rel)
                pending[rel] = now

        removed = set(previous) - set(current)
        previous = current

        ready = [rel for rel, changed in pending.items() if now - changed >= STABLE_SECONDS and rel in current]
        for rel in [r for r in pending if r not in current]:
            pending.pop(rel)

        if "catalog.toml" in ready:
            # поменялись названия/типы — проверяем всё
            for rel in list(pending):
                pending.pop(rel)
            _safe_run(None, prune=True)
            continue

        if ready:
            for rel in ready:
                pending.pop(rel)
            _safe_run([settings.data_dir / rel for rel in ready], prune=bool(removed))
        elif removed:
            _safe_run([], prune=True)


def _queue(rel: str) -> None:
    try:
        indexer.set_status(rel, "queued", "Ожидает индексации")
    except Exception as exc:
        log.error("Не удалось записать статус %s: %s", rel, exc)


def _safe_run(paths: list[Path] | None, prune: bool) -> None:
    try:
        if paths == []:
            # только удалить пропавшие файлы
            catalog = indexer.load_catalog()
            existing = {indexer.describe(f, catalog).rel_path for f in indexer.iter_files([settings.data_dir])}
            indexer.prune(existing, log=_print)
            return
        stats = indexer.run_index(paths, prune_missing=prune, log=_print)
        if any(stats[k] for k in ("indexed", "empty", "error", "removed")):
            log.info("Итог: %s", stats)
    except Exception:
        log.exception("Ошибка индексации")
