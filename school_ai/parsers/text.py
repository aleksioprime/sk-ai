"""TXT и Markdown: абзацы разделяются пустыми строками, «#» — заголовки."""

import re
from pathlib import Path

from .base import Block, ParsedDocument, split_label

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _read(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse(path) -> ParsedDocument:
    path = Path(path)
    text = _read(path).replace("\r\n", "\n")
    is_markdown = path.suffix.lower() in (".md", ".markdown")

    # Если пустых строк нет вообще — каждая строка считается абзацем
    one_per_line = "\n\n" not in text

    blocks: list[Block] = []
    buf: list[str] = []

    def flush():
        if buf:
            blocks.append(Block(" ".join(buf)))
            buf.clear()

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            flush()
            continue

        if is_markdown:
            m = _MD_HEADING_RE.match(line)
            if m:
                flush()
                heading = m.group(2).strip()
                label, kind, rest = split_label(heading)
                if kind in ("named", "numbered"):
                    blocks.append(Block(rest, kind=kind, label=label))
                else:
                    blocks.append(Block(heading, kind="heading", heading_level=len(m.group(1))))
                continue

        _, kind, _ = split_label(line)
        if kind != "text" or one_per_line:
            flush()
        buf.append(line)
        if one_per_line:
            flush()

    flush()
    return ParsedDocument(blocks=blocks, parser="markdown" if is_markdown else "text")
