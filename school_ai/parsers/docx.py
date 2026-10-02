"""
DOCX.

Главная сложность: в документах, выгруженных из КонсультантПлюс и подобных
систем, номера пунктов («28.1.3.», «Глава 6») не хранятся в тексте абзаца —
их рисует Word по правилам нумерации (word/numbering.xml). Поэтому номера
восстанавливаются здесь так же, как их считает Word:

* счётчики общие для всех списков, ссылающихся на один abstractNum;
* при переходе на уровень выше более глубокие уровни сбрасываются;
* учитываются start и startOverride;
* формат номера берётся из lvlText («%1.%2.», «Глава %1»).
"""

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .base import Block, ParsedDocument, is_decimal_label, split_label

W_VAL = qn("w:val")


def _to_roman(n: int) -> str:
    out = ""
    for value, sym in [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]:
        while n >= value:
            out += sym
            n -= value
    return out


def _format_number(n: int, fmt: str) -> str:
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "lowerLetter":
        return chr(ord("a") + (n - 1) % 26)
    if fmt == "upperLetter":
        return chr(ord("A") + (n - 1) % 26)
    if fmt == "russianLower":
        return "абвгдежзиклмнопрстуфхцчшщэюя"[(n - 1) % 28]
    if fmt == "russianUpper":
        return "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"[(n - 1) % 28]
    if fmt == "lowerRoman":
        return _to_roman(n).lower()
    if fmt == "upperRoman":
        return _to_roman(n)
    return str(n)


class _Numbering:
    def __init__(self, document):
        self.levels: dict[int, dict[int, dict]] = {}  # abstractId → ilvl → def
        self.num_to_abstract: dict[int, int] = {}
        self.start_overrides: dict[int, dict[int, int]] = {}
        self.counters: dict[int, list[int | None]] = {}
        self.used_nums: set[int] = set()

        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError):
            return

        for abstract in root.findall(qn("w:abstractNum")):
            aid = int(abstract.get(qn("w:abstractNumId")))
            levels = {}
            for lvl in abstract.findall(qn("w:lvl")):
                ilvl = int(lvl.get(qn("w:ilvl")))
                start = lvl.find(qn("w:start"))
                fmt = lvl.find(qn("w:numFmt"))
                text = lvl.find(qn("w:lvlText"))
                levels[ilvl] = {
                    "start": int(start.get(W_VAL)) if start is not None else 1,
                    "fmt": fmt.get(W_VAL) if fmt is not None else "decimal",
                    "text": text.get(W_VAL) if text is not None else "",
                }
            self.levels[aid] = levels

        for num in root.findall(qn("w:num")):
            nid = int(num.get(qn("w:numId")))
            ref = num.find(qn("w:abstractNumId"))
            if ref is None:
                continue
            self.num_to_abstract[nid] = int(ref.get(W_VAL))
            overrides = {}
            for ov in num.findall(qn("w:lvlOverride")):
                so = ov.find(qn("w:startOverride"))
                if so is not None:
                    overrides[int(ov.get(qn("w:ilvl")))] = int(so.get(W_VAL))
            self.start_overrides[nid] = overrides

    def next_label(self, num_id: int, ilvl: int) -> tuple[str, str] | None:
        """Возвращает (отрисованный номер, формат) и сдвигает счётчики."""

        aid = self.num_to_abstract.get(num_id)
        if aid is None or aid not in self.levels:
            return None
        levels = self.levels[aid]
        if ilvl not in levels:
            return None

        counters = self.counters.setdefault(aid, [None] * 10)

        if num_id not in self.used_nums:
            self.used_nums.add(num_id)
            for level, start in self.start_overrides.get(num_id, {}).items():
                counters[level] = start - 1
                for deeper in range(level + 1, 10):
                    counters[deeper] = None

        current = counters[ilvl]
        counters[ilvl] = levels[ilvl]["start"] if current is None else current + 1
        for deeper in range(ilvl + 1, 10):
            counters[deeper] = None

        label = levels[ilvl]["text"]
        for k in range(ilvl + 1):
            value = counters[k] if counters[k] is not None else levels.get(k, {}).get("start", 1)
            fmt = levels.get(k, {}).get("fmt", "decimal")
            label = label.replace(f"%{k + 1}", _format_number(value, fmt))

        return label.strip(), levels[ilvl]["fmt"]


def _num_pr(paragraph: Paragraph):
    """numPr абзаца — либо прямо в абзаце, либо унаследованный от стиля."""

    p_pr = paragraph._p.pPr
    if p_pr is not None and p_pr.numPr is not None:
        return p_pr.numPr

    style = paragraph.style
    while style is not None:
        s_pr = style.element.pPr
        if s_pr is not None and s_pr.numPr is not None:
            return s_pr.numPr
        style = style.base_style
    return None


def _heading_level(paragraph: Paragraph) -> int | None:
    name = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
    if name in ("title", "название", "заголовок"):
        return 1
    for prefix in ("heading", "заголовок"):
        if name.startswith(prefix):
            digits = "".join(ch for ch in name if ch.isdigit())
            return int(digits) if digits else 1
    return None


def _paragraph_block(paragraph: Paragraph, numbering: _Numbering) -> Block | None:
    text = paragraph.text.strip()
    if not text:
        return None

    num_pr = _num_pr(paragraph)
    if num_pr is not None and num_pr.numId is not None:
        num_id = int(num_pr.numId.val)
        ilvl = int(num_pr.ilvl.val) if num_pr.ilvl is not None else 0
        rendered = numbering.next_label(num_id, ilvl) if num_id else None

        if rendered:
            label, fmt = rendered
            clean = label.rstrip(".").strip()

            if fmt == "bullet":
                return Block(f"• {text}", kind="list")
            if fmt == "none" or not clean:
                pass  # нумерации фактически нет — обычный абзац
            elif is_decimal_label(clean):
                return Block(text, kind="numbered", label=clean)
            else:
                found, kind, _ = split_label(clean)
                if kind == "named" and found:
                    return Block(text, kind="named", label=found)
                # «1)», «а.», «(в)» — элемент списка внутри пункта
                return Block(f"{label} {text}", kind="list")

    level = _heading_level(paragraph)
    if level:
        found, kind, rest = split_label(text)
        if kind in ("named", "numbered"):
            return Block(rest, kind=kind, label=found)
        return Block(text, kind="heading", heading_level=level)

    return Block(text)


def _table_block(table: Table) -> Block | None:
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            value = " ".join(cell.text.split())
            # объединённые ячейки python-docx отдаёт повторно
            if value and (not cells or cells[-1] != value):
                cells.append(value)
        if cells:
            rows.append(" | ".join(cells))
    if not rows:
        return None
    return Block("\n".join(rows), kind="table")


def parse(path) -> ParsedDocument:
    document = Document(str(path))
    numbering = _Numbering(document)
    blocks: list[Block] = []

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            block = _paragraph_block(Paragraph(child, document), numbering)
        elif child.tag == qn("w:tbl"):
            block = _table_block(Table(child, document))
        else:
            continue
        if block:
            blocks.append(block)

    title = (document.core_properties.title or "").strip()
    if len(title) < 5 or title.lower().startswith("microsoft"):
        title = None

    return ParsedDocument(blocks=blocks, title=title, parser="docx")


