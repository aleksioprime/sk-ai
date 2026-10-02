"""
Гибридный поиск фрагментов.

Каналы поиска (результаты объединяются по схеме RRF):

1. vector — смысловая близость (embedding вопроса ↔ embedding фрагмента);
2. fts    — полнотекстовый поиск PostgreSQL по русской морфологии;
3. title  — совпадение с названием раздела («Основания отклонения заявки»),
            вес ×2, так как это сильный структурный сигнал;
4. term   — точные идентификаторы вида «44-ФЗ», «223-ФЗ»;
5. exact  — номер пункта из вопроса («пункт 28.2», «глава 6») — большой бонус.

Все результаты знают свой document_id и section_id, поэтому одинаковые
номера пунктов в разных документах не путаются.
"""

import re
import time
from collections import defaultdict

from . import db, ollama

VECTOR_LIMIT = 20
FTS_LIMIT = 20
TITLE_LIMIT = 10
TERM_LIMIT = 10
RESULT_LIMIT = 5
RRF_K = 60

_FIELDS = """
    c.id AS chunk_id, c.document_id, d.title AS document_title, d.filename,
    d.collection, c.section_id, c.section_number AS section, c.heading_path,
    c.page, c.text, c.chunk_index
"""

_FILTER = "(%(collection)s::text IS NULL OR d.collection = %(collection)s)"

_EXACT_PATTERNS = [
    r"(?:подпункт\w*|пункт\w*|п\.|пп\.)\s*(\d+(?:\.\d+)*)",
    r"(?:подраздел\w*|раздел\w*|разд\.)\s*(\d+(?:\.\d+)*)",
    r"(?:стать\w*|ст\.)\s*(\d+(?:\.\d+)*)",
]
_NAMED_PATTERN = r"(глав\w*|раздел\w*|стать\w*|част\w*|приложени\w*)\s+([IVXLC]+|\d+)\b"
_TERM_PATTERN = r"\b\d+-[А-ЯЁA-Z]{1,4}\b"


def extract_section_refs(query: str) -> list[str]:
    """
    «Что сказано в пункте 28.2?» → ["28.2"]
    «Что в главе 6?»             → ["Глава 6", "6"]
    «27.4»                        → ["27.4"]
    """

    refs: list[str] = []
    for pattern in _EXACT_PATTERNS:
        refs += re.findall(pattern, query, flags=re.IGNORECASE)

    for word, number in re.findall(_NAMED_PATTERN, query, flags=re.IGNORECASE):
        stem = word.lower()
        keyword = next(
            k for k in ("глава", "раздел", "статья", "часть", "приложение")
            if stem.startswith(k[:4])
        )
        refs.append(f"{keyword.capitalize()} {number.upper()}")

    refs += re.findall(r"\b(\d+(?:\.\d+)+)\b", query)
    return list(dict.fromkeys(refs))


def search_words(query: str) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", query)
    return [w for w in words if len(w) >= 4]


