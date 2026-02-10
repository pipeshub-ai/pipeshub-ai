"""
Google Sites Connector

Syncs a published Google Site by URL: user provides the published site URL (mandatory),
connector crawls it over HTTP to discover all pages, creates one RecordGroup and
FileRecords per page, and streams content for indexing via HTTP fetch.
"""

import asyncio
import uuid
from logging import Logger
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, Comment
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import config_node_constants
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOptionsResponse,
    FilterType,
    load_connector_filters,
)
from app.connectors.sources.google.common.apps import GoogleSitesApp
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

DEFAULT_CONNECTOR_ENDPOINT = "http://localhost:8000"

# URL-based crawl limits
MAX_CRAWL_PAGES = 500
CRAWL_DELAY_SEC = 0.5

# Default HTTP timeout and headers for crawling published sites
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _normalize_published_site_url(url: Optional[str]) -> str:
    """Normalize and validate published site URL. Raises ValueError if invalid."""
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


@ConnectorBuilder("Google Sites")\
    .in_group(AppGroups.GOOGLE_WORKSPACE.value)\
    .with_description("Sync a published Google Site by URL via HTTP crawl")\
    .with_categories(["Storage", "Documentation"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            AuthField(
                name="serviceAccountJson",
                display_name="Service Account JSON",
                placeholder="Click to upload service account JSON file",
                description="Upload your Service Account JSON key. This is optional for URL-based crawling of public sites.",
                field_type="FILE",
                is_secret=True
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/drive.svg")
        .add_documentation_link(DocumentationLink(
            "Google Drive API – Service account",
            "https://developers.google.com/identity/protocols/oauth2/service-account",
            "setup"
        ))
        .add_filter_field(FilterField(
            name="published_site_url",
            display_name="Published site URL",
            filter_type=FilterType.STRING,
            category=FilterCategory.SYNC,
            description="Full published Google Site URL (e.g. https://sites.google.com/view/your-site). This URL will be crawled over HTTP to discover and index all pages under the same site.",
            required=True,
        ))
        .with_sync_strategies(["MANUAL"])
        .with_scheduled_config(False, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class GoogleSitesConnector(BaseConnector):
    """
    Connector for Google Sites (new). Uses Drive API to list sites,
    creates RecordGroups for sites and FileRecords for pages within each site.
    Uses service account token authentication for fetching content.
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> None:
        super().__init__(
            app=GoogleSitesApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
        )
        self.connector_name = Connectors.GOOGLE_SITES
        self.connector_id = connector_id
        self.filter_key = "googlesites"
        self.batch_size = 100
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def get_app_users(self, users: List[User]) -> List[AppUser]:
        return [
            AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=user.source_user_id or user.id or user.email,
                org_id=user.org_id or self.data_entities_processor.org_id,
                email=user.email,
                full_name=user.full_name or user.email,
                is_active=user.is_active if user.is_active is not None else True,
                title=user.title,
            )
            for user in users
            if user.email
        ]

    async def init(self) -> bool:
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("[Google Sites] INIT: Starting connector initialization")
        self.logger.info("[Google Sites] INIT: connector_id=%s", self.connector_id)
        self.logger.info("")

        try:
            self.logger.info("[Google Sites] INIT: Step 1 — Load sync and indexing filters")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )
            self.logger.info("[Google Sites] INIT:   ✓ Filters loaded")
            self.logger.info("")
            self.logger.info("=" * 60)
            self.logger.info("[Google Sites] INIT: ✓ Initialization complete")
            self.logger.info("")
            return True
        except Exception as e:
            self.logger.error("[Google Sites] INIT:   ❌ Failed: %s", e, exc_info=True)
            return False

    def _build_permissions(self) -> List[Permission]:
        """Build permissions for synced content (org read)."""
        return [
            Permission(
                type=PermissionType.READ,
                entity_type=EntityType.ORG,
                external_id=self.data_entities_processor.org_id,
            )
        ]

    def _normalize_crawl_url(self, url: str) -> str:
        """Normalize URL for crawl deduplication (strip fragment, trailing slash)."""
        url = url.rstrip("/") or "/"
        if "#" in url:
            url = url.split("#")[0]
        return url

    def _same_origin(self, page_url: str, base_parsed: Any) -> bool:
        """Return True if page_url has same scheme and netloc as base."""
        try:
            p = urlparse(page_url)
            return p.scheme == base_parsed.scheme and p.netloc == base_parsed.netloc
        except Exception:
            return False

    async def _fetch_page(
        self, session: aiohttp.ClientSession, url: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Fetch URL; return (html, title, final_url). On failure return (None, None, None)."""
        try:
            async with session.get(url, headers=HTTP_HEADERS, allow_redirects=True) as response:
                if response.status >= 400:
                    self.logger.warning("[Google Sites] CRAWL:   HTTP %s for %s", response.status, url[:80])
                    return None, None, None
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    return None, None, None
                html = await response.text()
                final_url = str(response.url)
                soup = BeautifulSoup(html, "html.parser")
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else None
                return html, title or "", final_url
        except asyncio.TimeoutError:
            self.logger.warning("[Google Sites] CRAWL:   Timeout %s", url[:80])
            return None, None, None
        except Exception as e:
            self.logger.warning("[Google Sites] CRAWL:   Error %s: %s", url[:80], e)
            return None, None, None

    def _extract_same_origin_links(
        self, html: str, current_url: str, base_parsed: Any
    ) -> List[str]:
        """Extract same-origin links from HTML."""
        links: List[str] = []
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
            if not self._same_origin(absolute, base_parsed):
                continue
            normalized = self._normalize_crawl_url(absolute)
            if normalized not in links:
                links.append(normalized)
        return links

    async def run_sync(self) -> None:
        """Main sync: crawl published site URL over HTTP, discover pages, create RecordGroup + FileRecords."""
        try:
            self.logger.info("")
            self.logger.info("=" * 80)
            self.logger.info("[Google Sites] SYNC: ========== STARTING FULL SYNC ==========")
            self.logger.info("=" * 80)
            self.logger.info("")

            # Step 1: Reload filters
            self.logger.info("[Google Sites] SYNC: Step 1 — Reload filters")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )
            self.logger.info("[Google Sites] SYNC:   ✓ Filters loaded")
            self.logger.info("")

            # Step 2: Validate mandatory published site URL
            published_url_filter = self.sync_filters.get("published_site_url") if self.sync_filters else None
            raw_url = (published_url_filter.get_value() or "") if published_url_filter else ""
            if not raw_url or (isinstance(raw_url, str) and not raw_url.strip()):
                self.logger.error("[Google Sites] SYNC:   ❌ Published site URL is required")
                raise ValueError("Published site URL is required. Set the 'Published site URL' filter and try again.")
            try:
                start_url = _normalize_published_site_url(raw_url.strip() if isinstance(raw_url, str) else str(raw_url))
            except ValueError as e:
                self.logger.error("[Google Sites] SYNC:   ❌ Invalid URL: %s", e)
                raise
            self.logger.info("[Google Sites] SYNC:   Published site URL: %s", start_url[:80] + ("..." if len(start_url) > 80 else ""))
            self.logger.info("")

            # Step 3: Sync app users
            self.logger.info("[Google Sites] SYNC: Step 3 — Sync app users")
            all_active_users = await self.data_entities_processor.get_all_active_users()
            app_users = self.get_app_users(all_active_users)
            await self.data_entities_processor.on_new_app_users(app_users)
            self.logger.info("[Google Sites] SYNC:   ✓ %s app users", len(app_users))
            self.logger.info("")

            base_parsed = urlparse(start_url)
            base_origin = f"{base_parsed.scheme}://{base_parsed.netloc}"
            visited: Set[str] = set()
            to_visit: List[str] = [self._normalize_crawl_url(start_url)]
            pages_collected: List[Tuple[str, str, str]] = []  # (url, title, final_url)

            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                # Step 4: BFS crawl
                self.logger.info("[Google Sites] SYNC: Step 4 — Crawl published site (max %s pages)", MAX_CRAWL_PAGES)
                while to_visit and len(visited) < MAX_CRAWL_PAGES:
                    url = to_visit.pop(0)
                    url_norm = self._normalize_crawl_url(url)
                    if url_norm in visited:
                        continue
                    visited.add(url_norm)
                    self.logger.info("[Google Sites] SYNC:   Fetch [%s/%s] %s", len(visited), MAX_CRAWL_PAGES, url_norm[:70] + ("..." if len(url_norm) > 70 else ""))
                    html, title, final_url = await self._fetch_page(session, url_norm)
                    if html is None:
                        continue
                    final_norm = self._normalize_crawl_url(final_url)
                    if final_norm not in {p[0] for p in pages_collected}:
                        pages_collected.append((final_norm, title or "Page", final_url))
                    if len(visited) < MAX_CRAWL_PAGES:
                        for link in self._extract_same_origin_links(html, final_url, base_parsed):
                            if self._normalize_crawl_url(link) not in visited:
                                to_visit.append(link)
                    await asyncio.sleep(CRAWL_DELAY_SEC)

            if not pages_collected:
                self.logger.warning("[Google Sites] SYNC:   ⚠ No pages discovered")
                self.logger.info("=" * 80)
                self.logger.info("")
                return

            # Step 5: Create RecordGroup (one per site)
            site_name = pages_collected[0][1] if pages_collected[0][1] != "Page" else base_parsed.netloc or "Google Site"
            record_group = RecordGroup(
                name=site_name,
                external_group_id=base_origin,
                group_type=RecordGroupType.SITE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"Google Site: {site_name}",
                web_url=start_url,
            )
            permissions = self._build_permissions()
            await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])
            self.logger.info("[Google Sites] SYNC:   ✓ RecordGroup: %s", site_name)
            self.logger.info("")

            # Step 6: Create FileRecords for each page
            timestamp_ms = get_epoch_timestamp_in_ms()
            batch_records: List[Tuple[FileRecord, List[Permission]]] = []
            for page_url, page_title, _ in pages_collected:
                page_revision_id = f"{page_url}/{timestamp_ms}"
                file_record = FileRecord(
                    id=str(uuid.uuid4()),
                    record_name=page_title or "Page",
                    record_type=RecordType.FILE,
                    record_group_type=RecordGroupType.SITE.value,
                    external_record_group_id=base_origin,
                    external_record_id=page_url,
                    external_revision_id=page_revision_id,
                    version=0,
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_created_at=timestamp_ms,
                    source_updated_at=timestamp_ms,
                    weburl=page_url,
                    signed_url=None,
                    fetch_signed_url=None,
                    mime_type=MimeTypes.HTML.value,
                    is_file=True,
                    extension="html",
                    path=urlparse(page_url).path or "/",
                    parent_external_record_id=None,
                    parent_record_type=None,
                    etag=None,
                    ctag=None,
                    quick_xor_hash=None,
                    crc32_hash=None,
                    sha1_hash=None,
                    sha256_hash=None,
                )
                batch_records.append((file_record, permissions))
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)

            self.logger.info("")
            self.logger.info("=" * 80)
            self.logger.info("[Google Sites] SYNC: ========== SYNC COMPLETED ==========")
            self.logger.info("[Google Sites] SYNC: Pages discovered and indexed: %s", len(pages_collected))
            self.logger.info("=" * 80)
            self.logger.info("")

        except Exception as ex:
            self.logger.error("[Google Sites] SYNC: FAILED: %s", ex, exc_info=True)
            raise

    async def get_signed_url(self, record: Record) -> Optional[str]:
        endpoints = await self.config_service.get_config(config_node_constants.ENDPOINTS.value)
        connector_endpoint = endpoints.get("connectors", {}).get("endpoint", DEFAULT_CONNECTOR_ENDPOINT)
        return f"{connector_endpoint}/api/v1/internal/stream/record/{record.id}"

    async def test_connection_and_access(self) -> bool:
        """Test access by validating the Published site URL over HTTP.

        If the mandatory Published site URL filter is set, we normalize it and perform an
        HTTP HEAD request to confirm the site is reachable. Returns True on 2xx/3xx, and
        False on 4xx/5xx or other failures.

        If the Published site URL filter is not set, a ValueError is raised.
        """
        self.logger.info("")
        self.logger.info("[Google Sites] TEST: Testing connection")
        try:
            self.sync_filters, _ = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )
            published_url_filter = self.sync_filters.get("published_site_url") if self.sync_filters else None
            raw_url = (published_url_filter.get_value() or "") if published_url_filter else ""
            url_str = (raw_url.strip() if isinstance(raw_url, str) else str(raw_url or "")).strip()
            if not url_str:
                self.logger.error(
                    "[Google Sites] TEST:   ❌ Published site URL filter is required for connection testing"
                )
                raise ValueError(
                    "Google Sites connector requires the 'Published site URL' sync filter to be set "
                    "before testing the connection."
                )

            try:
                start_url = _normalize_published_site_url(url_str)
            except ValueError as e:
                self.logger.error("[Google Sites] TEST:   ❌ Invalid published site URL: %s", e)
                return False

            try:
                async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                    async with session.head(start_url, headers=HTTP_HEADERS, allow_redirects=True) as response:
                        if 200 <= response.status < 400:
                            self.logger.info(
                                "[Google Sites] TEST:   ✓ Published site URL reachable (HTTP %s)",
                                response.status,
                            )
                            self.logger.info("")
                            return True
                        self.logger.warning(
                            "[Google Sites] TEST:   Published URL returned HTTP %s",
                            response.status,
                        )
                        return False
            except Exception as e:
                self.logger.error("[Google Sites] TEST:   ❌ Failed to reach URL: %s", e)
                return False
        except Exception as e:
            self.logger.error("[Google Sites] TEST:   ❌ %s", e)
            return False

    def _clean_html_for_indexing(self, html: str) -> str:
        """Parse with BeautifulSoup and extract clean text content for indexing."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content elements
        for tag in soup.find_all(["script", "style", "noscript", "iframe", "svg", "nav", "footer", "header", "aside"]):
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

    def _is_url_based_record(self, record: Record) -> bool:
        """True if record was created from URL crawl (external_record_group_id is a URL)."""
        group_id = getattr(record, "external_record_group_id", None) or ""
        return isinstance(group_id, str) and (
            group_id.startswith("http://") or group_id.startswith("https://")
        )

    async def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> StreamingResponse:
        """Stream record content for indexing.

        URL-based records: fetch record.weburl over HTTP and return cleaned text.
        """
        if isinstance(record, FileRecord) and not record.is_file:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot stream folder content",
            )

        self.logger.info("")
        self.logger.info(
            "[Google Sites] STREAM: record_id=%s name=%s",
            record.id,
            getattr(record, "record_name", "") or "(unnamed)",
        )
        self.logger.info(
            "[Google Sites] STREAM:   URL: %s",
            (record.weburl or "").strip()[:70] + "..."
            if len((record.weburl or "")) > 70
            else (record.weburl or "(none)"),
        )

        if self._is_url_based_record(record):
            page_url = (record.weburl or "").strip()
            if not page_url:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail="Record has no web URL",
                )
            self.logger.info("[Google Sites] STREAM:   Fetching page over HTTP")
            try:
                async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                    async with session.get(page_url, headers=HTTP_HEADERS, allow_redirects=True) as response:
                        if response.status >= 400:
                            raise HTTPException(
                                status_code=HttpStatusCode.NOT_FOUND.value,
                                detail=f"Page returned HTTP {response.status}",
                            )
                        raw_html = await response.text()
            except aiohttp.ClientError as e:
                self.logger.error("[Google Sites] STREAM:   ❌ HTTP error: %s", e)
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_GATEWAY.value,
                    detail=f"Failed to fetch page: {e}",
                ) from e
            cleaned_text = self._clean_html_for_indexing(raw_html)
            content_bytes = cleaned_text.encode("utf-8")
            self.logger.info("[Google Sites] STREAM:   ✓ Returning %s bytes", len(content_bytes))
            self.logger.info("")

            async def content_gen():
                yield content_bytes

            return create_stream_record_response(
                content_gen(),
                filename=record.record_name or "page.txt",
                mime_type="text/plain",
                fallback_filename=f"record_{record.id}",
            )
        self.logger.error(
            "[Google Sites] STREAM:   ❌ Unsupported record type; only URL-based Google Sites records are supported"
        )
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail="Only URL-based Google Sites records are supported by the Google Sites connector.",
        )

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    def handle_webhook_notification(self, notification: Dict) -> None:
        pass

    async def cleanup(self) -> None:
        self.logger.info("")
        self.logger.info(
            "[Google Sites] CLEANUP: URL-based Google Sites connector has no external resources to release"
        )
        self.logger.info("")

    async def reindex_records(self, record_results: List[Record]) -> None:
        if not record_results:
            return
        for record in record_results:
            await self.data_entities_processor.on_record_content_update(record)

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> BaseConnector:
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service,
        )
        await data_entities_processor.initialize()
        return GoogleSitesConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        """Google Sites connector uses only the mandatory Published site URL filter; no dynamic options."""
        raise ValueError(
            "Google Sites connector has no dynamic filter options. "
            "Only the 'Published site URL' string sync filter is supported."
        )
