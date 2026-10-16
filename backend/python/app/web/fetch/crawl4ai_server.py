"""A Crawl4AI server the admin runs (the ``fetch-crawl4ai`` Compose profile ships one)."""

import re
from typing import Any

from app.web.deadline import Deadline
from app.web.endpoint import Endpoint
from app.web.errors import NotConfigured, UpstreamError
from app.web.fetch.http import check_public
from app.web.fetch.remote import markdown_page
from app.web.models import WebPage
from app.web.ports import HopCheck

NAME = "Crawl4AI"
# Earlier servers let anyone who can reach them run code (CVE-2026-56259).
MIN_VERSION = (0, 8, 8)
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


class Crawl4AIServerFetcher:
    """Reads a page through a Crawl4AI server's ``/md`` endpoint, which renders JavaScript and
    keeps the main content."""

    id = "crawl4ai"
    name = NAME

    def __init__(self, configuration: dict[str, Any]) -> None:
        self._endpoint = Endpoint(
            str(configuration.get("baseUrl") or ""), str(configuration.get("apiKey") or ""), source=self.id, name=NAME,
        )
        self.cooldown_key = self._endpoint.cooldown_key("crawl4ai")

    async def fetch(self, url: str, deadline: Deadline, *, allow_hop: HopCheck | None = None) -> WebPage:
        await check_public(url)
        data = await self._endpoint.call("POST", "/md", deadline, json={"url": url, "f": "fit"})
        if data.get("success") is False:
            raise UpstreamError(f"{NAME} could not read {url}: {data.get('error') or 'no reason given'}", source=self.id)
        return markdown_page(url, str(data.get("markdown") or ""), "", self.id)

    async def probe(self, deadline: Deadline) -> None:
        """Refuses a server too old to be safe, or one that answers without a token."""
        health = await self._endpoint.call("GET", "/health", deadline)
        version = _version(str(health.get("version") or ""))
        if version is None or version < MIN_VERSION:
            raise NotConfigured(
                f"{NAME} {'.'.join(map(str, MIN_VERSION))} or later is required (this server reports "
                f"{health.get('version') or 'no version'}); older versions are unsafe to expose",
                source=self.id,
            )
