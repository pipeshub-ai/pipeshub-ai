"""
Parse file bytes to text content.

Supports PDF, OOXML (docx, xlsx, pptx), and plain text.
Uses UTF-8 and Latin-1 only for text decoding.
"""

import json
import zipfile
from io import BytesIO
from typing import Optional

import fitz
from openpyxl import load_workbook

from app.modules.parsers.docx.docx_parser import DocxParser
from app.modules.parsers.pptx.pptx_parser import PPTXParser


class FileContentParser:
    """Parse file bytes to text content.

    Supports PDF, OOXML (docx, xlsx, pptx), and plain text.
    Uses UTF-8 and Latin-1 only for text decoding.
    """
    def __init__(self) -> None:
        """Initialize the file content parser."""
        pass

    def _detect_format(self, data: bytes) -> str:
        """Detect binary file type by magic bytes."""
        if len(data) >= 4 and data[:4] == b"%PDF":
            return "pdf"
        if len(data) >= 2 and data[:2] == b"PK":
            return "ooxml"
        return "text"

    def _truncate_text(
        self, text: str, max_bytes: Optional[int], encoding: str = "utf-8"
    ) -> tuple[str, bool]:
        """Truncate text to max_bytes if set. Returns (text, truncated)."""
        if not max_bytes:
            return text, False
        encoded = text.encode(encoding)
        if len(encoded) <= max_bytes:
            return text, False
        return encoded[:max_bytes].decode(encoding, errors="ignore"), True

    def parse(
        self,
        raw: bytes,
        max_bytes: Optional[int] = 500_000,
    ) -> tuple[bool, str]:
        """Parse file bytes to text content.

        Args:
            raw: File content as bytes.
            max_bytes: Optional max size in bytes for extracted text (truncate beyond this).

        Returns:
            (success, json_string): On success, JSON with content, truncated, bytes_read, format.
            On failure, JSON with error key.
        """
        original_size = len(raw)
        fmt = self._detect_format(raw)

        if fmt == "pdf":
            return self._parse_pdf(raw, original_size, max_bytes)
        if fmt == "ooxml":
            return self._parse_ooxml(raw, original_size, max_bytes)
        return self._parse_plain_text(raw, original_size, max_bytes)

    def _parse_pdf(
        self, raw: bytes, original_size: int, max_bytes: Optional[int]
    ) -> tuple[bool, str]:
        try:
            doc = fitz.open(stream=raw, filetype="pdf")
            pages_text = []
            for page in doc:
                pages_text.append(page.get_text() or "")
            doc.close()
            text = "\n\n".join(pages_text)
            text, truncated = self._truncate_text(text, max_bytes)
            return True, json.dumps({
                "content": text,
                "truncated": truncated,
                "bytes_read": original_size,
                "format": "pdf",
            })
        except Exception as e:
            return False, json.dumps({"error": f"PDF extraction failed: {e}"})

    def _parse_ooxml(
        self, raw: bytes, original_size: int, max_bytes: Optional[int]
    ) -> tuple[bool, str]:
        try:
            with zipfile.ZipFile(BytesIO(raw)) as z:
                names = z.namelist()
                if any(n.startswith("word/") for n in names):
                    docx_parser = DocxParser()
                    docling_doc = docx_parser.parse(BytesIO(raw))
                    text = docling_doc.export_to_text()
                    fmt_label = "docx"
                elif "xl/workbook.xml" in names:
                    wb = load_workbook(BytesIO(raw), read_only=True, data_only=True)
                    rows = []
                    for ws in wb.worksheets:
                        rows.append(f"=== Sheet: {ws.title} ===")
                        for row in ws.iter_rows(values_only=True):
                            rows.append(
                                "\t".join(str(c) if c is not None else "" for c in row)
                            )
                    text = "\n".join(rows)
                    fmt_label = "xlsx"
                elif "ppt/presentation.xml" in names:
                    pptx_parser = PPTXParser()
                    docling_doc = pptx_parser.parse_binary(raw)
                    text = docling_doc.export_to_text()
                    fmt_label = "pptx"
                else:
                    return False, json.dumps({"error": "Unrecognized Office format"})

            text, truncated = self._truncate_text(text, max_bytes)
            return True, json.dumps({
                "content": text,
                "truncated": truncated,
                "bytes_read": original_size,
                "format": fmt_label,
            })
        except Exception as e:
            return False, json.dumps({"error": f"Office file extraction failed: {e}"})

    def _parse_plain_text(
        self, raw: bytes, original_size: int, max_bytes: Optional[int]
    ) -> tuple[bool, str]:
        if max_bytes and len(raw) > max_bytes:
            raw = raw[:max_bytes]
            truncated = True
        else:
            truncated = False
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        return True, json.dumps({
            "content": text,
            "truncated": truncated,
            "bytes_read": original_size,
            "format": "text",
        })
