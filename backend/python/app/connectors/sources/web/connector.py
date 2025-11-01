import asyncio
import hashlib
import uuid
from io import BytesIO
from logging import Logger
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup
from fastapi.responses import StreamingResponse

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
    CustomField,
    DocumentationLink,
)
from app.models.entities import (
    FileRecord,
    Record,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

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

class WebApp(App):
    def __init__(self) -> None:
        super().__init__(Connectors.WEB, AppGroups.WEB)

@ConnectorBuilder("Web")\
    .in_group("Web")\
    .with_auth_type("NONE")\
    .with_description("Crawl and sync data from web pages")\
    .with_categories(["Web"])\
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
            description="Maximum depth for recursive crawling (1-10, only applies to recursive type)"
        ))
        .add_sync_custom_field(CustomField(
            name="max_pages",
            display_name="Maximum Pages",
            field_type="NUMBER",
            required=False,
            default_value="100",
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
    ) -> None:
        super().__init__(
            WebApp(), logger, data_entities_processor, data_store_provider, config_service
        )
        self.connector_name = Connectors.WEB

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
                "/services/connectors/web/config"
            ) or await self.config_service.get_config(
                f"/services/connectors/web/config/{self.data_entities_processor.org_id}"
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
            self.max_depth = int(sync_config.get("depth", 3))
            self.max_pages = int(sync_config.get("max_pages", 1000))
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
        """Test if the website is accessible."""
        if not self.url or not self.session:
            return False

        try:
            async with self.session.head(self.url, allow_redirects=True) as response:
                if response.status < HttpStatusCode.BAD_REQUEST.value:
                    self.logger.info(f"✅ Website accessible: {self.url} (status: {response.status})")
                    return True
                else:
                    self.logger.warning(f"⚠️ Website returned status {response.status}: {self.url}")
                    return False
        except Exception as e:
            self.logger.error(f"❌ Failed to access website: {e}")
            return False

    async def run_sync(self) -> None:
        """Main sync method to crawl and index web pages."""
        try:
            self.logger.info(f"🚀 Starting web crawl: {self.url}")

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

            if file_record:
                await self.data_entities_processor.on_new_records([(file_record, permissions)])
                self.logger.info(f"✅ Indexed single page: {url}")

        except Exception as e:
            self.logger.error(f"❌ Error crawling single page {url}: {e}", exc_info=True)

    async def _crawl_recursive(self, start_url: str, depth: int) -> None:
        """Recursively crawl pages starting from start_url."""
        try:
            # Queue for BFS crawling: (url, depth, referer)
            queue: List[Tuple[str, int, Optional[str]]] = [(start_url, depth, None)]
            batch_records: List[Tuple[FileRecord, List[Permission]]] = []

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
                    file_record, permissions = await self._fetch_and_process_url(
                        current_url, current_depth, referer=referer
                    )

                    if file_record:
                        batch_records.append((file_record, permissions))
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

                        # Process batch if it reaches batch size
                        if len(batch_records) >= self.batch_size:
                            await self.data_entities_processor.on_new_records(batch_records)
                            self.logger.info(f"✅ Batch processed: {len(batch_records)} records")
                            batch_records.clear()

                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to process {current_url}: {e}")
                    continue

                # Small delay to be respectful to the server
                await asyncio.sleep(0.5)

            # Process remaining batch
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)
                self.logger.info(f"✅ Final batch processed: {len(batch_records)} records")

        except Exception as e:
            self.logger.error(f"❌ Error in recursive crawl: {e}", exc_info=True)
            raise

    async def _fetch_and_process_url(
        self, url: str, depth: int, referer: Optional[str] = None
    ) -> Optional[Tuple[FileRecord, List[Permission]]]:
        """Fetch URL content and create a FileRecord."""
        try:
            # Add referer header if provided (mimics browser behavior)
            headers = {}
            if referer:
                headers["Referer"] = referer

            async with self.session.get(url, headers=headers, allow_redirects=True) as response:
                if response.status >= HttpStatusCode.BAD_REQUEST.value:
                    self.logger.warning(f"⚠️ HTTP {response.status} for {url}")
                    return None

                content_type = response.headers.get("Content-Type", "").lower()
                final_url = str(response.url)

                # Read content
                content_bytes = await response.read()

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
                        soup = BeautifulSoup(content_bytes, 'html.parser')
                        title = self._extract_title(soup, final_url)

                        # Remove script and style elements
                        for script in soup(["script", "style", "noscript", "iframe"]):
                            script.decompose()

                        # Get text content
                        text_content = soup.get_text(separator='\n', strip=True)

                        # Store cleaned HTML for indexing
                        content_bytes = text_content.encode('utf-8')
                        size_in_bytes = len(content_bytes)

                    except Exception as e:
                        self.logger.warning(f"⚠️ Failed to parse HTML for {url}: {e}")

                # Calculate MD5 hash once
                content_md5_hash = hashlib.md5(content_bytes).hexdigest()

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
                    f"✅ Processed: {title} ({mime_type.value}, {size_in_bytes} bytes)"
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
            return soup.title.string.strip()

        # Try <h1> tag
        h1 = soup.find('h1')
        if h1:
            return h1.get_text(strip=True)

        # Try og:title meta tag
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content'].strip()

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
        config_service: ConfigurationService
    ) -> BaseConnector:
        """Factory method to create a WebConnector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return WebConnector(
            logger, data_entities_processor, data_store_provider, config_service
        )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.session:
            await self.session.close()
            self.session = None
        self.visited_urls.clear()
        self.logger.info("✅ Web connector cleanup completed")

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Web connector doesn't support webhooks."""
        pass

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """Return the web URL as the signed URL."""
        return record.weburl if record.weburl else None

    async def stream_record(self, record: Record) -> Optional[StreamingResponse]:
        """Stream the web page content with proper content extraction."""
        if not record.weburl:
            return None

        try:
            # Use appropriate headers for streaming
            headers = {"Referer": self.url} if self.url else {}
            async with self.session.get(record.weburl, headers=headers) as response:
                if response.status >= HttpStatusCode.BAD_REQUEST.value:
                    return None

                content_bytes = await response.read()
                mime_type = record.mime_type or "text/html"

                # For PDF and other binary formats, return as-is
                # For other text formats (JSON, XML, TXT), return as-is

                return StreamingResponse(
                    BytesIO(content_bytes),
                    media_type=mime_type,
                    headers={
                        "Content-Disposition": f"inline; filename={record.record_name}"
                    }
                )
        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.id}: {e}")
            return None

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (same as full sync for web pages)."""
        await self.run_sync()
