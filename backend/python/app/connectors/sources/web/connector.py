import asyncio
import base64
import hashlib
import os
import uuid
from datetime import datetime
from io import BytesIO
from logging import Logger
from pathlib import Path
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
    ConnectorScope,
    CustomField,
    DocumentationLink,
)
from app.connectors.core.registry.filters import FilterOptionsResponse
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.utils.streaming import create_stream_record_response

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

    async def run_sync(self) -> None:
        """Main sync method to crawl and index web pages."""
        try:
            self.logger.info(f"🚀 Starting web crawl: {self.url}")

            # Step 1: fetch and sync all active users
            self.logger.info("Syncing users...")
            all_active_users = await self.data_entities_processor.get_all_active_users()
            app_users = self.get_app_users(all_active_users)
            await self.data_entities_processor.on_new_app_users(app_users)

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

            self.logger.info(f"visited_urls: {self.visited_urls}")

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

                        self.logger.debug(f"text_content: {text_content}")

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
        self.logger.warning("Reindex not implemented for Web connector")
        pass

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

                # Process HTML content: parse, clean, and convert images to base64
                cleaned_html_content = None
                if "html" in mime_type.lower():
                    try:
                        # Parse HTML with BeautifulSoup
                        html_content = content_bytes.decode('utf-8')
                        soup = BeautifulSoup(html_content, 'html.parser')

                        # Remove unwanted tags (script, style, noscript, iframe)
                        for tag in soup(["script", "style", "noscript", "iframe"]):
                            tag.decompose()

                        # Convert SVG tags to img tags with PNG base64 (OpenAI doesn't support SVG)
                        svg_tags = soup.find_all('svg')
                        for svg in svg_tags:
                            try:
                                # Get SVG content as string
                                svg_content = str(svg)
                                # Encode SVG content to base64
                                svg_bytes = svg_content.encode('utf-8')
                                svg_b64_str = base64.b64encode(svg_bytes).decode('utf-8')
                                
                                # Convert SVG to PNG
                                png_b64_str = ImageParser.svg_base64_to_png_base64(svg_b64_str)
                                
                                # Create new img tag with PNG data URI
                                new_img = soup.new_tag('img')
                                new_img['src'] = f"data:image/png;base64,{png_b64_str}"
                                # Preserve alt text if present in SVG
                                if svg.get('aria-label'):
                                    new_img['alt'] = svg.get('aria-label')
                                elif svg.get('title'):
                                    new_img['alt'] = svg.get('title')
                                else:
                                    new_img['alt'] = 'Converted SVG image'
                                
                                # Replace SVG tag with img tag
                                svg.replace_with(new_img)
                                self.logger.debug(f"✅ Converted SVG tag to PNG img tag")
                            except Exception as svg_error:
                                self.logger.warning(f"⚠️ Failed to convert SVG tag to PNG: {svg_error}. Removing SVG tag.")
                                # If conversion fails, remove the SVG tag
                                svg.decompose()
                                continue

                        # Process images: download and convert to base64
                        images = soup.find_all('img')
                        for img in images:
                            src = img.get('src')
                            if not src:
                                continue

                            # Skip if already a data URI
                            if "data:image" in src:
                                continue

                            try:
                                # Convert relative URLs to absolute
                                if not src.startswith(('http:', 'https:')):
                                    absolute_url = urljoin(record.weburl, src)
                                else:
                                    absolute_url = src

                                # Download the image
                                async with self.session.get(absolute_url, headers=headers) as img_response:
                                    if img_response.status >= HttpStatusCode.BAD_REQUEST.value:
                                        self.logger.warning(f"⚠️ Failed to download image: {absolute_url} (status: {img_response.status})")
                                        continue

                                    img_bytes = await img_response.read()
                                    if not img_bytes:
                                        continue

                                    # Determine content type
                                    content_type = img_response.headers.get('Content-Type', 'image/jpeg')
                                    # Fallback to extension-based type if header is missing
                                    if not content_type or content_type == 'application/octet-stream':
                                        parsed_img_url = urlparse(absolute_url)
                                        path_lower = parsed_img_url.path.lower()
                                        if path_lower.endswith('.png'):
                                            content_type = 'image/png'
                                        elif path_lower.endswith('.gif'):
                                            content_type = 'image/gif'
                                        elif path_lower.endswith('.webp'):
                                            content_type = 'image/webp'
                                        elif path_lower.endswith('.svg'):
                                            content_type = 'image/svg+xml'
                                        else:
                                            content_type = 'image/jpeg'

                                    # Normalize content type (remove parameters like charset)
                                    content_type = content_type.split(';')[0].strip().lower()
                                    
                                    # Convert SVG to PNG before base64 encoding (OpenAI doesn't support SVG)
                                    if content_type == 'image/svg+xml':
                                        try:
                                            # First encode SVG bytes to base64
                                            svg_b64_str = base64.b64encode(img_bytes).decode('utf-8')
                                            # Convert SVG base64 to PNG base64
                                            png_b64_str = ImageParser.svg_base64_to_png_base64(svg_b64_str)
                                            # Update content type to PNG
                                            content_type = 'image/png'
                                            b64_str = png_b64_str
                                            self.logger.debug(f"✅ Converted SVG to PNG and base64: {absolute_url}")
                                        except Exception as svg_error:
                                            self.logger.warning(f"⚠️ Failed to convert SVG to PNG: {svg_error}. Removing image from HTML.")
                                            # Remove the image tag entirely to prevent SVG URL from being extracted later
                                            img.decompose()
                                            continue
                                    else:
                                        # Convert to base64 for non-SVG images
                                        b64_str = base64.b64encode(img_bytes).decode('utf-8')
                                        self.logger.debug(f"✅ Converted image to base64: {absolute_url}")

                                    img['src'] = f"data:{content_type};base64,{b64_str}"

                            except Exception as img_error:
                                self.logger.warning(f"⚠️ Failed to process image {src}: {img_error}")
                                continue

                        # Get cleaned HTML string
                        cleaned_html_content = str(soup)

                    except Exception as html_error:
                        self.logger.warning(f"⚠️ Failed to parse/clean HTML: {html_error}")

                # Save cleaned content to file
                try:
                    # Create Documents/web-cleaned-logs directory
                    log_dir = Path.home() / "Documents" / "web-cleaned-logs"
                    log_dir.mkdir(parents=True, exist_ok=True)

                    # Generate filename from record ID and timestamp
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    safe_record_id = record.id.replace("-", "_")[:8] if record.id else "unknown"
                    
                    # Determine file extension based on mime_type
                    if "html" in mime_type.lower():
                        ext = ".html"
                    elif "json" in mime_type.lower():
                        ext = ".json"
                    elif "xml" in mime_type.lower():
                        ext = ".xml"
                    elif "text" in mime_type.lower():
                        ext = ".txt"
                    elif "pdf" in mime_type.lower():
                        ext = ".pdf"
                    else:
                        ext = ".txt"
                    
                    filename = f"{timestamp}_{safe_record_id}_{record.record_name}{ext}"
                    file_path = log_dir / filename

                    # Write content
                    if cleaned_html_content:
                        # Save cleaned HTML with base64 images
                        file_path.write_text(cleaned_html_content, encoding='utf-8')
                        self.logger.info(f"✅ Saved cleaned HTML with base64 images to: {file_path}")
                    elif mime_type.startswith("text/") or "html" in mime_type.lower() or "json" in mime_type.lower() or "xml" in mime_type.lower():
                        # Try to decode as UTF-8 for text content
                        try:
                            content_text = content_bytes.decode('utf-8')
                            file_path.write_text(content_text, encoding='utf-8')
                        except UnicodeDecodeError:
                            # Fallback: try other encodings or save as binary
                            try:
                                content_text = content_bytes.decode('latin-1')
                                file_path.write_text(content_text, encoding='utf-8')
                            except Exception:
                                file_path.write_bytes(content_bytes)
                    else:
                        # For binary content, save as-is
                        file_path.write_bytes(content_bytes)

                    if not cleaned_html_content:
                        self.logger.info(f"✅ Saved streamed content to: {file_path}")
                except Exception as save_error:
                    self.logger.warning(f"⚠️ Failed to save content to file: {save_error}")

                # Use cleaned HTML content if available, otherwise use original content
                if cleaned_html_content:
                    response_content = cleaned_html_content.encode('utf-8')
                else:
                    response_content = content_bytes

                return create_stream_record_response(
                    BytesIO(response_content),
                    filename=record.record_name,
                    mime_type=mime_type,
                    fallback_filename=f"record_{record.id}"
                )
        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.id}: {e}")
            return None

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (same as full sync for web pages)."""
        await self.run_sync()
