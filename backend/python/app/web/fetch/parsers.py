"""Turn a fetched body into text blocks according to what the body is."""

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from email.utils import collapse_rfc2231_value

import pypdfium2 as pdfium
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes

from app.utils.html_to_blocks import TextBlock, html_to_blocks
from app.web.errors import NoText, UnsupportedContent

MAX_PDF_PAGES = 200

_HTML = "text/html"
_PDF = "application/pdf"
_TEXT = "text/plain"
_MAX_TITLE_CHARS = 300
# Headers that say nothing about the body; the bytes decide instead.
_GENERIC_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream", "application/unknown"})


@dataclass(frozen=True)
class ParsedDocument:
    title: str = ""
    blocks: list[TextBlock] = field(default_factory=list)


def _media_type(content_type: str | None) -> tuple[str, str | None]:
    message = Message()
    message["content-type"] = content_type or ""
    media = message.get_content_type() if content_type else ""
    charset = message.get_param("charset")
    # ``charset*=`` (RFC 2231) comes back as a (charset, language, value) tuple.
    return media.lower(), collapse_rfc2231_value(charset) if charset else None


def _decode(body: bytes, charset: str | None) -> str:
    if charset:
        try:
            return body.decode(charset, errors="replace")
        except (LookupError, TypeError):
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        best = from_bytes(body).best()
        return str(best) if best is not None else body.decode("utf-8", errors="replace")


def _sniff(body: bytes) -> str:
    head = body[:1024].lstrip().lower()
    if head.startswith(b"%pdf-"):
        return _PDF
    if head.startswith((b"<!doctype html", b"<html")) or b"<body" in head:
        return _HTML
    if b"\x00" not in head:
        return _TEXT
    return ""


def _parse_html(body: bytes, url: str, charset: str | None) -> ParsedDocument:
    html = _decode(body, charset)
    title_tag = BeautifulSoup(html, "html.parser").title
    title = " ".join(title_tag.get_text().split())[:_MAX_TITLE_CHARS] if title_tag else ""
    blocks = html_to_blocks(html, base_url=url, use_trafilatura=False)
    return ParsedDocument(title, [b for b in blocks if isinstance(b, TextBlock)])


def _parse_pdf(body: bytes, url: str, charset: str | None) -> ParsedDocument:
    try:
        pdf = pdfium.PdfDocument(body)
    except pdfium.PdfiumError as e:
        raise NoText(f"{url} could not be opened as a PDF") from e
    try:
        title = str(pdf.get_metadata_dict().get("Title") or "").strip()[:_MAX_TITLE_CHARS]
        page_count = len(pdf)
        blocks: list[TextBlock] = []
        for index in range(min(page_count, MAX_PDF_PAGES)):
            page = pdf[index]
            text_page = page.get_textpage()
            try:
                text = text_page.get_text_range().strip()
            finally:
                text_page.close()
                page.close()
            if text:
                blocks.append(TextBlock(content=f"[Page {index + 1}]\n{text}"))
    finally:
        pdf.close()
    if not blocks:
        raise NoText(f"{url} is a PDF with no text layer (probably scanned)")
    if page_count > MAX_PDF_PAGES:
        blocks.append(TextBlock(content=f"[Stopped after {MAX_PDF_PAGES} of {page_count} pages]"))
    return ParsedDocument(title, blocks)


def _parse_text(body: bytes, url: str, charset: str | None) -> ParsedDocument:
    text = _decode(body, charset).strip()
    return ParsedDocument(blocks=[TextBlock(content=text)] if text else [])


def _parse_json(body: bytes, url: str, charset: str | None) -> ParsedDocument:
    text = _decode(body, charset)
    with contextlib.suppress(ValueError):
        text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    return ParsedDocument(blocks=[TextBlock(content=text.strip())] if text.strip() else [])


_PARSERS: dict[str, Callable[[bytes, str, str | None], ParsedDocument]] = {
    _HTML: _parse_html,
    "application/xhtml+xml": _parse_html,
    _PDF: _parse_pdf,
    _TEXT: _parse_text,
    "text/markdown": _parse_text,
    "text/csv": _parse_text,
    "application/json": _parse_json,
}


def parse_body(content_type: str | None, body: bytes, *, url: str) -> ParsedDocument:
    """The title and text blocks of ``body``, parsed by media type, or by its first bytes when
    the header is missing or generic. Raises ``UnsupportedContent`` or ``NoText``."""
    media, charset = _media_type(content_type)
    if media in _GENERIC_TYPES:
        media = _sniff(body)
    elif media.endswith("+json"):
        media = "application/json"
    parser = _PARSERS.get(media)
    if parser is None:
        raise UnsupportedContent(f"{url} is {media or 'binary data'}, which can't be read as text")
    return parser(body, url, charset)
