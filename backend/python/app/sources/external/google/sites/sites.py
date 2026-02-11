"""
Google Sites datasource for URL-based crawling of published sites.

This datasource encapsulates the HTTP crawl, URL normalization, link
extraction, and HTML cleaning logic used by the Google Sites connector.
The connector is responsible for orchestration and mapping to Record/RecordGroup
entities, while this module focuses on fetching and parsing content.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from logging import Logger
from typing import List, Optional, Set, Tuple
from urllib.parse import ParseResult, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, Comment

# URL-based crawl limits and HTTP configuration (kept here close to HTTP logic)
MAX_CRAWL_PAGES = 500
CRAWL_DELAY_SEC = 0.5

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Logging helpers / HTTP status thresholds
HTTP_ERROR_THRESHOLD = 400
HTTP_SUCCESS_MIN = 200
HTTP_SUCCESS_MAX = 399
LOG_URL_PREVIEW_LEN = 80
LOG_URL_SHORT_PREVIEW_LEN = 70


def normalize_published_site_url(url: Optional[str]) -> str:
    """
    Normalize and validate published site URL. Raises ValueError if invalid.

    This mirrors the _normalize_published_site_url helper previously defined
    in the connector, but is now reusable from both connector and tests.
    """
    if not url or not isinstance(url, str):
        raise ValueError("Published site URL is required")
    url = url.strip()
    if not url:
        raise ValueError("Published site URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Published site URL must use http or https")
    if not parsed.netloc:
        raise ValueError("Published site URL must have a valid host")
    # Prefer https
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = (parsed.path or "/").rstrip("/") or "/"
    normalized = f"{base}{path}"
    if parsed.query:
        normalized = f"{normalized}?{parsed.query}"
    return normalized


def _normalize_crawl_url(url: str) -> str:
    """Normalize URL for crawl deduplication (strip fragment, trailing slash)."""
    url = url.rstrip("/") or "/"
    if "#" in url:
        url = url.split("#")[0]
    return url


def _same_origin(page_url: str, base_parsed: ParseResult) -> bool:
    """Return True if page_url has same scheme and netloc as base."""
    try:
        p = urlparse(page_url)
        return p.scheme == base_parsed.scheme and p.netloc == base_parsed.netloc
    except Exception:
        return False


def _extract_same_origin_links(
    html: str,
    current_url: str,
    base_parsed: ParseResult,
) -> List[str]:
    """Extract same-origin links from HTML."""
    links_set: Set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        if href.startswith("mailto:") or href.startswith("tel:"):
            continue
        if href.startswith("http://") or href.startswith("https://"):
            absolute = href
        else:
            absolute = urljoin(current_url, href)
        if not _same_origin(absolute, base_parsed):
            continue
        normalized = _normalize_crawl_url(absolute)
        if normalized not in links_set:
            links_set.add(normalized)
    return list(links_set)


def _clean_html_for_indexing(html: str) -> str:
    """Parse with BeautifulSoup and extract clean text content for indexing."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(
        ["script", "style", "noscript", "iframe", "svg", "nav", "footer", "header", "aside"]
    ):
        tag.decompose()

    # Remove comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Extract title
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)

    # Get main content area
    main = soup.find("main") or soup.find("article") or soup.find("body")
    content_element = main if main else soup

    # Extract clean text using BeautifulSoup's get_text
    text_content = content_element.get_text(separator="\n", strip=True)

    # Prepend title if available
    if title:
        text_content = f"{title}\n\n{text_content}"

    # Clean up whitespace
    lines = [line.strip() for line in text_content.split("\n") if line.strip()]
    return "\n".join(lines)


@dataclass
class GoogleSitesPage:
    """Simple representation of a discovered Google Sites page."""

    url: str
    final_url: str
    title: str


