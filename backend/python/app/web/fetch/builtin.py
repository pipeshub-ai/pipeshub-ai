"""Fetch a page over plain HTTP and read it as text."""

import asyncio

from app.utils.url_redaction import redact_url
from app.web.deadline import Deadline
from app.web.errors import NoText
from app.web.fetch.http import check_page_status, fetch_public
from app.web.fetch.parsers import parse_body
from app.web.models import WebPage
from app.web.ports import HopCheck


class BuiltinFetcher:
    """HTML, PDF, text and JSON, without running the page's scripts."""

    id = "builtin"
    cooldown_key = "builtin"

    async def fetch(self, url: str, deadline: Deadline, *, allow_hop: HopCheck | None = None) -> WebPage:
        body = await fetch_public(url, deadline, allow_hop=allow_hop)
        check_page_status(body)
        # Parsing a large PDF or page is CPU work; keep it off the event loop.
        document = await asyncio.to_thread(parse_body, body.content_type, body.content, url=body.url)
        if not document.blocks:
            raise NoText(f"No readable content could be extracted from {redact_url(body.url)}", source=self.id)
        return WebPage(url=body.url, title=document.title, blocks=tuple(document.blocks), fetched_by=self.id)
