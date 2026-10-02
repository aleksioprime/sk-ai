"""
PDF (текстовый слой).

* Номера пунктов в PDF всегда набраны в тексте — их распознаёт structure.py.
* Повторяющиеся колонтитулы и номера страниц удаляются.
* Короткая строка крупным или целиком жирным шрифтом — заголовок
  («1.» + «Общие положения» на соседних строках склеиваются в «1. Общие положения»).
* Мелкий шрифт (сноски) не принимается за номер пункта.
* Страницы без текста (сканы) попадают в предупреждения: такой PDF нужно
  сначала распознать (OCR), иначе система его «не увидит».
"""

import re
from collections import Counter

import pymupdf

from .base import Block, ParsedDocument, split_label

_LONE_LABEL_RE = re.compile(r"^(\d{1,3}(\.\d{1,3})*\.?|[IVXLC]{1,6}\.|(глава|раздел|статья|часть)\s+\S+)$", re.I)
HEADING_MAX = 250

_PAGE_NUMBER_RE = re.compile(r"^(стр\.?|страница|page)?\s*\d{1,4}(\s*(из|of|/)\s*\d{1,4})?$", re.I)


def _heading_continues(prev: str, line: str) -> bool:
    """Следующая жирная строка — продолжение того же заголовка?"""
    return bool(
        _LONE_LABEL_RE.match(prev)              # «1.» + «Общие положения»
        or line[:1].islower()                   # перенос внутри заголовка
        or not re.search(r"[.:;!?»)]$", prev)   # предыдущая строка не закончилась
    )


def _normalize(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def parse(path) -> ParsedDocument:
    doc = pymupdf.open(str(path))
    warnings: list[str] = []

    # 1. Собираем строки с размером шрифта
    pages: list[list[tuple[str, float, bool]]] = []  # (text, size, bold)
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
                bold = all(s["flags"] & 16 or "bold" in s["font"].lower() for s in spans)
                lines.append((text, size, bold))
                sizes[round(size)] += len(text)
            # пустая строка-разделитель между блоками PDF
            lines.append(("", 0, False))
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
    # колонтитул — это текст (хотя бы несколько букв), а не номер вроде «1.»
    boilerplate = {k for k, v in repeat.items() if v >= threshold and len(re.findall(r"[^\W\d_]", k)) >= 3}

    # 3. Склеиваем строки в абзацы и заголовки
    blocks: list[Block] = []
    for page_no, lines in enumerate(pages, start=1):
        buf: list[str] = []    # текущий абзац
        head: list[str] = []   # текущий заголовок (жирные/крупные строки подряд)
        head_size = 0.0

        def join(parts: list[str]) -> str:
            return re.sub(r"(\w)- (\w)", r"\1\2", " ".join(parts))  # перенос слова

        def flush():
            if buf:
                blocks.append(Block(join(buf), page=page_no))
                buf.clear()

        def flush_head():
            nonlocal head_size
            if not head:
                return
            text = join(head)
            head.clear()
            label, kind, rest = split_label(text)
            if kind in ("named", "numbered"):
                blocks.append(Block(rest, kind=kind, label=label, page=page_no))
            elif len(text) > HEADING_MAX:
                blocks.append(Block(text, page=page_no))  # целый жирный абзац — не заголовок
            else:
                level = 1 if head_size >= body_size * 1.5 else 2
                blocks.append(Block(text, kind="heading", heading_level=level, page=page_no))
            head_size = 0.0

        for text, size, bold in lines:
            if not text:
                flush()
                continue
            if _normalize(text) in boilerplate or _PAGE_NUMBER_RE.match(text):
                continue

            # Сноски мелким шрифтом: «1 Второй иностранный язык...» — не пункт 1
            if size and size < body_size * 0.9:
                flush_head()
                flush()
                blocks.append(Block(text, kind="list", page=page_no))
                continue

            # жирная строка посреди незаконченного абзаца — выделение, а не заголовок
            mid_paragraph = buf and not re.search(r"[.:;!?]$", buf[-1]) and not _LONE_LABEL_RE.match(buf[-1])
            is_heading = (size >= body_size * 1.2 or (bold and not mid_paragraph)) and len(text) < 150
            if is_heading:
                flush()
                if head and not _heading_continues(head[-1], text):
                    flush_head()
                head.append(text)
                head_size = max(head_size, size)
                continue

            flush_head()
            # одинокий номер «1.» на строке — склеиваем со следующей строкой
            if buf and _LONE_LABEL_RE.match(buf[-1]):
                buf.append(text)
                continue
            # новая строка начинается с номера/маркера — новый абзац
            _, kind, _ = split_label(text)
            if kind != "text" or _LONE_LABEL_RE.match(text):
                flush()
            buf.append(text)
            # строка закончилась точкой/двоеточием — вероятно конец абзаца
            if re.search(r"[.:;!?]$", text) and len(text) < 60 and not _LONE_LABEL_RE.match(text):
                flush()
        flush_head()
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
