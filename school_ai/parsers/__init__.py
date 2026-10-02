"""
Выбор парсера по расширению файла.

Чтобы добавить новый формат: напишите функцию parse(path) -> ParsedDocument
(см. parsers/base.py) и зарегистрируйте её в PARSERS.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import docx, pdf, pptx, tables, text
from .base import ParsedDocument


SOFFICE_PATHS = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
]


def _convert_to_docx(path: Path, out_dir: Path) -> tuple[Path, str]:
    """
    DOC, RTF, ODT, HTML → DOCX.

    * LibreOffice (если установлен) сохраняет автонумерацию Word — предпочтительно.
    * Иначе встроенная в macOS утилита textutil: текст сохраняется,
      но автонумерация пунктов теряется (превращается в маркеры).
    """

    soffice = next((p for p in SOFFICE_PATHS if Path(p).exists()), None) or shutil.which("soffice")
    if soffice:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(path)],
            check=True,
            capture_output=True,
            timeout=300,
        )
        return out_dir / f"{path.stem}.docx", "libreoffice"

    if shutil.which("textutil"):
        out = out_dir / "converted.docx"
        subprocess.run(
            ["textutil", "-convert", "docx", "-output", str(out), str(path)],
            check=True,
            capture_output=True,
        )
        return out, "textutil"

    raise RuntimeError("Для этого формата нужен LibreOffice или утилита macOS textutil")


def _via_conversion(path) -> ParsedDocument:
    path = Path(path)
    with tempfile.TemporaryDirectory() as tmp:
        converted, tool = _convert_to_docx(path, Path(tmp))
        parsed = docx.parse(converted)
    parsed.parser = f"{tool}+docx ({path.suffix.lower()})"
    if tool == "textutil" and path.suffix.lower() in (".doc", ".rtf", ".odt"):
        parsed.warnings.append(
            "Файл сконвертирован через textutil: автонумерация пунктов могла потеряться. "
            "Лучше пересохранить его в Word как .docx (или установить LibreOffice)."
        )
    return parsed


PARSERS = {
    ".docx": docx.parse,
    ".docm": docx.parse,
    ".doc": _via_conversion,
    ".rtf": _via_conversion,
    ".odt": _via_conversion,
    ".html": _via_conversion,
    ".htm": _via_conversion,
    ".pdf": pdf.parse,
    ".txt": text.parse,
    ".md": text.parse,
    ".markdown": text.parse,
    ".xlsx": tables.parse_xlsx,
    ".xlsm": tables.parse_xlsx,
    ".csv": tables.parse_csv,
    ".pptx": pptx.parse,
}

SUPPORTED_EXTENSIONS = sorted(PARSERS)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in PARSERS


def parse_file(path) -> ParsedDocument:
    path = Path(path)
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(
            f"Формат {path.suffix} не поддерживается. "
            f"Поддерживаются: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    return parser(path)
