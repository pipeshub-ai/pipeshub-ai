import asyncio
import base64
import hashlib
import re
import uuid
from dataclasses import dataclass
from io import BytesIO
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import aiohttp
import pillow_avif  # noqa: F401  # pyright: ignore[reportUnusedImport]
from bs4 import BeautifulSoup, Tag
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
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    CustomField,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    MultiselectOperator,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.web.fetch_strategy import fetch_url_with_fallback
from app.models.entities import (
    AppUser,
    FileRecord,
    IndexingStatus,
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


async def _bytes_async_gen(data: bytes) -> AsyncGenerator[bytes, None]:
    """Wrap raw bytes as an async generator for StreamingResponse."""
    yield data


@dataclass
class RecordUpdate:
    """Track updates to a record"""
    record: Optional[FileRecord]
    is_new: bool
    is_updated: bool
    is_deleted: bool
    metadata_changed: bool
    content_changed: bool
    permissions_changed: bool
    old_permissions: Optional[List[Permission]] = None
    new_permissions: Optional[List[Permission]] = None
    external_record_id: Optional[str] = None
    html_bytes: Optional[bytes] = None

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
    '.tsv': MimeTypes.TSV,
    '.json': MimeTypes.JSON,
    '.xml': MimeTypes.XML,
    '.zip': MimeTypes.ZIP,
    '.jpg': MimeTypes.JPEG,
    '.jpeg': MimeTypes.JPEG,
    '.png': MimeTypes.PNG,
    '.gif': MimeTypes.GIF,
    '.svg': MimeTypes.SVG,
    '.webp': MimeTypes.WEBP,
    '.heic': MimeTypes.HEIC,
    '.heif': MimeTypes.HEIF,
    '.html': MimeTypes.HTML,
    '.htm': MimeTypes.HTML,
    '.md': MimeTypes.MARKDOWN,
    '.mdx': MimeTypes.MDX,
}

DOCUMENT_MIME_TYPES = {
    MimeTypes.PDF.value,
    MimeTypes.DOC.value,
    MimeTypes.DOCX.value,
    MimeTypes.XLS.value,
    MimeTypes.XLSX.value,
    MimeTypes.CSV.value,
    MimeTypes.PPT.value,
    MimeTypes.PPTX.value,
    MimeTypes.MARKDOWN.value,
    MimeTypes.MDX.value,
    MimeTypes.TEXT.value,
    MimeTypes.TSV.value,
    MimeTypes.JSON.value,
    MimeTypes.XML.value,
    MimeTypes.YAML.value,
}

IMAGE_MIME_TYPES = {
    MimeTypes.PNG.value,
    MimeTypes.JPEG.value,
    MimeTypes.JPG.value,
    MimeTypes.GIF.value,
}

class WebApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.WEB, AppGroups.WEB, connector_id)

