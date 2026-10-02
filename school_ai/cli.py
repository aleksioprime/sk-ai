"""
Командная строка: school-ai <команда>

    school-ai check                 — проверить, что всё работает
    school-ai init-db               — создать/обновить таблицы
    school-ai index [путь...]       — проиндексировать документы (по умолчанию всю папку data)
    school-ai docs                  — список проиндексированных документов
    school-ai parse <файл>          — показать, как система «видит» структуру файла (без БД)
    school-ai ask "вопрос"          — задать вопрос
    school-ai eval [файл.json]      — прогнать контрольные вопросы
    school-ai serve                 — запустить веб-интерфейс
"""

import argparse
import json
import sys
import time
from pathlib import Path

from .config import ENV_FILE, settings


def cmd_check(args) -> int:
    ok = True
    print(f"Файл настроек: {ENV_FILE}")
    print(f"Папка данных:  {settings.data_dir}")

    try:
        from . import ollama

        models = ollama.list_models()
        print(f"✓ Ollama доступна ({settings.ollama_url})")
        for model in (settings.chat_model, settings.embed_model):
            present = any(m == model or m == f"{model}:latest" for m in models)
            print(f"  {'✓' if present else '✗'} модель {model}")
            ok &= present
    except Exception as exc:
        print(f"✗ Ollama недоступна: {exc}")
        ok = False

    try:
        from . import db

        with db.connect() as conn:
            version = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
            docs, chunks = conn.execute(
                "SELECT (SELECT COUNT(*) FROM documents), (SELECT COUNT(*) FROM document_chunks)"
            ).fetchone()
        print(f"✓ PostgreSQL доступен, pgvector {version[0] if version else '—'}")
        print(f"  документов: {docs}, фрагментов: {chunks}")
    except Exception as exc:
        print(f"✗ PostgreSQL: {exc}")
        ok = False

    return 0 if ok else 1


def cmd_init_db(args) -> int:
    from . import db

    db.init_schema()
    print("Схема БД создана/обновлена.")
    return 0


def cmd_index(args) -> int:
    from . import indexer

    paths = [Path(p) for p in args.paths] or [settings.data_dir]
    catalog = indexer.load_catalog()
    files = list(indexer.iter_files(paths))
    if not files:
        print("Файлы для индексации не найдены.")
        return 1

    stats = {"indexed": 0, "skipped": 0, "empty": 0, "error": 0}
    for n, file in enumerate(files, start=1):
        print(f"[{n}/{len(files)}] {file}")
        try:
            status = indexer.index_file(file, catalog, force=args.force)
        except Exception as exc:
            status = "error"
            print(f"  ✗ ошибка: {exc}")
        if status == "skipped":
            print("  без изменений")
        stats[status] += 1

    # Удаляем из индекса то, чего больше нет на диске (только при полной индексации)
    if not args.paths and not args.no_prune:
        existing = {indexer.describe(f, catalog).rel_path for f in indexer.iter_files([settings.data_dir])}
        stats["removed"] = indexer.prune(existing)

    print(
        f"\nГотово: проиндексировано {stats['indexed']}, без изменений {stats['skipped']}, "
        f"пустых {stats['empty']}, ошибок {stats['error']}"
        + (f", удалено {stats['removed']}" if "removed" in stats else "")
    )
    return 1 if stats["error"] else 0


