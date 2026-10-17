"""Page fetchers an admin can add, by the type id stored in the org's web settings. The built-in
fetcher is always tried first and needs no settings."""

from collections.abc import Callable
from typing import Any

from app.web.fetch.crawl4ai_server import Crawl4AIServerFetcher
from app.web.fetch.firecrawl import FirecrawlFetcher
from app.web.ports import ContentFetcher

FETCHERS: dict[str, Callable[[dict[str, Any]], ContentFetcher]] = {
    "firecrawl": FirecrawlFetcher,
    "crawl4ai": Crawl4AIServerFetcher,
}
