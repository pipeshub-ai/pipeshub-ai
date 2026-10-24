"""A Crawl4AI server the admin runs (the ``fetch-crawl4ai`` Compose profile ships one)."""

import re
from typing import Any

from app.utils.url_redaction import redact_url
from app.web.deadline import Deadline
from app.web.endpoint import Endpoint
from app.web.errors import NotConfigured, UpstreamError
from app.web.fetch.http import FetchedBody, check_page_status, check_public
from app.web.fetch.remote import ensure_final_allowed, markdown_page
from app.web.models import WebPage
from app.web.ports import HopCheck

NAME = "Crawl4AI"
# Earlier servers let anyone who can reach them run code or reach internal addresses
# (CVE-2026-56259, CVE-2026-57573, CVE-2026-109238).
MIN_VERSION = (0, 9, 4)
# The main content only, as ``/md?f=fit`` gives; ``/crawl`` is used because it reports where the page landed.
_FIT_MARKDOWN = {
    "type": "CrawlerRunConfig",
    "params": {"markdown_generator": {"type": "DefaultMarkdownGenerator", "params": {
        "content_filter": {"type": "PruningContentFilter", "params": {}},
    }}},
}
# Servers whose version passed, by cooldown key (address and token).
_VERIFIED: set[str] = set()
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _markdown(value: object) -> str:
    """The page's markdown: the filtered main content when there is any, else all of it."""
    if isinstance(value, dict):
        return str(value.get("fit_markdown") or value.get("raw_markdown") or "")
    return str(value or "")


class Crawl4AIServerFetcher:
    """Reads a page through a Crawl4AI server's ``/crawl`` endpoint, which renders JavaScript and
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
        if self.cooldown_key not in _VERIFIED:
            # shortcut: checked once per server per process; a server downgraded later is caught on restart.
            await self.probe(deadline)
            _VERIFIED.add(self.cooldown_key)
        data = await self._endpoint.call("POST", "/crawl", deadline, json={"urls": [url], "crawler_config": _FIT_MARKDOWN})
        results = data.get("results")
        result = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
        if data.get("success") is False or not result.get("success"):
            # Not the server's own reason: it can quote the URL, query string and all.
            raise UpstreamError(f"{NAME} could not read {redact_url(url)}", source=self.id)
        final = str(result.get("redirected_url") or result.get("url") or url)
        await ensure_final_allowed(url, final, allow_hop)
        # `status_code` is the first hop's (a 302 for a redirect); the page read is the last one.
        status = result.get("redirected_status_code")
        status = status if isinstance(status, int) else result.get("status_code")
        if isinstance(status, int):
            check_page_status(FetchedBody(final, status, None, b""))
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        return markdown_page(final, _markdown(result.get("markdown")), str(metadata.get("title") or ""), self.id)

    async def probe(self, deadline: Deadline) -> None:
        """Refuses a server too old to be safe, or one that answers without a token."""
        health = await self._endpoint.call("GET", "/health", deadline)
        version = _version(str(health.get("version") or ""))
        if version is None or version < MIN_VERSION:
            raise NotConfigured(
                f"{NAME} {'.'.join(map(str, MIN_VERSION))} or later is required (this server reports "
                f"{health.get('version') or 'no version'}); older versions are unsafe to expose",
                source=self.id,
                reason="server_version",
            )
