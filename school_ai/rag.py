"""
RAG: вопрос → поиск → контекст → ответ модели → проверка источников.

Принцип: модель только формулирует ответ. Номера пунктов, названия
документов и тексты источников берутся из БД, а не из текста модели.

    вопрос
      ↓ гибридный поиск (retrieval.py), TOP-5
      ↓ раскрытие структуры: дочерние пункты первого «структурного» результата
      ↓ + родительский пункт для каждого результата («...на следующих основаниях:»)
      ↓ контекст с метками [S1], [S2], ...
      ↓ модель возвращает JSON: intro + claims[{text, source: "S3"}]
      ↓ Python: выбрасывает утверждения со ссылкой на несуществующий источник
      ↓ Python: контроль полноты перечней (все подпункты списка учтены?)
    ответ + проверенные ссылки
"""

import json
import re
import time

from . import db, ollama
from .config import settings
from .retrieval import search

MAX_CONTEXT_FRAGMENTS = 40
MAX_CONTEXT_CHARS = 30000
EXPAND_DEPTH = 2

NOT_FOUND = "В загруженных документах не найдено достаточной информации для ответа на этот вопрос."

# Вопросы, ответ на которые — перечень
_ENUMERATIVE_RE = re.compile(
    r"(как\w*\s+(основани|причин|требовани|услови|случа|документ|обязанност|прав|срок|этап|способ|критери|вид)"
    r"|по\s+как\w+\s+причин|в\s+как\w+\s+случа|перечисл|перечень|список|что\s+входит)",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """
Ты — ассистент по внутренним документам школы. Отвечаешь на русском языке.

Правила:
1. Отвечай только на основании предоставленных источников. Свои знания не используй.
2. Не придумывай требования, сроки, исключения и номера пунктов.
3. Каждое утверждение ответа должно опираться ровно на один источник
   и ссылаться на него меткой вида S3 — на тот источник, где это утверждение
   сформулировано прямо, а не просто упомянуто.
4. Если вопрос предполагает перечень (основания, причины, требования, условия,
   случаи), включи КАЖДЫЙ подпункт соответствующего перечня отдельным утверждением.
5. Не используй источник, если он по смыслу не отвечает на вопрос.
6. Различай ситуации, если источники относятся к разным процедурам.
7. Если в источниках нет ответа — answer_found = false, claims пустой.
""".strip()

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer_found": {"type": "boolean"},
        "intro": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["text", "source"],
            },
        },
        "note": {"type": "string"},
    },
    "required": ["answer_found", "intro", "claims"],
}


# ---------------------------------------------------------------------------
# Контекст
# ---------------------------------------------------------------------------

def _is_structural(result: dict) -> bool:
    """Найден по названию раздела или по точному номеру пункта."""
    return any(s.startswith("title #") or s == "exact section" for s in result.get("search_sources", []))


_CTX_SQL = """
    SELECT c.id AS chunk_id, c.document_id, d.title AS document_title, d.filename,
           d.collection, c.section_id, c.section_number AS section, c.heading_path,
           c.page, c.text, c.chunk_index
    FROM document_chunks c JOIN documents d ON d.id = c.document_id
"""


def _fetch_dicts(conn, sql: str, params) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def build_context(results: list[dict]) -> tuple[list[dict], dict[int, dict]]:
    """
    Возвращает (фрагменты контекста в порядке документа, сведения о разделах).
    """

    context: dict[int, dict] = {r["chunk_id"]: {**r, "reason": "search"} for r in results}

    with db.connect() as conn:
        # 1. Раскрываем дочерние пункты ПЕРВОГО структурного результата
        #    (раздел 28 → 28.1..28.4 → 28.1.1..28.1.7)
        root = next((r for r in results if r.get("section_id") and _is_structural(r)), None)
        if root:
            root_id = root["section_id"]
            # Для подпункта (28.1) раскрываем его родителя (28): так в контекст
            # попадают и соседние пункты 28.2–28.4
            if root.get("section") and "." in root["section"]:
                parent = conn.execute(
                    "SELECT parent_id FROM document_sections WHERE id = %s", (root_id,)
                ).fetchone()
                if parent and parent[0]:
                    root_id = parent[0]
            parents = [root_id]
            for _ in range(EXPAND_DEPTH):
                if not parents:
                    break
                rows = _fetch_dicts(
                    conn,
                    _CTX_SQL + """
                    JOIN document_sections s ON s.id = c.section_id
                    WHERE s.parent_id = ANY(%s)
                    ORDER BY s.position, c.chunk_index
                    """,
                    (parents,),
                )
                parents = []
                for row in rows:
                    context.setdefault(row["chunk_id"], {**row, "reason": f"child of {root['section'] or 'root'}"})
                    parents.append(row["section_id"])
                parents = list(dict.fromkeys(parents))

        # 2. Родительский пункт для найденных подпунктов:
        #    «28.1 ЗО отклоняет заявку ... на следующих основаниях:»
        section_ids = [r["section_id"] for r in results if r.get("section_id")]
        if section_ids:
            rows = _fetch_dicts(
                conn,
                _CTX_SQL + """
                JOIN document_sections parent ON parent.id = c.section_id
                JOIN document_sections child ON child.parent_id = parent.id
                WHERE child.id = ANY(%s) AND parent.kind = 'numbered' AND c.chunk_index = (
                    SELECT MIN(c2.chunk_index) FROM document_chunks c2 WHERE c2.section_id = parent.id
                )
                """,
                (section_ids,),
            )
            for row in rows:
                context.setdefault(row["chunk_id"], {**row, "reason": "parent"})

        # 3. Сведения о разделах (для контроля полноты перечней)
        all_section_ids = list({c["section_id"] for c in context.values() if c.get("section_id")})
        sections = {}
        if all_section_ids:
            for row in _fetch_dicts(
                conn,
                "SELECT id, parent_id, section_number, kind, position FROM document_sections WHERE id = ANY(%s)",
                (all_section_ids,),
            ):
                sections[row["id"]] = row

    # Ограничиваем размер: сначала найденное поиском, потом раскрытое
    ordered = sorted(context.values(), key=lambda c: c["reason"] != "search")
    kept, total = [], 0
    for c in ordered:
        if len(kept) >= MAX_CONTEXT_FRAGMENTS or total + len(c["text"]) > MAX_CONTEXT_CHARS:
            continue
        kept.append(c)
        total += len(c["text"])

    # Для модели — в порядке чтения документа
    kept.sort(key=lambda c: (c["document_id"], c["chunk_index"]))
    for i, c in enumerate(kept, start=1):
        c["source_id"] = f"S{i}"
    return kept, sections


