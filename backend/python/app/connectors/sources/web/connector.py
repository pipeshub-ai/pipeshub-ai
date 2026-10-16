import asyncio
import base64
import functools
import hashlib
import os
import re
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from http import HTTPStatus
from io import BytesIO
from logging import Logger
from typing import AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, unquote, urldefrag, urljoin, urlparse, urlunparse

import aiohttp
import pillow_avif  # noqa: F401  # pyright: ignore[reportUnusedImport]
from bs4 import BeautifulSoup, SoupStrainer, Tag
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    FILE_MIME_TYPES,
    AppGroups,
    Connectors,
    ExtensionTypes,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    internal_service_status,
    map_source_status,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.constants import IconPaths
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
from app.connectors.sources.web.address_guard import (
    create_guarded_session,
    is_unsafe_url,
)
from app.connectors.sources.web.browser_supervisor import BrowserUnavailableError
from app.connectors.sources.web.budget import (
    CrawlBudget,
    RunOutcome,
    RunReport,
    StopReason,
    run_limits,
)
from app.connectors.sources.web.checkpoint import CrawlCheckpoint, settings_fingerprint
from app.connectors.sources.web.crawl4ai_fetcher import (
    Crawl4AIFetcher,
    FetchResult,
    get_shared_fetcher,
    release_shared_fetcher,
    resolve_fetch_status_code,
)
from app.connectors.sources.web.csr_detection import (
    CSR_PROBE_JS,
    PRE_HYDRATION_CAPTURE_JS,
    analyze_rendering,
)
from app.connectors.sources.web.fetch_strategy import (
    MAX_RATE_LIMIT_BACKOFF,
    PIPESHUB_USER_AGENT,
    FetchResponse,
    build_crawler_headers,
    fetch_url_with_fallback,
    too_many_redirects_response,
    unsafe_address_response,
)
from app.connectors.sources.web.politeness import (
    crawl_concurrency,
    shared_fetch_slots,
    shared_host_gate,
)
from app.connectors.sources.web.robots import RobotsRules, shared_robots_cache
from app.connectors.sources.web.sitemaps import SitemapReport, read_sitemaps
from app.connectors.sources.web.url_identity import clean_url, is_same_page
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
from app.services.notification.types import NotificationSeverity, NotificationType
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
    status_code: int | None  # None when the site never answered
    retries: int
    last_attempted: int
    depth: int = 0                  # depth at which the URL was first encountered
    referer: str | None = None   # referer at the time of first attempt
    retry_after: float | None = None  # server-requested backoff (seconds)
    deferred: bool = False  # site asked to wait longer than we hold a sync open
    reason: str | None = None  # shown on the failed page instead of the status-based reason
    site_url: str | None = None  # the URL as the site gave it, which is what a stored record is keyed by
    # URLs that redirected to ``site_url`` this sync; each may still hold an older record.
    queued_urls: list[str] = field(default_factory=list)

class Status(Enum):
    PENDING = "PENDING"


# Node storage returns 308 + Location (S3/Azure PUT presigned URL) when it
# cannot proxy the bytes. aiohttp would follow 308 as POST, which S3 rejects
# with SignatureDoesNotMatch. Handle the redirect ourselves and PUT instead.
STORAGE_UPLOAD_REDIRECT_STATUS_CODES = frozenset({301, 302, 307, 308})

GONE_STATUS_CODES = frozenset({HTTPStatus.NOT_FOUND.value, HTTPStatus.GONE.value})

RETRYABLE_STATUS_CODES = {
    403, 408, 429,
    500, 502, 503, 504,
    999,
    520, 522, 524, 525, 529,
}

# Base and cap (seconds) for the exponential back-off between attempts.
# Max Pages counts pages kept; fetches are capped at this many per page so that pages the crawl
# fetches and drops (filtered, duplicates, failures) can't keep it busy for ever.
FETCHES_PER_KEPT_PAGE = 3
DISCOVERY_SITEMAP_AND_LINKS = "sitemap_and_links"
DISCOVERY_LINKS = "links"
DISCOVERY_SITEMAP = "sitemap"

# Setting limits: the form and the save route enforce them; init still clamps older stored values.
MAX_PAGES_LIMIT = 10_000
# A served page with less extracted text than this may be an empty shell a script fills in.
SHELL_TEXT_CHARS = 1000
SHELL_CHECKS_PER_SECTION = 2
SECTION_PAGES_HELD = 4
RATE_LIMITED_STATUSES = frozenset({HTTPStatus.TOO_MANY_REQUESTS.value, HTTPStatus.SERVICE_UNAVAILABLE.value})
MAX_DEPTH_LIMIT = 10
# Images embedded per page, each described by the vision model at indexing time: smaller ones are
# icons and spacers, and past the first few a page's images add little to what it says.
MIN_IMAGE_SIDE_PX = 64
MAX_IMAGES_PER_PAGE = 10
MAX_SIZE_MB_LIMIT = 100
# What a crawl can store, for the file-type filter.
WEB_FILE_TYPES = [
    ExtensionTypes.HTML, ExtensionTypes.PDF, ExtensionTypes.DOCX, ExtensionTypes.DOC, ExtensionTypes.PPTX,
    ExtensionTypes.PPT, ExtensionTypes.XLSX, ExtensionTypes.XLS, ExtensionTypes.CSV, ExtensionTypes.TXT,
    ExtensionTypes.MD, ExtensionTypes.PNG, ExtensionTypes.JPG, ExtensionTypes.JPEG, ExtensionTypes.WEBP,
    ExtensionTypes.SVG,
]
# Where sitemaps usually are when robots.txt names none.
DEFAULT_SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml")
SITEMAP_TIMEOUT_SECONDS = 30
# A crawl saves where it is after this many more pages, or this long, so a crash loses little.
CHECKPOINT_EVERY_PAGES = 200
CHECKPOINT_EVERY_SECONDS = 300
# A site's rendering verdict is probed again after this long, in case the site was rebuilt.
RENDER_VERDICT_MAX_AGE_MS = 7 * 24 * 3600 * 1000
# A <main> with less text than this is likely a wrapper around a widget, not the page's content.
MIN_MAIN_CONTENT_CHARS = 200
LAZY_IMAGE_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-url")
_META_CHARSET = re.compile(r"<meta[^>]+charset\s*=\s*[\"']?[\w-]+[\"']?[^>]*>", re.IGNORECASE)
MIN_REFUSALS_FOR_HINT = 3
STORE_RETRY_WAITS = (2.0, 8.0)
PRUNE_PAGE_SIZE = 500

_BACKOFF_BASE = 15.0
_BACKOFF_CAP = 300.0
MAX_RETRIES = 2

# Finding where a redirect the browser aborted was heading, without following it off the crawl.
MAX_PROBE_REDIRECTS = 10
PROBE_UNENDING = -1  # _probe_landing's status for a chain still redirecting after MAX_PROBE_REDIRECTS
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
HEAD_NOT_SUPPORTED = frozenset({HTTPStatus.METHOD_NOT_ALLOWED.value, HTTPStatus.NOT_IMPLEMENTED.value})
PROBE_TIMEOUT_SECONDS = 10

# The name robots.txt groups are matched against; sites without a group for it get their "*" rules.
ROBOTS_USER_AGENT = "PipesHub"
ROBOTS_TIMEOUT_SECONDS = 10
# A robots.txt that can't be read is asked for again after these waits before the site is left out.
ROBOTS_RETRY_WAITS = (2.0, 6.0)
TOO_MANY_REDIRECTS_REASON = (
    "This page redirects too many times, so it couldn't be fetched. "
    "Check the address in a browser, then sync again."
)
UNSAFE_ADDRESS_REASON = "This address is on a private or internal network, so it wasn't fetched."
BROWSER_CRASH_REASON = (
    "This page crashed the browser that renders the site's pages, twice, so it was skipped. "
    "The next sync tries it again."
)
BLOCKED_REASON = (
    "The site refused the crawler (403 Forbidden). If you may crawl this site, turn on "
    "Identify as a web browser (Advanced settings), then sync again."
)
ROBOTS_MAX_BYTES = 512 * 1024

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


def failed_page_reason(status_code: int | None) -> str:
    """What a person sees as the reason a crawled page failed, with what to do next."""
    try:
        code = int(status_code)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        code = 0
    if code <= 0:
        return "We couldn't reach this page. Check the URL is correct and publicly reachable, then sync again."
    try:
        status: HTTPStatus | int = HTTPStatus(code)
        label = f"{code} {HTTPStatus(code).phrase}"
    except ValueError:
        # The site answered, with a code outside the standard (e.g. CDN codes 520-529, or 999).
        status, label = code, str(code)
    if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return (
            f"The page refused access ({label}). It may need a login or block automated visitors; "
            "make sure it's publicly reachable, then sync again."
        )
    if status in (HTTPStatus.NOT_FOUND, HTTPStatus.GONE):
        return f"The page wasn't found ({label}). Check the URL is correct, then sync again."
    if status in (HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.REQUEST_TIMEOUT) or code >= 500:
        return f"The site didn't respond properly ({label}). PipesHub will try again on the next sync."
    return f"The page returned an error ({label}). Check the URL is correct and publicly reachable, then sync again."


