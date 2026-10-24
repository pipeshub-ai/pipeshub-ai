"""
Confluence Cloud Connector

This connector syncs Confluence Cloud data including:
- Spaces with permissions
- Pages with content and metadata
- Users and their access

Authentication: OAuth 2.0 (3-legged OAuth)
"""

import asyncio
import base64
import hashlib
import json
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import (
    Any,
    NamedTuple,
    Optional,
)
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AccessRule,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorInitError,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_downloadable,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.constants import (
    CONNECTOR_EMAIL_IDENTITY_INFO,
    IconPaths,
    OAuthConfigKeys,
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.auth_utils import include_jira_scope_enabled
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.atlassian.confluence_cloud.block_parser import (
    ConfluenceBlockParser,
)
from app.connectors.sources.atlassian.core.apps import ConfluenceApp
from app.connectors.sources.atlassian.core.auth_fields import (
    INCLUDE_JIRA_SCOPE,
    confluence_include_jira_scope_field,
)
from app.connectors.sources.atlassian.core.confluence_access import (
    _is_folder,
    apply_page_access_to_dependents,
    listing_modified_after,
    unresolved_principal_permission,
    v1_next_start,
)
from app.connectors.sources.atlassian.core.confluence_html import (
    HtmlImageContext,
    extract_attachment_filename_from_download_url,
    is_cloud_attachment_download_url,
    prepare_streaming_html,
)
from app.connectors.sources.atlassian.core.oauth import AtlassianScope
from app.connectors.sources.atlassian.core.quota import (
    API_TOKEN_REQUESTS_PER_SECOND,
    OAUTH_REQUESTS_PER_SECOND,
    AtlassianQuotaExhausted,
    QuotaAwarePolicy,
    quota_reset_text,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.blocks import (
    Block,
    BlockComment,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.notification.types import (
    NotificationOutcome,
    NotificationRecipientRole,
    NotificationSeverity,
    NotificationType,
)
from app.sources.client.confluence.confluence import (
    ConfluenceClient as ExternalConfluenceClient,
)
from app.sources.client.http.http_retry import is_retryable_status
from app.sources.external.common.atlassian import AtlassianMultiSiteError
from app.sources.external.confluence.confluence import ConfluenceDataSource
from app.utils.streaming import create_stream_record_response

# Confluence Cloud OAuth URLs
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"

# How far back each incremental sync re-reads before its checkpoint. CQL reads dates
# in the site's time zone, not UTC; the admin's date filters are used as set.
TIME_OFFSET_HOURS = 24

# Expand parameters for fetching pages and blogposts with required metadata
# Includes: ancestors, history, space, attachments, and comments
CONTENT_EXPAND_PARAMS = (
    "ancestors,"
    "history.lastUpdated,"
    "space,"
    "children.attachment,"
    "children.attachment.history.lastUpdated,"
    "children.attachment.version,"
    "childTypes.comment"
)

# Expand parameters for fetching folders with required metadata
# Folders don't need attachment or comment children
FOLDER_EXPAND_PARAMS = (
    "ancestors,"
    "history.lastUpdated,"
    "space"
)

# Graph page size when looking for records the source no longer has
RECORD_SCAN_PAGE_SIZE = 500

# Largest page the v2 space content listings return
V2_CONTENT_LIST_LIMIT = 250

# Ids per CQL search when checking which stored items a sync filter leaves out
ID_SEARCH_CHUNK = 50


class GoneAtSource(Exception):
    """Confluence answered 404 for a stored record: it was deleted or the account lost access."""


class CommentsUnreadable(Exception):
    """A page's comments could not be read in full, so its blocks must not be indexed without them."""


# Each item's own read restriction, for finding changed restrictions without the audit log
RESTRICTION_EXPAND_PARAMS = "restrictions.read.restrictions.user,restrictions.read.restrictions.group"
RESTRICTION_LISTING_LIMIT = 100


def read_restriction_fingerprint(item: dict[str, Any]) -> str | None:
    """Who a listed item's own read restriction names; None when it has none."""
    read = ((item.get("restrictions") or {}).get("read") or {}).get("restrictions") or {}
    users = read.get("user") or {}
    groups = read.get("group") or {}
    names = sorted(str(u.get("accountId")) for u in users.get("results") or [] if isinstance(u, dict))
    names += sorted(
        f"group:{g.get('id') or g.get('name')}" for g in groups.get("results") or [] if isinstance(g, dict)
    )
    totals = [
        listing.get("totalSize") if isinstance(listing.get("totalSize"), int) else listing.get("size")
        for listing in (users, groups)
    ]
    if not names and not any(totals):
        return None
    listed = len(users.get("results") or []) + len(groups.get("results") or [])
    if listed < sum(t for t in totals if isinstance(t, int)):
        # Names past the listing's first page can't be compared: always check such an item again.
        return f"unlisted@{time.time()}"
    return f"{totals[0]}/{totals[1]}:" + ",".join(names)


class ContentListing(NamedTuple):
    """What one space's page or blog post listing read.

    ``full`` means it ran without a checkpoint, so ``seen`` holds every item the
    sync filters admit; otherwise ``seen`` holds only what changed since then.
    ``resumed``: a full listing that picked up where an earlier one stopped, so
    ``seen`` lacks what that earlier part read.
    ``checkpoint_time`` is the checkpoint the listing earned, or None when it
    must not move.
    """

    full: bool
    complete: bool
    seen: frozenset[str]
    checkpoint_key: str
    checkpoint_time: str | None
    resumed: bool = False


# Constant for pseudo-user group prefix
PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"

# Largest image inlined into a page's content (10 MiB, as fetch_authenticated_binary allows).
MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024
# Images inlined into one page in all; past it the rest stay attachment records.
MAX_INLINE_IMAGE_BYTES_PER_PAGE = 40 * 1024 * 1024


def images_not_inlined(attachments: list[dict[str, Any]]) -> set[str]:
    """Image attachments left out of the page body, so they stay attachment records instead.

    Decided from the attachment list alone, so sync and streaming leave out the same ones:
    an image over MAX_INLINE_IMAGE_BYTES, and any image that would take the page's images
    past MAX_INLINE_IMAGE_BYTES_PER_PAGE (in list order).
    """
    left_out: set[str] = set()
    total = 0
    for att in attachments:
        att_id = att.get("id")
        extensions = att.get("extensions") or {}
        if not att_id or not str(att.get("mediaType") or extensions.get("mediaType") or "").startswith("image/"):
            continue
        size = att.get("fileSize", extensions.get("fileSize"))
        # shortcut: an image of unknown size counts as 0; the per-download size guard still applies.
        size = size if isinstance(size, int) else 0
        if size > MAX_INLINE_IMAGE_BYTES or total + size > MAX_INLINE_IMAGE_BYTES_PER_PAGE:
            left_out.add(str(att_id))
        else:
            total += size
    return left_out

# Most users /wiki/rest/api/search/user?cql=type=user returns (Atlassian's documented cap).
USER_SEARCH_CAP = 10_000

# Confluence v2 space permission access-class principals (ROLES-mode / default ACLs), as
# normalised by _access_class_id: Cloud spells them "access-class"/"ALL_LICENSED_USERS" or
# "ACCESS_CLASS"/"all-licensed-users".
ACCESS_CLASS_LICENSED_USERS = "ALL_LICENSED_USERS"
ACCESS_CLASS_PRODUCT_ADMINS = "ALL_PRODUCT_ADMINS"
ACCESS_CLASS_AUTHENTICATED_USERS = "AUTHENTICATED_USERS"
ACCESS_CLASS_ANONYMOUS_USERS = "ANONYMOUS_USERS"

# Synced groups an access class stands for: exact names, then name prefixes. Cloud sites
# name them per site, e.g. "confluence-users-acme" and "confluence-admins-acme".
LICENSED_USER_GROUP_NAMES = ("confluence-users",)
LICENSED_USER_GROUP_PREFIXES = ("confluence-users-",)
PRODUCT_ADMIN_GROUP_NAMES = ("org-admins", "confluence-administrators", "site-admins", "system-administrators")
PRODUCT_ADMIN_GROUP_PREFIXES = ("confluence-admins-",)

def extract_media_from_adf(adf_content: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract all media nodes from ADF content.

    Returns list of media info dicts with:
        - id: Media ID/token
        - alt: Alt text (usually filename)
        - type: Media type (file, image, etc.)
        - width: Image width (if available)
        - height: Image height (if available)
        - collection: Media collection (if available)
    """
    if not adf_content or not isinstance(adf_content, dict):
        return []

    media_nodes: list[dict[str, Any]] = []

    def traverse(node: dict[str, Any]) -> None:
        """Recursively traverse ADF nodes to find media."""
        if not isinstance(node, dict):
            return

        node_type = node.get("type", "")

        # Check if this is a media node
        if node_type == "media":
            attrs = node.get("attrs", {})
            # Get filename from multiple sources:
            # - __fileName: Used for PDFs and other files
            # - alt: Used for images (usually contains filename)
            alt_text = attrs.get("alt", "")
            internal_filename = attrs.get("__fileName", "")
            # Best filename: prefer __fileName (more reliable for files), fallback to alt
            filename = internal_filename or alt_text

            media_info = {
                "id": attrs.get("id", ""),
                "alt": alt_text,
                "filename": filename,  # Best filename for matching
                "type": attrs.get("type", "file"),
                "width": attrs.get("width"),
                "height": attrs.get("height"),
                "collection": attrs.get("collection", ""),
            }
            if media_info["id"]:  # Only add if we have an ID
                media_nodes.append(media_info)

        # Recurse into content
        if "content" in node:
            for child in node.get("content", []):
                traverse(child)

    # Start traversal from root
    if "content" in adf_content:
        for node in adf_content.get("content", []):
            traverse(node)
    else:
        traverse(adf_content)

    return media_nodes


@ConnectorBuilder("Confluence")\
    .in_group("Atlassian")\
    .with_description("Sync pages, folders, spaces, and users from Confluence Cloud")\
    .with_categories(["Knowledge Management", "Collaboration"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_resilience_config(rate_limit=API_TOKEN_REQUESTS_PER_SECOND, max_retries=3)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Confluence",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            redirect_uri="connectors/oauth/callback/Confluence",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=AtlassianScope.get_confluence_read_access(),
                agent=AtlassianScope.get_confluence_read_access()
            ),
            fields=[
                CommonFields.client_id("Atlassian OAuth App"),
                CommonFields.client_secret("Atlassian OAuth App"),
                confluence_include_jira_scope_field(),
            ],
            icon_path=IconPaths.connector_icon(Connectors.CONFLUENCE.value),
            app_group="Atlassian",
            app_description="OAuth application for accessing Confluence Cloud API and collaboration features",
            app_categories=["Knowledge Management", "Collaboration"]
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://yourcompany.atlassian.net",
                description="The base URL of your Atlassian instance",
                field_type="URL",
                required=True,
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="email",
                display_name="Email",
                placeholder="your-email@company.com",
                description="Your Atlassian account email",
                field_type="TEXT",
                required=True,
                max_length=500,
                is_secret=False,
            ),
            AuthField(
                name="apiToken",
                display_name="API Token",
                placeholder="your-api-token",
                description="API token from Atlassian account settings",
                field_type="PASSWORD",
                required=True,
                max_length=2000,
                is_secret=True,
            ),
        ])
    ])\
    .with_info(
        "Important: In order for users to get access to Confluence data, each user needs to make their email visible in their Confluence account settings. Users can do this by going to their Confluence profile settings and switching email visibility to Public."
        + "\n\n"
        + CONNECTOR_EMAIL_IDENTITY_INFO
    )\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.CONFLUENCE.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Confluence Cloud OAuth Setup",
            "https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/confluence/confluence',
            'pipeshub'
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(
            name="space_keys",
            display_name="Space Name",
            description="Filter pages and blogposts by space name",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(FilterField(
            name="page_ids",
            display_name="Page Name",
            description="Filter specific pages by their name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(FilterField(
            name="blogpost_ids",
            display_name="Blogpost Name",
            description="Filter specific blogposts by their name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter pages and blogposts by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter pages and blogposts by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        # Indexing filters - Pages
        .add_filter_field(FilterField(
            name="pages",
            display_name="Index Pages",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of pages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="page_attachments",
            display_name="Index Page Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of page attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="page_comments",
            display_name="Index Page Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of page comments",
            default_value=True
        ))
        # Indexing filters - Blogposts
        .add_filter_field(FilterField(
            name="blogposts",
            display_name="Index Blogposts",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogposts",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="blogpost_attachments",
            display_name="Index Blogpost Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogpost attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="blogpost_comments",
            display_name="Index Blogpost Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogpost comments",
            default_value=True
        ))
    )\
    .build_decorator()
class ConfluenceConnector(BaseConnector):
    """
    Confluence Cloud Connector

    This connector syncs Confluence Cloud data including:
    - Spaces with permissions
    - Folders (organizational structure)
    - Pages with content and metadata
    - Blogposts with content and metadata
    - Users and their access

    Authentication: OAuth 2.0 (3LO - 3-legged OAuth)
    """

    user_link_steps = ("_sync_users", "_sync_user_groups", "_reconcile_inactive_app_users")

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> None:
        """Initialize the Confluence connector."""
        super().__init__(
            ConfluenceApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        # Client instances
        self.external_client: ExternalConfluenceClient | None = None
        self.data_source: ConfluenceDataSource | None = None
        self.connector_id: str = connector_id
        # Email from GET /wiki/rest/api/user/current (cached in init)
        self._authenticated_confluence_email: str | None = None
        self._authenticated_confluence_account_id: str | None = None

        # Initialize sync points for incremental sync
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.pages_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.audit_log_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Lazy cache: lowercased Confluence group name -> external group id (UUID)
        self._group_name_to_external_id: dict[str, str] | None = None
        # From the members' accountStatus in the last group sync that read every member list.
        self._inactive_account_ids: set[str] = set()
        self._active_member_emails: set[str] = set()
        # This sync's group read, kept only once it has read every member list.
        self._pending_inactive_ids: set[str] = set()
        self._pending_active_emails: set[str] = set()
        self._group_read_complete = False
        # Until one group read completes (as after a restart) no status is known, so every user waits for it.
        self._account_statuses_known = False
        # Users _sync_users held back; saved if this sync's group read finds them active.
        self._held_back_users: list[AppUser] = []
        self._space_listing_complete = False
        # The site's wiki URL (https://<site>/wiki) from a listing's _links.base, for web links.
        self._site_wiki_url: str | None = None
        # What this run could not do, sent once as one notification when it ends.
        self._sync_problems: list[str] = []
        # (space key, what was skipped) -> count, shown as one line per pair.
        self._skipped_items: dict[tuple[str, str], int] = defaultdict(int)
        # Part of the source went unread, so a full sync must not unlink what it did not see.
        self._sync_read_incomplete = False
        # The client's retry policy, one per request rate (see _client_resilience).
        self._client_policies: dict[float, QuotaAwarePolicy] = {}
        self._client_policy: QuotaAwarePolicy | None = None
        self._run_started_at = 0.0
        self._persistent_problems: set[str] = set()
        self._quota_hit_in_run = False
        # Syncs, re-indexes and streams running on this instance; the HTTP client closes after the last.
        self._in_use = 0
        self._sync_finished = False
        self._close_after_sync = False
        # Pages just stored as blocks, whose comment records from older versions are dropped once saved.
        self._now_blocks: dict[str, RecordType] = {}
        # The status the audit log was refused with this run (401/403), if it was.
        self._audit_log_refused: int | None = None

    async def _build_confluence_client(self):
        """EE override point: swap to EE ConfluenceClient for org-scoped OAuth inheritance."""
        return await ExternalConfluenceClient.build_from_services(
            logger=self.logger,
            config_service=self.config_service,
            connector_instance_id=self.connector_id,
            resilience=await self._client_resilience(),
        )

    async def _client_resilience(self) -> QuotaAwarePolicy:
        """The rate limit and retry policy for this connector's client, by how it authenticates.

        OAuth calls are paced lower than API-token calls to stay inside Atlassian's hourly
        points quota. Kept across re-inits so a re-init does not reset a throttle or a spent quota.
        """
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")
        auth = config.get("auth") if isinstance(config, dict) else None
        # Unknown auth gets the lower OAuth pace.
        auth_type = str((auth.get("authType") if isinstance(auth, dict) else None) or "OAUTH").upper()
        rate = OAUTH_REQUESTS_PER_SECOND if auth_type == "OAUTH" else API_TOKEN_REQUESTS_PER_SECOND
        policy = self._client_policies.get(rate)
        if policy is None:
            declared = self.resilience
            policy = QuotaAwarePolicy(
                rate_limit=rate,
                max_retries=declared.max_retries if declared else 3,
                base_delay=declared.base_delay if declared else 1.0,
                max_delay=declared.max_delay if declared else 60.0,
                name=str(self.connector_name),
                logger=self.logger,
            )
            self._client_policies[rate] = policy
        self._client_policy = policy
        return policy

    def _quota_spent_this_run(self) -> float | None:
        """When Atlassian's quota resets, if running out of it stopped part of this run; else None."""
        policy = self._client_policy
        reset_at = policy.quota_reset_at if policy else None
        return reset_at if reset_at and self._quota_hit_in_run else None

    async def init(self) -> bool:
        """Initialize the Confluence connector with credentials and client."""
        try:
            self.logger.info("🔧 Initializing Confluence Cloud Connector...")

            # Build client from services (handles config loading, token, base URL internally)
            self.external_client = await self._build_confluence_client()

            # Initialize data source
            self.data_source = ConfluenceDataSource(self.external_client)
            await self._cache_authenticated_confluence_email()
            await self._remember_configured_site()

            self.logger.info("✅ Confluence connector initialized successfully")
            return True

        except AtlassianMultiSiteError as e:
            # Propagate the actionable reason so the API surfaces it instead of the
            # generic "Failed to initialize connector" message.
            await self._notify_multi_site_ambiguity(e)
            raise ConnectorInitError(str(e)) from e
        except ValueError as e:
            # The client builder's own message names the missing or bad setting.
            self.logger.error(f"❌ Failed to initialize Confluence connector: {e}")
            raise ConnectorInitError(f"Confluence settings are incomplete: {e}") from e
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Confluence connector: {e}", exc_info=True)
            # The message reaches any user who opens a record while the connector starts, so no raw
            # error text (hosts, response bodies): that stays in the log.
            raise ConnectorInitError(
                "Couldn't connect to Confluence. Check the site URL and the credentials; "
                "the connector's log has the details."
            ) from e

    # Process-wide: each failed start builds a new connector instance.
    _failed_start_noticed_at: dict[str, float] = {}
    _FAILED_START_NOTICE_WINDOW_S = 600

    async def on_background_init_failed(self, error: Optional[Exception]) -> None:
        """A sync could not start: tell the creator and the org admins, on every failed start."""
        if error is not None and isinstance(error.__cause__, AtlassianMultiSiteError):
            return  # init already sent its own notice for this
        # One start can be retried several times (a redelivered event builds a new instance each
        # time), so one notice per connector per window; the next scheduled run notifies again.
        now = time.time()
        last = ConfluenceConnector._failed_start_noticed_at.get(self.connector_id, 0.0)
        if now - last < self._FAILED_START_NOTICE_WINDOW_S:
            return
        ConfluenceConnector._failed_start_noticed_at[self.connector_id] = now
        reason = str(error)[:300] if error else "the connector could not be set up"
        try:
            await self.notify_and_wait(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR,
                title=f"{self.display_name}: could not start",
                message=(
                    f"PipesHub could not start the Confluence connector: {reason}. Syncs and opening its "
                    "records fail until this is fixed; check the connector's settings and credentials."
                ),
                recipient_user_ids=[self.created_by] if self.created_by else None,
                recipient_roles=[NotificationRecipientRole.ADMIN],
                suppress_repeats=False,
            )
        except Exception as e:
            self.logger.error(f"Could not send the failed-start notification: {e}")

    async def _cache_authenticated_confluence_email(self) -> None:
        """Cache the email of the account the connector is authenticated as; never fails init."""
        try:
            response = await self.data_source.get_current_user_v1()
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                return
            data = response.json() or {}
            self._authenticated_confluence_email = (data.get("email") or "").strip() or None
            self._authenticated_confluence_account_id = data.get("accountId")
        except Exception as e:
            self.logger.debug("Could not fetch authenticated Confluence user during init: %s", e)

    async def _notify_multi_site_ambiguity(self, error: AtlassianMultiSiteError) -> None:
        """Notify admin: account-level app reaches multiple sites — needs a new
        Resource-restricted OAuth app (grants cannot be changed on an existing app)."""
        self.logger.error(
            "❌ Confluence connector %s: OAuth app can access multiple Atlassian sites: %s",
            self.connector_id, error,
        )
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title="Confluence connector: Resource-restricted OAuth required",
            message=(
                "This OAuth app has access to multiple Confluence sites. "
                "Create a single-site (resource-restricted) OAuth app in the "
                "Atlassian Developer Console, then reconnect."
            ),
        )

    async def _get_fresh_datasource(self) -> ConfluenceDataSource:
        """
        Get ConfluenceDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current OAuth token from config
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        For API_TOKEN auth, returns existing datasource (no token refresh needed).

        Returns:
            ConfluenceDataSource with current valid token
        """
        if not self.external_client:
            raise connector_not_ready(self.display_name)

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

        if not config:
            raise connector_not_ready(self.display_name)

        # Check auth type
        auth_config = config.get("auth", {}) or {}
        auth_type = auth_config.get("authType", "OAUTH")

        # For API_TOKEN auth, no token refresh needed - return existing datasource
        if auth_type == "API_TOKEN":
            return ConfluenceDataSource(self.external_client)

        # For OAuth, extract fresh access token and update if changed
        credentials_config = config.get("credentials", {}) or {}
        fresh_token = credentials_config.get("access_token", "")

        if not fresh_token:
            raise connector_not_ready(self.display_name)

        # Get current token from client
        internal_client = self.external_client.get_client()
        current_token = internal_client.get_token()

        # Update client's token if it changed (mutation)
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.set_token(fresh_token)

        # Return datasource with updated client
        return ConfluenceDataSource(self.external_client)

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Confluence API.

        Raises ConnectorInitError with a message the admin can act on (bad credentials,
        missing OAuth scope, wrong site URL, no Confluence access); the API shows it as is.
        """
        if not self.external_client:
            self.logger.error("External client not initialized")
            return False
        try:
            # Test by fetching spaces with a limit of 1
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_spaces(
                limit=1
            )
        except (ConnectorInitError, HTTPException):
            # An HTTPException here already says what to do (e.g. the connector is not connected).
            raise
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            raise ConnectorInitError(
                f"Couldn't reach Confluence at {self._site_url_for_messages()}. "
                "Check the site URL and that it can be reached from PipesHub."
            ) from e

        status = response.status if response else None
        if status == HttpStatusCode.SUCCESS.value:
            self.logger.info("✅ Confluence connector connection test passed")
            return True
        self.logger.error(f"Connection test failed with status: {status if status else 'No response'}")
        raise ConnectorInitError(await self._connection_error_message(status, response))

    def _site_url_for_messages(self) -> str:
        """The site this connector talks to, for error messages."""
        try:
            return self.external_client.get_client().get_base_url().split("/wiki/")[0]
        except Exception:
            return "the configured site"

    async def _connection_error_message(self, status: int | None, response: object) -> str:
        """What a failed connection test means, by HTTP status."""
        site = self._site_url_for_messages()
        try:
            body = (response.text() or "") if response is not None else ""
        except Exception:
            body = ""
        if status == HttpStatusCode.UNAUTHORIZED.value and "scope" in body.lower():
            missing = await self._missing_oauth_scopes()
            named = f" Missing: {', '.join(missing)}." if missing else ""
            return (
                "The OAuth app was not granted every Confluence scope the connector needs."
                f"{named} Add them to the app in the Atlassian Developer Console, then reconnect."
            )
        if status == HttpStatusCode.UNAUTHORIZED.value:
            return (
                f"Confluence at {site} rejected the credentials (HTTP 401). Check the email and API "
                "token, or reconnect the OAuth app."
            )
        if status == HttpStatusCode.FORBIDDEN.value:
            return (
                f"The account signed in to {site} but can't read Confluence (HTTP 403). Give it "
                "Confluence access and permission to view the spaces to sync."
            )
        if status == HttpStatusCode.NOT_FOUND.value:
            return (
                f"No Confluence site was found at {site} (HTTP 404). The site URL should look like "
                "https://yourcompany.atlassian.net."
            )
        return (
            f"Confluence at {site} did not answer the connection test (HTTP {status or 'no response'}). "
            "Try again in a few minutes."
        )

    async def _missing_oauth_scopes(self) -> list[str]:
        """The connector's OAuth scopes that the token was not granted; empty when unknown."""
        try:
            token = self.external_client.get_client().get_token()
            resources = await ExternalConfluenceClient.get_accessible_resources(token)
        except Exception as e:
            self.logger.debug(f"Could not read the granted OAuth scopes: {e}")
            return []
        granted = {scope for resource in resources for scope in (getattr(resource, "scopes", None) or [])}
        if not granted:
            return []
        return sorted(set(AtlassianScope.get_confluence_read_access()) - granted)

    async def run_sync(self) -> None:
        """
        Run full synchronization of Confluence Cloud data.

        Sync order:
        1. Users and Groups (global, includes group memberships)
        2. Spaces
            - Permissions
        3. Pages (per space)
            - Permissions
            - Attachments
            - Comments (inline, footer)
        4. Blogposts (per space)
            - Permissions
            - Attachments
            - Comments (inline, footer)
        """
        self._sync_problems = []
        self._skipped_items = defaultdict(int)
        self._sync_read_incomplete = False
        self._persistent_problems = set()
        self._quota_hit_in_run = False
        self._run_started_at = time.time()
        self._in_use += 1
        self._sync_finished = False
        self._now_blocks = {}
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting Confluence Cloud sync for org: {org_id}")

            # Ensure client is initialized
            if not self.external_client or not self.data_source:
                raise Exception("Confluence client not initialized. Call init() first.")

            await self.register_authenticated_source_user(
                self._authenticated_confluence_email, self._authenticated_confluence_account_id
            )

            # Load sync and indexing filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "confluence", self.connector_id, self.logger
            )
            await self._open_audit_window()

            # Step 1: Sync users
            await self._sync_users()

            # Step 2: Sync groups and memberships
            await self._sync_user_groups()
            await self._reconcile_inactive_app_users()

            # Step 3: Sync spaces
            spaces = await self._sync_spaces()

            # Step 4: Sync folders, pages and blogposts per space
            await self._sync_spaces_content(spaces)
            await self._remove_spaces_out_of_scope(spaces)

            # Step 5: Sync permission changes from audit log
            # This catches permission changes that don't update content's lastModified
            await self._sync_permission_changes_from_audit_log(spaces)

            # Step 6: Backfill placeholder ancestors that out-of-scope sync filters left
            # unreconciled (metadata + permissions only; they remain non-indexed stubs).
            await self._sweep_placeholder_records(org_id)

            self.logger.info("✅ Confluence sync completed successfully")
            self._sync_finished = True

        except asyncio.CancelledError:
            # Stopped on purpose (disabled, deleted, stopped): no notice saying it finished.
            self._sync_problems = []
            self._skipped_items = defaultdict(int)
            self._sync_read_incomplete = True
            raise
        except AtlassianQuotaExhausted as e:
            # Not a failure: the next sync resumes from the checkpoints this one kept.
            self.logger.warning(f"⚠️ Confluence sync stopped: {e}")
            self._sync_read_incomplete = True
            self._quota_hit_in_run = True
        except Exception as e:
            self.logger.error(f"❌ Error during Confluence sync: {e}", exc_info=True)
            # init already sent its own notice for a multi-site OAuth app.
            if not isinstance(e, ConnectorInitError):
                self._note_problem(f"The sync stopped: {str(e)[:200]}")
            raise
        finally:
            await self._report_sync_problems()
            await self._release()

    async def _release(self) -> None:
        """One use of this instance ended; close the client if it was evicted meanwhile."""
        self._in_use -= 1
        if self._close_after_sync and not self._in_use:
            self._close_after_sync = False
            await self.cleanup()

    # Per connector, across instances: the persistent conditions last reported, and the quota
    # reset time last reported.
    _persistent_noticed: dict[str, set[str]] = {}
    _quota_noticed_reset: dict[str, int] = {}

    # Most lines one notification lists; the rest are counted.
    _MAX_PROBLEMS_SHOWN = 10

    def _note_problem(self, problem: str, *, unread: bool = True, persistent: bool = False) -> None:
        """Note a step this sync could not finish; all of them are reported once when the sync ends.

        ``unread``: part of the source was not read (or not saved), so a full sync must not
        unlink what it did not see. False for a problem that left nothing unread, such as a
        removal that is retried next sync. ``persistent``: a setting-like condition (no audit
        log access, an access class with no group) reported when it first appears, not every run.
        """
        if problem not in self._sync_problems:
            self._sync_problems.append(problem)
        if persistent:
            self._persistent_problems.add(problem)
        if unread:
            self._sync_read_incomplete = True
        self._note_quota_hit()

    def _note_skipped(self, space_key: str, what: str) -> None:
        """Count one item skipped this run, e.g. ("ENG", "pages whose restrictions could not be read")."""
        self._skipped_items[(space_key, what)] += 1
        self._sync_read_incomplete = True
        self._note_quota_hit()

    def _note_quota_hit(self) -> None:
        """A problem noted while the quota is spent was most likely caused by it."""
        policy = self._client_policy
        if policy and policy.quota_reset_at and policy.quota_reset_at > time.time():
            self._quota_hit_in_run = True

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A full sync that left part of Confluence unread unlinks nothing it did not see."""
        return FullSyncCoverage(complete=not self._sync_read_incomplete)

    async def _report_sync_problems(self) -> None:
        """Send one notification listing what this sync could not do, to the creator and org admins.

        Nothing is sent for a sync with no problems. Every failing run sends it, even when
        the previous run sent the same one. Never raises: it runs as the sync ends.
        """
        problems = list(self._sync_problems) + [
            f"Space {space_key}: skipped {count} {what}"
            for (space_key, what), count in self._skipped_items.items()
        ]
        # A persistent condition is shown when it first appears; it is shown again once it
        # has gone away and come back.
        noticed = ConfluenceConnector._persistent_noticed.get(self.connector_id, set())
        problems = [p for p in problems if p not in self._persistent_problems or p not in noticed]
        if self._sync_finished:
            # A run that stopped early may not have reached the step that reports a condition.
            ConfluenceConnector._persistent_noticed[self.connector_id] = set(self._persistent_problems)
        quota_reset_at = self._quota_spent_this_run()
        if quota_reset_at:
            # By the minute: a rebuilt connector computes the same reset a little differently.
            reset_minute = int(quota_reset_at // 60)
            if ConfluenceConnector._quota_noticed_reset.get(self.connector_id) == reset_minute:
                return  # This quota stop was already reported; the next notice comes after the reset.
            ConfluenceConnector._quota_noticed_reset[self.connector_id] = reset_minute
            self._sync_read_incomplete = True
            problems.insert(0, (
                f"Atlassian's hourly API quota for this connector is used up until {quota_reset_text(quota_reset_at)}, "
                "so the sync stopped early; the steps below did not finish because of it, and the next "
                "sync after that time continues where this one stopped"
            ))
        if not problems:
            return
        shown = problems[: self._MAX_PROBLEMS_SHOWN]
        lines = "\n".join(f"- {p}" for p in shown)
        if len(problems) > len(shown):
            lines += f"\n- and {len(problems) - len(shown)} more (see the connector logs)"
        stopped = bool(quota_reset_at) or any(p.startswith("The sync stopped") for p in self._sync_problems)
        try:
            outcome = await self.notify_and_wait(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR if stopped else NotificationSeverity.WARNING,
                title=(
                    f"{self.display_name}: sync paused, Atlassian API quota used up" if quota_reset_at
                    else f"{self.display_name}: sync {'failed' if stopped else 'finished with problems'}"
                ),
                message=(
                    f"{'The sync stopped before it finished' if stopped else 'The sync finished, but'} "
                    f"these steps did not complete:\n{lines}\n"
                    "What was not read is read again on the next sync."
                ),
                recipient_user_ids=[self.created_by] if self.created_by else None,
                recipient_roles=[NotificationRecipientRole.ADMIN],
                suppress_repeats=False,
            )
            if outcome != NotificationOutcome.SENT:
                self.logger.warning(f"The sync problems notification was not sent ({outcome.value})")
        except Exception as e:
            self.logger.error(f"Could not send the sync problems notification: {e}")

    # Bound the per-level source fanout; Confluence is rate-limited.
    _PLACEHOLDER_SWEEP_CONCURRENCY = 5
    # Runaway backstop only — `visited` already guarantees termination (even on cyclic
    # source data). If we ever exceed this many distinct ancestors, something is wrong.
    _PLACEHOLDER_SWEEP_SAFETY_MAX = 10000

    async def _sweep_placeholder_records(self, org_id: str) -> None:
        """Backfill the full ancestor breadcrumb for placeholder stubs left unreconciled.

        Time/created-time sync filters don't respect hierarchy: an in-scope child can be
        synced while its parent (and higher ancestors) are filtered out, leaving stubs
        keyed by the ancestors' source ids with no real name or permissions.

        This is an ancestor-closure walk over child->parent pointers, implemented as a
        frontier BFS with a ``visited`` set (dedup + cycle guard) and a boundary that stops
        at ancestors already materialized as real records or at the space root:

          - seed the frontier from the stubs this sync left behind (one DB query);
          - fetch each frontier level from source (bounded concurrency), refreshing metadata
            + permissions but keeping ``is_placeholder=True`` so out-of-scope ancestors are
            never indexed/searchable;
          - expand to each fetched record's parent, skipping ones already visited or already
            real in the graph, until the frontier drains.

        Reconciliation is keyed by external id and idempotent, so an interrupted sweep is
        completed by the next sync's sweep.
        """
        visited: set[str] = set()
        seeds = await self.data_entities_processor.get_placeholder_records(self.connector_id)
        frontier: list[Record] = []
        for stub in seeds:
            if stub.external_record_id not in visited:
                visited.add(stub.external_record_id)
                frontier.append(stub)

        total = 0
        while frontier:
            self.logger.info(f"Placeholder sweep: backfilling {len(frontier)} ancestor stub(s)")
            results = await self._fetch_ancestor_level(org_id, frontier)

            backfills: list[tuple[Record, list[Permission]]] = []
            parent_refs: list[tuple[str, RecordType]] = []
            for stub, result in zip(frontier, results):
                if result:
                    record, permissions = result
                    # Folders carry no content, so sync them as normal (real) folder records —
                    # identical to _sync_folders output. Content records (pages/blogposts) stay
                    # metadata-only stubs so their out-of-scope content is never indexed/searchable.
                    is_folder = isinstance(record, FileRecord) and not record.is_file
                    record.is_placeholder = not is_folder
                else:
                    # Unchanged since it was filled in, or the source can't be read: keep the stub
                    # as stored. Writing it again with no permissions replaced its grants with
                    # none on a full sync, hiding the in-scope pages under it from everyone.
                    self.data_entities_processor.note_kept_records(self.connector_id, [stub.id])
                    if stub.parent_external_record_id and stub.parent_record_type:
                        parent_refs.append((stub.parent_external_record_id, stub.parent_record_type))
                    continue
                backfills.append((record, permissions))
                if record.parent_external_record_id and record.parent_record_type:
                    parent_refs.append((record.parent_external_record_id, record.parent_record_type))

            if backfills:
                # Creates/updates the stubs and, via _handle_parent_record, materializes the
                # next level's parent stubs so we can pick them up below.
                await self.data_entities_processor.on_new_records(backfills)

            next_frontier: list[Record] = []
            for parent_ext_id, _parent_type in parent_refs:
                if parent_ext_id in visited:
                    continue
                visited.add(parent_ext_id)
                parent_record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, parent_ext_id
                )
                if parent_record is None:
                    continue  # stub should exist after on_new_records; skip defensively
                if not parent_record.is_placeholder:
                    continue  # boundary: parent already synced in scope — nothing to backfill
                next_frontier.append(parent_record)

            total += len(frontier)
            if total > self._PLACEHOLDER_SWEEP_SAFETY_MAX:
                self.logger.error(
                    f"Placeholder sweep exceeded safety bound ({self._PLACEHOLDER_SWEEP_SAFETY_MAX}); aborting"
                )
                break
            frontier = next_frontier

    async def _fetch_ancestor_level(
        self, org_id: str, frontier: list[Record]
    ) -> list[tuple[Record, list[Permission]] | None]:
        """Fetch one frontier level from source with bounded concurrency.

        Returns results aligned with ``frontier``; each entry is the refreshed
        ``(record, permissions)`` or ``None`` when the ancestor is gone/inaccessible.
        Placeholders carry no external_revision_id, so ``_check_and_fetch_updated_record``
        always treats them as changed and returns fresh data.
        """
        semaphore = asyncio.Semaphore(self._PLACEHOLDER_SWEEP_CONCURRENCY)

        async def fetch_one(stub: Record) -> tuple[Record, list[Permission]] | None:
            async with semaphore:
                try:
                    # Attachments can't be parents in Confluence, so a folder stub goes to the
                    # folder API. Stubs past the first level are base Records with no is_file.
                    if _is_folder(stub):
                        return await self._fetch_folder_for_sweep(stub)
                    return await self._check_and_fetch_updated_record(org_id, stub)
                except Exception as e:
                    self.logger.warning(
                        f"Placeholder sweep: failed to fetch {stub.external_record_id} "
                        f"({stub.record_type}): {e}"
                    )
                    return None

        return await asyncio.gather(*[fetch_one(stub) for stub in frontier])

    async def _fetch_folder_for_sweep(
        self, stub: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch a folder ancestor from source and return it as a real folder record.

        Folders have no indexable content, so they are backfilled as normal folders
        (``is_placeholder`` left False by the caller) rather than kept as stubs.
        """
        folder_id = stub.external_record_id
        datasource = await self._get_fresh_datasource()
        response = await datasource.get_folder_by_id(id=int(folder_id))
        if not response or response.status != HttpStatusCode.SUCCESS.value:
            self.logger.warning(f"Folder {folder_id} not found at source during placeholder sweep")
            return None

        folder_record = self._transform_to_folder_file_record(response.json(), existing_record=stub)
        if not folder_record:
            return None

        permissions = await self._fetch_page_permissions(folder_id)
        if permissions is None:
            return None
        # A READ restriction keeps inheriting and is recorded as RESTRICTED
        # instead: the node then needs its own grant as well as the space.
        # EDIT-only restrictions stay STRICT.
        folder_record.access_rule = (
            AccessRule.RESTRICTED
            if any(p.type == PermissionType.READ for p in permissions)
            else AccessRule.STRICT
        )
        folder_record.rewrite_permissions = True
        return (folder_record, permissions)

    async def _sync_users(self) -> None:
        """
        Sync users from Confluence using offset-based pagination.

        Uses CQL search: type=user
        Filters out users without email addresses.
        """
        try:
            self.logger.info("Starting user synchronization...")
            self._held_back_users = []

            # Pagination variables
            batch_size = 100
            start = 0
            total_synced = 0
            total_skipped = 0
            seen_account_ids: set[str] = set()
            user_list_complete = True

            # Paginate through all users
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_users(
                    cql="type=user",
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch users: {response.status if response else 'No response'}")
                    self._note_problem("Couldn't read the Confluence user list (HTTP %s)" % (response.status if response else "no response"))
                    user_list_complete = False
                    break

                response_data = response.json()
                users_data = response_data.get("results", [])

                if not users_data:
                    break
                page_ids = {r.get("user", {}).get("accountId") or r.get("accountId") for r in users_data} - {None}
                if not page_ids - seen_account_ids:
                    # The same page again: the offset is being ignored, so stop rather than loop.
                    self._note_problem("The Confluence user list repeated a page, so it may not have been read to the end")
                    user_list_complete = False
                    break
                seen_account_ids |= page_ids

                # Transform users (skip users without email)
                app_users = []
                for user_result in users_data:
                    # Flatten: merge nested 'user' dict with top-level fields
                    user_data = {**user_result.get("user", {}), **{k: v for k, v in user_result.items() if k != "user"}}

                    # Skip if no email
                    email = user_data.get("email", "").strip()
                    if not email:
                        self.logger.warning(f"Skipping user creation with name : {user_data.get('displayName')}, Reason: No email found for the user")
                        total_skipped += 1
                        continue

                    app_user = self._transform_to_app_user(user_data)
                    if not app_user:
                        continue
                    # search/user carries no account status: hold back what the last complete
                    # group read saw inactive until this sync's group read says otherwise.
                    if (
                        not self._account_statuses_known
                        or app_user.source_user_id in self._inactive_account_ids
                    ):
                        self._held_back_users.append(app_user)
                        continue
                    app_users.append(app_user)

                # Save batch to database
                if app_users:
                    await self._save_app_users(app_users)
                    total_synced += len(app_users)
                    self.logger.info(f"Synced {len(app_users)} users (batch starting at {start})")

                # Cloud's /search/user has no next link and its totalSize is the page size, and a
                # page can come back short before the end, so only an empty page ends the list.
                start += len(users_data)

            if len(seen_account_ids) >= USER_SEARCH_CAP:
                # Atlassian: "cql=type=user will return up to 10k users".
                self._note_problem(
                    f"Confluence's user search returns at most {USER_SEARCH_CAP:,} users, so users past that "
                    "were not added and can't be given access from Confluence"
                )
            elif user_list_complete and seen_account_ids:
                await self._withdraw_users_gone_from_source(seen_account_ids)
            self.logger.info(f"✅ User sync complete. Synced: {total_synced}, Skipped (no email): {total_skipped}")

            try:
                if user_list_complete and len(seen_account_ids) < USER_SEARCH_CAP:
                    await self._link_platform_users_via_jira(seen_account_ids)
                else:
                    # A Jira account can only be trusted as a Confluence user when Confluence's list confirms it.
                    self.logger.warning("Not matching users who hide their email through Jira: Confluence's user list is incomplete")
            except Exception as link_err:
                self.logger.error(
                    f"Jira user linking fallback failed (user sync still complete): {link_err}",
                    exc_info=True,
                )
                self._note_problem(
                    f"Couldn't match users who hide their email through Jira: {str(link_err)[:200]}", unread=False
                )

        except Exception as e:
            self.logger.error(f"❌ User sync failed: {e}", exc_info=True)
            raise

    async def _include_jira_scope_setting(self, auth_config: dict[str, Any]) -> object:
        """The connector's own includeJiraScope, else the one saved on its OAuth app."""
        value = auth_config.get(INCLUDE_JIRA_SCOPE)
        oauth_config_id = auth_config.get(OAuthConfigKeys.OAUTH_CONFIG_ID)
        if value not in (None, "") or not oauth_config_id:
            return value
        try:
            from app.edition_config import fetch_oauth_config_by_id
            shared = await fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type="Confluence",
                config_service=self.config_service,
                logger=self.logger,
                org_id=auth_config.get("inheritedFromOrgId"),
            )
        except Exception as e:
            self.logger.warning(
                "Could not read the OAuth app for the Jira access setting: %s", type(e).__name__
            )
            return value
        return ((shared or {}).get(OAuthConfigKeys.CONFIG) or {}).get(INCLUDE_JIRA_SCOPE)

    async def _link_platform_users_via_jira(self, confluence_account_ids: set[str] | None = None) -> None:
        """
        Link active platform users to Confluence via Jira user search.

        Confluence user search omits emails when users keep them private. For platform
        users without a userAppRelation on this connector, Jira user search by email
        can still resolve the Atlassian accountId.
        
        Uses batch-parallel processing with bounded concurrency to efficiently handle
        large numbers of unlinked users while respecting API rate limits.
        """
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")
        auth_config = (config or {}).get("auth") or {}
        auth_type = (auth_config.get("authType") or "OAUTH").upper()
        if auth_type == "OAUTH":
            if not include_jira_scope_enabled(await self._include_jira_scope_setting(auth_config)):
                return

        datasource = await self._get_fresh_datasource()
        org_id = self.data_entities_processor.org_id
        policy = self._client_policy
        quota_already_spent = bool(policy and policy.quota_reset_at and policy.quota_reset_at > time.time())

        active_users = await self.data_entities_processor.get_all_active_users()
        linked_users = await self.data_entities_processor.get_all_app_users(self.connector_id)

        linked_emails = {
            (u.email or "").strip().lower()
            for u in linked_users
            if u.email
        }

        unlinked: list[User] = []
        for user in active_users:
            email = (user.email or "").strip()
            if not email or "@" not in email:
                continue
            if email.lower() in linked_emails:
                continue
            unlinked.append(user)

        if not unlinked:
            self.logger.info("No unlinked active platform users for Jira fallback linking")
            return

        self.logger.info(
            "Attempting Jira user linking for %s unlinked platform user(s)",
            len(unlinked),
        )

        # Batch-parallel processing with bounded concurrency
        linked_count = 0
        not_found_count = 0
        failed_count = 0
        unlinked_count = len(unlinked)
        semaphore = asyncio.Semaphore(10)  # Limit concurrent API calls
        batch_size = 20
        rate_limit_hit = False

        async def try_link_user(user: User) -> Optional[tuple[str, str, str]]:
            """
            Try to link a single user via Jira API.
            Returns (accountId, email, displayName) if successful, None otherwise.
            """
            nonlocal not_found_count, failed_count
            email = (user.email or "").strip()
            
            async with semaphore:
                try:
                    try:
                        account_id = await datasource.find_user_account_id_by_email(email)
                    except AtlassianQuotaExhausted as quota_error:
                        # Jira's quota, not Confluence's: undo the mark the shared policy just set
                        # (unless Confluence's own quota was already spent) and stop linking.
                        if policy is not None and not quota_already_spent:
                            policy.quota_reset_at = None
                        raise RuntimeError(f"Jira rate limit: {quota_error}") from quota_error
                    if not account_id:
                        not_found_count += 1
                        return None
                    
                    display_name = user.full_name or email
                    return (account_id, email, display_name)
                    
                except Exception as e:
                    error_msg = str(e)
                    # Check if this is a rate limit error
                    if "rate limit" in error_msg.lower() or "429" in error_msg:
                        raise
                    else:
                        self.logger.debug(f"Failed to link platform user {email} via Jira: {e}")
                        failed_count += 1
                        return None

        # Process in batches with early termination
        for i in range(0, len(unlinked), batch_size):
            # Early termination if we've linked all unresolved users
            if linked_count >= unlinked_count:
                break
            
            batch = unlinked[i:i + batch_size]
            tasks = [try_link_user(user) for user in batch]
            
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as batch_err:
                self.logger.error(f"Batch processing failed: {batch_err}")
                break
            
            # Collect successful results and check for rate limit
            batch_app_users = []
            for result in results:
                if isinstance(result, Exception):
                    error_msg = str(result)
                    if "rate limit" in error_msg.lower() or "429" in error_msg:
                        self.logger.warning(
                            "Jira API rate limit hit. Stopping Jira fallback linking. "
                            "Consider reducing sync frequency or requesting higher rate limits from Atlassian."
                        )
                        rate_limit_hit = True
                        self._note_problem(
                            "Jira's rate limit stopped matching users who hide their email; "
                            "the rest are tried next sync",
                            unread=False,
                        )
                        break
                    # Other exceptions already logged and counted in try_link_user
                    continue
                
                if result is None:
                    continue
                
                # Successfully found a match
                account_id, email, display_name = result
                if confluence_account_ids is not None and account_id not in confluence_account_ids:
                    # A Jira account that Confluence's user list lacks has no Confluence access.
                    continue
                app_user = AppUser(
                    app_name=Connectors.CONFLUENCE,
                    connector_id=self.connector_id,
                    source_user_id=account_id,
                    org_id=org_id,
                    email=email,
                    full_name=display_name,
                    is_active=False,
                )
                batch_app_users.append((app_user, account_id, email))
            
            # Break early if rate limit was hit
            if rate_limit_hit:
                break
            
            # Batch save all AppUsers from this batch in a single DB transaction
            if batch_app_users:
                try:
                    app_users_only = [user for user, _, _ in batch_app_users]
                    await self.data_entities_processor.on_new_app_users(app_users_only)
                    
                    # Process migrations individually (can't batch these easily)
                    for app_user, account_id, email in batch_app_users:
                        try:
                            await self.data_entities_processor.migrate_group_to_user_by_external_id(
                                group_external_id=account_id,
                                user_email=email,
                                connector_id=self.connector_id,
                            )
                        except Exception as migrate_err:
                            self.logger.warning(
                                "Failed to migrate pseudo-group permissions for %s: %s",
                                email,
                                migrate_err,
                            )
                        
                        linked_count += 1
                        self.logger.info(f"Linked platform user {email} via Jira (accountId={account_id})")
                
                except Exception as save_err:
                    self.logger.error(f"Failed to save batch of linked users: {save_err}")
                    failed_count += len(batch_app_users)

        self.logger.info(
            "Jira user linking complete. Linked: %s, Not found: %s, Failed: %s (Total: %s)",
            linked_count,
            not_found_count,
            failed_count,
            unlinked_count,
        )
        
        # If ALL lookups returned None (not found), it might be a Data Center deployment
        if linked_count == 0 and not_found_count == unlinked_count and not rate_limit_hit:
            self.logger.debug(
                "Jira fallback did not find any users. If this is a Confluence Data Center/Server "
                "deployment, this is expected (Jira fallback only works with Cloud instances)."
            )

    async def _save_app_users(self, app_users: list[AppUser]) -> None:
        await self.data_entities_processor.on_new_app_users(app_users)
        # For each user with email, migrate pseudo-group permissions (Confluence-specific)
        for user in app_users:
            if user.email and "@" in user.email and user.source_user_id:
                try:
                    await self.data_entities_processor.migrate_group_to_user_by_external_id(
                        group_external_id=user.source_user_id,
                        user_email=user.email,
                        connector_id=self.connector_id
                    )
                except Exception as e:
                    # Log error but continue with other users
                    self.logger.warning(
                        f"Failed to migrate pseudo-group permissions for user {user.email}: {e}",
                        exc_info=True
                    )

    async def _sync_user_groups(self) -> None:
        """
        Sync user groups and their memberships from Confluence.

        Steps:
        1. Fetch all groups with pagination
        2. For each group, fetch all members with pagination
        3. Create group and membership records
        """
        try:
            self.logger.info("Starting user group synchronization...")

            # Groups may change between sync runs on a long-lived connector instance.
            self._group_name_to_external_id = None
            self._group_read_complete = False
            self._pending_inactive_ids = set()
            self._pending_active_emails = set()
            every_member_list_read = True
            # (group, member emails, member accountIds) of each group read in full.
            read_groups: list[tuple[dict, list[str], list[str]]] = []
            # Every group id the listing returned, and whether it was read to the end.
            listed_group_ids: set[str] = set()
            group_list_complete = True

            # Pagination variables for groups
            batch_size = 50
            start = 0
            total_groups_synced = 0
            total_memberships_synced = 0

            # Paginate through all groups
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_groups(
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch groups: {response.status if response else 'No response'}")
                    self._note_problem("Couldn't read the Confluence group list (HTTP %s)" % (response.status if response else "no response"))
                    every_member_list_read = False
                    group_list_complete = False
                    break

                response_data = response.json()
                groups_data = response_data.get("results", [])

                if not groups_data:
                    if (response_data.get("_links") or {}).get("next"):
                        # Only a missing next link ends the list; removal must not see a gap as deletions.
                        group_list_complete = False
                        self._note_problem("The Confluence group list stopped early")
                    break

                for group_data in groups_data:
                    try:
                        group_id = group_data.get("id")
                        group_name = group_data.get("name")

                        if group_id:
                            listed_group_ids.add(str(group_id))
                        if not group_id or not group_name:
                            continue

                        self.logger.debug(f"  Reading group: {group_name} ({group_id})")

                        members = await self._fetch_group_members(group_id, group_name)
                        if members is None:
                            # Saving the group now would replace its members with an empty list.
                            self.logger.warning(
                                f"Keeping the stored members of group {group_name}: its member list could not be read"
                            )
                            self._note_problem(f"Couldn't read the members of group {group_name}; its stored members were kept")
                            every_member_list_read = False
                            continue
                        read_groups.append((group_data, *members))

                    except AtlassianQuotaExhausted:
                        raise
                    except Exception as group_error:
                        self.logger.error(f"❌ Failed to read group {group_data.get('name')}: {group_error}")
                        self._note_problem(f"Couldn't read group {group_data.get('name')}: {str(group_error)[:200]}")
                        every_member_list_read = False
                        continue

                try:
                    next_start = v1_next_start(response_data, start, len(groups_data), batch_size, use_link_offset=True)
                except ValueError as e:
                    # Groups are saved one by one, so the ones not reached keep what is stored.
                    self.logger.error(f"❌ Stopping the group list: {e}")
                    self._note_problem("Couldn't follow the Confluence group list to its next page")
                    every_member_list_read = False
                    group_list_complete = False
                    break
                if next_start is None:
                    break
                start = next_start

            if every_member_list_read:
                self._inactive_account_ids = self._pending_inactive_ids
                self._active_member_emails = self._pending_active_emails
                self._group_read_complete = True
                self._account_statuses_known = True
            # Members resolve only to users linked to the connector, so a reactivated
            # account has to be linked again before its groups are saved.
            await self._restore_reactivated_users()

            for group_data, member_emails, member_account_ids in read_groups:
                group_name = group_data.get("name")
                try:
                    user_group = self._transform_to_user_group(group_data)
                    if not user_group:
                        continue

                    try:
                        app_users = await self._resolve_group_app_users(
                            member_emails, member_account_ids
                        )
                    except Exception as lookup_error:
                        # Saving the group now would replace its members with an empty list.
                        self.logger.warning(
                            f"Keeping the stored members of group {group_name}: "
                            f"its members could not be looked up ({lookup_error})"
                        )
                        self._note_problem(f"Couldn't look up the members of group {group_name}; its stored members were kept")
                        continue

                    await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
                    total_groups_synced += 1
                    total_memberships_synced += len(app_users)
                    self.logger.debug(f"Group {group_name}: {len(app_users)} members")

                except Exception as group_error:
                    self.logger.error(f"❌ Failed to process group {group_name}: {group_error}")
                    self._note_problem(f"Couldn't save group {group_name}: {str(group_error)[:200]}")
                    continue

            if group_list_complete:
                await self._remove_groups_gone_from_source(listed_group_ids)

            self.logger.info(f"✅ Group sync complete. Groups: {total_groups_synced}, Memberships: {total_memberships_synced}")

        except Exception as e:
            self.logger.error(f"❌ Group sync failed: {e}", exc_info=True)
            raise

    async def _sync_spaces(self) -> list[RecordGroup]:
        """
        Sync spaces from Confluence with permissions using cursor-based pagination.

        Steps:
        1. Fetch all spaces with cursor pagination
        2. Apply exclusion filters if NOT_IN operator is used
        3. For each space, fetch permissions
        4. Create RecordGroup with Permission objects

        Sets ``_space_listing_complete`` to whether every space the filters admit
        was read and returned.
        """
        self._space_listing_complete = False
        try:
            self.logger.info("Starting space synchronization...")

            # Get sync filter values for API
            space_keys_filter = self.sync_filters.get(SyncFilterKey.SPACE_KEYS)
            included_space_keys = None
            excluded_space_keys = None

            # Determine filter mode
            if space_keys_filter is not None:
                filter_operator = space_keys_filter.get_operator()
                if filter_operator == FilterOperator.IN:
                    included_space_keys = space_keys_filter.get_value()
                    self.logger.info(f"Filtering to include space keys: {included_space_keys}")
                elif filter_operator == FilterOperator.NOT_IN:
                    excluded_space_keys = space_keys_filter.get_value()
                    self.logger.info(f"Filtering to exclude space keys: {excluded_space_keys}")

            # Pagination variables
            batch_size = 20
            cursor = None
            followed: set[str] = set()
            total_spaces_synced = 0
            total_permissions_synced = 0
            base_url = None  # Extract from first response
            record_groups = []
            listing_complete = True

            # Paginate through all spaces using cursor
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_spaces(
                    limit=batch_size,
                    cursor=cursor,
                    keys=included_space_keys  # None for NOT_IN (fetch all then filter), list for IN
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch spaces: {response.status if response else 'No response'}")
                    self._note_problem(
                        "Couldn't read the Confluence space list (HTTP %s); no space was removed" % (response.status if response else "no response")
                    )
                    listing_complete = False
                    break

                response_data = response.json()
                spaces_data = response_data.get("results") if isinstance(response_data, dict) else None
                if not isinstance(spaces_data, list):
                    self.logger.error("❌ Space listing had no results list")
                    self._note_problem("The Confluence space list came back without results; no space was removed")
                    listing_complete = False
                    break

                # Extract base URL from first response
                self._remember_site_wiki_url(response_data)
                if not base_url and response_data.get("_links", {}).get("base"):
                    base_url = response_data["_links"]["base"]
                    self.logger.debug(f"Base URL extracted: {base_url}")

                if not spaces_data:
                    if (response_data.get("_links") or {}).get("next"):
                        listing_complete = False
                        self._note_problem("The Confluence space list stopped early; no space was removed")
                    break

                # Apply client-side exclusion filter if NOT_IN
                if excluded_space_keys:
                    original_count = len(spaces_data)
                    spaces_data = [
                        space for space in spaces_data
                        if space.get("key") not in excluded_space_keys
                    ]
                    filtered_count = original_count - len(spaces_data)
                    if filtered_count > 0:
                        self.logger.debug(f"Filtered out {filtered_count} excluded spaces from batch")

                # Process each space
                record_groups_with_permissions = []
                for space_data in spaces_data:
                    try:
                        space_id = space_data.get("id")
                        space_name = space_data.get("name")

                        if not space_id or not space_name:
                            listing_complete = False
                            self._note_problem("A space came back without its id or name; no space was removed")
                            continue

                        self.logger.debug(f"Processing space: {space_name} ({space_id})")

                        # Fetch permissions for this space
                        permissions = await self._fetch_space_permissions(space_id, space_name)

                        # Create RecordGroup for space
                        record_group = self._transform_to_space_record_group(space_data, base_url)
                        if not record_group:
                            listing_complete = False
                            continue

                        record_groups.append(record_group)
                        total_spaces_synced += 1
                        if permissions is None:
                            # Saving the space replaces its grants; keep them and still sync its content.
                            self.logger.warning(
                                f"Keeping the stored access of space {space_name}: its permissions could not be read"
                            )
                            self._note_problem(f"Couldn't read the permissions of space {space_name}; its stored access was kept")
                            continue

                        total_permissions_synced += len(permissions)
                        record_groups_with_permissions.append((record_group, permissions))
                        self.logger.debug(f"Space {space_name}: {len(permissions)} permissions")

                    except AtlassianQuotaExhausted:
                        raise
                    except Exception as space_error:
                        self.logger.error(f"❌ Failed to process space {space_data.get('name')}: {space_error}")
                        self._note_problem(f"Couldn't sync space {space_data.get('name')}: {str(space_error)[:200]}")
                        listing_complete = False
                        continue

                # Save batch to database
                if record_groups_with_permissions:
                    await self.data_entities_processor.on_new_record_groups(record_groups_with_permissions)
                    self.logger.info(f"Synced batch of {len(record_groups_with_permissions)} spaces")

                # Extract next cursor from _links.next
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    listing_complete = False
                    self._note_problem("Couldn't follow the Confluence space list to its next page; no space was removed")
                    break
                if cursor in followed:
                    listing_complete = False
                    self._note_problem("The Confluence space list pointed back at a page it had read; no space was removed")
                    break
                followed.add(cursor)

            self.logger.info(f"✅ Space sync complete. Spaces: {total_spaces_synced}, Permissions: {total_permissions_synced}")

            self._space_listing_complete = listing_complete
            return record_groups

        except Exception as e:
            self.logger.error(f"❌ Space sync failed: {e}", exc_info=True)
            raise

    async def _sync_folders(self, space_key: str) -> None:
        """
        Sync folders from Confluence using v1 API.

        Uses offset- or cursor-based pagination (via ``_split_pagination_token``) with
        modification time filtering for incremental sync.
        Creates FileRecord (isFile=False) for each folder with permissions.
        Folders are synced before pages to ensure proper parent hierarchy.

        Args:
            space_key: The space key to sync folders from
        """
        try:
            self.logger.info(f"Starting folder synchronization for space {space_key}...")
            
            # Get last sync checkpoint
            sync_point_key = generate_record_sync_point_key(
                RecordType.FILE.value, "confluence_folders", space_key
            )
            last_sync_data = await self.pages_sync_point.read_sync_point(sync_point_key)
            last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None
            if last_sync_time:
                self.logger.info(f"🔄 Incremental sync: Fetching folders modified after {last_sync_time}")

            # Build date filter parameters from sync filters
            modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
            modified_after = None
            modified_before = None

            if modified_filter:
                modified_after, modified_before = modified_filter.get_datetime_iso()

            created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
            created_after = None
            created_before = None

            if created_filter:
                created_after, created_before = created_filter.get_datetime_iso()

            filter_modified_after = modified_after
            modified_after = listing_modified_after(filter_modified_after, last_sync_time, TIME_OFFSET_HOURS)
            if filter_modified_after and last_sync_time:
                self.logger.info(f"🔄 Using latest modified_after: {modified_after} (filter: {filter_modified_after}, checkpoint: {last_sync_time} less {TIME_OFFSET_HOURS}h)")
            elif filter_modified_after:
                self.logger.info(f"🔍 Using filter: Fetching folders modified after {modified_after}")
            elif last_sync_time:
                self.logger.info(f"🔄 Incremental sync: Fetching folders modified after {modified_after}")
            else:
                self.logger.info(f"🆕 Full sync: Fetching all folders (first time)")

            # Pagination variables — v1 content/search may use start (offset) or cursor
            batch_size = 50
            pagination_token: Optional[str] = None
            followed: set[str] = set()
            total_synced = 0
            total_permissions_synced = 0
            listing_complete = True

            # Paginate through all folders
            while True:
                datasource = await self._get_fresh_datasource()

                start_offset, cursor_token = self._split_pagination_token(pagination_token)

                response = await datasource.get_folders_v1(
                    modified_after=modified_after,
                    modified_before=modified_before,
                    created_after=created_after,
                    created_before=created_before,
                    start=start_offset,
                    cursor=cursor_token,
                    limit=batch_size,
                    space_key=space_key,
                    order_by="lastModified",
                    sort_order="asc",
                    expand=FOLDER_EXPAND_PARAMS,
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch folders: {response.status if response else 'No response'}")
                    self._note_problem(f"Space {space_key}: couldn't read the folder list (HTTP %s)" % (response.status if response else "no response"))
                    listing_complete = False
                    break

                response_data = response.json()
                items_data = response_data.get("results", [])

                if not items_data:
                    # Only a missing next link ends the listing; an empty page that points further is a failed read.
                    if (response_data.get("_links") or {}).get("next"):
                        listing_complete = False
                        self._note_problem(f"Space {space_key}: the folder list stopped early")
                    break

                # Transform folders to FileRecords with permissions
                records_with_permissions = []
                for item_data in items_data:
                    try:
                        item_id = item_data.get("id")
                        item_title = item_data.get("title")

                        if not item_id or not item_title:
                            continue

                        self.logger.debug(f"Processing folder: {item_title} ({item_id})")

                        # Check if record exists in DB
                        existing_record = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=item_id
                        )

                        # Fetch folder permissions. None means the ACL could not
                        # be read — skip rather than write the folder as STRICT,
                        # which would expose a restricted one to the whole space.
                        permissions = await self._fetch_page_permissions(item_id)
                        if permissions is None:
                            self.logger.warning(f"Skipping folder {item_id} this run: its restrictions could not be read")
                            self._note_skipped(space_key, "folders whose restrictions could not be read")
                            listing_complete = False
                            continue
                        total_permissions_synced += len(permissions)

                        # Transform to FileRecord
                        folder_record = self._transform_to_folder_file_record(
                            item_data, existing_record
                        )

                        if not folder_record:
                            continue

                        # A READ restriction keeps inheriting and is recorded as
                        # RESTRICTED instead; EDIT-only stays STRICT.
                        folder_record.access_rule = (
                            AccessRule.RESTRICTED
                            if any(p.type == PermissionType.READ for p in permissions)
                            else AccessRule.STRICT
                        )
                        folder_record.rewrite_permissions = True

                        # Add folder to batch
                        records_with_permissions.append((folder_record, permissions))
                        total_synced += 1
                        self.logger.debug(f"Folder {item_title}: {len(permissions)} permissions")

                    except AtlassianQuotaExhausted:
                        raise
                    except Exception as item_error:
                        self.logger.error(f"❌ Failed to process folder {item_data.get('title')}: {item_error}")
                        self._note_skipped(space_key, "folders that failed to sync")
                        listing_complete = False
                        continue

                # Save batch to database
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Synced batch of {len(records_with_permissions)} folders")
                await self._save_batch_checkpoint(
                    sync_point_key, items_data, nothing_skipped=listing_complete, field="last_sync_time",
                    current=last_sync_time,
                )

                # Extract next page token from _links.next
                # May contain either start=N (offset) or cursor=<token> depending on API version
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                pagination_token = self._extract_cursor_from_next_link(next_url)
                if not pagination_token:
                    listing_complete = False
                    self._note_problem(f"Space {space_key}: couldn't follow the folder list to its next page")
                    break
                if pagination_token in followed:
                    listing_complete = False
                    self._note_problem(f"Space {space_key}: the folder list pointed back at a page it had read")
                    break
                followed.add(pagination_token)

            # Update sync checkpoint with current time (only if we synced something)
            if not listing_complete:
                self.logger.warning(
                    f"Keeping the folders checkpoint for space {space_key}: not everything in "
                    "this window could be read, so the next sync reads it again"
                )
            elif total_synced > 0:
                current_sync_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                await self.pages_sync_point.update_sync_point(sync_point_key, {"last_sync_time": current_sync_time})
                self.logger.info(f"Updated folders sync checkpoint to {current_sync_time}")

            self.logger.info(f"✅ Folder sync complete. Folders: {total_synced}, Permissions: {total_permissions_synced}")

        except Exception as e:
            self.logger.error(f"❌ Folder sync failed: {e}", exc_info=True)
            raise

    async def _sync_content(
        self, space_key: str, record_type: RecordType, *, defer_checkpoint: bool = False,
        within_ids: list[str] | None = None,
    ) -> ContentListing:
        """
        Unified sync for pages and blogposts from Confluence using v1 API.

        Uses cursor-based pagination with modification time filtering for incremental sync.
        Creates WebpageRecord for each content item with attachments, comments, and permissions.

        Args:
            space_key: The space key to sync content from
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

            defer_checkpoint: Leave the checkpoint to the caller, which saves it only
                once the space's removals have succeeded.
            within_ids: Sync only these items, whatever their last modified time,
                still within the sync filters; the checkpoint is neither read nor moved.

        Returns:
            What the listing read, for removing records the source no longer has.
        """
        # Derive content_type from record_type for logging and sync point
        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"
        # Read once: each read goes to the config service.
        comments_indexed = await self._comments_indexed(record_type)

        try:
            self.logger.info(f"Starting {content_type} synchronization for space {space_key}...")
            # Get indexing filter settings based on content type (default=True means index if not configured)
            content_ids_filter = None
            if record_type == RecordType.CONFLUENCE_PAGE:
                content_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGES)
                content_attachments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGE_ATTACHMENTS)
                content_ids_filter = self.sync_filters.get(SyncFilterKey.PAGE_IDS)
            else:  # CONFLUENCE_BLOGPOST
                content_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOSTS)
                content_attachments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOST_ATTACHMENTS)
                content_ids_filter = self.sync_filters.get(SyncFilterKey.BLOGPOST_IDS)

            # Get content IDs filter based on content type
            content_ids = None
            content_ids_operator_str = None
            if content_ids_filter is not None:
                content_ids = content_ids_filter.get_value()
                content_ids_operator = content_ids_filter.get_operator()
                # Extract operator value string for datasource
                content_ids_operator_str = content_ids_operator.value if hasattr(content_ids_operator, 'value') else str(content_ids_operator)
                if content_ids:
                    action = "Excluding" if content_ids_operator_str == "not_in" else "Including"
                    self.logger.info(f"🔍 Filter: {action} {content_type}s by IDs: {content_ids}")

            # Get last sync checkpoint (use content_type as suffix)
            sync_point_key = generate_record_sync_point_key(
                RecordType.WEBPAGE.value, f"confluence_{content_type}s", space_key
            )
            last_sync_data = None if within_ids else await self.pages_sync_point.read_sync_point(sync_point_key)
            last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None
            # Where an unfinished full listing stopped; a full listing keeps no checkpoint until it ends.
            full_resume_after = None if last_sync_time else (last_sync_data or {}).get("full_resume_after")
            if full_resume_after:
                self.logger.info(f"🔁 Resuming the full {content_type} listing after {full_resume_after}")
                # What the earlier part read is not seen by this run, so a full sync must not unlink it.
                self._sync_read_incomplete = True
            if last_sync_time:
                self.logger.info(f"🔄 Incremental sync: Fetching {content_type}s modified after {last_sync_time}")

            # Build date filter parameters from sync filters
            # Get modified filter
            modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
            modified_after = None
            modified_before = None

            if modified_filter:
                modified_after, modified_before = modified_filter.get_datetime_iso()

            # Get created filter
            created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
            created_after = None
            created_before = None

            if created_filter:
                created_after, created_before = created_filter.get_datetime_iso()

            filter_modified_after = modified_after
            modified_after = listing_modified_after(
                filter_modified_after, last_sync_time or full_resume_after, TIME_OFFSET_HOURS
            )
            if filter_modified_after and last_sync_time:
                self.logger.info(f"🔄 Using latest modified_after: {modified_after} (filter: {filter_modified_after}, checkpoint: {last_sync_time} less {TIME_OFFSET_HOURS}h)")
            elif filter_modified_after:
                self.logger.info(f"🔍 Using filter: Fetching {content_type}s modified after {modified_after}")
            elif last_sync_time:
                self.logger.info(f"🔄 Incremental sync: Fetching {content_type}s modified after {modified_after}")
            else:
                self.logger.info(f"🆕 Full sync: Fetching all {content_type}s (first time)")

            # Log other filters if set
            if modified_before:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s modified before {modified_before}")
            if created_after:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s created after {created_after}")
            if created_before:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s created before {created_before}")

            # Pagination variables
            # v1 content/search may use offset (start) or cursor depending on Confluence version
            batch_size = 50
            pagination_token: Optional[str] = None
            followed: set[str] = set()
            total_synced = 0
            total_attachments_synced = 0
            total_permissions_synced = 0
            listing_complete = True
            seen: set[str] = set()

            # Paginate through all content items
            while True:
                datasource = await self._get_fresh_datasource()

                # Determine pagination parameters based on token type
                start_offset, cursor_token = self._split_pagination_token(pagination_token)

                if record_type == RecordType.CONFLUENCE_PAGE:
                    response = await datasource.get_pages_v1(
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        start=start_offset,
                        cursor=cursor_token,
                        limit=batch_size,
                        space_key=space_key,
                        page_ids=content_ids,
                        page_ids_operator=content_ids_operator_str,
                        include_children=True,
                        order_by="lastModified",
                        sort_order="asc",
                        expand=CONTENT_EXPAND_PARAMS,
                        within_ids=within_ids,
                    )
                else:  # CONFLUENCE_BLOGPOST
                    response = await datasource.get_blogposts_v1(
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        start=start_offset,
                        cursor=cursor_token,
                        limit=batch_size,
                        space_key=space_key,
                        blogpost_ids=content_ids,
                        blogpost_ids_operator=content_ids_operator_str,
                        order_by="lastModified",
                        sort_order="asc",
                        expand=CONTENT_EXPAND_PARAMS,
                        within_ids=within_ids,
                    )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch {content_type}s: {response.status if response else 'No response'}")
                    self._note_problem(f"Space {space_key}: couldn't read the {content_type} list (HTTP %s)" % (response.status if response else "no response"))
                    listing_complete = False
                    break

                response_data = response.json()
                self._remember_site_wiki_url(response_data)
                items_data = response_data.get("results") if isinstance(response_data, dict) else None
                if not isinstance(items_data, list):
                    self.logger.error(f"❌ {content_type.capitalize()} listing for space {space_key} had no results list")
                    self._note_problem(f"Space {space_key}: the {content_type} list came back without results")
                    listing_complete = False
                    break

                if not items_data:
                    # Only a missing next link ends the listing; an empty page that points further is a failed read.
                    if (response_data.get("_links") or {}).get("next"):
                        listing_complete = False
                        self._note_problem(f"Space {space_key}: the {content_type} list stopped early")
                    break


                # Transform items to WebpageRecords with permissions
                records_with_permissions = []
                for item_data in items_data:
                    try:
                        item_id = item_data.get("id")
                        item_title = item_data.get("title")
                        # Before anything can skip it: an item that fails to save still exists.
                        if item_id:
                            seen.add(str(item_id))

                        if not item_id or not item_title:
                            continue

                        self.logger.debug(f"Processing {content_type}: {item_title} ({item_id})")

                        # Check if record exists in DB
                        existing_record = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=item_id
                        )

                        # Fetch page permissions. None means the ACL could not be
                        # read — skip rather than write the page as STRICT, which
                        # would expose a restricted one to the whole space.
                        permissions = await self._fetch_page_permissions(item_id)
                        if permissions is None:
                            self.logger.warning(f"Skipping {content_type} {item_id} this run: its restrictions could not be read")
                            self._note_skipped(space_key, f"{content_type}s whose restrictions could not be read")
                            listing_complete = False
                            continue
                        total_permissions_synced += len(permissions)

                        # Transform to WebpageRecord with update tracking
                        webpage_record_update = await self._process_webpage_with_update(
                            item_data, record_type, existing_record, permissions
                        )

                        if not webpage_record_update.record:
                            continue

                        webpage_record = webpage_record_update.record

                        # Set indexing status based on filter
                        if not content_indexing_enabled:
                            webpage_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                        # A READ restriction keeps inheriting and is recorded as
                        # RESTRICTED instead; EDIT-only stays STRICT.
                        webpage_record.access_rule = (
                            AccessRule.RESTRICTED
                            if any(p.type == PermissionType.READ for p in permissions)
                            else AccessRule.STRICT
                        )

                        # Add item to batch
                        records_with_permissions.append((webpage_record, permissions))
                        total_synced += 1
                        self.logger.debug(f"{content_type.capitalize()} {item_title}: {len(permissions)} permissions")

                        # Extract space_id for children
                        space_data = item_data.get("space", {})
                        space_id = str(space_data.get("id")) if space_data.get("id") else None

                        # Get parent_node_id for dependent nodes (comments and attachments)
                        parent_node_id = webpage_record.id

                        # Process attachments - skip embedded images
                        children = item_data.get("children", {})
                        attachment_data = children.get("attachment", {})
                        attachments = attachment_data.get("results", [])
                        # Only a list read in full can say an attachment is gone.
                        attachments_complete = not attachments and not attachment_data.get("size") and not (
                            attachment_data.get("_links") or {}
                        ).get("next")

                        if attachments:
                            # v2 list API puts base URL only on response _links, not per attachment
                            v2_attachments_base_url: str | None = None

                            try:
                                embedded_image_ids: set[str] = set()

                                try:
                                    attachments_v2, v2_base_url, attachments_cut_short = (
                                        await self._list_current_attachments_v2(datasource, item_id, content_type)
                                    )
                                    attachments_complete = bool(attachments_v2) and not attachments_cut_short
                                    if attachments_v2:
                                        attachments = attachments_v2
                                        v2_attachments_base_url = v2_base_url
                                    if attachments_cut_short:
                                        listing_complete = False
                                        self._note_skipped(
                                            space_key, f"{content_type}s whose attachment list could not be read in full"
                                        )
                                except Exception as v2_error:
                                    self.logger.debug(f"Error fetching v2 attachments: {v2_error}")

                                attachment_mime_types: dict[str, str] = {}
                                for att in attachments:
                                    att_id = att.get("id")
                                    if att_id:
                                        mime_type = att.get("mediaType", "")
                                        if mime_type:
                                            attachment_mime_types[att_id] = mime_type
                                # Not inlined into the page: kept as an attachment record instead, or it would be in neither.
                                oversized_image_ids = images_not_inlined(attachments)
                                if content_type == "page":
                                    page_adf_response = await datasource.get_page_content_v2(
                                        page_id=item_id,
                                        body_format="atlas_doc_format"
                                    )
                                else:  # blogpost
                                    page_adf_response = await datasource.get_blogpost_content_v2(
                                        blogpost_id=item_id,
                                        body_format="atlas_doc_format"
                                    )

                                if not page_adf_response or page_adf_response.status != HttpStatusCode.SUCCESS.value:
                                    raise Exception(f"Failed to fetch ADF content: status={page_adf_response.status if page_adf_response else 'None'}")

                                page_adf_data = page_adf_response.json()
                                body = page_adf_data.get("body", {})
                                adf_body = body.get("atlas_doc_format", {})
                                adf_value = adf_body.get("value")

                                if adf_value:
                                    adf_content = json.loads(adf_value) if isinstance(adf_value, str) else adf_value
                                    media_nodes = extract_media_from_adf(adf_content)

                                    for media_info in media_nodes:
                                        attachment_id = self._resolve_confluence_attachment_id(media_info, attachments)
                                        if attachment_id:
                                            mime_type = attachment_mime_types.get(attachment_id, "")
                                            if mime_type.startswith("image/") and attachment_id not in oversized_image_ids:
                                                embedded_image_ids.add(attachment_id)

                                # Comments are streamed into the page only when indexed; otherwise their images stay files.
                                if comments_indexed:
                                    try:
                                        if content_type == "page":
                                            footer_comments_response = await datasource.get_page_footer_comments(
                                                id=int(item_id),
                                                body_format="atlas_doc_format",
                                                limit=100
                                            )
                                        else:
                                            footer_comments_response = await datasource.get_blog_post_footer_comments(
                                                id=int(item_id),
                                                body_format="atlas_doc_format",
                                                limit=100
                                            )

                                        if footer_comments_response and footer_comments_response.status == HttpStatusCode.SUCCESS.value:
                                            footer_comments_data = footer_comments_response.json()
                                            footer_comments = footer_comments_data.get("results", [])

                                            for comment in footer_comments:
                                                comment_body = comment.get("body", {})
                                                comment_adf = comment_body.get("atlas_doc_format", {})
                                                comment_adf_value = comment_adf.get("value")

                                                if comment_adf_value:
                                                    comment_content = json.loads(comment_adf_value) if isinstance(comment_adf_value, str) else comment_adf_value
                                                    comment_media_nodes = extract_media_from_adf(comment_content)

                                                    for media_info in comment_media_nodes:
                                                        attachment_id = self._resolve_confluence_attachment_id(media_info, attachments)
                                                        if attachment_id:
                                                            mime_type = attachment_mime_types.get(attachment_id, "")
                                                            if mime_type.startswith("image/") and attachment_id not in oversized_image_ids:
                                                                embedded_image_ids.add(attachment_id)
                                    except Exception as comment_error:
                                        self.logger.debug(f"Failed to fetch footer comments for embedded image detection: {comment_error}", exc_info=True)

                                    try:
                                        if content_type == "page":
                                            inline_comments_response = await datasource.get_page_inline_comments(
                                                id=int(item_id),
                                                body_format="atlas_doc_format",
                                                limit=100
                                            )
                                        else:
                                            inline_comments_response = await datasource.get_blog_post_inline_comments(
                                                id=int(item_id),
                                                body_format="atlas_doc_format",
                                                limit=100
                                            )

                                        if inline_comments_response and inline_comments_response.status == HttpStatusCode.SUCCESS.value:
                                            inline_comments_data = inline_comments_response.json()
                                            inline_comments = inline_comments_data.get("results", [])

                                            for comment in inline_comments:
                                                comment_body = comment.get("body", {})
                                                comment_adf = comment_body.get("atlas_doc_format", {})
                                                comment_adf_value = comment_adf.get("value")

                                                if comment_adf_value:
                                                    comment_content = json.loads(comment_adf_value) if isinstance(comment_adf_value, str) else comment_adf_value
                                                    comment_media_nodes = extract_media_from_adf(comment_content)

                                                    for media_info in comment_media_nodes:
                                                        attachment_id = self._resolve_confluence_attachment_id(media_info, attachments)
                                                        if attachment_id:
                                                            mime_type = attachment_mime_types.get(attachment_id, "")
                                                            if mime_type.startswith("image/") and attachment_id not in oversized_image_ids:
                                                                embedded_image_ids.add(attachment_id)
                                    except Exception as comment_error:
                                        self.logger.debug(f"Failed to fetch inline comments for embedded image detection: {comment_error}", exc_info=True)

                            except Exception as adf_error:
                                self.logger.warning(f"Failed to detect embedded images for {content_type} {item_id}, all attachments will be created as FileRecords: {adf_error}")
                                embedded_image_ids = set()

                            for attachment in attachments:
                                try:
                                    attachment_id = attachment.get("id")
                                    if not attachment_id:
                                        continue

                                    if attachment_id in embedded_image_ids:
                                        continue
                                    existing_attachment = await self.data_entities_processor.get_record_by_external_id(
                                        connector_id=self.connector_id,
                                        external_record_id=attachment_id
                                    )

                                    attachment_record = self._transform_to_attachment_file_record(
                                        attachment,
                                        item_id,
                                        space_id,
                                        existing_record=existing_attachment,
                                        parent_node_id=parent_node_id,
                                        attachment_api_base_url=v2_attachments_base_url,
                                    )

                                    if attachment_record:
                                        if not content_attachments_indexing_enabled:
                                            attachment_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                                        # Attachments follow the page's access through inheritance.
                                        attachment_record.inherit_permissions = True
                                        attachment_record.rewrite_permissions = True
                                        records_with_permissions.append((attachment_record, []))
                                        total_attachments_synced += 1

                                except AtlassianQuotaExhausted:
                                    raise
                                except Exception as att_error:
                                    self.logger.error(f"Failed to process attachment: {att_error}")
                                    self._note_skipped(space_key, "attachments that failed to sync")
                                    listing_complete = False
                                    continue

                        if attachments_complete and existing_record is not None:
                            await self._remove_attachments_gone(
                                item_id, {str(a.get("id")) for a in attachments if a.get("id")}, space_key
                            )

                    except AtlassianQuotaExhausted:
                        raise
                    except Exception as item_error:
                        self.logger.error(f"Failed to process {content_type} {item_data.get('title')}: {item_error}")
                        self._note_skipped(space_key, f"{content_type}s that failed to sync")
                        listing_complete = False
                        continue

                # Save batch to database
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Synced batch of {len(records_with_permissions)} items ({content_type}s + attachments)")
                    await self._drop_legacy_comment_records(self._saved_as_blocks(r for r, _ in records_with_permissions))
                if not within_ids:
                    await self._save_batch_checkpoint(
                        sync_point_key, items_data, nothing_skipped=listing_complete,
                        field="last_sync_time" if last_sync_time else "full_resume_after",
                        current=last_sync_time or full_resume_after,
                    )

                # Extract next page token from _links.next
                # May contain either start=N (offset) or cursor=<token> depending on API version
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                pagination_token = self._extract_cursor_from_next_link(next_url)
                if not pagination_token:
                    listing_complete = False
                    self._note_problem(f"Space {space_key}: couldn't follow the {content_type} list to its next page")
                    break
                if pagination_token in followed:
                    listing_complete = False
                    self._note_problem(f"Space {space_key}: the {content_type} list pointed back at a page it had read")
                    break
                followed.add(pagination_token)

            # Update sync checkpoint with current time (only if we synced something)
            # Using current time instead of last item's time avoids re-fetching due to the 24-hour offset
            if not listing_complete:
                self.logger.warning(
                    f"Keeping the {content_type}s checkpoint for space {space_key}: not everything in "
                    "this window could be read, so the next sync reads it again"
                )
            current_sync_time = None
            if listing_complete and total_synced > 0 and not within_ids:
                current_sync_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                if not defer_checkpoint:
                    await self.pages_sync_point.update_sync_point(
                        sync_point_key, {"last_sync_time": current_sync_time, "full_resume_after": ""}
                    )
                    self.logger.info(f"Updated {content_type}s sync checkpoint to {current_sync_time}")

            self.logger.info(f"✅ {content_type.capitalize()} sync complete. {content_type.capitalize()}s: {total_synced}, Attachments: {total_attachments_synced}, Permissions: {total_permissions_synced}")
            return ContentListing(
                full=not last_sync_time, complete=listing_complete, seen=frozenset(seen),
                checkpoint_key=sync_point_key, checkpoint_time=current_sync_time,
                resumed=bool(full_resume_after),
            )

        except Exception as e:
            self.logger.error(f"❌ {content_type.capitalize()} sync failed: {e}", exc_info=True)
            raise

    # A comment changed this long before the last checkpoint is still looked at: the checkpoint
    # is taken at the end of a sync, after its comment search.
    _COMMENT_CHECK_OVERLAP = timedelta(minutes=10)
    # How long an item a filtered search left out is not searched for again.
    _UNSTORED_RECHECK_SECONDS = 24 * 3600

    async def _reindex_pages_with_changed_comments(self, space_key: str) -> None:
        """Re-index the stored pages and blog posts of a space whose comments changed since the last check.

        A comment changes neither its page's version nor its last modified time, so the listing
        never brings the page back. One search per space covers both types; its own checkpoint
        moves only after a search read to the end, so quiet spaces don't re-index the same page
        on every run. The first sync only sets the checkpoint: it indexes every page anyway.
        """
        types = {
            "page": await self._comments_indexed(RecordType.CONFLUENCE_PAGE),
            "blogpost": await self._comments_indexed(RecordType.CONFLUENCE_BLOGPOST),
        }
        if not any(types.values()):
            return
        key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "confluence_comments", space_key)
        stored = await self.pages_sync_point.read_sync_point(key) or {}
        checked_at = stored.get("checked_at")
        searched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if not checked_at:
            await self.pages_sync_point.update_sync_point(key, {"checked_at": searched_at})
            return
        containers = await self._containers_changed_since(space_key, "comment", checked_at)
        if containers is None:
            return
        page_ids = {item_id for item_id, kind in containers.items() if types.get(kind)}

        records = []
        for page_id in sorted(page_ids):
            # Typed: re-indexing needs the WebpageRecord, not the base record the external-id lookup gives.
            record = await self.data_entities_processor.get_typed_record_by_external_id(self.connector_id, page_id)
            # A page left out of indexing on purpose stays out: a comment is not a request to index it.
            if (
                record is not None and not record.is_placeholder
                and record.indexing_status != ProgressStatus.AUTO_INDEX_OFF.value
            ):
                records.append(record)
        if records:
            self.logger.info(f"Re-indexing {len(records)} pages and blog posts in space {space_key} whose comments changed")
            await self._fix_legacy_mime_types(records)
            await self.data_entities_processor.reindex_existing_records(records)
            await self._drop_legacy_comment_records(self._saved_as_blocks(records, every_page=True))
        await self.pages_sync_point.update_sync_point(key, {"checked_at": searched_at})

    async def _containers_changed_since(
        self, space_key: str, content_type: str, checked_at: str
    ) -> dict[str, str] | None:
        """Pages and blog posts (id to type) whose comments or attachments changed since ``checked_at``.

        None, with the problem noted, when the search could not be read to the end.
        """
        since = datetime.fromisoformat(checked_at.replace("Z", "+00:00")) - self._COMMENT_CHECK_OVERLAP
        search_after = listing_modified_after(None, checked_at, TIME_OFFSET_HOURS)
        problem = f"Space {space_key}: couldn't check for changed {content_type}s; the next sync tries again"
        containers: dict[str, str] = {}
        token: str | None = None
        followed: set[str] = set()
        while True:
            offset, cursor = self._split_pagination_token(token)
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_comments_v1(
                    space_key=space_key, modified_after=search_after, start=offset, cursor=cursor, limit=100,
                    content_type=content_type,
                )
                data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
            except AtlassianQuotaExhausted:
                raise
            except Exception as e:
                self.logger.warning(f"Could not search the {content_type}s of space {space_key}: {e}")
                data = None
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                self._note_problem(problem, unread=False)
                return None
            for item in results:
                container = item.get("container") or {}
                # An attachment on a comment names the comment; only pages and blog posts are synced.
                if container.get("type") not in ("page", "blogpost") or not container.get("id"):
                    continue
                if self._changed_since(item, since):
                    containers[str(container["id"])] = container["type"]
            next_url = (data.get("_links") or {}).get("next")
            if not next_url:
                return containers
            token = self._extract_cursor_from_next_link(next_url)
            if not results or not token or token in followed:
                self._note_problem(problem, unread=False)
                return None
            followed.add(token)

    async def _resync_pages_with_changed_attachments(self, space_key: str) -> None:
        """Sync again the pages and blog posts of a space whose attachments changed since the last check.

        Uploading a file, or a new version of one, adds no page version, so the listing by
        last change never brings the page back and the new file would not be synced. Its
        own checkpoint moves only after every such page was synced again; the first sync
        only sets it.
        """
        key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "confluence_attachments", space_key)
        stored = await self.pages_sync_point.read_sync_point(key) or {}
        checked_at = stored.get("checked_at")
        searched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if not checked_at:
            await self.pages_sync_point.update_sync_point(key, {"checked_at": searched_at})
            return
        containers = await self._containers_changed_since(space_key, "attachment", checked_at)
        if containers is None:
            return
        complete = True
        for record_type, kind in ((RecordType.CONFLUENCE_PAGE, "page"), (RecordType.CONFLUENCE_BLOGPOST, "blogpost")):
            ids = sorted(item_id for item_id, item_kind in containers.items() if item_kind == kind)
            for start in range(0, len(ids), ID_SEARCH_CHUNK):
                result = await self._sync_content(
                    space_key, record_type, defer_checkpoint=True, within_ids=ids[start:start + ID_SEARCH_CHUNK],
                )
                complete = complete and result.complete
            if ids:
                self.logger.info(f"Synced {len(ids)} {kind}s in space {space_key} again: their attachments changed")
        if complete:
            await self.pages_sync_point.update_sync_point(key, {"checked_at": searched_at})
        else:
            self._note_problem(
                f"Space {space_key}: couldn't sync every page whose attachments changed; the next sync tries again",
                unread=False,
            )

    @staticmethod
    def _changed_since(item: dict[str, Any], since: datetime) -> bool:
        """Whether a search result changed after ``since``; true when its time can't be read."""
        changed = ((item.get("version") or {}).get("when") or "").replace("Z", "+00:00")
        if not changed:
            return True
        try:
            when = datetime.fromisoformat(changed)
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when > since

    async def _save_batch_checkpoint(
        self, sync_point_key: str, items: list[dict[str, Any]], *, nothing_skipped: bool,
        field: str, current: str | None,
    ) -> None:
        """Move a listing's checkpoint to the latest change in a batch that was just saved.

        The listing runs oldest change first, so a sync stopped later resumes after this
        batch instead of from the start. Only while nothing so far in the listing was
        skipped: the checkpoint never moves past an item that was not saved. ``field``
        is ``last_sync_time``, or ``full_resume_after`` for a full listing, which keeps
        no checkpoint until it ends. Never moves back: the listing looks back a day.
        """
        if not nothing_skipped or not items:
            return
        latest: datetime | None = None
        for item in items:
            when = ((item.get("history") or {}).get("lastUpdated") or {}).get("when")
            try:
                changed = datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None
            except ValueError:
                changed = None
            if changed is None or changed.tzinfo is None:
                # Without every item's time the batch can't say how far it got.
                return
            latest = changed if latest is None or changed > latest else latest
        if current:
            try:
                if latest <= datetime.fromisoformat(current.replace("Z", "+00:00")):
                    return
            except ValueError:
                pass
        checkpoint = latest.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        await self.pages_sync_point.update_sync_point(sync_point_key, {field: checkpoint})

    async def _sync_spaces_content(self, spaces: list[RecordGroup]) -> None:
        """Sync each space's folders, pages and blog posts, then remove what left the source.

        A space's page or blog post checkpoint moves only once that space's removals
        for that type have succeeded, so a failed read or delete is repeated by the
        next sync. A space that fails is reported and the rest still sync.
        """
        for space in spaces:
            try:
                await self._sync_space_content(space)
            except AtlassianQuotaExhausted:
                raise
            except Exception as e:
                self.logger.error(f"Could not sync space {space.short_name}: {e}", exc_info=True)
                self._note_problem(f"Space {space.short_name}: sync failed ({str(e)[:200]}); the next sync tries again")

    async def _sync_space_content(self, space: RecordGroup) -> None:
        """One space's folders, pages and blog posts, then what left it."""
        space_key = space.short_name

        self.logger.info(f"Syncing folders for space: {space.name} ({space_key})")
        await self._sync_folders(space_key)
        await self._remove_folders_gone(space)

        try:
            await self._reindex_pages_with_changed_comments(space_key)
        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            # The comment check keeps its clock, so the next sync looks at these comments again.
            self.logger.warning(f"Could not check the comments of space {space_key}: {e}", exc_info=True)
            self._note_problem(
                f"Space {space_key}: couldn't check for new comments; the next sync tries again", unread=False
            )

        for record_type in (RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST):
            self.logger.info(f"Syncing {record_type.value} records for space: {space.name} ({space_key})")
            listing = await self._sync_content(space_key, record_type, defer_checkpoint=True)
            settled = await self._remove_content_gone_from_source(space, record_type, listing)
            if settled:
                if listing.checkpoint_time:
                    await self.pages_sync_point.update_sync_point(
                        listing.checkpoint_key, {"last_sync_time": listing.checkpoint_time, "full_resume_after": ""}
                    )
            else:
                self.logger.warning(
                    f"Keeping the {record_type.value} checkpoint for space {space_key}: some removals "
                    "could not be checked or made, so the next sync repeats them"
                )
                self._note_problem(
                    f"Space {space_key}: couldn't check or remove some deleted items; the next sync tries again",
                    # After a full listing an unsettled check may hide items it missed: no unlink then.
                    unread=listing.full,
                )

        try:
            await self._resync_pages_with_changed_attachments(space_key)
        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            # The attachment check keeps its clock, so the next sync looks at these files again.
            self.logger.warning(f"Could not check the attachments of space {space_key}: {e}", exc_info=True)
            self._note_problem(
                f"Space {space_key}: couldn't check for changed attachments; the next sync tries again",
                unread=False,
            )

    async def _remove_content_gone_from_source(
        self, space: RecordGroup, record_type: RecordType, listing: ContentListing
    ) -> bool:
        """Delete this space's stored pages or blog posts that are gone from it or that the filters leave out.

        Gone means missing from the space's v2 listing, which holds every current
        and archived page (current blog post) the connector's account can see: an
        item trashed, purged, moved away, or no longer visible to the account is
        removed, and comes back on a later sync if it reappears. After a full
        listing, a current item the listing missed is removed only when a search of
        the space finds it without the sync filters and not with them. Returns
        False when a read or delete failed, or a missed item could not be
        explained, so the caller holds the checkpoint.
        """
        parents: dict[str, str] = {}
        existing = await self._list_space_content(space.external_group_id, record_type, parents)
        if existing is None:
            return False
        try:
            stored = await self._stored_content(space.external_group_id, record_type)
        except Exception as e:
            self.logger.warning(f"Could not read the stored records of space {space.short_name}; nothing removed: {e}")
            return False

        settled = True
        if not listing.full:
            settled = await self._sync_listed_but_not_stored(space, record_type, existing, stored, listing)
        else:
            # The content search leaves archived pages out; Confluence still shows them to those
            # allowed, so a full sync keeps them as stored rather than unlinking them.
            archived = [r.id for r in stored if existing.get(r.external_record_id) == "archived"]
            if archived:
                self.data_entities_processor.note_kept_records(self.connector_id, archived)
        doomed = [r for r in stored if r.external_record_id not in existing]
        if doomed:
            self.logger.info(
                f"Removing {len(doomed)} {record_type.value} records the account can no longer find "
                f"in space {space.short_name}"
            )
        filters = self._content_filter_kwargs(record_type)
        if listing.full and listing.complete and not filters and not listing.resumed:
            # Without filters a full listing should have seen every current item; one it missed
            # (a search index behind the database) is read again, or the full sync would unlink it.
            missed = [
                r.external_record_id for r in stored
                if existing.get(r.external_record_id) == "current" and r.external_record_id not in listing.seen
            ]
            if missed and not await self._read_missed_again(space, record_type, missed):
                settled = False
        if listing.full and listing.complete and filters:
            missed = [
                r.external_record_id for r in stored
                if existing.get(r.external_record_id) == "current" and r.external_record_id not in listing.seen
            ]
            if missed:
                verdict = await self._filter_verdict(
                    space.short_name, record_type, missed, filters, resumed=listing.resumed
                )
                if verdict is None:
                    settled = False
                else:
                    excluded, unexplained = verdict
                    if unexplained and not await self._read_missed_again(space, record_type, sorted(unexplained)):
                        self.logger.warning(
                            f"{len(unexplained)} {record_type.value} records in space {space.short_name} were missed by "
                            "the listing but the search can't say why; keeping them and the full sync owed"
                        )
                        settled = False
                    left_out = [r for r in stored if r.external_record_id in excluded]
                    if left_out:
                        self.logger.info(
                            f"Removing {len(left_out)} {record_type.value} records the sync filters now leave out "
                            f"in space {space.short_name}"
                        )
                    doomed.extend(left_out)
        if doomed:
            settled = await self._delete_content_records(doomed) and settled
        if record_type == RecordType.CONFLUENCE_PAGE:
            gone = {r.external_record_id for r in doomed}
            settled = await self._resync_moved_pages(
                space, [r for r in stored if r.external_record_id not in gone], parents,
                just_listed=listing.full and not listing.resumed,
            ) and settled
        return settled

    async def _resync_moved_pages(
        self, space: RecordGroup, stored: list[Record], parents: dict[str, str], *, just_listed: bool
    ) -> bool:
        """Sync again stored pages whose parent in the space listing differs from the stored one.

        Moving a page need not change its version, so the listing by last change may never
        bring it back, and its stored parent (and the access inherited through it) stays.
        A difference still there after a page was synced again comes from how the two APIs
        name the parent, not from a move: it is remembered and not synced again until the
        listed parent changes. ``just_listed``: this run listed every page, so the stored
        parents are fresh and differences are only remembered. False if a sync failed.
        """
        listed_differs = {
            r.external_record_id: parents[r.external_record_id] for r in stored
            if r.external_record_id in parents and parents[r.external_record_id] != r.parent_external_record_id
        }
        key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "parents", space.short_name)
        seen = await self.pages_sync_point.read_sync_point(key)
        before: dict[str, str] = json.loads(seen.get("differs") or "{}") if seen else {}
        moved = [] if just_listed else sorted(i for i, parent in listed_differs.items() if before.get(i) != parent)
        failed: set[str] = set()
        for start in range(0, len(moved), ID_SEARCH_CHUNK):
            chunk = moved[start:start + ID_SEARCH_CHUNK]
            result = await self._sync_content(
                space.short_name, RecordType.CONFLUENCE_PAGE, defer_checkpoint=True, within_ids=chunk,
            )
            if not result.complete:
                failed.update(chunk)
        if moved:
            self.logger.info(f"Synced {len(moved)} pages in space {space.short_name} again: their parent changed")
        # A failed page is left out, so the next sync tries it again.
        remembered = {i: parent for i, parent in listed_differs.items() if i not in failed}
        await self.pages_sync_point.update_sync_point(key, {"differs": json.dumps(remembered, sort_keys=True)})
        return not failed

    async def _read_missed_again(self, space: RecordGroup, record_type: RecordType, ids: list[str]) -> bool:
        """Read again, by id, stored items a full listing missed; True if every one was read.

        One that still can't be read is kept as stored, and the sync reports a partial read so
        the full sync does not unlink it.
        """
        seen: set[str] = set()
        complete = True
        for start in range(0, len(ids), ID_SEARCH_CHUNK):
            result = await self._sync_content(
                space.short_name, record_type, defer_checkpoint=True, within_ids=ids[start:start + ID_SEARCH_CHUNK],
            )
            seen |= result.seen
            complete = complete and result.complete
        still_missed = set(ids) - seen
        if still_missed or not complete:
            self._note_problem(
                f"Space {space.short_name}: {len(still_missed) or 'some'} stored {record_type.value} records could not be "
                "read in this sync; they are kept as they were",
            )
            return False
        return True

    async def _sync_listed_but_not_stored(
        self,
        space: RecordGroup,
        record_type: RecordType,
        existing: dict[str, str],
        stored: list[Record],
        listing: ContentListing,
    ) -> bool:
        """Sync current items the space lists that are not stored, whatever their last modified time.

        An item removed because the account lost sight of it comes back here once
        the account can see it again: regaining access does not change its last
        modified time, so the incremental search would never return it. The sync
        filters still apply, so an item they leave out stays out. Returns False
        when a read failed, so the caller holds the checkpoint.

        Items the search did not return (left out by the filters, or placeholder
        ancestors) are searched again once a day or when the filters change, not every run.
        """
        stored_ids = {r.external_record_id for r in stored}
        unstored = {
            item_id for item_id, status in existing.items()
            if status == "current" and item_id not in stored_ids and item_id not in listing.seen
        }
        key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "unstored", f"{record_type.value}/{space.short_name}")
        filters_tag = hashlib.sha256(
            json.dumps(self._content_filter_kwargs(record_type), sort_keys=True, default=str).encode()
        ).hexdigest()
        before = await self.pages_sync_point.read_sync_point(key) or {}
        recent = (
            before.get("filters") == filters_tag
            and time.time() - float(before.get("at") or 0) < self._UNSTORED_RECHECK_SECONDS
        )
        not_returned = set(json.loads(before.get("ids") or "[]")) & unstored if recent else set()
        unseen = sorted(unstored - not_returned)
        if not unseen:
            return True
        self.logger.info(
            f"Syncing {len(unseen)} {record_type.value} items space {space.short_name} lists but PipesHub does not hold"
        )
        complete = True
        for start in range(0, len(unseen), ID_SEARCH_CHUNK):
            chunk = unseen[start:start + ID_SEARCH_CHUNK]
            result = await self._sync_content(space.short_name, record_type, defer_checkpoint=True, within_ids=chunk)
            complete = complete and result.complete
            if result.complete:
                not_returned |= set(chunk) - set(result.seen)
        await self.pages_sync_point.update_sync_point(key, {
            "filters": filters_tag,
            "at": before.get("at") if recent else time.time(),
            "ids": json.dumps(sorted(not_returned)),
        })
        return complete

    async def _list_space_content(
        self, space_id: str, record_type: RecordType, parents: dict[str, str] | None = None
    ) -> dict[str, str] | None:
        """Status by id of every page or blog post in a space the account can see; None unless read to the end.

        The v2 listing defaults to current and archived pages and current blog
        posts, and it comes from Confluence's database, not the search index. Only
        a response without a next link ends it; an empty page that still points
        further is a failed read.
        """
        datasource = await self._get_fresh_datasource()
        list_content = (
            datasource.get_pages_in_space if record_type == RecordType.CONFLUENCE_PAGE
            else datasource.get_blog_posts_in_space
        )
        statuses: dict[str, str] = {}
        cursor: str | None = None
        visited: set[str] = set()
        while True:
            kwargs: dict[str, Any] = {"id": int(space_id), "limit": V2_CONTENT_LIST_LIMIT}
            if cursor:
                kwargs["cursor"] = cursor
            try:
                response = await list_content(**kwargs)
                data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
            except Exception as e:
                self.logger.warning(f"Could not list the content of space {space_id}; nothing removed: {e}")
                return None
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                self.logger.warning(
                    f"Could not list the content of space {space_id} "
                    f"(HTTP {response.status if response else 'no response'}); nothing removed"
                )
                return None
            for item in results:
                if isinstance(item, dict) and item.get("id"):
                    statuses[str(item["id"])] = str(item.get("status") or "current")
                    if parents is not None and item.get("parentId"):
                        parents[str(item["id"])] = str(item["parentId"])
            next_url = (data.get("_links") or {}).get("next")
            if not next_url:
                return statuses
            next_cursor = self._extract_cursor_from_next_link(next_url)
            if not results or not next_cursor or next_cursor in visited:
                self.logger.warning(f"Can't follow the content listing of space {space_id}; nothing removed")
                return None
            visited.add(next_cursor)
            cursor = next_cursor

    async def _stored_content(
        self, space_id: str, record_type: RecordType, *, with_placeholders: bool = False
    ) -> list[Record]:
        """Stored records of this type in the space, without placeholder ancestors unless asked.

        Placeholders hold the breadcrumb of in-scope pages and are never listed themselves.
        """
        stored: list[Record] = []
        after_key: str | None = None
        while True:
            # The trash too: a removal scan must reach a trashed page the space no longer lists.
            page = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, space_id, RECORD_SCAN_PAGE_SIZE, after_key,
                visibility=RecordVisibility.ALL,
            )
            stored.extend(
                r for r in page if r.record_type == record_type and (with_placeholders or not r.is_placeholder)
            )
            if len(page) < RECORD_SCAN_PAGE_SIZE:
                return stored
            after_key = page[-1].id

    def _content_filter_kwargs(self, record_type: RecordType) -> dict[str, Any]:
        """The content sync filters as search arguments, as the listing applies them; empty when none is set."""
        kwargs: dict[str, Any] = {}
        is_page = record_type == RecordType.CONFLUENCE_PAGE
        ids_filter = self.sync_filters.get(SyncFilterKey.PAGE_IDS if is_page else SyncFilterKey.BLOGPOST_IDS)
        if ids_filter is not None and ids_filter.get_value():
            operator = ids_filter.get_operator()
            operator_str = operator.value if hasattr(operator, "value") else str(operator)
            if is_page:
                kwargs.update(page_ids=list(ids_filter.get_value()), page_ids_operator=operator_str, include_children=True)
            else:
                kwargs.update(blogpost_ids=list(ids_filter.get_value()), blogpost_ids_operator=operator_str)
        for key, after_arg, before_arg in (
            (SyncFilterKey.MODIFIED, "modified_after", "modified_before"),
            (SyncFilterKey.CREATED, "created_after", "created_before"),
        ):
            date_filter = self.sync_filters.get(key)
            if date_filter:
                after, before = date_filter.get_datetime_iso()
                if after:
                    kwargs[after_arg] = after
                if before:
                    kwargs[before_arg] = before
        return kwargs

    async def _filter_verdict(
        self, space_key: str, record_type: RecordType, ids: list[str], filters: dict[str, Any],
        *, resumed: bool = False,
    ) -> tuple[set[str], set[str]] | None:
        """Split ids the full listing missed into (left out by the filters, unexplained); None if a search failed.

        Left out: a search of the space finds the id without the filters and not
        with them. Unexplained: the search doesn't find it at all (a lagging index)
        or finds it with the filters too (the listing skipped it). After a resumed
        listing, one the filters still admit was read before it stopped, so only
        an id the search doesn't find is unexplained.
        """
        anywhere = await self._search_ids_in_space(space_key, record_type, ids, {})
        if anywhere is None:
            return None
        in_scope = await self._search_ids_in_space(space_key, record_type, sorted(anywhere), filters) if anywhere else set()
        if in_scope is None:
            return None
        excluded = anywhere - in_scope
        if resumed:
            return excluded, set(ids) - anywhere
        return excluded, set(ids) - excluded

    async def _search_ids_in_space(
        self, space_key: str, record_type: RecordType, ids: list[str], filters: dict[str, Any]
    ) -> set[str] | None:
        """Which of ``ids`` a search of the space finds under ``filters``; None if a search failed."""
        datasource = await self._get_fresh_datasource()
        search = datasource.get_pages_v1 if record_type == RecordType.CONFLUENCE_PAGE else datasource.get_blogposts_v1
        found: set[str] = set()
        for start in range(0, len(ids), ID_SEARCH_CHUNK):
            chunk = ids[start:start + ID_SEARCH_CHUNK]
            token: str | None = None
            followed: set[str] = set()
            while True:
                offset, cursor = self._split_pagination_token(token)
                try:
                    response = await search(
                        space_key=space_key, within_ids=chunk, start=offset, cursor=cursor,
                        limit=ID_SEARCH_CHUNK, **filters,
                    )
                    data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
                except Exception as e:
                    self.logger.warning(f"Could not search space {space_key} for filtered-out content: {e}")
                    return None
                results = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results, list):
                    self.logger.warning(f"Could not search space {space_key} for filtered-out content; nothing removed")
                    return None
                found.update(str(item["id"]) for item in results if isinstance(item, dict) and item.get("id"))
                next_url = (data.get("_links") or {}).get("next")
                if not next_url:
                    break
                next_token = self._extract_cursor_from_next_link(next_url)
                if not results or not next_token or next_token in followed:
                    self.logger.warning(f"Can't follow the search of space {space_key}; nothing removed")
                    return None
                followed.add(next_token)
                token = next_token
        return found

    async def _list_folder_ids(self, space_key: str) -> set[str] | None:
        """Ids of every folder a search of the space finds; None unless read to the end."""
        ids: set[str] = set()
        token: str | None = None
        followed: set[str] = set()
        while True:
            offset, cursor = self._split_pagination_token(token)
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_folders_v1(space_key=space_key, start=offset, cursor=cursor, limit=250)
            data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                return None
            ids.update(str(item["id"]) for item in results if isinstance(item, dict) and item.get("id"))
            next_url = (data.get("_links") or {}).get("next")
            if not next_url:
                return ids
            token = self._extract_cursor_from_next_link(next_url)
            if not results or not token or token in followed:
                return None
            followed.add(token)

    async def _remove_folders_gone(self, space: RecordGroup) -> None:
        """Delete the space's stored folders Confluence no longer has there (trashed, purged or moved away).

        Only a folder the search no longer finds is looked up, one by one; a lookup that
        fails keeps it. Its pages are detached to the space root, as Confluence does.
        """
        try:
            stored = [r for r in await self._stored_content(space.external_group_id, RecordType.FILE) if _is_folder(r)]
        except Exception as e:
            self.logger.warning(f"Could not read the stored folders of space {space.short_name}: {e}")
            return
        if not stored:
            return
        listed = await self._list_folder_ids(space.short_name)
        if listed is None:
            self._note_problem(
                f"Space {space.short_name}: couldn't check for deleted folders; the next sync tries again", unread=False
            )
            return
        if self.data_entities_processor.full_sync_running(self.connector_id):
            # A folder filled in above an in-scope page is not listed when the filters leave it out;
            # unseen, the full sync would unlink it and hide the pages under it.
            for folder in stored:
                if str(folder.external_record_id) in listed and await self.data_entities_processor.get_records_by_parent(
                    self.connector_id, folder.external_record_id
                ):
                    self.data_entities_processor.note_kept_records(self.connector_id, [folder.id])
        gone: list[Record] = []
        for folder in (f for f in stored if str(f.external_record_id) not in listed):
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_folder_by_id(id=int(folder.external_record_id))
            except Exception as e:
                self.logger.warning(f"Could not look up folder {folder.external_record_id}: {e}")
                continue
            if response and response.status == HttpStatusCode.NOT_FOUND.value:
                gone.append(folder)
            elif response and response.status == HttpStatusCode.SUCCESS.value:
                data = response.json() or {}
                moved = data.get("spaceId") and str(data["spaceId"]) != str(space.external_group_id)
                if data.get("status") in ("trashed", "deleted") or moved:
                    gone.append(folder)
        if gone:
            self.logger.info(f"Removing {len(gone)} folders space {space.short_name} no longer has")
            if not await self._delete_content_records(gone):
                self._note_problem(
                    f"Space {space.short_name}: couldn't remove some deleted folders; the next sync tries again",
                    unread=False,
                )

    async def _remove_attachments_gone(self, page_id: str, current_ids: set[str], space_key: str) -> None:
        """Delete a page's stored attachments that its complete attachment list no longer has.

        Removing an attachment leaves the page in place, so nothing else ever removed its record.
        """
        try:
            children = await self.data_entities_processor.get_records_by_parent(
                connector_id=self.connector_id, parent_external_record_id=page_id
            )
        except Exception as e:
            self.logger.warning(f"Could not read the stored attachments of {page_id}: {e}")
            self._note_problem(
                f"Space {space_key}: couldn't check a page for deleted attachments; the next sync tries again",
                unread=False,
            )
            return
        gone = [
            c for c in children
            if c.record_type == RecordType.FILE and not _is_folder(c) and str(c.external_record_id) not in current_ids
        ]
        if not gone:
            return
        self.logger.info(f"Removing {len(gone)} attachments page {page_id} no longer has")
        if not await self._delete_content_records(gone):
            self._note_problem(
                f"Space {space_key}: couldn't remove some deleted attachments; the next sync tries again", unread=False
            )

    def _saved_as_blocks(self, records: Iterable[Record], *, every_page: bool = False) -> dict[str, RecordType]:
        """The saved ``records`` just stored as blocks (or, with ``every_page``, all their pages and blog posts)."""
        pages: dict[str, RecordType] = {}
        for record in records:
            record_type = self._now_blocks.pop(record.external_record_id, None)
            if record_type is None and every_page and record.record_type in (
                RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST
            ) and record.mime_type == MimeTypes.BLOCKS.value:
                record_type = record.record_type
            if record_type is not None:
                pages[record.external_record_id] = record_type
        return pages

    async def _drop_legacy_comment_records(self, pages: dict[str, RecordType]) -> None:
        """Delete the comment records older versions stored under these pages, now stored as blocks.

        The blocks carry the page's comments, so the separate records would answer twice.
        An HTML page keeps them until it is converted (when it next changes or is re-indexed).
        With comment indexing off the blocks carry no comments, so the records stay.
        """
        comment_types = (RecordType.COMMENT, RecordType.INLINE_COMMENT)
        indexed: dict[RecordType, bool] = {}
        for page_id, record_type in sorted(pages.items()):
            if record_type not in indexed:
                indexed[record_type] = await self._comments_indexed(record_type)
            if not indexed[record_type]:
                continue
            try:
                queue, owned = [page_id], []
                while queue:
                    for child in await self.data_entities_processor.get_records_by_parent(
                        self.connector_id, queue.pop(), visibility=RecordVisibility.ALL
                    ):
                        if child.record_type in comment_types:
                            owned.append(child)
                            queue.append(child.external_record_id)
                for child in owned:
                    await self.data_entities_processor.on_record_deleted(child.id)
                if owned:
                    self.logger.info(f"Removed {len(owned)} comment records of page {page_id}: its blocks hold them")
            except Exception as e:
                # shortcut: not retried; the page is blocks now, so these stay until the page is deleted.
                self.logger.warning(f"Could not remove the old comment records of page {page_id}: {e}")

    async def _delete_content_records(self, records: list[Record]) -> bool:
        """Delete pages or blog posts with their file attachments and comments; True if every delete succeeded.

        Comments and their replies are records under the page, so they go with it.
        Folders and child pages stay: Confluence moves them up when their parent
        goes, so they are detached to the space root, where browse lists them.
        """
        comment_types = (RecordType.COMMENT, RecordType.INLINE_COMMENT)
        failed = 0
        for record in records:
            try:
                owned: list[Record] = []
                kept: list[Record] = []
                queue = [record]
                while queue:
                    parent = queue.pop()
                    for child in await self.data_entities_processor.get_records_by_parent(
                        self.connector_id, parent.external_record_id, visibility=RecordVisibility.ALL
                    ):
                        if child.record_type in comment_types:
                            owned.append(child)
                            queue.append(child)
                        elif parent is not record:
                            continue
                        elif child.record_type == RecordType.FILE and child.mime_type != MimeTypes.FOLDER.value:
                            owned.append(child)
                        else:
                            kept.append(child)
                # Detach first: a retry after a failed delete finds the page again, not its orphans.
                if kept:
                    await self.data_entities_processor.on_records_detached_from_parent([c.id for c in kept])
                for child in owned:
                    await self.data_entities_processor.on_record_deleted(child.id)
                await self.data_entities_processor.on_record_deleted(record.id)
            except Exception as e:
                failed += 1
                self.logger.warning(f"Could not delete {record.record_type} {record.external_record_id}: {e}")
        if failed:
            self.logger.warning(f"{failed} of {len(records)} records could not be deleted; retrying next sync")
        return not failed

    async def _remove_spaces_out_of_scope(self, spaces: list[RecordGroup]) -> None:
        """Delete the stored spaces this sync no longer lists, with their records and checkpoints.

        A space drops out when the space filter leaves it out, it is deleted or
        archived, or the account lost access; it comes back on a later sync if it
        is listed again, and clearing its checkpoints makes that a full re-read.
        Runs only after a space listing read to the end, and only when the listed
        set differs from the one last cleaned up or a removal is still unfinished.
        A listing with no spaces at all removes nothing: that is the account
        itself failing, not every space leaving. A space whose removal failed part
        way stays pending, so its space and checkpoints go once its records have.
        """
        if not self._space_listing_complete:
            return
        in_scope = sorted({str(s.external_group_id) for s in spaces})
        if not in_scope:
            self.logger.warning("The space listing came back empty; no space is removed")
            return
        key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "confluence_space_scope", "all")
        cleaned = await self.pages_sync_point.read_sync_point(key) or {}
        pending = {str(p) for p in (cleaned.get("pending") or []) if str(p) not in in_scope}
        if cleaned.get("space_ids") == in_scope and not pending:
            return

        wanted = set(in_scope)
        by_space: dict[str, list[Record]] = defaultdict(list)
        after_key: str | None = None
        try:
            while True:
                page = await self.data_entities_processor.get_records_by_status(
                    self.connector_id, None, limit=RECORD_SCAN_PAGE_SIZE, after_key=after_key,
                    visibility=RecordVisibility.ALL,
                )
                for r in page:
                    if r.external_record_group_id and r.external_record_group_id not in wanted:
                        by_space[r.external_record_group_id].append(r)
                if len(page) < RECORD_SCAN_PAGE_SIZE:
                    break
                after_key = page[-1].id
        except Exception as e:
            self.logger.warning(f"Could not read the stored spaces; no space removed: {e}")
            self._note_problem("Couldn't check for deleted spaces; the next sync tries again", unread=False)
            return

        # A space last seen in scope that has no records is in no record, so it comes from the last scope.
        left_scope = {str(p) for p in (cleaned.get("space_ids") or [])} - wanted
        unfinished: list[str] = []
        for space_id in sorted(set(by_space) | pending | left_scope):
            try:
                removed = await self._remove_space(space_id, by_space.get(space_id, []))
            except Exception as e:
                removed = False
                self.logger.warning(f"Could not finish removing space {space_id}; retrying next sync: {e}")
            if not removed:
                unfinished.append(space_id)
                self._note_problem(
                    f"Couldn't finish removing space {space_id}, which Confluence no longer lists; "
                    "the next sync tries again",
                    unread=False,
                )
        # The store merges fields, so an empty list clears what an earlier run left pending.
        await self.pages_sync_point.update_sync_point(
            key, {"pending": unfinished} if unfinished else {"space_ids": in_scope, "pending": []}
        )

    async def _remove_space(self, space_id: str, records: list[Record]) -> bool:
        """Delete one space's records, clear its checkpoints, then drop the space; True if all of it worked."""
        self.logger.info(f"Removing space {space_id} and its {len(records)} records: this sync no longer lists it")
        try:
            group = await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, space_id)
        except Exception as e:
            self.logger.warning(f"Could not read space {space_id}; not removed: {e}")
            return False
        failed = 0
        for record in records:
            try:
                await self.data_entities_processor.on_record_deleted(record.id)
            except Exception as e:
                failed += 1
                self.logger.warning(f"Could not delete {record.external_record_id}: {e}")
        if failed:
            self.logger.warning(f"{failed} records of space {space_id} could not be deleted; retrying next sync")
            return False
        if group is not None and group.short_name:
            # An empty checkpoint reads as none, so the space is read in full if it is listed again.
            for checkpoint_key in (
                generate_record_sync_point_key(RecordType.FILE.value, "confluence_folders", group.short_name),
                generate_record_sync_point_key(RecordType.WEBPAGE.value, "confluence_pages", group.short_name),
                generate_record_sync_point_key(RecordType.WEBPAGE.value, "confluence_blogposts", group.short_name),
            ):
                await self.pages_sync_point.update_sync_point(
                    checkpoint_key, {"last_sync_time": "", "full_resume_after": ""}
                )
            for kind in ("confluence_comments", "confluence_attachments"):
                await self.pages_sync_point.update_sync_point(
                    generate_record_sync_point_key(RecordType.WEBPAGE.value, kind, group.short_name), {"checked_at": ""}
                )
            await self.pages_sync_point.update_sync_point(
                generate_record_sync_point_key(RecordType.WEBPAGE.value, "parents", group.short_name), {"differs": ""}
            )
        if group is not None and not await self.data_entities_processor.on_record_group_deleted(
            space_id, self.connector_id
        ):
            self.logger.warning(f"Could not delete space {space_id}; retrying next sync")
            return False
        return True

    def _audit_sync_key(self) -> str:
        return generate_record_sync_point_key(RecordType.WEBPAGE.value, "permissions", "audit_log")

    async def _open_audit_window(self) -> None:
        """Start the audit log window when the sync starts, if none is open yet.

        Without it the first sync opened the window only when it ended, so a restriction
        changed while it ran (after its page was read) was never read again.
        """
        try:
            if not await self.audit_log_sync_point.read_sync_point(self._audit_sync_key()):
                await self.audit_log_sync_point.update_sync_point(
                    self._audit_sync_key(), {"last_sync_time_ms": int(datetime.now(timezone.utc).timestamp() * 1000)}
                )
        except Exception as e:
            # The window then opens when the sync ends, as before.
            self.logger.warning(f"Could not open the audit log window: {e}")

    async def _sync_permission_changes_from_audit_log(self, spaces: list[RecordGroup] | None = None) -> None:
        """
        Sync permission changes for pages/blogs using Confluence Audit Log API.

        This method tracks permission changes that don't update the content's
        lastModified timestamp, ensuring we capture:
        - Content restriction added (user/group gets access)
        - Content restriction removed (user/group loses access)

        Flow:
        1. Get last audit sync time
        2. If first run (no checkpoint): Initialize with current time and skip (permissions already synced)
        3. If subsequent run: Fetch audit logs since last sync
        4. Extract unique content titles from audit records
        5. Search content by titles and check if exists in DB
        6. For each existing content: Fetch current permissions and update
        7. Update audit log sync point with current timestamp

        Note: First run initializes checkpoint but skips permission sync because
        the initial content sync (_sync_content) already synced all permissions.
        """
        try:
            self.logger.info("🔍 Starting permission sync from audit log...")

            # Sync point key for audit log
            audit_sync_key = self._audit_sync_key()

            # Get last audit sync timestamp
            last_audit_sync = await self.audit_log_sync_point.read_sync_point(audit_sync_key)
            last_sync_time_ms = last_audit_sync.get("last_sync_time_ms") if last_audit_sync else None

            # Current time as checkpoint
            current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            # First run: Initialize checkpoint and skip (permissions already synced during content sync)
            if last_sync_time_ms is None:
                self.logger.info(
                    "🆕 First audit log sync - initializing checkpoint to current time and skipping. "
                    "Permissions already synced during content sync."
                )

                # Save initial checkpoint
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            # Events can reach the log a little late; reading them twice is harmless.
            window_start_ms = last_sync_time_ms - self._AUDIT_OVERLAP_MS
            self.logger.info(f"🔄 Fetching audit logs from {window_start_ms} to {current_time_ms}")

            # Fetch audit logs and extract content titles that had permission changes
            content_titles = await self._fetch_permission_audit_logs(window_start_ms, current_time_ms)

            if content_titles is None:
                if spaces is not None:
                    # Find restriction changes from the listings instead, so they still apply, also when
                    # the audit log keeps failing. Only a refusal moves the window: other failures read it again.
                    if await self._recheck_restrictions(spaces) and self._audit_log_refused:
                        await self.audit_log_sync_point.update_sync_point(
                            audit_sync_key, {"last_sync_time_ms": current_time_ms}
                        )
                    return
                self.logger.warning(
                    "Keeping the audit log checkpoint: the audit log could not be read in full, "
                    "so the next sync reads this window again"
                )
                return

            if not content_titles:
                self.logger.info("✅ No permission changes found in audit log")
                # Update sync point even if no changes
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            self.logger.info(f"📋 Found {len(content_titles)} content items with permission changes")

            # Search for content by titles and sync their permissions (only if exists in DB)
            try:
                await self._sync_content_permissions_by_titles(content_titles)
            except AtlassianQuotaExhausted:
                raise
            except Exception as e:
                self.logger.warning(f"Keeping the audit log window: some restriction changes could not be applied: {e}")
                self._note_problem(
                    "Couldn't apply some restriction changes from the audit log; the next sync tries again"
                )
                return

            # Update audit log sync point with current time
            await self.audit_log_sync_point.update_sync_point(
                audit_sync_key,
                {"last_sync_time_ms": current_time_ms}
            )

            self.logger.info("✅ Permission sync from audit log completed")

        except Exception as e:
            self.logger.error(f"❌ Permission sync from audit log failed: {e}", exc_info=True)
            raise

    # Overlap of each audit log read with the last one.
    _AUDIT_OVERLAP_MS = 15 * 60 * 1000

    async def _fetch_permission_audit_logs(
        self,
        start_date_ms: int,
        end_date_ms: int
    ) -> Optional[list[str]]:
        """
        Fetch audit logs and extract content titles that had permission changes.

        Filters for:
        - category = "Permissions"
        - scope = content (pages/blogs, not global or space-level)

        Args:
            start_date_ms: Start timestamp in milliseconds
            end_date_ms: End timestamp in milliseconds

        Returns:
            List of unique content titles (pages/blogs) that had permission changes, or None
            when a page of the audit log could not be read.
        """
        content_titles_set: set[str] = set()
        batch_size = 100
        start = 0
        self._audit_log_refused = None

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_audit_logs(
                start_date=start_date_ms,
                end_date=end_date_ms,
                start=start,
                limit=batch_size
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"⚠️ Failed to fetch audit logs: {response.status if response else 'No response'}")
                # Any 4xx but 429 repeats every run (no permission, a plan or site without the endpoint).
                refused = response is not None and 400 <= response.status < 500 and response.status != 429
                if refused:
                    self._audit_log_refused = response.status
                    problem = (
                        f"Couldn't read the Confluence audit log (HTTP {response.status}): it needs a Confluence "
                        "administrator account (and the read:audit-log:confluence scope for OAuth). Instead, each "
                        "sync re-reads every page's read restriction (one request per 100 pages in a space)"
                    )
                else:
                    problem = (
                        "Couldn't read the Confluence audit log (HTTP %s); restriction changes are read again "
                        "next sync" % (response.status if response else "no response")
                    )
                self._note_problem(problem, unread=False, persistent=refused)
                return None

            response_data = response.json()
            audit_records = response_data.get("results", [])

            if not audit_records:
                break

            # Process each audit record
            for record in audit_records:
                content_title = self._extract_content_title_from_audit_record(record)
                if content_title:
                    content_titles_set.add(content_title)

            try:
                next_start = v1_next_start(response_data, start, len(audit_records), batch_size, use_link_offset=True)
            except ValueError as e:
                self.logger.warning(f"⚠️ Failed to follow the audit log: {e}")
                self._note_problem(
                    "Couldn't follow the Confluence audit log to its next page; restriction changes are read again next sync",
                    unread=False,
                )
                return None
            if next_start is None:
                break
            start = next_start

        return list(content_titles_set)

    async def _recheck_restrictions(self, spaces: list[RecordGroup]) -> bool:
        """Sync again the stored pages, blog posts and folders whose own read restriction changed.

        For when the audit log is refused. Placeholder ancestors and folders only get their
        access read again; in-scope pages and blog posts are synced again. One listing per space and type carries each
        item's read restriction. An item is synced again when that restriction differs
        from the one the previous check saw, or when it has none and is stored as
        restricted; the first check therefore syncs every restricted item once. Returns
        False when a listing, the stored records or a sync could not be read in full.
        """
        complete = True
        for space in spaces:
            for record_type in (RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST, RecordType.FILE):
                listed = await self._list_read_restrictions(space.short_name, record_type)
                if listed is None:
                    complete = False
                    continue
                try:
                    stored = await self._stored_content(
                        space.external_group_id, record_type, with_placeholders=True
                    )
                    if record_type == RecordType.FILE:
                        stored = [r for r in stored if _is_folder(r)]
                except Exception as e:
                    self.logger.warning(f"Could not read the stored records of space {space.short_name}: {e}")
                    complete = False
                    continue
                key = generate_record_sync_point_key(
                    RecordType.WEBPAGE.value, "restrictions", f"{record_type.value}/{space.short_name}"
                )
                seen = await self.audit_log_sync_point.read_sync_point(key)
                before: dict[str, str] = json.loads(seen.get("restricted") or "{}") if seen else {}
                stored_rule = {r.external_record_id: r.access_rule for r in stored}
                changed = sorted(
                    item_id for item_id, fingerprint in listed.items()
                    if item_id in stored_rule and (
                        fingerprint != before.get(item_id) if fingerprint
                        else stored_rule[item_id] == AccessRule.RESTRICTED
                    )
                )
                by_id = {r.external_record_id: r for r in stored}
                access_only = [i for i in changed if record_type == RecordType.FILE or by_id[i].is_placeholder]
                failed = await self._refresh_access_by_ids(space.short_name, record_type, access_only, by_id)
                changed_content = [i for i in changed if i not in set(access_only)]
                for start in range(0, len(changed_content), ID_SEARCH_CHUNK):
                    chunk = changed_content[start:start + ID_SEARCH_CHUNK]
                    result = await self._sync_content(
                        space.short_name, record_type, defer_checkpoint=True, within_ids=chunk,
                    )
                    # One the search did not return (its index behind) is checked again next sync.
                    failed.update(chunk if not result.complete else set(chunk) - set(result.seen))
                if changed:
                    self.logger.info(
                        f"Synced {len(changed)} {record_type.value} items in space {space.short_name} again: "
                        "their read restriction changed"
                    )
                complete = complete and not failed
                # A failed item keeps the restriction last seen, so the next check retries it.
                restricted = {
                    item_id: (before.get(item_id) if item_id in failed else fingerprint)
                    for item_id, fingerprint in listed.items()
                }
                await self.audit_log_sync_point.update_sync_point(
                    key, {"restricted": json.dumps({k: v for k, v in restricted.items() if v}, sort_keys=True)}
                )
        if not complete:
            self._note_problem("Couldn't re-check every page's restrictions; the next sync tries again")
        return complete

    async def _with_current_access(
        self, item_data: dict[str, Any], record_type: RecordType, existing_record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """A stored page, blog post or folder with its access read again.

        None when the item could not be read into a record; raises when its restrictions could not be read.
        """
        item_id = str(item_data.get("id"))
        # Transform to WebpageRecord (a folder is a FileRecord)
        record = (
            self._transform_to_folder_file_record(item_data, existing_record)
            if record_type == RecordType.FILE
            else self._transform_to_webpage_record(item_data, record_type, existing_record)
        )
        if not record:
            return None
        # A stub of an item the filters leave out stays a stub: only its access changes.
        record.is_placeholder = bool(getattr(existing_record, "is_placeholder", False))
        permissions = await self._fetch_page_permissions(item_id)
        if permissions is None:
            raise ValueError(f"Restrictions for {item_id} could not be read; keeping what is stored")
        # A READ restriction keeps inheriting and is recorded as RESTRICTED instead; EDIT-only stays STRICT.
        record.access_rule = (
            AccessRule.RESTRICTED if any(p.type == PermissionType.READ for p in permissions) else AccessRule.STRICT
        )
        record.rewrite_permissions = True
        return record, permissions

    async def _save_with_access(self, records_with_permissions: list[tuple[Record, list[Permission]]]) -> None:
        """Save records whose access was read again, and pass it to their stored files and comments."""
        await self.data_entities_processor.on_new_records(records_with_permissions)
        self.logger.info(f"Updated permissions for {len(records_with_permissions)} content items")
        await self._drop_legacy_comment_records(self._saved_as_blocks(r for r, _ in records_with_permissions))
        # Their stored files and comments would otherwise keep the page's old access.
        for record, permissions in records_with_permissions:
            await apply_page_access_to_dependents(
                self.data_entities_processor,
                self.connector_id,
                record.external_record_id,
                permissions,
                inherits_space=record.inherit_permissions,
            )

    async def _refresh_access_by_ids(
        self, space_key: str, record_type: RecordType, ids: list[str], stored: dict[str, Record]
    ) -> set[str]:
        """Read again the access of stored folders and placeholder ancestors; returns the ids that failed.

        Their content is not synced: a folder has none, and a placeholder stays one.
        """
        datasource = await self._get_fresh_datasource()
        failed = set(ids)
        for start in range(0, len(ids), ID_SEARCH_CHUNK):
            chunk = ids[start:start + ID_SEARCH_CHUNK]
            if record_type == RecordType.FILE:
                listing, by_ids = datasource.get_folders_v1, {"folder_ids": chunk, "folder_ids_operator": "in"}
            elif record_type == RecordType.CONFLUENCE_PAGE:
                listing, by_ids = datasource.get_pages_v1, {"within_ids": chunk}
            else:
                listing, by_ids = datasource.get_blogposts_v1, {"within_ids": chunk}
            token: str | None = None
            followed: set[str] = set()
            while True:
                offset, cursor = self._split_pagination_token(token)
                try:
                    response = await listing(
                        space_key=space_key, expand="version,space,history.lastUpdated,ancestors",
                        start=offset, cursor=cursor, limit=ID_SEARCH_CHUNK, **by_ids,
                    )
                    data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
                except AtlassianQuotaExhausted:
                    raise
                except Exception as e:
                    self.logger.warning(f"Could not read items of space {space_key} to update their access: {e}")
                    break
                results = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results, list):
                    break
                batch: list[tuple[Record, list[Permission]]] = []
                for item in results:
                    item_id = str(item.get("id")) if isinstance(item, dict) else None
                    if item_id in stored:
                        try:
                            refreshed = await self._with_current_access(item, record_type, stored[item_id])
                        except AtlassianQuotaExhausted:
                            raise
                        except Exception as e:
                            self.logger.warning(f"Could not update the access of {item_id}: {e}")
                            continue
                        if refreshed is not None:
                            batch.append(refreshed)
                            failed.discard(item_id)
                if batch:
                    try:
                        await self._save_with_access(batch)
                    except Exception as e:
                        self.logger.warning(f"Could not save the access of items in space {space_key}: {e}")
                        failed.update(str(r.external_record_id) for r, _ in batch)
                next_url = (data.get("_links") or {}).get("next")
                token = self._extract_cursor_from_next_link(next_url) if next_url else None
                if not token or not results or token in followed:
                    break
                followed.add(token)
        return failed

    async def _list_read_restrictions(self, space_key: str, record_type: RecordType) -> dict[str, str | None] | None:
        """Read restriction fingerprint by id of every page, blog post or folder (FILE) in a space; None unless read to the end."""
        datasource = await self._get_fresh_datasource()
        listing = {
            RecordType.CONFLUENCE_PAGE: datasource.get_pages_v1,
            RecordType.CONFLUENCE_BLOGPOST: datasource.get_blogposts_v1,
            RecordType.FILE: datasource.get_folders_v1,
        }[record_type]
        found: dict[str, str | None] = {}
        token: str | None = None
        followed: set[str] = set()
        while True:
            offset, cursor = self._split_pagination_token(token)
            try:
                response = await listing(
                    space_key=space_key, expand=RESTRICTION_EXPAND_PARAMS, start=offset, cursor=cursor,
                    limit=RESTRICTION_LISTING_LIMIT,
                )
                data = response.json() if response and response.status == HttpStatusCode.SUCCESS.value else None
            except AtlassianQuotaExhausted:
                raise
            except Exception as e:
                self.logger.warning(f"Could not list the restrictions in space {space_key}: {e}")
                return None
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                self.logger.warning(f"Could not list the restrictions in space {space_key}")
                return None
            for item in results:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                if "read" not in (item.get("restrictions") or {}):
                    self.logger.warning(f"The listing of space {space_key} did not carry read restrictions")
                    return None
                found[str(item["id"])] = read_restriction_fingerprint(item)
            next_url = (data.get("_links") or {}).get("next")
            if not next_url:
                return found
            token = self._extract_cursor_from_next_link(next_url)
            if not results or not token or token in followed:
                self.logger.warning(f"Can't follow the restriction listing of space {space_key}")
                return None
            followed.add(token)

    def _extract_content_title_from_audit_record(self, record: dict[str, Any]) -> str | None:
        """
        Extract content title from an audit record if it's a content permission change.

        Filters for:
        - category = "Permissions"
        - Has a Page, Blog or Folder in associatedObjects (content-level permission)
        - Has a Space in associatedObjects (confirms it's content, not global)

        Args:
            record: Raw audit log record

        Returns:
            Content title (page/blog) or None if not a content permission change
        """
        # Must be a permission-related event
        if record.get("category") != "Permissions":
            return None

        associated_objects = record.get("associatedObjects", [])

        # Check for content-level permission (must have both content AND space)
        has_space = any(obj.get("objectType") == "Space" for obj in associated_objects)
        content_obj = next(
            # shortcut: "Folder" is assumed from the page and blog names; no real folder entry was seen yet.
            (obj for obj in associated_objects if obj.get("objectType") in ["Page", "Blog", "Folder"]),
            None
        )

        # Content restriction must have both page/blog AND space
        if not has_space or not content_obj:
            return None

        return content_obj.get("name")


    async def _sync_content_permissions_by_titles(self, titles: list[str]) -> None:
        """
        Search for content by titles and sync their current permissions.

        IMPORTANT: This method ONLY updates permissions for records that already exist in the database.
        It will NOT create new records, ensuring sync filters are respected.

        For each found content item:
        1. Check if record exists in DB (by external_record_id)
        2. If exists: Fetch current permissions and update
        3. If not exists: Skip (record was filtered out during initial sync)

        Args:
            titles: List of content titles to search for
        """
        if not titles:
            return

        # Batch titles to avoid CQL query size limits (process 50 at a time)
        batch_size = 50
        total_synced = 0
        total_skipped = 0
        total_permissions = 0
        has_failures = False

        for i in range(0, len(titles), batch_size):
            batch_titles = titles[i:i + batch_size]

            try:
                # One shared title can fill a page, so the search is read to its last page.
                pagination_token: Optional[str] = None
                followed: set[str] = set()
                while True:
                    datasource = await self._get_fresh_datasource()
                    start_offset, cursor_token = self._split_pagination_token(pagination_token)
                    response = await datasource.search_content_by_titles(
                        titles=batch_titles,
                        expand="version,space,history.lastUpdated,ancestors",
                        start=start_offset,
                        cursor=cursor_token,
                    )

                    if not response or response.status != HttpStatusCode.SUCCESS.value:
                        self.logger.warning(f"⚠️ Failed to search content by titles: {response.status if response else 'No response'}")
                        has_failures = True
                        break

                    response_data = response.json()
                    content_items = response_data.get("results", [])

                    if not content_items:
                        self.logger.debug(f"No content found for titles batch {i // batch_size + 1}")
                        break

                    # Process each content item
                    records_with_permissions = []
                    for item_data in content_items:
                        try:
                            item_id = item_data.get("id")
                            item_title = item_data.get("title")
                            item_type = item_data.get("type", "").lower()

                            if not item_id or not item_title:
                                continue

                            # Determine record type
                            if item_type == "page":
                                record_type = RecordType.CONFLUENCE_PAGE
                            elif item_type == "blogpost":
                                record_type = RecordType.CONFLUENCE_BLOGPOST
                            elif item_type == "folder":
                                record_type = RecordType.FILE
                            else:
                                self.logger.debug(f"Skipping unknown content type: {item_type}")
                                continue

                            # Check if record exists in database (respects sync filters)
                            existing_record = await self.data_entities_processor.get_record_by_external_id(
                                connector_id=self.connector_id,
                                external_record_id=item_id
                            )

                            if not existing_record:
                                # Record doesn't exist - it was filtered out during initial sync
                                self.logger.debug(
                                    f"Skipping {item_type} '{item_title}' ({item_id}) - "
                                    f"not in database (filtered out during sync)"
                                )
                                total_skipped += 1
                                continue

                            self.logger.debug(f"Updating permissions for {item_type}: {item_title} ({item_id})")
                            refreshed = await self._with_current_access(item_data, record_type, existing_record)
                            if refreshed is None:
                                continue
                            total_permissions += len(refreshed[1])
                            records_with_permissions.append(refreshed)
                            total_synced += 1

                        except Exception as item_error:
                            self.logger.error(f"❌ Failed to sync permissions for {item_data.get('title')}: {item_error}")
                            has_failures = True
                            continue

                    # Update batch in database
                    if records_with_permissions:
                        await self._save_with_access(records_with_permissions)

                    next_url = response_data.get("_links", {}).get("next")
                    if not next_url:
                        break
                    pagination_token = self._extract_cursor_from_next_link(next_url)
                    if not pagination_token or pagination_token in followed:
                        self.logger.warning(f"⚠️ Title search has a next page that could not be followed: {next_url}")
                        has_failures = True
                        break
                    followed.add(pagination_token)

            except Exception as batch_error:
                self.logger.error(f"❌ Failed to process titles batch: {batch_error}")
                has_failures = True
                continue

        if has_failures:
            raise ValueError("Failed to sync permissions for some content items")

        if total_skipped > 0:
            self.logger.info(f"🔍 Skipped {total_skipped} items not in database (filtered during sync)")

        self.logger.info(f"✅ Permission sync complete. Items updated: {total_synced}, Permissions: {total_permissions}")

    async def _fetch_space_permissions(self, space_id: str, space_name: str) -> Optional[list[Permission]]:
        """
        Fetch all permissions for a space with cursor-based pagination.

        Args:
            space_id: The space ID
            space_name: The space name (for logging)

        Returns:
            List of Permission objects, or None when they could not be read in full.
        """
        try:
            permissions = []
            batch_size = 100
            cursor = None
            followed: set[str] = set()

            # Paginate through space permissions
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_space_permissions_assignments(
                    id=space_id,
                    limit=batch_size,
                    cursor=cursor
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to fetch permissions for space {space_name}: {response.status if response else 'No response'}")
                    return None

                response_data = response.json()
                permissions_data = response_data.get("results", [])
                next_url = (response_data.get("_links") or {}).get("next")

                if not permissions_data:
                    if next_url:
                        # Saving a list read only in part would take the rest of the grants away.
                        self.logger.warning(f"⚠️ Permissions of space {space_name} stopped early")
                        return None
                    break

                # Transform permissions and add to list
                for perm_data in permissions_data:
                    if self._access_class_id(perm_data):
                        permissions.extend(await self._access_class_permissions(perm_data, space_name))
                        continue
                    permission = await self._transform_space_permission(perm_data)
                    if permission:
                        permissions.append(permission)

                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor or cursor in followed:
                    self.logger.warning(f"⚠️ Couldn't follow the permissions of space {space_name}: {next_url}")
                    return None
                followed.add(cursor)

            deduped = self._dedupe_space_permissions(permissions)
            self.logger.debug(
                "Space %s: %s permissions after dedupe (from %s API rows)",
                space_name,
                len(deduped),
                len(permissions),
            )
            return deduped

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch permissions for space {space_name}: {e}")
            return None

    # Users and groups per operation in one restriction response (Cloud's default and our page size).
    _RESTRICTION_PAGE = 100
    # Runaway backstop: 100 pages is 10,000 users and 10,000 groups on one restriction.
    _RESTRICTION_MAX_PAGES = 100

    async def _read_all_restrictions(self, page_id: str) -> Optional[list[dict[str, Any]]]:
        """Every user and group of each of a content item's restrictions; None when it can't be read.

        Cloud returns at most ``limit`` users and ``limit`` groups per operation in one
        response, so a longer list is read page by page and merged per operation.
        """
        merged: dict[str, dict[str, Any]] = {}
        seen: set[tuple[str, str, str]] = set()
        # How many users / groups each operation has in all, when Confluence says (totalSize).
        expected: dict[tuple[str, str], int] = {}
        start = 0
        for _ in range(self._RESTRICTION_MAX_PAGES):
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_page_permissions_v1(
                page_id=page_id, start=start, limit=self._RESTRICTION_PAGE
            )
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"⚠️ Failed to fetch permissions for page {page_id}: {response.status if response else 'No response'}")
                return None
            more = False
            new = False
            step = self._RESTRICTION_PAGE
            for restriction in response.json().get("results", []) or []:
                operation = restriction.get("operation")
                target = merged.setdefault(operation, {
                    "operation": operation,
                    "restrictions": {"user": {"results": []}, "group": {"results": []}},
                })
                for kind in ("user", "group"):
                    listing = (restriction.get("restrictions") or {}).get(kind) or {}
                    entries = listing.get("results") or []
                    if isinstance(listing.get("totalSize"), int):
                        expected[(str(operation), kind)] = listing["totalSize"]
                    for entry in entries:
                        key = (str(operation), kind, str(entry.get("accountId") or entry.get("id") or entry.get("name")))
                        if key not in seen:
                            seen.add(key)
                            new = True
                            target["restrictions"][kind]["results"].append(entry)
                    # The page size Confluence used, which a system limit may set below ours.
                    page_size = listing.get("limit") if isinstance(listing.get("limit"), int) else self._RESTRICTION_PAGE
                    more = more or (page_size > 0 and len(entries) >= page_size)
                    if 0 < page_size < step:
                        step = page_size
            # The next page starts after what this one could hold, at Confluence's page size.
            start += step
            # A full page may be followed by more; a page that adds nothing new ends it.
            if not more or not new:
                read = {
                    (str(op), kind): len(data["restrictions"][kind]["results"])
                    for op, data in merged.items() for kind in ("user", "group")
                }
                if any(read.get(key, 0) < total for key, total in expected.items()):
                    self.logger.warning(f"⚠️ Restrictions of {page_id} could not be read in full")
                    return None
                return list(merged.values())
        self.logger.warning(f"⚠️ Restrictions of {page_id} have more than {self._RESTRICTION_MAX_PAGES} pages")
        return None

    async def _fetch_page_permissions(self, page_id: str) -> Optional[list[Permission]]:
        """
        Fetch permissions for a Confluence page using v1 API.

        Args:
            page_id: The page ID

        Returns:
            List of Permission objects, or None when the restrictions could not be
            read (callers must not treat that as unrestricted).
        """
        permissions = []

        try:
            self.logger.debug(f"Fetching permissions for page: {page_id}")

            restrictions = await self._read_all_restrictions(page_id)
            if restrictions is None:
                return None

            # Process each restriction (read and update operations)
            for restriction_data in restrictions:
                operation_permissions = await self._transform_page_restriction_to_permissions(restriction_data)
                permissions.extend(operation_permissions)

            self.logger.debug(f"Found {len(permissions)} permissions for page {page_id}")
            return permissions

        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch permissions for page {page_id}: {e}")
            return None

    def _construct_web_url(self, links: dict[str, Any], base_url: str | None = None) -> str | None:
        """
        Construct web URL from _links dictionary.

        Supports both v1 and v2 API response formats:
        - v2 API: base_url is at response level (passed as parameter)
        - v1 API: base_url needs to be extracted from _links.self

        Args:
            links: The _links dictionary from API response
            base_url: Optional base URL from response level (v2 API)

        Returns:
            Constructed web URL or None if not possible
        """
        web_path = links.get("webui")
        if not web_path:
            return None

        # Use base_url from parameter (v2 API - from response level)
        if base_url:
            return f"{base_url}{web_path}"

        # Fall back to v1 format (extract from _links.self)
        extracted_base_url = self._wiki_base_from_self_link(links.get("self"))
        if extracted_base_url and extracted_base_url.startswith("https://"):
            return f"{extracted_base_url}{web_path}"

        return None

    def _wiki_base_from_self_link(self, self_link: str | None) -> str | None:
        """The site's wiki URL for a v1 item's self link.

        Through OAuth the self link is the API gateway (api.atlassian.com/ex/confluence/...),
        not the site, so the site URL a listing reported (``_links.base``) is used instead.
        """
        if not self_link or "/wiki/" not in self_link:
            return None
        if urlparse(self_link).hostname == "api.atlassian.com":
            return self._site_wiki_url
        return self_link.split("/wiki/")[0] + "/wiki"

    async def _remember_configured_site(self) -> None:
        """Start from the site URL in the settings until a listing reports it in ``_links.base``."""
        try:
            config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")
            auth = config.get("auth") if isinstance(config, dict) else None
            site = (auth.get("baseUrl") if isinstance(auth, dict) else None) or ""
        except Exception:
            return
        site = str(site).strip().rstrip("/")
        if site.startswith("http") and not self._site_wiki_url:
            self._site_wiki_url = site if site.endswith("/wiki") else f"{site}/wiki"

    def _remember_site_wiki_url(self, response_data: object) -> None:
        """Keep the site's wiki URL a response reports in ``_links.base``, for links built later."""
        base = ((response_data or {}).get("_links") or {}).get("base") if isinstance(response_data, dict) else None
        if isinstance(base, str) and base.startswith("http") and urlparse(base).hostname != "api.atlassian.com":
            self._site_wiki_url = base.rstrip("/")

    @staticmethod
    def _split_pagination_token(token: Optional[str]) -> tuple[Optional[int], Optional[str]]:
        """
        Split a pagination token into offset (int) or cursor (str) components.
        
        Handles both offset-based pagination (Data Center, Cloud v1) and 
        cursor-based pagination (Cloud v2).
        
        Args:
            token: Pagination token from _links.next (could be "123" or "base64cursor")
            
        Returns:
            Tuple of (start_offset, cursor_token) where one is None
        """
        if not token:
            return None, None
        try:
            return int(token), None
        except (TypeError, ValueError):
            return None, token

    def _extract_cursor_from_next_link(self, next_url: str) -> Optional[str]:
        """
        Extract pagination token from _links.next URL query string.

        v2 APIs use ``cursor``; v1 ``/rest/api/content/search`` uses ``start`` (offset).
        Prefer ``cursor`` when present so v2 callers behave unchanged.
        """
        try:
            if not next_url:
                return None

            parsed = urlparse(next_url)
            query_params = parse_qs(parsed.query)

            cursor_values = query_params.get("cursor", [])
            if cursor_values:
                return cursor_values[0]

            start_values = query_params.get("start", [])
            if start_values:
                return start_values[0]

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to extract cursor from URL '{next_url}': {e}")
            return None

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType,
        create_pseudo_group_if_missing: bool = False
    ) -> Permission | None:
        """
        Create Permission object from principal data (user or group).

        This is a common function used by both space and page permission processing.

        Args:
            principal_type: "user" or "group"
            principal_id: accountId for users, groupId for groups
            permission_type: Mapped PermissionType enum
            create_pseudo_group_if_missing: If True and user not found, create a
                pseudo-group to preserve the permission. Used for record-level

        Returns:
            Permission object or None if principal not found in DB
        """
        try:
            if principal_type == "user":
                entity_type = EntityType.USER
                # Lookup user by source_user_id (accountId)
                user = await self.data_entities_processor.get_user_by_source_id(
                    source_user_id=principal_id,
                    connector_id=self.connector_id,
                )
                if user:
                    return Permission(
                        email=user.email,
                        type=permission_type,
                        entity_type=entity_type
                    )

                # User not found - check if pseudo-group exists or should be created
                if create_pseudo_group_if_missing:
                    # Check for existing pseudo-group
                    pseudo_group = await self.data_entities_processor.get_user_group_by_external_id(
                        connector_id=self.connector_id,
                        external_id=principal_id,
                    )

                    if not pseudo_group:
                        # Create pseudo-group on-the-fly
                        pseudo_group = await self._create_pseudo_group(principal_id)

                    if pseudo_group:
                        self.logger.debug(
                            f"Using pseudo-group for user {principal_id} (no email available)"
                        )
                        return Permission(
                            external_id=pseudo_group.source_user_group_id,
                            type=permission_type,
                            entity_type=EntityType.GROUP
                        )

                self.logger.debug(f"  ⚠️ User {principal_id} not found in DB, skipping permission")
                return None

            elif principal_type == "group":
                entity_type = EntityType.GROUP
                # Lookup group by source_user_group_id
                group = await self.data_entities_processor.get_user_group_by_external_id(
                    connector_id=self.connector_id,
                    external_id=principal_id,
                )
                if not group:
                    self.logger.debug(f"  ⚠️ Group {principal_id} not found in DB, skipping permission")
                    return None

                return Permission(
                    external_id=group.source_user_group_id,
                    type=permission_type,
                    entity_type=entity_type
                )

            return None

        except Exception as e:
            # A failed lookup is not "not found": callers keep what is stored instead.
            self.logger.error(f"❌ Failed to create permission from principal: {e}")
            raise

    async def _create_pseudo_group(self, account_id: str) -> AppUserGroup | None:
        """
        Create a pseudo-group for a user without email.

        This preserves permissions for users who don't have email addresses yet.
        The pseudo-group uses the user's accountId as source_user_group_id.

        Args:
            account_id: Confluence user accountId

        Returns:
            Created AppUserGroup or None if creation fails
        """
        try:
            pseudo_group = AppUserGroup(
                app_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                source_user_group_id=account_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX} {account_id}",
                org_id=self.data_entities_processor.org_id,
            )

            # Save to database (empty members list)
            await self.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            self.logger.info(f"Created pseudo-group for user without email: {account_id}")

            return pseudo_group

        except Exception as e:
            self.logger.error(f"Failed to create pseudo-group for {account_id}: {e}")
            return None

    async def _ensure_group_name_cache(self) -> dict[str, str]:
        """Build name -> external group id map from synced Confluence groups."""
        if self._group_name_to_external_id is not None:
            return self._group_name_to_external_id

        name_map: dict[str, str] = {}
        # Raised, not read as no groups: every access-class grant would map to nothing for the whole sync.
        groups = await self.data_entities_processor.get_all_user_groups(
            connector_id=self.connector_id, raise_on_error=True,
        )
        for group in groups:
            if group.name and group.source_user_group_id:
                name_map[group.name.lower()] = group.source_user_group_id

        self._group_name_to_external_id = name_map
        self.logger.debug(
            "Loaded %s Confluence group name(s) for access-class resolution",
            len(name_map),
        )
        return name_map

    @staticmethod
    def _access_class_id(perm_data: dict[str, Any]) -> str | None:
        """The access class a space permission row grants, normalised (e.g. ALL_LICENSED_USERS); None for a user or group."""
        principal = perm_data.get("principal") or {}
        kind = str(principal.get("type") or "").lower().replace("_", "-")
        if kind != "access-class" or not principal.get("id"):
            return None
        return str(principal["id"]).upper().replace("-", "_")

    async def _groups_matching(self, names: tuple[str, ...], prefixes: tuple[str, ...]) -> list[str]:
        """External ids of every synced group named one of ``names`` or starting with one of ``prefixes``."""
        name_map = await self._ensure_group_name_cache()
        wanted = {n.lower() for n in names}
        return sorted({
            external_id for name, external_id in name_map.items()
            if name in wanted or any(name.startswith(prefix) for prefix in prefixes)
        })

    async def _access_class_permissions(
        self, perm_data: dict[str, Any], space_name: str = ""
    ) -> list[Permission]:
        """Map an access-class space grant to the synced groups it stands for.

        Never to the whole PipesHub org, except anonymous access (a public space), which
        is open to everyone. A class with no synced group grants nothing and is reported.
        """
        access_class = self._access_class_id(perm_data)
        operation = perm_data.get("operation") or {}
        operation_key, target_type = operation.get("key"), operation.get("targetType")
        if target_type != "space":
            return []
        if access_class == ACCESS_CLASS_ANONYMOUS_USERS and operation_key == "read":
            return [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        if access_class in (ACCESS_CLASS_LICENSED_USERS, ACCESS_CLASS_AUTHENTICATED_USERS) and operation_key == "read":
            names, prefixes, permission_type = LICENSED_USER_GROUP_NAMES, LICENSED_USER_GROUP_PREFIXES, PermissionType.READ
        elif access_class == ACCESS_CLASS_PRODUCT_ADMINS and operation_key == "administer":
            names, prefixes, permission_type = PRODUCT_ADMIN_GROUP_NAMES, PRODUCT_ADMIN_GROUP_PREFIXES, PermissionType.OWNER
        else:
            self.logger.debug("Skipping access-class %s for operation %s/%s", access_class, operation_key, target_type)
            return []
        group_ids = await self._groups_matching(names, prefixes)
        if not group_ids:
            self.logger.warning(f"Space {space_name}: no synced group stands for access class {access_class}; it grants nothing")
            self._note_problem(
                f"Space {space_name}: its {access_class.lower().replace('_', ' ')} grant matches no synced Confluence "
                "group, so it gives no one access in PipesHub",
                unread=False,
                persistent=True,
            )
            return []
        return [Permission(entity_type=EntityType.GROUP, external_id=g, type=permission_type) for g in group_ids]

    @staticmethod
    def _dedupe_space_permissions(permissions: list[Permission]) -> list[Permission]:
        """Collapse duplicate space permission grants (common with access-class ACLs)."""
        seen: set[tuple[Any, ...]] = set()
        deduped: list[Permission] = []
        for permission in permissions:
            key = (
                permission.entity_type,
                permission.external_id or "",
                permission.email or "",
                permission.type,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(permission)
        return deduped

    async def _transform_space_permission(self, perm_data: dict[str, Any]) -> Optional[Permission]:
        """
        Transform Confluence space permission to Permission object.

        Maps Confluence operations to PermissionType:
        - administer → OWNER
        - read → READ
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - delete (space) → OWNER

        Args:
            perm_data: Raw permission data from Confluence API

        Returns:
            Permission object or None if invalid or user/group not found in DB
        """
        try:
            principal = perm_data.get("principal", {})
            operation = perm_data.get("operation", {})

            principal_type = principal.get("type")  # "user" or "group"
            principal_id = principal.get("id")  # accountId or groupId
            operation_key = operation.get("key")  # e.g., "read", "administer"
            target_type = operation.get("targetType")  # e.g., "space", "page"

            if not principal_type or not principal_id or not operation_key:
                return None

            if self._access_class_id(perm_data):
                # Callers that keep every grant use _access_class_permissions; this keeps the first.
                granted = await self._access_class_permissions(perm_data)
                return granted[0] if granted else None

            # Map Confluence permission to PermissionType
            permission_type = self._map_confluence_permission(operation_key, target_type)

            # Use common function to create permission
            # A user whose email is hidden keeps their space access through a pseudo-group, as on pages.
            return await self._create_permission_from_principal(
                principal_type,
                principal_id,
                permission_type,
                create_pseudo_group_if_missing=True,
            )

        except Exception as e:
            # Raised, not dropped: saving the space without this grant would take it away.
            self.logger.error(f"❌ Failed to transform space permission: {e}")
            raise

    def _map_confluence_permission(self, operation_key: str, target_type: str) -> PermissionType:
        """
        Map Confluence operation to PermissionType enum.

        Mapping logic:
        - administer → OWNER
        - read → READ
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - delete (space) → OWNER
        - any other operation → READ (default)

        Args:
            operation_key: Operation key (e.g., "read", "create", "delete")
            target_type: Target type (e.g., "space", "page")

        Returns:
            PermissionType enum value
        """
        # Administer = OWNER
        if operation_key == "administer":
            return PermissionType.OWNER

        # Read = READ
        if operation_key == "read":
            return PermissionType.READ

        # Delete space = OWNER
        if operation_key == "delete" and target_type == "space":
            return PermissionType.OWNER

        # Page/blogpost/attachment operations = WRITE
        if (
            target_type in ["page", "blogpost", "attachment"]
            and operation_key in ["create", "delete", "archive"]
        ):
            return PermissionType.WRITE

        # Unrecognized operations default to READ
        return PermissionType.READ

    def _map_page_permission(self, operation: str) -> PermissionType:
        """
        Map page restriction operation to PermissionType enum.

        Page restrictions only have two operations:
        - read → READ
        - update → WRITE
        Any other value defaults to READ.

        Args:
            operation: Operation string ("read" or "update")

        Returns:
            PermissionType enum value
        """
        if operation == "read":
            return PermissionType.READ
        elif operation == "update":
            return PermissionType.WRITE
        else:
            return PermissionType.READ

    async def _transform_page_restriction_to_permissions(
        self,
        restriction_data: dict[str, Any]
    ) -> list[Permission]:
        """
        Transform page restriction data (from v1 API) to Permission objects.
        Creates pseudo-groups for users without email to preserve permissions.

        The v1 API returns restrictions in this format:
        {
            "operation": "read" | "update",
            "restrictions": {
                "user": {
                    "results": [{"type": "known", "accountId": "...", "displayName": "..."}]
                },
                "group": {
                    "results": [{"type": "group", "name": "...", "id": "..."}]
                }
            }
        }

        Args:
            restriction_data: Single restriction object with operation and restrictions

        Returns:
            List of Permission objects
        """
        permissions = []

        try:
            operation = restriction_data.get("operation")
            if operation != "read":
                return permissions

            # Map operation to PermissionType
            permission_type = self._map_page_permission(operation)

            restrictions = restriction_data.get("restrictions", {})

            # Process user restrictions - create pseudo-group if user not found
            user_restrictions = restrictions.get("user", {})
            user_results = user_restrictions.get("results", [])

            for user_data in user_results:
                # Extract accountId (could be under different keys)
                principal_id = user_data.get("accountId") or user_data.get("id")
                if not principal_id:
                    # Dropping it could leave the restriction with no grants, which reads as open.
                    permissions.append(unresolved_principal_permission("unidentified-user", permission_type))
                    continue
                if principal_id:
                    permission = await self._create_permission_from_principal(
                        "user",
                        principal_id,
                        permission_type,
                        create_pseudo_group_if_missing=True  # Enable pseudo-group creation for record-level permissions
                    )
                    permissions.append(permission or unresolved_principal_permission(principal_id, permission_type))

            # Process group restrictions
            group_restrictions = restrictions.get("group", {})
            group_results = group_restrictions.get("results", [])

            for group_data in group_results:
                principal_id = group_data.get("id")
                if not principal_id and group_data.get("name"):
                    principal_id = (await self._ensure_group_name_cache()).get(str(group_data["name"]).lower())
                if not principal_id:
                    permissions.append(unresolved_principal_permission("unidentified-group", permission_type))
                    continue
                if principal_id:
                    permission = await self._create_permission_from_principal(
                        "group",
                        principal_id,
                        permission_type,
                        create_pseudo_group_if_missing=False  # Groups don't need pseudo-groups
                    )
                    permissions.append(permission or unresolved_principal_permission(principal_id, permission_type))

        except Exception as e:
            self.logger.error(f"❌ Failed to transform page restriction: {e}")
            raise

        return permissions

    def _transform_to_space_record_group(
        self,
        space_data: dict[str, Any],
        base_url: str | None = None
    ) -> RecordGroup | None:
        """
        Transform Confluence space data to RecordGroup entity.

        Args:
            space_data: Raw space data from Confluence API
            base_url: Base URL from API response (_links.base)

        Returns:
            RecordGroup object or None if transformation fails
        """
        try:
            space_id = space_data.get("id")
            space_name = space_data.get("name")
            space_description = space_data.get("description", "")
            space_key = space_data.get("key", "")

            if not space_id or not space_name:
                return None

            # Parse timestamps
            source_created_at = None
            created_at_str = space_data.get("createdAt")
            if created_at_str:
                source_created_at = self._parse_confluence_datetime(created_at_str)

            # Construct web URL: base + webui
            web_url = None
            if base_url:
                webui = space_data.get("_links", {}).get("webui")
                if webui:
                    web_url = f"{base_url}{webui}"

            return RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=space_name,
                short_name=space_key,
                description=space_description,
                external_group_id=space_id,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                group_type=RecordGroupType.CONFLUENCE_SPACES,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_created_at,  # Confluence doesn't provide updated timestamp for spaces
                # Every space carries its own permission list, so app access
                # alone must never reveal one: it inherits from the App, but the
                # restriction means a grant is needed as well.
                # Inheritance is not optional: it defaults to False, and without
                # the edge the inherited half of the RESTRICTED rule is never
                # met, so the space becomes invisible to everyone.
                inherit_permissions=True,
                access_rule=AccessRule.RESTRICTED,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform space: {e}")
            return None

    def _transform_to_webpage_record(
        self,
        data: dict[str, Any],
        record_type: RecordType,
        existing_record: Record | None = None
    ) -> WebpageRecord | None:
        """
        Unified transform for page/blogpost data to WebpageRecord.

        Args:
            data: Raw data from Confluence API
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
            existing_record: Optional existing record to check for updates

        Returns:
            WebpageRecord object or None if transformation fails
        """
        # Derive content_type for logging
        if record_type == RecordType.CONFLUENCE_PAGE:
            content_type = "page"
        elif record_type == RecordType.CONFLUENCE_BLOGPOST:
            content_type = "blogpost"
        else:
            content_type = "content"

        try:
            item_id = data.get("id")
            item_title = data.get("title")

            if not item_id or not item_title:
                return None

            # Parse timestamps - v1 vs v2 have different structures
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version (version.createdAt, version.number)
            version_data = data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format (history.lastUpdated.when, history.lastUpdated.number)
            if not source_updated_at:
                history = data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    updated_when = last_updated.get("when")
                    if updated_when:
                        source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract space ID - v2 has spaceId at top level, v1 has space.id
            external_record_group_id = None
            space_id_v2 = data.get("spaceId")  # v2 format
            if space_id_v2:
                external_record_group_id = str(space_id_v2)
            else:
                # v1 format
                space_data = data.get("space", {})
                space_id = space_data.get("id")
                external_record_group_id = str(space_id) if space_id else None

            if not external_record_group_id:
                self.logger.warning(f"{content_type.capitalize()} {item_id} has no space - skipping")
                return None

            # Extract parent ID and type - v2 has parentId at top level, v1 uses ancestors
            parent_external_record_id = None
            parent_type_str = None
            parent_id_v2 = data.get("parentId")  # v2 format
            parent_type_v2 = data.get("parentType")  # v2 format includes parentType
            if parent_id_v2:
                parent_external_record_id = str(parent_id_v2)
                parent_type_str = parent_type_v2  # May be None if not provided
            else:
                # v1 format - last ancestor is direct parent
                ancestors = data.get("ancestors", [])
                if ancestors and len(ancestors) > 0:
                    direct_parent = ancestors[-1]
                    parent_external_record_id = direct_parent.get("id")
                    parent_type_str = direct_parent.get("type")  # May be None if not expanded

            # Construct web URL - v1 vs v2 have different link structures
            web_url = None
            links = data.get("_links", {})
            webui = links.get("webui")

            if webui:
                # Try v2 format first (_links.base)
                base_url = links.get("base")
                if base_url:
                    web_url = f"{base_url}{webui}"
                else:
                    # Fall back to v1 format (extract from _links.self)
                    base_url = self._wiki_base_from_self_link(links.get("self"))
                    if base_url:
                        web_url = f"{base_url}{webui}"

            # Set parent_record_type from parent's actual type when available
            # Maps Confluence type string to RecordType enum
            parent_record_type = None
            if parent_external_record_id:
                if parent_type_str == "folder":
                    parent_record_type = RecordType.FILE  # Folders are FILE type
                elif parent_type_str == "page":
                    parent_record_type = RecordType.CONFLUENCE_PAGE
                elif parent_type_str == "blogpost":
                    parent_record_type = RecordType.CONFLUENCE_BLOGPOST
                else:
                    # Fallback: assume parent is same type as current record for backwards compatibility
                    # This handles cases where type is not provided in ancestor data
                    parent_record_type = record_type

            # Determine record ID and version
            is_new = existing_record is None
            record_id = str(uuid.uuid4()) if is_new else existing_record.id

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            mime_type = (
                existing_record.mime_type
                if not is_new and isinstance(existing_record.mime_type, str) and existing_record.mime_type
                and str(version_number) == existing_record.external_revision_id
                else MimeTypes.BLOCKS.value
            )
            if (
                not is_new and isinstance(existing_record.mime_type, str)
                and existing_record.mime_type != MimeTypes.BLOCKS.value and mime_type == MimeTypes.BLOCKS.value
            ):
                self._now_blocks[item_id] = record_type

            return WebpageRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=item_title,
                record_type=record_type,
                external_record_id=item_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                external_record_group_id=external_record_group_id,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                weburl=web_url,
                # Blocks carry the page's comments. A stored HTML page keeps HTML until its content
                # changes (or it is re-indexed), so what is stored matches what was indexed.
                mime_type=mime_type,
                # The preview has no renderer for blocks; as for Jira, the page opens at its link.
                preview_renderable=False,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                is_dependent_node=False,  # Pages are root nodes
                parent_node_id=None,  # Pages have no parent node
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform {content_type}: {e}")
            return None


    def _transform_to_folder_file_record(
        self,
        data: dict[str, Any],
        existing_record: Optional[Record] = None
    ) -> Optional[FileRecord]:
        """
        Transform Confluence folder to FileRecord entity (isFile=False).
        
        Args:
            data: Raw folder data from Confluence API
            existing_record: Optional existing record to check for updates
        
        Returns:
            FileRecord object (folder) or None if transformation fails
        """
        try:
            folder_id = data.get("id")
            folder_title = data.get("title")

            if not folder_id or not folder_title:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version
            version_data = data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format
            if not source_updated_at:
                history = data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    updated_when = last_updated.get("when")
                    if updated_when:
                        source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract space ID
            external_record_group_id = None
            space_id_v2 = data.get("spaceId")
            if space_id_v2:
                external_record_group_id = str(space_id_v2)
            else:
                space_data = data.get("space", {})
                space_id = space_data.get("id")
                external_record_group_id = str(space_id) if space_id else None

            if not external_record_group_id:
                self.logger.warning(f"Folder {folder_id} has no space - skipping")
                return None

            # Extract parent ID and type
            parent_external_record_id = None
            parent_type_str = None
            parent_id_v2 = data.get("parentId")
            parent_type_v2 = data.get("parentType")
            if parent_id_v2:
                parent_external_record_id = str(parent_id_v2)
                parent_type_str = parent_type_v2
            else:
                ancestors = data.get("ancestors", [])
                if ancestors and len(ancestors) > 0:
                    direct_parent = ancestors[-1]
                    parent_external_record_id = direct_parent.get("id")
                    parent_type_str = direct_parent.get("type")

            # Construct web URL
            web_url = None
            links = data.get("_links", {})
            webui = links.get("webui")

            if webui:
                base_url = links.get("base")
                if base_url:
                    web_url = f"{base_url}{webui}"
                else:
                    base_url = self._wiki_base_from_self_link(links.get("self"))
                    if base_url:
                        web_url = f"{base_url}{webui}"

            # Set parent_record_type from parent's actual type
            parent_record_type = None
            if parent_external_record_id:
                if parent_type_str == "folder":
                    parent_record_type = RecordType.FILE  # Folders are FILE type
                elif parent_type_str == "page":
                    parent_record_type = RecordType.CONFLUENCE_PAGE
                elif parent_type_str == "blogpost":
                    parent_record_type = RecordType.CONFLUENCE_BLOGPOST
                else:
                    # Fallback: assume parent is also a folder
                    parent_record_type = RecordType.FILE

            # Determine record ID and version
            is_new = existing_record is None
            record_id = str(uuid.uuid4()) if is_new else existing_record.id

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            folder_record = FileRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=folder_title,
                record_type=RecordType.FILE,
                external_record_id=folder_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                external_record_group_id=external_record_group_id,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                is_file=False,  # This is a folder, not a file
                extension=None,
                mime_type=MimeTypes.FOLDER.value,
                size_in_bytes=0,
                weburl=web_url,
                path=None,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )
            # Manual indexing turns every key off; a folder has no key of its own.
            if not self.indexing_filters.is_enabled(IndexingFilterKey.FOLDERS):
                folder_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            return folder_record

        except Exception as e:
            self.logger.error(f"❌ Failed to transform folder: {e}")
            return None

    def _transform_to_attachment_file_record(
        self,
        attachment_data: dict[str, Any],
        parent_external_record_id: str,
        parent_external_record_group_id: str | None,
        existing_record: Record | None = None,
        parent_node_id: str | None = None,
        attachment_api_base_url: str | None = None,
    ) -> FileRecord | None:
        """
        Transform Confluence attachment to FileRecord entity.
        Supports both v1 and v2 API response formats.

        Args:
            attachment_data: Raw attachment data from v1 (children.attachment.results) or v2 API
            parent_external_record_id: Parent page external_record_id
            parent_external_record_group_id: Space ID from parent page
            existing_record: Optional existing record to check for updates
            parent_node_id: Internal record ID of parent page
            attachment_api_base_url: Root _links.base from v2 paginated attachment responses (not on each item)

        Returns:
            FileRecord object or None if transformation fails
        """
        try:
            # Get attachment ID - both v1 and v2 use "id" field with "att" prefix
            attachment_id = attachment_data.get("id")
            if not attachment_id:
                return None
            # Get filename - same field in both v1 and v2
            file_name = attachment_data.get("title")
            if not file_name:
                return None

            # Clean query params from filename if present
            if '?' in file_name:
                file_name = file_name.split('?')[0]

            # Parse timestamps - v1 vs v2 have different structures
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = attachment_data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = attachment_data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version (version.createdAt, version.number)
            version_data = attachment_data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format (history.lastUpdated.when, history.lastUpdated.number)
            if not source_updated_at or not version_number:
                history = attachment_data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    if not source_updated_at:
                        updated_when = last_updated.get("when")
                        if updated_when:
                            source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract file size - v2 has it at top level, v1 in extensions
            file_size = attachment_data.get("fileSize")  # v2 format
            if file_size is None:
                extensions = attachment_data.get("extensions", {})
                file_size = extensions.get("fileSize")  # v1 format

            # Extract mime type - v2 has it at top level (mediaType), v1 in extensions or metadata
            media_type = attachment_data.get("mediaType")  # v2 format
            if not media_type:
                extensions = attachment_data.get("extensions", {})
                media_type = extensions.get("mediaType")  # v1 format (extensions)
            if not media_type:
                metadata = attachment_data.get("metadata", {})
                media_type = metadata.get("mediaType")  # v1 format (metadata)

            mime_type = None
            if media_type:
                # Try to map to MimeTypes enum
                for mime in MimeTypes:
                    if mime.value == media_type:
                        mime_type = mime
                        break

                # If not found in enum, use the raw value
                if not mime_type:
                    mime_type = media_type

            # Extract extension from filename
            extension = None
            if '.' in file_name:
                extension = file_name.split('.')[-1].lower()

            # Construct web URL: v2 list responses put base only on the parent _links; per-item has webui + webuiLink
            links = dict(attachment_data.get("_links") or {})
            if not links.get("webui"):
                webui_link = attachment_data.get("webuiLink")
                if webui_link:
                    links["webui"] = webui_link
            effective_base = attachment_api_base_url or links.get("base")
            web_url = self._construct_web_url(links, effective_base)

            # Determine record ID and version
            is_new = existing_record is None
            attachment_record_id = str(uuid.uuid4()) if is_new else existing_record.id

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            return FileRecord(
                id=attachment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=attachment_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                mime_type=mime_type,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=RecordType.WEBPAGE,
                external_record_group_id=parent_external_record_group_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                weburl=web_url,
                is_file=True,
                size_in_bytes=file_size,
                extension=extension,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                is_dependent_node=True,  # Attachments are dependent nodes
                parent_node_id=parent_node_id,  # Internal record ID of parent page
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform attachment: {e}")
            return None

    async def _process_webpage_with_update(
        self,
        data: dict[str, Any],
        record_type: RecordType,
        existing_record: Record | None,
        permissions: list[Permission]
    ) -> RecordUpdate:
        """Process webpage with change detection.

        Args:
            data: Raw data from Confluence API
            record_type: RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST, or RecordType.CONFLUENCE_FOLDER
            existing_record: Existing record from database (if any)
            permissions: Permissions for the record

        Returns:
            RecordUpdate object with change tracking
        """
        # Transform with existing record context
        webpage_record = self._transform_to_webpage_record(
            data, record_type, existing_record
        )
        if webpage_record:
            webpage_record.rewrite_permissions = True

        if not webpage_record:
            return RecordUpdate(
                record=None,
                is_new=False,
                is_updated=False,
                is_deleted=False,
                content_changed=False,
                metadata_changed=False,
                permissions_changed=False,
                new_permissions=None,
                external_record_id=None
            )

        # Detect changes
        is_new = existing_record is None
        content_changed = False
        metadata_changed = False

        if not is_new:
            # Check if version changed (content update)
            # Handle both dict and direct number access
            version_data = data.get("version", {})
            if isinstance(version_data, dict):
                current_version = version_data.get("number")
            else:
                current_version = version_data
            
            if str(current_version) != existing_record.external_revision_id:
                content_changed = True

            # Check if parent changed (moved between pages/folders)
            current_parent_v2 = data.get("parentId")
            current_parent_v1 = None
            ancestors = data.get("ancestors", [])
            if ancestors and len(ancestors) > 0:
                current_parent_v1 = ancestors[-1].get("id")
            current_parent = current_parent_v2 or current_parent_v1

            if current_parent != existing_record.parent_external_record_id:
                metadata_changed = True

        return RecordUpdate(
            record=webpage_record,
            is_new=is_new,
            is_updated=content_changed or metadata_changed,
            is_deleted=False,
            content_changed=content_changed,
            metadata_changed=metadata_changed,
            permissions_changed=bool(permissions),
            new_permissions=permissions,
            external_record_id=webpage_record.external_record_id
        )

    async def _fetch_group_members(
        self, group_id: str, group_name: str
    ) -> Optional[tuple[list[str], list[str]]]:
        """
        Fetch all members of a group with pagination.

        Args:
            group_id: The group ID
            group_name: The group name (for logging)

        Returns:
            Tuple of (member emails, accountIds for members without email in API response),
            or None when the member list could not be read in full.
        """
        try:
            member_emails: list[str] = []
            member_account_ids: list[str] = []
            batch_size = 100
            start = 0

            # Paginate through group members
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_group_members(
                    group_id=group_id,
                    start=start,
                    limit=batch_size
                )

                if response and response.status == HttpStatusCode.NOT_FOUND.value:
                    # The group no longer exists, so it has no members to keep (not even earlier pages).
                    self.logger.warning(f"Group {group_name} was not found while reading its members")
                    return [], []

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to fetch members for group {group_name}: {response.status if response else 'No response'}")
                    return None

                response_data = response.json()
                members_data = response_data.get("results", [])

                if not members_data:
                    break

                for member_data in members_data:
                    email = (member_data.get("email") or "").strip()
                    account_id = (member_data.get("accountId") or member_data.get("id") or "").strip()
                    # Deactivated accounts keep their memberships in Confluence; the product
                    # API reports the status only here (search/user leaves it out).
                    if member_data.get("accountStatus", "active") != "active":
                        if account_id:
                            self._pending_inactive_ids.add(account_id)
                        continue
                    if email:
                        self._pending_active_emails.add(email.lower())
                        member_emails.append(email)
                    elif account_id:
                        member_account_ids.append(account_id)
                    else:
                        self.logger.debug(
                            "Skipping group member %s: no email or accountId",
                            member_data.get("displayName"),
                        )

                next_start = v1_next_start(response_data, start, len(members_data), batch_size, use_link_offset=True)
                if next_start is None:
                    break
                start = next_start

            return member_emails, member_account_ids

        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch members for group {group_name}: {e}")
            return None

    async def _remove_groups_gone_from_source(self, listed_group_ids: set[str]) -> None:
        """Delete the stored groups a complete group listing no longer has, with their access.

        Space grants are re-read every sync, but a page restricted to a deleted group is not,
        so the group's former members kept that page until it was edited. Pseudo-groups are
        the connector's own and stay. An empty listing removes nothing: that is the account
        failing, not every group going.
        """
        if not listed_group_ids:
            return
        try:
            stored = await self.data_entities_processor.get_all_user_groups(self.connector_id, raise_on_error=True)
        except Exception as e:
            self.logger.warning(f"Could not read the stored groups; none removed: {e}")
            self._note_problem("Couldn't check for deleted Confluence groups; the next sync tries again", unread=False)
            return
        for group in stored:
            if (group.name or "").startswith(PSEUDO_USER_GROUP_PREFIX):
                continue
            if str(group.source_user_group_id) in listed_group_ids:
                continue
            # An offset list can skip a group that shifted between pages: only a 404 says it is gone.
            if not await self._group_is_gone(group.source_user_group_id):
                continue
            try:
                await self.data_entities_processor.on_user_group_deleted(group.source_user_group_id, self.connector_id)
                self.logger.info(f"Removed group {group.name}: Confluence no longer has it")
            except Exception as e:
                self.logger.warning(f"Could not remove group {group.name}: {e}")
                self._note_problem(
                    f"Couldn't remove group {group.name}, which Confluence no longer has; the next sync tries again",
                    unread=False,
                )

    async def _group_is_gone(self, group_id: str) -> bool:
        """True only when Confluence answers 404 for the group; any other answer keeps it."""
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_group_members(group_id=group_id, start=0, limit=1)
        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            self.logger.debug(f"Could not look up group {group_id}: {e}")
            return False
        return response is not None and response.status == HttpStatusCode.NOT_FOUND.value

    async def _restore_reactivated_users(self) -> None:
        """Save the users _sync_users held back whose account this sync's full group read
        reports active again. Statuses from a partial group read are not trusted."""
        held_back, self._held_back_users = self._held_back_users, []
        if not self._group_read_complete:
            if held_back:
                self.logger.warning(
                    f"Not saving {len(held_back)} Confluence user(s) this sync: their account status is unknown"
                )
                self._note_problem(
                    f"Didn't save {len(held_back)} Confluence user(s): their account status is unknown "
                    "until every group's member list can be read"
                )
            return
        active = [u for u in held_back if u.source_user_id not in self._inactive_account_ids]
        if active:
            await self._save_app_users(active)
            self.logger.info(f"Saved {len(active)} held-back Confluence user(s) the group read reports active")

    async def _reconcile_inactive_app_users(self) -> None:
        """After the group sync: withdraw the stored users now inactive, from a full group read only."""
        if not self._group_read_complete:
            self.logger.warning(
                "Not every group member list was read: account statuses stay as last read in full"
            )
            return
        await self._withdraw_inactive_app_users()

    async def _withdraw_users_gone_from_source(self, listed_account_ids: set[str]) -> None:
        """Take the connector away from stored users a complete user list no longer has.

        A user removed from the site, or without Confluence access any more, is not
        deactivated, so the inactive check never sees them; their grants would stay.
        When more than half the stored users are missing the list is not trusted.
        """
        try:
            stored = await self.data_entities_processor.get_all_app_users(self.connector_id)
        except Exception as e:
            self.logger.warning(f"Could not read the stored users; none withdrawn: {e}")
            return
        gone = [u for u in stored if u.source_user_id and u.source_user_id not in listed_account_ids]
        if not gone:
            return
        if len(gone) * 2 > len(stored):
            self._note_problem(
                f"{len(gone)} of {len(stored)} stored users were missing from Confluence's user list; "
                "none was removed, because that many looks like a misread",
                unread=False,
                persistent=True,
            )
            return
        self.logger.info(f"Withdrawing {len(gone)} users Confluence no longer lists")
        await self.data_entities_processor.withdraw_app_user_access(self.connector_id, gone)

    async def _withdraw_inactive_app_users(self) -> None:
        """Take the connector away from stored users whose account a group listing reported
        inactive, unless another account with their email is an active member."""
        if not self._inactive_account_ids:
            return
        gone = [
            u for u in await self.data_entities_processor.get_all_app_users(self.connector_id)
            if u.source_user_id in self._inactive_account_ids
            and (u.email or "").lower() not in self._active_member_emails
        ]
        if gone:
            await self.data_entities_processor.withdraw_app_user_access(self.connector_id, gone)

    async def _app_user_from_linked_source_id(self, account_id: str) -> Optional[AppUser]:
        """Build AppUser for a Confluence accountId linked to this connector.

        Returns None when no user is linked to the accountId. A failed read
        raises instead: a group saved from it would lose its members.
        """
        user = await self.data_entities_processor.get_user_by_source_id(
            source_user_id=account_id,
            connector_id=self.connector_id,
            raise_on_error=True,
        )
        if not user or not user.email:
            return None
        return AppUser(
            app_name=Connectors.CONFLUENCE,
            connector_id=self.connector_id,
            source_user_id=account_id,
            org_id=self.data_entities_processor.org_id,
            email=user.email,
            full_name=user.full_name or user.email,
            is_active=False,
        )

    async def _resolve_group_app_users(
        self,
        member_emails: list[str],
        member_account_ids: list[str],
    ) -> list[AppUser]:
        """Resolve group members by email and by linked accountId."""
        app_users = await self._get_app_users_by_emails(member_emails)
        seen_emails = {u.email for u in app_users if u.email}

        for account_id in member_account_ids:
            app_user = await self._app_user_from_linked_source_id(account_id)
            if app_user and app_user.email not in seen_emails:
                app_users.append(app_user)
                seen_emails.add(app_user.email)

        return app_users

    async def _get_app_users_by_emails(self, emails: list[str]) -> list[AppUser]:
        """
        Get AppUser objects by their email addresses from database.

        Args:
            emails: List of user email addresses

        Returns:
            List of AppUser objects found in database

        Raises:
            Whatever the database read raised. A failed read is not "none of
            these people are users": a group saved from it would lose its members.
        """
        if not emails:
            return []

        # Fetch all users from database
        all_app_users = await self.data_entities_processor.get_all_app_users(
            connector_id=self.connector_id, raise_on_error=True
        )

        self.logger.debug(f"Fetched {len(all_app_users)} total users from database for email lookup")

        # Create email lookup map
        email_set = set(emails)

        # Filter users by email
        filtered_users = [user for user in all_app_users if user.email in email_set]

        if len(filtered_users) < len(emails):
            missing_count = len(emails) - len(filtered_users)
            self.logger.debug(f"  ⚠️ {missing_count} user(s) not found in database")

        return filtered_users

    def _transform_to_app_user(self, user_data: dict[str, Any]) -> AppUser | None:
        """
        Transform Confluence user data to AppUser entity.

        Args:
            user_data: Raw user data from Confluence API

        Returns:
            AppUser object or None if transformation fails
        """
        try:
            account_id = user_data.get("accountId")
            email = user_data.get("email", "").strip()

            if not account_id or not email:
                return None

            # Parse lastModified timestamp
            source_updated_at = None
            last_modified = user_data.get("lastModified")
            if last_modified:
                source_updated_at = self._parse_confluence_datetime(last_modified)

            return AppUser(
                app_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                source_user_id=account_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=user_data.get("displayName"),
                is_active=False,
                source_updated_at=source_updated_at,
                # Guests and external collaborators see only what they are given; not org members.
                is_guest=bool(user_data.get("isGuest") or user_data.get("isExternalCollaborator")),
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform user: {e}")
            return None

    def _transform_to_user_group(
        self,
        group_data: dict[str, Any]
    ) -> AppUserGroup | None:
        """
        Transform Confluence group data to AppUserGroup entity.

        Args:
            group_data: Raw group data from Confluence API

        Returns:
            AppUserGroup object or None if transformation fails
        """
        try:
            group_id = group_data.get("id")
            group_name = group_data.get("name")

            if not group_id or not group_name:
                return None

            return AppUserGroup(
                app_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                source_user_group_id=group_id,
                name=group_name,
                org_id=self.data_entities_processor.org_id,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform group: {e}")
            return None

    def _parse_confluence_datetime(self, datetime_str: str) -> int | None:
        """
        Parse Confluence datetime string to epoch timestamp in milliseconds.

        Confluence format: "2025-11-13T07:51:50.526Z" (ISO 8601 with Z suffix)

        Args:
            datetime_str: Confluence datetime string

        Returns:
            int: Epoch timestamp in milliseconds or None if parsing fails
        """
        try:
            # Parse ISO 8601 format: '2025-11-13T07:51:50.526Z'
            # Replace 'Z' with '+00:00' for proper ISO format parsing
            dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.warning(f"Failed to parse datetime '{datetime_str}': {e}")
            return None

    async def get_signed_url(self, record: Record) -> str:
        """Get a signed URL for a record (not implemented for Confluence)."""
        # Confluence uses OAuth, signed URLs are not applicable
        return ""

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a record; see ``_stream_record``."""
        # shortcut: covers building the response; a file still downloading after it is returned is not covered.
        self._in_use += 1
        try:
            return await self._stream_record(record)
        finally:
            await self._release()

    async def _stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (page BlocksContainer or attachment file) from Confluence.

        For pages/blogposts (WebpageRecord): Fetches ADF content and converts to BlocksContainer with embedded comments
        For attachments (FileRecord): Downloads file from attachment download URL

        Args:
            record: The record to stream (page, blogpost, or attachment)

        Returns:
            StreamingResponse: Streaming response with BlocksContainer JSON or file content
        """
        if record.is_placeholder:
            raise not_downloadable(
                f"Cannot stream placeholder record {record.external_record_id}: "
                "it is a stub for an out-of-scope ancestor and has no content",
                connector=self.display_name,
            )
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            if record.record_type in [RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST]:
                # Check if this is a legacy record with HTML mime type
                if record.mime_type == MimeTypes.HTML.value:
                    # Fetch styled_view HTML with inlined images and title
                    html_content = await self._fetch_page_content(record.external_record_id, record.record_type)
                    
                    return StreamingResponse(
                        iter([html_content.encode('utf-8')]),
                        media_type=MimeTypes.HTML.value,
                        headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.html"'}
                    )
                
                # Page or blogpost - fetch ADF content and convert to BlocksContainer
                page_data = await self._fetch_page_data_with_adf(record.external_record_id, record.record_type)

                # Process attachments for children_records
                # Fetch attachments directly from v2 API (since page content API doesn't include them)
                attachments_data = []
                attachments_api_base_url: str | None = None
                try:
                    datasource = await self._get_fresh_datasource()
                    attachments_data, attachments_api_base_url, _ = await self._list_current_attachments_v2(
                        datasource,
                        record.external_record_id,
                        "page" if record.record_type == RecordType.CONFLUENCE_PAGE else "blogpost",
                    )
                    self.logger.debug(f"Fetched {len(attachments_data)} attachment(s) for streaming")
                except AtlassianQuotaExhausted:
                    raise
                except Exception as e:
                    self.logger.warning(f"Failed to fetch attachments for streaming: {e}", exc_info=True)
                    attachments_data = []

                attachment_children_map = await self._process_page_attachments_for_children(
                    attachments_data,
                    record.external_record_id,
                    record.id,
                    record.external_record_group_id,
                    record.weburl,
                    attachment_api_base_url=attachments_api_base_url,
                )

                # Build MIME types map from attachments_data
                attachment_mime_types: dict[str, str] = {}
                for attachment in attachments_data:
                    attachment_id = attachment.get("id")
                    if attachment_id:
                        # Try different locations for mediaType (v2 API structure)
                        media_type = attachment.get("mediaType") or attachment.get("metadata", {}).get("mediaType")
                        if media_type:
                            attachment_mime_types[str(attachment_id)] = media_type

                # Parse to BlocksContainer
                blocks_container = await self._parse_confluence_page_to_blocks(
                    page_data=page_data,
                    page_id=record.external_record_id,
                    page_title=record.record_name,
                    weburl=record.weburl,
                    attachment_children_map=attachment_children_map,
                    attachment_mime_types=attachment_mime_types,
                    attachments_data=attachments_data,
                    record_type=record.record_type,
                )

                # Serialize and stream
                blocks_json = blocks_container.model_dump_json(indent=2)

                return StreamingResponse(
                    iter([blocks_json.encode('utf-8')]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.json"'}
                )

            elif record.record_type == RecordType.FILE:
                filename = record.record_name or f"{record.external_record_id}"
                return create_stream_record_response(
                    self._fetch_attachment_content(record),
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            elif record.record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT]:
                # Comment record - fetch comment content
                comment_data = await self._fetch_comment_data(record)
                if not comment_data:
                    raise not_found_at_source(self.display_name)

                # Extract comment content
                body = comment_data.get("body", {})
                storage_content = body.get("storage", {})
                comment_html = storage_content.get("value", "")
                
                # Return as HTML streaming response
                return StreamingResponse(
                    iter([comment_html.encode('utf-8')]),
                    media_type=MimeTypes.HTML.value,
                    headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.html"'}
                )

            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type for streaming: {record.record_type}"
                )

        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except AtlassianQuotaExhausted as e:
            raise self._quota_http_error(e) from e
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

    @staticmethod
    def _quota_http_error(error: AtlassianQuotaExhausted) -> HTTPException:
        """A 429 with Retry-After, so indexing defers the record instead of failing it."""
        return HTTPException(
            status_code=HttpStatusCode.TOO_MANY_REQUESTS.value,
            detail=f"Confluence can't be read right now: {error}. Try again after that.",
            headers={"Retry-After": str(max(1, int(error.reset_at - time.time())))},
        )

    async def _fetch_page_content(self, page_id: str, record_type: RecordType) -> str:
        """
        Fetch page or blogpost styled_view HTML from Confluence using v2 API.

        Resolves URLs, inlines same-origin images as base64, and injects the title as H1.

        Args:
            page_id: The page or blogpost ID
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            str: Processed HTML content of the page/blogpost

        Raises:
            HTTPException: If content not found or fetch fails
        """
        try:
            self.logger.debug(f"Fetching content for {page_id} (type: {record_type})")

            datasource = await self._get_fresh_datasource()

            if record_type == RecordType.CONFLUENCE_PAGE:
                response = await datasource.get_page_content_v2(
                    page_id=page_id,
                    body_format="styled_view"
                )
            elif record_type == RecordType.CONFLUENCE_BLOGPOST:
                response = await datasource.get_blogpost_content_v2(
                    blogpost_id=page_id,
                    body_format="styled_view"
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type: {record_type}"
                )

            if not response:
                raise not_found_at_source(self.display_name)

            if response.status != HttpStatusCode.SUCCESS.value:
                raise map_source_status(response.status, connector=self.display_name)

            response_data = response.json()

            body = response_data.get("body", {})
            styled_view = body.get("styled_view", {})
            html_content = str(styled_view.get("value") or "")

            if not html_content:
                self.logger.warning(f"Content {page_id} has no body")
                html_content = "<p>No content available</p>"

            _download = self._create_cloud_html_image_downloader(
                datasource, page_id, record_type
            )

            html_content = await prepare_streaming_html(
                html_content,
                response_data,
                _download,
                logger=self.logger,
            )

            self.logger.debug(f"✅ Fetched {len(html_content)} bytes of HTML for {page_id}")
            return html_content

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch content: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

    @staticmethod
    def _rest_attachment_id_from_linked_resource(linked_resource_id: str) -> str:
        """Convert styled_view data-linked-resource-id to v1/v2 attachment id."""
        value = str(linked_resource_id).strip()
        if not value:
            return ""
        if value.lower().startswith("att"):
            return value
        return f"att{value}"

    async def _list_current_attachments_v2(
        self,
        datasource: ConfluenceDataSource,
        content_id: str,
        content_type: str,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        """Every current attachment of a page or blog post, following the v2 cursor.

        Returns the attachments, the listing's base URL, and whether the list was cut
        short, so the caller keeps its checkpoint. It is cut short when a later page
        fails, when the first page fails for a temporary reason (the caller's fallback
        may then be partial), or when a next link can't be followed.
        """
        list_attachments = (
            datasource.get_page_attachments if content_type == "page" else datasource.get_blogpost_attachments
        )
        attachments: list[dict[str, Any]] = []
        base_url: str | None = None
        cursor: str | None = None
        visited_cursors: set[str] = set()
        while True:
            kwargs: dict[str, Any] = {"id": int(content_id), "status": ["current"], "limit": limit}
            if cursor:
                kwargs["cursor"] = cursor
            try:
                response = await list_attachments(**kwargs)
            except Exception as e:
                self.logger.warning(f"Could not list attachments of {content_type} {content_id}: {e}")
                response = None
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                temporary = not response or is_retryable_status(response.status)
                if attachments or temporary:
                    self.logger.warning(
                        f"Listed only {len(attachments)} attachments of {content_type} {content_id} before the "
                        "listing failed; the rest are read again next sync"
                    )
                return attachments, base_url, bool(attachments) or temporary
            data = response.json() or {}
            attachments.extend(data.get("results") or [])
            links = data.get("_links") or {}
            base_url = base_url or links.get("base")
            if not links.get("next"):
                return attachments, base_url, False
            next_cursor = self._extract_cursor_from_next_link(links["next"])
            if not next_cursor or next_cursor == cursor or next_cursor in visited_cursors:
                self.logger.warning(
                    f"Can't follow the next link of the attachment list of {content_type} {content_id} "
                    f"after {len(attachments)} attachments; the rest are read again next sync"
                )
                return attachments, base_url, True
            visited_cursors.add(next_cursor)
            cursor = next_cursor

    async def _fetch_page_attachments_list(
        self,
        page_id: str,
        record_type: RecordType,
        *,
        datasource: ConfluenceDataSource | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch every current attachment of one page/blogpost (``limit`` is the page size)."""
        try:
            ds = datasource or await self._get_fresh_datasource()
            content_type = "blogpost" if record_type == RecordType.CONFLUENCE_BLOGPOST else "page"
            attachments, _, _ = await self._list_current_attachments_v2(ds, str(page_id), content_type, limit=limit)
            return attachments
        except Exception as e:
            self.logger.debug(f"Failed to list attachments for page {page_id}: {e}")
        return []

    def _find_attachment_in_list(
        self,
        attachments: list[dict[str, Any]],
        *,
        file_id: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any] | None:
        if file_id:
            for attachment in attachments:
                if attachment.get("fileId") == file_id:
                    return attachment

        if filename:
            name = str(filename).strip()
            if not name:
                return None
            name_lower = name.lower()
            for attachment in attachments:
                title = attachment.get("title")
                if title and str(title).strip() == name:
                    return attachment
            for attachment in attachments:
                title = attachment.get("title")
                if title and str(title).lower().strip() == name_lower:
                    return attachment
        return None

    async def _get_attachment_metadata_by_id(
        self,
        datasource: ConfluenceDataSource,
        attachment_id: str,
        meta_cache: dict[str, dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        if attachment_id in meta_cache:
            return meta_cache[attachment_id]

        try:
            response = await datasource.get_attachment_by_id(id=attachment_id)
            if response and response.status == HttpStatusCode.SUCCESS.value:
                meta = response.json()
                meta_cache[attachment_id] = meta
                return meta
            meta_cache[attachment_id] = None
        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            self.logger.debug(f"Attachment metadata lookup failed for {attachment_id}: {e}")
            meta_cache[attachment_id] = None
        return None

    def _create_cloud_html_image_downloader(
        self,
        datasource: ConfluenceDataSource,
        page_id: str,
        record_type: RecordType,
    ) -> Callable[[str, HtmlImageContext], Awaitable[tuple[bytes, str] | None]]:
        """Build per-image download callback scoped to the current page only."""
        captured_record_type = record_type
        page_meta_cache: dict[str, dict[str, Any] | None] = {}
        page_attachments: list[dict[str, Any]] | None = None

        async def _ensure_page_attachments() -> list[dict[str, Any]]:
            nonlocal page_attachments
            if page_attachments is None:
                page_attachments = await self._fetch_page_attachments_list(
                    page_id,
                    captured_record_type,
                    datasource=datasource,
                )
            return page_attachments

        async def _resolve_attachment_for_html_image(
            image_context: HtmlImageContext,
            url_filename: str | None,
        ) -> dict[str, Any] | None:
            linked_type = (image_context.linked_resource_type or "").lower()
            if linked_type == "attachment" and image_context.linked_resource_id:
                att_id = self._rest_attachment_id_from_linked_resource(
                    image_context.linked_resource_id
                )
                if att_id:
                    meta = await self._get_attachment_metadata_by_id(
                        datasource, att_id, page_meta_cache
                    )
                    if meta:
                        return meta

            if image_context.media_id:
                attachments = await _ensure_page_attachments()
                match = self._find_attachment_in_list(
                    attachments, file_id=image_context.media_id
                )
                if match:
                    att_id = match.get("id")
                    if att_id:
                        page_meta_cache[str(att_id)] = match
                    return match

            seen: set[str] = set()
            for candidate in (
                image_context.default_alias,
                image_context.alt_text,
                url_filename,
            ):
                if not candidate or not str(candidate).strip():
                    continue
                key = str(candidate).lower().strip()
                if key in seen:
                    continue
                seen.add(key)

                attachments = await _ensure_page_attachments()
                match = self._find_attachment_in_list(
                    attachments, filename=str(candidate).strip()
                )
                if match:
                    att_id = match.get("id")
                    if att_id:
                        page_meta_cache[str(att_id)] = match
                    return match
            return None

        async def _download(
            url: str,
            image_context: HtmlImageContext,
        ) -> tuple[bytes, str] | None:
            if not is_cloud_attachment_download_url(url):
                return None

            url_filename = extract_attachment_filename_from_download_url(url)
            attachment = await _resolve_attachment_for_html_image(
                image_context,
                url_filename,
            )
            if not attachment:
                return None

            mime_type = attachment.get("mediaType") or ""
            if not mime_type.startswith("image/"):
                return None

            attachment_id = attachment.get("id")
            if not attachment_id:
                return None
            if str(attachment_id) in images_not_inlined(await _ensure_page_attachments()):
                return None

            try:
                content = bytearray()
                async for chunk in datasource.download_attachment(
                    parent_page_id=page_id,
                    attachment_id=str(attachment_id),
                ):
                    content.extend(chunk)
                    if len(content) > MAX_INLINE_IMAGE_BYTES:
                        # Inlined as base64 in the page: a huge image would sit in memory several times over.
                        self.logger.debug(f"Image {attachment_id} is over {MAX_INLINE_IMAGE_BYTES} bytes; left out of the page body")
                        return None
                content_bytes = bytes(content)
                if not content_bytes:
                    return None
                return content_bytes, mime_type
            except AtlassianQuotaExhausted:
                raise
            except Exception as e:
                self.logger.debug(
                    f"REST attachment download failed for {attachment_id} on page {page_id}: {e}"
                )
                return None

        return _download

    def _resolve_confluence_attachment_id(
        self,
        media_info: dict[str, Any],
        attachments: list[dict[str, Any]] | None = None
    ) -> str | None:
        """
        Resolve Confluence attachment ID from ADF media node.

        ADF media nodes have attrs.id which is a Media Platform UUID.
        Attachment objects have:
        - id: Confluence attachment ID (e.g., "att195952659")
        - fileId: Media Platform UUID that matches attrs.id from ADF

        Args:
            media_info: Media node info from extract_media_from_adf
            attachments: Optional list of attachment objects to match against

        Returns:
            Confluence attachment ID (e.g., "att195952659") or None
        """
        media_id = media_info.get("id")  # Media Platform UUID from ADF
        filename = media_info.get("filename") or media_info.get("alt")

        if media_id and attachments:
            for att in attachments:
                att_file_id = att.get("fileId")
                if att_file_id == media_id:
                    return att.get("id")

        if filename and attachments:
            for att in attachments:
                if att.get("title", "") == filename:
                    return att.get("id")

            filename_lower = filename.lower().strip()
            for att in attachments:
                if att.get("title", "").lower().strip() == filename_lower:
                    return att.get("id")

        return None

    def _create_confluence_media_fetcher(
        self,
        page_id: str,
        content_type: str = "page",
        attachments_data: list[dict[str, Any]] | None = None,
    ) -> Callable[[str, str], Awaitable[str | None]]:
        """
        Create a media fetcher callback bound to a specific Confluence page.

        Attachment metadata is loaded at most once per page and discarded when
        streaming for that page completes.
        """
        captured_page_id = page_id
        captured_content_type = content_type
        page_attachments: list[dict[str, Any]] | None = (
            list(attachments_data) if attachments_data else None
        )

        async def fetcher(media_id: str, alt_text: str) -> str | None:
            nonlocal page_attachments
            record_type = (
                RecordType.CONFLUENCE_BLOGPOST
                if captured_content_type == "blogpost"
                else RecordType.CONFLUENCE_PAGE
            )
            if page_attachments is None:
                page_attachments = await self._fetch_page_attachments_list(
                    captured_page_id,
                    record_type,
                )
            return await self._fetch_confluence_media_as_base64(
                captured_page_id,
                media_id,
                alt_text,
                captured_content_type,
                attachments=page_attachments,
            )

        return fetcher

    async def _fetch_confluence_media_as_base64(
        self,
        page_id: str,
        media_id: str,
        media_alt: str,
        content_type: str = "page",
        attachments: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """
        Fetch Confluence attachment content and return as base64 data URI.

        Args:
            page_id: The page ID containing the attachment
            media_id: The media ID from ADF (UUID token)
            media_alt: The alt text/filename for matching
            content_type: "page" or "blogpost" to determine which API to use
            attachments: Current-page attachment metadata (loaded once per page)

        Returns:
            Base64 data URI string or None
        """
        try:
            datasource = await self._get_fresh_datasource()
            record_type = (
                RecordType.CONFLUENCE_BLOGPOST
                if content_type == "blogpost"
                else RecordType.CONFLUENCE_PAGE
            )
            page_attachments = attachments
            if page_attachments is None:
                page_attachments = await self._fetch_page_attachments_list(
                    page_id, record_type, datasource=datasource
                )

            attachment_id = self._resolve_confluence_attachment_id(
                {
                    "id": media_id,
                    "filename": media_alt,
                    "alt": media_alt,
                },
                page_attachments,
            )
            if not attachment_id:
                self.logger.debug(
                    f"No attachment found matching media id '{media_id}' / '{media_alt}' on page {page_id}"
                )
                return None

            target_attachment = next(
                (att for att in page_attachments if att.get("id") == attachment_id),
                None,
            )
            if not target_attachment:
                self.logger.debug(
                    f"Attachment metadata missing for {attachment_id} on page {page_id}"
                )
                return None

            mime_type = target_attachment.get("mediaType") or "image/png"

            if not mime_type.startswith("image/"):
                self.logger.debug(f"Skipping non-image media type: {mime_type} for {media_alt}")
                return None
            if str(attachment_id) in images_not_inlined(page_attachments):
                self.logger.debug(f"Image {attachment_id} is left out of the page body; it stays an attachment")
                return None

            content = bytearray()
            async for chunk in datasource.download_attachment(
                parent_page_id=page_id,
                attachment_id=str(attachment_id),
            ):
                content.extend(chunk)
                if len(content) > MAX_INLINE_IMAGE_BYTES:
                    # Inlined as base64 in the page: a huge image would sit in memory several times over.
                    self.logger.debug(f"Image {attachment_id} is over {MAX_INLINE_IMAGE_BYTES} bytes; left out of the page body")
                    return None
            content_bytes = bytes(content)

            if not content_bytes:
                return None

            base64_data = base64.b64encode(content_bytes).decode("utf-8")
            return f"data:{mime_type};base64,{base64_data}"

        except AtlassianQuotaExhausted:
            raise
        except Exception as e:
            self.logger.warning(
                f"Error fetching media (id='{media_id}', alt='{media_alt}') for page {page_id}: {e}"
            )
            return None

    async def _comments_indexed(self, record_type: RecordType) -> bool:
        """Whether the "Index page comments" / "Index blog post comments" setting is on (default on).

        Read from the settings at stream time: indexing streams through an instance whose
        sync never ran, so its loaded filters may be empty.
        """
        try:
            _, indexing_filters = await load_connector_filters(
                self.config_service, "confluence", self.connector_id, self.logger
            )
        except Exception:
            indexing_filters = self.indexing_filters
        key = (
            IndexingFilterKey.PAGE_COMMENTS if record_type == RecordType.CONFLUENCE_PAGE
            else IndexingFilterKey.BLOGPOST_COMMENTS
        )
        return indexing_filters.is_enabled(key)

    async def _fetch_page_comments_recursive(
        self,
        page_id: str,
        record_type: RecordType,
        comment_type: str  # "footer" or "inline"
    ) -> list[dict[str, Any]]:
        """
        Recursively fetch all comments (footer or inline) for a page or blogpost using v2 API.
        Returns a flat list of all comments including nested replies.

        Args:
            page_id: The page/blogpost ID
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
            comment_type: "footer" or "inline"

        Returns:
            List of comment dictionaries with ADF body
        """
        all_comments: list[dict[str, Any]] = []
        batch_size = 100
        cursor = None
        followed: set[str] = set()

        # Early return for unsupported record types
        if record_type not in [RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST]:
            self.logger.debug(f"Unsupported record type for comments: {record_type}")
            return []

        datasource = await self._get_fresh_datasource()
        page_id_int = int(page_id) if isinstance(page_id, str) else page_id

        # Fetch top-level comments
        while True:
            try:
                if record_type == RecordType.CONFLUENCE_PAGE:
                    if comment_type == "footer":
                        response = await datasource.get_page_footer_comments(
                            id=page_id_int,
                            body_format="atlas_doc_format",  # Pass as string
                            cursor=cursor,
                            limit=batch_size
                        )
                    else:  # inline
                        response = await datasource.get_page_inline_comments(
                            id=page_id_int,
                            body_format="atlas_doc_format",  # Pass as string
                            cursor=cursor,
                            limit=batch_size
                        )
                elif record_type == RecordType.CONFLUENCE_BLOGPOST:
                    if comment_type == "footer":
                        response = await datasource.get_blog_post_footer_comments(
                            id=page_id_int,
                            body_format="atlas_doc_format",  # Pass as string
                            cursor=cursor,
                            limit=batch_size
                        )
                    else:  # inline
                        response = await datasource.get_blog_post_inline_comments(
                            id=page_id_int,
                            body_format="atlas_doc_format",  # Pass as string
                            cursor=cursor,
                            limit=batch_size
                        )
                else:
                    self.logger.error(f"Unsupported record type for comments: {record_type}")
                    break

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    raise CommentsUnreadable(
                        f"{comment_type} comments of {page_id} could not be read "
                        f"(HTTP {response.status if response else 'no response'})"
                    )

                response_data = response.json()
                comments_data = response_data.get("results", [])

                if not comments_data:
                    break

                # Process each comment and recursively fetch children
                for comment in comments_data:
                    all_comments.append(comment)
                    # Recursively fetch children
                    children = await self._fetch_comment_children_recursive(
                        comment_id=comment.get("id"),
                        comment_type=comment_type,
                        record_type=record_type
                    )
                    all_comments.extend(children)

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor or cursor in followed:
                    raise CommentsUnreadable(f"{comment_type} comments of {page_id} could not be followed to the end")
                followed.add(cursor)

            except (CommentsUnreadable, AtlassianQuotaExhausted):
                raise
            except Exception as e:
                # Indexed without its comments the page would stay that way until it changes.
                raise CommentsUnreadable(f"{comment_type} comments of {page_id} could not be read: {e}") from e

        return all_comments

    async def _fetch_comment_children_recursive(
        self,
        comment_id: str,
        comment_type: str,  # "footer" or "inline"
        record_type: RecordType
    ) -> list[dict[str, Any]]:
        """
        Recursively fetch all child comments (replies) for a given comment.

        Args:
            comment_id: The parent comment ID
            comment_type: "footer" or "inline"
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            List of child comment dictionaries
        """
        all_children: list[dict[str, Any]] = []
        batch_size = 100
        cursor = None
        followed: set[str] = set()

        datasource = await self._get_fresh_datasource()
        comment_id_int = int(comment_id) if isinstance(comment_id, str) else comment_id

        while True:
            try:
                if comment_type == "footer":
                    response = await datasource.get_footer_comment_children(
                        id=comment_id_int,
                        body_format="atlas_doc_format",  # Pass as string
                        cursor=cursor,
                        limit=batch_size
                    )
                else:  # inline
                    response = await datasource.get_inline_comment_children(
                        id=comment_id_int,
                        body_format="atlas_doc_format",  # Pass as string
                        cursor=cursor,
                        limit=batch_size
                    )

                if response is not None and response.status == HttpStatusCode.NOT_FOUND.value:
                    # Deleted between the list and this read: its replies went with it.
                    self.logger.warning(f"Replies to comment {comment_id} are gone; indexing the page without them")
                    break
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    raise CommentsUnreadable(
                        f"replies to comment {comment_id} could not be read "
                        f"(HTTP {response.status if response else 'no response'})"
                    )

                response_data = response.json()
                children_data = response_data.get("results", [])

                if not children_data:
                    break

                # Process each child and recursively fetch their children
                for child in children_data:
                    all_children.append(child)
                    # Recursively fetch grandchildren
                    grandchildren = await self._fetch_comment_children_recursive(
                        comment_id=child.get("id"),
                        comment_type=comment_type,
                        record_type=record_type
                    )
                    all_children.extend(grandchildren)

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor or cursor in followed:
                    raise CommentsUnreadable(f"replies to comment {comment_id} could not be followed to the end")
                followed.add(cursor)

            except (CommentsUnreadable, AtlassianQuotaExhausted):
                raise
            except Exception as e:
                raise CommentsUnreadable(f"replies to comment {comment_id} could not be read: {e}") from e

        return all_children

    def _organize_confluence_comments_to_threads(
        self,
        comments_data: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """
        Group Confluence comments by thread (parent comment) and sort by created timestamp.
        Returns list of threads, each thread is a list of comments sorted by created.

        Confluence comments can have parentCommentId for replies.
        - Top-level comments (no parentCommentId) start their own thread
        - Replies grouped under their parent's thread_id
        - Each thread sorted by created timestamp (oldest first)
        - Threads sorted by first comment's created timestamp
        """
        if not comments_data:
            return []

        threads: dict[str, list[dict[str, Any]]] = {}
        parent_of = {
            str(c.get("id", "")): str(c["parentCommentId"]) for c in comments_data if c.get("parentCommentId")
        }

        def root_of(comment_id: str) -> str:
            # A reply to a reply belongs to the thread of the comment that started it.
            seen: set[str] = set()
            while comment_id in parent_of and comment_id not in seen:
                seen.add(comment_id)
                comment_id = parent_of[comment_id]
            return comment_id

        for comment in comments_data:
            comment_id = str(comment.get("id", ""))
            thread_id = root_of(comment_id) if comment_id else ""
            if not thread_id:
                continue

            if thread_id not in threads:
                threads[thread_id] = []
            threads[thread_id].append(comment)

        # Sort each thread: the comment that started it first, then replies oldest first
        for thread_id in threads:
            threads[thread_id].sort(
                key=lambda c, root=thread_id: (
                    str(c.get("id", "")) != root,
                    self._parse_confluence_datetime(c.get("version", {}).get("createdAt", "")) or 0,
                )
            )

        # Sort threads by first comment's created timestamp (oldest thread first)
        return sorted(
            threads.values(),
            key=lambda t: self._parse_confluence_datetime(
                t[0].get("version", {}).get("createdAt", "")
            ) or 0 if t else 0
        )


    async def _parse_confluence_page_to_blocks(
        self,
        page_data: dict[str, Any],
        page_id: str,
        page_title: str,
        weburl: str | None = None,
        attachment_children_map: dict[str, ChildRecord] | None = None,
        attachment_mime_types: dict[str, str] | None = None,
        attachments_data: list[dict[str, Any]] | None = None,
        record_type: RecordType | None = None,
    ) -> BlocksContainer:
        """
        Parse Confluence page ADF content into BlocksContainer with comments.

        Structure:
        - Individual Block objects for each ADF node (paragraphs, headings, images, etc.)
        - BlockGroups for structural containers (tables, panels, toggles, etc.)
        - Footer comments as COMMENT_THREAD/COMMENT BlockGroups
        - Inline comments attached to Block.comments
        - Attachments as ChildRecords

        Args:
            page_data: Page data from API (with body.atlas_doc_format.value)
            page_id: Page external ID
            page_title: Page title
            weburl: Page web URL
            attachment_children_map: Map of attachment_id -> ChildRecord
            attachment_mime_types: Map of attachment_id -> mime_type
            attachments_data: List of attachment objects from API (for fileId matching)
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            BlocksContainer with BlockGroups and Blocks
        """
        # Initialize parser
        parser = ConfluenceBlockParser(self.logger)

        # Extract ADF content
        body = page_data.get("body", {})
        atlas_doc = body.get("atlas_doc_format", {})
        adf_content = atlas_doc.get("value")

        # Parse ADF if it's a string
        if isinstance(adf_content, str):
            try:
                adf_dict = json.loads(adf_content)
            except json.JSONDecodeError:
                adf_dict = None
        else:
            adf_dict = adf_content

        # Determine content_type for API calls
        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"

        # One fetcher for the page and its comments, so the page's image budget covers both.
        media_fetcher = self._create_confluence_media_fetcher(page_id, content_type, attachments_data=attachments_data)

        # 1. Parse ADF content to blocks (title prepended inside parse_adf as block 0)
        page_title_str = str(page_title).strip() if page_title and str(page_title).strip() else None
        blocks, block_groups = await parser.parse_adf(
            adf_content=adf_dict if adf_dict else {},
            media_fetcher=media_fetcher if adf_dict else None,
            parent_page_url=weburl,
            page_id=page_id,
            page_title=page_title_str,
        )

        # Track attachment IDs that are used (embedded images)
        used_attachment_ids: set[str] = set()

        # Helper function to resolve media from ADF to attachment IDs
        def resolve_attachment_id(media_info: dict[str, Any]) -> str | None:
            """Resolve ADF media node to attachment ID."""
            media_id = media_info.get("id")

            # Method 1: Match by fileId (most reliable)
            if media_id and attachments_data:
                for att in attachments_data:
                    if att.get("fileId") == media_id:
                        attachment_id = att.get("id")
                        if attachment_id and str(attachment_id) in (attachment_children_map or {}):
                            return str(attachment_id)

            # Method 2: Match by filename
            media_filename = media_info.get("filename", "") or media_info.get("alt", "")
            if media_filename and attachment_children_map:
                for att_id, child_record in attachment_children_map.items():
                    child_name = child_record.child_name
                    if child_name:
                        if child_name == media_filename or child_name.lower().strip() == media_filename.lower().strip():
                            return att_id

            return None

        def is_image_attachment(attachment_id: str) -> bool:
            """Check if attachment is an image based on MIME type."""
            _attachment_mime_types = attachment_mime_types or {}
            mime_type = _attachment_mime_types.get(attachment_id, "")
            return mime_type.startswith("image/")

        # Images left out of the body stay attachment records
        not_inlined = images_not_inlined(attachments_data or [])

        # Track which attachments were used as images in blocks
        if adf_dict:
            for media_info in extract_media_from_adf(adf_dict):
                attachment_id = resolve_attachment_id(media_info)
                if attachment_id and is_image_attachment(attachment_id) and attachment_id not in not_inlined:
                    used_attachment_ids.add(attachment_id)

        # 2. Fetch and process comments
        footer_comments: list[dict[str, Any]] = []
        inline_comments: list[dict[str, Any]] = []

        if record_type and await self._comments_indexed(record_type):
            # Fetch footer comments
            footer_comments = await self._fetch_page_comments_recursive(
                page_id=page_id,
                record_type=record_type,
                comment_type="footer"
            )

            # Fetch inline comments
            inline_comments = await self._fetch_page_comments_recursive(
                page_id=page_id,
                record_type=record_type,
                comment_type="inline"
            )

        # 3. Collect all unique author IDs from both inline and footer comments for batch fetching
        # Collect unique author IDs from both inline and footer comments
        author_ids_to_fetch = {
            comment.get("version", {}).get("authorId")
            for comment in (inline_comments or []) + (footer_comments or [])
        }
        author_ids_to_fetch.discard(None)
        author_ids_to_fetch.discard("Unknown")

        # Batch fetch user display names
        user_display_names: dict[str, str] = await self._batch_fetch_user_display_names(
            author_ids_to_fetch
        )

        # Attach inline comments to blocks with user display names
        if inline_comments:
            await parser.attach_inline_comments_to_blocks(
                blocks=blocks,
                inline_comments=inline_comments,
                media_fetcher=media_fetcher,
                parent_page_url=weburl,
                user_display_names=user_display_names,
            )

        # 4. Process footer comments as COMMENT_THREAD/COMMENT BlockGroups
        comment_attachment_ids: set[str] = set()

        # Inline threads are also anchored to their blocks above, but only block text is embedded,
        # so they get comment groups too, as footer threads do, to be searchable.
        comment_threads = [
            (kind, thread)
            for kind, comments in (("footer", footer_comments), ("inline", inline_comments))
            for thread in self._organize_confluence_comments_to_threads(comments or [])
        ]
        if comment_threads:
            for thread_comment_type, thread_comments in comment_threads:
                if not thread_comments:
                    continue

                # Get thread ID
                first_comment = thread_comments[0]
                parent_comment_id = first_comment.get("parentCommentId")
                first_comment_id = str(first_comment.get("id", ""))
                thread_id = str(parent_comment_id) if parent_comment_id else first_comment_id

                # Create COMMENT_THREAD BlockGroup
                thread_group_index = len(block_groups)
                thread_group = parser.create_comment_thread_group(
                    thread_id=thread_id,
                    group_index=thread_group_index,
                    comment_type=thread_comment_type,
                    page_title=page_title,
                    weburl=weburl,
                )
                block_groups.append(thread_group)

                # Create COMMENT BlockGroups for each comment in thread
                for comment in thread_comments:
                    comment_id = str(comment.get("id", ""))
                    comment_body_data = comment.get("body", {})
                    atlas_doc_format = comment_body_data.get("atlas_doc_format", {})
                    adf_value = atlas_doc_format.get("value")

                    if not adf_value:
                        continue

                    # Parse ADF
                    try:
                        if isinstance(adf_value, str):
                            comment_body_adf = json.loads(adf_value)
                        else:
                            comment_body_adf = adf_value
                    except json.JSONDecodeError:
                        self.logger.warning(f"Failed to parse ADF for comment {comment_id}")
                        continue

                    # Parse comment ADF directly to Blocks
                    comment_blocks, comment_block_groups = await parser.parse_adf(
                        adf_content=comment_body_adf,
                        media_fetcher=media_fetcher,
                        parent_page_url=weburl,
                        page_id=page_id,
                        page_title=None  # Don't add title for comments
                    )

                    if not comment_blocks and not comment_block_groups:
                        continue

                    # Who wrote it, and when (and what it is on, for an inline comment): without it
                    # the model reads a comment as part of the page.
                    header_index = len(blocks)
                    author_id = (comment.get("version") or {}).get("authorId")
                    blocks.append(Block(
                        id=str(uuid.uuid4()),
                        index=header_index,
                        parent_index=None,
                        type=BlockType.TEXT,
                        sub_type=BlockSubType.PARAGRAPH,
                        format=DataFormat.MARKDOWN,
                        data=ConfluenceBlockParser.comment_header_text(
                            user_display_names.get(author_id, "Unknown"),
                            parser.comment_created_at(comment),
                            (comment.get("properties") or {}).get("inlineOriginalSelection")
                            if thread_comment_type == "inline" else None,
                        ),
                        weburl=weburl,
                    ))

                    # Add comment blocks to main container (adjusting indices)
                    block_start_index = len(blocks)
                    block_group_index_offset = len(block_groups)
                    
                    for i, comment_block in enumerate(comment_blocks):
                        comment_block.index = block_start_index + i  # Update block index
                        
                        # Shift parent_index if it points to a BlockGroup
                        if comment_block.parent_index is not None:
                            comment_block.parent_index += block_group_index_offset
                        # else: Will be set to COMMENT group later
                        
                        blocks.append(comment_block)
                    block_end_index = len(blocks) - 1

                    # Add any block groups from comment (tables, etc.)
                    for comment_bg in comment_block_groups:
                        # Shift parent_index to account for existing block groups
                        if comment_bg.parent_index is not None:
                            comment_bg.parent_index += block_group_index_offset
                        
                        # Shift all indices in children
                        if comment_bg.children:
                            # Shift block indices in children
                            for block_range in comment_bg.children.block_ranges:
                                block_range.start += block_start_index
                                block_range.end += block_start_index
                            # Shift block group indices in children
                            for bg_range in comment_bg.children.block_group_ranges:
                                bg_range.start += block_group_index_offset
                                bg_range.end += block_group_index_offset
                        
                        # Update the block group's own index
                        comment_bg.index = len(block_groups)
                        block_groups.append(comment_bg)

                    # Build comment weburl
                    links = comment.get("_links", {})
                    comment_weburl_raw = links.get("webui", weburl)
                    comment_weburl = comment_weburl_raw
                    if (
                        comment_weburl
                        and not comment_weburl.startswith("http")
                        and weburl
                        and weburl.startswith("http")
                    ):
                        parsed = urlparse(weburl)
                        base_url = f"{parsed.scheme}://{parsed.netloc}/wiki"
                        comment_weburl = f"{base_url}{comment_weburl}"

                    # Get author info
                    version = comment.get("version", {})
                    author_id = version.get("authorId", "Unknown")

                    # Get file attachments used in this comment (images excluded)
                    comment_children: list[ChildRecord] = []
                    for media_info in extract_media_from_adf(comment_body_adf):
                        attachment_id = resolve_attachment_id(media_info)
                        if attachment_id and attachment_id in (attachment_children_map or {}):
                            comment_attachment_ids.add(attachment_id)
                            if not is_image_attachment(attachment_id) or attachment_id in not_inlined:
                                comment_children.append(attachment_children_map[attachment_id])

                    # Create BlockComment for metadata only
                    block_comment = BlockComment(
                        text="",
                        format=DataFormat.MARKDOWN,
                        author_id=author_id,
                        author_name=user_display_names.get(author_id, "Unknown"),
                        thread_id=thread_id,
                        weburl=parser._normalize_url(comment_weburl),
                        created_at=parser._parse_confluence_timestamp(parser.comment_created_at(comment)),
                    )

                    # Create COMMENT BlockGroup
                    comment_group_index = len(block_groups)
                    comment_group = parser.create_comment_group(
                        block_comment=block_comment,
                        group_index=comment_group_index,
                        parent_group_index=thread_group_index,
                        source_id=comment_id,
                        children_records=comment_children if comment_children else None,
                        block_indices=(header_index, block_end_index),
                    )
                    block_groups.append(comment_group)

                    # Set parent_index on top-level comment blocks (those without a parent yet)
                    # Blocks belonging to nested BlockGroups already have their parent set
                    for i in range(header_index, block_end_index + 1):
                        if blocks[i].parent_index is None:
                            blocks[i].parent_index = comment_group_index

                    # Set parent_index on top-level comment BlockGroups (tables, panels, etc.)
                    # that don't already have a parent (nested structures already have parent set)
                    for i in range(block_group_index_offset, comment_group_index):
                        if block_groups[i].parent_index is None:
                            block_groups[i].parent_index = comment_group_index

        # 5. Track attachments used in inline comments
        for block in blocks:
            if block.comments:
                for thread in block.comments:
                    for comment in thread:
                        if comment.attachments:
                            for att in comment.attachments:
                                comment_attachment_ids.add(att.id)

        # 6. Build non-comment attachments (excluding embedded images)
        remaining_attachments: list[ChildRecord] = []
        if attachment_children_map:
            for attachment_id, child_record in attachment_children_map.items():
                if attachment_id in comment_attachment_ids:
                    continue  # Used in comment
                if attachment_id in used_attachment_ids:
                    continue  # Embedded image - already in blocks as IMAGE
                remaining_attachments.append(child_record)

        # 7. Post-process blocks (fix indices, list numbering, grouping)
        parser.post_process_blocks(blocks, block_groups)

        # 8. Assign remaining attachments to appropriate parent
        # For now, assign to first block group (content wrapper) if it exists
        # Otherwise create a wrapper group
        if remaining_attachments:
            if block_groups:
                # Find or create content wrapper group
                content_group = None
                for bg in block_groups:
                    if bg.sub_type == GroupSubType.CONTENT:
                        content_group = bg
                        break

                if not content_group:
                    # Create content wrapper group
                    content_group = BlockGroup(
                        id=str(uuid.uuid4()),
                        index=0,
                        name=page_title,
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.CONTENT,
                        source_group_id=f"{page_id}_content",
                        weburl=parser._normalize_url(weburl),
                    )
                    block_groups.insert(0, content_group)

                    # Update indices after insertion
                    for i, bg in enumerate(block_groups):
                        bg.index = i

                    ConfluenceBlockParser.shift_parent_indices_after_group_insert(
                        blocks, block_groups, insert_at=0
                    )

                # Assign attachments
                content_group.children_records = remaining_attachments
            else:
                # No block groups - create a content wrapper
                content_group = BlockGroup(
                    id=str(uuid.uuid4()),
                    index=0,
                    name=page_title,
                    type=GroupType.TEXT_SECTION,
                    sub_type=GroupSubType.CONTENT,
                    source_group_id=f"{page_id}_content",
                    weburl=parser._normalize_url(weburl),
                    children_records=remaining_attachments,
                )
                block_groups.append(content_group)

        # 9. Update parent relationships after post-processing
        ConfluenceBlockParser.sync_table_row_links(blocks, block_groups)
        ConfluenceBlockParser.sync_nested_table_group_links(block_groups)

        # Build children references for block groups
        blockgroup_children_map: dict[int, list[int]] = defaultdict(list)
        block_children_map: dict[int, list[int]] = defaultdict(list)

        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)

        for b in blocks:
            if b.parent_index is not None:
                block_children_map[b.parent_index].append(b.index)

        # Populate children arrays
        for bg in block_groups:
            child_block_indices = []
            child_bg_indices = []

            if bg.index in blockgroup_children_map:
                child_bg_indices = sorted(blockgroup_children_map[bg.index])

            if bg.index in block_children_map:
                child_block_indices = sorted(block_children_map[bg.index])

            # Only update if we have children and group doesn't already have children set
            if (child_block_indices or child_bg_indices) and not bg.children:
                bg.children = BlockGroupChildren.from_indices(
                    block_indices=child_block_indices,
                    block_group_indices=child_bg_indices
                )

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def _fetch_page_data_with_adf(self, page_id: str, record_type: RecordType) -> dict[str, Any]:
        """Fetch page/blogpost with ADF format instead of HTML."""
        datasource = await self._get_fresh_datasource()

        if record_type == RecordType.CONFLUENCE_PAGE:
            response = await datasource.get_page_content_v2(
                page_id=page_id,
                body_format="atlas_doc_format"  # ADF format
            )
        elif record_type == RecordType.CONFLUENCE_BLOGPOST:
            response = await datasource.get_blogpost_content_v2(
                blogpost_id=page_id,
                body_format="atlas_doc_format"  # ADF format
            )
        else:
            raise ValueError(f"Unsupported record type: {record_type}")

        if not response:
            raise not_found_at_source(self.display_name)

        if response.status != HttpStatusCode.SUCCESS.value:
            raise map_source_status(response.status, connector=self.display_name)

        return response.json()

    async def _process_page_attachments_for_children(
        self,
        attachments_data: list[dict[str, Any]],
        page_id: str,
        page_node_id: str,
        space_id: str,
        page_weburl: str | None,
        attachment_api_base_url: str | None = None,
    ) -> dict[str, ChildRecord]:
        """Link a page's stored attachment records as children of its blocks.

        Read only: the sync owns attachment records (it applies the attachment indexing
        filters and leaves images shown in the page out), so streaming never creates one.
        An attachment added since the last sync is linked once the sync has stored it.
        """
        attachment_children_map: dict[str, ChildRecord] = {}
        for attachment in attachments_data:
            attachment_id = attachment.get("id")
            if not attachment_id:
                continue
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=str(attachment_id),
            )
            if existing_record:
                attachment_children_map[str(attachment_id)] = ChildRecord(
                    child_type=ChildType.RECORD,
                    child_id=existing_record.id,
                    child_name=existing_record.record_name,
                )
        return attachment_children_map

    async def _fetch_attachment_content(self, record: Record) -> AsyncGenerator[bytes, None]:
        """
        Stream attachment file content from Confluence Cloud.

        Args:
            record: Record with external_record_id and parent_external_record_id

        Yields:
            bytes: File content in 8KB chunks

        Raises:
            HTTPException: If attachment not found or download fails
        """
        try:
            attachment_id = record.external_record_id
            parent_page_id = record.parent_external_record_id

            if not attachment_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"No attachment ID available for record {record.id}"
                )

            if not parent_page_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"No parent page ID available for attachment {attachment_id}"
                )

            # Use datasource to stream attachment content
            datasource = await self._get_fresh_datasource()
            async for chunk in datasource.download_attachment(
                parent_page_id=parent_page_id,
                attachment_id=attachment_id
            ):
                yield chunk

        except HTTPException:
            raise
        except AtlassianQuotaExhausted as e:
            raise self._quota_http_error(e) from e
        except Exception as e:
            self.logger.error(f"Failed to download attachment {record.external_record_id}: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (delegates to full sync)."""
        await self.run_sync()

    async def reindex_records(self, records: list[Record]) -> None:
        """Reindex records; see ``_reindex_records``."""
        self._in_use += 1
        try:
            await self._reindex_records(records)
        finally:
            await self._release()

    async def _reindex_records(self, records: list[Record]) -> None:
        """Reindex a list of Confluence records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor

        Args:
            records: List of properly typed Record instances (WebpageRecord, FileRecord, etc.)
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Confluence records")

            # Ensure external clients are initialized
            if not self.external_client or not self.data_source:
                self.logger.error("External API clients not initialized. Call init() first.")
                raise Exception("External API clients not initialized. Call init() first.")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []
            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(org_id, record)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except GoneAtSource:
                    # Re-indexing it would only fail; the next sync's removal scan deletes it.
                    self.logger.info(f"Not re-indexing {record.external_record_id}: it is gone from Confluence")
                    continue
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB and publish updateRecord events for records that changed at source
            if updated_records:
                for updated_record, permissions in updated_records:
                    # Update record content and publish updateRecord event
                    await self.data_entities_processor.on_record_content_update(updated_record)

                    # Update permissions if they exist
                    if permissions or updated_record.rewrite_permissions:
                        await self.data_entities_processor.on_updated_record_permissions(
                            updated_record, list(permissions or [])
                        )

                self.logger.info(f"Published update events for {len(updated_records)} records that changed at source")

            # Reindex non-updated records (preserving existing mime types)
            if non_updated_records:
                # A re-index is asked for, so a stored HTML page is indexed as blocks, with its comments.
                fixed_records, ok_records = await self._fix_legacy_mime_types(non_updated_records)
                all_reindex_records = fixed_records + ok_records
                
                if all_reindex_records:
                    await self.data_entities_processor.reindex_existing_records(all_reindex_records)
                    self.logger.info(f"Published reindex events for {len(all_reindex_records)} non-updated records")
            # Every re-indexed page: comment indexing may have just been turned on for pages already stored as blocks.
            await self._drop_legacy_comment_records(
                self._saved_as_blocks([r for r, _ in updated_records] + non_updated_records, every_page=True)
            )
        except Exception as e:
            self.logger.error(f"Error during Confluence reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:  # raises GoneAtSource for a 404
        """Fetch record from source and return data for reindexing.

        Supports: pages, blogposts, attachments, and comments.

        Args:
            org_id: Organization ID
            record: Record to check

        Returns:
            Tuple of (Record, List[Permission]) if updated, None if not updated or error
        """
        try:
            if record.record_type == RecordType.CONFLUENCE_PAGE:
                return await self._check_and_fetch_updated_page(org_id, record)
            elif record.record_type == RecordType.CONFLUENCE_BLOGPOST:
                return await self._check_and_fetch_updated_blogpost(org_id, record)
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(org_id, record)
            elif record.record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT]:
                return await self._check_and_fetch_updated_comment(org_id, record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except GoneAtSource:
            raise
        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_page(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch page from source for reindexing."""
        try:
            page_id = record.external_record_id

            # Fetch page from source using v2 API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_page_content_v2(
                page_id=page_id,
                body_format="storage"
            )

            if response is not None and response.status == HttpStatusCode.NOT_FOUND.value:
                raise GoneAtSource(page_id)
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Page {page_id} could not be read at source")
                return None

            page_data = response.json()

            # Check if version changed
            current_version = page_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Page {page_id} has no version number")
                return None

            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Page {page_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Page {page_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform page to WebpageRecord with existing record context
            webpage_record = self._transform_to_webpage_record(
                page_data,
                RecordType.CONFLUENCE_PAGE,
                existing_record=record
            )
            if not webpage_record:
                return None

            # Fetch fresh permissions
            permissions = await self._fetch_page_permissions(page_id)
            if permissions is None:
                self.logger.warning(f"Restrictions for {page_id} could not be read; reindexing what is stored")
                return None
            # A READ restriction keeps inheriting and is recorded as RESTRICTED
            # instead; EDIT-only stays STRICT.
            webpage_record.access_rule = (
                AccessRule.RESTRICTED
                if any(p.type == PermissionType.READ for p in permissions)
                else AccessRule.STRICT
            )
            webpage_record.rewrite_permissions = True

            return (webpage_record, permissions)

        except GoneAtSource:
            raise
        except Exception as e:
            self.logger.error(f"Error fetching page {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_blogpost(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch blogpost from source for reindexing."""
        try:
            blogpost_id = record.external_record_id

            datasource = await self._get_fresh_datasource()
            response = await datasource.get_blog_post_by_id(
                id=int(blogpost_id),
                body_format="storage"
            )

            if response is not None and response.status == HttpStatusCode.NOT_FOUND.value:
                raise GoneAtSource(blogpost_id)
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Blogpost {blogpost_id} could not be read at source")
                return None

            blogpost_data = response.json()

            # Check if version changed
            current_version = blogpost_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Blogpost {blogpost_id} has no version number")
                return None

            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Blogpost {blogpost_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Blogpost {blogpost_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform blogpost to WebpageRecord with existing record context
            webpage_record = self._transform_to_webpage_record(
                blogpost_data,
                RecordType.CONFLUENCE_BLOGPOST,
                existing_record=record
            )
            if not webpage_record:
                return None

            # Fetch fresh permissions
            permissions = await self._fetch_page_permissions(blogpost_id)
            if permissions is None:
                self.logger.warning(f"Restrictions for {blogpost_id} could not be read; reindexing what is stored")
                return None
            # A READ restriction keeps inheriting and is recorded as RESTRICTED
            # instead; EDIT-only stays STRICT.
            webpage_record.access_rule = (
                AccessRule.RESTRICTED
                if any(p.type == PermissionType.READ for p in permissions)
                else AccessRule.STRICT
            )
            webpage_record.rewrite_permissions = True

            return (webpage_record, permissions)

        except GoneAtSource:
            raise
        except Exception as e:
            self.logger.error(f"Error fetching blogpost {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_attachment(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch attachment from source for reindexing."""
        try:
            attachment_id = record.external_record_id
            parent_page_id = record.parent_external_record_id

            if not parent_page_id:
                self.logger.warning(f"Attachment {attachment_id} has no parent page ID")
                return None

            # Get parent page's internal record ID
            parent_node_id = None
            parent_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=parent_page_id
            )
            if parent_record:
                parent_node_id = parent_record.id

            # Fetch attachment metadata from source using v2 API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_attachment_by_id(
                id=attachment_id,
                include_version=True
            )

            if response is not None and response.status == HttpStatusCode.NOT_FOUND.value:
                raise GoneAtSource(attachment_id)
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Attachment {attachment_id} could not be read at source")
                return None

            attachment_data = response.json()
            single_attachment_base = attachment_data.get("_links", {}).get("base")

            # Check if version changed
            current_version = attachment_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Attachment {attachment_id} has no version number")
                return None

            # Compare versions using external_revision_id
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Attachment {attachment_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Get space_id from parent page or use existing
            parent_space_id = record.external_record_group_id

            # Transform attachment to FileRecord with existing record context
            attachment_record = self._transform_to_attachment_file_record(
                attachment_data,
                parent_page_id,
                parent_space_id,
                existing_record=record,
                parent_node_id=parent_node_id,
                attachment_api_base_url=single_attachment_base,
            )

            if not attachment_record:
                return None

            # Attachments inherit permissions from parent page - fetch page permissions
            permissions = await self._fetch_page_permissions(parent_page_id)
            if permissions is None:
                self.logger.warning(f"Restrictions for {parent_page_id} could not be read; reindexing what is stored")
                return None
            # Attachments follow the page's access through inheritance.
            attachment_record.inherit_permissions = True
            attachment_record.rewrite_permissions = True
            return (attachment_record, [])

        except GoneAtSource:
            raise
        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    async def cleanup(self) -> None:
        """Close the HTTP client, so its pooled connections go when the connector does."""
        self.logger.info("Cleaning up Confluence connector resources")
        if self._in_use:
            # The router evicts a cached instance on a settings change; work on it still needs the client.
            self.logger.info("Work is running on this connector; its HTTP client closes when it ends")
            self._close_after_sync = True
            return
        client = self.external_client.get_client() if self.external_client else None
        if client is not None and hasattr(client, "close"):
            try:
                await client.close()
            except Exception as e:
                self.logger.debug(f"Closing the Confluence HTTP client failed: {e}")

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for Confluence filters with cursor-based pagination.

        Supports:
        - space_keys: All available Confluence spaces
        - page_ids: Pages (with CQL fuzzy search when search term provided)
        - blogpost_ids: Blogposts (with CQL fuzzy search when search term provided)

        Args:
            filter_key: Filter field name
            page: Page number (for API compatibility, not used with cursor)
            limit: Items per page
            search: Search text to filter options (uses CQL fuzzy matching)
            cursor: Cursor for pagination (Confluence uses cursor-based pagination)

        Returns:
            FilterOptionsResponse with options and pagination metadata
        """
        if filter_key == "space_keys":
            return await self._get_space_options(page, limit, search, cursor)
        elif filter_key == "page_ids":
            return await self._get_page_options(page, limit, search, cursor)
        elif filter_key == "blogpost_ids":
            return await self._get_blogpost_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_space_options(
        self,
        page: int,
        limit: int,
        search: str | None,
        cursor: str | None
    ) -> FilterOptionsResponse:
        """Fetch available Confluence spaces with cursor-based pagination.

        Uses CQL search for fuzzy matching when search term is provided,
        otherwise uses v2 API for listing all spaces.
        """
        # Get fresh datasource with refreshed OAuth token
        datasource = await self._get_fresh_datasource()

        spaces_list = []
        next_cursor = None

        if search:
            # Determine pagination parameters based on token type
            start_offset, cursor_token = self._split_pagination_token(cursor)
            
            # Use CQL search for fuzzy matching on space name/key
            spaces_response = await datasource.search_spaces_cql(
                search_term=search,
                limit=limit,
                start=start_offset,
                cursor=cursor_token
            )

            if not spaces_response or spaces_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search spaces: HTTP {spaces_response.status if spaces_response else 'No response'}"
                )

            response_data = spaces_response.json()

            # CQL search returns results with nested 'space' object
            # Note: CQL response space object has key and name but NOT id
            for result in response_data.get("results", []):
                space = result.get("space", {})
                space_key = space.get("key")
                space_name = space.get("name") or space.get("title")

                # CQL response doesn't include space id, so we use key as identifier
                if space_key and space_name:
                    spaces_list.append({
                        "id": space_key,  # Use key as id since CQL doesn't return id
                        "key": space_key,
                        "name": space_name
                    })

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")
        else:
            # Use v2 API to list all spaces (no search term)
            spaces_response = await datasource.get_spaces(
                cursor=cursor,
                limit=limit,
                status="current"
            )

            if not spaces_response or spaces_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch spaces: HTTP {spaces_response.status if spaces_response else 'No response'}"
                )

            response_data = spaces_response.json()
            spaces_list = response_data.get("results", [])

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")

        # Convert to FilterOption objects
        # Use key as id since the filter is "space_keys" and backend expects keys
        options = [
            FilterOption(
                id=space.get("key"),  # Frontend will use this value, backend expects keys
                label=space.get("name")
            )
            for space in spaces_list
            if space.get("key") and space.get("name")
        ]

        # Return success response
        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def _get_page_options(
        self,
        page: int,
        limit: int,
        search: str | None,
        cursor: str | None
    ) -> FilterOptionsResponse:
        """Fetch pages with cursor-based pagination.

        Uses CQL search for fuzzy title matching when search term is provided,
        otherwise uses v2 API for listing all pages.
        """
        # Get fresh datasource with refreshed OAuth token
        datasource = await self._get_fresh_datasource()

        pages_list = []
        next_cursor = None

        if search:
            # Determine pagination parameters based on token type
            start_offset, cursor_token = self._split_pagination_token(cursor)
            
            # Use CQL search for fuzzy title matching
            pages_response = await datasource.search_pages_cql(
                search_term=search,
                limit=limit,
                start=start_offset,
                cursor=cursor_token
            )

            if not pages_response or pages_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search pages: HTTP {pages_response.status if pages_response else 'No response'}"
                )

            response_data = pages_response.json()

            # CQL search returns results with nested 'content' object
            for result in response_data.get("results", []):
                content = result.get("content", {})
                if content.get("id") and content.get("title") and content.get("type") == "page":
                    pages_list.append(content)

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")
        else:
            # Use v2 API to list all pages (no search term)
            pages_response = await datasource.get_pages(
                cursor=cursor,
                limit=limit,
                status=["current"]
            )

            if not pages_response or pages_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch pages: HTTP {pages_response.status if pages_response else 'No response'}"
                )

            response_data = pages_response.json()
            pages_list = response_data.get("results", [])

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")

        # Convert to FilterOption objects
        options = [
            FilterOption(
                id=p.get("id"),
                label=p.get('title')
            )
            for p in pages_list
            if p.get("id") and p.get("title")
        ]

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def _get_blogpost_options(
        self,
        page: int,
        limit: int,
        search: str | None,
        cursor: str | None
    ) -> FilterOptionsResponse:
        """Fetch blogposts with cursor-based pagination.

        Uses CQL search for fuzzy title matching when search term is provided,
        otherwise uses v2 API for listing all blogposts.
        """
        # Get fresh datasource with refreshed OAuth token
        datasource = await self._get_fresh_datasource()

        blogposts_list = []
        next_cursor = None

        if search:
            # Determine pagination parameters based on token type
            start_offset, cursor_token = self._split_pagination_token(cursor)
            
            # Use CQL search for fuzzy title matching
            blogposts_response = await datasource.search_blogposts_cql(
                search_term=search,
                limit=limit,
                start=start_offset,
                cursor=cursor_token
            )

            if not blogposts_response or blogposts_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search blogposts: HTTP {blogposts_response.status if blogposts_response else 'No response'}"
                )

            response_data = blogposts_response.json()

            # CQL search returns results with nested 'content' object
            for result in response_data.get("results", []):
                content = result.get("content", {})
                if content.get("id") and content.get("title") and content.get("type") == "blogpost":
                    blogposts_list.append(content)

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")
        else:
            # Use v2 API to list all blogposts (no search term)
            blogposts_response = await datasource.get_blog_posts(
                cursor=cursor,
                limit=limit,
                status=["current"]
            )

            if not blogposts_response or blogposts_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch blogposts: HTTP {blogposts_response.status if blogposts_response else 'No response'}"
                )

            response_data = blogposts_response.json()
            blogposts_list = response_data.get("results", [])

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")

        # Convert to FilterOption objects
        options = [
            FilterOption(
                id=bp.get("id"),
                label=bp.get('title')
            )
            for bp in blogposts_list
            if bp.get("id") and bp.get("title")
        ]

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def _fetch_comment_data(self, record: Record) -> dict[str, Any] | None:
        """Fetch comment data from Confluence API.
        
        Args:
            record: CommentRecord to fetch
            
        Returns:
            Comment data dict or None if not found
        """
        try:
            comment_id = record.external_record_id
            comment_type = record.record_type
            
            datasource = await self._get_fresh_datasource()
            
            # Determine which API to call based on comment type
            if comment_type == RecordType.COMMENT:
                # Footer comment
                response = await datasource.get_footer_comment_by_id(
                    comment_id=int(comment_id),
                    body_format="storage"
                )
            elif comment_type == RecordType.INLINE_COMMENT:
                # Inline comment
                response = await datasource.get_inline_comment_by_id(
                    comment_id=int(comment_id),
                    body_format="storage"
                )
            else:
                self.logger.error(f"Unsupported comment type: {comment_type}")
                return None
            
            if not response:
                self.logger.warning(f"Comment {comment_id} not found at source")
                return None

            # Only a real 404 means "gone"; anything else (expired token,
            # revoked permission, rate limit) must keep its own status.
            if response.status == HttpStatusCode.NOT_FOUND.value:
                self.logger.warning(f"Comment {comment_id} not found at source")
                return None

            if response.status != HttpStatusCode.SUCCESS.value:
                raise map_source_status(response.status, connector=self.display_name)

            return response.json()

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch comment {record.external_record_id}: {e}")
            raise to_stream_error(e, connector=self.display_name) from e

    async def _batch_fetch_user_display_names(
        self, 
        author_ids: set[str]
    ) -> dict[str, str]:
        """
        Batch fetch user display names for a set of author IDs.
        
        Args:
            author_ids: Set of Confluence account IDs (authorId values)
            
        Returns:
            Dictionary mapping author_id -> display_name
        """
        user_map: dict[str, str] = {}
        
        if not author_ids:
            return user_map
        
        # Get fresh datasource instance
        datasource = await self._get_fresh_datasource()
        
        # Fetch users in parallel
        tasks = []
        for author_id in author_ids:
            task = datasource.get_user_v1(account_id=author_id)
            tasks.append((author_id, task))
        
        results = await asyncio.gather(
            *[task for _, task in tasks], 
            return_exceptions=True
        )
        
        for (author_id, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                self.logger.warning(f"Failed to fetch user {author_id}: {result}")
                user_map[author_id] = "Unknown"
                continue
            
            try:
                if result.status == HttpStatusCode.SUCCESS.value:
                    user_data = result.json()
                    display_name = user_data.get("displayName", "Unknown")
                    user_map[author_id] = display_name
                else:
                    self.logger.warning(f"Failed to fetch user {author_id}: HTTP {result.status}")
                    user_map[author_id] = "Unknown"
            except Exception as e:
                self.logger.warning(f"Failed to parse user data for {author_id}: {e}")
                user_map[author_id] = "Unknown"
        
        return user_map
    
    async def _check_and_fetch_updated_comment(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch comment from source for reindexing.
        
        Args:
            org_id: Organization ID
            record: Comment record to check
            
        Returns:
            Tuple of (CommentRecord, permissions) if updated, None otherwise
        """
        try:
            comment_data = await self._fetch_comment_data(record)
            if not comment_data:
                self.logger.warning(f"Comment {record.external_record_id} not found at source, may have been deleted")
                return None
            
            # Check if version changed
            current_version = comment_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Comment {record.external_record_id} has no version number")
                return None
            
            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Comment {record.external_record_id} has not changed at source (version {current_version})")
                return None
            
            self.logger.info(f"Comment {record.external_record_id} has changed at source (version {record.external_revision_id} -> {current_version})")
            
            # A reply's parent is its parent comment, so the page comes from the payload.
            page_id = comment_data.get("pageId") or comment_data.get("blogPostId")
            parent_comment_id = comment_data.get("parentCommentId")
            if record.parent_record_type == RecordType.COMMENT:
                parent_comment_id = parent_comment_id or record.parent_external_record_id
            elif not page_id:
                page_id = record.parent_external_record_id
            if not page_id:
                # Without the page its restrictions can't be read; saving would open the comment to the space.
                self.logger.warning(f"Comment {record.external_record_id}: page not known; reindexing what is stored")
                return None

            # Transform comment to CommentRecord with existing record context
            comment_record = self._transform_to_comment_record(
                comment_data,
                str(page_id),
                record.external_record_group_id,
                "footer" if record.record_type == RecordType.COMMENT else "inline",
                str(parent_comment_id) if parent_comment_id else None,
                existing_record=record,
                parent_node_id=record.parent_node_id
            )

            if not comment_record:
                return None

            # Comments get their page's access
            permissions = await self._fetch_page_permissions(str(page_id))
            if permissions is None:
                self.logger.warning(f"Restrictions for {page_id} could not be read; reindexing what is stored")
                return None
            # Comments follow the page's access through inheritance.
            comment_record.inherit_permissions = True
            comment_record.rewrite_permissions = True
            return (comment_record, [])
            
        except Exception as e:
            self.logger.error(f"Error fetching comment {record.external_record_id}: {e}")
            return None
    
    async def _fix_legacy_mime_types(self, records: list[Record]) -> tuple[list[Record], list[Record]]:
        """
        Fix legacy records with text/html mime type by updating to application/blocks.
        
        Pages and blogposts created before the ADF-to-blocks migration have text/html
        stored in the DB. When stream_record is called, it returns blocks JSON, but the
        Kafka payload uses the stored mime type, causing indexing to treat blocks as HTML.
        
        Args:
            records: List of records to check and fix
            
        Returns:
            Tuple of (records_needing_fix, records_not_needing_fix)
        """
        records_to_fix = []
        records_ok = []
        
        for record in records:
            # Check if this is a page/blogpost with legacy HTML mime type
            is_page_or_blogpost = record.record_type in [
                RecordType.CONFLUENCE_PAGE,
                RecordType.CONFLUENCE_BLOGPOST
            ]
            has_html_mime = record.mime_type == MimeTypes.HTML.value
            
            if is_page_or_blogpost and has_html_mime:
                self._now_blocks[record.external_record_id] = record.record_type
                self.logger.info(
                    f"Detected legacy HTML mime type for {record.record_type.value} "
                    f"{record.record_name} ({record.external_record_id}), updating to BLOCKS"
                )
                # Update the mime type to match what stream_record returns
                record.mime_type = MimeTypes.BLOCKS.value
                # The preview has no renderer for blocks; the page opens at its link instead.
                record.preview_renderable = False
                records_to_fix.append(record)
            else:
                records_ok.append(record)
        
        # Persist the mime type updates to database
        if records_to_fix:
            self.logger.info(f"Updating {len(records_to_fix)} legacy records to application/blocks mime type")
            await self.data_entities_processor.batch_upsert_records(records_to_fix)
            self.logger.info(f"Successfully updated mime types for {len(records_to_fix)} records")
        
        return records_to_fix, records_ok
    
    def _transform_to_comment_record(
        self,
        comment_data: dict[str, Any],
        page_id: str,
        parent_space_id: Optional[str],
        comment_type: str,
        parent_comment_id: Optional[str],
        base_url: Optional[str] = None,
        existing_record: Optional[Record] = None,
        parent_node_id: Optional[str] = None
    ) -> Optional[CommentRecord]:
        """Transform Confluence comment data to CommentRecord entity.

        Args:
            comment_data: Raw comment data from Confluence API
            page_id: Parent page external_record_id
            parent_space_id: Space ID from parent page
            comment_type: "footer" or "inline"
            parent_comment_id: Parent comment ID (None for top-level comments)
            base_url: Base URL from response level (v2 API) - if None, will extract from _links.self (v1 API)
            existing_record: Optional existing record to check for updates
            parent_node_id: Internal record ID of parent page

        Returns:
            CommentRecord object or None if transformation fails
        """
        try:
            comment_id = comment_data.get("id")
            title = comment_data.get("title", "")

            if not comment_id:
                return None

            # Extract author accountId
            author = comment_data.get("version", {}).get("authorId")
            if not author:
                self.logger.warning(f"Comment {comment_id} has no author - skipping")
                return None

            # Parse timestamps
            source_created_at = None

            created_at_str = comment_data.get("version", {}).get("createdAt")
            if created_at_str:
                source_created_at = self._parse_confluence_datetime(created_at_str)

            # Extract resolution status (for inline comments)
            resolution_status = None
            if comment_type == "inline":
                is_resolved = comment_data.get("resolutionStatus", False)
                resolution_status = "resolved" if is_resolved else "open"

            # Extract inline original selection (for inline comments)
            inline_original_selection = None
            if comment_type == "inline":
                inline_properties = comment_data.get("properties", {})
                if inline_properties:
                    inline_original_selection = inline_properties.get("inlineOriginalSelection")

            # Determine parent record ID and type
            parent_external_record_id = parent_comment_id if parent_comment_id else page_id
            parent_record_type = RecordType.COMMENT if parent_comment_id else RecordType.WEBPAGE

            # Determine record ID and version
            is_new = existing_record is None
            comment_record_id = str(uuid.uuid4()) if is_new else existing_record.id

            version_number = comment_data.get("version", {}).get("number", 0)

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            # Construct web URL for comment
            links = comment_data.get("_links", {})
            web_url = self._construct_web_url(links, base_url)

            return CommentRecord(
                id=comment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=title,
                record_type=RecordType.INLINE_COMMENT if comment_type == "inline" else RecordType.COMMENT,
                external_record_id=comment_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                mime_type=MimeTypes.HTML.value,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                external_record_group_id=parent_space_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                source_created_at=source_created_at,
                source_updated_at=source_created_at,
                weburl=web_url,
                author_source_id=author,
                resolution_status=resolution_status,
                comment_selection=inline_original_selection,
                is_dependent_node=True,
                parent_node_id=parent_node_id,
            )

        except Exception as e:
            self.logger.error(f"Failed to transform comment: {e}")
            return None

    async def handle_webhook_notification(self, notification: dict) -> None:
        """Handle webhook notifications (not implemented)."""
        self.logger.warning("Webhook notifications not yet supported for Confluence")
        pass

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> "ConfluenceConnector":
        """Factory method to create a Confluence connector instance."""
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
