import io
import re
import zipfile
from pathlib import PurePath

ALLOWED_EXTENSIONS = (".md", ".txt", ".docx", ".pdf")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000
MAX_PDF_PAGES = 300
MAX_TEXT_CHARS = 300_000
MAX_NAME_CHARS = 100
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class DocumentError(ValueError):
    """Непригодный документ; сообщение на русском для показа в интерфейсе."""


UNSUPPORTED = "Поддерживаются файлы md, txt, docx и pdf."
UNREADABLE = "Не удалось прочитать файл. Проверьте, что он не повреждён."


def safe_filename(name: str) -> str:
    """Оставляет только имя файла без путей и опасных символов; проверяет расширение."""
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip(" .")
    stem, dot, extension = base.rpartition(".")
    if not dot:
        stem, extension = base, ""
    extension = f".{extension.lower()}" if extension else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentError(UNSUPPORTED)
    stem = stem.strip(" .")[: MAX_NAME_CHARS - len(extension)]
    if not stem:
        raise DocumentError("У файла нет имени.")
    if stem.upper() in WINDOWS_RESERVED:
        stem = f"_{stem}"
    return stem + extension


def extract_text(filename: str, data: bytes) -> str:
    extension = PurePath(filename).suffix.lower()
    if extension in (".md", ".txt"):
        text = _decode(data)
    elif extension == ".docx":
        text = _docx_text(data)
    elif extension == ".pdf":
        text = _pdf_text(data)
    else:
        raise DocumentError(UNSUPPORTED)
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentError("Документ слишком большой.")
    return text


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentError(UNREADABLE)


def _docx_text(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES or sum(e.file_size for e in entries) > MAX_DOCX_UNCOMPRESSED:
                raise DocumentError("Документ слишком большой.")
        document = Document(io.BytesIO(data))
        lines: list[str] = []
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                lines.append(_docx_paragraph(Paragraph(child, document)))
            elif child.tag.endswith("}tbl"):
                for row in Table(child, document).rows:
                    lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(lines)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(UNREADABLE) from exc


def _docx_paragraph(paragraph) -> str:
    text = paragraph.text.strip()
    style = getattr(paragraph.style, "name", "") or ""
    if style.startswith("Heading") or style == "Title":
        level = re.search(r"(\d)$", style)
        return f"{'#' * int(level[1])} {text}" if level else f"# {text}"
    properties = paragraph._p.pPr
    in_list = style.startswith("List") or (properties is not None and properties.numPr is not None)
    if in_list and text:
        level_in_style = re.search(r"(\d)$", style)
        level = int(level_in_style[1]) - 1 if level_in_style else 0
        if properties is not None and properties.numPr is not None and properties.numPr.ilvl is not None:
            level = int(properties.numPr.ilvl.val)
        return f"{'  ' * level}- {text}"
    return text


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("PDF защищён паролем.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentError("В PDF слишком много страниц.")
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            total += len(text) + 1
            if total > MAX_TEXT_CHARS:  # не накапливаем весь документ, чтобы проверить размер в конце
                raise DocumentError("Документ слишком большой.")
            parts.append(text)
        return "\n".join(parts)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(UNREADABLE) from exc
