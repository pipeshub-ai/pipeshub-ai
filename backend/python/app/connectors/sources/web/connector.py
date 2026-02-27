import asyncio
import base64
import hashlib
import random
import re
import uuid
from io import BytesIO
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import aiohttp
import pillow_avif  # noqa: F401
from bs4 import BeautifulSoup
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AppGroups, Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.connector_builder import (
    ConnectorBuilder,
    ConnectorScope,
    CustomField,
    DocumentationLink,
)
from app.connectors.core.registry.filters import FilterOptionsResponse
from app.connectors.sources.web.fetch_strategy import fetch_url_with_fallback
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
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

RETRYABLE_EXCEPTIONS = (
    aiohttp.ClientConnectorError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerTimeoutError,
    asyncio.TimeoutError,
)

# MIME type mapping for common file extensions
FILE_MIME_TYPES = {
    '.pdf': MimeTypes.PDF,
    '.doc': MimeTypes.DOC,
    '.docx': MimeTypes.DOCX,
    '.xls': MimeTypes.XLS,
    '.xlsx': MimeTypes.XLSX,
    '.ppt': MimeTypes.PPT,
    '.pptx': MimeTypes.PPTX,
    '.txt': MimeTypes.TEXT,
    '.csv': MimeTypes.CSV,
    '.json': MimeTypes.JSON,
    '.xml': MimeTypes.XML,
    '.zip': MimeTypes.ZIP,
    '.jpg': MimeTypes.JPEG,
    '.jpeg': MimeTypes.JPEG,
    '.png': MimeTypes.PNG,
    '.gif': MimeTypes.GIF,
    '.svg': MimeTypes.SVG,
    '.html': MimeTypes.HTML,
    '.htm': MimeTypes.HTML,
}

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

RETRYABLE_EXCEPTIONS = (
    aiohttp.ClientConnectorError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ServerTimeoutError,
    asyncio.TimeoutError,
)


class WebApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.WEB, AppGroups.WEB, connector_id)

