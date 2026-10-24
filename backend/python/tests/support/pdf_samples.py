"""PDF bytes for tests: sound files, damaged ones, password-protected ones, and non-PDFs.

The damaged shapes are the two that make pdfminer crawl rather than fail: a
cross-reference table pointing at the wrong offsets, and a file whose end holds
no ``startxref`` (so pdfminer scans backwards through all of it).
"""
from __future__ import annotations

import io

from reportlab.lib import pdfencrypt
from reportlab.pdfgen import canvas

MIB = 1 << 20


def text_pdf(pages: int = 1) -> bytes:
    """Pages dense with text, so none of them needs OCR."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    for _ in range(pages):
        pdf.setFont("Helvetica", 11)
        for row in range(60):
            pdf.drawString(40, 800 - row * 13, "The quick brown fox jumps over the lazy dog " * 2)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def blank_pdf(pages: int = 1) -> bytes:
    """Pages with no text at all, which read as scanned."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    for _ in range(pages):
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def password_protected_pdf() -> bytes:
    """Needs a password to open at all."""
    return _encrypted(user_password="secret")


def owner_password_pdf() -> bytes:
    """Restricted by an owner password only, so it opens with none."""
    return _encrypted(user_password="")


def _encrypted(user_password: str) -> bytes:
    buf = io.BytesIO()
    encryption = pdfencrypt.StandardEncryption(user_password, ownerPassword="owner")
    pdf = canvas.Canvas(buf, encrypt=encryption)
    pdf.setFont("Helvetica", 11)
    for row in range(60):
        pdf.drawString(40, 800 - row * 13, "The quick brown fox jumps over the lazy dog " * 2)
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def broken_xref_pdf(stream_bytes: int) -> bytes:
    """One page whose cross-reference table is off by a few bytes, before a binary stream.

    pdfminer resolves every object from the wrong offset and tokenises the
    stream byte by byte, then reports no pages; pdfium rebuilds the table.
    """
    return _single_page_pdf(stream_bytes, offset_error=7)


def garbage_tail_pdf(tail_bytes: int) -> bytes:
    """A sound one-page PDF followed by *tail_bytes* of zeros, so no ``startxref`` is near the end."""
    return _single_page_pdf(0) + b"\x00" * tail_bytes


def truncated_pdf() -> bytes:
    sound = text_pdf()
    return sound[: len(sound) // 2]


# Bytes a connector may hand over under a .pdf name or PDF MIME type.
NOT_PDFS: dict[str, bytes] = {
    "empty": b"",
    "plain text": b"hello world, this is not a pdf",
    "header only": b"%PDF-1.7\n",
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
    "html": b"<html><body>not a pdf</body></html>",
}


def _single_page_pdf(stream_bytes: int, offset_error: int = 0) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % stream_bytes + b"\x00" * stream_bytes + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out) + offset_error)
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return bytes(out)
