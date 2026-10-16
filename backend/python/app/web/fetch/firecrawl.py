from typing import Any

from app.web.deadline import Deadline
from app.web.fetch.http import FetchedBody, check_page_status, check_public
from app.web.fetch.remote import ensure_final_allowed, markdown_page
from app.web.firecrawl import NAME, firecrawl_endpoint
from app.web.models import WebPage
from app.web.ports import HopCheck


class FirecrawlFetcher:
    """Reads a page through Firecrawl, which renders JavaScript and gets past many bot checks."""

    id = "firecrawl"
    name = NAME

    def __init__(self, configuration: dict[str, Any]) -> None:
        self._endpoint = firecrawl_endpoint(configuration, self.id)
        self.cooldown_key = f"firecrawl:{self._endpoint.base_url}"

    async def fetch(self, url: str, deadline: Deadline, *, allow_hop: HopCheck | None = None) -> WebPage:
        await check_public(url)
        data = await self._endpoint.call(
            "POST", "/v2/scrape", deadline,
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True, "timeout": int(deadline.remaining() * 1000)},
        )
        page = data.get("data") if isinstance(data.get("data"), dict) else {}
        metadata = page.get("metadata") if isinstance(page.get("metadata"), dict) else {}
        final = str(metadata.get("url") or metadata.get("sourceURL") or url)
        status = metadata.get("statusCode")
        if isinstance(status, int):
            check_page_status(FetchedBody(final, status, None, b""))
        await ensure_final_allowed(url, final, allow_hop)
        return markdown_page(final, str(page.get("markdown") or ""), str(metadata.get("title") or ""), self.id)