def _rows_to_dicts(cur) -> list[dict]:
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def search(query: str, limit: int = RESULT_LIMIT, collection: str | None = None, timings: dict | None = None) -> list[dict]:
    timings = timings if timings is not None else {}

    t0 = time.monotonic()
    embedding = ollama.embed_one(query, is_query=True)
    timings["embedding"] = round(time.monotonic() - t0, 3)

    scores: dict[int, float] = defaultdict(float)
    channels: dict[int, list[str]] = defaultdict(list)
    results: dict[int, dict] = {}

    def add(rows: list[dict], channel: str, weight: float = 1.0):
        for rank, row in enumerate(rows, start=1):
            results.setdefault(row["chunk_id"], row)
            scores[row["chunk_id"]] += weight / (RRF_K + rank)
            channels[row["chunk_id"]].append(f"{channel} #{rank}")

    params = {"collection": collection, "q": query, "emb": embedding}

    t0 = time.monotonic()
    with db.connect() as conn, conn.cursor() as cur:
        # pgvector 0.8: при фильтре по коллекции HNSW продолжает поиск,
        # пока не наберёт нужное количество строк
        cur.execute("SET hnsw.iterative_scan = relaxed_order")

        # 1. Vector
        cur.execute(
            f"""
            SELECT {_FIELDS}, 1 - (c.embedding <=> %(emb)s) AS similarity
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL AND {_FILTER}
            ORDER BY c.embedding <=> %(emb)s
            LIMIT {VECTOR_LIMIT}
            """,
            params,
        )
        add(_rows_to_dicts(cur), "vector")

        # 2. Full text: сначала все слова (AND), если пусто — любое из слов (OR)
        words = search_words(query)
        fts_sql = f"""
            SELECT {_FIELDS}
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE to_tsvector('russian', c.text) @@ {{tsq}} AND {_FILTER}
            ORDER BY ts_rank_cd(to_tsvector('russian', c.text), {{tsq}}) DESC
            LIMIT {FTS_LIMIT}
        """
        cur.execute(fts_sql.format(tsq="websearch_to_tsquery('russian', %(q)s)"), params)
        rows = _rows_to_dicts(cur)
        if not rows and words:
            cur.execute(
                fts_sql.format(tsq="to_tsquery('russian', %(or_q)s)"),
                {**params, "or_q": " | ".join(words)},
            )
            rows = _rows_to_dicts(cur)
        add(rows, "fts")

        # 3. Названия разделов (берём первый фрагмент каждого раздела).
        #    Считаем, сколько слов вопроса (после стемминга) есть в названии:
        #    «...заявку ... отклонить?» ↔ «Основания отклонения заявки» — 2 слова,
        #    сильный сигнал; «...запрос?» ↔ «Запрос цен» — 1 слово из 2, слабый.
        cur.execute("SELECT tsvector_to_array(to_tsvector('russian', %s))", (query,))
        # «отклонить» → отклон, «отклонения» → отклонен: сравниваем по префиксу основы
        lexemes = [lex for lex in (cur.fetchone()[0] or []) if len(lex) >= 4]
        if lexemes:
            cur.execute(
                f"""
                SELECT * FROM (
                    SELECT DISTINCT ON (s.id) {_FIELDS},
                        (SELECT COUNT(*) FROM unnest(%(lex)s::text[]) q
                         WHERE EXISTS (
                             SELECT 1 FROM unnest(tsvector_to_array(to_tsvector('russian', s.title))) l
                             WHERE starts_with(l, q) OR starts_with(q, l)
                         )) AS matched,
                        cardinality(tsvector_to_array(to_tsvector('russian', s.title))) AS title_words
                    FROM document_sections s
                    JOIN document_chunks c ON c.section_id = s.id
                    JOIN documents d ON d.id = c.document_id
                    WHERE s.title IS NOT NULL
                      -- заголовок, совпадающий с названием документа, ничего не уточняет
                      AND position(lower(s.title) in lower(COALESCE(d.title, ''))) = 0
                      AND to_tsvector('russian', s.title) @@ to_tsquery('simple', %(or_q)s)
                      AND {_FILTER}
                    ORDER BY s.id, c.chunk_index
                ) t
                ORDER BY matched DESC, title_words ASC
                LIMIT {TITLE_LIMIT}
                """,
                {**params, "lex": lexemes, "or_q": " | ".join("'" + lex.replace("'", "") + "':*" for lex in lexemes)},
            )
            rows = _rows_to_dicts(cur)
            strong = [r for r in rows if r["matched"] >= 2 or r["matched"] >= r["title_words"]]
            weak = [r for r in rows if r not in strong]
            add(strong, "title", weight=2.0)
            add(weak, "title~", weight=0.5)

        # 4. Точные идентификаторы: 44-ФЗ, 223-ФЗ
        terms = list(dict.fromkeys(re.findall(_TERM_PATTERN, query, flags=re.IGNORECASE)))
        if terms:
            cur.execute(
                f"""
                SELECT {_FIELDS}
                FROM document_chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.text ILIKE ANY(%(terms)s) AND {_FILTER}
                ORDER BY c.document_id, c.chunk_index
                LIMIT {TERM_LIMIT}
                """,
                {**params, "terms": [f"%{t}%" for t in terms]},
            )
            add(_rows_to_dicts(cur), "term")

        # 5. Номер пункта из вопроса
        refs = extract_section_refs(query)
        if refs:
            cur.execute(
                f"""
                SELECT DISTINCT ON (c.section_id) {_FIELDS}
                FROM document_chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.section_number = ANY(%(refs)s) AND {_FILTER}
                ORDER BY c.section_id, c.chunk_index
                LIMIT 5
                """,
                {**params, "refs": refs},
            )
            for row in _rows_to_dicts(cur):
                results.setdefault(row["chunk_id"], row)
                scores[row["chunk_id"]] += 1.0
                channels[row["chunk_id"]].append("exact section")

    timings["retrieval"] = round(time.monotonic() - t0, 3)

    ranked = sorted(results, key=lambda cid: scores[cid], reverse=True)[:limit]
    out = []
    for cid in ranked:
        row = dict(results[cid])
        for key in ("rank", "matched", "title_words"):
            row.pop(key, None)
        row["similarity"] = float(row["similarity"]) if row.get("similarity") is not None else None
        row["score"] = scores[cid]
        row["search_sources"] = channels[cid]
        out.append(row)
    return out
