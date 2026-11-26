"""Open a produced file the way a user's application would, and read its structure.

"Valid" means the file opens without error in a standard reader:
``python-docx`` for ``.docx``, ``openpyxl`` for ``.xlsx`` (both backend
dependencies), the ``csv`` module, and for ``.pptx`` the OOXML package itself
(zip, content types, presentation part, every slide part parses as XML) so
no new runtime dependency is needed. A file that does not open is invalid
with the reader's reason; nothing else about it is graded.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Literal
from xml.etree import ElementTree

FileKind = Literal["xlsx", "docx", "pptx", "csv", "md", "other"]
Cell = str | float | int | None

_DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


@dataclass(frozen=True)
class TableView:
    sheet: str
    header: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]


@dataclass(frozen=True)
class FileView:
    name: str
    kind: FileKind
    valid: bool
    reason: str | None = None
    text: str = ""
    headings: tuple[str, ...] = ()
    slides: int | None = None
    tables: tuple[TableView, ...] = field(default_factory=tuple[TableView, ...])


def kind_of(name: str) -> FileKind:
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return suffix if suffix in ("xlsx", "docx", "pptx", "csv", "md") else "other"  # type: ignore[return-value]


def open_file(name: str, content: bytes) -> FileView:
    kind = kind_of(name)
    if not content:
        return FileView(name, kind, valid=False, reason="empty file")
    try:
        if kind == "docx":
            return _docx(name, content)
        if kind == "pptx":
            return _pptx(name, content)
        if kind == "xlsx":
            return _xlsx(name, content)
        if kind == "csv":
            return _csv(name, content)
        if kind == "md":
            text = content.decode("utf-8")
            heads = tuple(
                line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")
            )
            return FileView(name, kind, valid=True, text=text, headings=heads)
        return FileView(name, kind, valid=True, reason="not a graded format")
    except Exception as exc:  # any reader error is the verdict, not a crash
        return FileView(name, kind, valid=False, reason=f"{type(exc).__name__}: {exc}"[:200])


def _docx(name: str, content: bytes) -> FileView:
    from docx import Document

    doc = Document(io.BytesIO(content))
    headings: list[str] = []
    texts: list[str] = []
    for para in doc.paragraphs:
        style = (para.style.name if para.style is not None else "") or ""
        if para.text.strip() and (style.startswith("Heading") or style == "Title"):
            headings.append(para.text.strip())
        texts.append(para.text)
    texts.extend(
        " | ".join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows
    )
    return FileView(name, "docx", valid=True, text="\n".join(texts), headings=tuple(headings))


def _pptx(name: str, content: bytes) -> FileView:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = set(zf.namelist())
        for required in ("[Content_Types].xml", "ppt/presentation.xml"):
            if required not in names:
                raise ValueError(f"missing {required}")
        ElementTree.fromstring(zf.read("ppt/presentation.xml"))
        slide_parts = sorted(
            (int(m.group(1)), n) for n in names if (m := _SLIDE.match(n))
        )
        texts: list[str] = []
        titles: list[str] = []
        for _, part in slide_parts:
            root = ElementTree.fromstring(zf.read(part))
            runs = [t.text or "" for t in root.iter(f"{_DRAWING_NS}t")]
            if runs:
                titles.append(runs[0].strip())
            texts.append("\n".join(runs))
    return FileView(
        name, "pptx", valid=True, text="\n".join(texts), headings=tuple(titles),
        slides=len(slide_parts),
    )


def _xlsx(name: str, content: bytes) -> FileView:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), data_only=False)
    tables: list[TableView] = []
    texts: list[str] = []
    for ws in wb.worksheets:
        rows = [
            tuple(_cell(v) for v in row)
            for row in ws.iter_rows(values_only=True)
            if any(v is not None and str(v).strip() for v in row)
        ]
        texts.extend(" | ".join("" if v is None else str(v) for v in row) for row in rows)
        if rows:
            header = tuple("" if v is None else str(v).strip() for v in rows[0])
            tables.append(TableView(ws.title, header, tuple(rows[1:])))
    return FileView(name, "xlsx", valid=True, text="\n".join(texts), tables=tuple(tables))


def _cell(value: object) -> Cell:
    if value is None or isinstance(value, (str, int, float)):
        return value
    return str(value)


def _csv(name: str, content: bytes) -> FileView:
    text = content.decode("utf-8-sig")
    rows = [tuple(r) for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("no rows")
    header = tuple(c.strip() for c in rows[0])
    body: list[tuple[Cell, ...]] = [tuple(_number_or_text(c) for c in r) for r in rows[1:]]
    return FileView(name, "csv", valid=True, text=text, tables=(TableView("csv", header, tuple(body)),))


def _number_or_text(raw: str) -> Cell:
    try:
        return float(raw) if any(ch in raw for ch in ".eE") else int(raw)
    except ValueError:
        return raw


__all__ = ["Cell", "FileKind", "FileView", "TableView", "kind_of", "open_file"]