@ConnectorBuilder("Web")\
    .in_group("Web")\
    .with_supported_auth_types("NONE")\
    .with_description("Crawl and sync data from web pages")\
    .with_categories(["Web"])\
    .with_scopes([ConnectorScope.PERSONAL, ConnectorScope.TEAM])\
    .with_permission_model(PermissionModel.APP_LEVEL)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.WEB.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Web Connector Guide",
            "https://docs.pipeshub.com/connectors/web/web",
            "setup"
        ))
        .with_scheduled_config(True, 1440)  # Daily sync
        .with_sync_field_group("website", "Website")
        .with_sync_field_group("crawl", "What to crawl")
        .with_sync_field_group("skip", "What to skip")
        .with_sync_field_group(
            "advanced", "Advanced", "Crawl depth, file size, browser and crawler identity.", collapsed=True
        )
        .add_sync_custom_field(CustomField(
            name="url",
            display_name="Start URL",
            field_type="URL",
            required=True,
            description=(
                "The first page PipesHub visits. You can't change it after saving; to crawl another site, "
                "add another connector."
            ),
            non_editable=True,
            group="website",
            placeholder="https://docs.example.com",
        ))
        .add_sync_custom_field(CustomField(
            name="type",
            display_name="Crawl",
            field_type="SELECT",
            required=True,
            default_value="recursive",
            options=["single", "recursive"],
            option_labels={"single": "Only this page", "recursive": "This page and the pages it links to"},
            description="Crawl only the start page, or follow its links to the rest of the site.",
            group="crawl",
        ))
        .add_sync_custom_field(CustomField(
            name="discovery",
            display_name="Find pages using",
            field_type="SELECT",
            required=False,
            default_value=DISCOVERY_SITEMAP_AND_LINKS,
            options=[DISCOVERY_SITEMAP_AND_LINKS, DISCOVERY_LINKS, DISCOVERY_SITEMAP],
            option_labels={
                DISCOVERY_SITEMAP_AND_LINKS: "Sitemaps and links (recommended)",
                DISCOVERY_LINKS: "Links only",
                DISCOVERY_SITEMAP: "Sitemaps only",
            },
            description="Sitemaps are fastest; links find pages a sitemap misses.",
            group="crawl",
            show_when={"type": ["recursive"]},
        ))
        .add_sync_custom_field(CustomField(
            name="restrict_to_start_path",
            display_name="Stay in this section",
            field_type="BOOLEAN",
            required=False,
            default_value="false",
            description="Only pages under the start URL's path, such as /docs/.",
            group="crawl",
            show_when={"type": ["recursive"]},
        ))
        .add_sync_custom_field(CustomField(
            name="max_pages",
            display_name="Maximum pages",
            field_type="NUMBER",
            required=False,
            default_value="100",
            min_value=1,
            max_value=MAX_PAGES_LIMIT,
            unit="pages",
            description=(
                "PipesHub keeps at most this many pages from this site. Files such as PDFs count as pages."
            ),
            group="crawl",
        ))
        .add_sync_custom_field(CustomField(
            name="url_should_contain",
            display_name="Only save pages whose URL contains",
            field_type="TAGS",
            required=False,
            default_value=[],
            description=(
                "Leave empty to save every page. Pages that don't match aren't visited, so their links "
                "aren't followed either."
            ),
            group="skip",
            show_when={"type": ["recursive"]},
        ))
        .add_sync_custom_field(CustomField(
            name="ignore_query_params",
            display_name="Same page, different URL",
            field_type="TAGS",
            required=False,
            default_value=[],
            description=(
                "URL parameters that don't change the page, such as sort or ref. Use * for all. Tracking "
                "parameters (utm_…) are always ignored."
            ),
            group="skip",
        ))
        .add_sync_custom_field(CustomField(
            name="depth",
            display_name="Clicks from the start page",
            field_type="NUMBER",
            required=False,
            default_value="3",
            min_value=1,
            max_value=MAX_DEPTH_LIMIT,
            unit="clicks",
            description="How far to follow links. Pages listed in a sitemap count as one click.",
            group="advanced",
            show_when={"type": ["recursive"]},
        ))
        .add_sync_custom_field(CustomField(
            name="max_size_mb",
            display_name="Largest file to download",
            field_type="NUMBER",
            required=False,
            default_value="10",
            min_value=1,
            max_value=MAX_SIZE_MB_LIMIT,
            unit="MB",
            description="Bigger pages and files are skipped and listed in the last sync.",
            group="advanced",
        ))
        .add_sync_custom_field(CustomField(
            name="follow_external",
            display_name="Also follow links to other sites",
            field_type="BOOLEAN",
            required=False,
            default_value="false",
            description="Usually off: the crawl can wander far beyond this site. Ignored with Stay in this section.",
            group="advanced",
            show_when={"type": ["recursive"]},
        ))
        .add_sync_custom_field(CustomField(
            name="use_headless_browser",
            display_name="Always use a browser (slower)",
            field_type="BOOLEAN",
            required=False,
            default_value="false",
            description=(
                "Off: PipesHub detects pages that need JavaScript and uses a browser only for those sites."
            ),
            group="advanced",
        ))
        .add_sync_custom_field(CustomField(
            name="browser_like_identity",
            display_name="Identify as a web browser",
            field_type="BOOLEAN",
            required=False,
            default_value="false",
            description=(
                "Off: requests identify as the PipesHub crawler. Turn on only for a site you may crawl that "
                "blocks crawlers; robots.txt is still respected."
            ),
            group="advanced",
        ))
        .add_sync_custom_field(CustomField(
            name="respect_robots_txt",
            display_name="Respect robots.txt",
            field_type="BOOLEAN",
            required=False,
            default_value="true",
            description="Skip pages the site asks crawlers not to visit or index.",
            group="advanced",
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(CommonFields.file_extension_filter(
            display_name="File types",
            description="Leave empty to keep every type. Choosing only some types also drops web pages unless Web pages is one of them.",
            extensions=WEB_FILE_TYPES,
            labels={ExtensionTypes.HTML.value: "Web pages (.html)"},
        ))
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
        self.ignore_query_params: list[str] = []
        self.discovery: str = DISCOVERY_SITEMAP_AND_LINKS
        # Normalised URL -> lastmod (epoch ms) for pages the sitemaps list with one, this sync.
        self._sitemap_lastmod: dict[str, int] = {}
        self._sitemap_report: SitemapReport | None = None
        self._sitemap_urls = 0
        # Resuming an unfinished crawl: the checkpoint read at the start of this sync, if one applies.
        self._resume: CrawlCheckpoint | None = None
        self._resumed = False
        self._sync_point_key: str | None = None
        self._sync_point_base: dict = {}
        # The crawl's queue, and the pages fetched but not yet written: a checkpoint saves both as to-do.
        self._frontier: deque[tuple[str, int, str | None]] | None = None
        self._in_flight: dict[str, tuple[str, int]] = {}
        # (url, depth) being fetched right now: taken off the queue, not settled yet.
        self._fetching: dict[str, tuple[str, int]] = {}
        self._crawl_concurrency = crawl_concurrency()
        # One browser context per sync: cookies and storage never reach another connector or sync.
        self._browser_context_key: str | None = None
        # Plain-HTTP answers the rendering check already got, kept for the crawl's own fetch of them.
        self._served_pages: dict[str, FetchResponse] = {}
        # Whether the site renders with scripts: probed, or read back from an earlier sync.
        self._robust_from_config = False
        self._csr_detected = False
        # Per crawl, sections (first path segment) whose pages turned out to need the browser, and how
        # many short pages a section had that the browser added nothing to.
        self._browser_sections: set[str] = set()
        self._plain_sections: dict[str, int] = {}
        self._section_locks: dict[str, asyncio.Lock] = {}
        self._section_passes: dict[str, int] = {}
        # Sitemap pages outside the start URL's section, crawled once that section is done.
        self._later_seeds: deque[tuple[str, int, str | None]] = deque()
        # Page fetches this crawl started and finished, capped at FETCHES_PER_KEPT_PAGE x Max Pages.
        self._fetches_started = 0
        self._fetches_done = 0
        self._render_verdict: tuple[str, int] | None = None
        self._checkpoint_kept = 0
        self._checkpoint_at = 0.0
        self._unchanged_by_lastmod = 0
        self._noindex_skipped = 0

        # Crawling state
        self.visited_urls: Set[str] = set()
        # Where redirects landed: deduplicated like visited_urls, but not counted toward max_pages.
        self._landed_urls: set[str] = set()
        # Pages this sync kept, directly or through a redirect; visited_urls also holds pages fetched and dropped.
        self._kept_urls: set[str] = set()
        self.retry_urls: dict[str, RetryUrl] = {}
        # Stored pages that answered 404/410: deleted when the next sync gets the same answer.
        self._gone_last_sync: set[str] = set()
        self._gone_this_sync: set[str] = set()
        # Removed pages whose children could not be linked to the folder left in their place.
        self._relink_pending: set[str] = set()
        self._domain_next_retry_at: dict[str, float] = {}  # domain -> monotonic time when retry is allowed
        self.processed_urls: int = 0
        self.base_domain: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.full_sync: bool = False
        self.use_headless_browser: bool = False
        self.respect_robots_txt: bool = True
        self.browser_like_identity: bool = False
        # Per crawl: each site's robots.txt rules, or None when it couldn't be read (RFC 9309: crawl nothing there).
        self._robots: dict[str, RobotsRules | None] = {}
        self._robots_skipped: set[str] = set()
        # The script-rendering check waits for a robots.txt that could be read.
        self._script_check_pending = False
        self.crawl4ai_fetcher: Optional[Crawl4AIFetcher] = None
        self.max_run_seconds, self.stall_seconds = run_limits({}, os.environ)
        self._budget: CrawlBudget | None = None
        self._page_limit_reached = False
        # Record ids this sync wrote; with _kept_urls, what pruning treats as still on the site.
        self._reached_record_ids: set[str] = set()
        self._start_page_kept = False
        # Ids of the pages stored for this connector: Max Pages caps their number, not one sync's fetches.
        self._stored_page_ids: set[str] = set()
        self._skipped_for_max_pages = 0

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
            self.ignore_query_params = config_values["ignore_query_params"]
            self.discovery = config_values["discovery"]
            self.use_headless_browser = config_values["use_headless_browser"]
            self.respect_robots_txt = config_values["respect_robots_txt"]
            self.browser_like_identity = config_values["browser_like_identity"]
            self.max_run_seconds, self.stall_seconds = config_values["run_limits"]

            # Load creator email if needed (for personal scope permission creation)
            await self._load_creator_email()

            timeout = aiohttp.ClientTimeout(total=30)
            self.session = create_guarded_session(
                timeout=timeout,
                headers=self._session_headers(),
            )

            self._robust_from_config = self.use_headless_browser
            # init runs inside the request that turns the connector on, so probing the site and
            # starting the browser wait for the sync.
            self._script_check_pending = not self.use_headless_browser and not await self._apply_stored_render_verdict()

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
            _iqp_raw = sync_config.get("ignore_query_params", [])
            ignore_query_params = (
                [p.strip() for p in _iqp_raw if isinstance(p, str) and p.strip()] if isinstance(_iqp_raw, list) else []
            )
            discovery = sync_config.get("discovery") or DISCOVERY_SITEMAP_AND_LINKS
            if discovery not in (DISCOVERY_SITEMAP_AND_LINKS, DISCOVERY_LINKS, DISCOVERY_SITEMAP):
                self.logger.warning("⚠️ Unknown page discovery %r; using sitemaps and links", discovery)
                discovery = DISCOVERY_SITEMAP_AND_LINKS
            _uhb_raw = sync_config.get("use_headless_browser", False)
            use_headless_browser = _uhb_raw if isinstance(_uhb_raw, bool) else str(_uhb_raw).lower() == "true"
            # Connectors set up before the setting existed have it stored by web_browser_identity_migration.
            _identity_raw = sync_config.get("browser_like_identity") or False
            browser_like_identity = (
                _identity_raw if isinstance(_identity_raw, bool) else str(_identity_raw).lower() != "false"
            )
            _robots_raw = sync_config.get("respect_robots_txt", True)
            respect_robots_txt = _robots_raw if isinstance(_robots_raw, bool) else str(_robots_raw).lower() != "false"

            # restrict_to_start_path implies staying on the starting domain,
            # so follow_external must be False — override with a warning.
            if restrict_to_start_path and follow_external:
                self.logger.warning(
                    "⚠️ 'restrict_to_start_path' is enabled — overriding 'follow_external' to False "
                    "(cannot follow external links while restricting to the start path)"
                )
                follow_external = False

            # Validate max_pages and max_depth
            if max_pages > MAX_PAGES_LIMIT:
                self.logger.warning("⚠️ WebPage max_pages is greater than %d, setting it to that", MAX_PAGES_LIMIT)
                max_pages = MAX_PAGES_LIMIT
            elif max_pages < 1:
                self.logger.warning("⚠️ WebPage max_pages is less than 1, setting to 1")
                max_pages = 1
            if max_depth > MAX_DEPTH_LIMIT:
                self.logger.warning("⚠️ WebPage max_depth is greater than %d, setting it to that", MAX_DEPTH_LIMIT)
                max_depth = MAX_DEPTH_LIMIT
            elif max_depth < 1:
                self.logger.warning("⚠️ WebPage max_depth is less than 1, setting to 1")
                max_depth = 1
            if max_size_mb > MAX_SIZE_MB_LIMIT:
                self.logger.warning("⚠️ WebPage max_size_mb is greater than %d, setting it to that", MAX_SIZE_MB_LIMIT)
                max_size_mb = MAX_SIZE_MB_LIMIT
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
                "ignore_query_params": ignore_query_params,
                "discovery": discovery,
                "use_headless_browser": use_headless_browser,
                "respect_robots_txt": respect_robots_txt,
                "browser_like_identity": browser_like_identity,
                "run_limits": run_limits(sync_config, os.environ),
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
                browser_like=self.browser_like_identity,
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

            self.respect_robots_txt = config_values["respect_robots_txt"]
            self.browser_like_identity = config_values["browser_like_identity"]
            self.max_run_seconds, self.stall_seconds = config_values["run_limits"]

            self.ignore_query_params = config_values["ignore_query_params"]
            self.discovery = config_values["discovery"]
            robust = config_values["use_headless_browser"]
            if robust != self._robust_from_config:
                # Robust Mode turned on or off since this connector was built; an auto-detected
                # script-rendered site keeps its browser either way.
                self._robust_from_config = robust
                self.use_headless_browser = robust or self._csr_detected
            new_url_should_contain = config_values["url_should_contain"]
            if new_url_should_contain != self.url_should_contain:
                self.url_should_contain = new_url_should_contain

        except Exception as e:
            self.logger.error(f"❌ Failed to reload config: {e}", exc_info=True)
            raise

    async def run_sync(self) -> None:  # type: ignore[override]
        """Main sync method to crawl and index web pages."""
        report = RunReport(started_at=get_epoch_timestamp_in_ms())
        # This run's figures from the start: one that fails early must not show the last run's counts.
        self.last_sync_summary = report.summary()
        self._browser_context_key = f"{self.connector_id}:{report.started_at}"
        sync_point_key: str | None = None
        sync_point: dict | None = None
        # Before anything can fail: a cached connector still holds the last run's frontier, which a
        # failure here would otherwise save as this run's checkpoint.
        self._frontier = None
        try:
            await self.reload_config()

            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "web", self.connector_id, self.logger
            )

            self.logger.info("Starting web crawl: %s", self.url)

            sync_point_key = self._sync_point_key_for(self.url or "")

            sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
            # A run that failed before finishing leaves only its report, not a timestamp.
            if not sync_point or "timestamp" not in sync_point:
                self.full_sync = True
            self._gone_last_sync = set((sync_point or {}).get("goneOnce") or [])
            self._gone_this_sync = set()
            self._relink_pending = set((sync_point or {}).get("relinkPending") or [])
            self._sync_point_key, self._sync_point_base = sync_point_key, dict(sync_point or {})
            self._sync_point_base.update(self._render_verdict_fields())
            self._resume = CrawlCheckpoint.decode(
                (sync_point or {}).get("resume"),
                fingerprint=self._settings_fingerprint(),
                now_ms=get_epoch_timestamp_in_ms(),
            ) if self.crawl_type == "recursive" else None

            if self.scope == ConnectorScope.TEAM.value:
                await self.data_entities_processor.ensure_team_app_edge(
                    self.connector_id
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
            self._landed_urls.clear()
            self._kept_urls.clear()
            self._robots.clear()
            self._robots_skipped.clear()
            # A body the last sync's rendering check read but never used is not this sync's answer.
            self._served_pages.clear()
            self.retry_urls.clear()
            self._domain_next_retry_at.clear()
            self.processed_urls = 0
            self._page_limit_reached = False
            self._reached_record_ids.clear()
            self._sitemap_lastmod.clear()
            self._sitemap_report = None
            self._sitemap_urls = 0
            self._unchanged_by_lastmod = 0
            self._noindex_skipped = 0
            self._resumed = False
            self._frontier = None
            self._in_flight.clear()
            self._fetching = {}
            self._checkpoint_kept = 0
            self._checkpoint_at = self._monotonic()
            self._start_page_kept = False
            self._browser_sections.clear()
            self._plain_sections.clear()
            self._section_locks.clear()
            self._section_passes.clear()
            self._fetches_started = self._fetches_done = 0
            self._stored_page_ids = {record.id async for record in self._stored_pages()}
            await self._relink_children_left_out()
            self._skipped_for_max_pages = 0
            self._budget = CrawlBudget(
                now=self._monotonic, deadline_seconds=self.max_run_seconds, stall_seconds=self.stall_seconds,
            )
            if self.use_headless_browser and await self._ensure_crawl4ai_fetcher() is None:
                if self._robust_from_config:
                    raise BrowserUnavailableError("Always use a browser is on, but the headless browser could not start")
                # The user didn't ask for a browser, so one that can't start means plain HTTP.
                self.use_headless_browser = False
            if self._script_check_pending and not self.use_headless_browser and self.url:
                await self._check_script_rendering(self.url, keep_robots=True)

            # Start crawling
            assert self.url is not None, "URL not set — init() must be called first"
            if self.crawl_type == "recursive":
                await self._crawl_recursive(self.url, depth=0)
            elif self.crawl_type in ("single", None, ""):
                await self._crawl_single_page(self.url)
            else:
                self.logger.warning(f"Unknown crawl type {self.crawl_type!r}; skipping crawl")

            self._served_pages.clear()
            budget_stop = self._budget.stop_reason
            # Pages still waiting for a retry when the budget ran out never got their answer:
            # they are left for the next sync, not shown as failed.
            await self.process_retry_urls(answered_only=budget_stop is not None)

            report.outcome = RunOutcome.PARTIAL if budget_stop else RunOutcome.COMPLETED
            report.stop_reason = budget_stop or (StopReason.MAX_PAGES if self._page_limit_reached else None)
            # Only a crawl that loaded its start page and went everywhere it was going to can tell a
            # page is no longer reached.
            unreached_last_sync = list((sync_point or {}).get("unreachedOnce") or [])
            unreached = (
                await self._prune_unreached(set(unreached_last_sync), report)
                if report.outcome is RunOutcome.COMPLETED and self._start_page_kept and not self._stopped_short()
                else unreached_last_sync
            )
            self._fill_run_report(report)
            self.last_sync_summary = report.summary()
            await self.record_sync_point.update_sync_point(
                sync_point_key,
                {
                    "timestamp": get_epoch_timestamp_in_ms(),
                    "goneOnce": sorted(self._gone_marks(report.outcome)),
                    "unreachedOnce": unreached,
                    "relinkPending": sorted(self._relink_pending),
                    **self._render_verdict_fields(),
                    "lastRun": report.to_json(),
                    # A partial crawl carries on from here next time; a finished one starts over.
                    "resume": (
                        self._checkpoint_text()
                        if report.outcome is RunOutcome.PARTIAL and self._resume_made_progress()
                        else ""
                    ),
                }
            )
            self.full_sync = False

            self.logger.info(
                "Web crawl %s: %d pages crawled, %d pages processed, %d pages failed%s",
                report.outcome.value,
                len(self.visited_urls),
                self.processed_urls,
                len(self.retry_urls),
                f", stopped by {report.stop_reason.value}" if report.stop_reason else "",
            )

            if len(self.retry_urls) > 0:
                message = f"Failed to crawl {len(self.retry_urls)} pages.\nCrawled {len(self.visited_urls)} pages.\nProcessed {self.processed_urls} pages."
            else:
                message = f"Added {self.processed_urls} pages."
            await self.notify(
                type=NotificationType.CONNECTOR_INFO,
                severity=NotificationSeverity.INFO,
                title="Web crawl stopped early" if budget_stop else "Web crawl completed",
                message=message + self._budget_summary(budget_stop) + self._identity_hint() + self._robots_summary(),
            )

        except BrowserUnavailableError as e:
            # Raised before process_retry_urls, so no page is recorded as unreachable for a fault of ours.
            self.logger.error("❌ Web sync stopped, headless browser unavailable: %s", e)
            await self._save_unfinished_run(sync_point_key, sync_point, report, RunOutcome.FAILED, "browser_unavailable")
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title="Web crawl stopped",
                message=(
                    "The browser that renders this site's pages stopped and could not be restarted. "
                    f"{self.processed_urls} pages were saved before it stopped. Sync again to continue."
                ),
            )
            raise
        except asyncio.CancelledError:
            # A stop from the user or a shutdown: the report still records how far the crawl got.
            await asyncio.shield(
                self._save_unfinished_run(sync_point_key, sync_point, report, RunOutcome.STOPPED, None)
            )
            raise
        except Exception as e:
            self.logger.error(f"❌ Error during web sync: {e}", exc_info=True)
            await self._save_unfinished_run(sync_point_key, sync_point, report, RunOutcome.FAILED, type(e).__name__)
            raise
        finally:
            self._budget = None

    async def _stored_pages(self) -> AsyncGenerator[Record, None]:
        """Every page, file and failed page stored for this connector; folder placeholders left out."""
        after_key: str | None = None
        while True:
            page = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, self.url or "", PRUNE_PAGE_SIZE, after_key
            )
            for record in page:
                if not record.is_internal:
                    yield record
            if len(page) < PRUNE_PAGE_SIZE:
                return
            after_key = page[-1].id

    def _admit_new_record(self, record: Record) -> bool:
        """Whether a record about to be written fits under Max Pages; one already stored always does."""
        if record.id in self._stored_page_ids:
            return True
        if len(self._stored_page_ids) >= self.max_pages:
            self._skipped_for_max_pages += 1
            return False
        self._stored_page_ids.add(record.id)
        return True

    def _note_reached(self, record_update: RecordUpdate) -> None:
        if record_update.record is not None:
            self._reached_record_ids.add(record_update.record.external_record_id)

    def _was_reached(self, record: Record) -> bool:
        """Whether this crawl wrote the record or asked the site for its page, whatever the answer.

        A page that was asked for and answered badly is the gone-page rules' to remove, not pruning's.
        """
        if record.external_record_id in self._reached_record_ids:
            return True
        if not self._keyed_by_current_rules(record.external_record_id):
            return False  # stored under a URL this crawl cleans before asking for it, so never asked for
        key = self._normalize_url(record.weburl or record.external_record_id)
        return any(key in seen for seen in (self.visited_urls, self._kept_urls, self._landed_urls, self.retry_urls))

    def _keyed_by_current_rules(self, key: str) -> bool:
        """Whether cleaning ``key`` drops none of its query parameters."""
        def params(url: str) -> int:
            return len(parse_qsl(urlparse(url).query, keep_blank_values=True))
        return params(self._identity_url(key)) == params(key)

    async def _prune_unreached(self, unreached_last_sync: set[str], report: RunReport) -> list[str]:
        """Delete stored pages this crawl and the last one both no longer reached; return this crawl's misses.

        Pages, files and failed pages count; folder placeholders don't, they hold the tree together.
        A crawl that suddenly misses most of the site is more likely to have hit a broken page than
        to be right, so it deletes nothing.
        """
        # Pages stored under URLs this crawl now cleans are leftovers, not misses, so they don't
        # count toward the "missed most of the site" check: turning on Ignore URL Parameters on a
        # site full of variants must still clear the variants out.
        current = 0
        unreached: list[Record] = []
        missed = 0
        async for record in self._stored_pages():
            keyed_now = self._keyed_by_current_rules(record.external_record_id)
            current += keyed_now
            if not self._was_reached(record):
                unreached.append(record)
                missed += keyed_now
        if missed and missed * 2 > current:
            self.logger.warning(
                "Not removing pages: this crawl missed %d of %d stored pages, more than half; "
                "the site may have been partly down", missed, current,
            )
            report.prune_skipped = True
            return sorted(unreached_last_sync)
        missed_again = [r for r in unreached if r.external_record_id in unreached_last_sync]
        for record in missed_again:
            self.logger.info("Removing %s: no longer reached by two complete crawls in a row", record.weburl)
            await self._remove_record(record, record.weburl or record.external_record_id)
            self._stored_page_ids.discard(record.id)
        report.pruned = len(missed_again)
        removed = {r.external_record_id for r in missed_again}
        return sorted(r.external_record_id for r in unreached if r.external_record_id not in removed)

    def _monotonic(self) -> float:
        return asyncio.get_event_loop().time()

    def _fill_run_report(self, report: RunReport) -> None:
        report.finished_at = get_epoch_timestamp_in_ms()
        report.pages_visited = len(self.visited_urls)
        report.pages_saved = self.processed_urls
        report.pages_stored = len(self._stored_page_ids)
        report.robots_skipped = len(self._robots_skipped)
        report.deferred = sum(1 for r in self.retry_urls.values() if r.deferred)
        report.skipped_max_pages = self._skipped_for_max_pages
        report.sitemap_urls = self._sitemap_urls
        report.unchanged_by_lastmod = self._unchanged_by_lastmod
        report.noindex_skipped = self._noindex_skipped
        report.resumed = self._resumed
        if self._sitemap_report is not None:
            report.sitemap_files_read = self._sitemap_report.files_read
            report.sitemap_files_failed = self._sitemap_report.files_failed
            report.sitemap_limits_hit = sorted(self._sitemap_report.limits_hit or ())
        report.failed_by_status.update(
            str(r.status_code) if r.status_code is not None else "no_answer"
            for r in self.retry_urls.values() if not r.deferred
        )

    async def _save_unfinished_run(
        self,
        sync_point_key: str | None,
        sync_point: dict | None,
        report: RunReport,
        outcome: RunOutcome,
        error: str | None,
    ) -> None:
        """Keep the report of a run that ended early, without touching what the last finished run saved."""
        if sync_point_key is None:
            return
        report.outcome = outcome
        report.error = error
        report.stop_reason = self._budget.stop_reason if self._budget else None
        self._fill_run_report(report)
        self.last_sync_summary = report.summary()
        try:
            await self.record_sync_point.update_sync_point(
                sync_point_key,
                {
                    **self._sync_point_base, **self._render_verdict_fields(),
                    "lastRun": report.to_json(),
                    # Before this run has a frontier, the last run's checkpoint is still the one to resume.
                    "resume": self._checkpoint_text() if self._frontier is not None
                    else self._sync_point_base.get("resume", ""),
                },
            )
        except Exception as e:
            self.logger.warning("Could not save the report of an unfinished web crawl: %s", e)

    def _identity_hint(self) -> str:
        """When the site refused most of the crawl as PipesHub, say what would let it through."""
        refused = sum(1 for r in self.retry_urls.values() if r.status_code == HTTPStatus.FORBIDDEN)
        answered = refused + len(self._kept_urls)
        if self.browser_like_identity or refused < MIN_REFUSALS_FOR_HINT or refused * 2 < answered:
            return ""
        return (
            f"\nThe site refused {refused} of {answered} pages to the crawler. If you may crawl it, turn on "
            "Identify as a web browser (Advanced settings)."
        )

    @staticmethod
    def _budget_summary(stop: StopReason | None) -> str:
        if stop is StopReason.TIME_LIMIT:
            return "\nThe crawl reached its time limit; the next sync continues with the remaining pages."
        if stop is StopReason.STALLED:
            return "\nThe crawl stopped because no page answered for a long time; the next sync tries again."
        if stop is StopReason.RATE_LIMITED:
            return (
                "\nThe site kept answering \"too many requests\", so the crawl stopped asking; the next sync "
                "continues with the remaining pages."
            )
        return ""

    async def _crawl_single_page(self, url: str) -> None:
        """Crawl a single page and index it."""
        try:
            if not await self._robots_allows(url):
                return
            record_update = await self._fetch_and_process_url(url, depth=0)

            self.visited_urls.add(self._normalize_url(url))

            if record_update is None:
                return
            self._note_reached(record_update)
            await self._forget_redirected_url(url, record_update)
            file_record = record_update.record
            if file_record:
                is_disabled = self._check_index_filter(file_record)
                if is_disabled:
                    file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                if record_update.is_updated:
                    await self._handle_record_updates(record_update)
                elif (
                    record_update.is_new and record_update.record is not None and record_update.new_permissions is not None
                ):
                    # A new page Max Pages has no room for is not written at all, not even its permissions.
                    if self._admit_new_record(record_update.record):
                        pair: Tuple[Record, List[Permission]] = (record_update.record, record_update.new_permissions)
                        await self._write_new_records([pair])
                        self.processed_urls += 1
                elif self.full_sync and record_update.record is not None:
                    await self.data_entities_processor.on_updated_record_permissions(record_update.record, record_update.new_permissions)
                    self.processed_urls += 1

        except BrowserUnavailableError:
            raise
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
                    # Placeholders carry no content of their own and this upsert runs
                    # on every crawl, so the stored version is carried over rather than
                    # bumped — and rather than reset to 0, which would discard the
                    # version of an ancestor since crawled as a page in its own right.
                    version=0 if not existing else (existing.version or 0),
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
            # Backstop for a fetch that never returns: the crawl loop checks the budget between fetches.
            watcher = asyncio.create_task(
                self._budget.watch(producer_task, interval=min(30.0, self.stall_seconds / 4))
            ) if self._budget is not None else None

            batch_records: List[Tuple[FileRecord, List[Permission]]] = []
            # Pages in batch_records: not written yet, so still to do if the crawl stops before the flush.
            batch_keys: list[str] = []

            try:
                while True:
                    crawl_result = await result_queue.get()
                    if crawl_result is None:
                        break
                    key = self._normalize_url(crawl_result.url)
                    # Settled only once it is written (or there was nothing to write): a page whose
                    # processing was cut short stays to-do for the checkpoint.
                    if await self._consume(crawl_result, batch_records):
                        batch_keys.append(key)
                    else:
                        self._in_flight.pop(key, None)
                    if len(batch_records) >= self.batch_size:
                        await self._write_new_records(batch_records)
                        self.processed_urls += len(batch_records)
                        batch_records.clear()
                        self._settle_in_flight(batch_keys)
            finally:
                if watcher is not None:
                    watcher.cancel()
                if not producer_task.done():
                    producer_task.cancel()
                    # Nobody reads the queue any more: empty it, or the producer's end marker waits on a
                    # full queue for ever. Dropped results stay in _in_flight, so the checkpoint keeps them.
                    while not result_queue.empty():
                        result_queue.get_nowait()
                    try:
                        await producer_task
                    except (asyncio.CancelledError, Exception):
                        pass

            if batch_records:
                await self._write_new_records(batch_records)
                self.processed_urls += len(batch_records)
                self._settle_in_flight(batch_keys)

            if producer_error is not None:
                raise producer_error

        except BrowserUnavailableError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error in recursive crawl: {e}", exc_info=True)
            raise

    async def _consume(
        self, crawl_result: CrawlFetchResult, batch_records: list[tuple[FileRecord, list[Permission]]],
    ) -> bool:
        """Build one fetched page's record and write it, or queue it in ``batch_records`` (then True)."""
        try:
            record_update = await self._fetch_and_process_url(
                crawl_result.url,
                crawl_result.depth,
                referer=crawl_result.referer,
                prefetched_result=crawl_result.fetch_response,
            )
        except Exception as e:
            self.logger.warning("⚠️ Failed to process %s: %s", crawl_result.url, e)
            return False

        if record_update is None:
            return False
        self._note_reached(record_update)
        await self._forget_redirected_url(crawl_result.url, record_update)

        file_record = record_update.record
        if file_record and self._check_index_filter(file_record):
            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        if record_update.is_updated:
            await self._handle_record_updates(record_update)
            self.processed_urls += 1
        elif record_update.is_new and record_update.record is not None and record_update.new_permissions is not None:
            if not self._admit_new_record(record_update.record):
                # Kept by the fetching side before the store filled up: it no longer uses up the crawl.
                self._kept_urls.discard(self._normalize_url(crawl_result.fetch_response.final_url or crawl_result.url))
                return False
            batch_records.append((record_update.record, record_update.new_permissions))
            return True
        elif self.full_sync and record_update.record is not None:
            await self.data_entities_processor.on_updated_record_permissions(record_update.record, record_update.new_permissions)
            self.processed_urls += 1
        return False

    async def _write_new_records(self, batch: list[tuple[Record, list[Permission]]]) -> None:
        await self._store_with_retry(lambda: self.data_entities_processor.on_new_records(batch), f"{len(batch)} page(s)")

    async def _store_with_retry(self, write: Callable[[], Awaitable[None]], what: str) -> None:
        """Run a records write, riding out a short database or broker blip; a longer outage fails the
        sync, whose checkpoint keeps the pages not yet written to do."""
        for wait in (*STORE_RETRY_WAITS, None):
            try:
                await write()
                return
            except Exception as e:
                if wait is None:
                    raise
                self.logger.warning("Writing %s failed (%s); trying again in %.0fs", what, e, wait)
                await asyncio.sleep(wait)

    def _settle_in_flight(self, keys: list[str]) -> None:
        for key in keys:
            self._in_flight.pop(key, None)
        keys.clear()

    def _settings_fingerprint(self) -> str:
        """The settings that decide what a crawl visits: a checkpoint taken under others doesn't apply."""
        return settings_fingerprint({
            "url": self.url, "depth": self.max_depth, "discovery": self.discovery,
            "ignore": sorted(self.ignore_query_params), "contain": sorted(self.url_should_contain),
            "external": self.follow_external, "start_path": self.restrict_to_start_path,
            "robots": self.respect_robots_txt,
        })

    def _resume_made_progress(self) -> bool:
        """False for a resumed crawl that reached no page beyond its checkpoint and has nothing new left.

        Its queue was only pages that keep failing until the time runs out (a hanging page, one that
        is always rate limited), so resuming again would do the same on every sync and never revisit
        the site: changed and new pages would go unseen. The next sync starts over instead. A page
        still queued that this run never fetched (a rate-limit stop leaves the rest for later) is
        progress to keep.
        """
        if not self._resumed or self._resume is None or len(self.visited_urls) > len(self._resume.visited):
            return True
        queued = {self._normalize_url(url) for url, _, _ in self._frontier or ()} | set(self._in_flight) | set(self._fetching)
        return any(url not in self.retry_urls and url not in self.visited_urls for url in queued)

    def _checkpoint_text(self) -> str:
        """This crawl's progress, encoded for the sync point; empty when there is no crawl to resume."""
        if self._frontier is None:
            return ""
        in_flight = set(self._in_flight)
        queue = [(url, depth) for url, depth, _ in self._frontier]
        queue += list(self._in_flight.values()) + list(self._fetching.values())
        # Failed pages too, retried or not: a stop or crash never wrote their failed-page records.
        queue += [(r.url, r.depth) for r in self.retry_urls.values() if not r.deferred]
        return CrawlCheckpoint(
            fingerprint=self._settings_fingerprint(),
            saved_at_ms=get_epoch_timestamp_in_ms(),
            visited=sorted(self.visited_urls - in_flight),
            kept=sorted(self._kept_urls - in_flight),
            queue=list(dict.fromkeys(queue)),
        ).encode()

    async def _maybe_checkpoint(self) -> None:
        """Save progress every so many pages or minutes, so a crash or restart loses little of the crawl."""
        if self._sync_point_key is None:
            return
        now = self._monotonic()
        if (
            len(self._kept_urls) - self._checkpoint_kept < CHECKPOINT_EVERY_PAGES
            and now - self._checkpoint_at < CHECKPOINT_EVERY_SECONDS
        ):
            return
        self._checkpoint_kept, self._checkpoint_at = len(self._kept_urls), now
        self._sync_point_base["resume"] = self._checkpoint_text()
        try:
            await self.record_sync_point.update_sync_point(self._sync_point_key, dict(self._sync_point_base))
        except Exception as e:
            self.logger.warning("Could not save the crawl's progress: %s", e)

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
        if self._resume is not None:
            queue = deque((url, url_depth, None) for url, url_depth in self._resume.queue)
            self.visited_urls.update(self._resume.visited)
            self._kept_urls.update(self._resume.kept)
            self._start_page_kept = self._normalize_url(start_url) in self._kept_urls
            self._resumed = True
            self.logger.info(
                "Resuming the unfinished crawl: %d page(s) already done, %d to go",
                len(self._resume.visited), len(queue),
            )
        self._frontier = queue
        self._later_seeds.clear()
        if self.discovery != DISCOVERY_LINKS:
            # The start URL's section first: the rest of the site's sitemap waits until the crawl of
            # that section runs out, so a small Max Pages isn't spent elsewhere.
            for url in await self._sitemap_seeds():
                (queue if self._in_start_section(url) else self._later_seeds).append((url, depth + 1, None))

        # Pages being fetched, over plain HTTP or in the browser.
        pending: set[asyncio.Future[tuple[str, int, str | None, FetchResponse | None]]] = set()
        try:
            async for crawled in self._crawl_loop(queue, pending):
                yield crawled
        finally:
            for task in pending:
                task.cancel()

        self._page_limit_reached = bool(queue or self._later_seeds) and not self._pages_left()

    async def _crawl_loop(
        self,
        queue: deque[tuple[str, int, str | None]],
        pending: set[asyncio.Future[tuple[str, int, str | None, FetchResponse | None]]],
    ) -> AsyncGenerator[CrawlFetchResult, None]:
        while (queue or pending or self.retry_urls or self._later_seeds) and self._pages_left():
            if self._budget is not None and self._budget.check() is not None:
                return
            if not queue and not pending and self._later_seeds:
                queue.extend(self._later_seeds)
                self._later_seeds.clear()
            if not queue and not pending:
                # Re-enqueue retry candidates that haven't hit the max-retry limit.
                # Exhausted entries are left for process_retry_urls() at the end.
                retry_candidates = [
                    r for r in self.retry_urls.values()
                    if r.retries < MAX_RETRIES and not r.deferred
                ]
                if not retry_candidates:
                    break

                now = asyncio.get_event_loop().time()

                # Group by domain and schedule per-domain exponential backoff so
                # a rate-limited domain doesn't block crawling of other domains.
                domain_map: dict[str, list[RetryUrl]] = {}
                for r in retry_candidates:
                    domain = urlparse(r.url).netloc
                    domain_map.setdefault(domain, []).append(r)

                for domain, candidates in list(domain_map.items()):
                    if domain not in self._domain_next_retry_at:
                        server_delays = [c.retry_after for c in candidates if c.retry_after]
                        if server_delays:
                            backoff = max(server_delays)
                        else:
                            min_retries = min(c.retries for c in candidates)
                            backoff = min(_BACKOFF_BASE * (2 ** min_retries), _BACKOFF_CAP)

                        # A site that asks for longer than the cap would hold this
                        # sync open for its whole wait, so leave its pages for the
                        # next sync instead and keep crawling everything else.
                        if backoff > MAX_RATE_LIMIT_BACKOFF:
                            for candidate in candidates:
                                candidate.deferred = True
                            del domain_map[domain]
                            self.logger.info(
                                "Rate-limited on %s: asked for %.0fs, longer than the %ds we wait; "
                                "leaving %d URL(s) for the next sync",
                                domain, backoff, MAX_RATE_LIMIT_BACKOFF, len(candidates),
                            )
                            continue

                        self._domain_next_retry_at[domain] = now + backoff
                        self.logger.info(
                            "Rate-limited on %s: backing off %.0fs before retry (%d URL(s))",
                            domain, backoff, len(candidates),
                        )

                if not domain_map:
                    continue

                # Sleep only until the soonest eligible domain is ready.
                earliest = min(self._domain_next_retry_at[d] for d in domain_map)
                sleep_secs = max(0.0, earliest - now)
                if sleep_secs > 0:
                    self.logger.info("Sleeping %.0fs before next domain retry", sleep_secs)
                    await asyncio.sleep(sleep_secs)

                now = asyncio.get_event_loop().time()

                # Re-enqueue only domains whose backoff has elapsed; leave others
                # for subsequent passes so their crawl is unblocked independently.
                for domain, candidates in domain_map.items():
                    if self._domain_next_retry_at.get(domain, 0) <= now:
                        for retry_entry in candidates:
                            normalized = self._normalize_url(retry_entry.url)
                            if normalized not in self.visited_urls:
                                queue.append((retry_entry.url, retry_entry.depth, retry_entry.referer))
                        del self._domain_next_retry_at[domain]

                continue

            await self._start_fetches(queue, pending)
            if not pending:
                continue
            done, still_running = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            pending.clear()
            pending.update(still_running)
            self._fetches_done += len(done)
            for task in done:
                current_url, current_depth, referer, raw_result = task.result()
                normalized_url = self._normalize_url(current_url)
                if normalized_url in self._landed_urls:
                    # A redirect fetched alongside it already landed here and was kept.
                    self._fetching.pop(normalized_url, None)
                    self.visited_urls.add(normalized_url)
                    continue
                try:
                    result = await self._validate_attempt(current_url, current_depth, referer, raw_result)
                    self._fetching.pop(normalized_url, None)

                    if normalized_url not in self.retry_urls:
                        self.visited_urls.add(normalized_url)

                    if (
                        result is None
                        or not await self._robots_allows_landing(current_url, result)
                        or not await self._keep_crawled_page(current_url, normalized_url, current_depth, result, queue)
                    ):
                        continue

                    self._in_flight[normalized_url] = (current_url, current_depth)
                    yield CrawlFetchResult(
                        url=current_url,
                        depth=current_depth,
                        referer=referer,
                        fetch_response=result,
                    )

                except Exception as e:
                    self._fetching.pop(normalized_url, None)
                    self.logger.warning("⚠️ Failed to process %s: %s", current_url, e)
            # After every round, not only after a kept page: a long run of failed pages still saves progress.
            await self._maybe_checkpoint()

    async def _start_fetches(
        self,
        queue: deque[tuple[str, int, str | None]],
        pending: set[asyncio.Future[tuple[str, int, str | None, FetchResponse | None]]],
    ) -> None:
        """Start fetching queued URLs until this connector's share of fetches is in flight."""
        while (
            queue and len(pending) < self._crawl_concurrency
            and len(self._kept_urls) + len(pending) < self.max_pages
            and self._fetches_started < self.max_pages * FETCHES_PER_KEPT_PAGE
        ):
            current_url, current_depth, referer = queue.popleft()
            normalized_url = self._normalize_url(current_url)
            if normalized_url in self.visited_urls or normalized_url in self._landed_urls or normalized_url in self._fetching:
                continue
            if normalized_url in self.retry_urls and self.retry_urls[normalized_url].retries >= MAX_RETRIES:
                continue
            if current_depth > self.max_depth:
                continue
            if not await self._robots_allows(current_url):
                continue
            if await self._unchanged_since_lastmod(current_url, normalized_url):
                continue
            self._fetching[normalized_url] = (current_url, current_depth)
            self._fetches_started += 1
            pending.add(asyncio.ensure_future(self._fetch_page(current_url, current_depth, referer)))

    async def _fetch_page(
        self, url: str, depth: int, referer: str | None,
    ) -> tuple[str, int, str | None, FetchResponse | None]:
        """One page; while its section's rendering is undecided, one page of that section at a time, so
        the others wait for the check instead of being fetched twice."""
        section = self._section_of(url)
        # Only first tries say how the site treats the crawl: retries are pages that failed already.
        first_try = self._normalize_url(url) not in self.retry_urls
        if self.use_headless_browser or section in self._browser_sections or (
            self._plain_sections.get(section, 0) >= SHELL_CHECKS_PER_SECTION
            # Files and unchanged pages decide nothing; past a few, the section's pages run freely again.
            or self._section_passes.get(section, 0) >= SECTION_PAGES_HELD
        ):
            fetched = await self._fetch_page_now(url, depth, referer)
        else:
            async with self._section_locks.setdefault(section, asyncio.Lock()):
                try:
                    fetched = await self._fetch_page_now(url, depth, referer)
                finally:
                    self._section_passes[section] = self._section_passes.get(section, 0) + 1
        if self._budget is not None and fetched[3] is not None and first_try:
            self._budget.page_answered(rate_limited=fetched[3].status_code in RATE_LIMITED_STATUSES)
        return fetched

    async def _fetch_page_now(
        self, url: str, depth: int, referer: str | None,
    ) -> tuple[str, int, str | None, FetchResponse | None]:
        """One page, within the site's and the process's limits: in the browser in Robust Mode, else over
        plain HTTP with the browser as a second try."""
        host = urlparse(url).netloc.lower()
        origin = f"{urlparse(url).scheme}://{host}"
        rules = self._robots.get(origin)
        raw_result: FetchResponse | None = None
        try:
            # The site's gate first: waiting on a slowed-down site must not hold the process's slots.
            async with shared_host_gate.slot(
                host, min_interval=rules.crawl_delay if rules else None, now=self._monotonic, sleep=asyncio.sleep,
            ), shared_fetch_slots.slot():
                if self.use_headless_browser and self.crawl4ai_fetcher:
                    raw_result = await self._headless_fetch(url)
                    shared_host_gate.observe(host, raw_result.status_code if raw_result else None, now=self._monotonic())
                    return url, depth, referer, raw_result
                if (served := self._served_pages.pop(self._normalize_url(url), None)) is not None:
                    return url, depth, referer, served
                if self._section_of(url) in self._browser_sections and self.crawl4ai_fetcher:
                    raw_result = await self._headless_fetch(url)
                    shared_host_gate.observe(host, raw_result.status_code if raw_result else None, now=self._monotonic())
                    return url, depth, referer, raw_result
                raw_result = await fetch_url_with_fallback(
                    browser_like=self.browser_like_identity,
                    url=url,
                    session=self.session,  # type: ignore[arg-type]
                    logger=self.logger,
                    referer=referer,
                    validators_for=functools.partial(self._conditional_headers, links_needed=depth < self.max_depth),
                    timeout=15,
                    max_size_mb=self.max_size_mb,
                    allow_hop=self._hop_allowed,
                )
                shared_host_gate.observe(host, raw_result.status_code if raw_result else None, now=self._monotonic())
                if self._should_try_crawl4ai_fallback(raw_result, url) and await self._ensure_crawl4ai_fetcher():
                    self.logger.info(
                        "🌐 [crawl4ai fallback] Non-headless strategies failed for %s (status=%s) — trying headless",
                        url, raw_result.status_code if raw_result else "connection error",
                    )
                    crawl4ai_resp = await self._headless_fallback_fetch(url)
                    if crawl4ai_resp is not None and crawl4ai_resp.success and crawl4ai_resp.status_code < HttpStatusCode.BAD_REQUEST.value:
                        raw_result = crawl4ai_resp
                else:
                    raw_result = await self._rendered_if_shell(url, raw_result)
        except BrowserUnavailableError:
            raise
        except Exception as e:
            self.logger.warning("⚠️ Failed to fetch %s: %s", url, e)
        return url, depth, referer, raw_result

    async def _sitemap_seeds(self) -> list[str]:
        """The site's sitemap URLs in scope, newest first; remembers each one's lastmod."""
        if not self.url or self.session is None:
            return []
        origin = f"{urlparse(self.url).scheme}://{urlparse(self.url).netloc}"
        roots: list[str] = []
        if self.respect_robots_txt and await self._robots_allows(self.url):
            rules = self._robots.get(origin.lower())
            roots = [urljoin(origin + "/", s) for s in (rules.sitemaps if rules else [])]
        roots = roots or [origin + path for path in DEFAULT_SITEMAP_PATHS]
        # Docs published under a path usually keep their own sitemap there (/docs/sitemap.xml).
        if (folder := self._start_folder()) != "/":
            roots += [urljoin(origin, folder + path.lstrip("/")) for path in DEFAULT_SITEMAP_PATHS]
        found, self._sitemap_report = await read_sitemaps(
            roots,
            self._fetch_sitemap,
            keep=lambda u: self._is_valid_url(u, self.url or "") and not self._outside_crawl(u),
            max_urls=self.max_pages * FETCHES_PER_KEPT_PAGE,
        )
        seeds: list[str] = []
        for entry in found:
            url = self._fetch_url(entry.url)
            if entry.lastmod_ms is not None:
                self._sitemap_lastmod[self._normalize_url(url)] = entry.lastmod_ms
            seeds.append(url)
        self._sitemap_urls = len(seeds)
        self.logger.info(
            "Sitemaps: %d page(s) listed in %d file(s)%s", len(seeds), self._sitemap_report.files_read,
            f", limits hit: {sorted(self._sitemap_report.limits_hit)}" if self._sitemap_report.limits_hit else "",
        )
        return seeds

    async def _fetch_sitemap(self, url: str, limit: int) -> bytes | None:
        """A sitemap's body (at most ``limit`` bytes, a byte more marks it too large), or None."""
        if self.session is None or await is_unsafe_url(url):
            return None
        if self.respect_robots_txt and not await self._robots_allows(url):
            return None
        try:
            async with self.session.get(
                url, headers=build_crawler_headers(url, browser_like=self.browser_like_identity), allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=SITEMAP_TIMEOUT_SECONDS),
            ) as response:
                if response.status != HTTPStatus.OK:
                    return None
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > limit:
                        return None
                return bytes(body)
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as e:
            self.logger.info("Couldn't read sitemap %s: %s", url, e)
            return None

    async def _unchanged_since_lastmod(self, url: str, normalized: str) -> bool:
        """In sitemap-only discovery, a stored page the sitemap says hasn't changed since is not fetched.

        With links in play the page is fetched anyway: its links may lead to pages no sitemap lists.
        """
        lastmod = self._sitemap_lastmod.get(normalized)
        if self.discovery != DISCOVERY_SITEMAP or lastmod is None:
            return False
        stored = await self._stored_record(url)
        # A folder placeholder is rewritten every sync, so its time says nothing about the page.
        if (
            stored is None or stored.is_internal or stored.indexing_status == ProgressStatus.FAILED.value
            or (stored.updated_at or 0) < lastmod
        ):
            return False
        self.visited_urls.add(normalized)
        self._kept_urls.add(normalized)
        self._reached_record_ids.add(stored.external_record_id)
        self._unchanged_by_lastmod += 1
        return True

    def _page_robots_directives(self, result: FetchResponse) -> set[str]:
        """``noindex`` / ``nofollow`` from the page's robots meta tags and X-Robots-Tag header.

        Read for robots or PipesHub, and only while the connector respects robots.txt: they are the
        same request from the site, made per page.
        """
        if not self.respect_robots_txt:
            return set()
        values = [
            v for v in (self._header(result.headers, "X-Robots-Tag") or "").split(",") if v.strip()
        ]
        content_type = (self._header(result.headers, "Content-Type") or "").lower()
        if result.content_bytes and "html" in content_type:
            try:
                metas = BeautifulSoup(result.content_bytes, "html.parser", parse_only=SoupStrainer("meta"))
                for meta in metas.find_all("meta"):
                    if str(meta.get("name", "")).lower() in ("robots", ROBOTS_USER_AGENT.lower()):
                        values.extend(str(meta.get("content", "")).split(","))
            except Exception as e:
                self.logger.debug("Could not read the robots meta tags of %s: %s", result.final_url, e)
        directives: set[str] = set()
        for value in values:
            agent, _, directive = value.strip().lower().rpartition(":")
            if agent and agent.strip() not in ("robots", ROBOTS_USER_AGENT.lower()):
                continue  # "otherbot: noindex" is for another crawler
            directive = directive.strip()
            directives |= {"noindex", "nofollow"} if directive == "none" else {directive}
        return directives & {"noindex", "nofollow"}

    def _session_headers(self) -> dict[str, str]:
        if not self.browser_like_identity:
            return {"User-Agent": PIPESHUB_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
        return {
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

    def _gone_marks(self, outcome: RunOutcome) -> set[str]:
        """Pages to remove if the next sync finds them gone again. A partial crawl keeps the earlier
        marks of pages it didn't get to, or a site that keeps resuming would never remove them."""
        if outcome is not RunOutcome.PARTIAL:
            return self._gone_this_sync
        return self._gone_this_sync | (self._gone_last_sync - self._reached_record_ids)

    def _stopped_short(self) -> bool:
        """The crawl stopped with pages left to visit: Max Pages, the fetch cap, or new pages the store
        had no room for. Stored pages it didn't reach may still be on the site, so none is pruned. This
        covers a store already over Max Pages (older versions capped visits, not stored pages, and a
        lowered Max Pages does the same): the pages over the cap are kept, not deleted."""
        return self._page_limit_reached

    def _pages_left(self) -> bool:
        # Fetches that failed count too: a site of broken links must not be crawled without end.
        return (
            len(self._kept_urls) < self.max_pages
            and self._fetches_done < self.max_pages * FETCHES_PER_KEPT_PAGE
        )

    async def _keep_crawled_page(
        self,
        requested_url: str,
        normalized_url: str,
        depth: int,
        result: FetchResponse,
        queue: deque[tuple[str, int, str | None]],
    ) -> bool:
        """Queue a fetched page's links, and say whether the page itself is kept.

        A redirected page is read as the page it landed on: its links resolve against that
        URL and the file-type filter sees that URL. The landing URL is remembered only once
        the page is kept, so a dropped copy never stops the page being crawled directly.
        """
        final_url = result.final_url
        landed = self._normalize_url(final_url)
        redirected = landed != normalized_url
        if redirected and (landed in self.visited_urls or landed in self._landed_urls):
            if landed in self._kept_urls:
                # A duplicate of a page this sync kept, so the URL asked for is now an old name for it:
                # its own record goes the same way as any other redirect source's.
                landing_record = await self._stored_record(final_url)
                await self._handle_gone_page(requested_url, keep_id=landing_record.id if landing_record else None)
            return False

        directives = self._page_robots_directives(result)
        if (
            depth < self.max_depth and result.content_bytes and self.discovery != DISCOVERY_SITEMAP
            and "nofollow" not in directives
        ):
            try:
                for link in self._extract_links_from_html(final_url, result.content_bytes):
                    normalized_link = self._normalize_url(link)
                    if (
                        normalized_link not in self.visited_urls
                        and normalized_link not in self._landed_urls
                        and normalized_link not in self.retry_urls
                        and self._pages_left()
                    ):
                        queue.append((link, depth + 1, final_url))
            except Exception:
                pass

        if "noindex" in directives:
            self._noindex_skipped += 1
            stored = await self._stored_record(final_url)
            if stored is not None and not stored.is_internal:
                self.logger.info("Removing %s: the page now asks not to be indexed", final_url)
                await self._remove_record(stored, final_url)
            return False
        if await self._excluded_by_extension_filter(result):
            await self._remove_filtered_out(result, requested_url)
            return False
        if redirected:
            self._landed_urls.add(landed)
        if len(self._stored_page_ids) >= self.max_pages:
            stored = await self._stored_record(final_url)
            if stored is None or stored.is_internal:
                # A new page with no room under Max Pages: it isn't kept, so it doesn't use up the crawl,
                # which still has to reach every page it stores (or pruning would take them).
                self._skipped_for_max_pages += 1
                return False
        self._kept_urls.add(landed)
        if depth == 0:
            self._start_page_kept = True
        return True

    def _should_try_crawl4ai_fallback(self, result: FetchResponse | None, url: str | None = None) -> bool:
        """Return True when non-headless strategies failed and crawl4ai is worth trying."""
        if url and self._is_document_url(url):
            return False  # a browser can't return the file itself
        if result is None:
            return True  # Hard connection error — headless may succeed
        if result.status_code < HttpStatusCode.BAD_REQUEST.value:
            return False  # Already successful
        # Absent pages, or over the size limit (413 is fetch_strategy's size-guard skip):
        # headless won't change the answer.
        if result.status_code in {404, 405, 410, 413, HTTPStatus.TOO_MANY_REQUESTS}:
            return False  # a 429 asks for fewer requests, which a browser render isn't
        if result.status_code == HTTPStatus.FORBIDDEN and not self.browser_like_identity:
            return False  # a browser would only be a disguise
        if result.headers.get("X-Fetch-Skip-Reason") in {"too_many_redirects", "unsafe_address"}:
            return False  # the browser would follow the same chain, without checking each hop
        return True  # Bot-block, rate-limit, or server error — try headless

    async def _ensure_crawl4ai_fetcher(self) -> Optional[Crawl4AIFetcher]:
        """Return the shared crawl4ai fetcher, initialising it on first use."""
        if self.crawl4ai_fetcher is None:
            try:
                self.crawl4ai_fetcher = await get_shared_fetcher(browser_like=self.browser_like_identity)
            except Exception as e:
                self.logger.warning("⚠️ Failed to initialise crawl4ai fetcher for fallback: %s", e)
                return None
        return self.crawl4ai_fetcher

    async def _robots_allows(self, url: str) -> bool:
        """Whether the site's robots.txt lets us fetch ``url``; read once per site per crawl."""
        if not self.respect_robots_txt:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        if origin not in self._robots:
            self._robots[origin] = await shared_robots_cache.rules(origin, functools.partial(self._read_robots, origin))
        rules = self._robots[origin]
        allowed = rules is not None and rules.allows(url)
        if not allowed:
            normalized = self._normalize_url(url)
            self._robots_skipped.add(normalized)
            # Never fetched, so never retried either: a queued retry would come back here forever.
            self.retry_urls.pop(normalized, None)
        return allowed

    async def _hop_allowed(self, url: str) -> bool:
        """Whether a redirect target may be requested at all: inside the crawl, and allowed by robots.txt.

        A refused target comes back as an empty response at that URL, which validation (scope)
        or the landing check (robots.txt) then drops, the same as a target that was fetched.
        """
        return not self._outside_crawl(url) and await self._robots_allows(url)

    async def _robots_allows_landing(self, requested_url: str, result: FetchResponse) -> bool:
        """robots.txt applies to where a redirect landed as well; the queued URL was checked before fetching."""
        # Raw URLs, not dedupe keys: /secret and /secret/ are one page but can have different rules.
        if urldefrag(result.final_url).url == urldefrag(requested_url).url:
            return True
        return await self._robots_allows(result.final_url)

    async def _read_robots(self, origin: str) -> RobotsRules | None:
        """RFC 9309: a missing or refused robots.txt (4xx) allows everything; one that can't be read (5xx,
        429, no answer) means crawl nothing on that site for now."""
        if self.session is None:
            return None
        url = f"{origin}/robots.txt"
        # A few short attempts, not the page fetcher's rate-limit backoff, which could hold the sync for minutes.
        for wait in (*ROBOTS_RETRY_WAITS, None):
            problem: object
            try:
                async with self.session.get(
                    url, headers=build_crawler_headers(url, browser_like=self.browser_like_identity), allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=ROBOTS_TIMEOUT_SECONDS),
                ) as response:
                    status = response.status
                    body = bytearray()
                    if status < 400:
                        # RFC 9309 §2.5 lets a crawler stop after 500 KiB; a site shouldn't hold a worker's memory.
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            body.extend(chunk)
                            if len(body) >= ROBOTS_MAX_BYTES:
                                del body[ROBOTS_MAX_BYTES:]
                                break
                if status != HTTPStatus.TOO_MANY_REQUESTS and status < 500:
                    break
                problem = status
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as e:
                problem = e
            if wait is None:
                self.logger.warning("Couldn't read %s (%s); not crawling that site this sync", url, problem)
                return None
            await asyncio.sleep(wait)
        if status >= 400:
            return RobotsRules()
        # utf-8-sig: a leading byte-order mark would otherwise hide the first "User-agent" line.
        return RobotsRules.parse(bytes(body).decode("utf-8-sig", "replace"), ROBOTS_USER_AGENT)

    def _robots_summary(self) -> str:
        unreadable = sorted(urlparse(origin).netloc for origin, rules in self._robots.items() if rules is None)
        blocked = len(self._robots_skipped) - sum(
            1 for url in self._robots_skipped if urlparse(url).netloc in unreadable
        )
        summary = ""
        if blocked:
            summary += (
                f"\nSkipped {blocked} pages that the site's robots.txt asks crawlers not to visit. "
                "To include them, turn off Respect robots.txt (Advanced settings)."
            )
        if unreadable:
            summary += (
                f"\nCouldn't read robots.txt for {', '.join(unreadable)}, so it wasn't crawled this time. "
                "PipesHub will try again on the next sync."
            )
        return summary

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

    async def _check_script_rendering(self, url: str, *, keep_robots: bool = False) -> None:
        """Switch to the browser if the start page is script-rendered. The check loads the start page
        in a browser, which follows redirects itself, so robots.txt and the redirects come first."""
        may_open = await self._start_page_may_open_in_browser(url, keep_robots=keep_robots)
        self._script_check_pending = may_open is None
        if not may_open:
            return
        self._csr_detected = await self._detect_csr(url)
        self._render_verdict = ("csr" if self._csr_detected else "ssr", get_epoch_timestamp_in_ms())
        if self._csr_detected:
            # The user didn't ask for a browser, so one that can't start means plain HTTP, not a failed init.
            self.use_headless_browser = await self._ensure_crawl4ai_fetcher() is not None

    async def _apply_stored_render_verdict(self) -> bool:
        """Use the last week's verdict on whether the site renders with scripts, instead of probing again."""
        try:
            stored = await self.record_sync_point.read_sync_point(self._sync_point_key_for(self.url or ""))
        except Exception as e:
            self.logger.debug("Could not read the stored rendering verdict: %s", e)
            return False
        verdict, at = (stored or {}).get("renderVerdict"), (stored or {}).get("renderVerdictAt") or 0
        if verdict not in ("csr", "ssr") or get_epoch_timestamp_in_ms() - int(at) > RENDER_VERDICT_MAX_AGE_MS:
            return False
        self._render_verdict = (verdict, int(at))
        self._csr_detected = verdict == "csr"
        self.use_headless_browser = self._csr_detected
        return True

    def _render_verdict_fields(self) -> dict[str, object]:
        if self._render_verdict is None:
            return {}
        return {"renderVerdict": self._render_verdict[0], "renderVerdictAt": self._render_verdict[1]}

    @staticmethod
    def _sync_point_key_for(url: str) -> str:
        return generate_record_sync_point_key(RecordType.WEBPAGE.value, "webpages", url)

    async def _start_page_may_open_in_browser(self, url: str, *, keep_robots: bool) -> bool | None:
        """Whether robots.txt and the crawl's scope let the browser load the start page and wherever
        it redirects; None when robots.txt couldn't be read, so the check is tried again next sync.
        ``keep_robots`` leaves the rules read here for the sync that asked; at setup nothing carries over.
        """
        if not self.respect_robots_txt:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        try:
            allowed = await self._robots_allows(url)
            if self._robots.get(origin) is None:
                return None
            if not allowed:
                return False
            probed = await self._probe_landing(url)
            if None in self._robots.values():
                return None  # a redirect onto a site whose robots.txt couldn't be read isn't a refusal
            return probed is None or probed[1] > 0
        finally:
            if not keep_robots:
                self._robots.clear()
                self._robots_skipped.clear()

    async def _detect_csr(self, url: str) -> bool:
        """Detect whether *url* is client-side rendered.

        Uses a single headless-browser fetch with two ``innerText``
        snapshots — one captured at ``DOMContentLoaded`` (before JS
        hydration) via an init script, and one after the page is fully
        rendered.  Both snapshots respect CSS visibility, avoiding false
        results from CSS-hidden elements.
        """
        # The browser opens file: and other non-http URLs itself, past the guard proxy.
        if await is_unsafe_url(url):
            return False
        try:
            # The shared, governed browser: a private one per connector would sit outside every limit.
            probe = await get_shared_fetcher(browser_like=self.browser_like_identity)
            try:
                rendered = await probe.fetch(
                    url, browser_like=self.browser_like_identity, context_key=f"{self.connector_id}:csr-probe",
                    js_code=CSR_PROBE_JS, js_code_before_wait=PRE_HYDRATION_CAPTURE_JS,
                )
            finally:
                await release_shared_fetcher()

            if not rendered.success or not (rendered.html or "").strip():
                self.logger.warning(
                    "⚠️ CSR probe for %s returned no content — defaulting to headless", url,
                )
                return True

            js_result = rendered.js_execution_result or {}
            # What the server sends, read without running any script: at DOMContentLoaded an app's
            # deferred and module scripts have already rendered, so the in-browser snapshot is the fallback.
            served = await self._served_text_length(url)
            pre_len = served if served is not None else js_result.get("preLen", -1)
            post_len = js_result.get("postLen", 0)
            if served is not None:
                # Like with like: the served length is the extracted text, without the site's chrome.
                post_len = await self._extracted_text_length(self._crawl4ai_result_to_response(rendered, url))

            if pre_len < 0:
                self.logger.debug(
                    "🔍 CSR probe for %s: init script did not fire, "
                    "falling back to CSR assumption",
                    url,
                )
                return True

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
            return True

    async def _served_text_length(self, url: str) -> int | None:
        """Visible text in the page as the server sends it, or None if plain HTTP can't get it."""
        if self.session is None:
            return None
        try:
            served = await fetch_url_with_fallback(
                browser_like=self.browser_like_identity, url=url, session=self.session, logger=self.logger,
                timeout=15, max_size_mb=self.max_size_mb, allow_hop=self._hop_allowed,
            )
            if served is None or not served.success or served.status_code >= 400 or not served.content_bytes:
                return None
            # The crawl needs this page too: its first fetch of it is this one, not a second request.
            self._served_pages[self._normalize_url(url)] = served
            return await self._extracted_text_length(served)
        except Exception as e:
            self.logger.debug("Could not read %s over plain HTTP for the rendering check: %s", url, e)
            return None

    def _start_folder(self) -> str:
        """The start URL's folder: its path up to the last slash ("/docs/" for /docs/index.html)."""
        path = urlparse(self.url or "").path or "/"
        return path[: path.rfind("/") + 1] or "/"

    def _in_start_section(self, url: str) -> bool:
        return (urlparse(url).path or "/").startswith(self._start_folder())

    @staticmethod
    def _section_of(url: str) -> str:
        """The part of a site a page belongs to, by its first path segment: pages there share a template."""
        segments = [s for s in urlparse(url).path.split("/") if s]
        return f"/{segments[0]}/" if len(segments) > 1 else "/"

    async def _rendered_if_shell(self, url: str, result: FetchResponse | None) -> FetchResponse | None:
        """The browser's copy of a served page that is an empty shell a script fills in, else ``result``.

        The start page's check speaks for the site, but a site can serve some sections whole and build
        others in the browser. A short page is rendered once to compare; when the browser adds the
        content, the rest of that section goes to the browser directly. A section with two pages that
        are served whole, or that the browser adds nothing to, isn't checked again, so the extra renders
        stay at two per section.
        """
        section = self._section_of(url)
        if (
            result is None or result.status_code != HTTPStatus.OK or not result.content_bytes
            or "html" not in (self._header(result.headers, "Content-Type") or "").lower()
            or self._plain_sections.get(section, 0) >= SHELL_CHECKS_PER_SECTION
        ):
            return result
        served = await self._extracted_text_length(result)
        if served >= SHELL_TEXT_CHARS or b"<script" not in result.content_bytes.lower():
            # A page served whole, or with no script to fill it in, says the server renders the section.
            self._plain_sections[section] = self._plain_sections.get(section, 0) + 1
            return result
        if await self._ensure_crawl4ai_fetcher() is None:
            return result
        try:
            rendered = await self._headless_fetch(url)
        except BrowserUnavailableError as e:
            # Our browser failing isn't the page's fault: keep the served copy and stop checking.
            self.logger.warning("Not checking %s in the browser, which is unavailable: %s", url, e)
            self._plain_sections[section] = SHELL_CHECKS_PER_SECTION
            return result
        if rendered is None or not rendered.success or rendered.status_code >= HttpStatusCode.BAD_REQUEST.value:
            return result
        analysis = analyze_rendering(served, await self._extracted_text_length(rendered))
        if not analysis.is_csr:
            self._plain_sections[section] = self._plain_sections.get(section, 0) + 1
            return result
        self.logger.info(
            "Pages under %s are built by scripts (%d characters served, %d rendered): using the browser for them",
            section, analysis.raw_text_length, analysis.rendered_text_length,
        )
        self._browser_sections.add(section)
        return rendered

    async def _extracted_text_length(self, page: FetchResponse | None) -> int:
        if page is None or not page.content_bytes:
            return 0
        _, text = await asyncio.to_thread(self._page_title_and_text, page.content_bytes, page.final_url)
        return len(text.decode("utf-8", "replace"))

    def _crawl4ai_result_to_response(self, fetch_result: FetchResult, url: str) -> Optional[FetchResponse]:
        # The site's own status is kept for the reason shown; 0 means the browser got no answer.
        status_code = resolve_fetch_status_code(
            fetch_result.status_code,
            fetch_result.error,
        ) or 0

        # The page's own headers: X-Robots-Tag decides indexing, ETag/Last-Modified later 304s.
        headers = dict(fetch_result.headers)
        if fetch_result.content_type:
            headers["Content-Type"] = fetch_result.content_type
        if not fetch_result.success or not (fetch_result.html or "").strip():
            return FetchResponse(
                status_code=status_code,
                content_bytes=b"",
                headers=headers,
                final_url=fetch_result.url or url,
                strategy="crawl4ai",
                success=False,
                error_message=fetch_result.error,
            )
        # The browser already decoded the page; re-encoded as UTF-8, its own charset declaration
        # would make the parser decode the bytes a second time, wrongly.
        html = _META_CHARSET.sub('<meta charset="utf-8">', fetch_result.html)
        return FetchResponse(
            status_code=status_code,
            content_bytes=html.encode("utf-8"),
            headers=headers,
            final_url=fetch_result.url,
            strategy="crawl4ai",
        )

    def _is_document_url(self, url: str) -> bool:
        """A browser can't hand back a PDF's or DOCX's bytes, only its viewer page or an aborted download."""
        return self._determine_mime_type(url, "")[0] != MimeTypes.HTML

    async def _fetch_linked_document(self, url: str) -> FetchResponse | None:
        """Fetch a linked document, walking its redirects first so nothing outside the crawl is requested."""
        if self._outside_crawl(url):
            return self._out_of_scope_response(url)
        probed = await self._probe_landing(url)
        if probed is None:
            return None  # the site answered neither HEAD nor GET; recorded as unreachable
        landing, status, _content_type = probed
        if status == PROBE_UNENDING:
            return too_many_redirects_response(url)
        if self._outside_crawl(landing):
            return self._out_of_scope_response(landing)
        if landing != url and not await self._robots_allows(landing):
            return self._robots_skip_response(landing)
        return await self._fetch_document(landing)

    async def _fetch_document(self, url: str) -> FetchResponse | None:
        if self.session is None:
            return None
        return await fetch_url_with_fallback(
            browser_like=self.browser_like_identity,
            url=url, session=self.session, logger=self.logger, timeout=15, max_size_mb=self.max_size_mb,
            validators_for=functools.partial(self._conditional_headers, links_needed=False),
            allow_hop=self._hop_allowed,
        )

    @staticmethod
    def _header(headers: dict, name: str) -> str | None:
        wanted = name.lower()
        return next((value for key, value in headers.items() if key.lower() == wanted), None)

    async def _conditional_headers(self, url: str, *, links_needed: bool) -> dict[str, str] | None:
        """Ask the site to skip the body when our stored copy is current (it answers 304).

        Not for a page whose links the crawl still needs, since a 304 carries no body to read them from.
        """
        if links_needed and not self._is_document_url(url):
            return None
        record = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id, external_record_id=self._ensure_trailing_slash(url)
        )
        if record is None or not record.storage_document_id:
            return None
        etag, last_modified = await self._stored_validators(record)
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return headers or None

    async def _stored_validators(self, record: Record) -> tuple[str | None, str | None]:
        """The ETag and Last-Modified stored for ``record``.

        A lookup by external id returns the plain record; they are fields of its file record.
        """
        if not isinstance(record, FileRecord):
            record = await self.data_entities_processor.get_file_record_by_id(record.id) or record
        return getattr(record, "etag", None), getattr(record, "ctag", None)

    async def _headless_fetch(self, url: str, *, walk_first: bool = True) -> FetchResponse | None:
        """Fetch a single URL via crawl4ai (used outside the BFS crawl loop); documents go over plain HTTP.

        ``walk_first=False`` is for fetches that aren't part of a crawl (a stored record opened on
        demand), and for a retry of a page whose redirects were just walked.
        """
        if self.crawl4ai_fetcher is None:
            return None
        # The browser opens file: and other non-http URLs itself, past the guard proxy.
        if await is_unsafe_url(url):
            return unsafe_address_response(url)
        if self._is_document_url(url):
            return await self._fetch_linked_document(url)
        if walk_first and (refused := await self._landing_refused_before_browser(url)) is not None:
            return refused
        result = await self.crawl4ai_fetcher.fetch(
            url, browser_like=self.browser_like_identity, context_key=self._browser_context_key,
        )
        if result.crashed_browser:
            return FetchResponse(
                status_code=0, content_bytes=b"", headers={"X-Fetch-Skip-Reason": "browser_crash"},
                final_url=url, strategy="crawl4ai", success=False, error_message=result.error,
            )
        return await self._fetch_document_behind_render(
            self._crawl4ai_result_to_response(result, url), url, no_answer=self._browser_got_no_answer(result),
        )

    async def _headless_fallback_fetch(self, url: str) -> FetchResponse | None:
        """The browser as a second try after plain HTTP: without one, the plain-HTTP answer stands."""
        try:
            return await self._headless_fetch(url)
        except BrowserUnavailableError as e:
            self.logger.warning("⚠️ Headless fallback skipped for %s: %s", url, e)
            return None
        except Exception as e:
            # Raising here would skip the plain-HTTP answer's validation, which is
            # what queues a rate-limited or failed page for retry.
            self.logger.warning("⚠️ Headless fallback failed for %s: %s", url, type(e).__name__)
            return None

    async def _landing_refused_before_browser(self, url: str) -> FetchResponse | None:
        """The browser follows redirects on its own, so walk them first: a hop outside the crawl or
        disallowed by robots.txt is answered here, never loaded. None means the browser may go."""
        probed = await self._probe_landing(url)
        if probed is None or probed[1] > 0:
            return None
        if probed[1] == PROBE_UNENDING:
            return too_many_redirects_response(url)
        landing = probed[0]
        if await is_unsafe_url(landing):
            return unsafe_address_response(landing)
        return self._out_of_scope_response(landing) if self._outside_crawl(landing) else self._robots_skip_response(landing)

    @staticmethod
    def _browser_got_no_answer(fetch_result: FetchResult) -> bool:
        return not fetch_result.success and resolve_fetch_status_code(fetch_result.status_code, fetch_result.error) is None

    async def _fetch_document_behind_render(
        self, response: FetchResponse | None, requested_url: str, *, no_answer: bool,
    ) -> FetchResponse | None:
        """A redirect onto a document renders its viewer page, or fails; fetch the file itself instead.

        Chromium aborts a redirect onto a file (net::ERR_ABORTED) before it reports where the page
        went, so a browser failure with no status is followed over plain HTTP, and that answer is
        kept only if it lands on a document. The plain-HTTP answer is used as it is, error status
        included, so a blocked file fails with its own status rather than being stored as viewer HTML.
        """
        if response is None:
            return response
        if self._is_document_response(response):
            if self._outside_crawl(response.final_url):
                return self._out_of_scope_response(response.final_url)
            if not await self._robots_allows(response.final_url):
                return self._robots_skip_response(response.final_url)
            return await self._fetch_document(response.final_url)
        if no_answer:
            probed = await self._probe_landing(requested_url)
            if probed is None:
                return response  # the site didn't answer the probe either; the browser retry stands
            landing, status, content_type = probed
            if status == PROBE_UNENDING:
                return too_many_redirects_response(requested_url)
            if self._outside_crawl(landing):
                return self._out_of_scope_response(landing)
            if landing != requested_url and not await self._robots_allows(landing):
                return self._robots_skip_response(landing)
            if self._is_document_url(landing) or self._is_document_type(content_type):
                # A walked, in-scope chain onto a file: fetch it, its own error or size skip included.
                return await self._fetch_document(landing)
            if status >= HttpStatusCode.BAD_REQUEST.value:
                # A page answering with an error has its status now; no browser retry needed.
                return FetchResponse(status_code=status, content_bytes=b"", headers={}, final_url=landing,
                                     strategy="probe", success=False, error_message=response.error_message)
        return response

    def _is_document_response(self, response: FetchResponse) -> bool:
        """A document by its URL or by the type the response reports.

        A browser that aborts a file as a download reports no status; ``_fetch_document_behind_render``
        then walks the redirects itself and uses the type the last hop reports.
        """
        if self._is_document_url(response.final_url):
            return True
        return self._is_document_type(self._header(response.headers, "Content-Type"))

    def _is_document_type(self, content_type: str | None) -> bool:
        return bool(content_type) and self._determine_mime_type("", content_type)[0] != MimeTypes.HTML

    def _off_site(self, url: str) -> bool:
        """Outside the site being crawled, when Follow External Links is off."""
        if not self.base_domain or self.follow_external:
            return False
        return urlparse(url).netloc.lower() != urlparse(self.base_domain).netloc.lower()

    def _outside_crawl(self, url: str) -> bool:
        return self._off_site(url) or self._excluded_by_url_should_contain(url)

    @staticmethod
    def _out_of_scope_response(url: str) -> FetchResponse:
        """Stands in for a redirect target the crawl won't fetch: validation drops it by its URL,
        and it isn't a browser block, so Robust Mode's retry leaves it alone."""
        return FetchResponse(
            status_code=0, content_bytes=b"", headers={}, final_url=url,
            strategy="scope_guard", success=False, error_message="outside the crawl's scope",
        )

    @staticmethod
    def _robots_skip_response(url: str) -> FetchResponse:
        """Stands in for a redirect target robots.txt disallows: it passes validation empty, so the
        landing check records the robots skip, and it is never downloaded or browser-retried."""
        return FetchResponse(status_code=200, content_bytes=b"", headers={}, final_url=url, strategy="robots_guard")

    async def _probe_landing(self, url: str) -> tuple[str, int, str | None] | None:
        """Follow ``url``'s redirects one hop at a time, stopping before any hop outside the crawl
        or disallowed by robots.txt.

        Each hop is asked with HEAD, or with GET (body left unread) when HEAD is refused or fails.
        Returns the landing URL, its status and Content-Type, or the first out-of-scope or
        disallowed hop, unrequested, with status 0, or the last hop checked with ``PROBE_UNENDING``
        when the chain is still redirecting after MAX_PROBE_REDIRECTS redirects, the same limit as
        a normal crawl's walk. Returns None if the site doesn't answer.
        """
        if self.session is None:
            return None
        for _ in range(MAX_PROBE_REDIRECTS + 1):
            if await is_unsafe_url(url):
                return url, 0, None  # never requested: not a public address
            try:
                status, location, content_type = await self._probe_hop("HEAD", url)
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
                status, location, content_type = None, None, None  # some servers mishandle HEAD; GET may still answer
            if status is None or status in HEAD_NOT_SUPPORTED:
                try:
                    status, location, content_type = await self._probe_hop("GET", url)
                except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
                    return None
            if not (status in REDIRECT_STATUS_CODES and location):
                return url, status, content_type
            url = urljoin(url, location)
            if self._outside_crawl(url) or not await self._robots_allows(url):
                return url, 0, None  # not requested at all: outside the crawl, or robots.txt disallows it
        return url, PROBE_UNENDING, None

    async def _probe_hop(self, method: str, url: str) -> tuple[int, str | None, str | None]:
        async with self.session.request(  # type: ignore[union-attr]
            method, url, headers=build_crawler_headers(url, browser_like=self.browser_like_identity), allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_SECONDS),
        ) as response:
            return response.status, response.headers.get("Location"), response.headers.get("Content-Type")

    def _excluded_by_url_should_contain(self, url: str) -> bool:
        """Fails the URL Should Contain setting; the start page is always crawled."""
        if not self.url_should_contain:
            return False
        if self._normalize_url(url) == self._normalize_url(self.url or ""):
            return False
        return not any(s.lower() in url.lower() for s in self.url_should_contain)

    async def _validate_attempt(
        self,
        url: str,
        depth: int,
        referer: str | None,
        result: FetchResponse | None,
    ) -> FetchResponse | None:
        """Validate one fetch of a queued URL, and make sure a URL waiting for a retry moves on.

        The crawl only ends once no retry is pending, so a retry entry this attempt left untouched
        would be re-queued for ever: it is dropped instead.
        """
        if self._budget is not None:
            self._budget.progress()
        normalized = self._normalize_url(url)
        pending = self.retry_urls.get(normalized)
        validated = await self._validate_fetch_result(url, depth, referer, result)
        if pending is not None and self.retry_urls.get(normalized) is pending:
            self.logger.warning("Retry of %s ended without an outcome; not retrying it again", url)
            del self.retry_urls[normalized]
        return validated

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
                # No status: the site never answered, so none is invented (a 408 read as a real reply).
                status_code=existing_entry.status_code if existing_entry else None,
                retries=(existing_entry.retries + 1) if existing_entry else 0,
                last_attempted=get_epoch_timestamp_in_ms(),
                depth=depth,
                referer=referer,
            )
            return None

        final_url = result.final_url

        if self._off_site(final_url):
            self.retry_urls.pop(self._normalize_url(url), None)
            return None

        if self._excluded_by_url_should_contain(final_url):
            self.retry_urls.pop(self._normalize_url(url), None)
            final_url_normalized = self._normalize_url(final_url)
            if final_url_normalized != self._normalize_url(url):
                self.visited_urls.add(final_url_normalized)
            return None

        if result.headers.get("X-Fetch-Skip-Reason") == "browser_crash":
            # It took the browser down twice: trying again this sync would only do it again.
            self._record_final_failure(url, depth, referer, None, BROWSER_CRASH_REASON)
            return None

        if result.status_code >= HttpStatusCode.BAD_REQUEST.value:
            if result.status_code == HTTPStatus.FORBIDDEN and not self.browser_like_identity:
                # Retried only to try another disguise; without one, the refusal is the answer.
                self._record_final_failure(
                    result.final_url or url, depth, referer, result.status_code, BLOCKED_REASON, queued_url=url,
                )
            elif result.status_code in RETRYABLE_STATUS_CODES:
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
                    retry_after=getattr(result, "retry_after", None),
                )
            else:
                skip = result.headers.get("X-Fetch-Skip-Reason")
                reason = (
                    self._too_large_reason() if skip == "max_size_exceeded"
                    else TOO_MANY_REDIRECTS_REASON if skip == "too_many_redirects"
                    else UNSAFE_ADDRESS_REASON if skip == "unsafe_address"
                    else None
                )
                self._record_final_failure(
                    result.final_url or url, depth, referer, result.status_code, reason, queued_url=url,
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
            # 413, as the plain fetch's size guard reports it: the page itself answered 200.
            self._record_final_failure(
                result.final_url or url, depth, referer, HTTPStatus.REQUEST_ENTITY_TOO_LARGE.value, self._too_large_reason(),
                queued_url=url,
            )
            return None

        return result

    def _record_final_failure(
        self, url: str, depth: int, referer: str | None, status_code: int | None, reason: str | None = None,
        queued_url: str | None = None,
    ) -> None:
        """A failure retrying can't fix (404, 401, too large): shown as a failed page, never re-fetched this sync.

        ``url`` is where the answer came from, after any redirect: the URL the page is stored under.
        ``queued_url`` is the URL that redirected there, whose own older record may need removing too.
        Several URLs can redirect to the same page in one sync, so they are collected, not replaced.
        """
        normalized = self._normalize_url(url)
        earlier = self.retry_urls.get(normalized)
        sources = list(earlier.queued_urls) if earlier else []
        if queued_url and urldefrag(queued_url).url != urldefrag(url).url and queued_url not in sources:
            sources.append(queued_url)
        self.retry_urls[normalized] = RetryUrl(
            url=normalized,
            status=Status.PENDING.value,
            status_code=status_code,
            retries=MAX_RETRIES,
            last_attempted=get_epoch_timestamp_in_ms(),
            depth=depth,
            referer=referer,
            reason=reason,
            site_url=url,
            queued_urls=sources,
        )

    def _too_large_reason(self) -> str:
        return (
            f"This file is larger than this connector's {self.max_size_mb} MB size limit, so it wasn't downloaded. "
            "Raise Largest file to download (Advanced settings) to include it, then sync again."
        )

    async def _excluded_by_extension_filter(self, result: FetchResponse) -> bool:
        """Checked after links are extracted: an "only PDFs" filter must still crawl the pages linking to them."""
        content_type = (self._header(result.headers, "Content-Type") or "").lower()
        if not content_type and result.status_code == HTTPStatus.NOT_MODIFIED:
            # A 304 has no body and often no Content-Type; without one, an extensionless
            # URL would read as html. The stored copy it vouches for says what the page is.
            stored = await self._stored_record(result.final_url)
            content_type = (stored.mime_type or "").lower() if stored else ""
        _, extension = self._determine_mime_type(result.final_url, content_type)
        return not self._pass_extension_filter(extension)

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
                        browser_like=self.browser_like_identity,
                        url=url,
                        session=self.session,
                        logger=self.logger,
                        referer=referer,
                        validators_for=functools.partial(self._conditional_headers, links_needed=False),
                        timeout=15,
                        max_size_mb=self.max_size_mb,
                        allow_hop=self._hop_allowed,
                    )
                    if self._should_try_crawl4ai_fallback(raw, url):
                        fetcher = await self._ensure_crawl4ai_fetcher()
                        if fetcher:
                            self.logger.info(
                                "🌐 [crawl4ai fallback] Non-headless strategies failed for %s (status=%s) — trying headless",
                                url,
                                raw.status_code if raw else "connection error",
                            )
                            crawl4ai_resp = await self._headless_fallback_fetch(url)
                            if crawl4ai_resp is not None and crawl4ai_resp.success and crawl4ai_resp.status_code < HttpStatusCode.BAD_REQUEST.value:
                                raw = crawl4ai_resp
                result = await self._validate_fetch_result(url, depth, referer, raw)
                if result is None:
                    return None
                # Robots first: a landing it refuses was never loaded, so its answer can't remove anything.
                if not await self._robots_allows_landing(url, result):
                    return None
                if await self._excluded_by_extension_filter(result):
                    await self._remove_filtered_out(result, url)
                    return None

            if result.status_code == HTTPStatus.NOT_MODIFIED:
                if self._normalize_url(result.final_url) == self._normalize_url(url):
                    return None  # the site confirmed our stored copy is current
                moved_to = result.final_url
                stored_there = await self._stored_record(moved_to)
                if stored_there is not None and stored_there.storage_document_id and self._validators_match(
                    result, *await self._stored_validators(stored_there)
                ):
                    # The 304 vouches for our copy at the new URL; only the old URL's record needs cleaning up.
                    await self._handle_gone_page(url, keep_id=stored_there.id)
                    return None
                # The 304 doesn't match our copy at the new URL, so fetch it in full; the redirect
                # cleanup deals with the old record. Validated as the URL that was asked for, so a
                # gone landing is filed with it as the source and the old record goes too.
                refetched = await fetch_url_with_fallback(
                    browser_like=self.browser_like_identity,
                    url=moved_to, session=self.session, logger=self.logger, referer=referer,
                    timeout=15, max_size_mb=self.max_size_mb, allow_hop=self._hop_allowed,
                )
                result = await self._validate_fetch_result(url, depth, referer, refetched)
                if result is None or result.status_code == HTTPStatus.NOT_MODIFIED:
                    return None
                if await self._excluded_by_extension_filter(result):
                    await self._remove_filtered_out(result, url)
                    return None

            # The site's own form, kept only to find a record stored under it before URLs were cleaned.
            site_url = result.final_url
            final_url = self._fetch_url(site_url)

            is_new = False
            is_updated = False
            is_deleted = False
            metadata_changed = False
            content_changed = False

            content_type = result.headers.get("Content-Type", "").lower()
            content_bytes = result.content_bytes

            mime_type, extension = self._determine_mime_type(final_url, content_type)
            html_bytes = content_bytes if mime_type == MimeTypes.HTML else None
            if html_bytes:
                final_url = self._same_page_canonical(html_bytes, final_url) or final_url

            # Normalize external_id to always end with '/' for extensionless (page) URLs
            # to prevent duplicate records for e.g. /docs and /docs/. Ignored parameters leave the
            # key but not the stored link, which must still open the page.
            external_id = self._ensure_trailing_slash(self._identity_url(final_url))

            record_id = None
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=external_id
            )

            # Migration compatibility: older records may be stored without the trailing slash,
            # or under the query string as the site gave it rather than in name order.
            legacy_lookup = False
            legacy_ids = [
                external_id.rstrip('/'),
                urlunparse(urlparse(site_url)._replace(fragment="")),
                self._ensure_trailing_slash(urldefrag(site_url).url),
            ]
            for legacy_external_id in dict.fromkeys(legacy_ids):
                if existing_record or legacy_external_id == external_id:
                    continue
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=legacy_external_id
                )
                legacy_lookup = existing_record is not None
            # A folder placeholder that turns out to be a page is a new page, so it counts toward Max Pages.
            write_as_new = legacy_lookup or bool(existing_record and existing_record.is_internal)

            record_id = existing_record.id if existing_record else str(uuid.uuid4())
            stored_etag, stored_last_modified = (
                await self._stored_validators(existing_record) if existing_record else (None, None)
            )

            # Get title and clean content for HTML
            title = self._extract_title_from_url(final_url)
            size_in_bytes = len(content_bytes)
            timestamp = get_epoch_timestamp_in_ms()

            # HTML: the text decides whether the page changed, before any of its images is downloaded.
            processed_content_bytes: Optional[bytes] = None
            raw_html = content_bytes
            if mime_type == MimeTypes.HTML:
                try:
                    title, content_bytes = await asyncio.to_thread(self._page_title_and_text, raw_html, final_url)
                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to parse HTML for {url}: {e}")

            # Calculate MD5 hash once (on text content for HTML, raw bytes otherwise)
            content_md5_hash = hashlib.md5(content_bytes).hexdigest()

            # Never for a record rewritten whole: it is uploaded again, and an unchanged page builds
            # no HTML, so its plain text would replace the stored page.
            unchanged = bool(
                existing_record and existing_record.storage_document_id and not write_as_new
                and existing_record.external_revision_id == content_md5_hash
            )
            if mime_type == MimeTypes.HTML and not unchanged:
                try:
                    headers_for_images = {"Referer": self.url} if self.url else {}
                    strategy = None if (result.strategy == "crawl4ai") else result.strategy
                    cleaned_html = await self._process_html_content(
                        raw_html, final_url, headers_for_images, strategy
                    )
                    if cleaned_html:
                        processed_content_bytes = cleaned_html.encode("utf-8")
                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to process HTML for {url}: {e}")

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
                # Also on the legacy path: the stored validators below are kept only for unchanged content.
                content_changed = existing_record.external_revision_id != content_md5_hash
                if write_as_new:
                    is_new = True # Rewritten whole: a legacy key migrates to the normalized form, a folder becomes a page
                else:
                    if existing_record.record_name != title:
                        metadata_changed = True
                    elif existing_record.parent_external_record_id != parent_url:
                        metadata_changed = True
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
                # Advance the version only when the page actually changed, so it stays
                # a signal of change rather than a count of crawls. A re-crawl that
                # finds nothing new is not persisted anyway.
                version=(
                    0
                    if not existing_record
                    else (existing_record.version or 0) + (1 if is_updated else 0)
                ),
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
                # A validator the site didn't send this time is kept only while the content is the same;
                # kept across a change, a later 304 would vouch for the old copy.
                etag=self._header(result.headers, "ETag") or (None if content_changed else stored_etag),
                # Last-Modified, kept verbatim to send back as If-Modified-Since.
                ctag=self._header(result.headers, "Last-Modified")
                or (None if content_changed else stored_last_modified),
            )

            # New or rotated validators on an unchanged page are saved as metadata: no re-index, no version bump.
            if existing_record and not write_as_new and not is_updated and (
                (file_record.etag, file_record.ctag) != (stored_etag, stored_last_modified)
            ):
                metadata_changed = is_updated = True

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

        except BrowserUnavailableError:
            raise
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
        record = record_update.record
        if record_update.metadata_changed:
            await self._store_with_retry(
                lambda: self.data_entities_processor.on_record_metadata_update(record), f"the update of {record.weburl}"
            )
        if record_update.content_changed:
            await self._store_with_retry(
                lambda: self.data_entities_processor.on_record_content_update(record), f"the update of {record.weburl}"
            )

    def _extract_links_from_html(
        self, base_url: str, html_bytes: bytes,
    ) -> List[str]:
        """Extract valid outbound links from raw HTML bytes."""
        links: List[str] = []
        soup = BeautifulSoup(html_bytes, "html.parser")
        for anchor in soup.find_all("a", href=True):
            if self.respect_robots_txt and "nofollow" in [r.lower() for r in anchor.get("rel") or []]:
                continue
            # "/guide#install" is a link to /guide; a bare "#top" resolves to this page.
            absolute_url = self._fetch_url(urljoin(base_url, anchor["href"]))
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
                        browser_like=self.browser_like_identity,
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

                absolute_url = urldefrag(urljoin(base_url, href)).url

                # Validate and filter URLs
                if self._is_valid_url(absolute_url, base_url):
                    links.append(absolute_url)

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to extract links from {base_url}: {e}")

        return links

    async def _create_failed_placeholder_record(
        self, url: str, status_code: int | None, reason: str | None = None, site_url: str | None = None,
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

        existing_record = None
        for candidate in dict.fromkeys([external_id, *self._stored_ids_for(site_url or url)]):
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=candidate
            )
            if existing_record:
                break

        if existing_record:
            return None, None

        record_id = str(uuid.uuid4())

        self.logger.warning(
            "⚠️ Creating FAILED placeholder for %s — "
            "failed due to error, status code: %s",
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
            reason=reason or failed_page_reason(status_code),
        )

        permissions = []

        return placeholder_record, permissions

    async def _retry_urls_generator(
        self,
        max_retries: int = 2,
        *,
        answered_only: bool = False,
    ) -> AsyncGenerator[RecordUpdate, None]:
        """Settle the URLs the crawl couldn't fetch: a FAILED placeholder record for each, within Max Pages.

        Fetches nothing: the crawl loop already made the retries. Iterates over a snapshot of
        ``self.retry_urls``. A 404/410 goes to the gone-page rules instead of becoming a record.
        """

        snapshot = list(self.retry_urls.values())
        if answered_only:
            snapshot = [r for r in snapshot if r.retries >= MAX_RETRIES or r.deferred]
        start_key = self._normalize_url(self.url or "")
        # A start page that is gone, directly or through a redirect, means the whole site is, or it is
        # misconfigured: delete nothing.
        site_gone = any(
            r.status_code in GONE_STATUS_CODES
            and start_key in (r.url, *(self._normalize_url(q) for q in r.queued_urls))
            for r in snapshot
        )

        for retry_url in snapshot:
            if retry_url.status_code in GONE_STATUS_CODES and not site_gone:
                await self._handle_gone_page(retry_url.site_url or retry_url.url)
                for source in retry_url.queued_urls:
                    # An older record may still sit under a URL that redirected here.
                    await self._handle_gone_page(source)
                # A link to a missing page is in the run report, not stored as a page.
                await self._remove_failed_placeholder(retry_url.site_url or retry_url.url)
                continue
            placeholder, perms = await self._create_failed_placeholder_record(
                retry_url.url, retry_url.status_code, retry_url.reason, retry_url.site_url
            )

            # Failed pages are stored too, so they share Max Pages with the pages kept.
            if placeholder is None or not self._admit_new_record(placeholder):
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

    async def _forget_redirected_url(self, requested_url: str, record_update: RecordUpdate) -> None:
        """A URL that now redirects to a page we store is an old name for it; its own record goes like a gone page."""
        record = record_update.record
        if record is None or not record.weburl:
            return
        if self._normalize_url(requested_url) != self._normalize_url(record.weburl):
            await self._handle_gone_page(requested_url, keep_id=record.id)

    def _validators_match(self, response: FetchResponse, stored_etag: str | None, stored_last_modified: str | None) -> bool:
        """Whether a 304 answered the validators stored for a record, and not another URL's.

        Both validators are sent together, so a Last-Modified that happens to match says nothing
        when the ETag doesn't: the ETag decides whenever the 304 carries one.
        """
        etag = self._header(response.headers, "ETag")
        if etag:
            return etag == stored_etag
        last_modified = self._header(response.headers, "Last-Modified")
        return bool(last_modified) and last_modified == stored_last_modified

    async def _stored_record(self, url: str) -> Record | None:
        for candidate in self._stored_ids_for(url):
            record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=candidate
            )
            if record:
                return record
        return None

    def _stored_ids_for(self, url: str) -> list[str]:
        """Every id a page at ``url`` may be stored under, the current form first.

        Records are keyed by the URL the site gave; older ones may lack the trailing slash
        or keep the query string in the site's order.
        """
        current = self._ensure_trailing_slash(url)
        as_given = urlunparse(urlparse(url)._replace(fragment=""))
        normalized = self._ensure_trailing_slash(self._normalize_url(url))
        return list(dict.fromkeys([current, current.rstrip("/"), as_given, normalized, normalized.rstrip("/")]))

    async def _handle_gone_page(self, url: str, keep_id: str | None = None) -> None:
        """Delete a stored page the second sync in a row it answers 404/410, or redirects to a page we store.

        Only those clear answers count: errors, blocks and timeouts never reach here.
        Failed-page records and folder placeholders are left alone.
        """
        record = None
        for candidate in self._stored_ids_for(url):
            record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=candidate
            )
            if record:
                break
        if record is None or record.id == keep_id or record.is_internal:
            return
        external_id = record.external_record_id
        if record.indexing_status == ProgressStatus.FAILED.value:
            return
        if external_id not in self._gone_last_sync:
            self._gone_this_sync.add(external_id)
            return
        self.logger.info("Removing %s: gone, or moved to another stored page, on two syncs in a row", url)
        await self._remove_record(record, url)

    async def _remove_failed_placeholder(self, url: str) -> None:
        record = await self._stored_record(url)
        if record is not None and not record.is_internal and record.indexing_status == ProgressStatus.FAILED.value:
            await self._remove_record(record, url)

    async def _remove_filtered_out(self, result: FetchResponse, requested_url: str) -> None:
        """Remove what we stored for a page whose answer the file-type filter now leaves out.

        Only a page the site actually answered gets here, so a narrowed filter removes
        what it excludes, and a fetch that failed removes nothing. The page is the one
        the answer came from. A requested URL that now redirects to it keeps its own
        record unless the filter leaves out that record's type too: a landing the
        filter drops is never kept, so the usual redirect cleanup does not run for it.
        Folder placeholders stay: they hold the tree
        together, not content.
        """
        record = await self._stored_record(result.final_url)
        if record is not None and not record.is_internal:
            self.logger.info("Removing %s: the file-type filter now leaves it out", result.final_url)
            await self._remove_record(record, result.final_url)
        if self._normalize_url(requested_url) == self._normalize_url(result.final_url):
            return
        old = await self._stored_record(requested_url)
        if old is None or old.is_internal or (record is not None and old.id == record.id):
            return
        _, old_extension = self._determine_mime_type(requested_url, (old.mime_type or "").lower())
        if not self._pass_extension_filter(old_extension):
            self.logger.info("Removing %s: the file-type filter now leaves it out", requested_url)
            await self._remove_record(old, requested_url)

    async def _remove_record(self, record: Record, url: str) -> None:
        # A folder placeholder is only removed once it holds nothing.
        children = [] if record.is_internal else await self._children_of(record.external_record_id)
        if children is None:
            # Removing it now would drop its children out of the tree; the gone mark lets the next sync try again.
            self._gone_this_sync.add(record.external_record_id)
            return
        in_trash = await self.data_entities_processor.on_record_deleted(record.id)
        # A page in the trash keeps its stored copy and its links; the purge removes both.
        if in_trash:
            return
        if record.storage_document_id and not await self._delete_storage_document(record.storage_document_id):
            self.logger.warning("Removed %s but could not delete its stored copy %s", url, record.storage_document_id)
        if children:
            await self._keep_children_in_the_tree(record.external_record_id, children)

    async def _children_of(self, external_id: str) -> list[Record] | None:
        """The records stored under ``external_id``; None when they could not be read."""
        try:
            return await self.data_entities_processor.get_records_by_parent(
                connector_id=self.connector_id, parent_external_record_id=external_id,
            )
        except Exception as e:
            self.logger.warning("Could not read the pages under %s: %s", external_id, e)
            return None

    async def _relink_children_left_out(self) -> None:
        """Link again the children an earlier removal could not: an unchanged child is never rewritten."""
        for external_id in sorted(self._relink_pending):
            children = await self._children_of(external_id)
            if children:
                await self._keep_children_in_the_tree(external_id, children)
            elif children is not None:
                self._relink_pending.discard(external_id)
        self._sync_point_base["relinkPending"] = sorted(self._relink_pending)

    async def _keep_children_in_the_tree(self, removed_external_id: str, children: list[Record]) -> None:
        """A removed page that had pages under it leaves a folder in its place, holding them.

        The delete took the parent links with it; the children are unchanged, so nothing would
        write them again, and they'd drop out of the tree. When the page comes back, the folder
        becomes the page again (``write_as_new``), children and all.
        """
        try:
            await self._ensure_parent_records_exist(removed_external_id)
            folder = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=removed_external_id,
            )
            if folder is None:
                raise RuntimeError("no folder was written")
            await self.data_entities_processor.on_records_attached_to_parent(folder.id, [c.id for c in children])
            self._relink_pending.discard(removed_external_id)
        except Exception as e:
            self.logger.error(
                "Removed %s, but the %d page(s) under it could not be linked to a folder in its place; "
                "the next sync tries again: %s",
                removed_external_id, len(children), e,
            )
            self._relink_pending.add(removed_external_id)
        self._sync_point_base["relinkPending"] = sorted(self._relink_pending)

    async def process_retry_urls(self, max_retries: int = 2, *, answered_only: bool = False) -> None:
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

        async for record_update in self._retry_urls_generator(max_retries=max_retries, answered_only=answered_only):
            if record_update.is_new and record_update.record is not None and record_update.new_permissions is not None:
                batch_records.append((record_update.record, record_update.new_permissions))

                if len(batch_records) >= self.batch_size:
                    await self._write_new_records(batch_records)
                    self.processed_urls += len(batch_records)
                    batch_records.clear()

        # Flush any remaining records
        if batch_records:
            await self._write_new_records(batch_records)
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

    def _same_page_canonical(self, html: bytes, page_url: str) -> str | None:
        """The page's ``rel=canonical`` URL when it names this same page (see ``is_same_page``)."""
        try:
            links = BeautifulSoup(html, "html.parser", parse_only=SoupStrainer("link"))
        except Exception as e:
            self.logger.debug("Could not read the canonical link of %s: %s", page_url, e)
            return None
        for link in links.find_all("link", href=True):
            if "canonical" in [r.lower() for r in link.get("rel") or []]:
                canonical = self._fetch_url(urljoin(page_url, link["href"]))
                return canonical if is_same_page(canonical, page_url) else None
        return None

    @staticmethod
    def _fetch_url(url: str) -> str:
        """The URL a page is fetched at: no fragment or tracking parameters, which a site never needs."""
        return clean_url(url)

    def _identity_url(self, url: str) -> str:
        """The URL that names a page: also without the parameters the connector is told to ignore."""
        return clean_url(url, self.ignore_query_params)

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        try:
            parsed = urlparse(self._identity_url(url))
            # Remove fragment and normalize
            return urlunparse((
                parsed.scheme,
                parsed.netloc.lower(),
                parsed.path.rstrip('/') or '/',
                parsed.params,
                self._sort_query(parsed.query),
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
            # Office and SVG checks come before 'xml': "application/vnd.openxmlformats-...", "image/svg+xml".
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
            elif 'json' in content_type_lower:
                return MimeTypes.JSON, 'json'
            elif 'image/svg' in content_type_lower:  # "image/svg+xml" would match 'xml' below
                return MimeTypes.SVG, 'svg'
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

    def _page_title_and_text(self, html: bytes, url: str) -> tuple[str, bytes]:
        """The page's title and its visible text; CPU-bound, so callers run it in a worker thread."""
        soup = BeautifulSoup(html, "html.parser")
        title = self._extract_title(soup, url)
        self._remove_unwanted_tags(soup)
        return title, soup.get_text(separator="\n", strip=True).encode("utf-8")

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

    @staticmethod
    def _sort_query(query: str) -> str:
        """?b=2&a=1 and ?a=1&b=2 are one page: parameters in name order, repeats and encoding kept as given."""
        if not query:
            return query
        return "&".join(sorted(query.split("&"), key=lambda part: part.split("=", 1)[0]))

    def _ensure_trailing_slash(self, url: str) -> str:
        """Append a trailing slash to extensionless (page) URLs to prevent duplicate records.
        File URLs containing a dot in the last path segment (e.g. /doc.pdf) are returned unchanged.
        """
        try:
            parsed = urlparse(url)
            # Don't touch the path of URLs with query params — the param might be the identifier
            if parsed.query:
                return urlunparse(parsed._replace(query=self._sort_query(parsed.query), fragment=""))
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
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> BaseConnector:
        """Factory method to create a WebConnector instance."""
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

                # Not self.session: the storage service is internal, which the crawl's session refuses.
                session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
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
        # NOTE: HTML void elements (link, meta, base) are intentionally
        # excluded.  html.parser sometimes mis-parses void elements as
        # non-void containers, nesting subsequent DOM content inside them.
        # Decomposing such a mis-parsed void tag destroys the entire page.
        # Since void elements carry no visible text, removing them has no
        # benefit anyway — get_text() already ignores them.
        technical_tags = [
            "script", "style", "noscript", "iframe", "canvas"
        ]

        # 2. Functional/Interactive Noise
        # UI elements that don't represent the text content of the page. A <form> is unwrapped,
        # not removed: ASP.NET and similar sites wrap the whole page body in one.
        ui_tags = ["button", "input", "select", "textarea", "label"]

        # 3. Structural/Navigational Noise
        structural_tags = ["nav"]

        # Combine and decompose
        for tag in soup(technical_tags + ui_tags + structural_tags):
            tag.decompose()
        for form in soup("form"):
            form.unwrap()
        # The site's own header, footer and sidebars; an article's header or footer stays with it.
        for tag in soup(["header", "footer", "aside"]):
            if not tag.decomposed and tag.find_parent(["article", "main"]) is None:
                tag.decompose()

        # 4. Common CSS Selectors (Class/ID Noise)
        # These catch elements on sites that don't use semantic tags.
        # Covers sidebars, menus, ads, and common 'skip' links.
        unwanted_selectors = [
            ".sidebar", "#sidebar", ".menu", ".nav", ".navigation",
            ".ads", ".promo", ".banner", ".popup", ".modal",
            ".toc", ".table-of-contents", ".breadcrumb",
            ".pagination", ".share-buttons", ".social-media",
            "a[href='#content']", "a.skip-to-content", ".edit-page-link",
            "[role='navigation']", "[role='banner']", "[role='contentinfo']",
            "#onetrust-consent-sdk", "#onetrust-banner-sdk", "#cookie-banner", ".cookie-banner",
            ".cookie-consent", "#CybotCookiebotDialog", ".cc-window",
        ]

        for selector in unwanted_selectors:
            for match in soup.select(selector):
                match.decompose()

        self._keep_main_content(soup)

    @staticmethod
    def _keep_main_content(soup: BeautifulSoup) -> None:
        """When the page marks its main content and it has real text, keep only that in the body."""
        main = soup.find("main") or soup.find(attrs={"role": "main"})
        body = soup.body
        if main is None or body is None or len(main.get_text(" ", strip=True)) < MIN_MAIN_CONTENT_CHARS:
            return
        main = main.extract()
        body.clear()
        body.append(main)

    @staticmethod
    def _resolve_lazy_images(soup: BeautifulSoup) -> None:
        """Lazy-loaded images keep the real address in a data attribute until scrolled into view."""
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src and not src.startswith("data:"):
                continue
            lazy = next((img.get(attr) for attr in LAZY_IMAGE_ATTRS if img.get(attr)), None)
            if lazy:
                img["src"] = lazy

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
                browser_like=self.browser_like_identity,
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
        """Embed the page's first MAX_IMAGES_PER_PAGE images of at least MIN_IMAGE_SIDE_PX a side; drop the rest.

        Every embedded image is described by the vision model at indexing time, so icons, spacers and
        a long tail of screenshots would cost far more than they add. Images are downloaded in page
        order, a batch at a time, only until the page has its share.
        """
        candidates = []
        for img in soup.find_all('img'):
            if self._declared_too_small(img):
                img.decompose()
            else:
                candidates.append(img)
        sem = asyncio.Semaphore(self._IMAGE_DOWNLOAD_CONCURRENCY)

        async def _bounded(img_tag):
            async with sem:
                await self._process_single_image(img_tag, soup, base_url, headers, preferred_strategy)

        kept = 0
        while candidates and kept < MAX_IMAGES_PER_PAGE:
            batch, candidates = candidates[:MAX_IMAGES_PER_PAGE - kept], candidates[MAX_IMAGES_PER_PAGE - kept:]
            await asyncio.gather(*(_bounded(img) for img in batch))
            for img in batch:
                if img.parent is None:
                    continue  # removed while being processed
                if self._embedded_too_small(img.get('src') or ''):
                    img.decompose()
                elif (img.get('src') or '').startswith('data:'):
                    kept += 1
        for img in candidates:
            img.decompose()

    @staticmethod
    def _declared_too_small(img: Tag) -> bool:
        """An image whose own width or height attribute says it is an icon; known before downloading it."""
        for attr in ('width', 'height'):
            value = str(img.get(attr) or '').strip().removesuffix('px')
            if value.isdigit() and int(value) < MIN_IMAGE_SIDE_PX:
                return True
        return False

    @staticmethod
    def _embedded_too_small(src: str) -> bool:
        """A data-URI image under MIN_IMAGE_SIDE_PX on a side; one whose size can't be read is kept."""
        if not src.startswith('data:') or ',' not in src:
            return False
        try:
            with Image.open(BytesIO(base64.b64decode(src.split(',', 1)[1]))) as image:
                width, height = image.size
        except Exception:
            return False
        return min(width, height) < MIN_IMAGE_SIDE_PX

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
                self._resolve_lazy_images(soup)
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

    @staticmethod
    def _storage_document_id_from_upload(headers, data) -> Optional[str]:
        """Resolve a storage document id from upload headers or JSON body."""
        doc_hdr = headers.get("x-document-id") or headers.get("X-Document-Id")
        if isinstance(doc_hdr, str) and doc_hdr.strip():
            return doc_hdr.strip()
        if not isinstance(data, dict):
            return None
        doc_id = data.get("_id") or data.get("id")
        if doc_id:
            return str(doc_id)
        nested = data.get("document")
        if isinstance(nested, dict):
            nested_id = nested.get("_id") or nested.get("id")
            if nested_id:
                return str(nested_id)
        return None

    async def _put_presigned_upload(
        self,
        session: aiohttp.ClientSession,
        resp: aiohttp.ClientResponse,
        content: bytes,
    ) -> Optional[str]:
        """PUT raw bytes to a storage-service redirect Location (presigned URL)."""
        location = resp.headers.get("Location") or resp.headers.get("location")
        if not location:
            self.logger.error("Storage direct-upload redirect missing Location header")
            return None
        try:
            data = await resp.json()
        except Exception:
            data = None
        doc_id = self._storage_document_id_from_upload(resp.headers, data)
        put_headers = {"Content-Length": str(len(content))}
        async with session.put(
            location,
            data=content,
            headers=put_headers,
            allow_redirects=False,
        ) as put_resp:
            if put_resp.status < 200 or put_resp.status >= 300:
                error = await put_resp.text()
                self.logger.error(
                    "Failed to upload to storage (status %d): %s",
                    put_resp.status, error,
                )
                return None
        return doc_id

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

        For S3/Azure, Node may 308 to a PUT-signed URL instead of proxying
        the bytes. That redirect must be followed as PUT of the raw file,
        not as another multipart POST.
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
                    allow_redirects=False,
                ) as resp:
                    if resp.status in STORAGE_UPLOAD_REDIRECT_STATUS_CODES:
                        return await self._put_presigned_upload(
                            session, resp, content
                        )
                    if resp.status == 200:
                        data = await resp.json()
                        return self._storage_document_id_from_upload(
                            resp.headers, data
                        )
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
                if record.storage_document_id:
                    # The read above failed and there is no live URL to fall
                    # back on — a storage outage, not a deleted page.
                    raise internal_service_status(HttpStatusCode.BAD_GATEWAY.value)
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"No stored content and no web URL for record {record.record_name} (id:{record.id})",
                )

            referer = self.url if self.url else None

            if self.session is None:
                raise connector_not_ready(self.display_name)

            if self.use_headless_browser and await self._ensure_crawl4ai_fetcher():
                try:
                    result = await self._headless_fetch(record.weburl, walk_first=False)
                except BrowserUnavailableError as e:
                    # Our browser failed, not the site: the page may be fine, so no site status is reported.
                    self.logger.warning("Headless browser unavailable for record %s: %s", record.id, e)
                    raise internal_service_status(HttpStatusCode.SERVICE_UNAVAILABLE.value) from e
            else:
                result = await fetch_url_with_fallback(
                    browser_like=self.browser_like_identity,
                    url=record.weburl,
                    session=self.session,
                    logger=self.logger,
                    referer=referer,
                )

            # The crawled site's status is never returned as our own: a 401 from
            # any target site would trip the frontend's axios interceptor and log
            # the user out of PipesHub, and sites emit non-standard codes (999)
            # that Starlette would hand to the client verbatim.
            if (
                result is None
                or not result.success
                or result.status_code >= HttpStatusCode.BAD_REQUEST.value
            ):
                self.logger.warning(
                    "Web fetch failed for record %s: status=%s error=%s",
                    record.id,
                    result.status_code if result else "no response",
                    result.error_message if result else None,
                )
                raise map_source_status(
                    result.status_code if result and result.status_code else HttpStatusCode.BAD_GATEWAY.value,
                    connector=self.display_name,
                    retry_after=(
                        str(int(result.retry_after))
                        if result and result.retry_after
                        else None
                    ),
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