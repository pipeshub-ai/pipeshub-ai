from typing import Any

from app.web.deadline import Deadline
from app.web.errors import TimedOut
from app.web.fetch.http import FetchedBody, check_page_status, check_public
from app.web.fetch.remote import ensure_final_allowed, markdown_page
from app.web.firecrawl import NAME, firecrawl_endpoint
from app.web.models import WebPage
from app.web.ports import HopCheck

_MIN_SERVER_TIMEOUT_MS = 1000


class FirecrawlFetcher:
    """Reads a page through Firecrawl, which renders JavaScript and gets past many bot checks."""

    id = "firecrawl"
    name = NAME

    def __init__(self, configuration: dict[str, Any]) -> None:
        self._endpoint = firecrawl_endpoint(configuration, self.id)
        self.cooldown_key = self._endpoint.cooldown_key("firecrawl-read")

    async def fetch(self, url: str, deadline: Deadline, *, allow_hop: HopCheck | None = None) -> WebPage:
        await check_public(url)
        # Firecrawl gives up a second before we stop waiting, so its own error reaches us.
        server_timeout_ms = int((deadline.remaining() - 1) * 1000)
        if server_timeout_ms < _MIN_SERVER_TIMEOUT_MS:
            raise TimedOut(f"Not enough time left to read {url} through {NAME}", source=self.id)
        data = await self._endpoint.call(
            "POST", "/v2/scrape", deadline,
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True, "timeout": server_timeout_ms},
        )
        page = data.get("data") if isinstance(data.get("data"), dict) else {}
        metadata = page.get("metadata") if isinstance(page.get("metadata"), dict) else {}
        final = str(metadata.get("url") or metadata.get("sourceURL") or url)
        await ensure_final_allowed(url, final, allow_hop)
        status = metadata.get("statusCode")
        if isinstance(status, int):
            check_page_status(FetchedBody(final, status, None, b""))
        return markdown_page(final, str(page.get("markdown") or ""), str(metadata.get("title") or ""), self.id)
