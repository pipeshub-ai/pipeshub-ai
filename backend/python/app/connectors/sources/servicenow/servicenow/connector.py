"""
ServiceNow Knowledge Base Connector

This connector syncs knowledge base articles, categories, attachments, and permissions
from ServiceNow into the PipesHub AI platform.

Synced Entities:
- Users and Groups (for permissions)
- Roles (for role-based permissions)
- Organizational Entities (companies, departments, locations, cost centers)
- Knowledge Bases (containers)
- Categories (hierarchy)
- KB Articles (content)
- Attachments (files)
"""

import mimetypes
import re
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, NoReturn, Optional, Tuple, TypeVar

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import (
    AuthFieldKeys,
    CommonStrings,
    CONNECTOR_EMAIL_IDENTITY_INFO,
    ConnectorRegistryCategories,
    IconPaths,
    OAuthConfigKeys,
    OAuthDefaults,
    OAuthRedirectPaths,
)
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorInitError,
    oauth_app_not_linked_error,
)
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import FailedItems, SyncDataPointType, SyncPoint
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.types import FieldType
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.servicenow.common.apps import ServicenowApp
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.servicenow.servicenow import (
    ServiceNowRateLimited,
    ServiceNowRateLimitPolicy,
    ServiceNowRESTClientViaOAuthAuthorizationCode,
)
from app.sources.external.servicenow.servicenow import ServiceNowDataSource
from app.sources.external.servicenow.models import (
    AttachmentMetadata,
    RawPermission,
    ServiceNowAPIError,
    ServiceNowAuthError,
    SysUserGroup,
    SysUserGroupMembership,
    SysUserRole,
    SysUserRoleAssignment,
    SysUserRoleContains,
    TableAPIRecord,
    TableAPIResponse,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import datetime_to_epoch_ms
from app.connectors.sources.servicenow.servicenow.constants import (
    CRITERIA_ALLOW_INCLUDES_CHILD_GROUPS,
    ORGANIZATIONAL_ENTITIES,
    OrganizationalEntityConfig,
    ServiceNowConfigPaths,
    ServiceNowConnectorMetadata,
    ServiceNowDefaults,
    ServiceNowDictKeys,
    ServiceNowFields,
    ServiceNowPrefixes,
    ServiceNowQueryValues,
    ServiceNowRoles,
    ServiceNowSyncPointKeys,
    ServiceNowTables,
    ServiceNowURLPatterns,
)



T = TypeVar("T")

# Articles stored before they were keyed by number carry a version's sys_id (32 hex) instead.
_LEGACY_ARTICLE_KEY = re.compile(r"^[0-9a-f]{32}$")

# The kb_knowledge fields an article is written from
_ARTICLE_FIELDS = [
    ServiceNowFields.SYS_ID,
    ServiceNowFields.NUMBER,
    ServiceNowFields.SHORT_DESCRIPTION,
    ServiceNowFields.TEXT,
    ServiceNowFields.AUTHOR,
    ServiceNowFields.KB_KNOWLEDGE_BASE,
    ServiceNowFields.KB_CATEGORY,
    ServiceNowFields.WORKFLOW_STATE,
    ServiceNowFields.ACTIVE,
    ServiceNowFields.PUBLISHED,
    ServiceNowFields.CAN_READ_USER_CRITERIA,
    ServiceNowFields.CANNOT_READ_USER_CRITERIA,
    ServiceNowFields.SYS_CREATED_ON,
    ServiceNowFields.SYS_UPDATED_ON,
]


class _CriteriaUnreadable(Exception):
    """A user criteria (Can Read or Cannot Read) could not be read or resolved to users;
    the grants it governs are left as stored."""


def _raise_if_run_stop(error: BaseException | None) -> None:
    """Raise the rate limit or auth failure behind ``error``, if any. Every later call would fail
    the same way, so the run ends there (checkpoints unmoved) instead of failing item by item."""
    while error is not None:
        if isinstance(error, (ServiceNowRateLimited, ServiceNowAuthError)):
            raise error
        error = error.__cause__


@ConnectorBuilder(ServiceNowConnectorMetadata.NAME)\
    .in_group(ServiceNowConnectorMetadata.NAME)\
    .with_description("Sync knowledge base articles, categories, and permissions from ServiceNow")\
    .with_categories([ConnectorRegistryCategories.KNOWLEDGE_MANAGEMENT])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_resilience_config(rate_limit=10, max_retries=3, base_delay=1.0, max_delay=60.0)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name=ServiceNowConnectorMetadata.NAME,
            authorize_url="https://example.service-now.com/oauth_auth.do",
            token_url="https://example.service-now.com/oauth_token.do",
            redirect_uri=OAuthRedirectPaths.CONNECTOR_CALLBACK.format(
                connector_name=ServiceNowConnectorMetadata.NAME
            ),
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=["useraccount"],
                agent=[]
            ),
            fields=[
                AuthField(
                    name=AuthFieldKeys.INSTANCE_URL,
                    display_name="ServiceNow Instance URL",
                    placeholder="https://your-instance.service-now.com",
                    description="Your ServiceNow instance URL (e.g., https://dev12345.service-now.com)",
                    field_type=FieldType.URL.value,
                    required=True,
                    max_length=OAuthDefaults.MAX_URL_LENGTH,
                ),
                AuthField(
                    name=AuthFieldKeys.AUTHORIZE_URL,
                    display_name="ServiceNow Authorize URL",
                    placeholder="https://your-instance.service-now.com/oauth_auth.do",
                    description="Your ServiceNow authorize URL (e.g., https://dev12345.service-now.com/oauth_auth.do)",
                    field_type=FieldType.URL.value,
                    required=True,
                    max_length=OAuthDefaults.MAX_URL_LENGTH,
                ),
                AuthField(
                    name=AuthFieldKeys.TOKEN_URL,
                    display_name="ServiceNow Token URL",
                    placeholder="https://your-instance.service-now.com/oauth_token.do",
                    description="Your ServiceNow token URL (e.g., https://dev12345.service-now.com/oauth_token.do)",
                    field_type=FieldType.URL.value,
                    required=True,
                    max_length=OAuthDefaults.MAX_URL_LENGTH,
                ),
                CommonFields.client_id(f"{ServiceNowConnectorMetadata.NAME} OAuth Application Registry"),
                CommonFields.client_secret(f"{ServiceNowConnectorMetadata.NAME} OAuth Application Registry")
            ],
            icon_path=IconPaths.connector_icon(Connectors.SERVICENOW.value),
            app_group=ServiceNowConnectorMetadata.NAME,
            app_description=f"OAuth application for accessing {ServiceNowConnectorMetadata.NAME} API and knowledge base services",
            app_categories=[ConnectorRegistryCategories.KNOWLEDGE_MANAGEMENT]
        )
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.SERVICENOW.value))
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "ServiceNow OAuth Setup",
                "https://docs.servicenow.com/bundle/latest/page/administer/security/concept/c_OAuthApplications.html",
                "Setup"
            )
        )
        .add_documentation_link(
            DocumentationLink(
                "Pipeshub Documentation",
                "https://docs.pipeshub.com/connectors/servicenow/servicenow",
                "Pipeshub"
            )
        )
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()