def cite(fragment: dict) -> str:
    """«Положение о закупках», п. 28.1.3 (стр. 12)"""
    section = fragment.get("section")
    if section and re.fullmatch(r"\d+(\.\d+)*", section):
        where = f"п. {section}"
    elif section:
        where = section
    else:
        where = (fragment.get("heading_path") or "").split(" → ")[-1] or "фрагмент"
    if fragment.get("page"):
        where += f", стр. {fragment['page']}"
    return f"«{fragment['document_title']}», {where}"


def format_context(fragments: list[dict]) -> str:
    blocks = []
    for f in fragments:
        header = [f"[{f['source_id']}] {cite(f)}"]
        if f.get("heading_path"):
            header.append(f"Расположение: {f['heading_path']}")
        blocks.append("\n".join(header) + "\n" + f["text"])
    return "\n\n---\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------

def validate_claims(raw_claims, by_source: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Оставляет только утверждения со ссылкой на реально переданный источник."""
    valid, rejected = [], []
    for claim in raw_claims if isinstance(raw_claims, list) else []:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("text", "")).strip()
        source = str(claim.get("source", "")).strip().strip("[]")
        if not text:
            continue
        if source in by_source:
            valid.append({"text": text, "source_id": source, "auto_added": False})
        else:
            rejected.append({"text": text, "source_id": source})
    return valid, rejected


def find_missing_list_items(question: str, claims: list[dict], fragments: list[dict], sections: dict) -> list[dict]:
    """
    Контроль полноты перечней.

    Если вопрос — про перечень, и ответ ссылается на пункт-список
    («28.1 ...на следующих основаниях:») или на его подпункты, то
    ВСЕ подпункты этого списка, переданные в контекст, должны быть в ответе.
    Возвращает фрагменты пропущенных подпунктов.
    """

    if not _ENUMERATIVE_RE.search(question):
        return []

    by_source = {f["source_id"]: f for f in fragments}
    by_section: dict[int, list[dict]] = {}
    for f in fragments:
        if f.get("section_id"):
            by_section.setdefault(f["section_id"], []).append(f)

    cited_sections = {by_source[c["source_id"]].get("section_id") for c in claims}
    cited_sections.discard(None)

    # Кандидаты в «списки»: сами процитированные разделы и их родители
    candidates = set()
    for sid in cited_sections:
        candidates.add(sid)
        parent = sections.get(sid, {}).get("parent_id")
        if parent:
            candidates.add(parent)

    missing = []
    for list_id in candidates:
        list_fragments = by_section.get(list_id)
        if not list_fragments:
            continue
        is_list = list_fragments[-1]["text"].rstrip().endswith(":")
        children = sorted(
            (s for s in sections.values() if s["parent_id"] == list_id),
            key=lambda s: s["position"],
        )
        if not is_list or len(children) < 2:
            continue
        for child in children:
            if child["id"] not in cited_sections and child["id"] in by_section:
                missing.append(by_section[child["id"]][0])

    return list({f["source_id"]: f for f in missing}.values())


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

def ask(question: str, collection: str | None = "documents", save: bool = True, user: str | None = None) -> dict:
    started = time.monotonic()
    timings: dict = {}
    warnings: list[str] = []

    results = search(question, collection=collection, timings=timings)
    if not results:
        return _finish(question, collection, NOT_FOUND, False, [], [], [], [], timings, warnings, started, save, user=user)

    t0 = time.monotonic()
    fragments, sections = build_context(results)
    timings["expansion"] = round(time.monotonic() - t0, 3)
    by_source = {f["source_id"]: f for f in fragments}

    user_prompt = f"""ВОПРОС:
{question}

ИСТОЧНИКИ:
{format_context(fragments)}

Верни JSON:
- answer_found: есть ли в источниках ответ на вопрос;
- intro: краткий прямой ответ на вопрос по существу (например: «Не более 5 рабочих
  дней.», «Нет, только по основаниям, указанным в документе.») или вводная фраза
  к перечню;
- claims: утверждения ответа; text — формулировка без номеров пунктов,
  source — метка источника (например "S3"), на котором утверждение основано;
- note: важное уточнение или исключение (если есть), иначе пустая строка."""

    data = ollama.chat_json(SYSTEM_PROMPT, user_prompt, ANSWER_SCHEMA)
    timings["llm"] = data.get("_meta", {}).get("llm_seconds")
    timings["prompt_tokens"] = data.get("_meta", {}).get("prompt_tokens")

    claims, rejected = validate_claims(data.get("claims"), by_source)
    if rejected:
        warnings.append(f"Отброшено утверждений без подтверждённого источника: {len(rejected)}")

    # Контроль полноты перечней
    missing = find_missing_list_items(question, claims, fragments, sections)
    if missing:
        warnings.append(
            "Модель пропустила пункты перечня, добавлены проверкой полноты: "
            + ", ".join(cite(f) for f in missing)
        )
        claims += _claims_for_missing(question, missing)
        claims.sort(key=lambda c: (by_source[c["source_id"]]["document_id"], by_source[c["source_id"]]["chunk_index"]))

    found = bool(data.get("answer_found")) and bool(claims)
    intro = str(data.get("intro") or "").strip()
    note = str(data.get("note") or "").strip()

    answer_lines = [intro] if found and intro else [NOT_FOUND if not found else ""]
    if found:
        for i, claim in enumerate(claims, start=1):
            answer_lines.append(f"{i}. {claim['text']} — {cite(by_source[claim['source_id']])}")
        if note:
            answer_lines.append(note)
    answer = "\n".join(line for line in answer_lines if line)

    return _finish(
        question, collection, answer, found, claims if found else [], fragments, results,
        rejected, timings, warnings, started, save, intro=intro, note=note, user=user,
    )


def _claims_for_missing(question: str, missing: list[dict]) -> list[dict]:
    """Просим модель кратко сформулировать пропущенные пункты; иначе — текст из БД."""
    sources = format_context(missing)
    try:
        data = ollama.chat_json(
            SYSTEM_PROMPT,
            f"ВОПРОС:\n{question}\n\nИСТОЧНИКИ:\n{sources}\n\n"
            "Для КАЖДОГО источника сформулируй одно краткое утверждение (claims), "
            "source — метка источника. answer_found = true, intro — пустая строка.",
            ANSWER_SCHEMA,
        )
        claims, _ = validate_claims(data.get("claims"), {f["source_id"]: f for f in missing})
    except Exception:
        claims = []

    covered = {c["source_id"] for c in claims}
    for f in missing:
        if f["source_id"] not in covered:
            claims.append({"text": f["text"].split("\n")[0], "source_id": f["source_id"]})
    for c in claims:
        c["auto_added"] = True
    return claims


def _finish(question, collection, answer, found, claims, fragments, results, rejected,
            timings, warnings, started, save, intro="", note="", user=None) -> dict:
    timings["total"] = round(time.monotonic() - started, 2)
    by_source = {f["source_id"]: f for f in fragments}

    def public(f: dict) -> dict:
        return {
            "document_id": f["document_id"],
            "document_title": f["document_title"],
            "filename": f["filename"],
            "section": f.get("section"),
            "heading_path": f.get("heading_path"),
            "page": f.get("page"),
            "chunk_id": f["chunk_id"],
            "citation": cite(f),
            "text": f["text"],
        }

    out_claims = [
        {"text": c["text"], "auto_added": c.get("auto_added", False), "source": public(by_source[c["source_id"]])}
        for c in claims
    ]
    sources = list({c["source"]["chunk_id"]: c["source"] for c in out_claims}.values())

    result = {
        "question": question,
        "found": found,
        "answer": answer,
        "intro": intro,
        "note": note,
        "claims": out_claims,
        "sources": sources,
        "warnings": warnings,
        "diagnostics": {
            "search_results": [
                {"citation": cite(r), "score": round(r["score"], 4), "channels": r["search_sources"]}
                for r in results
            ],
            "context_fragments": len(fragments),
            "rejected_claims": rejected,
            "timings": timings,
            "chat_model": settings.chat_model,
        },
    }

    if save:
        try:
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO rag_queries (question, collection, chat_model, embed_model, answer,
                                             result, timings, user_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        question, collection, settings.chat_model, settings.embed_model, answer,
                        json.dumps(result, ensure_ascii=False), json.dumps(timings), user,
                    ),
                )
                conn.commit()
        except Exception as exc:  # журнал не должен ломать ответ
            warnings.append(f"Не удалось записать журнал запроса: {exc}")

    return result