@ConnectorBuilder("Web")\
    .in_group("Web")\
    .with_supported_auth_types("NONE")\
    .with_description("Crawl and sync data from web pages")\
    .with_categories(["Web"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/web.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Web Connector Guide",
            "https://docs.pipeshub.ai/connectors/web",
            "setup"
        ))
        .with_scheduled_config(True, 1440)  # Daily sync
        .add_sync_custom_field(CustomField(
            name="url",
            display_name="Website URL",
            field_type="TEXT",
            required=True,
            description="The URL of the website to crawl (e.g., https://example.com)"
        ))
        .add_sync_custom_field(CustomField(
            name="type",
            display_name="Crawl Type",
            field_type="SELECT",
            required=True,
            default_value="single",
            options=["single", "recursive"],
            description="Choose whether to crawl a single page or recursively crawl linked pages"
        ))
        .add_sync_custom_field(CustomField(
            name="depth",
            display_name="Crawl Depth",
            field_type="NUMBER",
            required=False,
            default_value="3",
            min_length=1,
            max_length=10,
            description="Maximum depth for recursive crawling (1-10, only applies to recursive type)"
        ))
        .add_sync_custom_field(CustomField(
            name="max_pages",
            display_name="Maximum Pages",
            field_type="NUMBER",
            required=False,
            default_value="100",
            min_length=1,
            max_length=1000,
            description="Maximum number of pages to crawl (1-1000)"
        ))
        .add_sync_custom_field(CustomField(
            name="follow_external",
            display_name="Follow External Links",
            field_type="BOOLEAN",
            required=False,
            default_value="false",
            description="Follow links to external domains"
        ))
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class WebConnector(BaseConnector):
    """
    Web connector for crawling and indexing web pages.

    Features:
    - Single page or recursive crawling
    - Configurable depth control
    - Handles various file formats (PDF, images, documents)
    - Extracts clean HTML content
    - Deduplication via URL normalization
    - Respects max pages limit
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            WebApp(connector_id), logger, data_entities_processor, data_store_provider, config_service, connector_id
        )
        self.connector_name = Connectors.WEB
        self.connector_id = connector_id

        # Configuration
        self.url: Optional[str] = None
        self.crawl_type: str = "single"
        self.max_depth: int = 3
        self.max_pages: int = 100
        self.follow_external: bool = False

        # Crawling state
        self.visited_urls: Set[str] = set()
        self.base_domain: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None

        # Batch processing
        self.batch_size: int = 50

    async def init(self) -> bool:
        """Initialize the web connector with configuration."""
        try:
            # Try to get config from different paths
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )

            if not config:
                self.logger.error("❌ WebPage config not found")
                raise ValueError("Web connector configuration not found")

            sync_config = config.get("sync", {})
            if not sync_config:
                self.logger.error("❌ WebPage sync config not found")
                raise ValueError("WebPage sync config not found")

            self.url = sync_config.get("url")
            if not self.url:
                self.logger.error("❌ WebPage url not found")
                raise ValueError("WebPage url not found")

            self.crawl_type = sync_config.get("type", "single")
            self.max_depth = int(sync_config.get("depth") or 3)
            self.max_pages = int(sync_config.get("max_pages") or 1000)
            self.follow_external = sync_config.get("follow_external", False)


            # Parse base domain
            parsed_url = urlparse(self.url)
            self.base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"

            # Initialize aiohttp session with realistic browser headers
            # These headers mimic a real Chrome browser to avoid being blocked by websites
            # that check for bot traffic. Includes modern security headers (Sec-Fetch-*)
            # and Chrome client hints (sec-ch-ua-*) that are sent by real browsers.
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    # Modern Chrome User-Agent
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                    # Accept headers that match real browsers
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    # Security and privacy headers
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    # Fetch metadata headers (important for modern browsers)
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    # Additional headers for realism
                    "Cache-Control": "max-age=0",
                    "sec-ch-ua": '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"macOS"',
                }
            )

            self.logger.info(
                f"✅ Web connector initialized: url={self.url}, type={self.crawl_type}, "
                f"depth={self.max_depth}, max_pages={self.max_pages}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize web connector: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """Test if the website is accessible using the multi-strategy fallback."""
        if not self.url or not self.session:
            return False

        try:
            result = await fetch_url_with_fallback(
                url=self.url,
                session=self.session,
                logger=self.logger,
                max_retries_per_strategy=1,  # keep it fast for a connection test
            )

            if result is None:
                self.logger.warning(f"⚠️ Website not accessible: {self.url}")
                return False

            if result.status_code < HttpStatusCode.BAD_REQUEST.value:
                self.logger.info(
                    f"✅ Website accessible: {self.url} "
                    f"(status: {result.status_code}, via {result.strategy})"
                )
                return True
            else:
                self.logger.warning(
                    f"⚠️ Website returned status {result.status_code}: {self.url}"
                )
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to access website: {e}")
            return False

    def get_app_users(self, users: List[User]) -> List[AppUser]:
        """Convert User objects to AppUser objects."""
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

    async def create_record_group(self, app_users: List[AppUser]) -> None:
        """
        Create a record group with external_group_id as self.url and give permissions to all app_users.

        Args:
            app_users: List of AppUser objects to grant permissions to
        """
        try:
            if not self.url:
                self.logger.warning("⚠️ Cannot create record group: URL not set")
                return

            # Extract title from URL for the record group name
            parsed_url = urlparse(self.url)
            record_group_name = parsed_url.netloc or self.url

            # Create record group
            record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=record_group_name,
                external_group_id=self.url,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                group_type=RecordGroupType.WEB,
                web_url=self.url,
                created_at=get_epoch_timestamp_in_ms(),
                updated_at=get_epoch_timestamp_in_ms(),
            )

            # Create READ permissions for all app_users
            permissions = [
                Permission(
                    email=app_user.email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                )
                for app_user in app_users
                if app_user.email
            ]

            # Create/update record group with permissions
            await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])

            self.logger.info(
                f"✅ Created record group '{record_group_name}' with permissions for {len(permissions)} users"
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to create record group: {e}", exc_info=True)
            raise

    async def reload_config(self) -> None:
        """Reload the connector configuration."""
        try:
            self.logger.debug("running reload config")
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config",
                use_cache=False
            )

            if not config:
                self.logger.error("❌ WebPage config not found")
                raise ValueError("Web connector configuration not found")

            sync_config = config.get("sync", {})
            if not sync_config:
                self.logger.error("❌ WebPage sync config not found")
                raise ValueError("WebPage sync config not found")

            new_url = sync_config.get("url")
            if not new_url:
                self.logger.error("❌ WebPage url not found")
                raise ValueError("WebPage url not found")

            new_crawl_type = sync_config.get("type", "single")
            new_max_depth = int(sync_config.get("depth") or 3)
            new_max_pages = int(sync_config.get("max_pages") or 1000)
            new_follow_external = sync_config.get("follow_external", False)


            # Parse base domain
            parsed_url = urlparse(new_url)
            new_base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"

            if new_base_domain != self.base_domain:
                self.logger.error(f"❌ Cannot change base domain from {self.base_domain} to {new_base_domain}. Please create a new connector for {new_base_domain}")
                raise ValueError("Cannot change base domain for web connector.")

            if new_crawl_type != self.crawl_type:
                self.logger.info(f"🔄 Crawl type changed from {self.crawl_type} to {new_crawl_type}")
                self.crawl_type = new_crawl_type
            if new_max_depth != self.max_depth:
                self.logger.info(f"🔄 Max depth changed from {self.max_depth} to {new_max_depth}")
                self.max_depth = new_max_depth
            if new_max_pages != self.max_pages:
                self.logger.info(f"🔄 Max pages changed from {self.max_pages} to {new_max_pages}")
                self.max_pages = new_max_pages
            if new_follow_external != self.follow_external:
                self.logger.info(f"🔄 Follow external changed from {self.follow_external} to {new_follow_external}")
                self.follow_external = new_follow_external


        except Exception as e:
            self.logger.error(f"❌ Failed to reload config: {e}", exc_info=True)
            raise

    async def run_sync(self) -> None:
        """Main sync method to crawl and index web pages."""
        try:
            await self.reload_config()
            self.logger.info(f"🚀 Starting web crawl: {self.url}")

            # Step 1: fetch and sync all active users
            self.logger.info("Syncing users...")
            all_active_users = await self.data_entities_processor.get_all_active_users()
            app_users = self.get_app_users(all_active_users)
            await self.data_entities_processor.on_new_app_users(app_users)

            # Step 2: create record group with permissions
            await self.create_record_group(app_users)

            # Reset state for new sync
            self.visited_urls.clear()

            # Start crawling
            if self.crawl_type == "recursive":
                await self._crawl_recursive(self.url, depth=0)
            else:
                await self._crawl_single_page(self.url)

            self.logger.info(
                f"✅ Web crawl completed: {len(self.visited_urls)} pages processed"
            )

        except Exception as e:
            self.logger.error(f"❌ Error during web sync: {e}", exc_info=True)
            raise

    async def _crawl_single_page(self, url: str) -> None:
        """Crawl a single page and index it."""
        try:
            file_record, permissions = await self._fetch_and_process_url(url, depth=0)

            self.visited_urls.add(self._normalize_url(url))

            if file_record:
                await self.data_entities_processor.on_new_records([(file_record, permissions)])
                self.logger.info(f"✅ Indexed single page: {url}")

        except Exception as e:
            self.logger.error(f"❌ Error crawling single page {url}: {e}", exc_info=True)

    async def _crawl_recursive(self, start_url: str, depth: int) -> None:
        """Recursively crawl pages starting from start_url."""
        try:
            batch_records: List[Tuple[FileRecord, List[Permission]]] = []

            async for file_record, permissions in self._crawl_recursive_generator(start_url, depth):
                batch_records.append((file_record, permissions))

                # Process batch when it reaches the size limit
                if len(batch_records) >= self.batch_size:
                    await self.data_entities_processor.on_new_records(batch_records)
                    self.logger.info(f"✅ Batch processed: {len(batch_records)} records")
                    batch_records.clear()

            # Process remaining batch
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)
                self.logger.info(f"✅ Final batch processed: {len(batch_records)} records")

        except Exception as e:
            self.logger.error(f"❌ Error in recursive crawl: {e}", exc_info=True)
            raise

    async def _crawl_recursive_generator(
        self, start_url: str, depth: int
    ) -> AsyncGenerator[Tuple[FileRecord, List[Permission]], None]:
        """
        BFS crawl generator; yields (FileRecord, permissions) for each successfully
        fetched page. Allows non-blocking processing of large site crawls.

        Yields:
            Tuple of (FileRecord, List[Permission])
        """
        # Queue for BFS crawling: (url, depth, referer)
        queue: List[Tuple[str, int, Optional[str]]] = [(start_url, depth, None)]

        while queue and len(self.visited_urls) < self.max_pages:
            current_url, current_depth, referer = queue.pop(0)

            # Skip if already visited
            normalized_url = self._normalize_url(current_url)
            if normalized_url in self.visited_urls:
                continue

            # Skip if depth exceeded
            if current_depth > self.max_depth:
                continue

            self.logger.info(
                f"📄 Crawling [{len(self.visited_urls) + 1}/{self.max_pages}] "
                f"(depth {current_depth}): {current_url}"
            )

            try:
                # Fetch and process the page with referer
                file_record_with_permissions = await self._fetch_and_process_url(
                    current_url, current_depth, referer=referer
                )

                if file_record_with_permissions is None:
                    continue

                file_record, permissions = file_record_with_permissions

                if file_record:
                    self.visited_urls.add(normalized_url)

                    # Extract links if we haven't reached max depth
                    if current_depth < self.max_depth and file_record.mime_type == MimeTypes.HTML.value:
                        links = await self._extract_links_from_content(
                            current_url, file_record, referer=referer
                        )

                        # Add new links to queue with current URL as referer
                        for link in links:
                            normalized_link = self._normalize_url(link)
                            if (
                                normalized_link not in self.visited_urls
                                and len(self.visited_urls) < self.max_pages
                            ):
                                queue.append((link, current_depth + 1, current_url))

                    yield (file_record, permissions)

            except Exception as e:
                self.logger.warning(f"⚠️ Failed to process {current_url}: {e}")
                continue

            # Small delay to be respectful to the server; also yields control to
            # other async tasks (mirrors the OneDrive generator pattern).
            await asyncio.sleep(0.5)

    async def _fetch_and_process_url(
        self, url: str, depth: int, referer: Optional[str] = None
    ) -> Optional[Tuple[FileRecord, List[Permission]]]:
        """Fetch URL content using multi-strategy fallback and create a FileRecord."""
        try:

            result = await fetch_url_with_fallback(
                url=url,
                session=self.session,
                logger=self.logger,
                referer=referer,
                timeout=15,
            )

            if result is None:
                return None

            if result.status_code >= HttpStatusCode.BAD_REQUEST.value:
                # Already logged inside fetch_url_with_fallback
                return None

            content_type = result.headers.get("Content-Type", "").lower()
            final_url = result.final_url
            content_bytes = result.content_bytes

            # Determine MIME type and file extension
            mime_type, extension = self._determine_mime_type(url, content_type)

            # Generate unique ID
            record_id = str(uuid.uuid4())
            external_id = final_url

            # Get title and clean content for HTML
            title = self._extract_title_from_url(final_url)
            size_in_bytes = len(content_bytes)
            timestamp = get_epoch_timestamp_in_ms()

            # For HTML pages, extract clean content
            if mime_type == MimeTypes.HTML:
                try:
                    soup = BeautifulSoup(content_bytes, "html.parser")
                    title = self._extract_title(soup, final_url)

                    # Remove script and style elements
                    for script in soup(["script", "style", "noscript", "iframe"]):
                        script.decompose()

                    # Get text content
                    text_content = soup.get_text(separator="\n", strip=True)

                    # Store cleaned HTML for indexing
                    content_bytes = text_content.encode("utf-8")
                    size_in_bytes = len(content_bytes)

                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to parse HTML for {url}: {e}")

            # Calculate MD5 hash once
            content_md5_hash = hashlib.md5(content_bytes).hexdigest()

            # Ensure title is never empty (schema requirement)
            if not title or not title.strip():
                title = self._extract_title_from_url(final_url)
                # Final fallback: use URL if title extraction still fails
                if not title or not title.strip():
                    parsed = urlparse(final_url)
                    title = parsed.netloc or final_url

            # Create FileRecord
            file_record = FileRecord(
                id=record_id,
                record_name=title,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.WEB,
                external_record_id=external_id,
                external_revision_id=content_md5_hash,
                external_record_group_id=self.url,
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=timestamp,
                updated_at=timestamp,
                source_created_at=timestamp,
                source_updated_at=timestamp,
                weburl=final_url,
                size_in_bytes=size_in_bytes,
                is_file=True,
                extension=extension,
                path=urlparse(final_url).path,
                mime_type=mime_type.value,
                md5_hash=content_md5_hash,
                preview_renderable=False,
            )

            # Create permissions (org-level access for web pages)
            permissions = [
                Permission(
                    external_id=self.data_entities_processor.org_id,
                    type=PermissionType.READ,
                    entity_type=EntityType.ORG,
                )
            ]

            self.logger.debug(
                "✅ Processed: %s (%s, %s bytes) via %s",
                title, mime_type.value, size_in_bytes, result.strategy
            )

            return file_record, permissions

        except asyncio.TimeoutError:
            self.logger.warning(f"⚠️ Timeout fetching {url}")
            return None
        except Exception as e:
            self.logger.error(f"❌ Error fetching {url}: {e}", exc_info=True)
            return None

    async def _extract_links_from_content(
        self, base_url: str, file_record: FileRecord, referer: Optional[str] = None
    ) -> List[str]:
        """Extract valid links from HTML content."""
        links = []

        try:
            # Add referer header if provided
            headers = {}
            if referer:
                headers["Referer"] = referer

            # Re-fetch and parse the page to extract links
            async with self.session.get(file_record.weburl, headers=headers) as response:
                if response.status >= HttpStatusCode.BAD_REQUEST.value:
                    return links

                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')

                # Find all anchor tags
                for anchor in soup.find_all('a', href=True):
                    href = anchor['href']

                    # Convert relative URLs to absolute
                    absolute_url = urljoin(base_url, href)

                    # Validate and filter URLs
                    if self._is_valid_url(absolute_url, base_url):
                        links.append(absolute_url)

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to extract links from {base_url}: {e}")

        return links

    def _is_valid_url(self, url: str, base_url: str) -> bool:
        """Check if a URL should be crawled."""
        try:
            parsed = urlparse(url)
            base_parsed = urlparse(base_url)

            # Skip non-http(s) schemes
            if parsed.scheme not in ['http', 'https']:
                return False

            # Skip anchors and fragments
            if parsed.fragment:
                return False

            # Skip common file types we don't want to index
            skip_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.css', '.js', '.ico',
                             '.svg', '.woff', '.woff2', '.ttf', '.eot']
            if any(parsed.path.lower().endswith(ext) for ext in skip_extensions):
                return False

            # Check domain restrictions
            if not self.follow_external and parsed.netloc != base_parsed.netloc:
                return False

            return True

        except Exception:
            return False

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        try:
            parsed = urlparse(url)
            # Remove fragment and normalize
            normalized = urlunparse((
                parsed.scheme,
                parsed.netloc.lower(),
                parsed.path.rstrip('/') or '/',
                parsed.params,
                parsed.query,
                ''  # Remove fragment
            ))
            return normalized
        except Exception:
            return url

    def _determine_mime_type(self, url: str, content_type: str) -> Tuple[MimeTypes, Optional[str]]:
        """Determine MIME type and extension from URL and content-type header."""
        # First, try to get from content-type header
        if content_type:
            if 'html' in content_type:
                return MimeTypes.HTML, 'html'
            elif 'pdf' in content_type:
                return MimeTypes.PDF, 'pdf'
            elif 'json' in content_type:
                return MimeTypes.JSON, 'json'
            elif 'xml' in content_type:
                return MimeTypes.XML, 'xml'
            elif 'plain' in content_type:
                return MimeTypes.TEXT, 'txt'

        # Try to get from URL extension
        parsed_url = urlparse(url)
        path = parsed_url.path.lower()

        for ext, mime_type in FILE_MIME_TYPES.items():
            if path.endswith(ext):
                return mime_type, ext.lstrip('.')

        # Default to HTML
        return MimeTypes.HTML, 'html'

    def _extract_title(self, soup: BeautifulSoup, url: str) -> str:
        """Extract page title from BeautifulSoup object."""
        # Try <title> tag
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            if title:
                return title

        # Try <h1> tag
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
            if title:
                return title

        # Try og:title meta tag
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content'].strip()
            if title:
                return title

        # Fallback to URL
        return self._extract_title_from_url(url)

    def _extract_title_from_url(self, url: str) -> str:
        """Extract a title from the URL path."""
        parsed = urlparse(url)
        path = parsed.path.strip('/')

        if path:
            # Get last segment and clean it up
            segments = path.split('/')
            last_segment = segments[-1]

            # Remove file extension
            if '.' in last_segment:
                last_segment = last_segment.rsplit('.', 1)[0]

            # Replace hyphens and underscores with spaces and title case
            title = last_segment.replace('-', ' ').replace('_', ' ').title()
            return title if title else url

        return parsed.netloc

    @classmethod
    async def create_connector(
        cls, logger: Logger, data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> BaseConnector:
        """Factory method to create a WebConnector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return WebConnector(
            logger, data_entities_processor, data_store_provider, config_service, connector_id
        )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.session:
            await self.session.close()
            self.session = None
        self.visited_urls.clear()
        self.logger.info("✅ Web connector cleanup completed")

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records - not implemented for Web connector yet."""

        try:
            if not record_results:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(record_results)} Web records")

            await self.data_entities_processor.reindex_existing_records(record_results)
            self.logger.info(f"Published reindex events for {len(record_results)} records")

        except Exception as e:
            self.logger.error(f"Error during Web reindex: {e}", exc_info=True)
            raise

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Web connector does not support dynamic filter options."""
        raise NotImplementedError("Web connector does not support dynamic filter options")

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Web connector doesn't support webhooks."""
        pass

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Return the web URL as the signed URL."""
        return record.weburl if record.weburl else None

    # ==================== Base64 Validation Helpers ====================

    def _clean_base64_string(self, b64_str: str) -> str:
        """
        Clean and validate a base64 string to ensure it's valid for embedding in HTML
        and downstream processing (e.g., OpenAI API).

        This function performs thorough validation including:
        - URL decoding (handles %3D -> = etc.)
        - Whitespace/newline removal
        - Character validation (A-Z, a-z, 0-9, +, /, =)
        - Padding correction
        - Decode validation to ensure the base64 is actually valid

        Args:
            b64_str: Base64 encoded string (may be URL-encoded)

        Returns:
            Cleaned and validated base64 string, or empty string if invalid
        """
        if not b64_str:
            return ""

        # First, URL-decode the string in case it contains %3D (=) or other encoded chars
        cleaned = unquote(b64_str)

        # Remove all whitespace, newlines, and tabs
        cleaned = cleaned.replace("\n", "").replace("\r", "").replace(" ", "").replace("\t", "")

        # Validate base64 characters
        if not re.fullmatch(r"[A-Za-z0-9+/=]+", cleaned):
            self.logger.warning("⚠️ Invalid base64 characters detected, skipping")
            return ""

        # Fix padding if needed (base64 strings must be multiple of 4)
        missing_padding = (-len(cleaned)) % 4
        if missing_padding:
            cleaned += "=" * missing_padding

        # Validate by attempting to decode
        try:
            base64.b64decode(cleaned, validate=True)
        except Exception as e:
            self.logger.warning(f"⚠️ Invalid base64 string (decode failed): {str(e)[:100]}")
            return ""

        return cleaned

    def _clean_data_uris_in_html(self, html: str) -> str:
        """
        Clean and validate base64 in data URIs that might have been corrupted
        by BeautifulSoup formatting or contain URL-encoded characters.

        Args:
            html: HTML string containing data URIs

        Returns:
            HTML string with cleaned data URIs (invalid ones are removed)
        """
        # Use a simple, non-backtracking pattern that captures the data URI header
        # Then we manually extract the base64 content up to the closing quote
        pattern = r'data:image/[^;]+;base64,'

        result = []
        last_end = 0

        for match in re.finditer(pattern, html):
            header = match.group(0)
            start = match.start()
            base64_start = match.end()

            # Find the end of base64 content (first quote or >)
            base64_end = base64_start
            while base64_end < len(html) and html[base64_end] not in '"\'>' :
                base64_end += 1

            b64_part = html[base64_start:base64_end]

            # URL-decode and clean
            cleaned_b64 = unquote(b64_part)
            cleaned_b64 = cleaned_b64.replace("\n", "").replace("\r", "").replace(" ", "").replace("\t", "")

            # Validate and clean the base64
            is_valid = False
            if re.fullmatch(r"[A-Za-z0-9+/=]+", cleaned_b64):
                # Fix padding
                missing_padding = (-len(cleaned_b64)) % 4
                if missing_padding:
                    cleaned_b64 += "=" * missing_padding

                # Validate by attempting to decode
                try:
                    base64.b64decode(cleaned_b64, validate=True)
                    is_valid = True
                except Exception as e:
                    self.logger.warning(f"⚠️ Invalid base64 in data URI (decode failed): {str(e)[:50]}")
            else:
                self.logger.warning("⚠️ Invalid base64 characters in data URI during post-processing")

            # Add content up to this data URI
            result.append(html[last_end:start])

            if is_valid:
                # Add cleaned data URI
                result.append(header + cleaned_b64)
            else:
                # Remove invalid image by not adding the data URI
                # This effectively removes the src attribute value
                self.logger.warning("⚠️ Removing invalid base64 data URI from HTML")

            last_end = base64_end

        # Add remaining content
        result.append(html[last_end:])

        return ''.join(result)

    # ==================== HTML Processing Helpers ====================

    def _remove_unwanted_tags(self, soup: BeautifulSoup) -> None:
        """Remove script, style, noscript, and iframe tags from the soup."""
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()

    def _convert_svg_tag_to_png(self, soup: BeautifulSoup, svg) -> bool:
        """
        Convert an SVG tag to a PNG img tag.

        Args:
            soup: BeautifulSoup object for creating new tags
            svg: SVG tag element to convert

        Returns:
            True if conversion succeeded, False otherwise
        """
        try:
            svg_content = str(svg)
            svg_bytes = svg_content.encode('utf-8')
            svg_b64_str = base64.b64encode(svg_bytes).decode('utf-8')

            # Convert SVG to PNG
            png_b64_str = ImageParser.svg_base64_to_png_base64(svg_b64_str)
            png_b64_str = self._clean_base64_string(png_b64_str)

            if not png_b64_str:
                self.logger.warning("⚠️ Failed to clean/validate PNG base64 from SVG, skipping")
                svg.decompose()
                return False

            # Create new img tag
            new_img = soup.new_tag('img')
            new_img['src'] = f"data:image/png;base64,{png_b64_str}"
            new_img['alt'] = svg.get('aria-label') or svg.get('title') or 'Converted SVG image'

            svg.replace_with(new_img)
            self.logger.debug("✅ Converted SVG tag to PNG img tag")
            return True

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to convert SVG tag to PNG: {e}. Removing SVG tag.")
            svg.decompose()
            return False

    def _process_svg_tags(self, soup: BeautifulSoup) -> None:
        """Convert all SVG tags to PNG img tags."""
        for svg in soup.find_all('svg'):
            self._convert_svg_tag_to_png(soup, svg)

    # Image formats supported by OpenAI vision API
    OPENAI_SUPPORTED_IMAGE_TYPES = frozenset({'image/png', 'image/jpeg', 'image/gif', 'image/webp'})
    # Accept header for image requests — deliberately excludes unsupported image types
    _IMAGE_ACCEPT_HEADER = "image/png,image/jpeg,image/gif,image/webp,image/svg+xml,image/*;q=0.8"

    async def _process_single_image(
        self,
        img,
        soup: BeautifulSoup,
        base_url: str,
        headers: dict
    ) -> None:
        """
        Process a single image tag: download if needed and convert to base64.

        Args:
            img: Image tag element
            soup: BeautifulSoup object
            base_url: Base URL for resolving relative URLs
            headers: HTTP headers for requests
        """
        src = img.get('src')
        if not src:
            return

        # Handle existing data URIs
        if "data:image" in src:
            if "," in src:
                header, existing_b64 = src.split(",", 1)

                # Extract and validate the mime type from the data URI header
                mime_match = re.match(r'data:([^;,]+)', header)
                mime_type = mime_match.group(1).lower() if mime_match else ''

                if mime_type == 'image/svg+xml':
                    if ';base64' not in header:
                        # URL-encoded SVG, decode directly
                        svg_bytes = unquote(existing_b64).encode('utf-8')
                    else:
                        # base64-encoded SVG
                        cleaned_b64 = self._clean_base64_string(existing_b64)
                        if not cleaned_b64:
                            self.logger.warning("⚠️ Invalid base64 in SVG data URI, removing image")
                            img.decompose()
                            return
                        svg_bytes = base64.b64decode(cleaned_b64)

                    # Common path for both cases
                    try:
                        png_b64 = self._convert_svg_bytes_to_png_base64(svg_bytes, 'inline-svg-data-uri')
                        if png_b64:
                            img['src'] = f"data:image/png;base64,{png_b64}"
                        else:
                            self.logger.warning("⚠️ Failed to convert inline SVG data URI to PNG, removing image")
                            img.decompose()
                    except Exception as e:
                        self.logger.warning(f"⚠️ Error converting inline SVG data URI: {e}, removing image")
                        img.decompose()

                elif mime_type == 'image/avif':
                    self.logger.debug("Converting inline AVIF base64 to PNG base64")
                    # Inline AVIF data URI — decode and convert to PNG
                    cleaned_b64 = self._clean_base64_string(existing_b64)
                    if cleaned_b64:
                        try:
                            avif_bytes = base64.b64decode(cleaned_b64)
                            png_b64 = self._convert_avif_bytes_to_png_base64(avif_bytes, 'inline-avif-data-uri')
                            if png_b64:
                                img['src'] = f"data:image/png;base64,{png_b64}"
                            else:
                                img.decompose()
                        except Exception as e:
                            self.logger.warning(f"⚠️ Error converting inline AVIF data URI: {e}, removing image")
                            img.decompose()
                    else:
                        self.logger.warning("⚠️ Invalid base64 in AVIF data URI, removing image")
                        img.decompose()
                elif mime_type and mime_type not in self.OPENAI_SUPPORTED_IMAGE_TYPES:
                    self.logger.warning(f"⚠️ Unsupported image format '{mime_type}' in existing data URI, removing image")
                    img.decompose()
                else:
                    # Supported format — just clean/validate the base64
                    cleaned_b64 = self._clean_base64_string(existing_b64)
                    if cleaned_b64:
                        img['src'] = f"{header},{cleaned_b64}"
                    else:
                        self.logger.warning("⚠️ Invalid existing base64 data URI, removing image")
                        img.decompose()
            return

        # Download and convert external images
        try:
            absolute_url = src if src.startswith(('http:', 'https:')) else urljoin(base_url, src)

            # Override Accept header so servers don't return unsupported image types
            img_headers = {**headers, "Accept": self._IMAGE_ACCEPT_HEADER}

            async with self.session.get(absolute_url, headers=img_headers) as img_response:
                if img_response.status >= HttpStatusCode.BAD_REQUEST.value:
                    self.logger.warning(f"⚠️ Failed to download image: {absolute_url} (status: {img_response.status})")
                    return

                img_bytes = await img_response.read()
                if not img_bytes:
                    return

                content_type = self._determine_image_content_type(img_response, absolute_url)

                # Convert to base64 (handle SVG and AVIF specially)
                if content_type == 'image/svg+xml':
                    b64_str = self._convert_svg_bytes_to_png_base64(img_bytes, absolute_url)
                    if not b64_str:
                        img.decompose()
                        return
                    content_type = 'image/png'
                elif content_type == 'image/avif':
                    self.logger.debug("Converting external AVIF to PNG base64")
                    b64_str = self._convert_avif_bytes_to_png_base64(img_bytes, absolute_url)
                    if not b64_str:
                        img.decompose()
                        return
                    content_type = 'image/png'
                elif content_type not in self.OPENAI_SUPPORTED_IMAGE_TYPES:
                    # Server returned an unsupported format — log full metadata then skip.
                    raw_ct = img_response.headers.get('Content-Type', '<none>')
                    self.logger.debug(
                        f"⚠️ Unsupported downloaded image — "
                        f"resolved_content_type='{content_type}' | "
                        f"raw_Content-Type='{raw_ct}' | "
                        f"url='{absolute_url}' | "
                        f"response_status={img_response.status} | "
                        f"content_length={len(img_bytes)} bytes | "
                        f"response_headers={dict(img_response.headers)} — skipping"
                    )
                    img.decompose()
                    return
                else:
                    b64_str = base64.b64encode(img_bytes).decode('utf-8')
                    b64_str = self._clean_base64_string(b64_str)
                    if not b64_str:
                        self.logger.warning(f"⚠️ Failed to clean/validate base64 for image: {absolute_url}. Removing.")
                        img.decompose()
                        return
                    self.logger.debug(f"✅ Converted image to base64: {absolute_url}")

                img['src'] = f"data:{content_type};base64,{b64_str}"

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to process image {src}: {e}")

    def _determine_image_content_type(self, response, url: str) -> str:
        """Determine the content type of an image from response headers or URL."""
        content_type = response.headers.get('Content-Type', 'image/jpeg')

        if not content_type or content_type == 'application/octet-stream':
            parsed_url = urlparse(url)
            path_lower = parsed_url.path.lower()

            extension_map = {
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
                '.svg': 'image/svg+xml',
                '.avif': 'image/avif',
            }

            for ext, mime in extension_map.items():
                if path_lower.endswith(ext):
                    return mime
            return 'image/jpeg'

        return content_type.split(';')[0].strip().lower()

    def _convert_svg_bytes_to_png_base64(self, svg_bytes: bytes, url: str) -> Optional[str]:
        """Convert SVG bytes to PNG base64 string."""
        try:
            svg_b64_str = base64.b64encode(svg_bytes).decode('utf-8')
            png_b64_str = ImageParser.svg_base64_to_png_base64(svg_b64_str)
            png_b64_str = self._clean_base64_string(png_b64_str)

            if not png_b64_str:
                self.logger.warning(f"⚠️ Failed to clean/validate PNG base64 from SVG: {url}. Removing.")
                return None

            self.logger.debug(f"✅ Converted SVG to PNG and base64: {url}")
            return png_b64_str

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to convert SVG to PNG: {e}. Removing image.")
            return None

    def _convert_avif_bytes_to_png_base64(self, avif_bytes: bytes, url: str) -> Optional[str]:
        """
        Convert AVIF bytes to PNG base64 string. use pillow_avif to convert AVIF to PNG.
        """

        try:
            with Image.open(BytesIO(avif_bytes)) as img:
                out_mode = "RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB"
                png_buffer = BytesIO()
                img.convert(out_mode).save(png_buffer, format="PNG")
            png_b64_str = self._clean_base64_string(
                base64.b64encode(png_buffer.getvalue()).decode('utf-8')
            )
            if not png_b64_str:
                self.logger.warning(f"⚠️ Failed to clean/validate PNG base64 from AVIF (Pillow): {url}")
                return None
            self.logger.debug(f"✅ Converted AVIF→PNG via Pillow: {url}")
            return png_b64_str

        except Exception as pillow_err:
            self.logger.debug(
                f"ℹ️  Pillow could not open AVIF ({pillow_err}), trying ffmpeg fallback — '{url}'"
            )

    async def _process_all_images(
        self,
        soup: BeautifulSoup,
        base_url: str,
        headers: dict
    ) -> None:
        """Process all image tags in the soup."""
        for img in soup.find_all('img'):
            await self._process_single_image(img, soup, base_url, headers)

    async def _process_html_content(
        self,
        content_bytes: bytes,
        record: Record,
        headers: dict
    ) -> Optional[str]:
        """
        Process HTML content: parse, clean, and convert images to base64.

        Args:
            content_bytes: Raw HTML content bytes
            record: Record object containing URL and metadata
            headers: HTTP headers for image requests

        Returns:
            Cleaned HTML string with embedded base64 images, or None on failure
        """
        try:
            html_content = content_bytes.decode('utf-8')
            soup = BeautifulSoup(html_content, 'html.parser')

            # Remove unwanted tags
            self._remove_unwanted_tags(soup)

            # Convert SVG tags to PNG img tags
            self._process_svg_tags(soup)

            # Process all images: download and convert to base64
            await self._process_all_images(soup, record.weburl, headers)

            # Serialize and clean data URIs
            cleaned_html = str(soup)
            cleaned_html = self._clean_data_uris_in_html(cleaned_html)

            return cleaned_html

        except Exception as e:
            self.logger.error(f"⚠️ Failed to parse/clean HTML: {e}")
            raise

    # ==================== Retryable Fetch Method ====================

    async def _fetch_with_retry(
        self,
        url: str,
        headers: dict,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 10.0,
    ) -> bytes:
        """
        Fetch a URL with exponential backoff retry for transient errors.

        Args:
            url: The URL to fetch
            headers: Request headers
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay in seconds before first retry
            max_delay: Maximum delay cap in seconds

        Returns:
            The response content as bytes

        Raises:
            HTTPException: On non-retryable HTTP errors or after exhausting retries
        """

        for attempt in range(max_retries + 1):
            try:
                async with self.session.get(url, headers=headers) as response:
                    # Non-retryable client errors — fail immediately
                    if (
                        response.status >= HttpStatusCode.BAD_REQUEST.value
                        and response.status not in RETRYABLE_STATUS_CODES
                    ):
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"HTTP {response.status} for {url}",
                        )

                    # Retryable status codes
                    if response.status in RETRYABLE_STATUS_CODES:
                        if attempt == max_retries:
                            raise HTTPException(
                                status_code=response.status,
                                detail=f"HTTP {response.status} for {url} after {max_retries} retries",
                            )

                        delay = self._calculate_retry_delay(
                            attempt, base_delay, max_delay,
                            retry_after=response.headers.get("Retry-After"),
                        )
                        self.logger.warning(
                            f"\n\n\n⚠️ Retryable HTTP {response.status} for {url}. "
                            f"Attempt {attempt + 1}/{max_retries + 1}, retrying in {delay:.2f}s"
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Success
                    return await response.read()

            except RETRYABLE_EXCEPTIONS as e:
                if attempt == max_retries:
                    self.logger.error(
                        f"❌ Failed to fetch {url} after {max_retries} retries: {e}"
                    )
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_GATEWAY.value,
                        detail=f"Failed to fetch {url} after {max_retries} retries: {e}",
                    )

                delay = self._calculate_retry_delay(attempt, base_delay, max_delay)
                self.logger.warning(
                    f"⚠️ Connection error for {url}: {e}. "
                    f"Attempt {attempt + 1}/{max_retries + 1}, retrying in {delay:.2f}s"
                )
                await asyncio.sleep(delay)

        # Should not be reached, but just in case
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail=f"Failed to fetch {url} after {max_retries} retries",
        )

    @staticmethod
    def _calculate_retry_delay(
        attempt: int,
        base_delay: float,
        max_delay: float,
        retry_after: Optional[str] = None,
    ) -> float:
        """
        Calculate delay using exponential backoff with jitter,
        respecting Retry-After header if present.
        """
        if retry_after:
            try:
                return min(float(retry_after), max_delay)
            except (ValueError, TypeError):
                pass

        delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
        return min(delay, max_delay)

    # ==================== Main Stream Record Method ====================

    async def stream_record(self, record: Record) -> Optional[StreamingResponse]:
        """
        Stream the web page content with proper content extraction.
        """
        if not record.weburl:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Web URL is missing for record {record.record_name} (id:{record.id})",
            )

        try:
            headers = {"Referer": self.url} if self.url else {}

            content_bytes = await self._fetch_with_retry(record.weburl, headers)

            mime_type = record.mime_type or "text/html"

            # Process HTML content
            cleaned_html_content = None
            if "html" in mime_type.lower():
                cleaned_html_content = await self._process_html_content(
                    content_bytes, record, headers
                )

            # Prepare response content
            response_content = (
                cleaned_html_content.encode("utf-8")
                if cleaned_html_content
                else content_bytes
            )

            return create_stream_record_response(
                BytesIO(response_content),
                filename=record.record_name,
                mime_type=mime_type,
                fallback_filename=f"record_{record.id}",
            )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"❌ Error streaming record {record.id}: {e}", exc_info=True
            )
            raise

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (same as full sync for web pages)."""
        await self.run_sync()
