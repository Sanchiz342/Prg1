"""Turn uploaded files into sections that remember where they came from (heading path or PDF page)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import PurePath

SUPPORTED_EXTENSIONS = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".pdf": "pdf"}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^[\s|:\-]+$")


def _table_row(line: str) -> str | None:
    """'| ZK-401 | Token expired. |' -> 'ZK-401 — Token expired.' (one sentence per row, separators dropped)."""
    if _TABLE_SEPARATOR_RE.match(line):
        return None
    cells = [c.strip() for c in line.strip().strip("|").split("|") if c.strip()]
    if not cells:
        return None
    row = " — ".join(cells)
    return row if row[-1] in ".!?" else row + "."


class LoaderError(ValueError):
    """The file cannot be turned into text (unsupported type, empty, encrypted, scanned PDF...)."""


@dataclass(frozen=True)
class Section:
    text: str
    heading: str | None = None  # "Guide › Install › Linux" for Markdown
    page: int | None = None  # 1-based page number for PDF


def detect_kind(filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    try:
        return SUPPORTED_EXTENSIONS[ext]
    except KeyError:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise LoaderError(f"Unsupported file type '{ext or filename}'. Allowed: {allowed}") from None


def load_file(filename: str, data: bytes) -> tuple[str, list[Section]]:
    kind = detect_kind(filename)
    if kind == "pdf":
        sections = load_pdf(data)
    else:
        text = _decode(data)
        sections = load_markdown(text) if kind == "markdown" else load_text(text)
    if not sections:
        raise LoaderError("The file contains no text")
    return kind, sections


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Common legacy encoding for Ukrainian text files.
        return data.decode("cp1251", errors="replace")


def load_text(text: str) -> list[Section]:
    text = text.strip()
    return [Section(text=text)] if text else []


def load_markdown(text: str) -> list[Section]:
    """Split Markdown by headings; each section carries its full heading path."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            heading = " › ".join(title for _, title in stack) or None
            sections.append(Section(text=body, heading=heading))
        buffer.clear()

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_code = not in_code
            buffer.append(line)
            continue
        if not in_code and _TABLE_ROW_RE.match(line):
            row = _table_row(line)
            if row:
                buffer.append(row)
            continue
        match = None if in_code else _HEADING_RE.match(line)
        if match:
            flush()
            level, title = len(match.group(1)), match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buffer.append(line)
    flush()
    return sections


def load_pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise LoaderError("The PDF is password-protected")
        pages = [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, start=1)]
    except PdfReadError as exc:
        raise LoaderError(f"Cannot read PDF: {exc}") from exc

    sections = [Section(text=_clean_pdf_text(t), page=i) for i, t in pages if t.strip()]
    if not sections:
        raise LoaderError("The PDF has no extractable text (is it a scan? OCR it first)")
    return sections


def _clean_pdf_text(text: str) -> str:
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # re-join words hyphenated across lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
