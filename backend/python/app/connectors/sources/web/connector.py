import asyncio
import base64
import hashlib
import random
import re
import uuid
from collections import deque
from dataclasses import dataclass
from enum import Enum
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
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    FILE_MIME_TYPES,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.core.constants import IconPaths
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
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.connectors.sources.web.fetch_strategy import FetchResponse, fetch_url_with_fallback
from app.connectors.sources.web.crawl4ai_fetcher import Crawl4AIFetcher, FetchResult, get_shared_fetcher, release_shared_fetcher, resolve_fetch_status_code
from app.connectors.sources.web.csr_detection import CSR_PROBE_JS, PRE_HYDRATION_INIT_SCRIPT, analyze_rendering
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint, generate_record_sync_point_key
from app.services.notification.types import NotificationSeverity, NotificationType
from app.models.permission import EntityType, Permission, PermissionType
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.utils.api_call import make_api_call
from app.utils.jwt import generate_jwt
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

@dataclass
class CrawlFetchResult:
    """Lightweight result yielded by the BFS generator.

    Contains only what is needed to advance the crawl (link extraction,
    visited-URL tracking). Heavy processing (image downloads, storage upload,
    record building) is deferred to the consumer.
    """
    url: str
    depth: int
    referer: Optional[str]
    fetch_response: FetchResponse

@dataclass
class RetryUrl:
    url: str
    status: str
    status_code: int
    retries: int
    last_attempted: int
    depth: int = 0                  # depth at which the URL was first encountered
    referer: str | None = None   # referer at the time of first attempt

class Status(Enum):
    PENDING = "PENDING"


RETRYABLE_STATUS_CODES = {
    403, 408, 429,
    500, 502, 503, 504,
    999,
    520, 522, 524, 525, 529,
}

