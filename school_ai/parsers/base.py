"""
Общие типы для всех парсеров.

Любой парсер (DOCX, PDF, TXT, ...) превращает файл в плоский список блоков
Block в порядке чтения. Дальше structure.py одинаково для всех форматов
строит из блоков иерархию разделов и фрагменты для поиска.
"""

import re
from dataclasses import dataclass, field


@dataclass
class Block:
    text: str
    # numbered — пункт с номером 28.1.3
    # named    — «Глава 6», «Раздел II», «Статья 5»
    # heading  — заголовок без номера (стиль «Заголовок N», "# ..." в Markdown)
    # text     — обычный абзац (номер может быть набран прямо в тексте —
    #            его попробует распознать structure.py)
    # list     — элемент маркированного/буквенного списка: «1)», «а)», «•»
    # table    — строка/строки таблицы
    kind: str = "text"
    label: str | None = None
    heading_level: int | None = None
    page: int | None = None


@dataclass
class ParsedDocument:
    blocks: list[Block]
    title: str | None = None
    pages: int | None = None
    parser: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Распознавание номеров, набранных прямо в тексте
# ---------------------------------------------------------------------------

NAMED_KEYWORDS = {
    "приложение": 5,
    "часть": 10,
    "раздел": 20,
    "глава": 30,
    "статья": 40,
}
ROMAN_RANK = 25
HEADING_RANK = 50  # + уровень заголовка
DECIMAL_RANK = 100  # + глубина номера (28 → 101, 28.1 → 102)

_NAMED_RE = re.compile(
    r"^(приложение|часть|раздел|глава|статья)\s+(?:№\s*)?"
    r"([IVXLCDM]+|\d+(?:\.\d+)*)\.?(?:\s+|$)(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_ROMAN_RE = re.compile(r"^([IVXLC]{1,6})\.\s+(\S.*)$", re.DOTALL)
_DECIMAL_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){0,5})\.?\s+([«\"(A-Za-zА-Яа-яЁё].*)$", re.DOTALL)
_DECIMAL_LABEL_RE = re.compile(r"^\d+(?:\.\d+)*$")
_LIST_RE = re.compile(r"^(?:\d{1,2}\)|[а-яёa-z]\)|\([а-яёa-z0-9]{1,2}\)|[-–—•·▪●◦]\s)")


def is_decimal_label(label: str | None) -> bool:
    return bool(label) and bool(_DECIMAL_LABEL_RE.match(label))


def named_label(keyword: str, number: str) -> str:
    return f"{keyword.capitalize()} {number}"


def classify_named(label: str) -> str | None:
    """«Глава 6» → «глава». Для прочих строк None."""
    word = label.split(" ", 1)[0].lower()
    return word if word in NAMED_KEYWORDS else None


def split_label(text: str) -> tuple[str | None, str, str]:
    """
    Пытается найти номер в начале абзаца.
    Возвращает (label, kind, остальной текст).
    """

    m = _NAMED_RE.match(text)
    if m:
        return named_label(m.group(1), m.group(2)), "named", m.group(3).strip()

    m = _ROMAN_RE.match(text)
    if m:
        return m.group(1), "named", m.group(2).strip()

    m = _DECIMAL_RE.match(text)
    if m:
        return m.group(1), "numbered", m.group(2).strip()

    if _LIST_RE.match(text):
        return None, "list", text

    return None, "text", text


def rank_of(kind: str, label: str | None, heading_level: int | None) -> int:
    """Чем меньше ранг — тем выше уровень в иерархии."""

    if kind == "numbered" and label:
        return DECIMAL_RANK + label.count(".") + 1
    if kind == "named" and label:
        keyword = classify_named(label)
        return NAMED_KEYWORDS[keyword] if keyword else ROMAN_RANK
    return HEADING_RANK + (heading_level or 1)
