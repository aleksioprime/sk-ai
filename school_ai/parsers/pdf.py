"""
PDF (текстовый слой).

* Номера пунктов в PDF всегда набраны в тексте — их распознаёт structure.py.
* Повторяющиеся колонтитулы и номера страниц удаляются.
* Крупный шрифт короткой строки считается заголовком.
* Страницы без текста (сканы) попадают в предупреждения: такой PDF нужно
  сначала распознать (OCR), иначе система его «не увидит».
"""

import re
from collections import Counter

import pymupdf

from .base import Block, ParsedDocument, split_label

_PAGE_NUMBER_RE = re.compile(r"^(стр\.?|страница|page)?\s*\d{1,4}(\s*(из|of|/)\s*\d{1,4})?$", re.I)


def _normalize(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def parse(path) -> ParsedDocument:
    doc = pymupdf.open(str(path))
    warnings: list[str] = []

    # 1. Собираем строки с размером шрифта
    pages: list[list[tuple[str, float, float]]] = []  # (text, size, x0)
    sizes: Counter = Counter()
    empty_pages = []

    for page_no, page in enumerate(doc, start=1):
        lines = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in line["spans"]).strip()
                size = max(s["size"] for s in spans)
                lines.append((text, size, line["bbox"][0]))
                sizes[round(size)] += len(text)
            # пустая строка-разделитель между блоками PDF
            lines.append(("", 0, 0))
        if not any(t for t, _, _ in lines):
            empty_pages.append(page_no)
        pages.append(lines)

    if empty_pages:
        warnings.append(
            f"Нет текстового слоя на страницах: {_ranges(empty_pages)} "
            "(скан?) — нужен OCR, иначе текст этих страниц не попадёт в поиск."
        )

    body_size = sizes.most_common(1)[0][0] if sizes else 10

    # 2. Колонтитулы: строки, повторяющиеся на большинстве страниц
    repeat = Counter()
    for lines in pages:
        for norm in {_normalize(t) for t, _, _ in lines if t}:
            repeat[norm] += 1
    threshold = max(3, len(pages) * 0.5)
    boilerplate = {k for k, v in repeat.items() if v >= threshold}

    # 3. Склеиваем строки в абзацы
    blocks: list[Block] = []
    for page_no, lines in enumerate(pages, start=1):
        buf: list[str] = []

        def flush():
            if buf:
                text = " ".join(buf)
                text = re.sub(r"(\w)- (\w)", r"\1\2", text)  # перенос слова
                blocks.append(Block(text, page=page_no))
                buf.clear()

        for text, size, _x in lines:
            if not text:
                flush()
                continue
            if _normalize(text) in boilerplate or _PAGE_NUMBER_RE.match(text):
                continue

            is_heading = size >= body_size * 1.2 and len(text) < 120
            if is_heading:
                flush()
                label, kind, rest = split_label(text)
                if kind in ("named", "numbered"):
                    blocks.append(Block(rest, kind=kind, label=label, page=page_no))
                else:
                    level = 1 if size >= body_size * 1.5 else 2
                    blocks.append(Block(text, kind="heading", heading_level=level, page=page_no))
                continue

            # новая строка начинается с номера/маркера — новый абзац
            _, kind, _ = split_label(text)
            if kind != "text":
                flush()
            buf.append(text)
            # строка закончилась точкой/двоеточием — вероятно конец абзаца
            if re.search(r"[.:;!?]$", text) and len(text) < 60:
                flush()
        flush()

    title = (doc.metadata or {}).get("title") or None
    if title and (len(title) < 5 or title.lower().startswith("microsoft")):
        title = None

    return ParsedDocument(
        blocks=blocks,
        title=title,
        pages=len(doc),
        parser="pdf",
        warnings=warnings,
    )


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in nums + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = n
    return ", ".join(out)
