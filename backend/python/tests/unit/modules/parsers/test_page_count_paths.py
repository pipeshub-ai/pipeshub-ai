"""The page count stored for a PDF is the same number on both parsing paths.

The in-process path takes it from ``inspect_untrusted_pdf`` (``open_pdf``: pdfplumber's ``len(pdf.pages)``) of the copy
that is parsed; the parsing service takes it from ``SmartPDFParser`` (pdfplumber's ``len(pdf.pages)`` of the bytes it
parses). Every file here is generated. A PDF that pdfium has to rewrite is counted on the rewrite (the bytes that are
parsed), and the corpus asserts that the rewrite keeps the count.
"""

from __future__ import annotations

import io

import pytest
from reportlab.lib import pdfencrypt
from reportlab.pdfgen import canvas

from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers.pdf.untrusted_pdf import inspect_untrusted_pdf
from app.services.parsing.providers.smart_pdf_parser import (
    _detect_needs_ocr,
    _with_page_count,
)


def _pdf(pages: int, *, text: bool = True, user: str | None = None) -> bytes:
    buffer = io.BytesIO()
    encrypt = None if user is None else pdfencrypt.StandardEncryption(user, ownerPassword="owner", strength=128)
    pdf = canvas.Canvas(buffer, encrypt=encrypt)
    for page in range(pages):
        if text:
            pdf.setFont("Helvetica", 11)
            for row in range(40):
                pdf.drawString(40, 800 - row * 13, f"page {page} line {row} lorem ipsum dolor sit amet " * 2)
        else:
            pdf.rect(0, 0, 595, 842, fill=1)  # no text layer: the look of a scan
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _damaged(pages: int) -> bytes:
    """The cross-reference table and trailer cut off: pdfplumber cannot open it as it is."""
    data = _pdf(pages)
    return data[: data.rindex(b"xref")]


CORPUS = {
    "one text page": _pdf(1),
    "seven text pages": _pdf(7),
    "forty text pages": _pdf(40),
    "a scan with no text layer": _pdf(3, text=False),
    "damaged, rewritten by pdfium": _damaged(5),
}


@pytest.mark.parametrize("name", list(CORPUS))
async def test_the_in_process_count_equals_the_service_count_for_the_bytes_that_are_parsed(name: str) -> None:
    inspected = await inspect_untrusted_pdf(CORPUS[name], label=name)

    in_process = inspected.page_count
    # the parsing service counts the bytes it is given: the same bytes the in-process path parses
    _, service = _detect_needs_ocr(inspected.content)

    assert in_process is not None and in_process >= 1
    assert in_process == service


@pytest.mark.parametrize(("name", "pages"), [("seven text pages", 7), ("damaged, rewritten by pdfium", 5)])
async def test_the_count_is_the_files_page_count_also_when_pdfium_rewrote_it(name: str, pages: int) -> None:
    inspected = await inspect_untrusted_pdf(CORPUS[name], label=name)

    assert inspected.page_count == pages
    assert inspected.repaired is (name != "seven text pages")


async def test_an_encrypted_pdf_raises_before_any_count_is_taken() -> None:
    with pytest.raises(PasswordProtectedError):
        await inspect_untrusted_pdf(_pdf(2, user="s3cret"), label="locked")


def test_the_service_puts_the_count_it_took_in_the_result_metadata() -> None:
    class Result:
        metadata: dict | None = {"keep": 1}

    result = Result()

    assert _with_page_count(result, 12).metadata == {"keep": 1, "page_count": 12}  # type: ignore[arg-type]
    unreadable = Result()
    unreadable.metadata = None
    assert _with_page_count(unreadable, None).metadata is None  # type: ignore[arg-type]
    assert _with_page_count(Result(), 0).metadata == {"keep": 1}  # type: ignore[arg-type]