# Base and cap (seconds) for the exponential back-off between attempts.
_BACKOFF_BASE = 15.0
_BACKOFF_CAP = 300.0
MAX_RETRIES = 2

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
    MimeTypes.PLAIN_TEXT.value,
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
        .with_icon(IconPaths.connector_icon(Connectors.WEB.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Web Connector Guide",
            "https://docs.pipeshub.com/connectors/overview",
            "setup"
        ))
        .with_scheduled_config(True, 1440)  # Daily sync
        .add_sync_custom_field(CustomField(
            name="url",
            display_name="Website URL",
            field_type="URL",
            required=True,
            description="The URL of the website to crawl (e.g., https://example.com). Can't be changed later.",
            non_editable=True,
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
            max_length=10000,
            description="Maximum number of pages to crawl (1-10,000)"
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
            default_value="false",
            description="Only crawl URLs within the same path as the starting URL (prevents crawling parent directories)"
        ))
        .add_sync_custom_field(CustomField(
            name="url_should_contain",
            display_name="URL Should Contain",
            field_type="TAGS",
            required=False,
            default_value=[],
            description="Sync only pages whose URL contains these strings; others are skipped. Leave empty to sync all pages."
        ))
        .add_sync_custom_field(CustomField(
            name="use_headless_browser",
            display_name="Robust Mode (slower)",
            field_type="BOOLEAN",
            required=False,
            default_value="false",
            description=(
                "Use a real Chromium browser to fetch pages. "
                "Recommended for JavaScript-heavy or bot-protected sites — "
                "without this, some pages may not be indexed properly."
            )
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
        connector_id: str,
        scope: str,
        created_by: str
    ) -> None:
        super().__init__(
            WebApp(connector_id), logger, data_entities_processor, data_store_provider, config_service, connector_id, scope, created_by
        )
        self.connector_name = Connectors.WEB
        self.connector_id = connector_id

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        # Configuration
        self.url: Optional[str] = None
        self.crawl_type: str = "single"
        self.max_depth: int = 3
        self.max_pages: int = 100
        self.follow_external: bool = False
        self.restrict_to_start_path: bool = False
        self.start_path_prefix: str = "/"
        self.url_should_contain: List[str] = []

        # Crawling state
        self.visited_urls: Set[str] = set()
        self.retry_urls: dict[str, RetryUrl] = {}
        self.processed_urls: int = 0
        self.base_domain: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.full_sync: bool = False
        self.use_headless_browser: bool = False
        self.crawl4ai_fetcher: Optional[Crawl4AIFetcher] = None

        # Batch processing
        self.batch_size: int = 10

        # Filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    async def init(self) -> bool:
        """Initialize the web connector with configuration."""
        try:
            config_values = await self._fetch_and_parse_config(use_cache=False)

            self.url = config_values["url"]
            self.crawl_type = config_values["crawl_type"]
            self.max_depth = config_values["max_depth"]
            self.max_pages = config_values["max_pages"]
            self.max_size_mb = config_values["max_size_mb"]
            self.follow_external = config_values["follow_external"]
            self.base_domain = config_values["base_domain"]
            self.restrict_to_start_path = config_values["restrict_to_start_path"]
            self.start_path_prefix = config_values["start_path_prefix"]
            self.url_should_contain = config_values["url_should_contain"]
            self.use_headless_browser = config_values["use_headless_browser"]

            # Load creator email if needed (for personal scope permission creation)
            await self._load_creator_email()

            # Initialize aiohttp session with realistic browser headers
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Cache-Control": "max-age=0",
                    "sec-ch-ua": '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"macOS"',
                }
            )

            if self.use_headless_browser:
                self.crawl4ai_fetcher = await get_shared_fetcher()
            elif self.url:
                if await self._detect_csr(self.url):
                    self.use_headless_browser = True
                    self.crawl4ai_fetcher = await get_shared_fetcher()

            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize web connector: {e}", exc_info=True)
            return False

    async def _fetch_and_parse_config(self, use_cache: bool = False) -> Dict:
        """
        Fetch and parse connector configuration.

        Args:
            use_cache: Whether to use cached config (default: False)

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

            crawl_type = sync_config.get("type", "single")
            max_depth = int(sync_config.get("depth") or 3)
            max_pages = int(sync_config.get("max_pages") or 1000)
            max_size_mb = int(sync_config.get("max_size_mb") or 10)
            follow_external = sync_config.get("follow_external", False)
            restrict_to_start_path = sync_config.get("restrict_to_start_path", False)
            # Accept both a legacy plain string and the new list-of-strings format.
            _usc_raw = sync_config.get("url_should_contain", [])
            if isinstance(_usc_raw, list):
                url_should_contain = [s for s in _usc_raw if isinstance(s, str) and s.strip()]
            else:
                self.logger.warning("⚠️ WebPage url_should_contain is not a list, setting to empty list: %s", _usc_raw)
                url_should_contain = []
            _uhb_raw = sync_config.get("use_headless_browser", False)
            use_headless_browser = _uhb_raw if isinstance(_uhb_raw, bool) else str(_uhb_raw).lower() == "true"

            # restrict_to_start_path implies staying on the starting domain,
            # so follow_external must be False — override with a warning.
            if restrict_to_start_path and follow_external:
                self.logger.warning(
                    "⚠️ 'restrict_to_start_path' is enabled — overriding 'follow_external' to False "
                    "(cannot follow external links while restricting to the start path)"
                )
                follow_external = False

            # Validate max_pages and max_depth
            if max_pages > 10000:
                self.logger.warning("⚠️ WebPage max_pages is greater than 10000, setting to 10000")
                max_pages = 10000
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

            # Compute the path prefix for restrict_to_start_path.
            # Strip any trailing slash then re-add one, so both
            # "/globalprotect" and "/globalprotect/" produce "/globalprotect/".
            # Comparisons are also normalised (strip trailing slash, then add
            # one) so "/globalprotect" (the start URL itself) is accepted.
            start_path_prefix = parsed_url.path.rstrip('/') + '/'

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
                "url_should_contain": url_should_contain,
                "use_headless_browser": use_headless_browser,
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
                await self.notify(
                    type=NotificationType.CONNECTOR_NOT_ACCESSIBLE,
                    severity=NotificationSeverity.ERROR,
                    title=f"Website not accessible",
                    message=f"Website {self.url} is not accessible.",
                )
                return False

            if result.status_code < HttpStatusCode.BAD_REQUEST.value:
                return True
            else:
                self.logger.warning(
                    f"⚠️ Website returned status {result.status_code}: {self.url}"
                )
                await self.notify(
                    type=NotificationType.CONNECTOR_NOT_ACCESSIBLE,
                    severity=NotificationSeverity.ERROR,
                    title=f"Website not accessible",
                    message=f"Website {self.url} returned status {result.status_code}",
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

            # Create READ permissions: TEAM scope uses org; PERSONAL uses app_users
            if not app_users and self.scope == ConnectorScope.TEAM.value:
                permissions = [
                    Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id,
                    )
                ]
            else:
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

        except Exception as e:
            self.logger.error(f"❌ Failed to create record group: {e}", exc_info=True)
            raise

    async def reload_config(self) -> None:
        """Reload the connector configuration."""
        try:
            config_values = await self._fetch_and_parse_config(use_cache=False)

            new_url =  config_values["url"]
            new_crawl_type = config_values["crawl_type"]
            new_max_depth = config_values["max_depth"]
            new_max_pages = config_values["max_pages"]
            new_max_size_mb = config_values["max_size_mb"]
            new_follow_external = config_values["follow_external"]
            new_base_domain = config_values["base_domain"]
            new_restrict_to_start_path = config_values["restrict_to_start_path"]
            new_start_path_prefix = config_values["start_path_prefix"]

            if self.url is not None and new_url.lower() != self.url.lower():
                self.logger.error(f"❌ Cannot change URL from {self.url} to {new_url}. Please create a new connector for {new_url}")
                raise ValueError("Cannot change URL for web connector.")
            if new_base_domain != self.base_domain:
                self.logger.error(f"❌ Cannot change base domain from {self.base_domain} to {new_base_domain}. Please create a new connector for {new_base_domain}")
                raise ValueError("Cannot change base domain for web connector.")

            if new_crawl_type != self.crawl_type:
                self.crawl_type = new_crawl_type
            if new_max_depth != self.max_depth:
                self.max_depth = new_max_depth
            if new_max_pages != self.max_pages:
                self.max_pages = new_max_pages
            if new_max_size_mb != self.max_size_mb:
                self.max_size_mb = new_max_size_mb
            if new_follow_external != self.follow_external:
                self.follow_external = new_follow_external
            if new_restrict_to_start_path != self.restrict_to_start_path:
                self.restrict_to_start_path = new_restrict_to_start_path
                self.start_path_prefix = new_start_path_prefix

            new_url_should_contain = config_values["url_should_contain"]
            if new_url_should_contain != self.url_should_contain:
                self.url_should_contain = new_url_should_contain

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

            self.logger.info("Starting web crawl: %s", self.url)

            sync_point_key = generate_record_sync_point_key(
                RecordType.WEBPAGE.value,
                "webpages",
                self.url
            )

            sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
            if not sync_point:
                self.full_sync = True

            if self.scope == ConnectorScope.TEAM.value:
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.ensure_team_app_edge(
                        self.connector_id,
                        self.data_entities_processor.org_id,
                    )
                app_users = []
            else:
                # Personal: create user-app edge only for the creator
                if self.created_by:
                    creator_user = await self.data_entities_processor.get_user_by_user_id(self.created_by)
                    if creator_user and getattr(creator_user, "email", None):
                        app_users = self.get_app_users([creator_user])
                        await self.data_entities_processor.on_new_app_users(app_users)
                    else:
                        self.logger.warning(
                            "Creator user not found or has no email for created_by %s; skipping user-app edges.",
                            self.created_by,
                        )
                        app_users = []
                else:
                    self.logger.warning(
                        "Personal connector has no created_by; skipping user-app edges."
                    )
                    app_users = []

            # Step 2: create record group with permissions
            await self.create_record_group(app_users)

            # Reset state for new sync
            self.visited_urls.clear()
            self.retry_urls.clear()
            self.processed_urls = 0

            # Start crawling
            assert self.url is not None, "URL not set — init() must be called first"
            if self.crawl_type == "recursive":
                await self._crawl_recursive(self.url, depth=0)
            elif self.crawl_type in ("single", None, ""):
                await self._crawl_single_page(self.url)
            else:
                self.logger.warning(f"Unknown crawl type {self.crawl_type!r}; skipping crawl")

            #fetch urls with retryable errors
            await self.process_retry_urls()

            #update sync point
            await self.record_sync_point.update_sync_point(
                sync_point_key,
                {
                    "timestamp": get_epoch_timestamp_in_ms()
                }
            )
            self.full_sync = False

            self.logger.info(
                "Web crawl completed: %d pages crawled, %d pages processed, %d pages failed",
                len(self.visited_urls),
                self.processed_urls,
                len(self.retry_urls),
            )

            if len(self.retry_urls) > 0:
                await self.notify(
                    type=NotificationType.CONNECTOR_INFO,
                    severity=NotificationSeverity.INFO,
                    title=f"Web crawl completed",
                    message=f"Failed to crawl {len(self.retry_urls)} pages.\nCrawled {len(self.visited_urls)} pages.\nProcessed {self.processed_urls} pages.",
                )
            else:
                await self.notify(
                    type=NotificationType.CONNECTOR_INFO,
                    severity=NotificationSeverity.INFO,
                    title=f"Web crawl completed",
                    message=f"Added {self.processed_urls} pages.",
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
                    file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                if record_update.is_updated:
                    await self._handle_record_updates(record_update)
                elif record_update.is_new and record_update.record is not None and record_update.new_permissions is not None:
                    pair: Tuple[Record, List[Permission]] = (record_update.record, record_update.new_permissions)
                    await self.data_entities_processor.on_new_records([pair])
                    self.processed_urls += 1
                elif self.full_sync and record_update.record is not None:
                    await self.data_entities_processor.on_updated_record_permissions(record_update.record, record_update.new_permissions)
                    self.processed_urls += 1

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

                # Resolve parent URL one level up (returns None when parent would be the domain root)
                parent_url = self._get_parent_url(ancestor_url)

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
                    origin=OriginTypes.CONNECTOR,
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
                    is_internal=True,
                    parent_external_record_id=parent_url,
                    parent_record_type=RecordType.FILE if parent_url else None,
                    indexing_status=ProgressStatus.NOT_STARTED.value,
                )

                permissions = []

                placeholder_records.append((file_record, permissions))

            if placeholder_records:
                await self.data_entities_processor.on_new_records(placeholder_records)

        except ValueError as e:
            # Raised by urlparse/urlunparse when start_url is structurally invalid
            self.logger.error(
                f"❌ Invalid URL while building ancestor placeholders for {start_url}: {e}",
                exc_info=True,
            )
        except Exception as e:
            # Covers data-store transaction failures and Kafka messaging errors
            self.logger.error(
                f"❌ Persistence error while upserting ancestor placeholder records for {start_url}: {e}",
                exc_info=True,
            )

    async def _crawl_recursive(self, start_url: str, depth: int) -> None:
        """Recursively crawl pages starting from start_url.

        The BFS generator runs as a separate task, pushing lightweight
        CrawlFetchResult items into an asyncio.Queue. The consumer pulls
        from the queue and does the heavy per-page work (image downloads,
        storage upload, record building). Because both sides are independent
        tasks, the generator can fetch the next batch while the consumer is
        still processing images from the current one.
        """
        try:
            await self._create_ancestor_placeholder_records(start_url)

            result_queue: asyncio.Queue[Optional[CrawlFetchResult]] = asyncio.Queue(maxsize=self.batch_size * 2)
            producer_error: Optional[BaseException] = None

            async def _produce() -> None:
                nonlocal producer_error
                try:
                    async for crawl_result in self._crawl_recursive_generator(start_url, depth):
                        await result_queue.put(crawl_result)
                except Exception as exc:
                    producer_error = exc
                finally:
                    await result_queue.put(None)

            producer_task = asyncio.create_task(_produce())

            batch_records: List[Tuple[FileRecord, List[Permission]]] = []

            try:
                while True:
                    crawl_result = await result_queue.get()
                    if crawl_result is None:
                        break

                    try:
                        record_update = await self._fetch_and_process_url(
                            crawl_result.url,
                            crawl_result.depth,
                            referer=crawl_result.referer,
                            prefetched_result=crawl_result.fetch_response,
                        )
                    except Exception as e:
                        self.logger.warning(
                            "⚠️ Failed to process %s: %s", crawl_result.url, e
                        )
                        continue

                    if record_update is None:
                        continue

                    file_record = record_update.record
                    if file_record:
                        is_disabled = self._check_index_filter(file_record)
                        if is_disabled:
                            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    if record_update.is_updated:
                        await self._handle_record_updates(record_update)
                        self.processed_urls += 1
                    elif record_update.is_new and record_update.record is not None and record_update.new_permissions is not None:
                        entry: Tuple[Record, List[Permission]] = (record_update.record, record_update.new_permissions)
                        batch_records.append(entry)

                        if len(batch_records) >= self.batch_size:
                            await self.data_entities_processor.on_new_records(batch_records)
                            self.processed_urls += len(batch_records)
                            batch_records.clear()
                    elif self.full_sync and record_update.record is not None:
                        await self.data_entities_processor.on_updated_record_permissions(record_update.record, record_update.new_permissions)
                        self.processed_urls += 1
            finally:
                if not producer_task.done():
                    producer_task.cancel()
                    try:
                        await producer_task
                    except (asyncio.CancelledError, Exception):
                        pass

            if producer_error is not None:
                raise producer_error

            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)
                self.processed_urls += len(batch_records)

        except Exception as e:
            self.logger.error(f"❌ Error in recursive crawl: {e}", exc_info=True)
            raise

    async def _crawl_recursive_generator(
        self, start_url: str, depth: int
    ) -> AsyncGenerator[CrawlFetchResult, None]:
        """BFS crawl generator; yields one CrawlFetchResult per successfully fetched page.

        The generator is intentionally kept lightweight: it only fetches,
        validates, and extracts links. All heavy per-page work (image downloads,
        storage upload, record building) is deferred to the consumer
        (_crawl_recursive) so the BFS queue is never blocked by I/O.
        """
        queue: deque[Tuple[str, int, Optional[str]]] = deque([(start_url, depth, None)])

        while (queue or self.retry_urls) and len(self.visited_urls) < self.max_pages:
            if not queue:
                # Re-enqueue retry candidates that haven't hit the max-retry limit.
                # Exhausted entries are left for process_retry_urls() at the end.
                retry_candidates = [
                    r for r in self.retry_urls.values() if r.retries < MAX_RETRIES
                ]
                if not retry_candidates:
                    break

                min_retries = min(r.retries for r in retry_candidates)
                backoff = min(_BACKOFF_BASE * (2 ** min_retries), _BACKOFF_CAP)
                self.logger.info(
                    "Backing off %.0fs before retrying %d URL(s)",
                    backoff, len(retry_candidates),
                )
                await asyncio.sleep(backoff)

                for retry_entry in retry_candidates:
                    normalized = self._normalize_url(retry_entry.url)
                    if normalized not in self.visited_urls:
                        queue.append((retry_entry.url, retry_entry.depth, retry_entry.referer))

                continue

            if self.use_headless_browser and self.crawl4ai_fetcher:
                # Headless batch path: fetch a batch concurrently, extract links
                # from raw HTML immediately, then yield each validated result.
                batch: list[tuple[str, int, Optional[str]]] = []
                batch_seen: set[str] = set()
                while queue and len(batch) < self.batch_size:
                    if len(self.visited_urls) + len(batch_seen) >= self.max_pages:
                        break
                    candidate_url, candidate_depth, candidate_referer = queue.popleft()
                    norm = self._normalize_url(candidate_url)
                    if norm in self.visited_urls or norm in batch_seen:
                        continue
                    if norm in self.retry_urls and self.retry_urls[norm].retries >= MAX_RETRIES:
                        continue
                    if candidate_depth > self.max_depth:
                        continue
                    batch.append((candidate_url, candidate_depth, candidate_referer))
                    batch_seen.add(norm)
                if not batch:
                    continue

                fetch_responses = await self._headless_fetch_many(
                    [u for u, _, _ in batch]
                )
                fetch_responses = await self._retry_rate_limited(batch, fetch_responses)

                for (current_url, current_depth, referer), raw_result in zip(batch, fetch_responses):
                    normalized_url = self._normalize_url(current_url)
                    try:
                        result = await self._validate_fetch_result(
                            current_url, current_depth, referer, raw_result
                        )

                        if normalized_url not in self.retry_urls:
                            self.visited_urls.add(normalized_url)

                        if result is None:
                            continue

                        # Extract links from raw HTML immediately so the queue
                        # is populated before the next batch fetch.
                        if current_depth < self.max_depth and result.content_bytes:
                            try:
                                for link in self._extract_links_from_html(
                                    current_url, result.content_bytes
                                ):
                                    normalized_link = self._normalize_url(link)
                                    if (
                                        normalized_link not in self.visited_urls
                                        and normalized_link not in self.retry_urls
                                        and len(self.visited_urls) < self.max_pages
                                    ):
                                        queue.append((link, current_depth + 1, current_url))
                            except Exception:
                                pass

                        yield CrawlFetchResult(
                            url=current_url,
                            depth=current_depth,
                            referer=referer,
                            fetch_response=result,
                        )

                    except Exception as e:
                        self.logger.warning("⚠️ Failed to process %s: %s", current_url, e)

            else:
                current_url, current_depth, referer = queue.popleft()

                normalized_url = self._normalize_url(current_url)
                if normalized_url in self.visited_urls:
                    continue
                if normalized_url in self.retry_urls:
                    if self.retry_urls[normalized_url].retries >= MAX_RETRIES:
                        continue

                if current_depth > self.max_depth:
                    continue

                try:
                    if self.session is None:
                        self.logger.error("❌ Session not initialized")
                        continue

                    raw_result = await fetch_url_with_fallback(
                        url=current_url,
                        session=self.session,
                        logger=self.logger,
                        referer=referer,
                        timeout=15,
                        max_size_mb=self.max_size_mb,
                    )

                    result = await self._validate_fetch_result(
                        current_url, current_depth, referer, raw_result
                    )

                    if normalized_url not in self.retry_urls:
                        self.visited_urls.add(normalized_url)

                    if result is None:
                        continue

                    if current_depth < self.max_depth and result.content_bytes:
                        try:
                            for link in self._extract_links_from_html(
                                current_url, result.content_bytes
                            ):
                                normalized_link = self._normalize_url(link)
                                if (
                                    normalized_link not in self.visited_urls
                                    and normalized_link not in self.retry_urls
                                    and len(self.visited_urls) < self.max_pages
                                ):
                                    queue.append((link, current_depth + 1, current_url))
                        except Exception:
                            pass

                    yield CrawlFetchResult(
                        url=current_url,
                        depth=current_depth,
                        referer=referer,
                        fetch_response=result,
                    )

                except Exception as e:
                    self.logger.warning("⚠️ Failed to process %s: %s", current_url, e)
                    continue


    def _is_rate_limited(
        self,
        response: Optional[FetchResponse],
    ) -> bool:
        if response is None:
            return False
        return self._is_rate_limited_status(response.status_code, response.error_message)

    def _is_rate_limited_status(
        self,
        status_code: int,
        error_message: Optional[str] = None,
    ) -> bool:
        if status_code in {
            HttpStatusCode.TOO_MANY_REQUESTS.value,   # 429
            HttpStatusCode.SERVICE_UNAVAILABLE.value,  # 503
            HttpStatusCode.FORBIDDEN.value,            # 403 — WAF/bot-block, treat as rate-limit for backoff
        }:
            return True
        if not error_message:
            return False
        lower = error_message.lower()
        return (
            "429" in lower or "too many requests" in lower
            or "503" in lower or "service unavailable" in lower
            or "403" in lower or "forbidden" in lower
        )

    # ------------------------------------------------------------------
    # CSR (client-side rendering) detection
    # ------------------------------------------------------------------

    async def _detect_csr(self, url: str) -> bool:
        """Detect whether *url* is client-side rendered.

        Uses a single headless-browser fetch with two ``innerText``
        snapshots — one captured at ``DOMContentLoaded`` (before JS
        hydration) via an init script, and one after the page is fully
        rendered.  Both snapshots respect CSS visibility, avoiding false
        results from CSS-hidden elements.
        """
        try:
            probe = Crawl4AIFetcher(
                concurrency=1,
                js_code=CSR_PROBE_JS,
                init_scripts=[PRE_HYDRATION_INIT_SCRIPT],
            )
            await probe.start()
            try:
                rendered = await probe.fetch(url)
            finally:
                await probe.close()

            if not rendered.success or not (rendered.html or "").strip():
                return False

            js_result = rendered.js_execution_result or {}
            pre_len = js_result.get("preLen", -1)
            post_len = js_result.get("postLen", 0)

            if pre_len < 0:
                self.logger.debug(
                    "🔍 CSR probe for %s: init script did not fire, "
                    "falling back to SSR assumption",
                    url,
                )
                return False

            analysis = analyze_rendering(pre_len, post_len)

            if analysis.is_csr:
                self.logger.info(
                    "🔍 CSR detected for %s (verdict=%s, confidence=%s, "
                    "pre_text=%d, post_text=%d, text_ratio=%.2f) "
                    "— auto-enabling headless browser",
                    url, analysis.verdict, analysis.confidence,
                    analysis.raw_text_length, analysis.rendered_text_length,
                    analysis.text_ratio,
                )
            else:
                self.logger.debug(
                    "🔍 CSR probe for %s: verdict=%s, confidence=%s, "
                    "pre_text=%d, post_text=%d, text_ratio=%.2f",
                    url, analysis.verdict, analysis.confidence,
                    analysis.raw_text_length, analysis.rendered_text_length,
                    analysis.text_ratio,
                )
            return analysis.is_csr

        except Exception as e:
            self.logger.warning("⚠️ CSR detection failed for %s: %s", url, e)
            return False

    def _crawl4ai_result_to_response(self, fetch_result: FetchResult, url: str) -> Optional[FetchResponse]:
        status_code = resolve_fetch_status_code(
            fetch_result.status_code,
            fetch_result.error,
        ) or 503
        if self._is_rate_limited_status(status_code, fetch_result.error):
            status_code = HttpStatusCode.TOO_MANY_REQUESTS.value

        if not fetch_result.success or not (fetch_result.html or "").strip():
            return FetchResponse(
                status_code=status_code,
                content_bytes=b"",
                headers={},
                final_url=url,
                strategy="crawl4ai",
                success=False,
                error_message=fetch_result.error,
            )
        return FetchResponse(
            status_code=status_code,
            content_bytes=fetch_result.html.encode("utf-8"),
            headers={},
            final_url=fetch_result.url,
            strategy="crawl4ai",
        )

    async def _headless_fetch(self, url: str) -> Optional[FetchResponse]:
        """Fetch a single URL via crawl4ai (used outside the BFS crawl loop)."""
        if self.crawl4ai_fetcher is None:
            return None
        result = await self.crawl4ai_fetcher.fetch(url)
        return self._crawl4ai_result_to_response(result, url)

    async def _headless_fetch_many(self, urls: list[str]) -> list[Optional[FetchResponse]]:
        """Fetch a batch of URLs via crawl4ai concurrently."""
        assert self.crawl4ai_fetcher is not None
        results = await self.crawl4ai_fetcher.fetch_many(urls)
        return [self._crawl4ai_result_to_response(r, url) for r, url in zip(results, urls)]

    async def _retry_rate_limited(
        self,
        batch: list[tuple[str, int, Optional[str]]],
        responses: list[Optional[FetchResponse]],
    ) -> list[Optional[FetchResponse]]:
        """Re-fetch any rate-limited/bot-blocked responses with exponential backoff, leaving others untouched."""
        rate_limited_indices = [
            i for i, r in enumerate(responses)
            if self._is_rate_limited(r)
        ]
        if not rate_limited_indices:
            return responses

        results = list(responses)
        pending = rate_limited_indices
        delay = _BACKOFF_BASE

        while pending and delay <= _BACKOFF_CAP:
            self.logger.warning(
                "Rate-limited/bot-blocked on %d URL(s), backing off %.0fs before retry",
                len(pending), delay,
            )
            await asyncio.sleep(delay)

            still_limited: list[int] = []
            for batch_idx in pending:
                url = batch[batch_idx][0]
                new_resp = await self._headless_fetch(url)
                if self._is_rate_limited(new_resp):
                    still_limited.append(batch_idx)
                else:
                    results[batch_idx] = new_resp

            pending = still_limited
            delay *= 2

        if pending:
            self.logger.warning(
                "Giving up on %d URL(s) still rate-limited after backoff cap",
                len(pending),
            )

        return results

    async def _validate_fetch_result(
        self,
        url: str,
        depth: int,
        referer: str | None,
        result: Optional[FetchResponse],
    ) -> Optional[FetchResponse]:
        """Validate a fetch response and update retry state.

        Handles connection failures, domain-boundary redirects, url_should_contain
        filtering, HTTP error codes, and size/MIME checks. Returns the validated
        response on success, or None if the URL should be skipped.

        Side-effects: may mutate self.retry_urls and self.visited_urls.
        """
        if result is None:
            normalized = self._normalize_url(url)
            existing_entry = self.retry_urls.get(normalized)
            self.retry_urls[normalized] = RetryUrl(
                url=normalized,
                status=Status.PENDING.value,
                status_code=existing_entry.status_code if existing_entry else 408,
                retries=(existing_entry.retries + 1) if existing_entry else 0,
                last_attempted=get_epoch_timestamp_in_ms(),
                depth=depth,
                referer=referer,
            )
            return None

        final_url = result.final_url

        if self.base_domain and not self.follow_external:
            final_netloc = urlparse(final_url).netloc
            base_netloc = urlparse(self.base_domain).netloc
            if final_netloc.lower() != base_netloc.lower():
                return None

        if self.url_should_contain:
            is_start_url = self._normalize_url(final_url) == self._normalize_url(self.url or "")
            if not is_start_url:
                final_url_lower = final_url.lower()
                matched = any(s.lower() in final_url_lower for s in self.url_should_contain)
                if not matched:
                    final_url_normalized = self._normalize_url(final_url)
                    current_url_normalized = self._normalize_url(url)
                    if final_url_normalized != current_url_normalized:
                        self.visited_urls.add(final_url_normalized)
                    return None

        if result.status_code >= HttpStatusCode.BAD_REQUEST.value:
            if result.status_code in RETRYABLE_STATUS_CODES:
                normalized = self._normalize_url(url)
                existing_entry = self.retry_urls.get(normalized)
                self.retry_urls[normalized] = RetryUrl(
                    url=normalized,
                    status=Status.PENDING.value,
                    status_code=result.status_code,
                    retries=(existing_entry.retries + 1) if existing_entry else 0,
                    last_attempted=get_epoch_timestamp_in_ms(),
                    depth=depth,
                    referer=referer,
                )
            return None
        elif not result.success:
            normalized = self._normalize_url(url)
            existing_entry = self.retry_urls.get(normalized)
            self.retry_urls[normalized] = RetryUrl(
                url=normalized,
                status=Status.PENDING.value,
                status_code=result.status_code,
                retries=(existing_entry.retries + 1) if existing_entry else 0,
                last_attempted=get_epoch_timestamp_in_ms(),
                depth=depth,
                referer=referer,
            )
            self.logger.warning(
                "⚠️ Fetch returned status %d but marked unsuccessful for %s: %s — will retry",
                result.status_code, url, result.error_message or "unknown error",
            )
            return None
        else:
            normalized_url = self._normalize_url(url)
            if normalized_url in self.retry_urls:
                self.retry_urls.pop(normalized_url, None)

        content_bytes = result.content_bytes
        if len(content_bytes) > self.max_size_mb * 1024 * 1024:
            return None

        content_type = result.headers.get("Content-Type", "").lower()
        _, extension = self._determine_mime_type(url, content_type)
        if not self._pass_extension_filter(extension):
            return None

        return result

    async def _fetch_and_process_url(
        self, url: str, depth: int, referer: str | None = None,
        prefetched_result: Optional[FetchResponse] = None,
    ) -> Optional[RecordUpdate]:
        """Build a RecordUpdate from a validated fetch response.

        When *prefetched_result* is supplied it is assumed to have already
        passed through ``_validate_fetch_result``; validation is skipped and
        processing begins immediately.
        """
        try:
            if self.session is None:
                self.logger.error("❌ Session not initialized")
                return None

            if prefetched_result is not None:
                result = prefetched_result
            else:
                if self.use_headless_browser and self.crawl4ai_fetcher:
                    raw = await self._headless_fetch(url)
                else:
                    raw = await fetch_url_with_fallback(
                        url=url,
                        session=self.session,
                        logger=self.logger,
                        referer=referer,
                        timeout=15,
                        max_size_mb=self.max_size_mb,
                    )
                result = await self._validate_fetch_result(url, depth, referer, raw)
                if result is None:
                    return None

            final_url = result.final_url

            is_new = False
            is_updated = False
            is_deleted = False
            metadata_changed = False
            content_changed = False

            content_type = result.headers.get("Content-Type", "").lower()
            content_bytes = result.content_bytes

            # Determine MIME type and file extension
            mime_type, extension = self._determine_mime_type(url, content_type)
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

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            # Get title and clean content for HTML
            title = self._extract_title_from_url(final_url)
            size_in_bytes = len(content_bytes)
            timestamp = get_epoch_timestamp_in_ms()

            # For HTML pages, extract title and produce fully processed content
            processed_content_bytes: Optional[bytes] = None
            if mime_type == MimeTypes.HTML:
                try:
                    soup = BeautifulSoup(content_bytes, "html.parser")
                    title = self._extract_title(soup, final_url)

                    headers_for_images = {"Referer": self.url} if self.url else {}
                    strategy = None if (result.strategy == "crawl4ai") else result.strategy
                    cleaned_html = await self._process_html_content(
                        content_bytes, final_url, headers_for_images, strategy
                    )
                    if cleaned_html:
                        processed_content_bytes = cleaned_html.encode("utf-8")

                    # Text-only hash for change detection (consistent with previous behaviour)
                    self._remove_unwanted_tags(soup)
                    text_content = soup.get_text(separator="\n", strip=True)
                    content_bytes = text_content.encode("utf-8")

                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to parse HTML for {url}: {e}")

            # Calculate MD5 hash once (on text content for HTML, raw bytes otherwise)
            content_md5_hash = hashlib.md5(content_bytes).hexdigest()

            # Ensure title is never empty (schema requirement)
            if not title or not title.strip():
                title = self._extract_title_from_url(final_url)
                # Final fallback: use URL if title extraction still fails
                if not title or not title.strip():
                    parsed = urlparse(final_url)
                    title = parsed.netloc or final_url

            # Resolve parent URL (returns None when parent would be the domain root)
            parent_url = self._get_parent_url(final_url)

            await self._ensure_parent_records_exist(parent_url)

            if existing_record:
                if legacy_lookup:
                    is_new = True # Force record to be treated as new to migrate external_record_id to the normalized form
                else:
                    if existing_record.record_name != title:
                        metadata_changed = True
                    elif existing_record.parent_external_record_id != parent_url:
                        metadata_changed = True
                    if existing_record.external_revision_id != content_md5_hash:
                        content_changed = True
                    is_updated = metadata_changed or content_changed
            else:
                is_new = True

            # Upload processed content to storage
            upload_bytes = processed_content_bytes if processed_content_bytes else content_bytes
            existing_storage_doc_id = (
                existing_record.storage_document_id if existing_record else None
            )

            # Only upload when content is new or changed
            storage_document_id: Optional[str] = None
            if is_new or content_changed or (existing_record and not existing_storage_doc_id):
                storage_document_id = await self._store_crawled_content(
                    content=upload_bytes,
                    record_name=title,
                    extension=extension or "html",
                    mime_type=mime_type.value,
                    existing_storage_doc_id=existing_storage_doc_id,
                )
                if not storage_document_id:
                    self.logger.warning("Failed to store content for %s, indexing will fall back to live fetch", url)
            else:
                storage_document_id = existing_storage_doc_id

            # Build the signed URL route for the indexer to download from storage
            fetch_signed_url: Optional[str] = None
            if storage_document_id:
                try:
                    storage_url = await self._get_storage_url()
                    fetch_signed_url = f"{storage_url}/api/v1/document/internal/{storage_document_id}/download"
                except Exception:
                    pass

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
                storage_document_id=storage_document_id,
                fetch_signed_url=fetch_signed_url,
            )

            if existing_record and not content_changed:
                file_record.parsing_status = existing_record.parsing_status
                file_record.indexing_status = existing_record.indexing_status
                file_record.extraction_status = existing_record.extraction_status

            permissions = []

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

    def _extract_links_from_html(
        self, base_url: str, html_bytes: bytes,
    ) -> List[str]:
        """Extract valid outbound links from raw HTML bytes."""
        links: List[str] = []
        soup = BeautifulSoup(html_bytes, "html.parser")
        for anchor in soup.find_all("a", href=True):
            absolute_url = urljoin(base_url, anchor["href"])
            if self._is_valid_url(absolute_url, base_url):
                links.append(absolute_url)
        return links

    async def _extract_links_from_content(
        self, base_url: str, html_bytes: Optional[bytes], file_record: FileRecord, referer: Optional[str] = None
    ) -> List[str]:
        """Extract valid links from HTML content."""
        links = []

        try:

            if not html_bytes:
                if not self.session or not file_record.weburl:
                    return links

                # Re-fetch using the same strategy configured for this connector
                if self.use_headless_browser and self.crawl4ai_fetcher:
                    result = await self._headless_fetch(file_record.weburl)
                else:
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

    async def _create_failed_placeholder_record(
        self, url: str, status_code: int
    ) -> tuple[FileRecord | None, list[Permission] | None]:
        """Build a FAILED-status placeholder FileRecord for a URL that could not be fetched.

        Looks up any existing record by external_id so the same database document is
        reused (prevents duplicates on repeated sync runs).

        Args:
            url:         The URL that permanently failed to fetch.
            status_code: The HTTP status code of the last failed attempt.

        Returns:
            A (FileRecord, permissions) tuple ready to pass to on_new_records.
        """
        normalized_url = self._normalize_url(url)
        external_id = self._ensure_trailing_slash(normalized_url)
        timestamp = get_epoch_timestamp_in_ms()
        title = self._extract_title_from_url(url)
        parent_url = self._get_parent_url(url)

        existing_record = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id, external_record_id=external_id
        )

        if not existing_record:
            legacy_external_id = external_id.rstrip('/')
            if legacy_external_id != external_id:
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=legacy_external_id
                )


        if existing_record:
            return None, None

        record_id = str(uuid.uuid4())

        self.logger.warning(
            "⚠️ Creating FAILED placeholder for %s — "
            "failed due to error, status code: %d",
            url,
            status_code,
        )

        await self._ensure_parent_records_exist(parent_url)

        placeholder_record = FileRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=title,
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.WEB,
            external_record_id=external_id,
            external_record_group_id=self.url,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            created_at=timestamp,
            updated_at=timestamp,
            source_created_at=timestamp,
            source_updated_at=timestamp,
            weburl=url,
            size_in_bytes=None,
            is_file=True,
            extension=None,
            path=urlparse(url).path,
            mime_type=MimeTypes.HTML.value,
            preview_renderable=False,
            parent_external_record_id=parent_url,
            parent_record_type=RecordType.FILE if parent_url else None,
            indexing_status=ProgressStatus.FAILED.value,
            reason=f"Failed to process URL, status code: {status_code}",
        )

        permissions = []

        return placeholder_record, permissions

    async def _retry_urls_generator(
        self,
        max_retries: int = 2,
    ) -> AsyncGenerator[RecordUpdate, None]:
        """Generator that processes each queued retry URL and yields a RecordUpdate.

        Iterates over a snapshot of ``self.retry_urls`` so that mutations to the
        set during iteration are safe.

        Each URL is attempted up to ``max_retries`` times.  Between attempts an
        exponential back-off with full jitter is applied (base 15 s, cap 120 s)
        to avoid triggering bot-detection on the remote server.

        Yields:
            RecordUpdate — either the real fetch result (success) or a
            synthetic RecordUpdate wrapping a FAILED placeholder record
            (exhausted retries).
        """

        snapshot = list(self.retry_urls.values())

        for retry_url in snapshot:
            placeholder, perms = await self._create_failed_placeholder_record(
                retry_url.url, retry_url.status_code
            )

            if placeholder is None:
                continue

            yield RecordUpdate(
                record=placeholder,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                new_permissions=perms,
            )

    async def process_retry_urls(self, max_retries: int = 2) -> None:
        """Process retry URLs in batches.

        Delegates per-URL fetching to ``_retry_urls_generator``.  Yielded
        records are routed as follows:

        - **Updated** records are forwarded immediately to
          ``_handle_record_updates``.
        - **New** records (including FAILED placeholders) are collected into a
          batch and flushed to ``on_new_records`` once the batch reaches
          ``self.batch_size``, with a final flush after all URLs are processed.
        """
        batch_records: list[tuple[Record, list[Permission]]] = []

        async for record_update in self._retry_urls_generator(max_retries=max_retries):
            if record_update.is_new and record_update.record is not None and record_update.new_permissions is not None:
                batch_records.append((record_update.record, record_update.new_permissions))

                if len(batch_records) >= self.batch_size:
                    await self.data_entities_processor.on_new_records(batch_records)
                    self.processed_urls += len(batch_records)
                    batch_records.clear()

        # Flush any remaining records
        if batch_records:
            await self.data_entities_processor.on_new_records(batch_records)
            self.processed_urls += len(batch_records)

    def _check_index_filter(self, record: Record) -> bool:
        """Check if the record should be indexed."""
        mime_type = record.mime_type
        is_disabled = False

        if record.indexing_status == ProgressStatus.COMPLETED.value:
            return False

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
                # Normalise both sides: strip trailing slash then add one so
                # that "/globalprotect" (no slash) is accepted alongside
                # "/globalprotect/getting-started/..." while "/content/dam"
                # is correctly rejected.
                decoded_path = unquote(parsed.path).rstrip('/') + '/'
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
                return MimeTypes.PLAIN_TEXT, 'txt'
            elif 'csv' in content_type_lower:
                return MimeTypes.CSV, 'csv'
            elif 'tab-separated' in content_type_lower or 'tsv' in content_type_lower:
                return MimeTypes.TSV, 'tsv'
            elif 'mdx' in content_type_lower:
                return MimeTypes.MDX, 'mdx'
            elif 'markdown' in content_type_lower or 'md' in content_type_lower:
                return MimeTypes.MARKDOWN, 'md'
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
            # Don't touch URLs with query params — the param might be the identifier
            if parsed.query:
                return url
            last_segment = parsed.path.rstrip('/').rsplit('/', 1)[-1]
            if '.' not in last_segment:  # no file extension → treat as a page URL
                path = parsed.path.rstrip('/') + '/'
                return urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))
        except Exception as e:
            self.logger.warning(f"⚠️ Error in _ensure_trailing_slash for url '{url}': {e}")
        return url

    def _get_parent_url(self, url: str) -> Optional[str]:
        """Derive the parent URL by stripping the last non-empty path segment.

        Returns ``None`` when the URL is already at the domain root or when
        the resolved parent path would itself be the root (``/``), so callers
        never need to perform that check themselves.
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip('/')
        if not path or '/' not in path:
            return None  # Already at root, no parent
        parent_path = path.rsplit('/', 1)[0] + '/'
        if parent_path == '/':
            return None  # Parent is the domain root — no parent record exists
        return urlunparse((parsed.scheme, parsed.netloc, parent_path, '', '', ''))

    async def _ensure_parent_records_exist(self, parent_url: Optional[str]) -> None:
        """Ensure that every ancestor record up to (and including) *parent_url*
        exists in the data store before the child record is upserted.

        Strategy
        --------
        1. Return immediately when *parent_url* is ``None``.
        2. Walk up the URL hierarchy from *parent_url* to build an ordered
           segment list::

               [parent_url, grandparent_url, great-grandparent_url, ...]

        3. Iterate through that list, checking the DB for each URL.
           - If the record **already exists** → break (all ancestors above it
             are assumed to exist too).
           - If it **does not exist** → build a placeholder ``FileRecord`` and
             append it to ``batch_parent_records``.
        4. Reverse ``batch_parent_records`` so the highest ancestor comes first.
        5. Upsert via ``on_new_records``.  Because the list is ordered
           root-first, ``_process_record`` will always find a parent already
           in the DB and will never need to create its own placeholder via
           ``_handle_parent_record``.
        """
        if not parent_url:
            return

        try:
            # ── Step 1: build the segment list (closest → root) ─────────────
            segments: List[str] = []
            current: Optional[str] = parent_url
            while current:
                segments.append(current)
                current = self._get_parent_url(current)

            # ── Step 2: walk segments, collect the ones that are missing ─────
            batch_parent_records: List[Tuple[FileRecord, List[Permission]]] = []
            timestamp = get_epoch_timestamp_in_ms()

            for segment_url in segments:
                external_id = self._ensure_trailing_slash(segment_url)

                # Primary lookup
                existing = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=external_id,
                )

                # Legacy fallback: old records may have been stored without a trailing slash
                if not existing:
                    legacy_id = external_id.rstrip("/")
                    if legacy_id != external_id:
                        existing = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=legacy_id,
                        )

                if existing:
                    # This ancestor (and everything above it) already exists → stop
                    break

                # Record is missing — build a placeholder
                parsed = urlparse(segment_url)
                prefix_path = parsed.path if parsed.path.endswith("/") else parsed.path + "/"
                record_name = parsed.netloc + prefix_path

                segment_parent_url = self._get_parent_url(segment_url)

                file_record = FileRecord(
                    id=str(uuid.uuid4()),
                    org_id=self.data_entities_processor.org_id,
                    record_name=record_name,
                    record_type=RecordType.FILE,
                    record_group_type=RecordGroupType.WEB,
                    external_record_id=external_id,
                    external_record_group_id=self.url,
                    version=0,
                    origin=OriginTypes.CONNECTOR,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                    source_created_at=timestamp,
                    source_updated_at=timestamp,
                    weburl=segment_url,
                    size_in_bytes=None,
                    is_file=True,
                    extension=None,
                    path=prefix_path,
                    mime_type=MimeTypes.HTML.value,
                    preview_renderable=False,
                    is_internal=True,
                    parent_external_record_id=segment_parent_url,
                    parent_record_type=RecordType.FILE if segment_parent_url else None,
                    indexing_status=ProgressStatus.NOT_STARTED.value,
                )

                permissions = []

                batch_parent_records.append((file_record, permissions))

            if not batch_parent_records:
                return

            # ── Step 3: reverse so root-level ancestors are processed first ──
            batch_parent_records.reverse()

            await self.data_entities_processor.on_new_records(batch_parent_records)

        except Exception as e:
            self.logger.error(
                f"❌ Error ensuring parent records exist for {parent_url}: {e}",
                exc_info=True,
            )


    @classmethod
    async def create_connector(
        cls, logger: Logger, data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str
    ) -> BaseConnector:
        """Factory method to create a WebConnector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return WebConnector(
            logger, data_entities_processor, data_store_provider, config_service, connector_id, scope, created_by
        )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.crawl4ai_fetcher:
            self.crawl4ai_fetcher = None
            await release_shared_fetcher()
        if self.session:
            await self.session.close()
            self.session = None
        self.visited_urls.clear()

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records - not implemented for Web connector yet."""

        try:
            if not record_results:
                return

            await self.data_entities_processor.reindex_existing_records(record_results)

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
        """Return a storage signed URL if content is stored, otherwise the web URL."""
        if record.storage_document_id:
            try:
                storage_url = await self._get_storage_url()
                token = await self._get_storage_token()
                download_endpoint = f"{storage_url}/api/v1/document/internal/{record.storage_document_id}/download"

                owned_session = self.session is None
                session = self.session or aiohttp.ClientSession()
                try:
                    async with session.get(
                        download_endpoint,
                        headers={"Authorization": f"Bearer {token}"},
                    ) as response:
                        if response.status == HttpStatusCode.OK.value:
                            content_type = response.headers.get("Content-Type", "")
                            if "application/json" in content_type:
                                data = await response.json()
                                signed_url = data.get("signedUrl")
                                if signed_url:
                                    return signed_url
                finally:
                    if owned_session:
                        await session.close()
            except Exception as e:
                self.logger.warning("Failed to get storage signed URL for record %s: %s", record.id, e)

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
                b64_str = self._convert_avif_bytes_to_png_base64(img_bytes, absolute_url)
                if not b64_str:
                    img.decompose()
                    return
                content_type = 'image/png'
            elif content_type not in self.OPENAI_SUPPORTED_IMAGE_TYPES:
                img.decompose()
                return
            else:
                b64_str = base64.b64encode(img_bytes).decode('utf-8')
                b64_str = self._clean_base64_string(b64_str)
                if not b64_str:
                    self.logger.warning(f"⚠️ Failed to clean/validate base64 for image: {absolute_url}. Removing.")
                    img.decompose()
                    return

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
            return png_b64_str

        except Exception as pillow_err:
            self.logger.warning(
                f"⚠️  Pillow could not open AVIF ({pillow_err})"
            )
            return None

    _IMAGE_DOWNLOAD_CONCURRENCY = 8

    async def _process_all_images(
        self,
        soup: BeautifulSoup,
        base_url: str,
        headers: dict,
        preferred_strategy: Optional[str] = None
    ) -> None:
        """Download and convert all images concurrently (up to _IMAGE_DOWNLOAD_CONCURRENCY)."""
        imgs = soup.find_all('img')
        if not imgs:
            return

        sem = asyncio.Semaphore(self._IMAGE_DOWNLOAD_CONCURRENCY)

        async def _bounded(img_tag):
            async with sem:
                await self._process_single_image(img_tag, soup, base_url, headers, preferred_strategy)

        await asyncio.gather(*(_bounded(img) for img in imgs))

    async def _process_html_content(
        self,
        content_bytes: bytes,
        weburl: str,
        headers: dict,
        preferred_strategy: Optional[str] = None
    ) -> Optional[str]:
        """
        Process HTML content: parse, clean, and convert images to base64.

        Args:
            content_bytes: Raw HTML content bytes
            weburl: URL of the page being processed
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
                await self._process_all_images(soup, weburl, headers, preferred_strategy)
            else:
                # Remove all image and SVG tags when image indexing is disabled
                self._remove_image_tags(soup)

            # Serialize and clean data URIs
            cleaned_html = str(soup)
            return self._clean_data_uris_in_html(cleaned_html)


        except Exception as e:
            self.logger.error(f"⚠️ Failed to parse/clean HTML: {e}")
            raise

    # ==================== Storage Helpers ====================

    async def _get_storage_url(self) -> str:
        """Resolve the storage service endpoint from config."""
        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        return endpoints.get("storage", {}).get(
            "endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value
        )

    async def _get_storage_token(self) -> str:
        """Generate a scoped JWT token for storage service calls."""
        jwt_payload = {
            "orgId": self.data_entities_processor.org_id,
            "scopes": ["storage:token"],
        }
        return await generate_jwt(self.config_service, jwt_payload)

    @staticmethod
    def _sanitize_storage_name(name: str) -> str:
        """Sanitize a name for use as a storage document/file name.

        Removes characters that are invalid on Windows filesystems and trims
        the result to a reasonable length.
        """
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
        sanitized = sanitized.strip(". ")
        return sanitized[:200] if sanitized else "untitled"

    async def _upload_new_to_storage(
        self,
        content: bytes,
        record_name: str,
        extension: str,
        mime_type: str,
    ) -> Optional[str]:
        """Upload new content to storage using the full upload endpoint.

        Creates the document record and writes the file in a single call,
        same as local KB uploads. Returns the storage document ID on success.
        """
        try:
            storage_url = await self._get_storage_url()
            token = await self._get_storage_token()

            clean_name = self._sanitize_storage_name(record_name)
            if clean_name.endswith(f".{extension}"):
                clean_name = clean_name[: -(len(extension) + 1)]

            filename = f"{clean_name}.{extension}" if extension else clean_name

            form = aiohttp.FormData()
            form.add_field(
                "file",
                content,
                filename=filename,
                content_type=mime_type,
            )
            form.add_field("documentName", clean_name)
            form.add_field("documentPath", f"WebConnector/{self.connector_id}")
            form.add_field("isVersionedFile", "false")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{storage_url}/api/v1/document/internal/upload",
                    data=form,
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        doc_id = data.get("_id") or data.get("id")
                        return str(doc_id) if doc_id else None
                    else:
                        error = await resp.text()
                        self.logger.error(
                            "Failed to upload to storage (status %d): %s",
                            resp.status, error,
                        )
                        return None
        except Exception as e:
            self.logger.error("Error uploading new doc to storage: %s", e, exc_info=True)
            return None

    async def _update_storage_buffer(
        self,
        storage_document_id: str,
        content: bytes,
        filename: str,
        mime_type: str,
    ) -> bool:
        """Update content of an existing storage document via PUT buffer.

        Only used when the document already has a file path (from a prior upload).
        Returns True on success.
        """
        try:
            storage_url = await self._get_storage_url()
            token = await self._get_storage_token()

            form = aiohttp.FormData()
            form.add_field(
                "file",
                content,
                filename=filename,
                content_type=mime_type,
            )

            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{storage_url}/api/v1/document/internal/{storage_document_id}/buffer",
                    data=form,
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        error = await resp.text()
                        self.logger.error(
                            "Failed to update buffer for storage doc %s (status %d): %s",
                            storage_document_id, resp.status, error,
                        )
                        return False
        except Exception as e:
            self.logger.error("Error updating storage buffer: %s", e, exc_info=True)
            return False

    async def _store_crawled_content(
        self,
        content: bytes,
        record_name: str,
        extension: str,
        mime_type: str,
        existing_storage_doc_id: Optional[str] = None,
    ) -> Optional[str]:
        """Store crawled content in the storage service.

        For new documents, uses the full upload endpoint (creates record + writes
        file in one call). For existing documents, updates the buffer in-place.

        Returns the storage document ID on success, None on failure.
        """
        if existing_storage_doc_id:
            safe_name = self._sanitize_storage_name(record_name)
            filename = f"{safe_name}.{extension}" if extension else safe_name
            ok = await self._update_storage_buffer(
                existing_storage_doc_id, content, filename, mime_type
            )
            return existing_storage_doc_id if ok else None

        return await self._upload_new_to_storage(
            content, record_name, extension or "html", mime_type
        )

    async def _read_from_storage(self, storage_document_id: str) -> Optional[bytes]:
        """Read the buffer for a stored document from the storage service."""
        try:
            storage_url = await self._get_storage_url()
            token = await self._get_storage_token()
            buffer_url = f"{storage_url}/api/v1/document/internal/{storage_document_id}/buffer"
            response = await make_api_call(route=buffer_url, token=token)
            if isinstance(response.get("data"), dict):
                data = response["data"].get("data")
                return bytes(data) if isinstance(data, list) else data
            return response.get("data")
        except Exception as e:
            self.logger.warning("Failed to read from storage doc %s: %s", storage_document_id, e)
            return None

    async def _delete_storage_document(self, storage_document_id: str) -> bool:
        """Delete a document from the storage service."""
        try:
            storage_url = await self._get_storage_url()
            token = await self._get_storage_token()
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{storage_url}/api/v1/document/internal/{storage_document_id}",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    return resp.status in (200, 204, 404)
        except Exception as e:
            self.logger.warning("Failed to delete storage doc %s: %s", storage_document_id, e)
            return False

    # ==================== Main Stream Record Method ====================

    async def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> Optional[StreamingResponse]:  # type: ignore[override]
        """Stream the web page content, preferring stored content over live re-fetch."""
        if not record.weburl and not record.storage_document_id:
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Web URL and storage document ID are both missing for record {record.record_name} (id:{record.id})",
            )

        try:
            mime_type = record.mime_type or "text/html"

            # Prefer reading from storage if available
            if record.storage_document_id:
                stored_content = await self._read_from_storage(record.storage_document_id)
                if stored_content:
                    return create_stream_record_response(
                        _bytes_async_gen(stored_content),
                        filename=record.record_name,
                        mime_type=mime_type,
                        fallback_filename=f"record_{record.id}",
                    )
                self.logger.warning(
                    "Storage read failed for record %s (doc %s), falling back to live fetch",
                    record.id, record.storage_document_id,
                )

            # Fallback: live fetch (for legacy records without stored content)
            if not record.weburl:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"No stored content and no web URL for record {record.record_name} (id:{record.id})",
                )

            referer = self.url if self.url else None

            if self.session is None:
                raise HTTPException(
                    status_code=500,
                    detail="Session not initialized",
                )

            if self.use_headless_browser and self.crawl4ai_fetcher:
                result = await self._headless_fetch(record.weburl)
            else:
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

            cleaned_html_content = None
            if "html" in mime_type.lower():
                if result.strategy == "crawl4ai":
                    strategy = None
                else:
                    strategy = result.strategy
                cleaned_html_content = await self._process_html_content(
                    content_bytes, record.weburl or "", headers, strategy
                )

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