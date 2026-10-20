"""Rendering a page in the headless browser, for sites that build their content with scripts.

The browser is the web connector's shared one (``crawl4ai_fetcher``), started on first use and
kept for the process. Its requests go through the connector's address guard, which allows the
hosts in ``WEB_CONNECTOR_ALLOWED_HOSTS``; a page's own scripts could then pull an internal host's
data into what the model reads, so rendering is off whenever that allowlist is set. The URL is
checked as public before it is opened and the page the browser lands on is checked after.
"""

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from app.connectors.sources.web.address_guard import ALLOWED_HOSTS_ENV
from app.utils.url_redaction import redact_url
from app.web.deadline import Deadline
from app.web.errors import TimedOut, UpstreamError
from app.web.fetch.http import FetchedBody, check_page_status, check_public

if TYPE_CHECKING:
    from app.connectors.sources.web.crawl4ai_fetcher import Crawl4AIFetcher

logger = logging.getLogger(__name__)

RENDER_SWITCH_ENV = "WEB_FETCH_RENDER_JS"
# Pages rendered for chat share one browser context, apart from the web connector's crawls.
_CONTEXT_KEY = "chat"
_OFF = frozenset({"0", "false", "no", "off"})


class BrowserRenderer:
    id = "browser"
    # The process's reference to the shared browser, taken on first use.
    _fetcher: "Crawl4AIFetcher | None" = None

    @staticmethod
    def available() -> bool:
        """Off with ``WEB_FETCH_RENDER_JS=off``, and whenever internal hosts are allowlisted."""
        switched_off = os.getenv(RENDER_SWITCH_ENV, "").strip().lower() in _OFF
        return not switched_off and not os.getenv(ALLOWED_HOSTS_ENV, "").strip()

    async def render(self, url: str, deadline: Deadline) -> FetchedBody:
        """The page's HTML after its scripts ran. Raises a ``WebError``."""
        await check_public(url)
        try:
            async with asyncio.timeout(deadline.remaining()):
                fetcher = await self._shared_fetcher()
                result = await fetcher.fetch(url, browser_like=True, context_key=_CONTEXT_KEY)
        except TimeoutError as e:
            raise TimedOut(f"The browser did not finish {redact_url(url)} in time", source=self.id) from e
        final = result.url or url
        if result.status_code:
            check_page_status(FetchedBody(final, result.status_code, None, b""))
        if not result.success or not (result.html or "").strip():
            raise UpstreamError(f"The browser could not read {redact_url(url)}: {result.error or 'no content'}", source=self.id)
        await check_public(final)
        return FetchedBody(final, result.status_code or 200, "text/html; charset=utf-8", result.html.encode("utf-8"))

    @classmethod
    async def _shared_fetcher(cls) -> "Crawl4AIFetcher":
        # shortcut: the browser stays up once a chat page needed it; release it on idle if memory says so.
        # Two first calls at once just take two references to the same browser.
        if cls._fetcher is None:
            from app.connectors.sources.web.crawl4ai_fetcher import get_shared_fetcher

            cls._fetcher = await get_shared_fetcher(browser_like=True)
        return cls._fetcher
