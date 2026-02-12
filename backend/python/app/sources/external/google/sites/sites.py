"""
Google Sites datasource for URL-based crawling of published sites.

This datasource encapsulates the HTTP crawl, URL normalization, link
extraction, and HTML cleaning logic used by the Google Sites connector.
The connector is responsible for orchestration and mapping to Record/RecordGroup
entities, while this module focuses on fetching and parsing content.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from logging import Logger
from typing import List, Optional, Set, Tuple
from urllib.parse import ParseResult, urljoin, urlparse

from bs4 import BeautifulSoup, Comment

from app.sources.client.google.google import GoogleClient
from app.sources.client.google.sites import (
    GOOGLE_SITES_DEFAULT_HEADERS,
    GoogleSitesRESTClient,
)
from app.sources.client.http.http_request import HTTPRequest

# URL-based crawl limits and HTTP configuration (kept here close to HTTP logic)
MAX_CRAWL_PAGES = 500
CRAWL_DELAY_SEC = 0.5

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


def _determine_image_type_from_bytes(image_bytes: bytes, url: str) -> str:
    """Determine image type from bytes or URL."""
    # Try to detect from content first (most reliable)
    if image_bytes.startswith(b'\x89PNG'):
        return "png"
    elif image_bytes.startswith(b'\xff\xd8\xff'):
        return "jpeg"
    elif image_bytes.startswith(b'GIF'):
        return "gif"
    elif image_bytes.startswith(b'<svg') or image_bytes.startswith(b'<?xml'):
        return "svg"
    elif image_bytes.startswith(b'RIFF') and b'WEBP' in image_bytes[:12]:
        return "webp"

    # Fallback to URL extension
    url_lower = url.lower()
    if ".jpg" in url_lower or ".jpeg" in url_lower:
        return "jpeg"
    elif ".gif" in url_lower:
        return "gif"
    elif ".webp" in url_lower:
        return "webp"
    elif ".svg" in url_lower:
        return "svg"
    elif ".png" in url_lower:
        return "png"

    # Default to png if unknown
    return "png"


def _clean_html_for_indexing(html: str) -> str:
    """
    Parse with BeautifulSoup and return clean HTML content for indexing.

    Preserves images (which should already be converted to base64 data URIs)
    and returns HTML format suitable for multimodal indexing.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements (but keep img tags - they contain base64 images)
    for tag in soup.find_all(
        ["script", "style", "noscript", "iframe", "svg", "nav", "footer", "header", "aside"]
    ):
        tag.decompose()

    # Remove comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Get main content area
    main = soup.find("main") or soup.find("article") or soup.find("body")
    content_element = main if main else soup

    # Return clean HTML with images preserved
    return str(content_element)


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

    def __init__(
        self,
        client: Optional[GoogleClient] = None,
        logger: Optional[Logger] = None,
    ) -> None:
        """
        Initialize the datasource with a GoogleClient.

        Uses the same pattern as other Google connectors (Drive, Gmail, etc.):
        the client is a GoogleClient; get_client() returns the underlying
        HTTP client (GoogleSitesRESTClient) used for crawling published sites.

        When no client is provided, a default is built via
        GoogleClient.build_with_client(GoogleSitesRESTClient()) for tests
        and the standalone example script.
        """
        if client is None:
            client = GoogleClient.build_with_client(GoogleSitesRESTClient())

        self._client = client.get_client()
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
            request = HTTPRequest(
                url=url,
                method="GET",
                headers=dict(GOOGLE_SITES_DEFAULT_HEADERS),
            )
            response = await self._client.execute(request)
            status = response.status
            if status >= HTTP_ERROR_THRESHOLD:
                if self.logger:
                    self.logger.warning(
                        "[Google Sites] CRAWL:   HTTP %s for %s",
                        status,
                        url[:LOG_URL_PREVIEW_LEN],
                    )
                return None, None, None

            # Use the HTTPResponse.content_type helper which already normalizes
            # header casing and strips parameters like charset.
            content_type = (response.content_type or "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return None, None, None

            html = response.text()
            final_url = response.url
            soup = BeautifulSoup(html, "html.parser")
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            return html, title, final_url
        except Exception as e:
            if self.logger:
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

    async def _convert_images_to_base64(self, html: str, base_url: str) -> str:
        """
        Convert embedded images in HTML content to base64 data URIs.

        Finds all <img> tags and downloads their images, converting them to
        base64 data URIs for proper indexing.

        Args:
            html: HTML content that may contain embedded images
            base_url: Base URL for resolving relative image URLs

        Returns:
            HTML content with images converted to base64 data URIs
        """
        if not html:
            return html

        soup = BeautifulSoup(html, "html.parser")
        img_tags = soup.find_all("img")

        if not img_tags:
            return html

        # Process each image tag
        for img_tag in img_tags:
            src = img_tag.get("src", "")
            if not src:
                continue

            # Skip data URIs (already converted)
            if src.startswith("data:"):
                continue

            # Skip invalid URLs
            if src.startswith("javascript:") or src.startswith("#"):
                continue

            try:
                # Resolve relative URLs
                if src.startswith("http://") or src.startswith("https://"):
                    absolute_url = src
                else:
                    absolute_url = urljoin(base_url, src)

                # Download image using HTTP client
                img_request = HTTPRequest(
                    url=absolute_url,
                    method="GET",
                    headers=dict(GOOGLE_SITES_DEFAULT_HEADERS),
                )
                img_response = await self._client.execute(img_request)

                if img_response.status >= HTTP_ERROR_THRESHOLD:
                    if self.logger:
                        self.logger.debug(
                            "[Google Sites] IMAGE:   HTTP %s for %s",
                            img_response.status,
                            absolute_url[:LOG_URL_PREVIEW_LEN],
                        )
                    continue  # Skip failed images

                # Check content type
                content_type = (img_response.content_type or "").lower()
                if not content_type.startswith("image/"):
                    if self.logger:
                        self.logger.debug(
                            "[Google Sites] IMAGE:   Non-image content type %s for %s",
                            content_type,
                            absolute_url[:LOG_URL_PREVIEW_LEN],
                        )
                    continue

                # Get image bytes
                image_bytes = img_response.content
                if not image_bytes:
                    if self.logger:
                        self.logger.debug(
                            "[Google Sites] IMAGE:   Empty content for %s",
                            absolute_url[:LOG_URL_PREVIEW_LEN],
                        )
                    continue

                # Determine image type
                image_type = _determine_image_type_from_bytes(image_bytes, absolute_url)

                # Convert to base64
                base64_encoded = base64.b64encode(image_bytes).decode("utf-8")

                # Create data URI
                if image_type == "svg":
                    data_uri = f"data:image/svg+xml;base64,{base64_encoded}"
                else:
                    data_uri = f"data:image/{image_type};base64,{base64_encoded}"

                # Replace the src attribute with the data URI
                img_tag["src"] = data_uri

                if self.logger:
                    self.logger.debug(
                        "[Google Sites] IMAGE:   ✓ Converted %s to base64 (%s)",
                        absolute_url[:LOG_URL_SHORT_PREVIEW_LEN],
                        image_type,
                    )

            except Exception as e:
                if self.logger:
                    self.logger.debug(
                        "[Google Sites] IMAGE:   ⚠ Failed to convert image %s: %s",
                        src[:LOG_URL_SHORT_PREVIEW_LEN] if src else "(no src)",
                        e,
                    )
                continue  # Skip on error

        return str(soup)

    async def fetch_and_clean_page(self, url: str) -> str:
        """
        Fetch a single page over HTTP and return cleaned text for indexing.

        Raises an exception if the page is not reachable or returns an error
        status code.
        """
        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers=dict(GOOGLE_SITES_DEFAULT_HEADERS),
            )
            response = await self._client.execute(request)
            status = response.status
            if status >= HTTP_ERROR_THRESHOLD:
                raise RuntimeError(f"Page returned HTTP {status}")

            content_type = (response.content_type or "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                raise RuntimeError(
                    f"Unsupported content type for Google Sites page: {content_type}"
                )

            html = response.text()

            # Convert images to base64 before cleaning
            html = await self._convert_images_to_base64(html, url)

        except Exception as e:
            if self.logger:
                self.logger.error("[Google Sites] STREAM:   ❌ HTTP error: %s", e)
            raise

        return _clean_html_for_indexing(html)

    async def check_url_reachable(self, url: str) -> bool:
        """
        Perform a lightweight HEAD request to check if a URL is reachable.
        """
        try:
            request = HTTPRequest(
                url=url,
                method="HEAD",
                headers=dict(GOOGLE_SITES_DEFAULT_HEADERS),
            )
            response = await self._client.execute(request)
            status = response.status
        except Exception as e:
            if self.logger:
                self.logger.error("[Google Sites] TEST:   ❌ Failed to reach URL: %s", e)
            return False
        return HTTP_SUCCESS_MIN <= status <= HTTP_SUCCESS_MAX

