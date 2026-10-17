"""Fetch a page over plain HTTP and read it as text, rendering it in a browser when it is an empty
shell its scripts fill in."""

import asyncio
import logging

from app.utils.url_redaction import redact_url
from app.web.deadline import Deadline
from app.web.errors import NoText, WebError
from app.web.fetch.browser import BrowserRenderer
from app.web.fetch.http import FetchedBody, check_page_status, fetch_public
from app.web.fetch.parsers import ParsedDocument, parse_body
from app.web.models import WebPage
from app.web.ports import HopCheck

logger = logging.getLogger(__name__)

# A served page with less text than this, and a script, may be a shell its scripts fill in (the
# web connector's SHELL_TEXT_CHARS).
SHELL_TEXT_CHARS = 1000


def _text_length(document: ParsedDocument) -> int:
    return sum(len(block.content) for block in document.blocks)


def looks_like_shell(body: FetchedBody, document: ParsedDocument | None) -> bool:
    """Whether an HTML page has too little text of its own to be the whole page."""
    is_html = "html" in (body.content_type or "").lower() or body.content[:1024].lstrip().lower().startswith((b"<!doctype html", b"<html"))
    served = _text_length(document) if document is not None else 0
    return is_html and served < SHELL_TEXT_CHARS and b"<script" in body.content.lower()


async def _parse(body: FetchedBody) -> ParsedDocument | None:
    """The body's text, or None when it has none; other failures are raised."""
    try:
        # Parsing a large PDF or page is CPU work; keep it off the event loop.
        return await asyncio.to_thread(parse_body, body.content_type, body.content, url=body.url)
    except NoText:
        return None


class BuiltinFetcher:
    """HTML, PDF, text and JSON over plain HTTP; pages built by scripts through the browser."""

    id = "builtin"
    cooldown_key = "builtin"

    def __init__(self, renderer: BrowserRenderer | None = None) -> None:
        self._renderer = renderer

    async def fetch(self, url: str, deadline: Deadline, *, allow_hop: HopCheck | None = None) -> WebPage:
        body = await fetch_public(url, deadline, allow_hop=allow_hop)
        check_page_status(body)
        document = await _parse(body)
        fetched_by = self.id

        if self._renderer is not None and looks_like_shell(body, document) and not deadline.expired:
            rendered = await self._rendered(body.url, deadline)
            if rendered is not None and _text_length(rendered[1]) > (_text_length(document) if document else 0):
                body, document = rendered
                fetched_by = self._renderer.id

        if document is None or not document.blocks:
            raise NoText(f"No readable content could be extracted from {redact_url(body.url)}", source=self.id)
        return WebPage(url=body.url, title=document.title, blocks=tuple(document.blocks), fetched_by=fetched_by)

    async def _rendered(self, url: str, deadline: Deadline) -> tuple[FetchedBody, ParsedDocument] | None:
        """The browser's copy, or None: when the browser fails, the served copy is still an answer."""
        assert self._renderer is not None  # only called when there is one
        try:
            body = await self._renderer.render(url, deadline)
            document = await _parse(body)
        except WebError as e:
            logger.info("Keeping the served copy of %s: the browser failed (%s)", redact_url(url), e.error_type)
            return None
        except Exception:
            logger.warning("Keeping the served copy of %s: the browser failed", redact_url(url), exc_info=True)
            return None
        return (body, document) if document is not None else None
