"""Docling detects a stream's format from its content before its name, so a
record routed to Docling as a PDF, DOCX, PPTX or Markdown file can reach a
different Docling backend (JSON_DOCLING, METS-GBS, HTML, LaTeX, ...) when its
bytes look like one. The converters only accept the formats we route to them.
"""

from __future__ import annotations

import gzip
import io
import logging
import tarfile
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.exceptions import ConversionError
from docling_core.types.doc.document import DoclingDocument

from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.markdown.docling_markdown_parser import DoclingMarkdownParser
from app.modules.parsers.pdf import docling_processor
from app.modules.parsers.pptx.pptx_parser import PPTXParser

if TYPE_CHECKING:
    from collections.abc import Iterator

    from docling.datamodel.document import ConversionResult
    from docling.document_converter import DocumentConverter

ROUTED_FORMATS = {InputFormat.PDF, InputFormat.DOCX, InputFormat.PPTX, InputFormat.MD}


@pytest.fixture
def converter() -> Iterator[DocumentConverter]:
    docling_processor._get_converter.cache_clear()
    yield docling_processor._get_converter()
    docling_processor._get_converter.cache_clear()


def _mets_gzip() -> bytes:
    mets = (
        b'<?xml version="1.0"?>'
        b'<mets xmlns="http://www.loc.gov/METS/"><fileSec/></mets>'
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("book.xml")
        info.size = len(mets)
        tar.addfile(info, io.BytesIO(mets))
    return gzip.compress(buf.getvalue())


def _convert(converter: DocumentConverter, name: str, content: bytes) -> ConversionResult:
    return converter.convert(DocumentStream(name=name, stream=io.BytesIO(content)))


def test_converter_accepts_exactly_the_routed_formats(converter: DocumentConverter) -> None:
    assert set(converter.allowed_formats) == ROUTED_FORMATS
    assert set(converter.format_to_options) == ROUTED_FORMATS


def test_gzip_mets_named_as_pdf_is_rejected(converter: DocumentConverter) -> None:
    with pytest.raises(ConversionError, match="File format not allowed"):
        _convert(converter, "scan.pdf", _mets_gzip())


def test_docling_json_is_rejected(converter: DocumentConverter) -> None:
    payload = DoclingDocument(name="x").model_dump_json().encode()
    with pytest.raises(ConversionError, match="File format not allowed"):
        _convert(converter, "record.json", payload)


def test_html_is_rejected(converter: DocumentConverter) -> None:
    html = b"<!DOCTYPE html><html><body><table><tr><td>a</td></tr></table></body></html>"
    with pytest.raises(ConversionError, match="File format not allowed"):
        _convert(converter, "page.html", html)


def test_markdown_still_converts(converter: DocumentConverter) -> None:
    result = _convert(converter, "notes.md", b"# Title\n\nSome text.\n")
    assert result.status.value == "success"
    assert "Some text." in result.document.export_to_markdown()


def test_pptx_parser_only_accepts_pptx() -> None:
    assert PPTXParser().converter.allowed_formats == [InputFormat.PPTX]


def test_html_parser_only_accepts_html() -> None:
    assert DoclingHtmlParser().converter.allowed_formats == [InputFormat.HTML]


def test_markdown_parser_only_accepts_markdown() -> None:
    parser = DoclingMarkdownParser(logging.getLogger(__name__), MagicMock())
    assert parser.converter.allowed_formats == [InputFormat.MD]
