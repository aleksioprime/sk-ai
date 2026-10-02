"""
Из плоского списка блоков строит:

1. дерево разделов (глава → раздел → пункт → подпункт);
2. фрагменты (chunks) для поиска: один логический пункт = один фрагмент,
   длинные пункты и документы без структуры режутся по абзацам.

Код общий для всех форматов файлов.
"""

import re
from dataclasses import dataclass, field

from .parsers.base import Block, ParsedDocument, is_decimal_label, rank_of, split_label

# Размеры фрагментов в символах
CHUNK_TARGET = 1200
CHUNK_MAX = 1800

TITLE_MAX = 160


@dataclass
class Section:
    position: int
    kind: str  # numbered | named | heading | preamble
    label: str | None
    rank: int
    parent: "Section | None"
    page: int | None
    paragraphs: list[tuple[str, int | None]] = field(default_factory=list)
    children: list["Section"] = field(default_factory=list)
    title: str | None = None

    @property
    def depth(self) -> int:
        return 1 if self.parent is None else self.parent.depth + 1

    def short(self) -> str:
        """Короткое имя для «пути»: «28. Основания отклонения заявки», «28.1»."""
        if self.kind == "preamble":
            return "Вступительная часть"
        if self.label and self.title:
            return f"{self.label}. {_cut(self.title, 80)}"
        if self.label:
            return self.label
        return _cut(self.title or "", 80)

    def heading_path(self) -> str:
        parts = []
        node: Section | None = self
        while node is not None:
            parts.append(node.short())
            node = node.parent
        return " → ".join(reversed(parts))


@dataclass
class Chunk:
    section: Section
    text: str
    page: int | None


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class _DecimalTracker:
    """
    Проверяет правдоподобие номера, найденного в тексте (не из Word numbering).
    Например «1.5 млн рублей» в середине документа не должно стать пунктом 1.5.
    """

    def __init__(self):
        self.seen: set[tuple[int, ...]] = set()
        self.last_child: dict[tuple[int, ...], int] = {}
        self.allow_restart = True

    def plausible(self, label: str) -> bool:
        nums = tuple(int(x) for x in label.split("."))
        parent, n = nums[:-1], nums[-1]
        if parent and parent not in self.seen:
            return False
        prev = self.last_child.get(parent)
        if prev is None:
            # первый пункт верхнего уровня может быть любым (выдержка из документа),
            # первый подпункт — обычно 1
            return not parent or n <= 3
        if not parent and self.allow_restart and n <= 3:
            return True  # после новой главы нумерация может начаться заново
        return prev < n <= prev + 3

    def accept(self, label: str) -> None:
        nums = tuple(int(x) for x in label.split("."))
        if not nums[:-1]:
            if nums[-1] <= self.last_child.get((), 0):
                # нумерация началась заново — забываем старые номера
                self.seen.clear()
                self.last_child.clear()
            self.allow_restart = False
        self.seen.add(nums)
        self.last_child[nums[:-1]] = nums[-1]


def build_sections(parsed: ParsedDocument) -> list[Section]:
    sections: list[Section] = []
    stack: list[Section] = []
    preamble: Section | None = None
    tracker = _DecimalTracker()

    for block in parsed.blocks:
        text = block.text.strip()
        if not text:
            continue

        kind, label = block.kind, block.label

        # Номер, набранный прямо в тексте абзаца
        if kind == "text":
            found_label, found_kind, rest = split_label(text)
            if found_kind == "numbered" and not tracker.plausible(found_label):
                found_kind, found_label, rest = "text", None, text
            kind, label, text = found_kind, found_label, rest

        if kind in ("numbered", "named", "heading"):
            if kind == "numbered" and is_decimal_label(label):
                tracker.accept(label)
            elif kind in ("named", "heading"):
                tracker.allow_restart = True

            rank = rank_of(kind, label, block.heading_level)

            while stack and stack[-1].rank >= rank:
                stack.pop()
            # 28.1 не может быть дочерним для 27.4 — снимаем «чужие» номера
            if kind == "numbered":
                while (
                    stack
                    and stack[-1].kind == "numbered"
                    and not label.startswith(stack[-1].label + ".")
                ):
                    stack.pop()

            parent = stack[-1] if stack else None
            section = Section(
                position=len(sections),
                kind=kind,
                label=label,
                rank=rank,
                parent=parent,
                page=block.page,
            )
            if text:
                section.paragraphs.append((text, block.page))
            if kind in ("named", "heading"):
                section.title = _cut(text, 300) if text else None

            if parent:
                parent.children.append(section)
            sections.append(section)
            stack.append(section)
            continue

        # Обычный абзац, элемент списка, таблица — продолжение текущего пункта
        target = stack[-1] if stack else None
        if target is None:
            if preamble is None:
                preamble = Section(
                    position=len(sections),
                    kind="preamble",
                    label=None,
                    rank=0,
                    parent=None,
                    page=block.page,
                )
                sections.append(preamble)
            target = preamble
        target.paragraphs.append((text, block.page))

    # Заголовок пронумерованного раздела: «28. Основания отклонения заявки.»
    for s in sections:
        if s.kind != "numbered" or not s.children or len(s.paragraphs) != 1:
            continue
        first = s.paragraphs[0][0]
        if len(first) <= TITLE_MAX and not re.search(r"[:;,]\s*$", first):
            s.title = first.rstrip(".").strip()

    return sections


def _split_long(text: str) -> list[str]:
    """Режет слишком длинный абзац по предложениям."""
    if len(text) <= CHUNK_MAX:
        return [text]
    sentences = re.split(r"(?<=[.!?;])\s+", text)
    parts, buf = [], ""
    for sentence in sentences:
        while len(sentence) > CHUNK_MAX:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(sentence[:CHUNK_TARGET])
            sentence = sentence[CHUNK_TARGET:]
        if buf and len(buf) + len(sentence) + 1 > CHUNK_TARGET:
            parts.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        parts.append(buf)
    return parts


def build_chunks(sections: list[Section]) -> list[Chunk]:
    chunks: list[Chunk] = []

    for section in sections:
        if not section.paragraphs:
            continue

        buf: list[str] = []
        buf_len = 0
        buf_page = section.paragraphs[0][1]

        for paragraph, page in section.paragraphs:
            for piece in _split_long(paragraph):
                if buf and buf_len + len(piece) > CHUNK_TARGET:
                    chunks.append(Chunk(section, "\n".join(buf), buf_page))
                    buf, buf_len, buf_page = [], 0, page
                buf.append(piece)
                buf_len += len(piece) + 1

        if buf:
            chunks.append(Chunk(section, "\n".join(buf), buf_page))

    return chunks