class GoogleSitesDataSource:
    """
    Datasource responsible for crawling published Google Sites over HTTP.

    This class is intentionally focused on HTTP + HTML behavior and does not
    know anything about connector entities (RecordGroup, FileRecord, etc.).
    """

    def __init__(self, logger: Logger) -> None:
        self.logger = logger

    async def _fetch_page(
        self,
        url: str,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Fetch URL; return (html, title, final_url).
        On failure return (None, None, None).
        """
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(
                    url,
                    headers=HTTP_HEADERS,
                    allow_redirects=True,
                ) as response:
                    status = response.status
                    if status >= HTTP_ERROR_THRESHOLD:
                        self.logger.warning(
                            "[Google Sites] CRAWL:   HTTP %s for %s",
                            status,
                            url[:LOG_URL_PREVIEW_LEN],
                        )
                        return None, None, None

                    content_type = (response.headers.get("Content-Type") or "").lower()
                    if "text/html" not in content_type and "application/xhtml" not in content_type:
                        return None, None, None

                    html = await response.text()
                    final_url = str(response.url)
                    soup = BeautifulSoup(html, "html.parser")
                    title_tag = soup.find("title")
                    title = title_tag.get_text(strip=True) if title_tag else ""
                    return html, title, final_url
        except asyncio.TimeoutError:
            self.logger.warning(
                "[Google Sites] CRAWL:   Timeout %s",
                url[:LOG_URL_PREVIEW_LEN],
            )
            return None, None, None
        except Exception as e:
            self.logger.warning(
                "[Google Sites] CRAWL:   Error %s: %s",
                url[:LOG_URL_PREVIEW_LEN],
                e,
            )
            return None, None, None

    async def crawl_site(self, start_url: str) -> List[GoogleSitesPage]:
        """
        Perform a BFS crawl starting from start_url and return discovered pages.
        """
        base_parsed = urlparse(start_url)
        visited: Set[str] = set()
        to_visit: List[str] = [_normalize_crawl_url(start_url)]
        pages_collected: List[GoogleSitesPage] = []
        collected_pages_set: Set[str] = set()

        self.logger.info(
            "[Google Sites] CRAWL: Starting crawl from %s",
            start_url[:LOG_URL_PREVIEW_LEN]
            + ("..." if len(start_url) > LOG_URL_PREVIEW_LEN else ""),
        )

        while to_visit and len(visited) < MAX_CRAWL_PAGES:
            url = to_visit.pop(0)
            url_norm = _normalize_crawl_url(url)
            if url_norm in visited:
                continue
            visited.add(url_norm)
            self.logger.info(
                "[Google Sites] CRAWL:   Fetch [%s/%s] %s",
                len(visited),
                MAX_CRAWL_PAGES,
                url_norm[:LOG_URL_SHORT_PREVIEW_LEN]
                + ("..." if len(url_norm) > LOG_URL_SHORT_PREVIEW_LEN else ""),
            )
            html, title, final_url = await self._fetch_page(url_norm)
            if html is None or final_url is None:
                continue
            final_norm = _normalize_crawl_url(final_url)
            if final_norm not in collected_pages_set:
                pages_collected.append(
                    GoogleSitesPage(
                        url=final_norm,
                        final_url=final_url,
                        title=title or "Page",
                    )
                )
                collected_pages_set.add(final_norm)
            if len(visited) < MAX_CRAWL_PAGES:
                for link in _extract_same_origin_links(html, final_url, base_parsed):
                    if _normalize_crawl_url(link) not in visited:
                        to_visit.append(link)
            await asyncio.sleep(CRAWL_DELAY_SEC)

        if not pages_collected:
            self.logger.warning("[Google Sites] CRAWL:   ⚠ No pages discovered")

        return pages_collected

    async def fetch_and_clean_page(self, url: str) -> str:
        """
        Fetch a single page over HTTP and return cleaned text for indexing.

        Raises an exception if the page is not reachable or returns an error
        status code.
        """
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(
                    url,
                    headers=HTTP_HEADERS,
                    allow_redirects=True,
                ) as response:
                    status = response.status
                    if status >= HTTP_ERROR_THRESHOLD:
                        raise RuntimeError(f"Page returned HTTP {status}")

                    content_type = (response.headers.get("Content-Type") or "").lower()
                    if "text/html" not in content_type and "application/xhtml" not in content_type:
                        raise RuntimeError(
                            f"Unsupported content type for Google Sites page: {content_type}"
                        )

                    html = await response.text()
        except aiohttp.ClientError as e:
            self.logger.error("[Google Sites] STREAM:   ❌ HTTP error: %s", e)
            raise

        return _clean_html_for_indexing(html)

    async def check_url_reachable(self, url: str) -> bool:
        """
        Perform a lightweight HEAD request to check if a URL is reachable.
        """
        try:
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.head(
                    url,
                    headers=HTTP_HEADERS,
                    allow_redirects=True,
                ) as response:
                    status = response.status
        except Exception as e:
            self.logger.error("[Google Sites] TEST:   ❌ Failed to reach URL: %s", e)
            return False
        return HTTP_SUCCESS_MIN <= status <= HTTP_SUCCESS_MAX