def cmd_docs(args) -> int:
    from . import db

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.collection, d.file_path, d.title, d.parser,
                   (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id),
                   d.indexed_at
            FROM documents d ORDER BY d.collection, d.file_path
            """
        ).fetchall()
    if not rows:
        print("Документов пока нет. Запустите: school-ai index")
    for doc_id, collection, path, title, parser, chunks, indexed in rows:
        when = indexed.strftime("%Y-%m-%d %H:%M") if indexed else "—"
        print(f"#{doc_id:<4} [{collection}] {path}\n      «{title}» — {parser}, фрагментов {chunks}, {when}")
    return 0


def cmd_parse(args) -> int:
    from .parsers import parse_file
    from .structure import build_chunks, build_sections

    parsed = parse_file(args.file)
    sections = build_sections(parsed)
    chunks = build_chunks(sections)

    print(f"Парсер: {parsed.parser}; блоков: {len(parsed.blocks)}; страниц: {parsed.pages or '—'}")
    for warning in parsed.warnings:
        print(f"⚠ {warning}")
    print(f"Разделов: {len(sections)} (с номером: {sum(1 for s in sections if s.label)}); фрагментов: {len(chunks)}\n")

    shown = 0
    for s in sections:
        if args.grep and args.grep not in (s.label or "") and args.grep.lower() not in s.heading_path().lower():
            continue
        first = s.paragraphs[0][0] if s.paragraphs else ""
        page = f" [стр. {s.page}]" if s.page else ""
        print(f"{'  ' * (s.depth - 1)}{s.label or '·'} ({s.kind}){page}: {first[:90]}")
        shown += 1
        if shown >= args.limit:
            print(f"... (показано {args.limit}; используйте --limit или --grep)")
            break
    return 0


def print_answer(result: dict, debug: bool) -> None:
    print(f"\nВОПРОС\n{result['question']}\n\nОТВЕТ\n{result['answer']}")
    if result["sources"]:
        print("\nИСТОЧНИКИ")
        for s in result["sources"]:
            print(f"- {s['citation']}")
    for w in result["warnings"]:
        print(f"\n⚠ {w}")

    d = result["diagnostics"]
    t = d["timings"]
    print(
        f"\nВремя: всего {t.get('total')} с (поиск {t.get('retrieval')} с, "
        f"модель {t.get('llm')} с); фрагментов в контексте: {d['context_fragments']}"
    )
    if debug:
        print("\nРЕЗУЛЬТАТЫ ПОИСКА")
        for i, r in enumerate(d["search_results"], start=1):
            print(f"{i}. {r['citation']} (score={r['score']}; {', '.join(r['channels'])})")


def cmd_ask(args) -> int:
    from .rag import ask

    question = " ".join(args.question)
    collection = None if args.collection == "all" else args.collection
    result = ask(question, collection=collection)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print_answer(result, args.debug)
    return 0


def cmd_eval(args) -> int:
    """
    Контрольные вопросы: для каждого проверяется, что ожидаемые пункты
    найдены поиском и процитированы в ответе.
    """

    from .rag import ask

    path = Path(args.file)
    cases = json.loads(path.read_text())
    passed = 0
    for n, case in enumerate(cases, start=1):
        started = time.monotonic()
        result = ask(case["question"], collection=case.get("collection", "documents"), save=False)
        cited = {c["source"]["section"] for c in result["claims"]}
        expected = set(case.get("expected_sections", []))
        must_not_find = case.get("expect_not_found", False)

        if must_not_find:
            ok = not result["found"]
            detail = "ответа нет — верно" if ok else f"система ответила, хотя не должна была: {sorted(cited)}"
        else:
            missing = expected - cited
            ok = result["found"] and not missing
            detail = f"процитировано: {', '.join(sorted(cited, key=str)) or '—'}"
            if missing:
                detail += f"; НЕ процитировано: {', '.join(sorted(missing))}"

        passed += ok
        print(f"{'✓' if ok else '✗'} {n}. {case['question']}")
        print(f"     {detail}; {time.monotonic() - started:.1f} с")
        if args.verbose or not ok:
            for line in result["answer"].splitlines():
                print(f"       │ {line}")

    print(f"\nИтог: {passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run(
        "school_ai.web.app:app",
        host=args.host or settings.web_host,
        port=args.port or settings.web_port,
        log_level="info",
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="school-ai", description="Локальный RAG по документам школы")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="проверить Ollama, модели и БД").set_defaults(func=cmd_check)
    sub.add_parser("init-db", help="создать/обновить таблицы").set_defaults(func=cmd_init_db)

    p = sub.add_parser("index", help="проиндексировать документы")
    p.add_argument("paths", nargs="*", help="файлы или папки (по умолчанию вся папка данных)")
    p.add_argument("--force", action="store_true", help="переиндексировать даже неизменённые файлы")
    p.add_argument("--no-prune", action="store_true", help="не удалять из индекса пропавшие файлы")
    p.set_defaults(func=cmd_index)

    sub.add_parser("docs", help="список документов в индексе").set_defaults(func=cmd_docs)

    p = sub.add_parser("parse", help="показать структуру файла без записи в БД")
    p.add_argument("file")
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--grep", help="показать только разделы, содержащие строку (например 28.1)")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("ask", help="задать вопрос")
    p.add_argument("question", nargs="+")
    p.add_argument("--collection", default="documents", help="documents | reports | all")
    p.add_argument("--debug", action="store_true", help="показать результаты поиска")
    p.add_argument("--json", action="store_true", help="вывести полный результат в JSON")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("eval", help="прогнать контрольные вопросы")
    p.add_argument("file", nargs="?", default=str(Path(__file__).resolve().parent.parent / "eval" / "questions.json"))
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("serve", help="запустить веб-интерфейс")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