class ServiceNowConnector(BaseConnector):
    """
    ServiceNow Knowledge Base Connector

    This connector syncs ServiceNow Knowledge Base data including:
    - Knowledge bases and categories
    - KB articles with metadata
    - Article attachments
    - User and group permissions
    """

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
        """
        Initialize the ServiceNow KB Connector.

        Args:
            logger: Logger instance
            data_entities_processor: Processor for handling entities
            data_store_provider: Data store provider
            config_service: Configuration service
        """
        super().__init__(
            ServicenowApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        # The declared resilience as a policy that stops on an hourly rate limit instead of
        # retrying into it. Built once, so a re-init keeps an armed stop (see BaseConnector.resilience).
        self._resilience = ServiceNowRateLimitPolicy.from_config(
            self.connector_metadata.get("resilienceConfig"), name=str(self.connector_name), logger=self.logger
        )
        self._resilience_loaded = True

        # ServiceNow API client instances
        self.servicenow_client: Optional[ServiceNowRESTClientViaOAuthAuthorizationCode] = None
        self.servicenow_datasource: Optional[ServiceNowDataSource] = None
        self.connector_id = connector_id

        # Configuration
        self.instance_url: Optional[str] = None
        # The portal that serves the knowledge pages. Read from the instance
        # during init(), because it differs from one instance to the next.
        self.kb_portal_suffix: str = ServiceNowDefaults.KB_PORTAL_SUFFIX
        # Whether article criteria override the criteria of the knowledge base.
        # Read from the instance during init(); false is the ServiceNow default.
        self.apply_article_read_criteria: bool = False
        # The connector's users, read once per sync run after the user sync (see _all_app_users)
        self._app_users: list[AppUser] | None = None
        # source user sys_id -> email, read once per sync run (see _process_criteria_permissions)
        self._email_by_source_id: dict[str, str] | None = None
        # user_criteria rows by sys_id, read once per sync run (see _fetch_user_criteria)
        self._criteria_rows: dict[str, TableAPIRecord | None] = {}
        # KB sys_id -> (Can Read ids, Cannot Read ids), read once per run for articles with Cannot Read
        self._kb_criteria_cache: dict[str, tuple[list[str], list[str]]] = {}
        self._admin_source_ids: set | None = None
        self._kb_owner_cache: dict[str, set] = {}
        # Set when this run skipped something after an error it did not raise, so a full
        # sync's cleanup unlinks nothing (see full_sync_coverage).
        self._sync_read_incomplete = False
        # Set by the knowledge base and category steps of a run (see _sync_knowledge_bases, _sync_categories)
        self._live_kb_ids: set[str] | None = None
        self._kbs_back_in_scope: set[str] = set()
        self._category_ids: set[str] | None = None
        self.client_id: str | None = None
        self.client_secret: str | None = None
        self.redirect_uri: str | None = None

        # OAuth tokens (managed by framework/client)
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

        # Initialize sync points for incremental sync
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        # Sync points for different entity types. Groups, roles and knowledge
        # bases have none: those three reads must stay full, see
        # _fetch_all_memberships and _sync_knowledge_bases.
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.category_sync_point = _create_sync_point(SyncDataPointType.RECORD_GROUPS)
        self.article_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        # Organizational entity sync points
        self.company_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.department_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.location_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.cost_center_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        # Map entity types to their sync points for easy lookup
        self.org_entity_sync_points = {
            "company": self.company_sync_point,
            "department": self.department_sync_point,
            "location": self.location_sync_point,
            "cost_center": self.cost_center_sync_point,
        }

    async def init(self) -> bool:
        """
        Initialize the connector with OAuth credentials and API client.

        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            self.logger.info("🔧 Initializing ServiceNow KB Connector (OAuth)...")
            connector_id = self.connector_id
            # Load configuration
            config = await self.config_service.get_config(
                ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_id)
            )

            if not config:
                self.logger.error("❌ ServiceNow configuration not found")
                return False

            # Extract OAuth configuration
            auth_config = config.get(OAuthConfigKeys.AUTH, {})
            oauth_config_id = auth_config.get(OAuthConfigKeys.OAUTH_CONFIG_ID)

            if not oauth_config_id:
                self.logger.error(
                    "ServiceNow has no oauthConfigId in its auth config; it reads its "
                    "client ID and secret only from a linked OAuth app."
                )
                raise oauth_app_not_linked_error()

            oauth_config = await self._fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type=ServiceNowDefaults.CONNECTOR_TYPE,
                auth_config=auth_config,
            )

            if not oauth_config:
                self.logger.error("OAuth config not found for ServiceNow connector.")
                return False

            oauth_config_data = oauth_config.get(OAuthConfigKeys.CONFIG, {})

            # A trailing slash would give "https://<instance>.service-now.com//kb?...".
            self.instance_url = (oauth_config_data.get(AuthFieldKeys.INSTANCE_URL) or "").rstrip("/") or None
            self.client_id = oauth_config_data.get(AuthFieldKeys.CLIENT_ID)
            self.client_secret = oauth_config_data.get(AuthFieldKeys.CLIENT_SECRET)
            self.redirect_uri = oauth_config.get(AuthFieldKeys.REDIRECT_URI)
            self.logger.info("Using shared OAuth config for ServiceNow connector")

            # OAuth tokens (stored after authorization flow completes)
            credentials = config.get(OAuthConfigKeys.CREDENTIALS, {})
            self.access_token = credentials.get(OAuthConfigKeys.ACCESS_TOKEN)
            self.refresh_token = credentials.get(OAuthConfigKeys.REFRESH_TOKEN)

            if not all(
                [
                    self.instance_url,
                    self.client_id,
                    self.client_secret,
                    self.redirect_uri,
                ]
            ):
                self.logger.error(
                    "❌ Incomplete ServiceNow OAuth configuration. "
                    "Ensure instanceUrl, clientId, clientSecret, and redirectUri are configured."
                )
                return False

            # Check if OAuth flow is complete
            if not self.access_token:
                self.logger.warning("⚠️ OAuth authorization not complete. User needs to authorize.")
                return False

            # Initialize ServiceNow OAuth client
            self.logger.info(
                f"🔗 Connecting to ServiceNow instance: {self.instance_url}"
            )
            await self._close_client()
            self.servicenow_client = ServiceNowRESTClientViaOAuthAuthorizationCode(
                instance_url=self.instance_url,
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                access_token=self.access_token,
                resilience=self.resilience,
            )

            # Store refresh token if available
            if self.refresh_token:
                self.servicenow_client.refresh_token = self.refresh_token

            # Initialize data source wrapper
            self.servicenow_datasource = ServiceNowDataSource(self.servicenow_client)

            # Test connection
            if not await self.test_connection_and_access():
                self.logger.error("❌ Connection test failed")
                return False

            self.kb_portal_suffix = await self._resolve_kb_portal_suffix()

            self.apply_article_read_criteria = await self._resolve_article_read_criteria()

            self.logger.info("✅ ServiceNow KB Connector initialized successfully")
            return True

        except ConnectorInitError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize connector: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> ServiceNowDataSource:
        """
        Get ServiceNowDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current token from config (async I/O)
        2. Updates client if token changed
        3. Returns ready-to-use datasource

        Returns:
            ServiceNowDataSource with current valid token
        """
        if not self.servicenow_client:
            raise connector_not_ready(self.display_name)

        connector_id = self.connector_id

        # Fetch current token from config (async I/O)
        config = await self.config_service.get_config(ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_id))

        if not config:
            raise connector_not_ready(self.display_name)

        credentials = config.get(OAuthConfigKeys.CREDENTIALS) or {}
        fresh_token = credentials.get(OAuthConfigKeys.ACCESS_TOKEN)

        if not fresh_token:
            raise connector_not_ready(self.display_name)

        if self.servicenow_client.access_token != fresh_token:
            self.servicenow_client.set_access_token(fresh_token)
        # The refresh token a refresh after a 401 passes on (see _with_token_retry).
        self.refresh_token = credentials.get(OAuthConfigKeys.REFRESH_TOKEN) or self.refresh_token

        return ServiceNowDataSource(self.servicenow_client)

    async def _with_token_retry(self, call: Callable[[ServiceNowDataSource], Awaitable[T]]) -> T:
        """Run ``call`` on a fresh datasource. After a 401, get a working token once and run it
        again; a 401 that remains raises ServiceNowAuthError. The access token lives 1,800 s, so a
        long sync outlives the token it started with."""
        for attempt in range(2):
            datasource = await self._get_fresh_datasource()
            # Held before the call: refresh_now adopts a token rotated meanwhile instead of posting this one.
            held = (getattr(self.servicenow_client, "access_token", None), self.refresh_token)
            try:
                return await call(datasource)
            except ServiceNowAPIError as e:
                if e.status_code != HttpStatusCode.UNAUTHORIZED.value or isinstance(e, ServiceNowAuthError):
                    raise
                if attempt or not await self._refresh_after_unauthorized(*held):
                    raise ServiceNowAuthError(e.message, e.details) from e
        raise AssertionError("unreachable: the second attempt returns or raises")

    async def _refresh_after_unauthorized(self, held_access: str | None, held_refresh: str | None) -> bool:
        """After a 401: adopt an access token stored since the call, else refresh with the refresh
        token held before it. False when there is nothing to refresh with or the refresh failed."""
        client = self.servicenow_client
        if client is None:
            return False
        try:
            config = await self.config_service.get_config(
                ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=self.connector_id), use_cache=False
            )
            stored = ((config or {}).get(OAuthConfigKeys.CREDENTIALS) or {}).get(OAuthConfigKeys.ACCESS_TOKEN)
            if stored and stored != held_access:
                client.set_access_token(stored)
                return True
            refresh_service = startup_service.get_token_refresh_service()
            if not held_refresh or not refresh_service:
                self.logger.error("ServiceNow returned 401 and there is no refresh token or refresh service")
                return False
            token = await refresh_service.refresh_now(self.connector_id, self.connector_name.value, held_refresh)
        except Exception as e:
            self.logger.error(f"ServiceNow token refresh after a 401 failed: {e}")
            return False
        if not token or not token.access_token:
            return False
        client.set_access_token(token.access_token)
        self.logger.info("Refreshed the ServiceNow access token after a 401")
        return True

    async def _table_get(self, table: str, **params: str | None) -> TableAPIResponse:
        """The one place the connector reads the Table API, with the options every read uses."""
        return await self._with_token_retry(lambda datasource: datasource.get_now_table_tableName(
            tableName=table,
            **{
                "sysparm_display_value": ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                "sysparm_no_count": ServiceNowQueryValues.NO_COUNT_TRUE,
                "sysparm_exclude_reference_link": ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                **params,
            },
        ))

    async def _iter_table(
        self, table: str, query: str | None, fields: list[str], page: int = ServiceNowDefaults.BATCH_SIZE
    ) -> AsyncGenerator[list[TableAPIRecord], None]:
        """Every row of ``table`` that ``query`` matches, a page at a time, oldest update first.

        Pages are keyset on (sys_updated_on, sys_id), not offsets, so rows that share a timestamp,
        or a row updated during the walk, are not skipped. ServiceNow removes the rows an ACL hides
        after it cuts a page, so a page can come back short, or empty in the middle of the list.
        So a page's size says nothing about the end: X-Total-Count does, which counts the rows of
        the current keyset query before ACLs. A page with rows moves the cursor to its last row; an
        empty one reads the same query at the next offset. The listing ends once ``offset + page``
        reaches the count. ``query`` is repeated in both halves of the keyset condition, so it must
        not hold ``^NQ`` or ``ORDERBY``.
        """
        names = CommonStrings.COMMA.join(dict.fromkeys([*fields, ServiceNowFields.SYS_UPDATED_ON, ServiceNowFields.SYS_ID]))
        scope = f"{query}^" if query else ""
        after: tuple[str, str] | None = None
        offset = 0
        while True:
            keyset = "" if after is None else (
                f"{ServiceNowFields.SYS_UPDATED_ON}>{after[0]}^NQ{scope}"
                f"{ServiceNowFields.SYS_UPDATED_ON}={after[0]}^{ServiceNowFields.SYS_ID}>{after[1]}^"
            )
            response = await self._table_get(
                table,
                sysparm_query=f"{scope}{keyset}{ServiceNowQueryValues.ORDER_BY_KEYSET}",
                sysparm_fields=names,
                sysparm_limit=str(page),
                sysparm_offset=str(offset),
                sysparm_no_count=None,  # the count is the end of the listing
            )
            total = response.total_count
            if total is None:
                raise ServiceNowAPIError(status_code=500, message=f"{table} listing returned no X-Total-Count")
            rows = response.result
            if rows:
                yield rows
                last = (
                    str(rows[-1].get(ServiceNowFields.SYS_UPDATED_ON) or ""),
                    str(rows[-1].get(ServiceNowFields.SYS_ID) or ""),
                )
                if after is not None and last <= after:
                    # The instance did not apply the keyset: reading on would repeat or skip rows.
                    raise ServiceNowAPIError(status_code=500, message=f"{table} listing did not advance past {after}")
            if offset + page >= total:
                return
            if rows:
                after, offset = last, 0
            else:
                offset += page

    @staticmethod
    def _chunk_ids(ids: list[str]) -> list[list[str]]:
        """Split an ``IN`` list so the request URL stays under MAX_URL_LENGTH. A keyset query
        repeats the list and every comma is sent as %2C, so a chunk gets a quarter of the limit."""
        budget = ServiceNowDefaults.MAX_URL_LENGTH // 4
        chunks: list[list[str]] = []
        size = 0
        for item in ids:
            if not chunks or size + len(item) + 3 > budget:
                chunks.append([])
                size = 0
            chunks[-1].append(item)
            size += len(item) + 3
        return chunks

    async def _read_checkpoint(self, sync_point: SyncPoint, key: str) -> str | None:
        """The stored checkpoint, or None while a full sync runs. A full sync whose sync points
        could not be cleared would otherwise read only the delta, and its cleanup would then
        unlink everything that did not change."""
        if self.data_entities_processor.full_sync_running(self.connector_id):
            return None
        data = await sync_point.read_sync_point(key)
        return data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME) if data else None

    @staticmethod
    def _delta_since(checkpoint: str) -> str:
        """The UTC literal an incremental read starts from: DELTA_OVERLAP_SECONDS before ``checkpoint``."""
        stored = datetime.strptime(checkpoint, ServiceNowDefaults.DATETIME_FORMAT).replace(tzinfo=timezone.utc)
        start = stored - timedelta(seconds=ServiceNowDefaults.DELTA_OVERLAP_SECONDS)
        return start.strftime(ServiceNowDefaults.DATETIME_FORMAT)

    @staticmethod
    def _current_article_query() -> str:
        """The article rows PipesHub indexes: the ones ServiceNow search shows (its knowledge
        search sources use this filter). Every other row is not indexed, and a stored article
        whose number has no such row is deleted: retired, outdated, draft or review rows,
        ``active=false``, ``valid_to`` before today (UTC; empty never expires) and articles in
        an inactive knowledge base. Each number has at most one such row. Change the rule here
        only: the full read, the version resolution, the reconcile and streaming all use it."""
        today = datetime.now(timezone.utc).strftime(ServiceNowDefaults.DATE_FORMAT)
        return (
            f"{ServiceNowFields.WORKFLOW_STATE}={ServiceNowFields.PUBLISHED}^{ServiceNowFields.ACTIVE}=true"
            f"^{ServiceNowFields.KB_KNOWLEDGE_BASE}.{ServiceNowFields.ACTIVE}=true"
            f"^{ServiceNowFields.VALID_TO}ISEMPTY^OR{ServiceNowFields.VALID_TO}>={today}"
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).strftime(ServiceNowDefaults.DATETIME_FORMAT)

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A full sync that skipped an article or a knowledge base after an error did not see
        it, so it unlinks nothing."""
        return FullSyncCoverage(complete=not self._sync_read_incomplete)

    async def test_connection_and_access(self) -> bool:
        """
        Test OAuth connection and access to ServiceNow API.

        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            self.logger.info("🔍 Testing ServiceNow OAuth connection...")

            # Make a simple API call to verify OAuth token works
            try:
                await self._table_get(
                    ServiceNowTables.KB_KNOWLEDGE_BASE,
                    sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    sysparm_fields=CommonStrings.COMMA.join(
                        [
                            ServiceNowFields.SYS_ID,
                            ServiceNowFields.TITLE,
                        ]
                    )
                )
                
                self.logger.info("✅ OAuth connection test successful")
                return True
                
            except ServiceNowAPIError as e:
                self.logger.error(f"❌ Connection test failed: {e.message} (status: {e.status_code})")
                return False

        except Exception as e:
            self.logger.error(f"❌ Connection test failed: {e}", exc_info=True)
            return False

    async def _register_authenticated_identity(self) -> None:
        """Record which source account this connector is authenticated as, so a creator whose
        PipesHub email differs still resolves that account's permissions for this connector."""
        email, source_user_id = None, None
        try:
            response = await self._table_get(
                ServiceNowTables.SYS_USER,
                sysparm_query=ServiceNowQueryValues.CURRENT_USER,
                sysparm_fields=CommonStrings.COMMA.join([ServiceNowFields.SYS_ID, ServiceNowFields.EMAIL]),
                sysparm_limit="2",
            )
            rows = response.result or []
            # An instance that will not evaluate the script can drop the condition and list
            # other users, so anything but exactly one row could link the wrong account
            if len(rows) == 1:
                email = rows[0].get(ServiceNowFields.EMAIL)
                source_user_id = rows[0].get(ServiceNowFields.SYS_ID)
        except Exception as e:
            self.logger.debug("Could not read the authenticated ServiceNow user: %s", e)
            return
        await self.register_authenticated_source_user(
            email.strip() if isinstance(email, str) else None, source_user_id
        )

    async def run_sync(self) -> None:
        """
        Sync ServiceNow knowledge data. Full and incremental runs both come here: users,
        organizational entities, categories and articles read from their checkpoints (none
        during a full sync); groups, roles and knowledge bases are always read in full.

        Sync order:
        1. Users and Groups (global)
        2. Get admin users from ServiceNow
        3. Knowledge Bases (with admin permissions)
        4. Categories
        5. KB Articles and their attachments
        """
        try:
            self._app_users = None  # user listing is read once per run, after the user sync
            self._email_by_source_id = None
            self._criteria_rows = {}
            self._kb_criteria_cache = {}
            self._admin_source_ids = None
            self._kb_owner_cache = {}
            self._sync_read_incomplete = False
            self._live_kb_ids, self._kbs_back_in_scope, self._category_ids = None, set(), None
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting ServiceNow KB sync for org: {org_id}")

            # Ensure client is initialized
            if not self.servicenow_client:
                raise Exception("ServiceNow client not initialized. Call init() first.")

            await self._register_authenticated_identity()

            # Step 1: Sync users and groups globally
            self.logger.info("Step 1/5: Syncing users and groups...")
            await self._sync_users_and_groups()

            # Step 2: Get admin users from ServiceNow
            self.logger.info("Step 2/5: Fetching admin users from ServiceNow...")
            admin_users = await self._get_admin_users()

            if not admin_users:
                self.logger.warning("No admin users found, proceeding without explicit admin permissions")
                admin_users = []
            # The article grants need the same set (_kb_privileged_users): read once per run.
            self._admin_source_ids = {a.source_user_id for a in admin_users if a.source_user_id}

            self.logger.info(f"✅ Found {len(admin_users)} admin users")

            # Step 3: Knowledge Bases
            self.logger.info("Step 3/5: Syncing Knowledge Bases...")
            await self._sync_knowledge_bases(admin_users)

            # Step 4: Categories
            self.logger.info("Step 4/5: Syncing Categories...")
            await self._sync_categories()

            # Step 5: Articles & Attachments
            self.logger.info("Step 5/5: Syncing Articles & Attachments...")
            await self._sync_articles()

            self.logger.info("🎉 ServiceNow KB sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error during sync: {e}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """
        Run incremental synchronization using delta links or timestamps.

        For ServiceNow, this uses the sys_updated_on field to fetch only
        records updated since the last sync.
        """
        # delegate to full sync
        await self.run_sync()

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (article HTML or attachment file) from ServiceNow.

        For articles (WebpageRecord): Fetches HTML content from kb_knowledge table
        For attachments (FileRecord): Downloads file from attachment API

        Args:
            record: The record to stream (article or attachment)

        Returns:
            StreamingResponse: Streaming response with article HTML or file content
        """
        # A stub for a parent that never synced: there is nothing behind it to serve.
        if getattr(record, "is_placeholder", False) is True:
            raise not_found_at_source(self.display_name)
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            if record.record_type == RecordType.WEBPAGE:
                # Article - fetch HTML content from kb_knowledge table
                html_content = await self._fetch_article_content(record.external_record_id)

                async def generate_article() -> AsyncGenerator[bytes, None]:
                    yield html_content.encode('utf-8')

                return StreamingResponse(
                    generate_article(),
                    media_type='text/html',
                    headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.html"'}
                )

            elif record.record_type == RecordType.FILE:
                # Attachment - download file from ServiceNow
                file_content = await self._fetch_attachment_content(record.external_record_id)

                async def generate_attachment() -> AsyncGenerator[bytes, None]:
                    yield file_content

                filename = record.record_name or f"{record.external_record_id}"
                return create_stream_record_response(
                    generate_attachment(),
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            else:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Unsupported record type for streaming: {record.record_type}"
                )

        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

    async def _read_instance_property(self, name: str) -> Optional[str]:
        """Read one sys_properties value. None means the property is not set.

        A read that fails raises, because "not set" and "could not tell" are
        different answers and only the caller knows which way to lean on the
        second one.
        """
        response = await self._table_get(
            ServiceNowTables.SYS_PROPERTIES,
            sysparm_query=f"{ServiceNowFields.NAME}={name}",
            sysparm_fields=ServiceNowFields.VALUE,
            sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
        )
        if response.result:
            return (response.result[0].get(ServiceNowFields.VALUE) or "").strip()
        return None

    async def _resolve_article_read_criteria(self) -> bool:
        """Whether an article's own Can Read criteria override its knowledge base.

        ServiceNow applies them as an override only when
        glide.knowman.apply_article_read_criteria is true, and leaves the
        property unset on a default instance, so unset means false.

        A property that cannot be read is a different case: the answer decides
        who reaches an article, so it leans to true. That indexes an article
        under its own criteria when the base would have opened it wider, which
        shows fewer people less than they are entitled to, rather than showing
        somebody something the instance restricts.
        """
        try:
            value = await self._read_instance_property(
                ServiceNowDefaults.ARTICLE_READ_CRITERIA_PROPERTY
            )
        except Exception as e:
            self.logger.warning(
                "Could not read "
                f"{ServiceNowDefaults.ARTICLE_READ_CRITERIA_PROPERTY} ({e}); "
                "applying article read criteria as an override until it can be read"
            )
            return True

        applies = (value or "").strip().lower() == "true"
        self.logger.info(f"Article read criteria override the knowledge base: {applies}")
        return applies

    async def _resolve_kb_portal_suffix(self) -> str:
        """Find the Service Portal that serves the knowledge pages.

        The article pages (kb_article_view, kb_category, kb_home) belong to a
        portal, and each instance chooses its own. The Knowledge Management
        portal plugin records that choice in a system property. When the
        property is empty the default portal serves the pages.

        Returns:
            str: The portal URL suffix, for example "kb" or "esc".
        """
        try:
            suffix = await self._read_instance_property(ServiceNowDefaults.KB_PORTAL_PROPERTY)
        except Exception as e:
            # A link that points at the wrong portal is wrong on screen only, so
            # this one falls back rather than leaning any particular way.
            self.logger.warning(f"Could not read the knowledge portal property: {e}")
            suffix = None
        if suffix:
            self.logger.info(f"Knowledge portal from the instance property: /{suffix}")
            return suffix

        try:
            response = await self._table_get(
                ServiceNowTables.SP_PORTAL,
                sysparm_query=f"{ServiceNowFields.DEFAULT}=true",
                sysparm_fields=ServiceNowFields.URL_SUFFIX,
                sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
            )
            suffix = (response.result[0].get(ServiceNowFields.URL_SUFFIX) or "").strip() if response.result else ""
            if suffix:
                self.logger.info(f"Knowledge portal from the default portal: /{suffix}")
                return suffix
        except Exception as e:
            self.logger.warning(f"Could not read the default portal: {e}")

        self.logger.info(
            f"Knowledge portal falls back to /{ServiceNowDefaults.KB_PORTAL_SUFFIX}"
        )
        return ServiceNowDefaults.KB_PORTAL_SUFFIX

    async def _fetch_article_content(self, article_sys_id: str) -> str:
        """
        Fetch the HTML of an article's current version (see _current_article_query).

        Args:
            article_sys_id: The record's key: the article number, or a version's sys_id for a
                record stored before articles were keyed by number

        Returns:
            str: HTML content of the article

        Raises:
            HTTPException: not found when the article has no current version (retired, expired, ...)
        """
        key_field = ServiceNowFields.SYS_ID if _LEGACY_ARTICLE_KEY.match(article_sys_id) else ServiceNowFields.NUMBER
        try:
            # Fetch article using ServiceNow Table API
            try:
                response = await self._table_get(
                    ServiceNowTables.KB_KNOWLEDGE,
                    sysparm_query=f"{key_field}={article_sys_id}^{self._current_article_query()}",
                    sysparm_fields=CommonStrings.COMMA.join(
                        [
                            ServiceNowFields.SYS_ID,
                            ServiceNowFields.SHORT_DESCRIPTION,
                            ServiceNowFields.TEXT,
                            ServiceNowFields.NUMBER,
                        ]
                    ),
                    sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                )
            except ServiceNowAPIError as e:
                self.logger.error(f"Failed to fetch article {article_sys_id}: {e.message}")
                raise map_source_status(e.status_code, connector=self.display_name) from e

            # Extract article from result array
            articles = response.result
            if not articles or len(articles) == 0:
                raise not_found_at_source(self.display_name)

            article = articles[0]

            # Get raw HTML content from text field
            html_content = article.text or ""

            if not html_content:
                # If no content, return empty HTML
                self.logger.warning(f"Article {article_sys_id} has no content")
                html_content = "<p>No content available</p>"

            return html_content

        except HTTPException:
            raise  # Re-raise HTTP exceptions
        except Exception as e:
            self.logger.error(f"Failed to fetch article content: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

    async def _fetch_attachment_content(self, attachment_sys_id: str) -> bytes:
        """
        Fetch attachment file content from ServiceNow.

        Uses the attachment download API: GET /api/now/attachment/{sys_id}/file

        Args:
            attachment_sys_id: The sys_id of the attachment

        Returns:
            bytes: Binary file content

        Raises:
            HTTPException: If attachment not found or download fails
        """
        try:
            # Download using REST client (returns bytes directly)
            file_content = await self._with_token_retry(
                lambda datasource: datasource.download_attachment(attachment_sys_id)
            )

            if not file_content:
                raise not_found_at_source(self.display_name)

            return file_content

        except HTTPException:
            raise  # Re-raise HTTP exceptions
        except ServiceNowAPIError as e:
            self.logger.error(f"ServiceNow API error downloading attachment: {e.message}")
            raise map_source_status(e.status_code, connector=self.display_name) from e
        except Exception as e:
            self.logger.error(f"Failed to download attachment: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

    def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get signed URL for record access.

        ServiceNow doesn't support pre-signed URLs in the traditional sense,
        so this returns None. Access is controlled through the stream_record method.

        Args:
            record: The record to get URL for

        Returns:
            Optional[str]: None for ServiceNow
        """
        return None

    async def handle_webhook_notification(
        self, org_id: str, notification: Dict
    ) -> bool:
        """
        Handle webhook notifications from ServiceNow.

        This can be used for real-time sync when ServiceNow sends notifications
        about changes to KB articles.

        Args:
            org_id: Organization ID
            notification: Webhook notification payload

        Returns:
            bool: True if handled successfully
        """
        try:
            # TODO: Implement webhook handling
            # ServiceNow can send notifications via Business Rules or Flow Designer
            self.logger.info(f"📬 Received webhook notification: {notification}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error handling webhook: {e}", exc_info=True)
            return False

    async def cleanup(self) -> None:
        """
        Clean up resources used by the connector.

        This is called when the connector is being shut down.
        """
        try:
            self.logger.info("🧹 Cleaning up ServiceNow KB Connector...")
            await self._close_client()

            self.logger.info("✅ Cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}", exc_info=True)

    async def _close_client(self) -> None:
        """Close the current client's connection pool; a re-init or shutdown would leak it otherwise."""
        client, self.servicenow_client, self.servicenow_datasource = self.servicenow_client, None, None
        if client is not None:
            await client.close()

    async def reindex_records(self, record_results: List[Record]) -> None:
        # Index events only: on_new_records would re-run permission handling.
        if not record_results:
            return
        await self.data_entities_processor.reindex_existing_records(record_results)

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """ServiceNow connector does not support dynamic filter options."""
        raise NotImplementedError("ServiceNow connector does not support dynamic filter options")

    async def _sync_users_and_groups(self) -> None:
        """
        Sync users, groups, roles, and organizational entities from ServiceNow.

        This is the foundation for permission management.

        API Endpoints:
        - /api/now/table/sys_user - Users
        - /api/now/table/sys_user_group - Groups
        - /api/now/table/sys_user_grmember - Group memberships
        - /api/now/table/sys_user_role - Roles
        - /api/now/table/sys_user_role_contains - Role hierarchy
        - /api/now/table/sys_user_has_role - User-role assignments
        """
        try:
            # Step 1: Sync organizational entities
            self.logger.info("Step 1/4: Syncing organizational entities...")
            await self._sync_organizational_entities()

            # Step 4: Sync users
            self.logger.info("Step 2/4: Syncing users...")
            await self._sync_users()

            # Step 2: Sync user groups
            self.logger.info("Step 3/4: Syncing user groups...")
            await self._sync_user_groups()

            # Step 3: Sync roles
            self.logger.info("Step 4/4: Syncing roles...")
            await self._sync_roles()


            self.logger.info("✅ Users, groups, roles, and organizational entities synced successfully")

        except Exception as e:
            self.logger.error(f"❌ Error syncing users/groups: {e}", exc_info=True)
            raise

    async def _get_admin_users(self) -> List[AppUser]:
        """
        Get users with admin role from ServiceNow and match with platform users.

        Fetches users with admin role from sys_user_has_role table and matches them
        with existing platform users in the database. A failed read or lookup raises:
        an empty list would be written as "no admins" and remove their grants.

        Returns:
            List[AppUser]: List of admin users from platform
        """
        role_assignments: list[TableAPIRecord] = []
        try:
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER_HAS_ROLE,
                f"{ServiceNowFields.ROLE}.{ServiceNowFields.NAME}={ServiceNowRoles.ADMIN}^{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
                [ServiceNowFields.USER],
            ):
                role_assignments.extend(rows)
        except ServiceNowAPIError as e:
            self.logger.error(f"Failed to fetch admin users from ServiceNow: {e.message}")
            raise
        self.logger.info(f"Found {len(role_assignments)} admin role assignments")

        admin_sys_ids = {a.user for a in role_assignments if getattr(a, "user", None)}
        self.logger.info(f"Found {len(admin_sys_ids)} unique admin users")

        # Match with platform users using source_user_id; an admin with no platform account is skipped.
        admin_users = []
        for sys_id in admin_sys_ids:
            app_user = await self.data_entities_processor.get_user_by_source_id(
                source_user_id=sys_id,
                connector_id=self.connector_id
            )
            if app_user:
                admin_users.append(app_user)

        self.logger.info(f"✅ Matched {len(admin_users)} admin users with platform accounts")
        return admin_users

    async def _sync_users(self) -> None:
        """
        Sync users from ServiceNow using offset-based pagination.

        First sync: Fetches all users
        Subsequent syncs: Only fetches users modified since last sync
        """
        try:
            last_sync_time = await self._read_checkpoint(self.user_sync_point, ServiceNowSyncPointKeys.USERS)

            query = None
            if last_sync_time:
                since = self._delta_since(last_sync_time)
                self.logger.info(f"🔄 Delta sync: fetching users updated after {since}")
                query = f"{ServiceNowFields.SYS_UPDATED_ON}>{since}"
            else:
                self.logger.info("🆕 Full sync: fetching all users")

            total_synced = 0
            latest_update_time = None

            # A failed page raises: a short list is indistinguishable from a complete one to
            # everything downstream, so the sync would report success over what it missed.
            async for users_data in self._iter_table(
                ServiceNowTables.SYS_USER,
                query,
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.USER_NAME,
                    ServiceNowFields.EMAIL,
                    ServiceNowFields.FIRST_NAME,
                    ServiceNowFields.LAST_NAME,
                    ServiceNowFields.TITLE,
                    ServiceNowFields.DEPARTMENT,
                    ServiceNowFields.COMPANY,
                    ServiceNowFields.LOCATION,
                    ServiceNowFields.COST_CENTER,
                    ServiceNowFields.ACTIVE,
                    ServiceNowFields.SYS_CREATED_ON,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
            ):
                # Pages come oldest first, so the last row is the newest seen
                latest_update_time = users_data[-1].sys_updated_on

                # Transform users (skip users without email)
                app_users = []
                user_org_links = []  # Collect organizational links

                for user_data in users_data:
                    email = (user_data.email or "").strip()
                    if not email:
                        continue

                    app_user = await self._transform_to_app_user(user_data)
                    if app_user:
                        app_users.append(app_user)

                        # Collect organizational links for this user
                        user_sys_id = user_data.sys_id
                        if user_sys_id:
                            org_fields = {
                                "company": user_data.company,
                                "department": user_data.department,
                                "location": user_data.location,
                                "cost_center": user_data.cost_center,
                            }

                            for org_type, org_ref in org_fields.items():
                                if not org_ref:
                                    continue

                                # org_ref is now just a sys_id string (not a reference object)
                                org_sys_id = org_ref if isinstance(org_ref, str) else None

                                if org_sys_id:
                                    user_org_links.append({
                                        ServiceNowDictKeys.USER_SYS_ID: user_sys_id,
                                        ServiceNowDictKeys.ORG_SYS_ID: org_sys_id,
                                        ServiceNowDictKeys.ORG_TYPE: org_type,
                                    })

                # Save batch to database
                if app_users:
                    await self.data_entities_processor.on_new_app_users(app_users)
                    total_synced += len(app_users)

                # Create user-to-organizational-entity edges
                if user_org_links:
                    self.logger.info(f"Creating {len(user_org_links)} user-to-organizational-entity link")
                    for link in user_org_links:
                        await self.data_entities_processor.create_user_group_membership(
                            link[ServiceNowDictKeys.USER_SYS_ID],
                            link[ServiceNowDictKeys.ORG_SYS_ID],
                            self.connector_id
                        )

            # Save checkpoint for next sync
            if latest_update_time:
                await self.user_sync_point.update_sync_point(ServiceNowSyncPointKeys.USERS, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})

            self.logger.info(f"User sync complete, Total synced: {total_synced}")

        except Exception as e:
            self.logger.error(f"❌ User sync failed: {e}", exc_info=True)
            raise

    async def _sync_user_groups(self) -> None:
        """
        Sync user groups and flatten memberships.
        Simple 3-step process: fetch groups → fetch memberships → flatten & upsert
        """
        try:
            self.logger.info("Starting user group synchronization")

            # STEP 1: Fetch all memberships
            memberships_data = await self._fetch_all_memberships()

            if not memberships_data:
                self.logger.info("No memberships found, skipping group sync")
                return

            # STEP 2: Fetch all groups
            groups_data = await self._fetch_all_groups()

            # STEP 3: Flatten and create AppUserGroup objects
            group_with_permissions = await self._flatten_and_create_user_groups(
                groups_data,
                memberships_data
            )

            # STEP 4: Upsert to database
            if group_with_permissions:
                await self.data_entities_processor.on_new_user_groups(group_with_permissions)

            self.logger.info(f"✅ Processed {len(group_with_permissions)} user groups")

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}", exc_info=True)
            raise


    async def _fetch_all_groups(self) -> List[SysUserGroup]:
        """Fetch all groups from ServiceNow (no delta sync)
        
        Returns:
            List of SysUserGroup Pydantic models
        """
        try:
            all_groups: List[SysUserGroup] = []
            # A failed page raises: a partial list makes the caller delete permission
            # edges that it cannot rebuild.
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER_GROUP,
                None,
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.NAME,
                    ServiceNowFields.DESCRIPTION,
                    ServiceNowFields.PARENT,
                    ServiceNowFields.MANAGER,
                    ServiceNowFields.SYS_CREATED_ON,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
            ):
                all_groups.extend(SysUserGroup(**record.model_dump()) for record in rows)

            self.logger.info(f"Fetched {len(all_groups)} groups")
            return all_groups

        except Exception as e:
            self.logger.error(f"❌ Error fetching groups: {e}", exc_info=True)
            raise


    async def _fetch_all_memberships(self) -> List[SysUserGroupMembership]:
        """Fetch every user-group membership from ServiceNow.

        This read must stay full. ``on_new_user_groups`` deletes all permission
        edges that point to a group before it writes the members given to it, so
        a delta read would drop every membership whose ``sys_user_grmember`` row
        did not change inside the delta window.

        Returns:
            List of SysUserGroupMembership Pydantic models
        """
        try:
            self.logger.info("Fetching all user memberships")

            all_memberships: List[SysUserGroupMembership] = []
            # A failed page raises: a partial list makes the caller delete permission
            # edges that it cannot rebuild.
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER_GRMEMBER,
                None,
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.USER,
                    ServiceNowFields.GROUP,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
            ):
                all_memberships.extend(SysUserGroupMembership(**record.model_dump()) for record in rows)

            self.logger.info(f"Fetched {len(all_memberships)} memberships")
            return all_memberships

        except Exception as e:
            self.logger.error(f"❌ Error fetching memberships: {e}", exc_info=True)
            raise


    async def _sync_roles(self) -> None:
        """
        Sync roles using the same flattening logic as user groups.

        Roles are synced by:
        1. Fetching roles, role hierarchy, and role assignments
        2. Merging hierarchy into roles (embed parent field)
        3. Transforming role assignments to look like group memberships
        4. Using the same flatten function as groups
        5. Adding ROLE_ prefix to distinguish from regular groups
        """
        try:
            self.logger.info("Starting role synchronization")

            # Step 1: Fetch role assignments
            role_assignments = await self._fetch_all_role_assignments()
            if not role_assignments:
                self.logger.info("No role assignments found")
                return

            # Step 2: Fetch roles
            roles_data = await self._fetch_all_roles()

            # Step 3: Fetch role hierarchy
            hierarchy_data = await self._fetch_role_hierarchy()

            # Step 4: Merge hierarchy into roles (embed parent field)
            child_to_parent = {}
            for hierarchy_record in hierarchy_data:
                # Both contains and role are sys_id strings
                parent_id = hierarchy_record.contains
                child_id = hierarchy_record.role

                if parent_id and child_id and child_id not in child_to_parent:
                    child_to_parent[child_id] = parent_id

            # Add parent field to roles and convert to SysUserGroup models for flatten function
            roles_with_hierarchy = []
            for role in roles_data:
                # Convert role to SysUserGroup format (roles are treated as groups)
                group_data = {
                    "sys_id": role.sys_id,
                    "name": role.name,
                    "description": role.description,
                    "sys_created_on": role.sys_created_on,
                    "sys_updated_on": role.sys_updated_on,
                    "parent": child_to_parent.get(role.sys_id),  # Add parent sys_id if exists
                    "manager": None,
                    "active": "true"
                }
                roles_with_hierarchy.append(SysUserGroup(**group_data))

            self.logger.info(
                f"Merged hierarchy: {len(roles_with_hierarchy)} roles, "
                f"{len(child_to_parent)} with parents"
            )

            # Step 5: Transform role assignments to SysUserGroupMembership models
            role_assignments_as_memberships = []
            for assignment in role_assignments:
                membership_data = {
                    "sys_id": assignment.sys_id,
                    "user": assignment.user,
                    "group": assignment.role,  # Rename role to group for flatten function
                    "sys_created_on": None,
                    "sys_updated_on": assignment.sys_updated_on
                }
                role_assignments_as_memberships.append(SysUserGroupMembership(**membership_data))

            # Step 6: flatten user roles hierarchy
            roles_with_permissions = await self._flatten_and_create_user_groups(
                roles_with_hierarchy,  # Roles as SysUserGroup models with parent
                role_assignments_as_memberships,  # Role assignments as SysUserGroupMembership models
            )

            # Step 7: Add ROLE_ prefix to names
            for role_group, users in roles_with_permissions:
                if not role_group.name.startswith(ServiceNowPrefixes.ROLE):
                    role_group.name = f"{ServiceNowPrefixes.ROLE}{role_group.name}"

            # Step 8: Upsert roles as user groups
            if roles_with_permissions:
                await self.data_entities_processor.on_new_user_groups(roles_with_permissions)

            self.logger.info(f"✅ Processed {len(roles_with_permissions)} roles")

        except Exception as e:
            self.logger.error(f"❌ Error syncing roles: {e}", exc_info=True)
            raise


    async def _fetch_all_roles(self) -> List[SysUserRole]:
        """Fetch all roles from sys_user_role table.
        
        Returns:
            List of SysUserRole Pydantic models
        """
        try:
            self.logger.info("Fetching all roles")

            all_roles: List[SysUserRole] = []
            # A failed page raises: a partial list makes the caller delete permission
            # edges that it cannot rebuild.
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER_ROLE,
                None,
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.NAME,
                    ServiceNowFields.DESCRIPTION,
                    ServiceNowFields.SYS_CREATED_ON,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
            ):
                all_roles.extend(SysUserRole(**record.model_dump()) for record in rows)

            self.logger.info(f"Fetched {len(all_roles)} roles")
            return all_roles

        except Exception as e:
            self.logger.error(f"❌ Error fetching roles: {e}", exc_info=True)
            raise


    async def _fetch_all_role_assignments(self) -> List[SysUserRoleAssignment]:
        """
        Fetch all user-role assignments from sys_user_has_role table.

        This read must stay full, for the reason given in
        ``_fetch_all_memberships``: roles go through the same
        ``on_new_user_groups`` replace-on-write path.

        Returns:
            List of SysUserRoleAssignment Pydantic models
        """
        try:
            self.logger.info("Fetching all active role assignments")
            all_assignments: List[SysUserRoleAssignment] = []
            # A failed page raises: a partial list makes the caller delete permission
            # edges that it cannot rebuild.
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER_HAS_ROLE,
                f"state={ServiceNowFields.ACTIVE}",
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.USER,
                    ServiceNowFields.ROLE,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
            ):
                all_assignments.extend(SysUserRoleAssignment(**record.model_dump()) for record in rows)

            self.logger.info(f"Fetched {len(all_assignments)} role assignments")
            return all_assignments

        except Exception as e:
            self.logger.error(f"❌ Error fetching role assignments: {e}", exc_info=True)
            raise


    async def _fetch_role_hierarchy(self) -> List[SysUserRoleContains]:
        """
        Fetch role hierarchy from sys_user_role_contains table.

        This is a full sync (no checkpoint) since role hierarchy changes are rare.
        
        Returns:
            List of SysUserRoleContains Pydantic models
        """
        try:
            self.logger.info("Fetching role hierarchy")

            all_hierarchy: List[SysUserRoleContains] = []
            # A failed page raises: a partial list makes the caller delete permission
            # edges that it cannot rebuild.
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER_ROLE_CONTAINS,
                None,
                [ServiceNowFields.SYS_ID, ServiceNowFields.CONTAINS, ServiceNowFields.ROLE],
            ):
                all_hierarchy.extend(SysUserRoleContains(**record.model_dump()) for record in rows)

            self.logger.info(f"Fetched {len(all_hierarchy)} role hierarchy records")
            return all_hierarchy

        except Exception as e:
            self.logger.error(f"❌ Error fetching role hierarchy: {e}", exc_info=True)
            raise


    async def _flatten_and_create_user_groups(
        self,
        groups_data: List[SysUserGroup],
        memberships_data: List[SysUserGroupMembership]
    ) -> List[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Flatten group hierarchy and create AppUserGroup objects.

        Args:
            groups_data: List of SysUserGroup Pydantic models
            memberships_data: List of SysUserGroupMembership Pydantic models

        Returns:
            List of (AppUserGroup, [AppUser]) tuples
        """
        try:
            # Build parent-child relationships
            children_map = defaultdict(set)  # parent_id -> {child_ids}
            group_by_id = {}  # group_id -> group_data

            for group in groups_data:
                group_id = group.sys_id
                group_by_id[group_id] = group

                # Extract parent sys_id
                parent_id = group.parent
                if parent_id:
                    children_map[parent_id].add(group_id)

            # Build direct user memberships
            direct_users = defaultdict(set)  # group_id -> {user_ids}

            for membership in memberships_data:
                user_id = membership.user
                group_id = membership.group

                if user_id and group_id:
                    direct_users[group_id].add(user_id)

            # Recursive function to get all users for a group
            def get_all_users(group_id: str, visited: set = None) -> set:
                """Get all users including inherited from child groups."""
                if visited is None:
                    visited = set()

                # Prevent infinite loops
                if group_id in visited:
                    return set()
                visited.add(group_id)

                # Start with direct users
                all_users = set(direct_users.get(group_id, []))

                # Add users from child groups recursively
                for child_id in children_map.get(group_id, []):
                    all_users.update(get_all_users(child_id, visited))

                return all_users

            # Create AppUserGroup objects with flattened members
            result = []

             # Get all existing users from database for lookup
            existing_app_users = await self._all_app_users()

            # Create lookup map: source_user_id -> AppUser
            user_lookup = {user.source_user_id: user for user in existing_app_users}

            for group_id, group_data in group_by_id.items():
                # Create AppUserGroup
                user_group = self._transform_to_user_group(group_data)

                if not user_group:
                    continue

                # Get flattened user IDs
                flattened_user_ids = get_all_users(group_id)

                # Create AppUser objects
                app_users = []
                for user_id in flattened_user_ids:
                    app_user = user_lookup.get(user_id)
                    if app_user:
                        app_users.append(app_user)

                result.append((user_group, app_users))

            self.logger.info(f"Flattened {len(result)} groups")
            return result

        except Exception as e:
            self.logger.error(f"❌ Error flattening groups: {e}", exc_info=True)
            raise

    async def _sync_organizational_entities(self) -> None:
        """
        Sync all organizational entities from ServiceNow.

        Syncs in order:
        1. Companies (top-level)
        2. Departments
        3. Locations
        4. Cost Centers

        Each entity type creates:
        - AppUserGroup nodes with prefix (COMPANY_, DEPARTMENT_, etc.)
        - Parent-child hierarchy edges between entities (commented out for now)

        Note: User-to-organizational-entity edges are created during user sync.
        """
        try:
            self.logger.info("🏢 Starting organizational entities sync")

            # Sync each entity type in order
            for entity_type, config in ORGANIZATIONAL_ENTITIES.items():
                await self._sync_single_organizational_entity(entity_type, config)

            self.logger.info("✅ All organizational entities synced successfully")

        except Exception as e:
            self.logger.error(f"❌ Error syncing organizational entities: {e}", exc_info=True)
            raise

    async def _sync_single_organizational_entity(
        self, entity_type: str, config: OrganizationalEntityConfig
    ) -> None:
        """
        Generic sync method for a single organizational entity type.

        Uses two-pass approach:
        - Pass 1: Create all entity nodes as AppUserGroups
        - Pass 2: Create hierarchy edges (parent-child) - COMMENTED OUT FOR NOW

        Args:
            entity_type: Type of entity (company, department, location, cost_center)
            config: OrganizationalEntityConfig with table name, fields, prefix, and sync point key
        """
        try:
            table_name = config["table"]
            fields = config["fields"]
            prefix = config["prefix"]
            sync_point_key = config["sync_point_key"]

            # Get sync point for this entity type
            sync_point = self.org_entity_sync_points.get(entity_type)

            self.logger.info(f"📊 Starting {entity_type} sync from table {table_name}")

            last_sync_time = await self._read_checkpoint(sync_point, sync_point_key)

            query = None
            if last_sync_time:
                since = self._delta_since(last_sync_time)
                self.logger.info(f"🔄 Delta sync: fetching {entity_type} updated after {since}")
                query = f"{ServiceNowFields.SYS_UPDATED_ON}>{since}"
            else:
                self.logger.info(f"🆕 Full sync: fetching all {entity_type}")

            total_synced = 0
            latest_update_time = None

            # A failed page raises: a short list is indistinguishable from a complete one to
            # everything downstream, so the sync would report success over what it missed.
            async for entities_data in self._iter_table(table_name, query, fields.split(CommonStrings.COMMA)):
                # Pages come oldest first, so the last row is the newest seen
                latest_update_time = entities_data[-1].sys_updated_on

                # Transform to AppUserGroup entities
                user_groups = []
                for entity_data in entities_data:
                    user_group = self._transform_to_organizational_group(
                        entity_data, prefix
                    )
                    if user_group:
                        user_groups.append(user_group)

                if user_groups:
                    await self.data_entities_processor.batch_upsert_user_groups(user_groups)

                    total_synced += len(user_groups)

            # Save checkpoint
            if latest_update_time:
                await sync_point.update_sync_point(
                    sync_point_key, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time}
                )

            self.logger.info(
                f"✅ {entity_type.capitalize()} sync complete. Total synced: {total_synced}"
            )

        except Exception as e:
            self.logger.error(
                f"❌ {entity_type.capitalize()} sync failed: {e}", exc_info=True
            )
            raise

    async def _sync_knowledge_bases(self, admin_users: List[AppUser]) -> None:
        """
        Sync knowledge bases from ServiceNow kb_knowledge_base table.

        Uses data_entities_processor.on_new_record_groups() so that BELONGS_TO edges
        (RecordGroup → Org, RecordGroup → App) are created; Knowledge Hub tree relies on these.

        Creates:
        - RecordGroup nodes (type=SERVICENOW) in recordGroups collection
        - BELONGS_TO edges: RecordGroup → Org, RecordGroup → App
        - OWNER edges: owner → KB RecordGroup
        - WRITER edges: kb_managers → KB RecordGroup
        - READ edges: admin users → KB RecordGroup

        Every run reads every KB (see the comment in the body).

        Args:
            admin_users: List of admin users to grant explicit READ permissions
        """
        try:
            # This read stays full. A knowledge base holds its read grants in
            # kb_uc_can_read_mtom, and a change there does not touch
            # kb_knowledge_base.sys_updated_on. A delta on the base row would
            # therefore never see a new or a removed grant.
            self.logger.info("Fetching all knowledge bases")
            total_synced = 0
            listed: set[str] = set()
            live: set[str] = set()

            # A failed page raises: a short list is indistinguishable from a complete one to
            # everything downstream, so the sync would report success over what it missed.
            async for kbs_data in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE_BASE,
                None,
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.TITLE,
                    ServiceNowFields.DESCRIPTION,
                    ServiceNowFields.OWNER,
                    ServiceNowFields.KB_MANAGERS,
                    ServiceNowFields.ACTIVE,
                    ServiceNowFields.SYS_CREATED_ON,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
            ):
                # Transform to RecordGroup entities
                kb_record_groups = []
                for kb_data in kbs_data:
                    listed.add(kb_data.sys_id)
                    # ServiceNow search leaves an inactive knowledge base out, and its readers lose it.
                    if str(getattr(kb_data, ServiceNowFields.ACTIVE, "") or "").lower() == "false":
                        continue
                    live.add(kb_data.sys_id)
                    kb_record_group = self._transform_to_kb_record_group(kb_data)
                    if kb_record_group:
                        kb_record_groups.append((kb_record_group, kb_data))

                # Build (RecordGroup, permissions) list and route through on_new_record_groups so that
                # BELONGS_TO edges (RecordGroup → Org, RecordGroup → App) are created by the processor.
                if kb_record_groups:
                    kb_list_with_permissions = []
                    for kb_record_group, kb_data in kb_record_groups:
                        kb_sys_id = kb_data.sys_id

                        # Fetch criteria IDs for this KB
                        try:
                            criteria_map = await self._fetch_kb_permissions_from_criteria(kb_sys_id)

                            # Process READ permissions using shared method
                            read_permissions = await self._process_criteria_permissions(
                                criteria_map[ServiceNowDictKeys.READ],
                                PermissionType.READ,
                                deny_criteria_ids=criteria_map.get(ServiceNowDictKeys.CANNOT_READ, []),
                            )

                            # Process WRITE permissions using shared method
                            write_permissions = await self._process_criteria_permissions(
                                criteria_map[ServiceNowDictKeys.WRITE],
                                PermissionType.WRITE,
                            )
                        except (_CriteriaUnreadable, ServiceNowAPIError) as e:
                            _raise_if_run_stop(e)
                            # Unknown is not empty: write no grants for this KB, keep what is stored.
                            self.logger.error(f"Skipping knowledge base {kb_sys_id}, its user criteria were not read in full: {e}")
                            await self.data_entities_processor.note_kept_record_group(self.connector_id, kb_sys_id)
                            self._sync_read_incomplete = True
                            continue

                        # Combine all permissions
                        permission_objects = read_permissions + write_permissions

                        # Add OWNER permission (fallback from owner field)
                        owner_sys_id = kb_data.owner
                        if owner_sys_id:
                            owner_perms = await self._convert_permissions_to_objects(
                                [
                                    RawPermission(
                                        entity_type="USER",
                                        source_sys_id=owner_sys_id,
                                        role="OWNER",
                                    )
                                ],
                            )
                            permission_objects.extend(owner_perms)

                        # Add admin users as explicit READ permissions
                        for admin_user in admin_users:
                            admin_permission = Permission(
                                email=admin_user.email,
                                type=PermissionType.READ,
                                entity_type=EntityType.USER,
                            )
                            permission_objects.append(admin_permission)

                        kb_list_with_permissions.append((kb_record_group, permission_objects))

                    await self.data_entities_processor.on_new_record_groups(kb_list_with_permissions)
                    total_synced += len(kb_list_with_permissions)

            # The listing read to the end (a failed page raises), so what it left out is gone. An
            # empty one says nothing (see _remove_groups_out_of_scope), so no category leaves scope.
            self._live_kb_ids = live if listed else None
            self._kbs_back_in_scope = await self._remove_groups_out_of_scope(
                ServiceNowSyncPointKeys.KB_SCOPE, listed, live
            )
            self.logger.info(f"✅ Knowledge base sync complete, Total synced: {total_synced}")

        except Exception as e:
            self.logger.error(f"❌ Error syncing knowledge bases: {e}", exc_info=True)
            raise

    async def _sync_categories(self) -> None:
        """
        Sync categories from ServiceNow kb_category table.

        Creates:
        - RecordGroup nodes (type=SERVICENOW_CATEGORY) in recordGroups collection
        - PARENT_CHILD edges in nodeRelations collection

        First sync: Fetches all categories
        Subsequent syncs: Only fetches categories modified since last sync. Every run also lists
        every category id, which tells the categories that are gone, and the ones out of scope:
        a category is synced only when its parent chain ends at a knowledge base this run wrote.
        """
        try:
            parents: dict[str, str] = {}
            async for rows in self._iter_table(
                ServiceNowTables.KB_CATEGORY, None, [ServiceNowFields.PARENT_ID], page=ServiceNowDefaults.ID_PAGE_SIZE
            ):
                parents.update({r.sys_id: r.parent_id or "" for r in rows})
            in_scope = set(parents)
            if self._live_kb_ids is not None:
                in_scope = {c for c in parents if self._category_root(c, parents) in self._live_kb_ids}
            self._category_ids = in_scope

            # A knowledge base back in scope lost its categories when it left, and they did not change since.
            last_sync_time = None if self._kbs_back_in_scope else await self._read_checkpoint(
                self.category_sync_point, ServiceNowSyncPointKeys.CATEGORIES
            )

            query = None
            if last_sync_time:
                since = self._delta_since(last_sync_time)
                self.logger.info(f"🔄 Delta sync: Fetching categories updated after {since}")
                query = f"{ServiceNowFields.SYS_UPDATED_ON}>{since}"
            else:
                self.logger.info("🆕 Full sync: Fetching all categories")

            total_synced = 0
            latest_update_time = None

            # A failed page raises: a short list is indistinguishable from a complete one to
            # everything downstream, so the sync would report success over what it missed.
            async for categories_data in self._iter_table(
                ServiceNowTables.KB_CATEGORY,
                query,
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.LABEL,
                    ServiceNowFields.VALUE,
                    ServiceNowFields.PARENT_TABLE,
                    ServiceNowFields.PARENT_ID,
                    ServiceNowFields.ACTIVE,
                    ServiceNowFields.SYS_CREATED_ON,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
            ):
                # Pages come oldest first, so the last row is the newest seen
                latest_update_time = categories_data[-1].sys_updated_on

                # Transform categories to RecordGroups with hierarchy information
                categories_with_permissions = []
                for cat_data in categories_data:
                    if cat_data.sys_id not in in_scope:
                        continue
                    category_rg = self._transform_to_category_record_group(cat_data)
                    if not category_rg:
                        continue

                    # Categories inherit permissions from parent KB
                    categories_with_permissions.append((category_rg, []))

                # Use on_new_record_groups to create nodes and edges in one transaction
                if categories_with_permissions:
                    await self.data_entities_processor.on_new_record_groups(categories_with_permissions)
                    total_synced += len(categories_with_permissions)

            # Update sync checkpoint
            if latest_update_time:
                await self.category_sync_point.update_sync_point(ServiceNowSyncPointKeys.CATEGORIES, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})

            # An inactive category changes nothing in ServiceNow (its articles stay searchable), so it stays.
            await self._remove_groups_out_of_scope(ServiceNowSyncPointKeys.CATEGORY_SCOPE, set(parents), in_scope)
            self.logger.info(f"✅ Categories synced: {total_synced} total")

        except Exception as e:
            self.logger.error(f"❌ Error syncing categories: {e}", exc_info=True)
            raise

    @staticmethod
    def _category_root(category_id: str, parents: dict[str, str]) -> str:
        """The id a category's parent chain ends at: its knowledge base, or a parent the listing lacks."""
        seen: set[str] = set()
        while category_id in parents and category_id not in seen:
            seen.add(category_id)
            category_id = parents[category_id]
        return category_id

    async def _remove_groups_out_of_scope(self, key: str, listed: set[str], in_scope: set[str]) -> set[str]:
        """Delete the stored knowledge bases or categories that a complete listing no longer has
        in scope, with everything below them, and return the ones in scope now that were not
        in the last listing.

        What is stored is the last listing's scope, kept under ``key``; before there is one, only
        the listed groups out of scope can be stored ones. A listing with nothing in it removes
        nothing: that is the account failing, not every group leaving. A failed removal keeps the
        last scope, so the next run tries again.
        """
        if not listed:
            self.logger.warning(f"The {key} listing came back empty; nothing is removed")
            return set()
        stored = await self.category_sync_point.read_sync_point(key)
        previous = set(stored.get(ServiceNowDictKeys.IDS) or []) if stored else listed
        removed_all = True
        for group_id in sorted(previous - in_scope):
            try:
                await self.data_entities_processor.on_record_group_deleted(group_id, self.connector_id)
            except Exception as e:
                _raise_if_run_stop(e)
                self.logger.error(f"Could not remove {group_id}, which is out of scope now; retrying next sync: {e}")
                removed_all = False
        if removed_all:
            await self.category_sync_point.update_sync_point(key, {ServiceNowDictKeys.IDS: sorted(in_scope)})
        return in_scope - previous

    async def _sync_articles(self) -> None:
        """
        Sync KB articles and attachments from ServiceNow using batch processing.

        An article is stored under its number: each version of it is a row with its own sys_id,
        and the record follows the current row (see _current_article_query). A full read lists the
        current rows. A delta lists every row changed since the checkpoint, in any state, and
        resolves each changed number to its current row, which is written; a stored number with
        none is deleted. Then stored articles with no current row are deleted (_reconcile_articles).
        That also removes the records stored under a version's sys_id before articles were keyed by
        number: until it has run to the end once, every run reads every article.

        API Endpoints:
        - /api/now/table/kb_knowledge - Articles
        - /api/now/table/user_criteria - Permissions
        - /api/now/table/sys_attachment - Attachments
        """
        try:
            migrated = bool(await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ARTICLE_KEY_V2))
            last_sync_time = (
                await self._read_checkpoint(self.article_sync_point, ServiceNowSyncPointKeys.ARTICLES) if migrated else None
            )
            current = self._current_article_query()
            inline_changed, inline_checkpoint = await self._inline_image_changes()

            # (query, whether its rows move the checkpoint, whether its rows are versions to resolve)
            queries = [(current, True, False)]
            if last_sync_time:
                since = self._delta_since(last_sync_time)
                self.logger.info(f"🔄 Delta sync: Fetching articles updated after {since}")
                queries = [
                    # Every state: a new version, a retire or an unpublish changes some row of the article.
                    (f"{ServiceNowFields.SYS_UPDATED_ON}>{since}", True, True),
                    # Cannot Read is resolved to users here, so whom it denies changes without the
                    # article changing: articles that have it are re-read every run, not only when updated.
                    (
                        f"{current}^{ServiceNowFields.CANNOT_READ_USER_CRITERIA}ISNOTEMPTY"
                        f"^{ServiceNowFields.SYS_UPDATED_ON}<={since}",
                        False,
                        False,
                    ),
                    # An inline image changes without its article row changing.
                    *[(f"{ServiceNowFields.SYS_ID}IN{CommonStrings.COMMA.join(c)}", False, True)
                      for c in self._chunk_ids(inline_changed)],
                    # A knowledge base back in scope: its articles left with it and did not change since.
                    *[(f"{current}^{ServiceNowFields.KB_KNOWLEDGE_BASE}IN{CommonStrings.COMMA.join(c)}", False, False)
                      for c in self._chunk_ids(sorted(self._kbs_back_in_scope))],
                ]
            else:
                self.logger.info("🆕 Full sync: Fetching all articles")

            total_articles_synced = 0
            total_attachments_synced = 0
            total_records_dropped = 0
            latest_update_time = None
            failed = FailedItems()
            failed_ids: set[str] = set()  # kept by the reconcile (see _reconcile_articles)
            brought_by: dict[str, TableAPIRecord] = {}  # number -> the changed row that brought it, this page

            def note_failed(article_data: TableAPIRecord, *, moves_checkpoint: bool) -> None:
                # The checkpoint stays before the row that brought it, so the next run retries it.
                # The re-read pass lists its articles every run, so its failures do not hold it.
                row = brought_by.get(getattr(article_data, ServiceNowFields.NUMBER, None) or "", article_data)
                failed.add(
                    datetime_to_epoch_ms(row.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)
                    if moves_checkpoint else None
                )
                failed_ids.add(article_data.sys_id)
                self._sync_read_incomplete = True

            for query, moves_checkpoint, resolve in queries:
                # A failed page raises: a short list is indistinguishable from a complete one to
                # everything downstream, so the sync would report success over what it missed.
                async for rows in self._iter_table(
                    ServiceNowTables.KB_KNOWLEDGE, query, [ServiceNowFields.NUMBER] if resolve else _ARTICLE_FIELDS
                ):
                    if moves_checkpoint:
                        # Pages come oldest first, so the last row is the newest seen
                        latest_update_time = rows[-1].sys_updated_on

                    articles_data = rows
                    brought_by = {r.number: r for r in rows if r.number} if resolve else {}
                    if resolve:
                        numbers = set(brought_by)
                        articles_data = await self._current_versions(sorted(numbers), _ARTICLE_FIELDS)
                        gone = numbers - {a.number for a in articles_data}
                        try:
                            await self._delete_articles(gone)
                        except Exception as e:
                            _raise_if_run_stop(e)
                            self.logger.error(f"❌ Failed to delete {len(gone)} articles with no current version: {e}")
                            for row in rows:
                                if row.number in gone:
                                    note_failed(row, moves_checkpoint=moves_checkpoint)
                    if not articles_data:
                        continue

                    try:
                        attachments = await self._fetch_attachments_for_articles([a.sys_id for a in articles_data])
                    except Exception as e:
                        _raise_if_run_stop(e)
                        self.logger.error(f"❌ Failed to list the attachments of {len(articles_data)} articles: {e}")
                        for article_data in articles_data:
                            note_failed(article_data, moves_checkpoint=moves_checkpoint)
                        continue

                    # Collect RecordUpdates for this batch
                    record_updates = []
                    processed = []

                    for article_data in articles_data:
                        try:
                            updates = await self._process_single_article(
                                article_data, attachments.get(article_data.sys_id, [])
                            )
                        except Exception as e:
                            _raise_if_run_stop(e)
                            self.logger.error(
                                f"❌ Failed to process article {getattr(article_data, 'sys_id', 'unknown')}: {e}",
                                exc_info=True,
                            )
                            note_failed(article_data, moves_checkpoint=moves_checkpoint)
                            continue
                        if updates:
                            record_updates.extend(updates)
                            processed.append(article_data)
                            total_articles_synced += 1
                            # Count attachments
                            total_attachments_synced += len([u for u in updates if u.record.record_type == RecordType.FILE])

                    # Process batch of RecordUpdates
                    if record_updates:
                        total_records_dropped += await self._process_record_updates_batch(record_updates)

                    # After the write, which moved the listed attachments under the article's number.
                    for article_data in processed:
                        try:
                            await self._remove_attachments_gone(
                                article_data.number, {a.sys_id for a in attachments.get(article_data.sys_id, [])}
                            )
                        except Exception as e:
                            _raise_if_run_stop(e)
                            self.logger.error(f"❌ Failed to remove the deleted attachments of {article_data.sys_id}: {e}")
                            note_failed(article_data, moves_checkpoint=moves_checkpoint)

            # Update sync checkpoint
            if latest_update_time:
                checkpoint_ms = failed.checkpoint(
                    datetime_to_epoch_ms(latest_update_time, ServiceNowDefaults.DATETIME_FORMAT)
                )
                checkpoint = datetime.fromtimestamp(checkpoint_ms / 1000, tz=timezone.utc).strftime(
                    ServiceNowDefaults.DATETIME_FORMAT
                )
                if checkpoint != last_sync_time:
                    await self.article_sync_point.update_sync_point(
                        ServiceNowSyncPointKeys.ARTICLES, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: checkpoint}
                    )

            # Its articles are not held by the article checkpoint, so a failure reads them again.
            if not failed.count:
                await self.article_sync_point.update_sync_point(
                    ServiceNowSyncPointKeys.INLINE_IMAGES, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: inline_checkpoint}
                )
            for removal in (self._remove_expired_articles, self._apply_audited_deletes):
                try:
                    await removal()
                except Exception as e:
                    _raise_if_run_stop(e)
                    self.logger.error(f"❌ {removal.__name__} failed; it runs again next sync: {e}")
            if await self._reconcile_articles(force=not migrated, keep=failed_ids) and not migrated:
                await self.article_sync_point.update_sync_point(
                    ServiceNowSyncPointKeys.ARTICLE_KEY_V2, {ServiceNowDictKeys.DONE: True}
                )
                self.logger.info("Articles are now stored under their number")

            self.logger.info(
                f"✅ Articles synced: {total_articles_synced} articles, "
                f"{total_attachments_synced} attachments, "
                f"{total_records_dropped} record(s) dropped without permissions, "
                f"{failed.count} failed"
            )

        except Exception as e:
            self.logger.error(f"❌ Error syncing articles: {e}", exc_info=True)
            raise

    async def _current_versions(self, numbers: list[str], fields: list[str]) -> list[TableAPIRecord]:
        """The current row (see _current_article_query) of each of ``numbers`` that has one."""
        rows: list[TableAPIRecord] = []
        for chunk in self._chunk_ids(numbers):
            async for page in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE,
                f"{ServiceNowFields.NUMBER}IN{CommonStrings.COMMA.join(chunk)}^{self._current_article_query()}",
                fields,
            ):
                rows.extend(page)
        return rows

    async def _delete_articles(self, numbers: set[str]) -> None:
        """Delete the stored articles among ``numbers``, with their attachments."""
        for number in sorted(numbers):
            # Most numbers without a current version were never stored (drafts of new articles).
            if await self.data_entities_processor.get_record_by_external_id(self.connector_id, number):
                await self.data_entities_processor.delete_record_by_external_id(self.connector_id, number)

    async def _remove_attachments_gone(self, number: str, listed: set[str]) -> None:
        """Delete the stored attachments of article ``number`` that its complete attachment listing no longer has."""
        children = await self.data_entities_processor.get_records_by_parent(
            self.connector_id, number, RecordType.FILE.value
        )
        for child in children:
            if child.external_record_id not in listed:
                await self.data_entities_processor.on_record_deleted(child.id)

    async def _inline_image_changes(self) -> tuple[list[str], str]:
        """The sys_ids of the article rows whose inline images changed since the last run, and the
        checkpoint to store once those are synced. Before there is a checkpoint nothing is read:
        that run reads every article."""
        checkpoint = await self._read_checkpoint(self.article_sync_point, ServiceNowSyncPointKeys.INLINE_IMAGES)
        if not checkpoint:
            return [], self._utc_now()
        changed: set[str] = set()
        async for rows in self._iter_table(
            ServiceNowTables.SYS_ATTACHMENT,
            f"{ServiceNowFields.TABLE_NAME}={ServiceNowTables.INLINE_IMAGE}"
            f"^{ServiceNowFields.SYS_UPDATED_ON}>{self._delta_since(checkpoint)}",
            [ServiceNowFields.TABLE_SYS_ID],
        ):
            changed.update(r.table_sys_id for r in rows if r.table_sys_id)
            checkpoint = rows[-1].sys_updated_on
        return sorted(changed), checkpoint

    async def _reconcile_articles(self, *, force: bool, keep: set[str]) -> bool:
        """Delete the stored articles whose key is no current article's number: deleted at the
        source, which leaves no row a delta could see, or stored under a version's sys_id.
        Runs when forced, on a full sync, and otherwise at most once per
        RECONCILE_INTERVAL_SECONDS. True when it ran to the end; a listing that failed or came
        back empty deletes nothing.

        ``keep``: the sys_ids of the rows this run failed to write. Such an article is not under
        its number yet, so a record under its sys_id still holds its attachments; the run that
        writes it moves them. While such records are kept, every run reconciles.
        """
        kept_before = ((await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ARTICLES_RECONCILE))
                       or {}).get(ServiceNowDictKeys.KEPT)
        last = await self._read_checkpoint(self.article_sync_point, ServiceNowSyncPointKeys.ARTICLES_RECONCILE)
        now = datetime.now(timezone.utc)
        if not force and not kept_before and last and now - datetime.strptime(
            last, ServiceNowDefaults.DATETIME_FORMAT
        ).replace(tzinfo=timezone.utc) < timedelta(seconds=ServiceNowDefaults.RECONCILE_INTERVAL_SECONDS):
            return False
        try:
            current: set[str] = set()
            async for rows in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE, self._current_article_query(), [ServiceNowFields.NUMBER],
                page=ServiceNowDefaults.ID_PAGE_SIZE,
            ):
                current.update(r.number for r in rows if r.number)
            if not current:
                self.logger.warning("The article listing came back empty; no stored article is removed")
                return False
            stored = {r.external_record_id for r in
                      await self.data_entities_processor.get_records_by_record_type(self.connector_id, RecordType.WEBPAGE)}
            kept = sorted((stored - current) & keep)
            gone = sorted(stored - current - keep)
            if gone:
                self.logger.info(f"Removing {len(gone)} stored articles with no current version in ServiceNow")
            for key in gone:
                await self.data_entities_processor.delete_record_by_external_id(self.connector_id, key)
        except Exception as e:
            _raise_if_run_stop(e)
            self.logger.error(f"❌ Could not reconcile the stored articles; trying again next sync: {e}")
            return False
        await self.article_sync_point.update_sync_point(
            ServiceNowSyncPointKeys.ARTICLES_RECONCILE,
            {ServiceNowSyncPointKeys.LAST_SYNC_TIME: now.strftime(ServiceNowDefaults.DATETIME_FORMAT),
             ServiceNowDictKeys.KEPT: kept},
        )
        return True

    async def _remove_expired_articles(self) -> None:
        """Delete the articles whose ``valid_to`` passed since the last check: expiry changes no
        row, so no delta sees it. The first check only records the date; the run that makes it
        reads every article."""
        today = datetime.now(timezone.utc).strftime(ServiceNowDefaults.DATE_FORMAT)
        last = ((await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ARTICLES_EXPIRY)) or {}).get(
            ServiceNowSyncPointKeys.LAST_SYNC_TIME
        )
        if last == today:
            return
        if last:
            expired: set[str] = set()
            async for rows in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE,
                f"{ServiceNowFields.WORKFLOW_STATE}={ServiceNowFields.PUBLISHED}"
                f"^{ServiceNowFields.VALID_TO}>={last}^{ServiceNowFields.VALID_TO}<{today}",
                [ServiceNowFields.NUMBER],
            ):
                expired.update(r.number for r in rows if r.number)
            await self._delete_articles(expired)
        await self.article_sync_point.update_sync_point(
            ServiceNowSyncPointKeys.ARTICLES_EXPIRY, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: today}
        )

    async def _apply_audited_deletes(self) -> None:
        """Delete the articles whose deletion sys_audit_delete recorded since the last run, without
        waiting for the reconcile. Only an admin can read that table: on a 403 this pass does
        nothing and the reconcile stays the only way. Deleted categories need no pass: every run
        lists every category id (see _sync_categories)."""
        checkpoint = await self._read_checkpoint(self.article_sync_point, ServiceNowSyncPointKeys.AUDIT_DELETE)
        if checkpoint:
            numbers: set[str] = set()
            try:
                async for rows in self._iter_table(
                    ServiceNowTables.SYS_AUDIT_DELETE,
                    f"{ServiceNowFields.TABLENAME}={ServiceNowTables.KB_KNOWLEDGE}"
                    f"^{ServiceNowFields.SYS_UPDATED_ON}>{self._delta_since(checkpoint)}",
                    [ServiceNowFields.DISPLAY_VALUE],
                ):
                    # display_value is "<number> v<version>"
                    numbers.update(str(r.display_value).rsplit(" v", 1)[0] for r in rows if r.display_value)
                    checkpoint = rows[-1].sys_updated_on
            except ServiceNowAPIError as e:
                if e.status_code == HttpStatusCode.FORBIDDEN.value:
                    self.logger.debug("This account cannot read sys_audit_delete; the reconcile removes deleted articles")
                    return
                raise
            # A deleted old version of an article that still has a current one deletes nothing.
            live = {r.number for r in await self._current_versions(sorted(numbers), [ServiceNowFields.NUMBER])}
            await self._delete_articles(numbers - live)
        await self.article_sync_point.update_sync_point(
            ServiceNowSyncPointKeys.AUDIT_DELETE, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: checkpoint or self._utc_now()}
        )

    async def _process_single_article(
        self, article_data: TableAPIRecord, attachments_data: list[AttachmentMetadata]
    ) -> List[RecordUpdate]:
        """
        Process a single article and return RecordUpdate objects for article + attachments.

        Args:
            article_data: ServiceNow kb_knowledge TableAPIRecord
            attachments_data: its attachments, listed for the whole page (_fetch_attachments_for_articles)

        Returns:
            List[RecordUpdate]: RecordUpdate for article + RecordUpdates for attachments

        Raises:
            Exception: when the article's grants could not be read; the caller retries it next
                run instead of writing it without them.
        """
        article_sys_id = article_data.sys_id

        record_updates = []

        # Transform article to WebpageRecord
        article_record = self._transform_to_article_webpage_record(article_data)
        if not article_record:
            self.logger.warning(f"Failed to transform article {article_sys_id}")
            return []

        # Extract criteria IDs from article's can_read_user_criteria field
        can_read_criteria = article_data.can_read_user_criteria or ""
        criteria_ids = []
        if can_read_criteria:
            # Split comma-separated sys_ids
            criteria_ids = [c.strip() for c in can_read_criteria.split(",") if c.strip()]

        # Process READ permissions using shared method
        deny_ids = self._split_ids(getattr(article_data, ServiceNowFields.CANNOT_READ_USER_CRITERIA, None))
        if deny_ids:
            all_permission_objects = await self._article_deny_permissions(article_data, criteria_ids, deny_ids)
            # The list is complete and read every run, so it replaces the stored grants: a user
            # Cannot Read now denies loses the grant an earlier run wrote.
            article_record.rewrite_permissions = True
        else:
            all_permission_objects = await self._process_criteria_permissions(criteria_ids, PermissionType.READ)

        # Add OWNER permission from author field
        author_sys_id = article_data.author
        if author_sys_id:
            owner_perms = await self._convert_permissions_to_objects(
                [RawPermission(
                    entity_type="USER",
                    source_sys_id=author_sys_id,
                    role="OWNER",
                )],
            )
            all_permission_objects.extend(owner_perms)

        # Create RecordUpdate for article
        article_update = RecordUpdate(
            record=article_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=True,
            new_permissions=all_permission_objects,
            external_record_id=article_record.external_record_id,
        )
        record_updates.append(article_update)

        # Process attachments
        for att_data in attachments_data:
            att_sys_id = att_data.sys_id

            # Transform attachment to FileRecord
            att_record = self._transform_to_attachment_file_record(
                att_data,
                parent_record_group_type=article_record.record_group_type,
                parent_external_record_group_id=article_record.external_record_group_id,
                inherit_permissions=article_record.inherit_permissions,
                article=article_record,
            )

            if att_record:
                att_record.rewrite_permissions = article_record.rewrite_permissions
                # Attachments inherit all permissions from article
                attachment_update = RecordUpdate(
                    record=att_record,
                    is_new=True,
                    is_updated=False,
                    is_deleted=False,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=True,
                    new_permissions=all_permission_objects,  # Same as article
                    external_record_id=att_sys_id,
                )
                record_updates.append(attachment_update)

        return record_updates

    async def _process_record_updates_batch(self, record_updates: List[RecordUpdate]) -> int:
        """
        Process a batch of RecordUpdates using the data entities processor.

        This method converts RecordUpdates to (Record, Permissions) tuples and passes them
        to on_new_records() for batch processing.

        Args:
            record_updates: List of RecordUpdate objects

        Returns:
            int: Number of records dropped because they carry no permissions.
        """
        try:
            if not record_updates:
                return 0

            # Convert RecordUpdates to (Record, Permissions) tuples
            records_with_permissions = []
            dropped_external_ids = []
            for update in record_updates:
                if not update.record:
                    continue
                # A rewrite with no grants is written too: it removes the grants stored before.
                # So is an inheriting record: its group's readers reach it without grants of its own.
                if update.new_permissions or update.record.rewrite_permissions or update.record.inherit_permissions:
                    records_with_permissions.append((update.record, update.new_permissions or []))
                else:
                    dropped_external_ids.append(update.external_record_id)

            # A record with no principal reaches nobody, so it is not written. Name
            # the records, otherwise the sync reports a count it did not store.
            if dropped_external_ids:
                self.logger.warning(
                    "Dropped %d record(s) with no permissions: %s",
                    len(dropped_external_ids),
                    dropped_external_ids,
                )

            # Use processor's batch method
            if records_with_permissions:
                await self.data_entities_processor.on_new_records(records_with_permissions)

            return len(dropped_external_ids)

        except Exception as e:
            self.logger.error(f"Failed to process record updates batch: {e}", exc_info=True)
            raise

    async def _fetch_attachments_for_articles(
        self, article_sys_ids: list[str]
    ) -> dict[str, list[AttachmentMetadata]]:
        """
        The attachments of a page of articles, by article sys_id: one listing per IN chunk, not
        one per article.

        Raises:
            ServiceNowAPIError: a failed listing is not "no attachments"; read that way, a full
                sync would unlink the articles' attachments.
        """
        attachments: dict[str, list[AttachmentMetadata]] = defaultdict(list)
        for chunk in self._chunk_ids(article_sys_ids):
            async for rows in self._iter_table(
                ServiceNowTables.SYS_ATTACHMENT,
                f"{ServiceNowFields.TABLE_NAME}={ServiceNowTables.KB_KNOWLEDGE}"
                f"^{ServiceNowFields.TABLE_SYS_ID}IN{CommonStrings.COMMA.join(chunk)}",
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.FILE_NAME,
                    ServiceNowFields.CONTENT_TYPE,
                    ServiceNowFields.SIZE_BYTES,
                    ServiceNowFields.TABLE_SYS_ID,
                    ServiceNowFields.SYS_CREATED_ON,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
            ):
                for record in rows:
                    attachments[record.table_sys_id].append(AttachmentMetadata(**record.model_dump()))
        return attachments

    async def _convert_permissions_to_objects(
        self, permissions_dict: List[RawPermission],
    ) -> List[Permission]:
        """
        Convert USER and GROUP permissions from RawPermission format to Permission objects.

        ServiceNow-specific: Uses sourceUserId field to look up users, then gets their email.
        """
        permission_objects = []

        for perm in permissions_dict:
            try:
                entity_type = perm.entity_type
                source_sys_id = perm.source_sys_id
                role = perm.role

                if entity_type == EntityType.USER.value:
                    user = await self.data_entities_processor.get_user_by_source_id(
                        source_sys_id,
                        self.connector_id
                    )

                    if user:
                        permission_objects.append(
                            Permission(
                                email=user.email,
                                type=PermissionType(role),
                                entity_type=EntityType.USER,
                            )
                        )
                    else:
                        self.logger.warning(f"User not found for source_sys_id: {source_sys_id}")

                elif entity_type == EntityType.GROUP.value:
                    permission_objects.append(
                        Permission(
                            external_id=source_sys_id,
                            type=PermissionType(role),
                            entity_type=EntityType.GROUP,
                        )
                    )
                else:
                    self.logger.warning(f"Unknown entity_type '{entity_type}' in permission: {perm}")

            except Exception as e:
                self.logger.error(f"Failed to convert permission {perm}: {str(e)}", exc_info=True)

        return permission_objects

    async def _fetch_kb_permissions_from_criteria(
        self, kb_sys_id: str
    ) -> Dict[str, List[str]]:
        """
        Fetch permission criteria IDs for a knowledge base from mtom tables.

        ServiceNow KB permissions use many-to-many tables:
        - kb_uc_can_read_mtom: Read permissions (maps to READER)
        - kb_uc_can_contribute_mtom: Contribute permissions (maps to WRITER)

        Args:
            kb_sys_id: Knowledge base sys_id

        Returns:
            Dict with 'read' and 'write' lists of criteria sys_ids
        """
        try:
            criteria_map = {
                ServiceNowDictKeys.READ: [],
                ServiceNowDictKeys.CANNOT_READ: [],
                ServiceNowDictKeys.WRITE: []
            }

            # A failed read raises: an empty Can Read list would remove every reader of the KB,
            # and an empty Cannot Read list would grant the KB to users it denies.
            for key, table in (
                (ServiceNowDictKeys.READ, ServiceNowTables.KB_UC_CAN_READ_MTOM),
                (ServiceNowDictKeys.CANNOT_READ, ServiceNowTables.KB_UC_CANNOT_READ_MTOM),
                (ServiceNowDictKeys.WRITE, ServiceNowTables.KB_UC_CAN_CONTRIBUTE_MTOM),
            ):
                try:
                    async for rows in self._iter_table(
                        table, f"{ServiceNowFields.KB_KNOWLEDGE_BASE}={kb_sys_id}", [ServiceNowFields.USER_CRITERIA]
                    ):
                        criteria_map[key].extend(
                            r.user_criteria for r in rows if getattr(r, ServiceNowFields.USER_CRITERIA, None)
                        )
                except ServiceNowAPIError as e:
                    self.logger.error(f"Failed to fetch {table} for KB {kb_sys_id}: {e.message}")
                    raise

            return criteria_map

        except Exception as e:
            self.logger.error(f"Failed to fetch KB permissions: {e}", exc_info=True)
            raise

    async def _process_criteria_permissions(
        self, criteria_ids: list[str], permission_type: PermissionType,
        deny_criteria_ids: list[str] | None = None,
    ) -> list[Permission]:
        """
        Shared method to process user_criteria IDs and extract permissions.

        This method:
        1. Batch fetches all user_criteria details
        2. Extracts permissions from each criteria
        3. Converts to Permission objects

        The grant model has no deny and no AND. When a Cannot Read criteria applies, or a
        criteria has Match All over more than one field, the allowed users are computed
        here (allowed minus denied) and granted one by one. ServiceNow docs: a user who
        matches both Can Read and Cannot Read is denied; Match All requires a match in every
        populated field (any one element within a field), not just one field.
        A failed read raises _CriteriaUnreadable, so the caller keeps the stored grants:
        unknown is neither allowed nor empty.
        """
        try:
            if not criteria_ids:
                return []

            # Deny rows first: unknown deny must never read as "no deny".
            deny_records = await self._fetch_deny_records(deny_criteria_ids or [])
            can_records = self._allow_rows(await self._fetch_user_criteria(criteria_ids))

            if not deny_records and not any(self._needs_user_resolution(r) for r in can_records):
                permission_dicts = []
                for criteria_record in can_records:
                    permission_dicts.extend(
                        await self._extract_permissions_from_user_criteria_details(criteria_record, permission_type)
                    )
                return await self._convert_permissions_to_objects(permission_dicts)

            users = await self._users_allowed_by(can_records, deny_records)
            return await self._users_to_permissions(users, permission_type)

        except _CriteriaUnreadable:
            raise
        except Exception as e:
            self.logger.error(f"Failed to process criteria permissions: {e}", exc_info=True)
            raise _CriteriaUnreadable(str(e)) from e

    async def _fetch_deny_records(self, deny_ids: list[str]) -> list[TableAPIRecord]:
        """Active Cannot Read user_criteria rows. A failed read raises _CriteriaUnreadable."""
        try:
            return [r for r in await self._fetch_user_criteria(deny_ids) if self._criteria_is_active(r)]
        except Exception as e:
            raise _CriteriaUnreadable(str(e)) from e

    async def _users_allowed_by(self, can_records: list[TableAPIRecord], deny_records: list[TableAPIRecord]) -> set[str]:
        """Users matching any allow row minus users matching any deny row. A deny that cannot be
        resolved raises _CriteriaUnreadable; an allow that cannot be resolved raises too (grants nothing)."""
        if any(self._deny_reach_unknown(record) for record in deny_records):
            # Who a scripted or unevaluable Cannot Read denies is unknown, so it may deny anyone:
            # nobody is allowed through criteria (privileged readers are added by the callers).
            self.logger.warning("A Cannot Read criteria cannot be evaluated; denying everyone it may cover")
            return set()
        denied: set[str] = set()
        for record in deny_records:
            try:
                denied |= await self._users_matching_criteria(record, deny=True)
            except Exception as e:
                raise _CriteriaUnreadable(str(e)) from e
        allowed: set[str] = set()
        for record in can_records:
            allowed |= await self._users_matching_criteria(record)
        return allowed - denied

    async def _effective_users(self, allow_ids: list[str], deny_ids: list[str]) -> set[str]:
        deny_records = await self._fetch_deny_records(deny_ids)
        can_records = self._allow_rows(await self._fetch_user_criteria(allow_ids))
        return await self._users_allowed_by(can_records, deny_records)

    async def _all_app_users(self) -> list[AppUser]:
        """The connector's users, read once per sync run (after the user sync) for groups and grants."""
        if self._app_users is None:
            self._app_users = await self.data_entities_processor.get_all_app_users(connector_id=self.connector_id)
        return self._app_users

    async def _users_to_permissions(self, users: set[str], permission_type: PermissionType) -> list[Permission]:
        # One user listing per sync run, not a lookup per user or per article.
        if self._email_by_source_id is None:
            self._email_by_source_id = {
                u.source_user_id: u.email for u in await self._all_app_users() if u.source_user_id and u.email
            }
        email_by_id = self._email_by_source_id
        return [
            Permission(email=email_by_id[u], type=permission_type, entity_type=EntityType.USER)
            for u in sorted(users) if u in email_by_id
        ]

    async def _article_deny_permissions(
        self, article_data: TableAPIRecord, own_ids: list[str], deny_ids: list[str]
    ) -> list[Permission]:
        """Explicit READ grants of an article that has Cannot Read criteria.

        An inherited KB grant cannot be taken back, so such an article does not inherit and gets the
        effective set: (KB Can Read minus KB and article Cannot Read), narrowed by the article's own
        Can Read if it has one. ServiceNow docs: Cannot Read wins over Can Read at KB and article level
        (https://www.servicenow.com/docs/r/servicenow-platform/knowledge-management/user-access-knowledge.html).
        Where the article override property is on, the article's own Can Read replaces the KB's.
        Any unreadable rule raises, so the article is skipped and its stored grants stay.
        """
        if self.apply_article_read_criteria and own_ids:
            users = await self._effective_users(own_ids, deny_ids)
        else:
            kb_id = article_data.kb_knowledge_base
            if kb_id not in self._kb_criteria_cache:
                kb_map = await self._fetch_kb_permissions_from_criteria(kb_id)
                self._kb_criteria_cache[kb_id] = (
                    kb_map[ServiceNowDictKeys.READ],
                    kb_map.get(ServiceNowDictKeys.CANNOT_READ, []),
                )
            kb_allow, kb_deny = self._kb_criteria_cache[kb_id]
            users = await self._effective_users(kb_allow, kb_deny + deny_ids)
            if own_ids:
                users &= await self._effective_users(own_ids, [])
        # Knowledge admins read every KB and article, and a KB's owner and managers read their KB, without
        # user criteria being evaluated (ServiceNow docs, "Managing access to knowledge bases and knowledge
        # articles": https://www.servicenow.com/docs/r/servicenow-platform/knowledge-management/user-access-knowledge.html).
        # The KB loop grants the same people, so an article that stops inheriting must keep them.
        users |= await self._kb_privileged_users(article_data.kb_knowledge_base)
        return await self._users_to_permissions(users, PermissionType.READ)

    async def _kb_privileged_users(self, kb_id: str | None) -> set[str]:
        """Source ids of the admin users plus the KB's owner and managers. Raises if unreadable,
        so nothing is cached and the article keeps its stored grants."""
        if self._admin_source_ids is None:
            self._admin_source_ids = {a.source_user_id for a in await self._get_admin_users() if a.source_user_id}
        people = set(self._admin_source_ids)
        if kb_id and kb_id not in self._kb_owner_cache:
            ids: set[str] = set()
            async for rows in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE_BASE,
                f"{ServiceNowFields.SYS_ID}={kb_id}",
                [ServiceNowFields.OWNER, ServiceNowFields.KB_MANAGERS],
            ):
                for row in rows:
                    ids |= set(self._split_ids(getattr(row, ServiceNowFields.OWNER, None)))
                    ids |= set(self._split_ids(getattr(row, ServiceNowFields.KB_MANAGERS, None)))
            self._kb_owner_cache[kb_id] = ids
        return people | self._kb_owner_cache.get(kb_id, set())

    _CRITERIA_DIMENSIONS = (
        ServiceNowFields.USER, ServiceNowFields.GROUP, ServiceNowFields.ROLE,
        ServiceNowFields.DEPARTMENT, ServiceNowFields.LOCATION, ServiceNowFields.COMPANY,
    )

    @staticmethod
    def _split_ids(value: object) -> list[str]:
        return [s.strip() for s in str(value or "").split(",") if s.strip()]

    @staticmethod
    def _criteria_is_active(record: TableAPIRecord) -> bool:
        return str(getattr(record, ServiceNowFields.ACTIVE, "true")).lower() != "false"

    def _has_unevaluable_condition(self, record: TableAPIRecord) -> bool:
        """A script (Advanced) or a cost centre: conditions not evaluated here."""
        return (
            str(getattr(record, ServiceNowFields.ADVANCED, "false")).lower() == "true"
            or bool(self._split_ids(getattr(record, ServiceNowFields.COST_CENTER, None)))
        )

    def _deny_reach_unknown(self, record: TableAPIRecord) -> bool:
        """An unevaluable condition, or no field evaluated here: the users matched are unknown."""
        return self._has_unevaluable_condition(record) or not any(
            self._split_ids(getattr(record, d, None)) for d in self._CRITERIA_DIMENSIONS
        )

    def _allow_rows(self, records: list[TableAPIRecord]) -> list[TableAPIRecord]:
        """The active Can Read / Can Contribute rows that may grant. A condition not evaluated here
        counts as not met: under Match All the row grants nobody, under Match Any only its
        evaluated fields grant (they are all the evaluation below reads)."""
        return [
            r for r in records
            if self._criteria_is_active(r)
            and not (
                str(getattr(r, ServiceNowFields.MATCH_ALL, "false")).lower() == "true"
                and self._has_unevaluable_condition(r)
            )
        ]

    def _needs_user_resolution(self, record: TableAPIRecord) -> bool:
        match_all = str(getattr(record, ServiceNowFields.MATCH_ALL, "false")).lower() == "true"
        populated = [d for d in self._CRITERIA_DIMENSIONS if self._split_ids(getattr(record, d, None))]
        return match_all and len(populated) > 1

    async def _fetch_user_criteria(self, criteria_ids: list[str]) -> list[TableAPIRecord]:
        """user_criteria rows by sys_id, each read once per sync run. Raises on an API error."""
        missing = [c for c in dict.fromkeys(criteria_ids) if c not in self._criteria_rows]
        for chunk in self._chunk_ids(missing):
            async for page in self._iter_table(
                ServiceNowTables.USER_CRITERIA,
                f"{ServiceNowFields.SYS_ID}IN{CommonStrings.COMMA.join(chunk)}",
                [
                    ServiceNowFields.SYS_ID, ServiceNowFields.ACTIVE, ServiceNowFields.MATCH_ALL,
                    ServiceNowFields.ADVANCED, *self._CRITERIA_DIMENSIONS, ServiceNowFields.COST_CENTER,
                ],
            ):
                self._criteria_rows.update((r.sys_id, r) for r in page)
        for c in missing:
            self._criteria_rows.setdefault(c, None)  # deleted or hidden: not asked for again this run
        return [r for c in dict.fromkeys(criteria_ids) if (r := self._criteria_rows.get(c)) is not None]

    async def _with_child_groups(self, group_ids: list[str]) -> list[str]:
        """The groups and all their descendants (sys_user_group.parent). Raises on any read error.

        User criteria Group matching includes the members of child groups: reported by a
        ServiceNow community answer that tested it
        (https://www.servicenow.com/community/developer-forum/user-criteria-group-does-it-include-child-groups/m-p/1635493);
        no official page was found. The same flattening is done for synced groups. Departments do not
        include child departments, so they are not expanded.
        """
        seen = set(group_ids)
        frontier = list(group_ids)
        while frontier:
            children: list[str] = []
            for chunk in self._chunk_ids(frontier):
                async for rows in self._iter_table(
                    ServiceNowTables.SYS_USER_GROUP,
                    f"{ServiceNowFields.PARENT}IN{CommonStrings.COMMA.join(chunk)}",
                    [ServiceNowFields.SYS_ID],
                ):
                    children.extend(r.sys_id for r in rows if getattr(r, "sys_id", None))
            frontier = [c for c in children if c not in seen]
            seen.update(frontier)
        return sorted(seen)

    async def _users_matching_criteria(self, record: TableAPIRecord, *, deny: bool = False) -> set[str]:
        """sys_ids of the users one user_criteria row matches. Raises if any read fails.

        Child groups: whether group criteria match members of child groups is unverified (only a
        community answer says yes:
        https://www.servicenow.com/community/developer-forum/user-criteria-group-does-it-include-child-groups/m-p/1635493).
        So the safe side is taken per set: a deny (Cannot Read) expands child groups (denies more),
        an allow uses direct members only (grants less) unless CRITERIA_ALLOW_INCLUDES_CHILD_GROUPS is
        turned on, which should happen only after checking on a real instance.
        Roles count direct assignments (no hierarchy flattening). Scripted (advanced) and cost-centre
        conditions are not evaluated and fail closed: a deny denies everyone (_deny_reach_unknown), an
        allow counts them as not met (_allow_rows).
        """
        match_all = str(getattr(record, ServiceNowFields.MATCH_ALL, "false")).lower() == "true"
        sources = {
            ServiceNowFields.GROUP: (ServiceNowTables.SYS_USER_GRMEMBER, ServiceNowFields.GROUP, ServiceNowFields.USER),
            ServiceNowFields.ROLE: (ServiceNowTables.SYS_USER_HAS_ROLE, ServiceNowFields.ROLE, ServiceNowFields.USER),
            ServiceNowFields.DEPARTMENT: (ServiceNowTables.SYS_USER, ServiceNowFields.DEPARTMENT, ServiceNowFields.SYS_ID),
            ServiceNowFields.LOCATION: (ServiceNowTables.SYS_USER, ServiceNowFields.LOCATION, ServiceNowFields.SYS_ID),
            ServiceNowFields.COMPANY: (ServiceNowTables.SYS_USER, ServiceNowFields.COMPANY, ServiceNowFields.SYS_ID),
        }
        per_dimension: list[set[str]] = []
        for dim in self._CRITERIA_DIMENSIONS:
            ids = self._split_ids(getattr(record, dim, None))
            if not ids:
                continue
            if dim == ServiceNowFields.USER:
                per_dimension.append(set(ids))
                continue
            table, filter_field, user_field = sources[dim]
            if dim == ServiceNowFields.GROUP and (deny or CRITERIA_ALLOW_INCLUDES_CHILD_GROUPS):
                ids = await self._with_child_groups(ids)
            users: set[str] = set()
            for chunk in self._chunk_ids(ids):
                async for rows in self._iter_table(
                    table, f"{filter_field}IN{CommonStrings.COMMA.join(chunk)}", [user_field]
                ):
                    users.update(str(getattr(r, user_field)) for r in rows if getattr(r, user_field, None))
            per_dimension.append(users)
        if not per_dimension:
            return set()
        return set.intersection(*per_dimension) if match_all else set.union(*per_dimension)

    async def _extract_permissions_from_user_criteria_details(
        self, criteria_details: TableAPIRecord, permission_type: PermissionType
    ) -> List[RawPermission]:
        """
        Extract all permissions from a user_criteria record.

        User criteria can contain:
        - user: Individual user sys_id (comma-separated if multiple)
        - group: User group sys_id (comma-separated if multiple)
        - role: Role name (comma-separated if multiple, needs lookup)
        - department: Department sys_id (comma-separated if multiple)
        - location: Location sys_id (comma-separated if multiple)
        - company: Company sys_id (comma-separated if multiple)

        Args:
            criteria_details: user_criteria TableAPIRecord from ServiceNow
            permission_type: READER or WRITER

        Returns:
            List of RawPermission Pydantic models
        """
        permissions = []

        try:
            # Helper function to parse comma-separated sys_ids
            def parse_sys_ids(field_value: Optional[str]) -> List[str]:
                """Parse comma-separated sys_ids from field value."""
                if not field_value:
                    return []
                return [s.strip() for s in field_value.split(",") if s.strip()]

            # 1. Extract USER permissions
            user_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.USER, None))
            for user_sys_id in user_sys_ids:
                permissions.append(RawPermission(
                    entity_type="USER",
                    source_sys_id=user_sys_id,
                    role=permission_type.value,
                ))

            # 2. Extract GROUP permissions
            group_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.GROUP, None))
            for group_sys_id in group_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",
                    source_sys_id=group_sys_id,
                    role=permission_type.value,
                ))

            # 3. Extract ROLE permissions (role names need lookup)
            role_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.ROLE, None))
            for role_sys_id in role_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",  # Roles are stored as groups
                    source_sys_id=role_sys_id,
                    role=permission_type.value,
                ))

            # 4. Extract DEPARTMENT permissions (organizational entity)
            department_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.DEPARTMENT, None))
            for dept_sys_id in department_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",  # Org entities stored as groups
                    source_sys_id=dept_sys_id,
                    role=permission_type.value,
                ))

            # 5. Extract LOCATION permissions (organizational entity)
            location_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.LOCATION, None))
            for loc_sys_id in location_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",  # Org entities stored as groups
                    source_sys_id=loc_sys_id,
                    role=permission_type.value,
                ))

            # 6. Extract COMPANY permissions (organizational entity)
            company_sys_ids = parse_sys_ids(getattr(criteria_details, ServiceNowFields.COMPANY, None))
            for company_sys_id in company_sys_ids:
                permissions.append(RawPermission(
                    entity_type="GROUP",  # Org entities stored as groups
                    source_sys_id=company_sys_id,
                    role=permission_type.value,
                ))

        except Exception as e:
            self.logger.error(
                f"Error extracting permissions from user_criteria: {e}",
                exc_info=True
            )
        return permissions

    async def _transform_to_app_user(
        self, user_data: TableAPIRecord
    ) -> Optional[AppUser]:
        """
        Transform ServiceNow user to AppUser entity.

        Args:
            user_data: ServiceNow sys_user TableAPIRecord

        Returns:
            AppUser: Transformed user entity or None if invalid
        """
        try:
            sys_id = user_data.sys_id
            email = (user_data.email or "").strip()
            user_name = user_data.user_name or ""
            first_name = user_data.first_name or ""
            last_name = user_data.last_name or ""

            if not sys_id or not email:
                return None

            # Build full name
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                full_name = user_name or email

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if user_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(user_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if user_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(user_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            app_user = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=sys_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=full_name,
                is_active=False,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return app_user

        except Exception as e:
            self.logger.error(f"Error transforming user {getattr(user_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_user_group(
        self, group_data: SysUserGroup
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow group to AppUserGroup entity.

        Args:
            group_data: ServiceNow SysUserGroup Pydantic model

        Returns:
            AppUserGroup: Transformed user group entity or None if invalid
        """
        try:
            sys_id = group_data.sys_id
            name = group_data.name or ""

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if group_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(group_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if group_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(group_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Create AppUserGroup (for user groups, not record groups)
            user_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                source_user_group_id=sys_id,
                name=name,
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return user_group

        except Exception as e:
            self.logger.error(f"Error transforming group {getattr(group_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_organizational_group(
        self, entity_data: TableAPIRecord, prefix: str
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow organizational entity to AppUserGroup.

        This is a generic transform method for companies, departments, locations, and cost centers.

        Args:
            entity_data: ServiceNow entity TableAPIRecord (company, department, location, cost_center)
            prefix: Name prefix (COMPANY_, DEPARTMENT_, LOCATION_, COSTCENTER_)

        Returns:
            AppUserGroup: Transformed organizational group or None if invalid
        """
        try:
            sys_id = entity_data.sys_id
            name = entity_data.name or ""

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if entity_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(
                    entity_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT
                )
            if entity_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(
                    entity_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT
                )

            # Create AppUserGroup with prefix
            org_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                source_user_group_id=sys_id,
                name=f"{prefix}{name}",
                description=f"ServiceNow {prefix.rstrip('_')}: {name}",
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return org_group

        except Exception as e:
            self.logger.error(
                f"Error transforming organizational entity {getattr(entity_data, 'sys_id', 'unknown')}: {e}",
                exc_info=True,
            )
            return None

    def _transform_to_kb_record_group(
        self, kb_data: TableAPIRecord
    ) -> Optional[RecordGroup]:
        """
        Transform ServiceNow knowledge base to RecordGroup entity.

        Args:
            kb_data: ServiceNow kb_knowledge_base TableAPIRecord

        Returns:
            RecordGroup: Transformed KB as RecordGroup with type SERVICENOW or None if invalid
        """
        try:
            sys_id = kb_data.sys_id
            # An untitled KB is still written: its categories and articles hang under it.
            title = kb_data.title or sys_id

            if not sys_id:
                self.logger.warning("KB missing sys_id")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if kb_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(kb_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if kb_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(kb_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.KB_BASE.format(
                    instance_url=self.instance_url,
                    portal=self.kb_portal_suffix,
                    sys_id=sys_id,
                )

            # Create RecordGroup for Knowledge Base
            kb_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=title,
                description=kb_data.description or "",
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                group_type=RecordGroupType.SERVICENOWKB,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return kb_record_group

        except Exception as e:
            self.logger.error(f"Error transforming KB {getattr(kb_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_category_record_group(
        self, category_data: TableAPIRecord
    ) -> Optional[RecordGroup]:
        """
        Transform ServiceNow kb_category to RecordGroup entity.

        Args:
            category_data: ServiceNow kb_category TableAPIRecord

        Returns:
            RecordGroup: Transformed category as RecordGroup with type SERVICENOW_CATEGORY or None if invalid
        """
        try:
            sys_id = category_data.sys_id
            label = category_data.label or ""
            parent_sys_id = category_data.parent_id

            if not sys_id or not label:
                self.logger.warning(f"Category missing sys_id or label: {category_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if category_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(category_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if category_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(category_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.KB_CATEGORY.format(
                    instance_url=self.instance_url,
                    portal=self.kb_portal_suffix,
                    sys_id=sys_id,
                )

            # Create RecordGroup for Category
            category_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=label,
                short_name=category_data.value or "",
                parent_external_group_id=parent_sys_id,
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                group_type=RecordGroupType.SERVICENOW_CATEGORY,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                inherit_permissions=True,
            )

            return category_record_group

        except Exception as e:
            self.logger.error(f"Error transforming category {getattr(category_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_article_webpage_record(
        self, article_data: TableAPIRecord
    ) -> Optional[WebpageRecord]:
        """
        Transform ServiceNow kb_knowledge article to WebpageRecord entity.

        Args:
            article_data: ServiceNow kb_knowledge TableAPIRecord

        Returns:
            WebpageRecord: Transformed article or None if invalid
        """
        try:
            sys_id = article_data.sys_id
            # The record is the article, not this version of it (see _sync_articles)
            number = getattr(article_data, ServiceNowFields.NUMBER, None) or ""
            short_description = article_data.short_description or number

            if not sys_id or not number:
                self.logger.warning(f"Article {sys_id} has no sys_id or number")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if article_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(article_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if article_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(article_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.KB_ARTICLE.format(
                    instance_url=self.instance_url,
                    portal=self.kb_portal_suffix,
                    number=number,
                )

            # Extract category sys_id for external_record_group_id. Fall back to the KB when
            # the category is empty or this run did not sync it (see _sync_categories): an
            # article under a stub group named by its sys_id would reach nobody.
            kb_category_sys_id = article_data.kb_category
            if self._category_ids is not None and kb_category_sys_id not in self._category_ids:
                kb_category_sys_id = None
            external_record_group_id = None
            record_group_type = None

            # Try category first
            if kb_category_sys_id:
                external_record_group_id = kb_category_sys_id
                record_group_type = RecordGroupType.SERVICENOW_CATEGORY
            else:
                # Fallback to KB if no category
                kb_sys_id = article_data.kb_knowledge_base
                if kb_sys_id:
                    external_record_group_id = kb_sys_id
                    record_group_type = RecordGroupType.SERVICENOWKB
                else:
                    # No category and no KB - skip this article
                    self.logger.warning(f"Article {sys_id} has no category and no KB - skipping")
                    return None

            # An article that carries its own Can Read criteria must not also
            # receive the grants of its knowledge base, but only on an instance
            # where that override is switched on. See KBKnowledgeSNC.canRead.
            own_read_criteria = bool((article_data.can_read_user_criteria or "").strip())
            # An article with Cannot Read criteria must not inherit either: the KB grant could not be taken back.
            has_deny = bool(self._split_ids(getattr(article_data, ServiceNowFields.CANNOT_READ_USER_CRITERIA, None)))
            inherit_permissions = not (has_deny or (self.apply_article_read_criteria and own_read_criteria))

            # Create WebpageRecord for Article
            record_id = str(uuid.uuid4())
            article_record = WebpageRecord(
                id=record_id,
                inherit_permissions=inherit_permissions,
                external_record_id=number,
                # The processor rewrites a stored record, and re-queues it for
                # indexing, only when this differs from what it holds. Leaving it
                # unset froze every record at its first index.
                external_revision_id=article_data.sys_updated_on,
                version=0,
                record_name=short_description,
                record_type=RecordType.WEBPAGE,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                record_group_type=record_group_type,  # CATEGORY or KB
                external_record_group_id=external_record_group_id,  # Category or KB sys_id
                parent_external_record_id=None,
                weburl=web_url,
                mime_type=MimeTypes.HTML.value,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )
            return article_record

        except Exception as e:
            self.logger.error(f"Error transforming article {getattr(article_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None

    def _transform_to_attachment_file_record(
        self,
        attachment_data: AttachmentMetadata,
        parent_record_group_type: Optional[RecordGroupType] = None,
        parent_external_record_group_id: Optional[str] = None,
        *,
        inherit_permissions: bool = True,
        article: WebpageRecord | None = None,
    ) -> Optional[FileRecord]:
        """
        Transform ServiceNow sys_attachment to FileRecord entity.

        Args:
            attachment_data: ServiceNow sys_attachment AttachmentMetadata model
            parent_record_group_type: The record group type from parent article (CATEGORY or KB)
            parent_external_record_group_id: The external record group ID from parent article
            inherit_permissions: Follows the parent article, so an attachment never
                reaches a reader that the article itself keeps out
            article: The parent article's record: the attachment hangs under its number
                and links to its page (a sys_attachment.do link downloads the file)

        Returns:
            FileRecord: Transformed attachment or None if invalid
        """
        try:
            sys_id = attachment_data.sys_id
            file_name = attachment_data.file_name

            if not sys_id or not file_name:
                self.logger.warning(f"Attachment missing sys_id or file_name: {attachment_data}")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if attachment_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(attachment_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if attachment_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(attachment_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # ServiceNow sends "type;charset=..." and generic types for uploads it could not
            # type; the file name decides those. An unknown type still keeps the file.
            mime_type = (attachment_data.content_type or "").split(";")[0].strip().lower()
            if not mime_type or mime_type in ServiceNowDefaults.UNRELIABLE_MIME_TYPES:
                mime_type = mimetypes.guess_type(file_name, strict=False)[0] or MimeTypes.UNKNOWN.value

            # Parse file size
            file_size = None
            size_bytes = attachment_data.size_bytes
            if size_bytes:
                try:
                    file_size = int(size_bytes)
                except (ValueError, TypeError):
                    pass

            # Create FileRecord for Attachment
            attachment_record_id = str(uuid.uuid4())
            attachment_record = FileRecord(
                id=attachment_record_id,
                inherit_permissions=inherit_permissions,
                external_revision_id=attachment_data.sys_updated_on,
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=sys_id,
                version=0,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                mime_type=mime_type,
                parent_external_record_id=article.external_record_id if article else None,
                parent_record_type=RecordType.WEBPAGE,
                record_group_type=parent_record_group_type,
                external_record_group_id=parent_external_record_group_id,
                weburl=article.weburl if article else None,
                is_file=True,
                size_in_bytes=file_size,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                extension=file_name.rsplit(".", 1)[-1].lower() if "." in file_name else None,
            )

            return attachment_record

        except Exception as e:
            self.logger.error(f"Error transforming attachment {getattr(attachment_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            return None



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
    ) -> "ServiceNowConnector":
        """
        Factory method to create and initialize the connector.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider
            config_service: Configuration service

        Returns:
            ServiceNowConnector: Initialized connector instance
        """
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