@ConnectorBuilder("Web")\
    .in_group("Web")\
    .with_supported_auth_types("NONE")\
    .with_description("Crawl and sync data from web pages")\
    .with_categories(["Web"])\
    .with_scopes([ConnectorScope.PERSONAL, ConnectorScope.TEAM])\
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
            default_value="recursive",
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
            name="max_size_mb",
            display_name="Maximum Size in MB (default 10MB)",
            field_type="NUMBER",
            required=False,
            default_value="10",
            min_length=1,
            max_length=100,
            description="Maximum size in MB of the response (1-100)"
        ))
        .add_sync_custom_field(CustomField(
            name="follow_external",
            display_name="Follow External Links",
            field_type="BOOLEAN",
            required=False,
            default_value="false",
            description="Follow links to external domains"
        ))
        .add_sync_custom_field(CustomField(
            name="restrict_to_start_path",
            display_name="Restrict to Start Path",
            field_type="BOOLEAN",
            required=False,
            default_value="true",
            description="Only crawl URLs within the same path as the starting URL (prevents crawling parent directories)"
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(FilterField(
            name=IndexingFilterKey.WEBPAGES.value,
            display_name="Index Webpages",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of webpages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.IMAGES.value,
            display_name="Index Images",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of images",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.VIDEOS.value,
            display_name="Index Videos",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of videos",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.DOCUMENTS.value,
            display_name="Index Documents",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of documents",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.ATTACHMENTS.value,
            display_name="Index Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of attachments",
            default_value=True
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
        self.restrict_to_start_path: bool = True
        self.start_path_prefix: str = "/"

        # Crawling state
        self.visited_urls: Set[str] = set()
        self.base_domain: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None

        # Batch processing
        self.batch_size: int = 50

        # Filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    async def init(self) -> bool:
        """Initialize the web connector with configuration."""
        try:
            config_values = await self._fetch_and_parse_config(use_cache=True)

            self.url = config_values["url"]
            self.crawl_type = config_values["crawl_type"]
            self.max_depth = config_values["max_depth"]
            self.max_pages = config_values["max_pages"]
            self.max_size_mb = config_values["max_size_mb"]
            self.follow_external = config_values["follow_external"]
            self.base_domain = config_values["base_domain"]
            self.restrict_to_start_path = config_values["restrict_to_start_path"]
            self.start_path_prefix = config_values["start_path_prefix"]

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
                f"✅ Web connector initialized: url={self.url}, type={self.crawl_type},"
                + f" depth={self.max_depth}, max_pages={self.max_pages}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize web connector: {e}", exc_info=True)
            return False

    async def _fetch_and_parse_config(self, use_cache: bool = True) -> Dict:
        """
        Fetch and parse connector configuration.

        Args:
            use_cache: Whether to use cached config (default: True)

        Returns:
            Dictionary containing parsed config values:
            - url: str
            - crawl_type: str
            - max_depth: int
            - max_pages: int
            - follow_external: bool
            - base_domain: str
            - restrict_to_start_path: bool
        Raises:
            ValueError: If config is invalid or missing required fields
        """
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config",
                use_cache=use_cache
            )

            if not config or not isinstance(config, dict):
                self.logger.error("❌ WebPage config not found")
                raise ValueError("Web connector configuration not found")

            sync_config = config.get("sync", {})

            if not sync_config:
                self.logger.error("❌ WebPage sync config not found")
                raise ValueError("WebPage sync config not found")

            url = sync_config.get("url")
            if not url:
                self.logger.error("❌ WebPage url not found")
                raise ValueError("WebPage url not found")
            url = self._ensure_trailing_slash(url)

            crawl_type = sync_config.get("type", "single")
            max_depth = int(sync_config.get("depth") or 3)
            max_pages = int(sync_config.get("max_pages") or 1000)
            max_size_mb = int(sync_config.get("max_size_mb") or 10)
            follow_external = sync_config.get("follow_external", False)
            restrict_to_start_path = sync_config.get("restrict_to_start_path", True)

            # restrict_to_start_path implies staying on the starting domain,
            # so follow_external must be False — override with a warning.
            if restrict_to_start_path and follow_external:
                self.logger.warning(
                    "⚠️ 'restrict_to_start_path' is enabled — overriding 'follow_external' to False "
                    "(cannot follow external links while restricting to the start path)"
                )
                follow_external = False

            # Validate max_pages and max_depth
            if max_pages > 1000:
                self.logger.warning("⚠️ WebPage max_pages is greater than 1000, setting to 1000")
                max_pages = 1000
            elif max_pages < 1:
                self.logger.warning("⚠️ WebPage max_pages is less than 1, setting to 1")
                max_pages = 1
            if max_depth > 10:
                self.logger.warning("⚠️ WebPage max_depth is greater than 10, setting to 10")
                max_depth = 10
            elif max_depth < 1:
                self.logger.warning("⚠️ WebPage max_depth is less than 1, setting to 1")
                max_depth = 1
            if max_size_mb > 100:
                self.logger.warning("⚠️ WebPage max_size_mb is greater than 100, setting to 100")
                max_size_mb = 100
            elif max_size_mb < 1:
                self.logger.warning("⚠️ WebPage max_size_mb is less than 1, setting to 1")
                max_size_mb = 1

            # Parse base domain
            parsed_url = urlparse(url)
            base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"

            # Compute the path prefix to prevent crawling parent paths.
            # If the start URL ends with '/', use it as-is (it's already a directory).
            # Otherwise, use the directory portion (up to and including the last '/').
            start_path = parsed_url.path
            if start_path.endswith('/'):
                start_path_prefix = start_path
            else:
                parent = start_path.rsplit('/', 1)[0]
                start_path_prefix = (parent + '/') if parent else '/'

            return {
                "url": url,
                "crawl_type": crawl_type,
                "max_depth": max_depth,
                "max_pages": max_pages,
                "max_size_mb": max_size_mb,
                "follow_external": follow_external,
                "base_domain": base_domain,
                "restrict_to_start_path": restrict_to_start_path,
                "start_path_prefix": start_path_prefix,
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch and parse config: {e}")
            raise

    async def test_connection_and_access(self) -> bool:  # type: ignore[override]
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
                    + f"(status: {result.status_code}, via {result.strategy})"
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
            config_values = await self._fetch_and_parse_config(use_cache=False)

            config_values["url"]
            new_crawl_type = config_values["crawl_type"]
            new_max_depth = config_values["max_depth"]
            new_max_pages = config_values["max_pages"]
            new_max_size_mb = config_values["max_size_mb"]
            new_follow_external = config_values["follow_external"]
            new_base_domain = config_values["base_domain"]
            new_restrict_to_start_path = config_values["restrict_to_start_path"]

            if new_base_domain != self.base_domain:
                self.logger.error(f"❌ Cannot change base domain from {self.base_domain} to {new_base_domain}. Please create a new connector for {new_base_domain}")
                raise ValueError("Cannot change base domain for web connector.")

            if new_crawl_type != self.crawl_type:
                self.logger.info("🔄 Crawl type changed from %s to %s", self.crawl_type, new_crawl_type)
                self.crawl_type = new_crawl_type
            if new_max_depth != self.max_depth:
                self.logger.info("🔄 Max depth changed from %s to %s", self.max_depth, new_max_depth)
                self.max_depth = new_max_depth
            if new_max_pages != self.max_pages:
                self.logger.info("🔄 Max pages changed from %s to %s", self.max_pages, new_max_pages)
                self.max_pages = new_max_pages
            if new_max_size_mb != self.max_size_mb:
                self.logger.info("🔄 Max size in MB changed from %s to %s", self.max_size_mb, new_max_size_mb)
                self.max_size_mb = new_max_size_mb
            if new_follow_external != self.follow_external:
                self.logger.info("🔄 Follow external changed from %s to %s", self.follow_external, new_follow_external)
                self.follow_external = new_follow_external
            if new_restrict_to_start_path != self.restrict_to_start_path:
                self.logger.info(f"🔄 Restrict to start path changed from {self.restrict_to_start_path} to {new_restrict_to_start_path}")
                self.restrict_to_start_path = new_restrict_to_start_path

        except Exception as e:
            self.logger.error(f"❌ Failed to reload config: {e}", exc_info=True)
            raise

    async def run_sync(self) -> None:  # type: ignore[override]
        """Main sync method to crawl and index web pages."""
        try:
            await self.reload_config()

            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "web", self.connector_id, self.logger
            )

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
            assert self.url is not None, "URL not set — init() must be called first"
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
            record_update = await self._fetch_and_process_url(url, depth=0)

            self.visited_urls.add(self._normalize_url(url))

            if record_update is None:
                return
            file_record = record_update.record
            if file_record:
                is_disabled = self._check_index_filter(file_record)
                if is_disabled:
                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                if record_update.is_updated:
                    await self._handle_record_updates(record_update)
                elif record_update.is_new and record_update.record is not None and record_update.new_permissions is not None:
                    pair: Tuple[Record, List[Permission]] = (record_update.record, record_update.new_permissions)
                    await self.data_entities_processor.on_new_records([pair])
                self.logger.info(f"✅ Indexed single page: {url}")

        except Exception as e:
            self.logger.error(f"❌ Error crawling single page {url}: {e}", exc_info=True)

    async def _create_ancestor_placeholder_records(self, start_url: str) -> None:
        """Create and upsert placeholder WEBPAGE records for every intermediate path
        segment of *start_url* (all segments except the last one).

        Example
        -------
        start_url = "https://developer.mozilla.org/en-US/docs/Web/HTTP/"

        Creates placeholders for:
          - https://developer.mozilla.org/en-US/
          - https://developer.mozilla.org/en-US/docs/

        The name of each record is set to the URL without the scheme
        (e.g. ``developer.mozilla.org/en-US/``).
        """
        try:
            parsed = urlparse(start_url)
            segments = [s for s in parsed.path.split("/") if s]

            # Need at least 2 segments to have any intermediate ancestors
            if len(segments) < 2:
                return

            timestamp = get_epoch_timestamp_in_ms()
            placeholder_records: List[Tuple[FileRecord, List[Permission]]] = []

            # Build prefix URLs for every segment except the last one
            for i in range(1, len(segments)):
                prefix_path = "/" + "/".join(segments[:i]) + "/"
                ancestor_url = urlunparse(
                    (parsed.scheme, parsed.netloc, prefix_path, "", "", "")
                )
                # external_record_id is always the trailing-slash-normalised URL
                external_id = self._ensure_trailing_slash(ancestor_url)

                # name = URL without scheme  (e.g. "developer.mozilla.org/en-US/")
                record_name = parsed.netloc + prefix_path

                # Resolve parent URL one level up; if it resolves to the netloc root there is no parent record
                parent_url = self._get_parent_url(ancestor_url)
                if parent_url and urlparse(parent_url).path in ("", "/"):
                    parent_url = None

                # Upsert-safe id resolution
                existing = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=external_id,
                )

                # Migration compatibility: old records may have been saved WITHOUT a trailing slash.
                # If not found by the new normalized id, fall back to the legacy (no-slash) form.
                if not existing:
                    legacy_external_id = external_id.rstrip('/')
                    if legacy_external_id != external_id:
                        existing = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=legacy_external_id,
                        )

                        if existing:
                            self.logger.info(
                                f"🔄 Found legacy record (no trailing slash) for {legacy_external_id}, "
                                f"will migrate external_record_id to {external_id}"
                            )

                record_id = existing.id if existing else str(uuid.uuid4())

                file_record = FileRecord(
                    id=record_id,
                    org_id=self.data_entities_processor.org_id,
                    record_name=record_name,
                    record_type=RecordType.FILE,
                    record_group_type=RecordGroupType.WEB,
                    external_record_id=external_id,
                    external_record_group_id=self.url,
                    version=0,
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                    source_created_at=timestamp,
                    source_updated_at=timestamp,
                    weburl=ancestor_url,
                    size_in_bytes=None,
                    is_file=True,
                    extension=None,
                    path=prefix_path,
                    mime_type=MimeTypes.HTML.value,
                    preview_renderable=False,
                    parent_external_record_id=parent_url,
                    parent_record_type=RecordType.FILE if parent_url else None,
                    indexing_status=IndexingStatus.AUTO_INDEX_OFF.value,
                )

                permissions = [
                    Permission(
                        external_id=self.data_entities_processor.org_id,
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                    )
                ]

                placeholder_records.append((file_record, permissions))
                self.logger.info(
                    f"📁 Queued ancestor placeholder: {record_name}"
                )

            if placeholder_records:
                await self.data_entities_processor.on_new_records(placeholder_records)
                self.logger.info(
                    f"✅ Upserted {len(placeholder_records)} ancestor placeholder record(s) "
                    f"for start URL: {start_url}"
                )

        except Exception as e:
            self.logger.error(
                f"❌ Failed to create ancestor placeholder records for {start_url}: {e}",
                exc_info=True,
            )

    async def _crawl_recursive(self, start_url: str, depth: int) -> None:
        """Recursively crawl pages starting from start_url."""
        try:
            # Upsert placeholder WEBPAGE records for every intermediate path
            # segment of the start URL before we begin crawling.
            await self._create_ancestor_placeholder_records(start_url)

            batch_records: List[Tuple[FileRecord, List[Permission]]] = []

            async for record_update in self._crawl_recursive_generator(start_url, depth):

                if record_update.is_updated:
                    await self._handle_record_updates(record_update)
                    continue
                elif record_update.is_new and record_update.record is not None and record_update.new_permissions is not None:
                    entry: Tuple[Record, List[Permission]] = (record_update.record, record_update.new_permissions)
                    batch_records.append(entry)

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
    ) -> AsyncGenerator[RecordUpdate, None]:
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
                + f"(depth {current_depth}): {current_url}"
            )

            try:
                # Fetch and process the page with referer
                record_update = await self._fetch_and_process_url(
                    current_url, current_depth, referer=referer
                )

                if record_update is None:
                    continue

                file_record = record_update.record

                if file_record:
                    self.visited_urls.add(normalized_url)

                    is_disabled = self._check_index_filter(file_record)

                    if is_disabled:
                        file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                    # Extract links if we haven't reached max depth
                    if current_depth < self.max_depth and file_record.mime_type == MimeTypes.HTML.value:
                        links = await self._extract_links_from_content(
                            current_url, record_update.html_bytes, file_record, referer=referer
                        )

                        # Add new links to queue with current URL as referer
                        for link in links:
                            normalized_link = self._normalize_url(link)
                            if (
                                normalized_link not in self.visited_urls
                                and len(self.visited_urls) < self.max_pages
                            ):
                                queue.append((link, current_depth + 1, current_url))

                    yield record_update

            except Exception as e:
                self.logger.warning(f"⚠️ Failed to process {current_url}: {e}")
                continue

            # Small delay to be respectful to the server; also yields control to
            # other async tasks (mirrors the OneDrive generator pattern).
            await asyncio.sleep(0.5)

    async def _fetch_and_process_url(
        self, url: str, depth: int, referer: Optional[str] = None
    ) -> Optional[RecordUpdate]:
        """Fetch URL content using multi-strategy fallback and create a RecordUpdate."""
        try:
            if self.session is None:
                self.logger.error("❌ Session not initialized")
                return None

            result = await fetch_url_with_fallback(
                url=url,
                session=self.session,
                logger=self.logger,
                referer=referer,
                timeout=15,
                max_size_mb=self.max_size_mb,
            )

            if result is None:
                return None

            if result.status_code >= HttpStatusCode.BAD_REQUEST.value:
                # Already logged inside fetch_url_with_fallback
                return None

            is_new = False
            is_updated = False
            is_deleted = False
            metadata_changed = False
            content_changed = False

            content_type = result.headers.get("Content-Type", "").lower()
            final_url = result.final_url
            content_bytes = result.content_bytes

            if len(content_bytes) > self.max_size_mb * 1024 * 1024:
                size_mb = len(content_bytes) / (1024 * 1024)
                self.logger.warning(
                    f"⚠️ Skipping {url}: downloaded size {size_mb:.1f}MB "
                    + f"exceeds limit of {self.max_size_mb:.0f}MB"
                )
                return None

            # Determine MIME type and file extension
            mime_type, extension = self._determine_mime_type(url, content_type)
            if not self._pass_extension_filter(extension):
                return None
            html_bytes = content_bytes if mime_type == MimeTypes.HTML else None

            # Normalize external_id to always end with '/' for extensionless (page) URLs
            # to prevent duplicate records for e.g. /docs and /docs/
            external_id = self._ensure_trailing_slash(final_url)

            record_id = None
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=external_id
            )

            # Migration compatibility: old records may have been saved WITHOUT a trailing slash.
            # If not found by the new normalized id, fall back to the legacy (no-slash) form.
            legacy_lookup = False
            if not existing_record:
                legacy_external_id = external_id.rstrip('/')
                if legacy_external_id != external_id:
                    existing_record = await self.data_entities_processor.get_record_by_external_id(
                        connector_id=self.connector_id, external_record_id=legacy_external_id
                    )
                    if existing_record:
                        legacy_lookup = True
                        self.logger.info(
                            f"🔄 Found legacy record (no trailing slash) for {legacy_external_id}, "
                            f"will migrate external_record_id to {external_id}"
                        )

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            # Get title and clean content for HTML
            title = self._extract_title_from_url(final_url)
            size_in_bytes = len(content_bytes)
            timestamp = get_epoch_timestamp_in_ms()

            # For HTML pages, extract clean content
            if mime_type == MimeTypes.HTML:
                try:
                    soup = BeautifulSoup(content_bytes, "html.parser")
                    title = self._extract_title(soup, final_url)

                    self._remove_unwanted_tags(soup)

                    # Get text content
                    text_content = soup.get_text(separator="\n", strip=True)

                    # Store cleaned HTML for indexing
                    content_bytes = text_content.encode("utf-8")
                    # size_in_bytes = len(content_bytes)

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

            # If the parent resolves to the netloc root there is no parent record
            parent_url = self._get_parent_url(final_url)
            if parent_url and urlparse(parent_url).path in ("", "/"):
                parent_url = None
            
            if existing_record:
                if legacy_lookup:
                    # Force metadata update to migrate external_record_id to the normalized form
                    metadata_changed = True
                elif existing_record.record_name != title:
                    metadata_changed = True
                elif existing_record.parent_external_record_id != parent_url:
                    metadata_changed = True
                if existing_record.external_revision_id != content_md5_hash:
                    content_changed = True
                is_updated = metadata_changed or content_changed
            else:
                is_new = True

            # Create FileRecord
            file_record = FileRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=title,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.WEB,
                external_record_id=external_id,
                external_revision_id=content_md5_hash,
                external_record_group_id=self.url,
                version=0,
                origin=OriginTypes.CONNECTOR,
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
                parent_external_record_id=parent_url,
                parent_record_type=RecordType.FILE if parent_url else None,
            )

            # Create permissions (org-level access for web pages)
            permissions = [
                Permission(
                    external_id=self.data_entities_processor.org_id,
                    type=PermissionType.READ,
                    entity_type=EntityType.ORG,
                )
            ]

            record_update = RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=is_deleted,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=False,
                new_permissions=permissions,
                html_bytes=html_bytes,
            )

            self.logger.debug(
                "✅ Processed: %s (%s, %s bytes) via %s",
                title, mime_type.value, size_in_bytes, result.strategy
            )

            return record_update

        except asyncio.TimeoutError:
            self.logger.warning(f"⚠️ Timeout fetching {url}")
            return None
        except Exception as e:
            self.logger.error(f"❌ Error fetching {url}: {e}", exc_info=True)
            return None

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Handle record updates."""
        if not record_update.record:
            return
        if record_update.is_deleted:
            await self.data_entities_processor.on_record_deleted(record_update.record.id)
        if record_update.metadata_changed:
            await self.data_entities_processor.on_record_metadata_update(record_update.record)
        if record_update.content_changed:
            await self.data_entities_processor.on_record_content_update(record_update.record)

    async def _extract_links_from_content(
        self, base_url: str, html_bytes: Optional[bytes], file_record: FileRecord, referer: Optional[str] = None
    ) -> List[str]:
        """Extract valid links from HTML content."""
        links = []

        try:

            if not html_bytes:
                self.logger.debug(f"no HTML content, fetching from {file_record.weburl}")
                if not self.session or not file_record.weburl:
                    return links

                # Re-fetch using the same multi-strategy fallback used everywhere else
                result = await fetch_url_with_fallback(
                    url=file_record.weburl,
                    session=self.session,
                    logger=self.logger,
                    referer=referer,
                )
                if result is None or result.status_code >= HttpStatusCode.BAD_REQUEST.value:
                    return links

                html_content: bytes = result.content_bytes
            else:
                html_content = html_bytes

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

    def _check_index_filter(self, record: Record) -> bool:
        """Check if the record should be indexed."""
        mime_type = record.mime_type
        is_disabled = False

        if mime_type == MimeTypes.HTML.value:
            is_disabled = not self.indexing_filters.is_enabled(IndexingFilterKey.WEBPAGES, default=True)
        elif mime_type in DOCUMENT_MIME_TYPES:
            is_disabled = not self.indexing_filters.is_enabled(IndexingFilterKey.DOCUMENTS, default=True)
        elif mime_type in IMAGE_MIME_TYPES:
            is_disabled = not self.indexing_filters.is_enabled(IndexingFilterKey.IMAGES, default=True)

        return is_disabled

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

            # Prevent upward path traversal.
            if self.restrict_to_start_path and self.url:
                decoded_path = unquote(parsed.path)
                if not decoded_path.startswith(self.start_path_prefix):
                    return False

            return True

        except Exception:
            return False

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        try:
            parsed = urlparse(url)
            # Remove fragment and normalize
            return urlunparse((
                parsed.scheme,
                parsed.netloc.lower(),
                parsed.path.rstrip('/') or '/',
                parsed.params,
                parsed.query,
                ''  # Remove fragment
            ))
        except Exception:
            return url

    def _determine_mime_type(self, url: str, content_type: str) -> Tuple[MimeTypes, Optional[str]]:
        """Determine MIME type and extension from URL and content-type header."""
        # First, try to get from content-type header
        if content_type:
            content_type_lower = content_type.lower()
            if 'html' in content_type_lower:
                return MimeTypes.HTML, 'html'
            elif 'pdf' in content_type_lower:
                return MimeTypes.PDF, 'pdf'
            elif 'json' in content_type_lower:
                return MimeTypes.JSON, 'json'
            elif 'xml' in content_type_lower:
                return MimeTypes.XML, 'xml'
            elif 'plain' in content_type_lower:
                return MimeTypes.TEXT, 'txt'
            elif 'csv' in content_type_lower:
                return MimeTypes.CSV, 'csv'
            elif 'tab-separated' in content_type_lower or 'tsv' in content_type_lower:
                return MimeTypes.TSV, 'tsv'
            elif 'markdown' in content_type_lower or 'md' in content_type_lower:
                return MimeTypes.MARKDOWN, 'md'
            elif 'mdx' in content_type_lower:
                return MimeTypes.MDX, 'mdx'
            elif 'image/webp' in content_type_lower:
                return MimeTypes.WEBP, 'webp'
            elif 'image/heic' in content_type_lower:
                return MimeTypes.HEIC, 'heic'
            elif 'image/heif' in content_type_lower:
                return MimeTypes.HEIF, 'heif'
            elif 'image/png' in content_type_lower:
                return MimeTypes.PNG, 'png'
            elif 'image/jpeg' in content_type_lower or 'image/jpg' in content_type_lower:
                return MimeTypes.JPEG, 'jpeg'
            elif 'image/gif' in content_type_lower:
                return MimeTypes.GIF, 'gif'
            elif 'image/svg' in content_type_lower:
                return MimeTypes.SVG, 'svg'
            elif 'wordprocessingml' in content_type_lower or 'msword' in content_type_lower:
                if 'openxml' in content_type_lower:
                    return MimeTypes.DOCX, 'docx'
                else:
                    return MimeTypes.DOC, 'doc'
            elif 'spreadsheetml' in content_type_lower or 'ms-excel' in content_type_lower:
                if 'openxml' in content_type_lower:
                    return MimeTypes.XLSX, 'xlsx'
                else:
                    return MimeTypes.XLS, 'xls'
            elif 'presentationml' in content_type_lower or 'ms-powerpoint' in content_type_lower:
                if 'openxml' in content_type_lower:
                    return MimeTypes.PPTX, 'pptx'
                else:
                    return MimeTypes.PPT, 'ppt'
            elif 'zip' in content_type_lower or 'compressed' in content_type_lower:
                return MimeTypes.ZIP, 'zip'

        # Try to get from URL extension
        parsed_url = urlparse(url)
        path = parsed_url.path.lower()

        for ext, mime_type in FILE_MIME_TYPES.items():
            if path.endswith(ext):
                return mime_type, ext.lstrip('.')

        # Default to HTML
        return MimeTypes.HTML, 'html'

    def _pass_extension_filter(self, extension: Optional[str]) -> bool:
        """
        Checks if the file extension passes the configured file extensions filter.

        For MULTISELECT filters:
        - Operator IN: Only allow files with extensions in the selected list
        - Operator NOT_IN: Allow files with extensions NOT in the selected list

        Args:
            extension: File extension (e.g., "pdf", "docx", "html") without leading dot

        Returns:
            True if the extension passes the filter (should be kept), False otherwise
        """
        # 1. Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # 2. Handle files without extensions
        if extension is None or extension == '':
            operator = extensions_filter.get_operator()
            # If using NOT_IN operator, files without extensions pass (not in excluded list)
            # If using IN operator, files without extensions fail (not in allowed list)
            return operator== MultiselectOperator.NOT_IN

        # 3. Normalize extension (lowercase, without dots)
        file_extension = extension.lower().lstrip(".")

        # 4. Get the list of extensions from the filter value
        allowed_extensions = extensions_filter.value
        if not isinstance(allowed_extensions, list):
            return True  # Invalid filter value, allow the file

        # 5. Normalize extensions (lowercase, without dots)
        normalized_extensions = [ext.lower().lstrip(".") for ext in allowed_extensions]

        # 6. Apply the filter based on operator
        operator = extensions_filter.get_operator()

        if operator == MultiselectOperator.IN:
            # Only allow files with extensions in the list
            return file_extension in normalized_extensions
        elif operator == MultiselectOperator.NOT_IN:
            # Allow files with extensions NOT in the list
            return file_extension not in normalized_extensions

        # Unknown operator, default to allowing the file
        return True

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
        if og_title and isinstance(og_title, Tag):
            content = og_title.get('content')
            if isinstance(content, str):
                title = content.strip()
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

    def _ensure_trailing_slash(self, url: str) -> str:
        """Append a trailing slash to extensionless (page) URLs to prevent duplicate records.
        File URLs containing a dot in the last path segment (e.g. /doc.pdf) are returned unchanged.
        """
        try:
            parsed = urlparse(url)
            last_segment = parsed.path.rstrip('/').rsplit('/', 1)[-1]
            if '.' not in last_segment:  # no file extension → treat as a page URL
                path = parsed.path.rstrip('/') + '/'
                return urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))
        except Exception:
            pass
        return url

    def _get_parent_url(self, url: str) -> Optional[str]:
        """Derive the parent URL by stripping the last non-empty path segment.
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip('/')
        if not path or '/' not in path:
            return None  # Already at root, no parent
        parent_path = path.rsplit('/', 1)[0] + '/'
        return urlunparse((parsed.scheme, parsed.netloc, parent_path, '', '', ''))

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

    async def handle_webhook_notification(self, notification: Dict) -> None:  # type: ignore[override]
        """Web connector doesn't support webhooks."""
        pass

    async def get_signed_url(self, record: Record) -> Optional[str]:  # type: ignore[override]
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
            _ = base64.b64decode(cleaned, validate=True)
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
                    _ = base64.b64decode(cleaned_b64, validate=True)
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

    def _remove_unwanted_tags(self, soup: BeautifulSoup) -> None:
        """
        Remove all non-content tags and structural noise (nav, sidebars, etc.)
        """

        # 1. Technical & Invisible Noise
        # These are tags that never contain user-facing article content.
        technical_tags = [
            "script", "style", "noscript", "iframe", "meta",
            "base", "link", "canvas"
        ]

        # 2. Functional/Interactive Noise
        # UI elements that don't represent the text content of the page.
        ui_tags = ["button", "form", "input", "select", "textarea", "label"]

        # 3. Structural/Navigational Noise
        structural_tags = ["nav"]

        # Combine and decompose
        for tag in soup(technical_tags + ui_tags + structural_tags):
            tag.decompose()

        # 4. Common CSS Selectors (Class/ID Noise)
        # These catch elements on sites that don't use semantic tags.
        # Covers sidebars, menus, ads, and common 'skip' links.
        unwanted_selectors = [
            ".sidebar", "#sidebar", ".menu", ".nav", ".navigation",
            ".ads", ".promo", ".banner", ".popup", ".modal",
            ".toc", ".table-of-contents", ".breadcrumb",
            ".pagination", ".share-buttons", ".social-media",
            "a[href='#content']", "a.skip-to-content", ".edit-page-link"
        ]

        for selector in unwanted_selectors:
            for match in soup.select(selector):
                match.decompose()

    def _remove_image_tags(self, soup: BeautifulSoup) -> None:
        """Remove all image and SVG tags from the soup."""
        # Remove all img tags
        for img in soup.find_all('img'):
            img.decompose()

        # Remove all svg tags
        for svg in soup.find_all('svg'):
            svg.decompose()

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
            _ = self._convert_svg_tag_to_png(soup, svg)

    # Image formats supported by OpenAI vision API
    OPENAI_SUPPORTED_IMAGE_TYPES = frozenset({'image/png', 'image/jpeg', 'image/gif', 'image/webp'})
    # Accept header for image requests — deliberately excludes unsupported image types
    _IMAGE_ACCEPT_HEADER = "image/png,image/jpeg,image/gif,image/webp,image/svg+xml,image/*;q=0.8"

    async def _process_single_image(
        self,
        img,
        soup: BeautifulSoup,
        base_url: str,
        headers: dict,
        preferred_strategy: Optional[str] = None
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

            if self.session is None:
                self.logger.warning("⚠️ Session not initialized, skipping image download")
                return

            img_result = await fetch_url_with_fallback(
                url=absolute_url,
                session=self.session,
                logger=self.logger,
                referer=base_url,
                # Override Accept so servers don't return unsupported image types
                extra_headers={"Accept": self._IMAGE_ACCEPT_HEADER},
                preferred_strategy=preferred_strategy,
            )

            if img_result is None or img_result.status_code >= HttpStatusCode.BAD_REQUEST.value:
                self.logger.warning(
                    f"⚠️ Failed to download image: {absolute_url} "
                    + f"(status: {img_result.status_code if img_result else 'N/A'})"
                )
                return

            img_bytes = img_result.content_bytes     # was: await img_response.read()
            if not img_bytes:
                return

            content_type = self._determine_image_content_type(img_result, absolute_url)

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
                raw_ct = img_result.headers.get('Content-Type') or img_result.headers.get('content-type', '<none>')
                self.logger.debug(
                    "⚠️ Unsupported downloaded image — "
                    + f"resolved_content_type='{content_type}' | "
                    + f"raw_Content-Type='{raw_ct}' | "
                    + f"url='{absolute_url}' | "
                    + f"response_status={img_result.status_code} | "
                    + f"content_length={len(img_bytes)} bytes | "
                    + f"response_headers={dict(img_result.headers)} — skipping"
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
        # FetchResponse.headers is a plain dict; header casing varies by strategy
        # (aiohttp → title-case, curl_cffi/cloudscraper → may be lowercase).
        h = response.headers
        content_type = h.get('Content-Type') or h.get('content-type', 'image/jpeg')

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
        headers: dict,
        preferred_strategy: Optional[str] = None
    ) -> None:
        """Process all image tags in the soup."""
        for img in soup.find_all('img'):
            await self._process_single_image(img, soup, base_url, headers, preferred_strategy)

    async def _process_html_content(
        self,
        content_bytes: bytes,
        record: Record,
        headers: dict,
        preferred_strategy: Optional[str] = None
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
            soup = BeautifulSoup(content_bytes, 'html.parser')

            # Remove unwanted tags
            self._remove_unwanted_tags(soup)

            # Check if image indexing is enabled
            images_enabled = self.indexing_filters.get_value(IndexingFilterKey.IMAGES, default=True)
            if images_enabled:
                # Convert SVG tags to PNG img tags
                self._process_svg_tags(soup)

                # Process all images: download and convert to base64
                await self._process_all_images(soup, record.weburl or "", headers, preferred_strategy)
            else:
                self.logger.debug("Removing all image tags: image indexing is disabled")
                # Remove all image and SVG tags when image indexing is disabled
                self._remove_image_tags(soup)

            # Serialize and clean data URIs
            cleaned_html = str(soup)
            return self._clean_data_uris_in_html(cleaned_html)


        except Exception as e:
            self.logger.error(f"⚠️ Failed to parse/clean HTML: {e}")
            raise

    # ==================== Main Stream Record Method ====================

    async def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> Optional[StreamingResponse]:  # type: ignore[override]
        """
        Stream the web page content with proper content extraction.
        """
        if not record.weburl:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Web URL is missing for record {record.record_name} (id:{record.id})",
            )

        try:
            referer = self.url if self.url else None

            if self.session is None:
                raise HTTPException(
                    status_code=500,
                    detail="Session not initialized",
                )

            result = await fetch_url_with_fallback(
                url=record.weburl,
                session=self.session,
                logger=self.logger,
                referer=referer,
            )

            if result is None or result.status_code >= HttpStatusCode.BAD_REQUEST.value:
                raise HTTPException(
                    status_code=result.status_code if result else 502,
                    detail=f"Failed to fetch {record.weburl}",
                )

            content_bytes = result.content_bytes
            headers = {"Referer": self.url} if self.url else {}
            mime_type = record.mime_type or "text/html"

            # Process HTML content
            cleaned_html_content = None
            if "html" in mime_type.lower():
                cleaned_html_content = await self._process_html_content(
                    content_bytes, record, headers, result.strategy
                )

            # Prepare response content
            response_content = (
                cleaned_html_content.encode("utf-8")
                if cleaned_html_content
                else content_bytes
            )

            return create_stream_record_response(
                _bytes_async_gen(response_content),
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

    async def run_incremental_sync(self) -> None:  # type: ignore[override]
        """Run incremental sync (same as full sync for web pages)."""
        await self.run_sync()
