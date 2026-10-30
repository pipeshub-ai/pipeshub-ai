"""Linear Connector Implementation"""
import asyncio
import base64
import hashlib
import re
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime, timezone
from logging import Logger
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    CollectionNames,
    Connectors,
    ProgressStatus,
    RecordRelations,
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
    map_source_status,
    not_downloadable,
    not_found_at_source,
    raise_for_stream_fetch,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO, IconPaths
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
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterOperatorType,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.linear.api_gateway import (
    Invoke,
    LinearApiError,
    LinearAuthError,
    LinearForbidden,
    LinearGateway,
    LinearNotFound,
    LinearRateLimited,
)
from app.connectors.sources.linear.common.apps import LinearApp
from app.connectors.sources.linear.listing_checkpoint import (
    ListingCheckpoint,
    ListingRun,
)
from app.connectors.sources.linear.record_removal import RecordRemover
from app.connectors.sources.linear.source_reconciler import SourceReconciler
from app.connectors.sources.linear.team_access import (
    WORKSPACE_MEMBERS_GROUP_ID,
    WORKSPACE_MEMBERS_GROUP_NAME,
    boundary_ancestors,
    is_full_member,
    team_permissions,
    visibility,
)
from app.connectors.utils.value_mapper import ValueMapper, map_relationship_type
from app.models.blocks import (
    Block,
    BlockComment,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    ChildRecord,
    ChildType,
    CommentAttachment,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import Permission
from app.services.notification.types import (
    NotificationSeverity,
    NotificationType,
)
from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.linear.linear import LinearClient
from app.sources.client.linear.upload_urls import (
    content_disposition,
    is_linear_upload_url,
)
from app.sources.external.linear.linear import LinearDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Config path for Linear connector
LINEAR_CONFIG_PATH = "/services/connectors/{connector_id}/config"
# Renamed when projects stopped being shared into their non-lead teams: a key with no
# checkpoint re-lists every project once, and that rewrite drops the old team edges.
PROJECTS_CHECKPOINT_KEY = "projects-by-lead-team"

# Placeholder ancestor sweep (parent sub-issue hierarchy left out of sync filters)
PLACEHOLDER_SWEEP_BATCH: int = 50
PLACEHOLDER_SWEEP_MAX_DEPTH: int = 10
PLACEHOLDER_SWEEP_CONCURRENCY: int = 10
PLACEHOLDER_REVISION_PREFIX: str = "placeholder:"
# One page failing a sync loop drops the rest of that loop until the next sync, so a
# timeout, rate limit or 5xx gets a few more tries: waits of 2, 4 and 8 seconds.
LINEAR_TRANSIENT_RETRIES: int = 3
LINEAR_RETRY_BASE_DELAY_SEC: float = 2.0


def _is_transient_linear_failure(response: GraphQLResponse) -> bool:
    """A failed Linear response worth asking again: rate limited, a 5xx, or nothing came back."""
    errors = getattr(response, "errors", None)
    if isinstance(errors, list) and any(
        (getattr(error, "extensions", None) or {}).get("code") == "RATELIMITED" for error in errors
    ):
        return True
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status == HttpStatusCode.TOO_MANY_REQUESTS.value or status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value
    # The data source turns a transport failure, a read timeout among them, into a
    # response with neither a status nor errors.
    return status is None and errors is None

# Page sizes. Each is chosen so the query stays well under Linear's 10,000 point cap.
ISSUE_PAGE_SIZE: int = 50
TEAM_PAGE_SIZE: int = 25
# Projects carry their teams and issues inline; 25 a page costs about 6,800 of Linear's
# 10,000 points per query, 50 is refused
PROJECT_PAGE_SIZE: int = 10
ACTIVITY_PAGE_SIZE: int = 250
NESTED_PAGE_SIZE: int = 100
PROJECT_UPDATE_PAGE_SIZE: int = 50
ISSUE_REFRESH_BATCH: int = 50

# An uploaded file is its own record per place it is linked from, so that each copy
# follows the access of the issue or project it sits in.
FILE_PARENT_MARK: str = "#parent="

# Images are inlined into the indexed content; anything larger is left as a link.
MAX_INLINE_IMAGE_BYTES: int = 5 * 1024 * 1024
MAX_INLINE_IMAGES_TOTAL_BYTES: int = 20 * 1024 * 1024

# A team whose issues could not all be stored keeps its checkpoint for this many
# consecutive syncs, then moves on and reports it, so one bad issue cannot stall a team.
MAX_HELD_RUNS: int = 3


def file_record_id(url: str, parent_external_id: str) -> str:
    return f"{url}{FILE_PARENT_MARK}{parent_external_id}"


def file_record_url(external_record_id: str) -> str:
    return (external_record_id or "").split(FILE_PARENT_MARK, 1)[0]


def link_destination(raw: str) -> str:
    """The URL of a markdown link or image. Linear writes it bare, or in angle brackets
    (``[a](<https://...>)``, as it stores text that came through the API); either may be
    followed by a title. CommonMark allows all three."""
    raw = raw.strip()
    if raw.startswith("<"):
        end = raw.find(">")
        return raw[1:end].strip() if end > 0 else raw
    return raw.split(None, 1)[0] if raw else raw


def reads_current_content(record: Record, existing: Record | None) -> bool:
    """Whether storing *record* gets it indexed from the source as it is now.

    A record that is new, restored, a placeholder or at a new version is published
    for indexing. One stored again at its old version is not: an unchanged, indexed
    record is left alone, so a comment written since its last indexing would wait.
    """
    return (
        existing is None
        or getattr(existing, "is_deleted", False) is True
        or getattr(existing, "is_placeholder", False) is True
        or record.version != existing.version
    )


@ConnectorBuilder("Linear")\
    .in_group(AppGroups.LINEAR.value)\
    .with_description("Sync issues, comments, and projects from Linear")\
    .with_categories(["Issue Tracking", "Project Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Linear",
            authorize_url="https://linear.app/oauth/authorize",
            token_url="https://api.linear.app/oauth/token",
            redirect_uri="connectors/oauth/callback/Linear",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=["read"],
                agent=[]
            ),
            fields=[
                AuthField(
                    name="clientId",
                    display_name="Client ID",
                    placeholder="Enter your Linear OAuth Client ID",
                    description="OAuth Client ID from Linear (https://linear.app/settings/api)",
                    field_type="TEXT",
                    max_length=2000
                ),
                AuthField(
                    name="clientSecret",
                    display_name="Client Secret",
                    placeholder="Enter your Linear OAuth Client Secret",
                    description="OAuth Client Secret from Linear",
                    field_type="PASSWORD",
                    max_length=2000,
                    is_secret=True
                )
            ],
            documentation_links=[
                DocumentationLink(
                    "Linear OAuth Setup",
                    "https://linear.app/developers/oauth-2-0-authentication",
                    "setup"
                )
            ]
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="apiToken",
                display_name="API Token",
                placeholder="Enter your Linear personal API key",
                description="Personal API key from Linear",
                field_type="PASSWORD",
                required=True,
                max_length=2000,
                is_secret=True,
            ),
        ])
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.LINEAR.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Linear OAuth Setup",
            "https://linear.app/developers/oauth-2-0-authentication",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            "Linear API Key Setup",
            "https://linear.app/settings/api",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/linear/linear',
            'pipeshub'
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(
            name="team_ids",
            display_name="Teams",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter issues by team (leave empty for all teams)",
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter issues by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter issues by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="issues",
            display_name="Index Issues",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issues",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="issue_attachments",
            display_name="Index Issue Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="documents",
            display_name="Index Issue Documents",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue documents",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="files",
            display_name="Index Issue and Comment Files",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of files extracted from issue descriptions and comments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="projects",
            display_name="Index Projects",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of projects",
            default_value=True
        ))
    )\
    .build_decorator()
class LinearConnector(BaseConnector):
    """
    Linear connector for syncing issues, comments, and users from Linear
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
        super().__init__(
            LinearApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.external_client: Optional[LinearClient] = None
        self.data_source: Optional[LinearDataSource] = None
        self.organization_id: Optional[str] = None
        self.organization_name: Optional[str] = None
        self.organization_url_key: Optional[str] = None
        self.connector_id = connector_id
        self.connector_name = Connectors.LINEAR

        # Initialize value mapper (all mappings are now in defaults)
        self.value_mapper = ValueMapper()

        # Initialize sync points
        org_id = self.data_entities_processor.org_id

        self.issues_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.attachments_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.documents_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.projects_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.deletion_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.reconcile_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.sync_filters = None
        self.indexing_filters = None

        # Collaborators: one talks to Linear, one removes records, one compares both sides.
        self._api = LinearGateway(
            lambda: self._get_fresh_datasource(), logger, sleep=lambda seconds: self._sleep(seconds))
        self._remover = RecordRemover(data_entities_processor, connector_id, logger)
        self._reconciler = SourceReconciler(
            self._api, data_entities_processor, self._remover, self.reconcile_sync_point,
            connector_id, logger, now=self._now,
        )
        # Per-run state
        self._full_member_emails: set[str] = set()
        self._refreshed_issue_ids: set[str] = set()
        self._refreshed_project_ids: set[str] = set()
        self._team_visibility: dict[str, str] = {}
        # False once this run skipped something after an error; see full_sync_coverage
        self._read_everything = True
        # Parents of the issues this run wrote; see _sweep_placeholder_records
        self._issue_parent_ids: set[str] = set()

    @staticmethod
    def _now() -> int:
        return get_epoch_timestamp_in_ms()

    @staticmethod
    async def _sleep(seconds: float) -> None:
        await asyncio.sleep(seconds)

    def _checkpoint(self, sync_point: SyncPoint, key: str) -> ListingCheckpoint:
        return ListingCheckpoint(sync_point, key, self._now)

    def _notification_title(self, event: str) -> str:
        return f"{self.connector_instance_name or 'Linear'} connector {event}"

    async def init(self) -> bool:
        """
        Initialize Linear client using proper Client + DataSource architecture
        """
        try:
            # Use LinearClient.build_from_services() to create client with proper auth
            client = await LinearClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )

            # Store client for future use
            self.external_client = client

            # Create DataSource from client
            self.data_source = LinearDataSource(client)

            # Validate connection by fetching organization info
            org_response = await self._with_transient_retry("organization", self.data_source.organization)
            if not org_response.success:
                raise Exception(f"Failed to connect to Linear: {org_response.message}")

            org_data = org_response.data.get("organization", {}) if org_response.data else {}
            if not org_data:
                raise Exception("No organization data returned from Linear")

            self.organization_id = org_data.get("id")
            self.organization_name = org_data.get("name")
            self.organization_url_key = org_data.get("urlKey")

            self.logger.info(f"✅ Linear client initialized successfully for organization: {self.organization_name}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Linear client: {e}")
            raise ConnectorInitError(str(e)) from e

    async def _with_transient_retry(
        self,
        description: str,
        request: Callable[..., Awaitable[GraphQLResponse]],
        **arguments: object,
    ) -> GraphQLResponse:
        """Run one Linear request, asking again while it fails transiently."""
        response = await request(**arguments)
        for attempt in range(LINEAR_TRANSIENT_RETRIES):
            if response.success or not _is_transient_linear_failure(response):
                break
            delay = LINEAR_RETRY_BASE_DELAY_SEC * (2 ** attempt)
            self.logger.warning(
                "Linear %s failed (attempt %d/%d), retrying in %.0fs: %s",
                description, attempt + 1, LINEAR_TRANSIENT_RETRIES + 1, delay, response.message,
            )
            await asyncio.sleep(delay)
            response = await request(**arguments)
        return response

    async def _get_fresh_datasource(self) -> LinearDataSource:
        """
        Get LinearDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current token from config (supports both API_TOKEN and OAUTH)
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        Returns:
            LinearDataSource with current valid token
        """
        if not self.external_client:
            raise Exception("Linear client not initialized. Call init() first.")

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

        if not config:
            raise Exception("Linear configuration not found")

        # Extract fresh token (supports both API_TOKEN and OAUTH)
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "API_TOKEN")

        if auth_type == "OAUTH":
            credentials_config = config.get("credentials", {}) or {}
            fresh_token = credentials_config.get("access_token", "")
        else:
            # API_TOKEN
            fresh_token = auth_config.get("apiToken", "")

        if not fresh_token:
            raise Exception("No access token available")

        # Get current token from client
        internal_client = self.external_client.get_client()
        current_token = internal_client.get_token()

        # Update client's token if it changed (mutation)
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.set_token(fresh_token)

        # Return datasource with updated client
        return LinearDataSource(self.external_client)

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for a given filter field.

        Args:
            filter_key: The filter field name (e.g., "team_ids")
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search term to filter results
            cursor: Optional cursor for pagination (for cursor-based pagination)
        """
        if filter_key == "team_ids":
            return await self._get_team_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unknown filter field: {filter_key}")

    async def _get_team_options(
        self,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get team options for the team_ids filter with cursor-based pagination.

        Linear uses cursor-based pagination. For the first page, we don't pass a cursor.
        For subsequent pages, we use the cursor from the previous response.
        """
        # Ensure datasource is initialized
        if not self.data_source:
            await self.init()

        datasource = await self._get_fresh_datasource()

        try:
            # Build filter for server-side search if provided
            # Linear TeamFilter supports name filtering, but syntax may vary
            filter_dict: Optional[Dict[str, Any]] = None
            if search and search.strip():
                term = search.strip()
                filter_dict = {"or": [
                    {"name": {"containsIgnoreCase": term}},
                    {"key": {"containsIgnoreCase": term}},
                ]}

            # Not through the gateway: its rate-limit retries outlast the UI's request timeout.
            response = await datasource.execute_operation(
                "teamList", {"first": limit, "after": cursor, "filter": filter_dict})

            if not response.success:
                self.logger.error(f"❌ Failed to fetch teams: {response.message}")
                raise RuntimeError(f"Failed to fetch teams: {response.message}")

            teams_data = response.data.get("teams", {}) if response.data else {}
            teams_list = teams_data.get("nodes", [])

            # Get pagination info
            page_info = teams_data.get("pageInfo", {})
            has_more = page_info.get("hasNextPage", False)
            end_cursor = page_info.get("endCursor")

            # Convert to FilterOption objects
            options = [
                FilterOption(
                    id=team.get("id"),
                    label=f"{team.get('name', '')} ({team.get('key', '')})"
                )
                for team in teams_list
                if team.get("id") and team.get("name")
            ]

            self.logger.debug(
                f"✅ Fetched {len(options)} teams (page {page}, has_more: {has_more})"
            )

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=end_cursor
            )
        except Exception as e:
            self.logger.error(f"❌ Error fetching teams: {e}", exc_info=True)
            raise RuntimeError(f"Failed to fetch team options: {str(e)}")

    async def _register_authenticated_identity(self) -> None:
        """Record which source account this connector is authenticated as, so a creator whose
        PipesHub email differs still resolves that account's permissions for this connector."""
        if not self.data_source:
            return
        viewer = {}
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.viewer()
            if response.success and response.data:
                viewer = response.data.get("viewer") or {}
        except Exception as e:
            self.logger.debug("Could not read the authenticated Linear account: %s", e)
            return
        if not isinstance(viewer, dict):
            return
        email = viewer.get("email")
        await self.register_authenticated_source_user(
            email.strip() if isinstance(email, str) else None, viewer.get("id")
        )

    async def run_sync(self) -> None:
        """
        Main sync orchestration method.
        Syncs users, teams, issues, attachments, documents, projects from Linear, then
        removes what Linear no longer has.

        A step that cannot read Linear raises instead of ending quietly. Losing the
        token or exhausting the rate limit stops the sync (the next run resumes where
        this one stopped); any other failure is reported and the remaining steps run.
        """
        try:
            self.logger.info(f"🚀 Starting Linear sync for connector {self.connector_id}")

            if not self.data_source:
                init_error = RuntimeError(
                    f"Linear connector {self.connector_id} not initialized. Call init() first."
                )
                init_error._notification_sent = True
                raise init_error

            await self._register_authenticated_identity()

            # Load sync and indexing filters (loaded in run_sync to ensure latest values)
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "linear",
                self.connector_id,
                self.logger
            )
            self.logger.info(f"📋 Loaded filters - Sync: {self.sync_filters}, Indexing: {self.indexing_filters}")
            self._refreshed_issue_ids = set()
            self._refreshed_project_ids = set()
            self._read_everything = True
            self._issue_parent_ids = set()

            # Step 1: Get active users from system
            users = await self.data_entities_processor.get_all_active_users()
            if not users:
                self.logger.info("ℹ️ No active users found in system")
                return

            # Step 2: Fetch and sync Linear users
            linear_users = await self._fetch_users()
            if linear_users:
                await self.data_entities_processor.on_new_app_users(linear_users)
                self.logger.info(f"👥 Synced {len(linear_users)} Linear users")

            # Step 3: Get team_ids filter and fetch teams
            team_ids = None
            team_ids_operator = None
            team_ids_filter = self.sync_filters.get("team_ids") if self.sync_filters else None

            if team_ids_filter:
                team_ids = team_ids_filter.get_value(default=[])
                team_ids_operator = team_ids_filter.get_operator()
                if team_ids:
                    # Extract operator value string (handles both enum and string)
                    operator_value = team_ids_operator.value if hasattr(team_ids_operator, 'value') else str(team_ids_operator) if team_ids_operator else "in"
                    action = "Excluding" if operator_value == "not_in" else "Including"
                    self.logger.info(f"📋 Filter: {action} teams by IDs: {team_ids}")
                else:
                    self.logger.info("📋 Team filter is empty, will fetch no teams")
            else:
                self.logger.info("📋 No team filter set - will fetch all teams")

            # Step 4: Build email map from already-synced users for team member lookup
            user_email_map: Dict[str, AppUser] = {}
            if linear_users:
                for app_user in linear_users:
                    if app_user.email:
                        user_email_map[app_user.email.lower()] = app_user

            # Step 5: Fetch and sync teams (as both UserGroups and RecordGroups)
            team_user_groups, team_record_groups = await self._fetch_teams(
                team_ids=team_ids,
                team_ids_operator=team_ids_operator,
                user_email_map=user_email_map
            )

            # Step 6a: Sync the workspace-members group and the teams as UserGroups.
            # The workspace group goes first: public teams are granted to it.
            workspace_group = self._workspace_members_group(user_email_map)
            await self.data_entities_processor.on_new_user_groups([workspace_group, *team_user_groups])
            total_members = sum(len(members) for _, members in team_user_groups)
            self.logger.info(f"👥 Synced {len(team_user_groups)} Linear teams as UserGroups ({total_members} total memberships)")

            # A team's key is part of every issue's identifier and URL, but changing it
            # does not mark the issues as updated: read such a team from the beginning.
            await self._forget_checkpoints_of_rekeyed_teams(team_record_groups)

            # Step 6b: Sync teams as RecordGroups (content organization)
            if team_record_groups:
                await self.data_entities_processor.on_new_record_groups(team_record_groups)
                self.logger.info(f"📁 Synced {len(team_record_groups)} Linear teams as RecordGroups")

            synced_team_ids = {
                g.external_group_id for g, _ in team_record_groups if g.external_group_id
            }

            # Step 6c: Remove teams that were deleted or hidden from this account
            await self._run_step("remove deleted teams", self._remove_stale_teams(synced_team_ids))

            # Step 7: Sync issues for teams
            full_sync_team_ids, failed_issue_team_keys = await self._sync_issues_for_teams(team_record_groups)

            if failed_issue_team_keys:
                preview = ", ".join(failed_issue_team_keys[:5])
                if len(failed_issue_team_keys) > 5:
                    preview += f" (+{len(failed_issue_team_keys) - 5} more)"
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title=self._notification_title("couldn't sync some teams"),
                    message=(
                        f"Couldn't sync issues for {len(failed_issue_team_keys)} team(s): {preview}. "
                        "Retry sync; check Linear access if it keeps failing."
                    ),
                )

            # A team read from the beginning brings issues and projects whose attachments,
            # documents and projects are older than the workspace-wide checkpoints of
            # those listings.
            if full_sync_team_ids:
                await self._checkpoint(self.attachments_sync_point, "attachments").reset()
                await self._checkpoint(self.documents_sync_point, "documents").reset()
                await self._checkpoint(self.projects_sync_point, PROJECTS_CHECKPOINT_KEY).reset()

            # Step 8: Sync attachments separately (Linear doesn't update issue.updatedAt when attachments are added)
            await self._sync_attachments(team_record_groups)

            # Step 9: Sync documents separately (Linear doesn't update issue.updatedAt when documents are added)
            await self._sync_documents(team_record_groups)

            # Step 10: Sync projects
            await self._sync_projects_for_teams(team_record_groups)

            # Step 11: Comments and project updates change what an issue or project says
            # without changing the issue or project itself.
            await self._run_step("pick up new comments and project updates", self._sync_comment_activity(synced_team_ids))

            # Step 12: Sync deleted issues
            await self._sync_deleted_issues(team_record_groups)

            # Step 13: Sync deleted projects
            await self._sync_deleted_projects(team_record_groups)

            # Step 14: Remove what Linear no longer has (deleted for good, moved out of
            # scope, deleted attachments and documents)
            await self._run_step("check for moved or deleted records", self._reconcile(synced_team_ids, forced=bool(full_sync_team_ids)))

            # Step 15: Backfill placeholder parent stubs left by date/incremental filters
            placeholders_backfilled = await self._sweep_placeholder_records(
                synced_team_ids=synced_team_ids,
                full_sync_team_ids=full_sync_team_ids,
            )

            self.logger.info(
                "✅ Linear sync completed; placeholders backfilled: %s",
                placeholders_backfilled,
            )

        except Exception as e:
            self.logger.error(f"❌ Error during Linear sync: {e}", exc_info=True)
            if not isinstance(e, ConnectorInitError) and not getattr(e, "_notification_sent", False):
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title=self._notification_title("sync failed"),
                    message=self._sync_failure_message(e),
                )
            raise

    @staticmethod
    def _sync_failure_message(error: Exception) -> str:
        if isinstance(error, LinearAuthError):
            return (
                "Linear rejected the connector's credentials, so nothing could be synced. "
                "Reconnect Linear (or replace the API key) in the connector's settings."
            )
        if isinstance(error, LinearRateLimited):
            return (
                "Linear's rate limit was reached and did not clear in time. The sync stopped "
                "where it was and the next run continues from there."
            )
        return (
            f"The sync stopped due to an error: {str(error)[:200]}. Recent Linear changes "
            "may not be reflected yet. Run the sync again; if it keeps failing, "
            "check the connector's configuration."
        )

    @staticmethod
    def _stops_the_sync(error: BaseException) -> bool:
        """Retrying the next step cannot help when the token is dead or the hourly
        allowance is spent; every other failure is local to the step that hit it."""
        return isinstance(error, (LinearAuthError, LinearRateLimited))

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A full sync that skipped something after an error (a team, a page, a step) did
        not read all of Linear, so nothing it did not write may be unlinked after it."""
        return FullSyncCoverage(complete=self._read_everything)

    async def _run_step[T](self, name: str, step: Awaitable[T]) -> T | None:
        """Run one optional step of the sync. Its failure is reported, not fatal."""
        try:
            return await step
        except Exception as e:
            if self._stops_the_sync(e):
                raise
            self._read_everything = False
            self.logger.error(f"❌ Linear sync step '{name}' failed: {e}", exc_info=True)
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title(f"couldn't finish: {name}"),
                message=f"The step '{name}' failed: {str(e)[:200]}. It will run again on the next sync.",
            )
            return None

    def _workspace_members_group(
        self, user_email_map: dict[str, AppUser]
    ) -> tuple[AppUserGroup, list[AppUser]]:
        """The group public teams are granted to: active accounts that are not guests."""
        group = AppUserGroup(
            id=str(uuid4()),
            org_id=self.data_entities_processor.org_id,
            source_user_group_id=WORKSPACE_MEMBERS_GROUP_ID,
            connector_id=self.connector_id,
            app_name=Connectors.LINEAR,
            name=WORKSPACE_MEMBERS_GROUP_NAME,
            description="Everyone in the Linear workspace except guests",
        )
        members = [user for email, user in user_email_map.items() if email in self._full_member_emails]
        return group, members

    async def _forget_checkpoints_of_rekeyed_teams(
        self, team_record_groups: list[tuple[RecordGroup, list[Permission]]]
    ) -> None:
        stored = await self.data_entities_processor.get_nodes_by_filters(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": self.connector_id},
            ["externalGroupId", "shortName"])
        old_keys = {doc.get("externalGroupId"): doc.get("shortName") for doc in stored or []}
        for group, _ in team_record_groups:
            old_key = old_keys.get(group.external_group_id)
            if old_key and group.short_name and old_key != group.short_name:
                self.logger.info(f"Team key changed from {old_key} to {group.short_name}; re-reading its issues")
                await self._team_issue_checkpoint(group.external_group_id).reset()

    async def _remove_stale_teams(self, synced_team_ids: set[str]) -> None:
        """Remove teams Linear deleted or no longer shows this account, and forget their
        checkpoints so a team that comes back is read from the beginning.

        A team the filter leaves out is not removed: a filter change runs a full sync,
        after which the platform unlinks what that sync did not write."""
        for team_id in await self._reconciler.remove_stale_teams(synced_team_ids):
            await self._team_issue_checkpoint(team_id).reset()

    async def _reconcile(self, synced_team_ids: set[str], *, forced: bool) -> None:
        if await self._reconciler.due(forced=forced):
            await self._reconciler.run(synced_team_ids)

    async def _fetch_users(self) -> List[AppUser]:
        """
        Fetch all Linear users using cursor-based pagination.
        Returns list of AppUser objects.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        all_users: List[Dict[str, Any]] = []
        try:
            async for users_list, _, _ in self._api.pages(
                "users", ["users"], page_size=50,
                fetch=lambda ds, first, after: ds.users(first=first, after=after),
            ):
                all_users.extend(users_list)
        except LinearApiError as e:
            if self._stops_the_sync(e):
                raise
            raise RuntimeError(f"Failed to fetch users: {e}") from e

        # Convert Linear users to AppUser objects
        app_users: List[AppUser] = []
        full_members: set[str] = set()

        for user in all_users:
            user_id = user.get("id")
            email = user.get("email")
            active = user.get("active", True)

            # Skip inactive users
            if not active:
                continue

            # Skip users without email (required for AppUser)
            if not email:
                self.logger.debug(f"Skipping user {user_id} - no email address")
                continue

            if is_full_member(user):
                full_members.add(email.lower())

            # Use displayName if available, otherwise name, otherwise email
            full_name = user.get("displayName") or user.get("name") or email

            # Parse updatedAt timestamp
            source_updated_at = None
            updated_at_str = user.get("updatedAt")
            if updated_at_str:
                source_updated_at = self._parse_linear_datetime(updated_at_str)

            app_user = AppUser(
                app_name=Connectors.LINEAR,
                connector_id=self.connector_id,
                source_user_id=user_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=full_name,
                is_active=active,
                source_updated_at=source_updated_at
            )
            app_users.append(app_user)

        self._full_member_emails = full_members
        self.logger.info(f"📥 Fetched {len(all_users)} Linear users, converted {len(app_users)} to AppUser")
        return app_users

    async def _fetch_teams(
        self,
        team_ids: Optional[List[str]] = None,
        team_ids_operator: Optional[FilterOperatorType] = None,
        user_email_map: Optional[Dict[str, AppUser]] = None
    ) -> Tuple[List[Tuple[AppUserGroup, List[AppUser]]],List[Tuple[RecordGroup, List[Permission]]]]:
        """
        Fetch Linear teams and convert them to both UserGroups and RecordGroups.

        Dual approach:
        - UserGroups: Track WHO is in each team (membership management)
        - RecordGroups: Track WHAT each team contains (issues/content organization)
        - Permissions: the team's UserGroup → RecordGroup, plus the workspace-members group for public teams

        Args:
            team_ids: Optional list of team IDs to include/exclude
            team_ids_operator: Optional filter operator (IN or NOT_IN)
            user_email_map: Optional map of email -> AppUser for already-synced users.

        Returns:
            Tuple of:
            - List of (AppUserGroup, List[AppUser]) for membership tracking
            - List of (RecordGroup, List[Permission]) for content organization
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        all_teams: List[Dict[str, Any]] = []
        # Build filter if specific team IDs are requested (build once before pagination loop)
        filter_dict: Optional[Dict[str, Any]] = None
        if team_ids:
            # Check operator: "in" (include) or "not_in" (exclude)
            if team_ids_operator:
                operator_value = team_ids_operator.value if hasattr(team_ids_operator, 'value') else str(team_ids_operator)
            else:
                operator_value = "in"

            is_exclude = operator_value == "not_in"

            if is_exclude:
                # Linear TeamFilter supports "nin" for not in
                filter_dict = {"id": {"nin": team_ids}}
            else:
                # Linear TeamFilter supports "in" for include
                filter_dict = {"id": {"in": team_ids}}

        try:
            async for teams_list, _, _ in self._api.pages(
                "teams", ["teams"], page_size=TEAM_PAGE_SIZE,
                fetch=lambda ds, first, after: ds.teams(first=first, after=after, filter=filter_dict),
            ):
                for team in teams_list:
                    # A team lists 10 members inline (Linear multiplies nested connection sizes into its
                    # per-query complexity limit); the rest come in further pages.
                    await self._api.complete(team, "members", "teamMembers", ["team", "members"],
                                             page_size=NESTED_PAGE_SIZE)
                all_teams.extend(teams_list)
        except LinearApiError as e:
            if self._stops_the_sync(e):
                raise
            raise RuntimeError(f"Failed to fetch teams: {e}") from e

        # A restricted team is readable by the members of its parent teams. Those must
        # exist as user groups even when the team filter leaves them out.
        teams_by_id = {t["id"]: t for t in all_teams if t.get("id")}
        synced_team_ids = set(teams_by_id)
        await self._add_missing_ancestor_teams(teams_by_id)

        # Convert teams to both UserGroups and RecordGroups
        user_groups: List[Tuple[AppUserGroup, List[AppUser]]] = []
        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        # Linear has no per-issue access, so a team's audience is everything in it.
        # A team with sub-teams is not declared: a private sub-team is hidden from the
        # parent's members, and a declared group admits every group below it. A
        # filtered sync may not list a team's sub-teams, so it declares none.
        parent_team_ids = {str((t.get("parent") or {}).get("id")) for t in all_teams if t.get("parent")}

        for team in teams_by_id.values():
            team_id = team.get("id")
            team_name = team.get("name")
            team_key = team.get("key")
            team_description = team.get("description")
            team_members = (team.get("members") or {}).get("nodes") or []

            if not team_id or not team_name:
                self.logger.warning(f"Skipping team with missing id or name: {team}")
                continue

            # Extract parent team information
            parent_team = team.get("parent")
            parent_external_group_id = None
            if parent_team:
                parent_external_group_id = parent_team.get("id")
                self.logger.debug(f"Team {team_key} has parent team: {parent_team.get('name')} ({parent_external_group_id})")

            # Build team URL if we have organization urlKey
            web_url: Optional[str] = None
            if self.organization_url_key and team_key:
                web_url = f"https://linear.app/{self.organization_url_key}/team/{team_key}"

            # 1. Create UserGroup for membership tracking
            user_group = AppUserGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                source_user_group_id=team_id,
                connector_id=self.connector_id,
                app_name=Connectors.LINEAR,
                name=team_name,
                description=team_description,
            )

            # Get AppUser objects for team members (use already-synced users)
            member_app_users: List[AppUser] = []
            for member in team_members:
                member_email = member.get("email")
                if member_email and user_email_map:
                    # Look up already-synced AppUser by email
                    app_user = user_email_map.get(member_email.lower())
                    if app_user:
                        member_app_users.append(app_user)
                    else:
                        self.logger.warning(f"⚠️ Team member {member_email} not found in synced users - skipping")

            user_groups.append((user_group, member_app_users))
            if team_id not in synced_team_ids:
                continue  # an ancestor fetched only for its members

            # 2. Create RecordGroup for content organization
            record_group = RecordGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                external_group_id=team_id,
                connector_id=self.connector_id,
                connector_name=Connectors.LINEAR,
                name=team_name,
                short_name=team_key or team_id,
                group_type=RecordGroupType.PROJECT,
                web_url=web_url,
                parent_external_group_id=parent_external_group_id,
                permission_model=(
                    PermissionModel.RECORD_GROUP_LEVEL
                    if not team_ids and str(team_id) not in parent_team_ids
                    else None
                ),
            )

            # 3. Permissions: see team_access (team members; workspace members for public
            #    teams; parent team members for restricted teams)
            self._team_visibility[team_id] = visibility(team)
            record_groups.append((record_group, team_permissions(team, teams_by_id)))

        self.logger.info(
            f"📥 Fetched {len(all_teams)} Linear teams, "
            f"created {len(user_groups)} UserGroups and {len(record_groups)} RecordGroups"
        )
        return user_groups, record_groups

    async def _add_missing_ancestor_teams(self, teams_by_id: dict[str, dict[str, Any]]) -> None:
        """Fetch the parent teams restricted teams need but the team filter left out."""
        for _ in range(10):  # parent chains are short; this bounds a malformed one
            missing = sorted({
                ancestor
                for team in list(teams_by_id.values()) if visibility(team) == "restricted"
                for ancestor in boundary_ancestors(team, teams_by_id)
                if ancestor not in teams_by_id
            })
            if not missing:
                return
            found: list[dict[str, Any]] = []
            async for teams_list, _, _ in self._api.pages(
                "parent teams", ["teams"], page_size=TEAM_PAGE_SIZE,
                fetch=lambda ds, first, after, ids=missing: ds.teams(first=first, after=after, filter={"id": {"in": ids}}),
            ):
                for team in teams_list:
                    await self._api.complete(team, "members", "teamMembers", ["team", "members"],
                                             page_size=NESTED_PAGE_SIZE)
                found.extend(teams_list)
            found = [t for t in found if t.get("id") in missing]
            if not found:
                return  # gone or invisible to this account: nobody is granted through them
            teams_by_id.update({t["id"]: t for t in found})

    async def _sync_issues_for_teams(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> Tuple[set[str], List[str]]:
        """
        Sync issues for all teams with batch processing and incremental sync.

        Each team has its own checkpoint, stored under the team's id (a team's key can be
        changed and later taken by another team). The checkpoint moves only when the
        team's whole list was read and stored; an interrupted run is resumed from its
        last stored page.

        Args:
            team_record_groups: List of (RecordGroup, permissions) tuples for teams to sync

        Returns:
            Tuple of (full_sync_team_ids, failed_team_keys).
        """
        if not team_record_groups:
            self.logger.info("ℹ️ No teams to sync issues for")
            return set(), []

        full_sync_team_ids: set[str] = set()
        failed_team_keys: List[str] = []

        for team_record_group, team_perms in team_record_groups:
            try:
                team_id = team_record_group.external_group_id
                team_key = team_record_group.short_name or team_record_group.name

                # Skip teams without external_group_id (shouldn't happen, but defensive check)
                if not team_id:
                    self.logger.warning(f"⚠️ Skipping team {team_key}: missing external_group_id")
                    continue

                self.logger.info(f"📋 Starting issue sync for team: {team_key}")

                checkpoint = self._team_issue_checkpoint(team_id)
                run = await checkpoint.begin()

                if run.is_full:
                    full_sync_team_ids.add(team_id)
                else:
                    self.logger.info(f"🔄 Incremental sync for team {team_key} from {run.since}")

                # The issue query is expensive in Linear's complexity points even when it
                # returns nothing. An incremental run first asks, for a few points,
                # whether anything changed at all.
                if not run.is_full and not run.resumed and not await self._team_has_changes(team_id, run.since):
                    await checkpoint.finish(run)
                    self.logger.info(f"ℹ️ No new/updated issues for team {team_key}")
                    continue

                total_records_processed = 0
                total_tickets = 0
                total_files = 0
                failures: list[str] = []

                async for batch_records in self._fetch_issues_for_team_batch(
                    team_id=team_id,
                    team_key=team_key,
                    last_sync_time=run.since,
                    run=run,
                    checkpoint=checkpoint,
                    failures=failures,
                ):
                    if not batch_records:
                        continue

                    for record, _ in batch_records:
                        if isinstance(record, TicketRecord):
                            total_tickets += 1
                            if parent_id := getattr(record, "parent_external_record_id", None):
                                self._issue_parent_ids.add(parent_id)
                        elif isinstance(record, FileRecord):
                            total_files += 1

                    total_records_processed += len(batch_records)
                    await self.data_entities_processor.on_new_records(batch_records)

                if failures:
                    self._read_everything = False
                if failures and await self._hold_checkpoint(checkpoint, team_key, failures):
                    failed_team_keys.append(team_record_group.name or team_key)
                else:
                    await checkpoint.finish(run)

                # Log final status
                if total_records_processed > 0:
                    parts = []
                    if total_tickets > 0:
                        parts.append(f"{total_tickets} tickets")
                    if total_files > 0:
                        parts.append(f"{total_files} files")
                    summary = ", ".join(parts) if parts else "0 records"
                    self.logger.info(f"✅ Team {team_key}: {summary} ({total_records_processed} total)")
                else:
                    self.logger.info(f"ℹ️ No new/updated issues for team {team_key}")

            except Exception as e:
                if self._stops_the_sync(e):
                    raise
                self._read_everything = False
                team_name = team_record_group.name or team_record_group.short_name or "unknown"
                self.logger.error(f"❌ Error syncing issues for team {team_name}: {e}", exc_info=True)
                failed_team_keys.append(team_name)
                continue

        return full_sync_team_ids, failed_team_keys

    async def _team_has_changes(self, team_id: str, since: int | None) -> bool:
        """Whether the team has any issue the incremental listing would return."""
        team_filter: dict[str, Any] = {"team": {"id": {"eq": team_id}}}
        self._apply_date_filters_to_linear_filter(team_filter, since)
        data = await self._api.call("issueIds", {"first": 1, "filter": team_filter})
        return bool(((data.get("issues") or {}).get("nodes") or []))

    def _team_issue_checkpoint(self, team_id: str) -> ListingCheckpoint:
        return self._checkpoint(self.issues_sync_point, f"team_issues_{team_id}")

    async def _hold_checkpoint(self, checkpoint: ListingCheckpoint, team_key: str, failures: list[str]) -> bool:
        """Some issues of this run could not be stored. Keep the checkpoint so the next
        sync reads them again, but not forever: after ``MAX_HELD_RUNS`` syncs in a row
        the checkpoint moves on. Returns True while the checkpoint is being held."""
        held = await checkpoint.hold()
        if held <= MAX_HELD_RUNS:
            self.logger.warning(
                "%s: %s item(s) could not be stored; they will be read again (%s/%s)",
                team_key, len(failures), held, MAX_HELD_RUNS)
            return True
        self.logger.error(
            "%s: giving up on %s item(s) after %s syncs: %s",
            team_key, len(failures), MAX_HELD_RUNS, ", ".join(failures[:10]))
        return False

    async def _sweep_placeholder_records(
        self,
        synced_team_ids: set[str],
        full_sync_team_ids: set[str] | None = None,
    ) -> int:
        """Backfill metadata for placeholder parent stubs left unreconciled.

        Date/incremental filters don't respect hierarchy: an in-scope child can sync
        while its parent (and higher ancestors) stay out of the window, leaving stubs
        keyed by the ancestors' issue ids with no name, status or weburl.

        Returns:
            Total number of placeholder stubs refreshed from source (all BFS depths).
        """
        if not synced_team_ids:
            return 0

        full_sync_team_ids = full_sync_team_ids or set()
        visited: set[str] = set()
        frontier: list[Record] = []
        for stub in await self.data_entities_processor.get_placeholder_records(self.connector_id):
            if (
                stub.record_type != RecordType.TICKET
                or stub.external_record_group_id not in synced_team_ids
                or stub.external_record_id in visited
            ):
                continue
            # Skip already-backfilled stubs on incremental sync; re-submit on full sync
            # so BELONGS_TO / parent edges wiped by full-sync edge deletion are restored.
            needs_backfill = stub.external_revision_id is None
            needs_edge_restore = stub.external_record_group_id in full_sync_team_ids
            if not (needs_backfill or needs_edge_restore):
                continue
            visited.add(stub.external_record_id)
            frontier.append(stub)

        # A parent stored in full that the date filters now leave out becomes a stub too
        # once one of its sub-issues is synced. Written, it stays linked after a full sync
        # (which unlinks what it did not write), and the sub-issue stays under it.
        for parent_id in sorted(self._issue_parent_ids - visited):
            parent = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=parent_id)
            if parent is not None and self._dated_out(parent, synced_team_ids):
                visited.add(parent_id)
                frontier.append(parent)

        if not frontier:
            return 0

        total_backfilled = 0
        depth = 0
        while frontier:
            depth += 1
            self.logger.info("Placeholder sweep: backfilling %s ancestor stub(s)", len(frontier))
            issues = await self._fetch_ancestor_level(frontier)

            backfills: list[tuple[Record, list[Permission]]] = []
            parent_refs: list[str] = []
            for stub, issue in zip(frontier, issues):
                record: Record | None = None
                if issue:
                    # Always anchored and inheriting, so the issues below it stay readable;
                    # titled only where its readers could see the parent in Linear.
                    record = self._build_ancestor_stub(
                        issue, stub, describe=self._may_describe_ancestor(issue, stub, synced_team_ids))
                    if record is not None:
                        total_backfilled += 1
                if record is None:
                    record = stub
                record.is_placeholder = True
                backfills.append((record, []))
                if record.parent_external_record_id:
                    parent_refs.append(record.parent_external_record_id)

            await self.data_entities_processor.on_new_records(backfills)

            next_frontier: list[Record] = []
            for parent_ext_id in parent_refs:
                if parent_ext_id in visited:
                    continue
                visited.add(parent_ext_id)
                parent_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=parent_ext_id,
                )
                if parent_record is None or not (
                        parent_record.is_placeholder or self._dated_out(parent_record, synced_team_ids)):
                    continue
                next_frontier.append(parent_record)

            if depth >= PLACEHOLDER_SWEEP_MAX_DEPTH and next_frontier:
                self.logger.error(
                    "Placeholder sweep hit the depth cap (%s) with %s stub(s) unresolved; aborting",
                    PLACEHOLDER_SWEEP_MAX_DEPTH,
                    len(next_frontier),
                )
                break
            frontier = next_frontier

        self.logger.info(
            "Placeholder sweep: backfilled %s ancestor stub(s) total",
            total_backfilled,
        )
        return total_backfilled

    async def _fetch_ancestor_level(
        self, frontier: list[Record]
    ) -> list[dict[str, Any] | None]:
        """Fetch one frontier level, aligned with ``frontier`` (``None`` if missing)."""
        issue_by_id: dict[str, dict[str, Any]] = {}

        for i in range(0, len(frontier), PLACEHOLDER_SWEEP_BATCH):
            chunk = frontier[i:i + PLACEHOLDER_SWEEP_BATCH]
            issue_ids = list(dict.fromkeys(
                str(stub.external_record_id)
                for stub in chunk
                if stub.external_record_id
            ))
            if not issue_ids:
                continue
            fetched = await self._search_ancestors_by_id_filter(issue_ids)
            if fetched is None:
                fetched = await self._fetch_ancestors_by_get(issue_ids)
            else:
                missing = [iid for iid in issue_ids if iid not in fetched]
                if missing:
                    fetched.update(await self._fetch_ancestors_by_get(missing))
            issue_by_id.update(fetched)

        return [issue_by_id.get(str(stub.external_record_id)) for stub in frontier]

    async def _search_ancestors_by_id_filter(
        self,
        issue_ids: list[str],
    ) -> dict[str, dict[str, Any]] | None:
        """Batch-fetch issues via ``issues(filter: {id: {in: ...}})``.

        Returns ``None`` when the caller should fall back to per-id ``issue``.
        """
        if not issue_ids:
            return {}

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.issues(
                first=len(issue_ids),
                filter={"id": {"in": issue_ids}},
            )
        except Exception as e:
            self.logger.warning(
                "Placeholder sweep: issues id-in failed for %s id(s): %s — using issue()",
                len(issue_ids),
                e,
            )
            return None

        if not response.success:
            self.logger.warning(
                "Placeholder sweep: issues id-in failed for %s id(s): %s — using issue()",
                len(issue_ids),
                response.message,
            )
            return None

        payload = response.data or {}
        issues_conn = payload.get("issues") or {}
        nodes = issues_conn.get("nodes") or []
        return {
            str(issue["id"]): issue
            for issue in nodes
            if isinstance(issue, dict) and issue.get("id")
        }

    async def _fetch_ancestors_by_get(
        self,
        issue_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Concurrent ``issue(id)``; skips missing/inaccessible ids."""
        if not issue_ids:
            return {}

        semaphore = asyncio.Semaphore(PLACEHOLDER_SWEEP_CONCURRENCY)
        results: dict[str, dict[str, Any]] = {}

        async def fetch_one(issue_id: str) -> None:
            async with semaphore:
                try:
                    datasource = await self._get_fresh_datasource()
                    response = await datasource.issue(id=issue_id)
                    if not response.success:
                        return
                    issue = (response.data or {}).get("issue")
                    if isinstance(issue, dict) and issue.get("id"):
                        results[str(issue["id"])] = issue
                except Exception as e:
                    self.logger.debug(
                        "Placeholder sweep: issue(%s) failed: %s", issue_id, e
                    )

        await asyncio.gather(*(fetch_one(i) for i in issue_ids))
        return results

    def _may_describe_ancestor(
        self, issue_data: dict[str, Any], stub: Record, synced_team_ids: set[str]
    ) -> bool:
        """Whether a stub may carry its issue's title and status.

        The stub is filed under the child's team. Naming the parent there is fine when
        the parent is in that same team, or in a synced public team. A parent in a
        private team, or in a team this connector does not sync, stays an unnamed stub.
        """
        team_id = (issue_data.get("team") or {}).get("id")
        if not team_id:
            return False
        if team_id == stub.external_record_group_id:
            return True
        return team_id in synced_team_ids and self._team_visibility.get(team_id) == "public"

    def _build_ancestor_stub(
        self, issue_data: dict[str, Any], stub: Record, *, describe: bool = True
    ) -> Record | None:
        """Refresh a stub's metadata from source, keeping it a stub.

        Builds a minimal TicketRecord (no relations) so backfill does not create
        related-issue placeholders outside the parent chain. Without ``describe`` it
        carries the identifier only: no title, status, priority or people.
        """
        if not describe:
            issue_data = {k: issue_data.get(k) for k in ("id", "identifier", "team", "parent", "url")}
        team = issue_data.get("team") or {}
        # Prefer stub's group so cross-team parents stay anchored to the child's team.
        team_id = stub.external_record_group_id or team.get("id") or ""
        if not team_id:
            self.logger.warning(
                "Placeholder sweep: skipping stub %s — missing team id",
                stub.external_record_id,
            )
            return None

        issue_id = issue_data.get("id") or stub.external_record_id
        identifier = issue_data.get("identifier") or ""
        title = issue_data.get("title") or ""
        if identifier and title:
            record_name = f"[{identifier}] {title}"
        elif identifier:
            record_name = identifier
        elif title:
            record_name = title
        else:
            record_name = issue_id

        priority_num = issue_data.get("priority")
        if priority_num is None:
            priority_str = None
        elif priority_num == 0:
            priority_str = "none"
        else:
            priority_str = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}.get(priority_num)
        priority = self.value_mapper.map_priority(priority_str)

        state = issue_data.get("state") or {}
        status = self.value_mapper.map_status(state.get("name"))
        if status and not isinstance(status, Status):
            status = self.value_mapper.map_status(state.get("type"))

        parent = issue_data.get("parent") or {}
        parent_external_id = parent.get("id") if isinstance(parent, dict) else None

        assignee = issue_data.get("assignee") or {}
        creator = issue_data.get("creator") or {}
        created_at = self._parse_linear_datetime(issue_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(issue_data.get("updatedAt", "")) or 0

        type_value = ItemType.SUB_ISSUE if parent_external_id else ItemType.ISSUE

        return TicketRecord(
            id=stub.id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.TICKET,
            external_record_id=stub.external_record_id,
            external_revision_id=f"{PLACEHOLDER_REVISION_PREFIX}{updated_at}",
            external_record_group_id=team_id,
            record_group_type=RecordGroupType.PROJECT,
            parent_external_record_id=parent_external_id,
            parent_record_type=RecordType.TICKET if parent_external_id else None,
            version=stub.version,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.UNKNOWN.value,
            weburl=issue_data.get("url"),
            source_created_at=created_at,
            source_updated_at=updated_at,
            status=status,
            priority=priority,
            type=type_value,
            assignee=assignee.get("displayName") or assignee.get("name"),
            assignee_email=assignee.get("email"),
            creator_email=creator.get("email"),
            creator_name=creator.get("displayName") or creator.get("name"),
            inherit_permissions=True,
            preview_renderable=False,
            is_placeholder=True,
        )

    async def _fetch_issues_for_team_batch(
        self,
        team_id: str,
        team_key: str,
        last_sync_time: Optional[int] = None,
        batch_size: int = ISSUE_PAGE_SIZE,
        run: ListingRun | None = None,
        checkpoint: ListingCheckpoint | None = None,
        failures: list[str] | None = None,
    ) -> AsyncGenerator[List[Tuple[Record, List[Permission]]], None]:
        """
        Fetch issues for a team with pagination and incremental sync, yielding batches.

        Linear lists newest-created first and the listing is filtered by ``updatedAt``,
        so the pages are stable while it is read. A page that cannot be read raises:
        the caller must never take a failed page for the end of the list.

        Args:
            team_id: Team ID
            team_key: Team key (e.g., "ENG")
            last_sync_time: Read issues updated after this instant (ms); None reads all
            batch_size: Number of issues to fetch per batch
            run, checkpoint: when given, the cursor is saved after each stored page
            failures: collects the identifiers of issues that could not be converted

        Yields:
            Batches of (record, permissions) tuples
        """
        # Build filter for team
        team_filter: Dict[str, Any] = {
            "team": {"id": {"eq": team_id}}
        }

        # Apply date filters to team_filter
        self._apply_date_filters_to_linear_filter(team_filter, last_sync_time)

        after_cursor = run.cursor if run else None
        is_full_sync = last_sync_time is None

        while True:
            try:
                pages = self._api.pages(
                    "issues", ["issues"], page_size=batch_size, after=after_cursor,
                    fetch=lambda ds, first, after: ds.issues(first=first, after=after, filter=team_filter),
                )
                async for issues_list, end_cursor, _ in pages:
                    if issues_list:
                        self.logger.info(f"📋 Fetched {len(issues_list)} issues for team {team_key}")

                    batch_records: list[tuple[Record, list[Permission]]] = []
                    arrived: dict[str, str] = {}
                    for issue_data in issues_list:
                        # Linear's team filter also returns sub-team issues; each is synced in its own team's pass.
                        issue_team_id = (issue_data.get("team") or {}).get("id")
                        if issue_team_id and issue_team_id != team_id:
                            continue
                        try:
                            batch_records.extend(await self._issue_to_records(
                                issue_data, team_id, is_full_sync=is_full_sync,
                                arrived=None if is_full_sync else arrived))
                        except Exception as e:
                            if self._stops_the_sync(e):
                                raise
                            issue_ref = issue_data.get("identifier") or issue_data.get("id", "unknown")
                            self.logger.error(f"❌ Error processing issue {issue_ref}: {e}", exc_info=True)
                            if failures is not None:
                                failures.append(issue_ref)
                            continue

                    # An issue the store has not seen, met by an incremental listing: it was
                    # restored, moved in, or newly passes a filter. Its attachments and
                    # documents may be older than those listings' checkpoints.
                    if arrived:
                        batch_records.extend(await self._children_of_arrived_issues(arrived, team_id))

                    if batch_records:
                        yield batch_records

                    # The consumer has stored this page: it is safe to resume after it.
                    if run is not None and checkpoint is not None:
                        await checkpoint.advance(run, end_cursor)
                return
            except LinearApiError as e:
                # A cursor saved by an interrupted run that Linear no longer accepts:
                # read this run's list from the top instead.
                if run is not None and run.resumed and checkpoint is not None and type(e) is LinearApiError:
                    self.logger.warning(f"Team {team_key}: saved cursor rejected ({e}); restarting the listing")
                    await checkpoint.abandon_cursor(run)
                    after_cursor = None
                    continue
                raise

    async def _issue_to_records(
        self,
        issue_data: dict[str, Any],
        team_id: str,
        *,
        is_full_sync: bool = False,
        arrived: dict[str, str] | None = None,
    ) -> list[tuple[Record, list[Permission]]]:
        """Everything one issue contributes: its ticket and the files linked from its
        description and comments. Also removes file records the issue no longer links
        and moves the issue's children when the issue changed team.

        ``arrived`` collects (issue id -> record id) for issues that are new to the
        store or are coming back from its trash, so the caller can fetch their
        attachments and documents."""
        issue_id = issue_data.get("id", "")
        records: list[tuple[Record, list[Permission]]] = []

        # Look up existing record to handle versioning
        existing_record = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id,
            external_record_id=issue_id
        )

        # Transform issue to TicketRecord with existing record info
        ticket_record = self._transform_issue_to_ticket_record(
            issue_data, team_id, existing_record
        )

        # Set indexing status based on filters
        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUES):
            ticket_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        # Records inherit permissions from RecordGroup (team), so pass empty list
        records.append((ticket_record, []))
        # Only an issue this run re-indexes counts as refreshed: a listing in the
        # overlap window returns it unchanged, and skipping it in the comment pass
        # then loses the comment, whose activity checkpoint moves past it.
        if reads_current_content(ticket_record, existing_record):
            self._refreshed_issue_ids.add(issue_id)
        if arrived is not None and (existing_record is None or getattr(existing_record, "is_deleted", False) is True):
            arrived[issue_id] = ticket_record.id

        # An issue that changed team takes its files, attachments and documents along
        moved = existing_record is not None and existing_record.external_record_group_id not in (None, team_id)
        is_full_sync = is_full_sync or moved

        # Note: Comments are handled as blocks during streaming, not as separate records

        linked_urls: set[str] = set()

        # Extract files from issue description
        issue_description = issue_data.get("description") or ""
        if issue_description:
            issue_created_at = self._parse_linear_datetime(issue_data.get("createdAt", "")) or 0
            issue_updated_at = self._parse_linear_datetime(issue_data.get("updatedAt", "")) or 0
            linked_urls.update(f["url"] for f in self._extract_file_urls_from_markdown(issue_description, exclude_images=True))

            new_file_records, _ = await self._extract_files_from_markdown(
                markdown_text=issue_description,
                parent_external_id=issue_id,
                parent_node_id=ticket_record.id,
                parent_record_type=RecordType.TICKET,
                team_id=team_id,
                parent_created_at=issue_created_at,
                parent_updated_at=issue_updated_at,
                parent_weburl=ticket_record.weburl,
                exclude_images=True,
                is_full_sync=is_full_sync,
            )
            records.extend(new_file_records)

        # Extract files from comment bodies and create FileRecords. The list query
        # carries the first comments; an issue with more gets the remaining pages.
        comments_data = await self._api.complete(
            issue_data, "comments", "issueComments", ["issue", "comments"], page_size=NESTED_PAGE_SIZE)
        for comment_data in comments_data:
            comment_id = comment_data.get("id", "")
            comment_body = comment_data.get("body") or ""

            if not comment_id or not comment_body:
                continue

            comment_created_at = self._parse_linear_datetime(comment_data.get("createdAt", "")) or 0
            comment_updated_at = self._parse_linear_datetime(comment_data.get("updatedAt", "")) or 0
            comment_url = comment_data.get("url") or ticket_record.weburl
            linked_urls.update(f["url"] for f in self._extract_file_urls_from_markdown(comment_body, exclude_images=True))

            new_comment_file_records, _ = await self._extract_files_from_markdown(
                markdown_text=comment_body,
                parent_external_id=issue_id,
                parent_node_id=ticket_record.id,
                parent_record_type=RecordType.TICKET,
                team_id=team_id,
                parent_created_at=comment_created_at,
                parent_updated_at=comment_updated_at,
                parent_weburl=comment_url,
                exclude_images=True,
                indexing_filter_key=IndexingFilterKey.FILES,
                is_full_sync=is_full_sync,
            )
            records.extend(new_comment_file_records)

        if existing_record is not None:
            await self._settle_children(parent_external_id=issue_id, keep_file_urls=linked_urls)
        if moved:
            records.extend(await self._moved_issue_children(issue_id, ticket_record.id, team_id))

        return records

    async def _settle_children(
        self,
        parent_external_id: str,
        keep_file_urls: set[str] | None = None,
        keep_child_ids: dict[RecordType, set[str]] | None = None,
    ) -> None:
        """Remove an issue's or project's stored children that the parent no longer has:
        file records whose link is gone from the text (``keep_file_urls``), and links or
        documents no longer listed (``keep_child_ids``)."""
        children = await self.data_entities_processor.get_records_by_parent(
            self.connector_id, parent_external_id)
        for child in children:
            if child.record_type == RecordType.TICKET:
                continue  # a sub-issue is synced as an issue of its own
            if child.record_type == RecordType.FILE and keep_file_urls is not None:
                # A file stored under the bare URL predates per-parent file records.
                if file_record_url(child.external_record_id) not in keep_file_urls \
                        or FILE_PARENT_MARK not in (child.external_record_id or ""):
                    await self._remover.remove_record(child)
                    continue
            wanted = (keep_child_ids or {}).get(child.record_type)
            if wanted is not None and child.external_record_id not in wanted:
                await self._remover.remove_record(child)

    async def _moved_issue_children(
        self, issue_id: str, issue_node_id: str, team_id: str
    ) -> list[tuple[Record, list[Permission]]]:
        """An issue changed team: its attachments and documents are filed again under
        the new team so that they follow the issue's access."""
        attachments = await self._api.all_nodes(
            "issueAttachments", ["issue", "attachments"], {"id": issue_id}, page_size=NESTED_PAGE_SIZE)
        documents = await self._api.all_nodes(
            "issueDocuments", ["issue", "documents"], {"id": issue_id}, page_size=NESTED_PAGE_SIZE)
        return await self._issue_child_records(issue_id, issue_node_id, team_id, attachments, documents)

    async def _children_of_arrived_issues(
        self, arrived: dict[str, str], team_id: str
    ) -> list[tuple[Record, list[Permission]]]:
        """Attachments and documents of issues that newly entered the sync, read in one
        request for the whole page of issues."""
        records: list[tuple[Record, list[Permission]]] = []
        issue_ids = list(arrived)
        nodes = await self._api.all_nodes(
            "issueChildren", ["issues"], {"filter": {"id": {"in": issue_ids}}, "includeArchived": True},
            page_size=len(issue_ids))
        for node in nodes:
            issue_id = node.get("id")
            if issue_id not in arrived:
                continue
            attachments = await self._api.complete(
                node, "attachments", "issueAttachments", ["issue", "attachments"], page_size=NESTED_PAGE_SIZE)
            documents = await self._api.complete(
                node, "documents", "issueDocuments", ["issue", "documents"], page_size=NESTED_PAGE_SIZE)
            records.extend(await self._issue_child_records(
                issue_id, arrived[issue_id], team_id, attachments, documents))
        return records

    async def _issue_child_records(
        self,
        issue_id: str,
        issue_node_id: str,
        team_id: str,
        attachments: list[dict[str, Any]],
        documents: list[dict[str, Any]],
    ) -> list[tuple[Record, list[Permission]]]:
        records: list[tuple[Record, list[Permission]]] = []
        for attachment in attachments:
            if not attachment.get("id") or not attachment.get("url"):
                continue
            existing = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=attachment["id"])
            link = self._transform_attachment_to_link_record(attachment, issue_id, issue_node_id, team_id, existing)
            if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
                link.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            records.append((link, []))
        for document in documents:
            if not document.get("id") or not document.get("url"):
                continue
            existing = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=document["id"])
            page = self._transform_document_to_webpage_record(document, issue_id, issue_node_id, team_id, existing)
            if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.DOCUMENTS):
                page.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            records.append((page, []))
        return records

    async def _sync_attachments(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync attachments separately from issues.

        Linear doesn't update issue.updatedAt when attachments are added,
        so we query attachments directly with their own sync point.
        This ensures new attachments are captured even if the parent issue wasn't updated.
        """
        if not team_record_groups:
            return

        # Build team lookup map for quick access
        team_map: Dict[str, Tuple[RecordGroup, List[Permission]]] = {}
        for team_record_group, team_perms in team_record_groups:
            team_id = team_record_group.external_group_id
            if team_id:
                team_map[team_id] = (team_record_group, team_perms)

        try:
            # Attachments have their own checkpoint (separate from the issues' ones)
            checkpoint = self._checkpoint(self.attachments_sync_point, "attachments")
            run = await checkpoint.begin()
            total_attachments = 0

            # Build filter for attachments
            attachment_filter: Dict[str, Any] = {}
            # Only the checkpoint applies here. The connector's date filters choose
            # issues; an attachment belongs to its issue however old it is.
            self._apply_checkpoint_to_linear_filter(attachment_filter, run.since)
            failures: list[str] = []

            async for attachments_list, end_cursor, _ in self._api.pages(
                "attachments", ["attachments"], page_size=50, after=run.cursor,
                fetch=lambda ds, first, after: ds.attachments(
                    first=first, after=after, filter=attachment_filter if attachment_filter else None),
            ):
                if attachments_list:
                    self.logger.info(f"📎 Fetched {len(attachments_list)} attachments")

                # Process attachments
                batch_records: List[Tuple[Record, List[Permission]]] = []

                for attachment_data in attachments_list:
                    try:
                        attachment_id = attachment_data.get("id", "")
                        if not attachment_id:
                            continue

                        # Get parent issue info
                        issue_data = attachment_data.get("issue") or {}
                        issue_id = issue_data.get("id", "")
                        team_data = issue_data.get("team") or {}
                        team_id = team_data.get("id", "")

                        if not team_id or team_id not in team_map:
                            # Skip attachments from teams not in our sync scope
                            continue

                        # Get parent issue's internal record ID
                        parent_record = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=issue_id
                        )
                        parent_node_id = parent_record.id if parent_record else None

                        if not parent_node_id or getattr(parent_record, "is_placeholder", False) is True:
                            # Parent issue not synced yet, skip this attachment
                            self.logger.debug(f"⚠️ Skipping attachment {attachment_id}: parent issue {issue_id} not synced")
                            continue
                        if not self._in_date_scope(parent_record):
                            continue

                        # Check if attachment already exists
                        existing_attachment = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=attachment_id
                        )

                        # Transform attachment to LinkRecord
                        link_record = self._transform_attachment_to_link_record(
                            attachment_data, issue_id, parent_node_id, team_id, existing_attachment
                        )

                        # Set indexing status based on filters
                        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
                            link_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                        # Look up related record by weburl
                        if link_record.weburl:
                            try:
                                related_record = await self.data_entities_processor.get_record_by_weburl(
                                    link_record.weburl
                                )
                                if related_record:
                                    link_record.linked_record_id = related_record.id
                                    self.logger.info(f"🔗 Found related record {related_record.id} for attachment URL: {link_record.weburl}")
                            except Exception as e:
                                self.logger.debug(f"⚠️ Could not fetch related record for URL {link_record.weburl}: {e}")

                        batch_records.append((link_record, []))
                        total_attachments += 1

                    except Exception as e:
                        if self._stops_the_sync(e):
                            raise
                        attachment_id = attachment_data.get("id", "unknown")
                        self.logger.error(f"❌ Error processing attachment {attachment_id}: {e}", exc_info=True)
                        failures.append(attachment_id)
                        continue

                # Process batch
                if batch_records:
                    await self.data_entities_processor.on_new_records(batch_records)

                await checkpoint.advance(run, end_cursor)

            # An attachment that could not be stored is read again next time
            if failures:
                self._read_everything = False
            if failures and await self._hold_checkpoint(checkpoint, "attachments", failures):
                raise RuntimeError(f"{len(failures)} attachment(s) could not be stored")
            await checkpoint.finish(run)

            if total_attachments > 0:
                self.logger.info(f"✅ Synced {total_attachments} attachments")
            else:
                self.logger.info("ℹ️ No new/updated attachments")

        except Exception as e:
            if self._stops_the_sync(e):
                raise
            self._read_everything = False
            self.logger.error(f"❌ Error syncing attachments: {e}", exc_info=True)
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("couldn't sync attachments"),
                message=(
                    f"Attachment sync failed: {str(e)[:200]}. "
                    "Existing attachments are preserved; they'll retry on the next sync."
                ),
            )

    async def _sync_documents(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync documents separately from issues and projects.

        Linear doesn't update issue.updatedAt (or a project's) when one of its documents
        is added or edited, so documents are listed directly with their own checkpoint.
        A document belongs to the issue or project it is attached to and is filed under
        that parent's team.
        """
        if not team_record_groups:
            return

        team_ids = {g.external_group_id for g, _ in team_record_groups if g.external_group_id}

        try:
            checkpoint = self._checkpoint(self.documents_sync_point, "documents")
            run = await checkpoint.begin()
            total_documents = 0
            failures: list[str] = []

            # Build filter for documents
            document_filter: Dict[str, Any] = {}
            # Only the checkpoint applies here. The connector's date filters choose
            # issues and projects; a document belongs to its parent however old it is.
            self._apply_checkpoint_to_linear_filter(document_filter, run.since)

            async for documents_list, end_cursor, _ in self._api.pages(
                "documents", ["documents"], page_size=50, after=run.cursor,
                fetch=lambda ds, first, after: ds.documents(
                    first=first, after=after, filter=document_filter if document_filter else None),
            ):
                if documents_list:
                    self.logger.info(f"📄 Fetched {len(documents_list)} documents")

                batch_records: List[Tuple[Record, List[Permission]]] = []

                for document_data in documents_list:
                    try:
                        document_id = document_data.get("id", "")
                        if not document_id:
                            continue

                        issue_data = document_data.get("issue") or {}
                        project_data = document_data.get("project") or {}
                        if issue_data.get("id"):
                            parent_id, parent_type = issue_data["id"], RecordType.TICKET
                            filter_key = IndexingFilterKey.DOCUMENTS
                        elif project_data.get("id"):
                            parent_id, parent_type = project_data["id"], RecordType.PROJECT
                            filter_key = IndexingFilterKey.PROJECTS
                        else:
                            self.logger.debug(f"⚠️ Skipping document {document_id}: not attached to an issue or project")
                            continue

                        # The parent decides where the document is filed and who reads it
                        parent_record = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=parent_id
                        )
                        if parent_record is None or getattr(parent_record, "is_placeholder", False):
                            self.logger.debug(f"⚠️ Skipping document {document_id}: parent {parent_id} not synced")
                            continue
                        if not self._in_date_scope(parent_record):
                            continue

                        team_id = parent_record.external_record_group_id or ""
                        if team_id not in team_ids:
                            # Skip documents from teams not in our sync scope
                            continue

                        # Check if document already exists
                        existing_document = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=document_id
                        )

                        # Transform document to WebpageRecord
                        webpage_record = self._transform_document_to_webpage_record(
                            document_data, parent_id, parent_record.id, team_id, existing_document,
                            parent_record_type=parent_type,
                        )

                        # Set indexing status based on filters
                        if self.indexing_filters and not self.indexing_filters.is_enabled(filter_key):
                            webpage_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                        batch_records.append((webpage_record, []))
                        total_documents += 1

                    except Exception as e:
                        if self._stops_the_sync(e):
                            raise
                        document_id = document_data.get("id", "unknown")
                        self.logger.error(f"❌ Error processing document {document_id}: {e}", exc_info=True)
                        failures.append(document_id)
                        continue

                if batch_records:
                    self.logger.info(f"💾 Processing batch of {len(batch_records)} documents")
                    await self.data_entities_processor.on_new_records(batch_records)

                await checkpoint.advance(run, end_cursor)

            # A document that could not be stored is read again next time
            if failures:
                self._read_everything = False
            if failures and await self._hold_checkpoint(checkpoint, "documents", failures):
                raise RuntimeError(f"{len(failures)} document(s) could not be stored")
            await checkpoint.finish(run)

            if total_documents > 0:
                self.logger.info(f"✅ Synced {total_documents} documents")
            else:
                self.logger.info("ℹ️ No new/updated documents")

        except Exception as e:
            if self._stops_the_sync(e):
                raise
            self._read_everything = False
            self.logger.error(f"❌ Error syncing documents: {e}", exc_info=True)
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("couldn't sync documents"),
                message=(
                    f"Document sync failed: {str(e)[:200]}. "
                    "Existing documents are preserved; they'll retry on the next sync."
                ),
            )

    async def _sync_projects_for_teams(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Sync the projects of the synced teams.

        A project can belong to several teams, so projects are listed once for all
        synced teams (not once per team) and each is filed under one home team chosen by
        ``_project_home``. The checkpoint moves only when the whole list was stored.

        Args:
            team_record_groups: List of (RecordGroup, permissions) tuples for teams to sync
        """
        if not team_record_groups:
            self.logger.info("ℹ️ No teams to sync projects for")
            return

        scope = sorted(g.external_group_id for g, _ in team_record_groups if g.external_group_id)
        if not scope:
            return

        try:
            checkpoint = self._checkpoint(self.projects_sync_point, PROJECTS_CHECKPOINT_KEY)
            run = await checkpoint.begin()
            total_projects = 0
            failures: list[str] = []

            project_filter: dict[str, Any] = {"accessibleTeams": {"some": {"id": {"in": scope}}}}
            self._apply_date_filters_to_linear_filter(project_filter, run.since)

            async for projects_list, end_cursor, _ in self._api.pages(
                "projects", ["projects"], page_size=PROJECT_PAGE_SIZE, after=run.cursor,
                fetch=lambda ds, first, after: ds.projects(first=first, after=after, filter=project_filter),
            ):
                for project_data in projects_list:
                    try:
                        records = await self._project_to_records(project_data.get("id", ""), set(scope))
                        if records:
                            await self.data_entities_processor.on_new_records(records)
                            total_projects += 1
                    except Exception as e:
                        if self._stops_the_sync(e):
                            raise
                        project_ref = project_data.get("name") or project_data.get("id", "unknown")
                        self.logger.error(f"❌ Error processing project {project_ref}: {e}", exc_info=True)
                        failures.append(project_ref)
                await checkpoint.advance(run, end_cursor)

            if failures:
                self._read_everything = False
            if failures and await self._hold_checkpoint(checkpoint, "projects", failures):
                raise RuntimeError(f"{len(failures)} project(s) could not be synced: {', '.join(failures[:5])}")
            await checkpoint.finish(run)

            if total_projects > 0:
                self.logger.info(f"✅ Synced {total_projects} projects")
            else:
                self.logger.info("ℹ️ No new/updated projects")

        except Exception as e:
            if self._stops_the_sync(e):
                raise
            self._read_everything = False
            self.logger.error(f"❌ Error syncing projects: {e}", exc_info=True)
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("couldn't sync projects"),
                message=(
                    f"Project sync failed: {str(e)[:200]}. "
                    "Retry sync; check Linear access if it keeps failing."
                ),
            )

    def _project_home(self, project_data: dict[str, Any], scope: set[str]) -> str | None:
        """The one synced team a project is filed under: its lead team, else the first by
        id, so the choice never depends on the order teams are read.

        Only this team's readers see the project and its children. Linear also shows it
        to the readers of its other teams; filing it under those too listed its documents,
        links and files at the top of each of them as well as under the project.
        ``leadTeamId`` is marked internal in Linear's schema, hence the fallback."""
        lead_team_id = project_data.get("leadTeamId")
        if lead_team_id in scope:
            return lead_team_id
        teams = sorted(t["id"] for t in ((project_data.get("teams") or {}).get("nodes") or [])
                       if t and t.get("id") in scope)
        return teams[0] if teams else None

    async def _fetch_full_project(self, project_id: str) -> dict[str, Any] | None:
        """A project with every nested list read to its end. None when Linear no longer
        has it (or no longer shows it to this account)."""
        try:
            data = await self._api.run("project", lambda ds: ds.project(project_id))
        except LinearNotFound:
            return None
        project = data.get("project")
        if not project:
            return None
        for field, operation, size in (
            ("externalLinks", "projectExternalLinks", NESTED_PAGE_SIZE),
            ("documents", "projectDocuments", NESTED_PAGE_SIZE),
            ("projectMilestones", "projectMilestones", NESTED_PAGE_SIZE),
            ("comments", "projectComments", NESTED_PAGE_SIZE),
            ("projectUpdates", "projectUpdates", PROJECT_UPDATE_PAGE_SIZE),
            ("issues", "projectIssues", NESTED_PAGE_SIZE),
        ):
            await self._api.complete(project, field, operation, ["project", field], page_size=size)
        for update in ((project.get("projectUpdates") or {}).get("nodes") or []):
            await self._api.complete(update, "comments", "projectUpdateComments",
                                     ["projectUpdate", "comments"], page_size=NESTED_PAGE_SIZE)
        return project

    async def _project_to_records(
        self,
        project_id: str,
        scope: set[str],
    ) -> list[tuple[Record, list[Permission]]]:
        """Everything one project contributes: its record, external links, documents and
        the files linked from its content. Children that are gone are removed."""
        if not project_id:
            return []
        full_project_data = await self._fetch_full_project(project_id)
        if full_project_data is None:
            return []

        team_id = self._project_home(full_project_data, scope)
        if not team_id:
            return []

        # Look up existing record to handle versioning
        existing_record = await self.data_entities_processor.get_record_by_external_id(
            connector_id=self.connector_id,
            external_record_id=project_id
        )


        # Transform project to ProjectRecord FIRST (without BlocksContainer - created only during streaming)
        project_record = self._transform_to_project_record(
            full_project_data, team_id, existing_record
        )
        # As for issues: a project stored again unchanged is not re-indexed, so the
        # comment and children-stamp passes must still be able to refresh it.
        if reads_current_content(project_record, existing_record):
            self._refreshed_project_ids.add(project_id)

        # Process related records (links, documents) using project_record.id as parent
        batch_records = await self._prepare_project_related_records(
            full_project_data=full_project_data,
            project_id=project_id,
            existing_record=project_record,
            team_id=team_id,
        )

        # Set indexing status based on filters
        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.PROJECTS):
            project_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        # Extract issues data directly from full_project_data and add to related_external_records
        issues_data = ((full_project_data.get("issues") or {}).get("nodes") or [])
        if issues_data:
            project_record.related_external_records = [
                RelatedExternalRecord(
                    external_record_id=issue.get("id"),
                    record_type=RecordType.TICKET,
                    relation_type=RecordRelations.LINKED_TO
                )
                for issue in issues_data
                if issue.get("id")
            ]

        # Extract files (not images) from project description and create FileRecords
        project_content = full_project_data.get("content") or ""
        linked_urls = {f["url"] for f in self._extract_file_urls_from_markdown(project_content, exclude_images=True)}
        if project_content:
            new_file_records, _ = await self._extract_files_from_markdown(
                markdown_text=project_content,
                parent_external_id=project_id,
                parent_node_id=project_record.id,
                parent_record_type=RecordType.PROJECT,
                team_id=team_id,
                parent_created_at=project_record.source_created_at,
                parent_updated_at=project_record.source_updated_at,
                parent_weburl=project_record.weburl,
                exclude_images=True,
                indexing_filter_key=IndexingFilterKey.PROJECTS,
                # Filed again on every read, as its links and documents are, so they follow the
                # project's current teams; a rebuilt file keeps its version, so nothing is re-indexed.
                is_full_sync=True,
            )
            batch_records.extend(new_file_records)

        if existing_record is not None:
            listed = {
                RecordType.LINK: {n.get("id") for n in ((full_project_data.get("externalLinks") or {}).get("nodes") or [])},
                RecordType.WEBPAGE: {n.get("id") for n in ((full_project_data.get("documents") or {}).get("nodes") or [])},
            }
            await self._settle_children(
                parent_external_id=project_id, keep_file_urls=linked_urls, keep_child_ids=listed)

        # Children were once shared into the project's other teams; this drops those groups.
        for child_record, _ in batch_records:
            child_record.shared_groups_complete = project_record.shared_groups_complete

        # The project first: a child written before it finds no parent in its team.
        return [(project_record, []), *batch_records]

    async def _sync_comment_activity(self, synced_team_ids: set[str]) -> None:
        """Queue issues and projects for re-indexing when their discussion changed.

        Comments and project updates are part of what gets indexed for an issue or a
        project, but writing one does not change the issue's or project's ``updatedAt``,
        so the "changed since" listings never return the parent. This lists comment and
        project-update activity since the last run and refreshes the parents.
        """
        checkpoint = self._checkpoint(self.issues_sync_point, "comment_activity")
        run = await checkpoint.begin()
        if run.is_full:
            # Nothing indexed before this sync: everything was just read with its comments.
            await checkpoint.finish(run)
            return

        since = self._linear_datetime_from_timestamp(run.since or 0)
        activity_filter = {"updatedAt": {"gt": since}}
        issue_ids: set[str] = set()
        project_ids: set[str] = set()

        async for comments, _, _ in self._api.pages(
            "commentActivity", ["comments"], {"filter": activity_filter}, page_size=ACTIVITY_PAGE_SIZE,
        ):
            for comment in comments:
                issue = comment.get("issue") or {}
                project = comment.get("project") or ((comment.get("projectUpdate") or {}).get("project") or {})
                if issue.get("id"):
                    issue_ids.add(issue["id"])
                elif project.get("id"):
                    project_ids.add(project["id"])

        async for updates, _, _ in self._api.pages(
            "projectUpdateActivity", ["projectUpdates"], {"filter": activity_filter}, page_size=ACTIVITY_PAGE_SIZE,
        ):
            for update in updates:
                project = update.get("project") or {}
                if project.get("id"):
                    project_ids.add(project["id"])

        await self._refresh_issues(sorted(issue_ids - self._refreshed_issue_ids), synced_team_ids)
        await self._refresh_projects(sorted(project_ids - self._refreshed_project_ids), synced_team_ids)
        await checkpoint.finish(run)

    async def _refresh_issues(self, issue_ids: list[str], scope: set[str]) -> None:
        """Re-read these issues and queue the ones already indexed for re-indexing."""
        for start in range(0, len(issue_ids), ISSUE_REFRESH_BATCH):
            batch = issue_ids[start:start + ISSUE_REFRESH_BATCH]
            data = await self._api.run(
                "issues", lambda ds, ids=batch: ds.issues(first=len(ids), filter={"id": {"in": ids}}))
            for issue_data in ((data.get("issues") or {}).get("nodes") or []):
                team_id = (issue_data.get("team") or {}).get("id")
                if not team_id or team_id not in scope:
                    continue
                known = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id, external_record_id=issue_data.get("id", ""))
                records = await self._issue_to_records(issue_data, team_id)
                if not self._in_date_scope(records[0][0]):
                    continue
                if records[0][0].parent_external_record_id:
                    self._issue_parent_ids.add(records[0][0].parent_external_record_id)
                await self.data_entities_processor.on_new_records(records)
                if known is not None and not getattr(known, "is_placeholder", False):
                    await self.data_entities_processor.on_record_content_update(records[0][0])

    async def _refresh_projects(self, project_ids: list[str], scope: set[str]) -> None:
        for project_id in project_ids:
            known = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=project_id)
            records = await self._project_to_records(project_id, scope)
            # The project comes first; its links, documents and files follow it.
            if not records or not self._in_date_scope(records[0][0]):
                continue
            await self.data_entities_processor.on_new_records(records)
            if known is not None:
                await self.data_entities_processor.on_record_content_update(records[0][0])

    # ==================== HELPER FUNCTIONS ====================

    async def _prepare_project_related_records(
        self,
        full_project_data: Dict[str, Any],
        project_id: str,
        existing_record: Optional[Record],
        team_id: str,
    ) -> List[Tuple[Record, List[Permission]]]:
        """
        Prepare project-related records (links, documents) for sync.

        This method processes external links and documents without creating BlockGroups
        (BlockGroups are only created during streaming, not during sync).

        Args:
            full_project_data: Full project data from Linear API
            project_id: Project external ID
            existing_record: Existing project record (if any)
            team_id: Team ID for external_record_group_id

        Returns:
            List of (Record, permissions) tuples
        """
        project_batch_records: List[Tuple[Record, List[Permission]]] = []

        # Get project node ID (use existing record ID if available, otherwise will be set after record creation)
        project_node_id = existing_record.id if existing_record else ""

        # Extract external links data
        external_links_data = ((full_project_data.get("externalLinks") or {}).get("nodes") or [])
        if external_links_data:
            # Process external links (without creating block groups for sync)
            link_records, _ = await self._process_project_external_links(
                external_links_data=external_links_data,
                project_id=project_id,
                project_node_id=project_node_id,
                team_id=team_id,
                create_block_groups=False
            )
            project_batch_records.extend(link_records)

        # Extract documents data
        documents_data = ((full_project_data.get("documents") or {}).get("nodes") or [])
        if documents_data:
            # Process documents (without creating block groups for sync)
            document_records, _ = await self._process_project_documents(
                documents_data=documents_data,
                project_id=project_id,
                project_node_id=project_node_id,
                team_id=team_id,
                create_block_groups=False  # No block groups during sync
            )
            project_batch_records.extend(document_records)

        return project_batch_records

    async def _process_project_external_links(
        self,
        external_links_data: List[Dict[str, Any]],
        project_id: str,
        project_node_id: str,
        team_id: str,
        create_block_groups: bool = False
    ) -> Tuple[List[Tuple[Record, List[Permission]]], List[BlockGroup]]:
        """
        Process project external links and create LinkRecords. Optionally create BlockGroups.

        Args:
            external_links_data: List of external link data from Linear API
            project_id: Project external ID
            project_node_id: Internal record ID of project
            team_id: Team ID for external_record_group_id
            create_block_groups: If True, also create BlockGroups (for streaming). Default False (for sync).

        Returns:
            Tuple of (List of (LinkRecord, permissions) tuples, List of BlockGroups)
        """
        link_records: List[Tuple[Record, List[Permission]]] = []
        block_groups: List[BlockGroup] = []

        for link_data in external_links_data:
            try:
                link_id = link_data.get("id", "")
                if not link_id:
                    continue

                # Check if link already exists
                existing_link = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=link_id
                )

                # Transform external link to LinkRecord (reuse attachment transform function)
                link_record = self._transform_attachment_to_link_record(
                    attachment_data=link_data,
                    issue_id=project_id,
                    parent_node_id=project_node_id,
                    team_id=team_id,
                    existing_record=existing_link,
                    parent_record_type=RecordType.PROJECT
                )

                # Set indexing status based on FILES filter (external links are treated as files)
                if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.PROJECTS):
                    link_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                # Look up related record by weburl
                if link_record.weburl:
                    try:
                        related_record = await self.data_entities_processor.get_record_by_weburl(
                            link_record.weburl
                        )
                        if related_record:
                            link_record.linked_record_id = related_record.id
                            self.logger.debug(f"🔗 Found related record {related_record.id} for project link URL: {link_record.weburl}")
                    except Exception as e:
                        self.logger.debug(f"⚠️ Could not fetch related record for URL {link_record.weburl}: {e}")

                link_records.append((link_record, []))

                # Create BlockGroup only if requested (for streaming)
                if create_block_groups:
                    child_record = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=link_record.id,
                        child_name=link_record.record_name
                    )

                    block_group = BlockGroup(
                        id=str(uuid4()),
                        index=len(block_groups),
                        name=link_record.record_name,
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.CHILD_RECORD,
                        description=f"External Link: {link_record.record_name}",
                        source_group_id=link_id,
                        weburl=link_record.weburl,
                        requires_processing=False,  # No data to process, just a child record reference
                        children_records=[child_record]
                    )
                    block_groups.append(block_group)

            except Exception as e:
                link_id = link_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing project external link {link_id}: {e}", exc_info=True)
                continue

        return link_records, block_groups

    async def _process_project_documents(
        self,
        documents_data: List[Dict[str, Any]],
        project_id: str,
        project_node_id: str,
        team_id: str,
        create_block_groups: bool = False
    ) -> Tuple[List[Tuple[Record, List[Permission]]], List[BlockGroup]]:
        """
        Process project documents and create WebpageRecords. Optionally create BlockGroups.

        Args:
            documents_data: List of document data from Linear API
            project_id: Project external ID
            project_node_id: Internal record ID of project
            team_id: Team ID for external_record_group_id
            create_block_groups: If True, also create BlockGroups (for streaming). Default False (for sync).

        Returns:
            Tuple of (List of (WebpageRecord, permissions) tuples, List of BlockGroups)
        """
        document_records: List[Tuple[Record, List[Permission]]] = []
        block_groups: List[BlockGroup] = []

        for document_data in documents_data:
            try:
                document_id = document_data.get("id", "")
                if not document_id:
                    continue

                # Check if document already exists
                existing_document = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=document_id
                )

                # Transform document to WebpageRecord (reuse existing transform function)
                webpage_record = self._transform_document_to_webpage_record(
                    document_data=document_data,
                    issue_id=project_id,
                    parent_node_id=project_node_id,
                    team_id=team_id,
                    existing_record=existing_document,
                    parent_record_type=RecordType.PROJECT
                )

                # Set indexing status based on PROJECTS filter (documents are part of project content)
                if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.PROJECTS):
                    webpage_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                document_records.append((webpage_record, []))

                # Create BlockGroup only if requested (for streaming)
                if create_block_groups:
                    child_record = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=webpage_record.id,
                        child_name=webpage_record.record_name
                    )

                    block_group = BlockGroup(
                        id=str(uuid4()),
                        index=len(block_groups),
                        name=webpage_record.record_name,
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.CHILD_RECORD,
                        description=f"Document: {webpage_record.record_name}",
                        source_group_id=document_id,
                        weburl=webpage_record.weburl,
                        requires_processing=False,  # No data to process, just a child record reference
                        children_records=[child_record]
                    )
                    block_groups.append(block_group)

            except Exception as e:
                document_id = document_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing project document {document_id}: {e}", exc_info=True)
                continue

        return document_records, block_groups

    async def _process_issue_attachments(
        self,
        attachments_data: List[Dict[str, Any]],
        issue_id: str,
        issue_node_id: str,
        team_id: str,
    ) -> List[ChildRecord]:
        """
        Process issue attachments and create ChildRecords.
        Creates LinkRecords if they don't exist (like projects do).

        Args:
            attachments_data: List of attachment data from Linear API
            issue_id: Issue external ID
            issue_node_id: Internal record ID of issue
            team_id: Team ID for external_record_group_id

        Returns:
            List of ChildRecord objects for attachments
        """
        child_records: List[ChildRecord] = []

        for attachment_data in attachments_data:
            try:
                attachment_id = attachment_data.get("id", "")
                if not attachment_id:
                    continue

                # Look up existing attachment record from database
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=attachment_id
                )

                # Create record if it doesn't exist (like projects do)
                if not existing_record:
                    link_record = self._transform_attachment_to_link_record(
                        attachment_data=attachment_data,
                        issue_id=issue_id,
                        parent_node_id=issue_node_id,
                        team_id=team_id,
                        existing_record=None,
                        parent_record_type=RecordType.TICKET
                    )

                    # Set indexing status based on filters
                    if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
                        link_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    # Save the record
                    await self.data_entities_processor.on_new_records([(link_record, [])])
                    existing_record = link_record

                if existing_record:
                    child_records.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name
                    ))

            except Exception as e:
                attachment_id = attachment_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing issue attachment {attachment_id} for children_records: {e}", exc_info=True)
                continue

        return child_records

    async def _process_issue_documents(
        self,
        documents_data: List[Dict[str, Any]],
        issue_id: str,
        issue_node_id: str,
        team_id: str,
    ) -> List[ChildRecord]:
        """
        Process issue documents and create ChildRecords.
        Creates WebpageRecords if they don't exist (like projects do).

        Args:
            documents_data: List of document data from Linear API
            issue_id: Issue external ID
            issue_node_id: Internal record ID of issue
            team_id: Team ID for external_record_group_id

        Returns:
            List of ChildRecord objects for documents
        """
        child_records: List[ChildRecord] = []

        for document_data in documents_data:
            try:
                document_id = document_data.get("id", "")
                if not document_id:
                    continue

                # Look up existing document record from database
                existing_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=document_id
                )

                # Create record if it doesn't exist (like projects do)
                if not existing_record:
                    webpage_record = self._transform_document_to_webpage_record(
                        document_data=document_data,
                        issue_id=issue_id,
                        parent_node_id=issue_node_id,
                        team_id=team_id,
                        existing_record=None,
                        parent_record_type=RecordType.TICKET
                    )

                    # Set indexing status based on filters
                    if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.DOCUMENTS):
                        webpage_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                    # Save the record
                    await self.data_entities_processor.on_new_records([(webpage_record, [])])
                    existing_record = webpage_record

                if existing_record:
                    child_records.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name
                    ))

            except Exception as e:
                document_id = document_data.get("id", "unknown")
                self.logger.error(f"❌ Error processing issue document {document_id} for children_records: {e}", exc_info=True)
                continue

        return child_records

    async def _process_content_files_for_children(
        self,
        content: str,
        parent_external_id: str,
        parent_node_id: str,
        parent_record_type: RecordType,
        team_id: str,
        parent_created_at: Optional[int] = None,
        parent_updated_at: Optional[int] = None,
        parent_weburl: Optional[str] = None,
    ) -> List[ChildRecord]:
        """
        Process files extracted from content (description/body) and create ChildRecords.
        Creates FileRecords if they don't exist (excluding images).

        This method is reusable for both issues and projects.

        Args:
            content: Markdown content to extract files from
            parent_external_id: External ID of parent (issue or project)
            parent_node_id: Internal ID of parent record
            parent_record_type: Type of parent record (TICKET or PROJECT)
            team_id: Team ID for external_record_group_id
            parent_created_at: Source created timestamp of parent (in ms)
            parent_updated_at: Source updated timestamp of parent (in ms)
            parent_weburl: Web URL of parent record (used for file weburl)

        Returns:
            List of ChildRecord objects for files (excluding images)
        """
        child_records: List[ChildRecord] = []

        if not content:
            return child_records

        # Extract files from markdown - returns NEW files and EXISTING file children separately
        new_file_records, existing_file_children = await self._extract_files_from_markdown(
            markdown_text=content,
            parent_external_id=parent_external_id,
            parent_node_id=parent_node_id,
            parent_record_type=parent_record_type,
            team_id=team_id,
            parent_created_at=parent_created_at,
            parent_updated_at=parent_updated_at,
            parent_weburl=parent_weburl,
            exclude_images=True,
            indexing_filter_key=IndexingFilterKey.FILES
        )

        # Save NEW FileRecords only (existing files are already in DB, don't need to save)
        if new_file_records:
            await self.data_entities_processor.on_new_records(new_file_records)

        # Create ChildRecords for NEW files
        for file_record, _ in new_file_records:
            child_records.append(ChildRecord(
                child_type=ChildType.RECORD,
                child_id=file_record.id,
                child_name=file_record.record_name
            ))

        # Add EXISTING file children (already created as ChildRecords in _extract_files_from_markdown)
        child_records.extend(existing_file_children)

        return child_records

    async def _process_comment_files_for_children(
        self,
        comments_data: List[Dict[str, Any]],
        issue_id: str,
        issue_node_id: str,
        team_id: str,
        issue_weburl: Optional[str],
    ) -> Dict[str, List[ChildRecord]]:
        """
        Process files from comment bodies and return a map of comment_id -> ChildRecords.
        Fetches already-synced FileRecords from database, or creates them if they don't exist (excluding images).
        Uses the same indexing keys as issue files.

        Args:
            comments_data: List of comment data from Linear API
            issue_id: Issue external ID
            issue_node_id: Internal record ID of issue
            team_id: Team ID for external_record_group_id
            issue_weburl: Issue web URL (for file weburl)

        Returns:
            Dictionary mapping comment_id to List[ChildRecord] for files in that comment
        """
        comment_file_children_map: Dict[str, List[ChildRecord]] = {}

        if not comments_data:
            return comment_file_children_map

        for comment_data in comments_data:
            comment_id = comment_data.get("id", "")
            comment_body = comment_data.get("body") or ""

            if not comment_id or not comment_body:
                continue

            # Get comment timestamps for file records
            comment_created_at = self._parse_linear_datetime(comment_data.get("createdAt", "")) or 0
            comment_updated_at = self._parse_linear_datetime(comment_data.get("updatedAt", "")) or 0
            comment_url = comment_data.get("url", issue_weburl)

            # Extract files from comment body - returns NEW files and EXISTING file children separately
            new_file_records, existing_file_children = await self._extract_files_from_markdown(
                markdown_text=comment_body,
                parent_external_id=issue_id,
                parent_node_id=issue_node_id,
                parent_record_type=RecordType.TICKET,
                team_id=team_id,
                parent_created_at=comment_created_at,
                parent_updated_at=comment_updated_at,
                parent_weburl=comment_url,
                exclude_images=True,
                indexing_filter_key=IndexingFilterKey.FILES
            )

            # Save NEW FileRecords only (existing files are already in DB, don't need to save)
            if new_file_records:
                await self.data_entities_processor.on_new_records(new_file_records)

            # Create ChildRecords for NEW files
            child_records: List[ChildRecord] = []
            for file_record, _ in new_file_records:
                child_records.append(ChildRecord(
                    child_type=ChildType.RECORD,
                    child_id=file_record.id,
                    child_name=file_record.record_name
                ))

            # Add EXISTING file children (already created as ChildRecords in _extract_files_from_markdown)
            child_records.extend(existing_file_children)

            if child_records:
                comment_file_children_map[comment_id] = child_records

        return comment_file_children_map

    async def _extract_files_from_markdown(
        self,
        markdown_text: str,
        parent_external_id: str,
        parent_node_id: str,
        parent_record_type: RecordType,
        team_id: str,
        parent_created_at: Optional[int] = None,
        parent_updated_at: Optional[int] = None,
        parent_weburl: Optional[str] = None,
        exclude_images: bool = False,
        indexing_filter_key: Optional[IndexingFilterKey] = IndexingFilterKey.FILES,
        is_full_sync: bool = False,
    ) -> Tuple[List[Tuple[Record, List[Permission]]], List[ChildRecord]]:
        """
        Extract files from markdown text and create FileRecords.

        Args:
            markdown_text: Markdown content to extract files from
            parent_external_id: External ID of parent (issue or comment)
            parent_node_id: Internal ID of parent record
            parent_record_type: Type of parent record (TICKET or COMMENT)
            team_id: Team ID for external_record_group_id
            parent_created_at: Source created timestamp of parent (in ms)
            parent_updated_at: Source updated timestamp of parent (in ms)
            parent_weburl: Web URL of parent record (used for file weburl)
            exclude_images: If True, exclude image patterns and only extract file links
            indexing_filter_key: Indexing filter key to use (defaults to FILES)
            is_full_sync: If True, existing FileRecords are also rebuilt and returned in
                the first list (so on_new_records recreates their edges after a full-sync
                edge wipe). Default False preserves the incremental contract.

        Returns:
            Tuple of:
            - List of (FileRecord, permissions) tuples for records to emit through
              on_new_records. Contains NEW files, plus EXISTING files when
              is_full_sync=True (rebuilt with preserved id/version).
            - List of ChildRecord objects for EXISTING files (for children_records
              during streaming).
        """
        file_records: List[Tuple[Record, List[Permission]]] = []
        existing_file_children: List[ChildRecord] = []

        if not markdown_text:
            return file_records, existing_file_children

        # Extract file URLs from markdown (excluding images if requested)
        file_urls = self._extract_file_urls_from_markdown(markdown_text, exclude_images=exclude_images)

        if not file_urls:
            return file_records, existing_file_children

        for file_info in file_urls:
            try:
                file_url = file_info["url"]
                filename = file_info["filename"]

                # One record per (file, parent): the same upload linked from two issues
                # is two records, each following its own issue's access.
                existing_file = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=file_record_id(file_url, parent_external_id)
                )

                # If file already exists, add to children_records (for streaming)
                existing_file_record = None
                if existing_file and existing_file.record_type == RecordType.FILE:
                    existing_file_children.append(ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_file.id,
                        child_name=existing_file.record_name or filename
                    ))
                    if not is_full_sync and getattr(existing_file, "is_deleted", False) is not True:
                        # Incremental: edges already present, skip re-emission
                        continue
                    # Full sync: edges were wiped; fall through to rebuild with
                    # existing_record so _link_record_to_group restores them.
                    existing_file_record = existing_file

                file_record = await self._transform_file_url_to_file_record(
                    file_url=file_url,
                    filename=filename,
                    parent_external_id=parent_external_id,
                    parent_node_id=parent_node_id,
                    parent_record_type=parent_record_type,
                    team_id=team_id,
                    existing_record=existing_file_record,
                    parent_created_at=parent_created_at,
                    parent_updated_at=parent_updated_at,
                    parent_weburl=parent_weburl
                )

                # Set indexing status based on filters (use provided filter key or default to FILES)
                filter_key = indexing_filter_key or IndexingFilterKey.FILES
                if self.indexing_filters and not self.indexing_filters.is_enabled(filter_key):
                    file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                # Files inherit permissions from parent, so pass empty list
                file_records.append((file_record, []))

            except Exception as e:
                self.logger.error(f"❌ Error processing file {file_info.get('url', 'unknown')}: {e}", exc_info=True)
                self._read_everything = False
                continue

        return file_records, existing_file_children

    def _extract_file_urls_from_markdown(self, markdown_text: str, exclude_images: bool = False) -> List[Dict[str, str]]:
        """
        Extract file URLs from markdown text (images and file links).
        Only extracts URLs on Linear's upload host (https://uploads.linear.app/...);
        a link to any other host is never fetched.

        Args:
            markdown_text: Markdown content to extract from
            exclude_images: If True, only extract file links (skip URLs that appear in image patterns)

        Returns:
            List of dicts with 'url', 'filename', 'alt_text' keys
        """
        if not markdown_text:
            return []

        file_urls: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()

        # If excluding images, collect image URLs first to skip them in links
        image_urls = set()
        if exclude_images:
            image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'  # ![alt](url)
            for match in re.finditer(image_pattern, markdown_text):
                url = link_destination(match.group(2))
                if is_linear_upload_url(url):
                    image_urls.add(url)

        # Extract images (only if not excluding)
        if not exclude_images:
            image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'  # ![alt](url)
            for match in re.finditer(image_pattern, markdown_text):
                url = link_destination(match.group(2))
                if is_linear_upload_url(url) and url not in seen_urls:
                    seen_urls.add(url)
                    alt_text = match.group(1) or ""
                    filename = alt_text or url.split("/")[-1].split("?")[0]
                    file_urls.append({"url": url, "filename": filename, "alt_text": alt_text})

        # Extract file links (skip image URLs if excluding images)
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'  # [text](url)
        for match in re.finditer(link_pattern, markdown_text):
            url = link_destination(match.group(2))
            if is_linear_upload_url(url) and url not in seen_urls:
                # Skip if excluding images and this URL appears in an image pattern
                if exclude_images and url in image_urls:
                    continue

                seen_urls.add(url)
                link_text = match.group(1) or ""
                filename = link_text or url.split("?")[0].split("/")[-1]
                file_urls.append({"url": url, "filename": filename, "alt_text": link_text})

        return file_urls

    def _get_mime_type_from_url(self, url: str, filename: str = "") -> str:
        """
        Determine mime type from URL or filename extension.

        Args:
            url: File URL
            filename: Optional filename

        Returns:
            Mime type string
        """
        # Extract extension from filename or URL
        ext = ""
        if filename and "." in filename:
            ext = filename.split(".")[-1].lower()
        elif "." in url:
            url_path = url.split("?")[0]  # Remove query params
            ext = url_path.split(".")[-1].lower()

        # Map common extensions to mime types
        extension_to_mime = {
            "pdf": MimeTypes.PDF.value,
            "png": MimeTypes.PNG.value,
            "jpg": MimeTypes.JPEG.value,
            "jpeg": MimeTypes.JPEG.value,
            "gif": MimeTypes.GIF.value,
            "webp": MimeTypes.WEBP.value,
            "svg": MimeTypes.SVG.value,
            "doc": MimeTypes.DOC.value,
            "docx": MimeTypes.DOCX.value,
            "xls": MimeTypes.XLS.value,
            "xlsx": MimeTypes.XLSX.value,
            "ppt": MimeTypes.PPT.value,
            "pptx": MimeTypes.PPTX.value,
            "csv": MimeTypes.CSV.value,
            "zip": MimeTypes.ZIP.value,
            "json": MimeTypes.JSON.value,
            "xml": MimeTypes.XML.value,
            "txt": MimeTypes.PLAIN_TEXT.value,
            "md": MimeTypes.MARKDOWN.value,
            "html": MimeTypes.HTML.value,
        }

        return extension_to_mime.get(ext, MimeTypes.UNKNOWN.value)

    async def _convert_images_to_base64_in_markdown(self, markdown_text: str) -> str:
        """
        Convert images in markdown text to base64 data URIs.

        Finds image patterns like ![alt](url) and replaces them with
        base64-encoded data URIs: ![alt](data:image/png;base64,...)

        Only images on Linear's upload host are fetched. An image larger than
        ``MAX_INLINE_IMAGE_BYTES``, or one that would take the text past
        ``MAX_INLINE_IMAGES_TOTAL_BYTES``, is left as a link.

        Args:
            markdown_text: Markdown content that may contain images

        Returns:
            Markdown text with images converted to base64 data URIs
        """
        if not markdown_text:
            return markdown_text

        # Pattern to match markdown images: ![alt](url)
        image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

        # Find all image matches
        matches = list(re.finditer(image_pattern, markdown_text))

        if not matches:
            return markdown_text  # No images found

        if not any(is_linear_upload_url(link_destination(match.group(2))) for match in matches):
            return markdown_text  # Nothing of ours to fetch

        # Initialize datasource if needed
        if not self.data_source:
            await self.init()

        datasource = await self._get_fresh_datasource()

        # Process all images and build replacements
        replacements = {}
        total_bytes = 0
        for match in matches:
            alt_text = match.group(1) or ""
            image_url = link_destination(match.group(2))

            # Only process Linear upload URLs
            if not is_linear_upload_url(image_url):
                continue  # Skip non-Linear images

            try:
                # Download image content, giving up as soon as it is too large
                image_bytes = b""
                too_large = False
                async for chunk in datasource.download_file(image_url):
                    image_bytes += chunk
                    if (len(image_bytes) > MAX_INLINE_IMAGE_BYTES
                            or total_bytes + len(image_bytes) > MAX_INLINE_IMAGES_TOTAL_BYTES):
                        too_large = True
                        break

                if too_large:
                    self.logger.debug(f"⚠️ Image {image_url} is too large to inline; left as a link")
                    continue

                if not image_bytes:
                    self.logger.debug(f"⚠️ Empty image content from {image_url}")
                    continue  # Skip empty images

                total_bytes += len(image_bytes)

                # Determine image type from URL
                image_type = "png"  # default
                url_lower = image_url.lower()
                if ".jpg" in url_lower or ".jpeg" in url_lower:
                    image_type = "jpeg"
                elif ".gif" in url_lower:
                    image_type = "gif"
                elif ".webp" in url_lower:
                    image_type = "webp"
                elif ".svg" in url_lower:
                    image_type = "svg"
                elif ".png" in url_lower:
                    image_type = "png"

                # Convert to base64
                base64_encoded = base64.b64encode(image_bytes).decode('utf-8')

                # Create data URI
                if image_type == "svg":
                    data_uri = f"data:image/svg+xml;base64,{base64_encoded}"
                else:
                    data_uri = f"data:image/{image_type};base64,{base64_encoded}"

                # Store replacement
                original = match.group(0)
                replacement = f"![{alt_text}]({data_uri})"
                replacements[original] = replacement

            except Exception as e:
                self.logger.debug(f"⚠️ Failed to convert image {image_url} to base64: {e}")
                continue  # Skip on error

        # Apply replacements (process in reverse order to preserve string indices)
        result = markdown_text
        for match in reversed(matches):
            original = match.group(0)
            if original in replacements:
                start, end = match.span()
                result = result[:start] + replacements[original] + result[end:]

        return result

    # ==================== TRANSFORMATIONS ====================

    def _transform_issue_to_ticket_record(
        self,
        issue_data: Dict[str, Any],
        team_id: str,
        existing_record: Optional[Record] = None
    ) -> TicketRecord:
        """
        Transform Linear issue data to TicketRecord.
        This method is reusable for both initial sync and reindex operations.

        Args:
            issue_data: Raw issue data from Linear API (from GraphQL query)
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling

        Returns:
            TicketRecord: Transformed ticket record
        """
        issue_id = issue_data.get("id", "")
        if not issue_id:
            raise ValueError("Issue data missing required 'id' field")

        identifier = issue_data.get("identifier", "")
        title = issue_data.get("title", "")

        # Build record name with issue identifier in square brackets at start for better searchability
        # Matches Jira pattern: "[ENG-30] Issue Title"
        if identifier and title:
            record_name = f"[{identifier}] {title}"
        elif identifier:
            record_name = identifier
        else:
            self.logger.warning(f"Issue {issue_id} missing both identifier and title, using ID as record name")
            record_name = issue_id

        # Ensure team_id is not None or empty
        if not team_id:
            self.logger.error(f"Issue {issue_id} has no team_id, cannot create record without team association")
            raise ValueError(f"team_id is required but was {team_id}")

        # Priority mapping
        priority_num = issue_data.get("priority")
        if priority_num is None:
            priority_str = None
        elif priority_num == 0:
            priority_str = "none"  # Linear priority 0 = no priority
        else:
            priority_map = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
            priority_str = priority_map.get(priority_num)

        # Map to standard Priority enum
        priority = self.value_mapper.map_priority(priority_str)

        # State information - map to standard Status enum
        state = issue_data.get("state", {})
        state_name = state.get("name") if state else None
        state_type = state.get("type") if state else None

        # Try custom state name first (e.g., "new", "In Review")
        status = self.value_mapper.map_status(state_name)

        # If state_name didn't map to enum, try state_type (backlog, started, etc.)
        if status and not isinstance(status, Status):
            status = self.value_mapper.map_status(state_type)

        # Type extraction from labels with fallback logic
        labels = issue_data.get("labels", {})
        label_nodes = labels.get("nodes", []) if labels else []
        type_value = None

        # Check all labels for type match
        for label in label_nodes:
            label_name = label.get("name", "") if label else ""
            if label_name:
                mapped_type = self.value_mapper.map_type(label_name)
                if mapped_type and isinstance(mapped_type, ItemType):
                    type_value = mapped_type
                    break  # Use first matching type

        # If no label matched, check if it's a sub-issue or default to TASK
        if not type_value:
            parent = issue_data.get("parent")
            if parent and parent.get("id"):
                type_value = ItemType.SUB_ISSUE
            else:
                type_value = ItemType.ISSUE

        # Assignee information
        assignee = issue_data.get("assignee", {})
        assignee_email = assignee.get("email") if assignee else None
        assignee_name = assignee.get("displayName") or assignee.get("name") if assignee else None

        # Creator information
        creator = issue_data.get("creator", {})
        creator_email = creator.get("email") if creator else None
        creator_name = creator.get("displayName") or creator.get("name") if creator else None

        # Parent issue (for sub-issues)
        parent = issue_data.get("parent")
        parent_external_record_id = parent.get("id") if parent else None

        # Timestamps
        created_at = self._parse_linear_datetime(issue_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(issue_data.get("updatedAt", "")) or 0

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Issue {identifier} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        # Get web URL directly from Linear API response
        weburl = issue_data.get("url")

        # Use updatedAt as external_revision_id so placeholders (None) will trigger update
        external_revision_id = str(updated_at) if updated_at else None

        ticket = TicketRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.TICKET,
            external_record_id=issue_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=parent_external_record_id,
            parent_record_type=RecordType.TICKET if parent_external_record_id else None,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=weburl,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            status=status,
            priority=priority,
            type=type_value,
            delivery_status=None,
            assignee=assignee_name,
            preview_renderable=False,
            assignee_email=assignee_email,
            creator_email=creator_email,
            creator_name=creator_name,
            inherit_permissions=True,
        )

        # Extract and map issue relationships
        relations = issue_data.get("relations", {})
        relation_nodes = relations.get("nodes", []) if relations else []

        # Process each relation
        related_external_records = []
        for relation in relation_nodes:
            relation_type = relation.get("type")
            related_issue = relation.get("relatedIssue", {})
            related_issue_id = related_issue.get("id") if related_issue else None

            # Skip if missing required data
            if not related_issue_id or not relation_type:
                continue

            # Map Linear type to RecordRelations enum using standard utility
            mapped_relation_type = map_relationship_type(relation_type)

            # Only create record if we got a valid RecordRelations enum (not a string)
            if isinstance(mapped_relation_type, RecordRelations):
                related_external_records.append(
                    RelatedExternalRecord(
                        external_record_id=related_issue_id,
                        record_type=RecordType.TICKET,
                        relation_type=mapped_relation_type,
                    )
                )

        # Add all related external records to ticket
        ticket.related_external_records.extend(related_external_records)

        return ticket

    @staticmethod
    def _project_children_stamp(project_data: dict[str, Any]) -> str | None:
        """A fingerprint of a project's external links and milestones.

        Adding, editing or removing one does not change the project's ``updatedAt``, so
        the fingerprint is kept in the record's revision and compared on every sync with
        what the light project listing reports. None when a list is not fully known.
        """
        parts: list[str] = []
        for field in ("externalLinks", "projectMilestones"):
            connection = project_data.get(field) or {}
            if (connection.get("pageInfo") or {}).get("hasNextPage"):
                return None
            parts.append(",".join(sorted(
                f"{node.get('id')}@{node.get('updatedAt')}" for node in connection.get("nodes") or [] if node)))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def _transform_to_project_record(
        self,
        project_data: Dict[str, Any],
        team_id: str,
        existing_record: Optional[Record] = None
    ) -> ProjectRecord:
        """
        Transform Linear project to ProjectRecord.

        Args:
            project_data: Raw project data from Linear API
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling

        Returns:
            ProjectRecord: Transformed project record
        """
        project_id = project_data.get("id", "")
        if not project_id:
            raise ValueError("Project data missing required 'id' field")

        # Build record_name: Use name directly (projects don't have meaningful identifiers like issues)
        name = project_data.get("name", "")
        slug_id = project_data.get("slugId", "")
        if name:
            record_name = name
        elif slug_id:
            record_name = slug_id
        else:
            self.logger.warning(f"Project {project_id} missing both slugId and name, using ID as record name")
            record_name = project_id

        # Status information
        status = project_data.get("status", {})
        status_name = status.get("name") if status else None

        # Priority information
        priority_label = project_data.get("priorityLabel", "")

        # Lead information
        lead = project_data.get("lead", {})
        lead_id = lead.get("id") if lead else None
        lead_name = lead.get("displayName") or lead.get("name") if lead else None
        lead_email = lead.get("email") if lead else None

        # Timestamps
        created_at = self._parse_linear_datetime(project_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(project_data.get("updatedAt", "")) or 0

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Project {record_name} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        # Get web URL directly from Linear API response
        weburl = project_data.get("url")

        # The revision is updatedAt plus a fingerprint of the links and milestones,
        # which change without touching updatedAt.
        external_revision_id = str(updated_at) if updated_at else None
        children_stamp = self._project_children_stamp(project_data)
        if external_revision_id and children_stamp:
            external_revision_id = f"{external_revision_id}:{children_stamp}"

        # Create ProjectRecord
        project = ProjectRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.PROJECT,
            external_record_id=project_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=None,  # Projects don't have parents
            parent_record_type=None,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=Connectors.LINEAR,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=weburl,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            status=status_name,
            priority=priority_label,
            lead_id=lead_id,
            lead_name=lead_name,
            lead_email=lead_email,
            preview_renderable=False,
            inherit_permissions=True,
            is_dependent_node=False,
            parent_node_id=None,
        )
        # A project is filed under its home team only (``_project_home``). Projects synced
        # before that were shared into their other teams; a complete list of none drops them.
        project.shared_groups_complete = "teams" in project_data


        return project

    def _transform_attachment_to_link_record(
        self,
        attachment_data: Dict[str, Any],
        issue_id: str,
        parent_node_id: str,
        team_id: str,
        existing_record: Optional[Record] = None,
        parent_record_type: RecordType = RecordType.TICKET
    ) -> LinkRecord:
        """
        Transform Linear attachment or external link data to LinkRecord.
        Can be used for both issue attachments and project external links.

        Args:
            attachment_data: Raw attachment/external link data from Linear API (supports title/subtitle/label)
            issue_id: Parent record external ID (issue or project)
            parent_node_id: Internal record ID of parent (ticket or project)
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling
            parent_record_type: Type of parent record (TICKET or PROJECT)

        Returns:
            LinkRecord: Transformed link record
        """
        attachment_id = attachment_data.get("id", "")
        if not attachment_id:
            raise ValueError("Attachment data missing required 'id' field")

        url = attachment_data.get("url", "")
        if not url:
            raise ValueError(f"Attachment {attachment_id} missing required 'url' field")

        # Handle both attachments (title/subtitle) and external links (label)
        title = attachment_data.get("title") or attachment_data.get("subtitle") or attachment_data.get("label")

        # Timestamps
        created_at = self._parse_linear_datetime(attachment_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(attachment_data.get("updatedAt", "")) or 0

        # Use updatedAt as external_revision_id
        external_revision_id = str(updated_at) if updated_at else None

        # Set is_public to UNKNOWN for Linear attachments (we don't know if they're public or private)
        is_public = LinkPublicStatus.UNKNOWN

        # Build record name
        record_name = title if title else url.split("/")[-1] or f"Attachment {attachment_id[:8]}"

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Attachment {attachment_id[:8]} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        link = LinkRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.LINK,
            external_record_id=attachment_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=issue_id,
            parent_record_type=parent_record_type,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=url,
            url=url,
            title=title,
            is_public=is_public,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            preview_renderable=False,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
        )

        return link

    def _transform_document_to_webpage_record(
        self,
        document_data: Dict[str, Any],
        issue_id: str,
        parent_node_id: str,
        team_id: str,
        existing_record: Optional[Record] = None,
        parent_record_type: RecordType = RecordType.TICKET
    ) -> WebpageRecord:
        """
        Transform Linear document data to WebpageRecord.
        This method handles versioning similar to TicketRecord.

        Args:
            document_data: Raw document data from Linear API
            issue_id: Parent issue/project external ID
            parent_node_id: Internal record ID of parent ticket/project
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling
            parent_record_type: Type of parent record (TICKET or PROJECT)

        Returns:
            WebpageRecord: Transformed webpage record
        """
        document_id = document_data.get("id", "")
        if not document_id:
            raise ValueError("Document data missing required 'id' field")

        url = document_data.get("url", "")
        if not url:
            raise ValueError(f"Document {document_id} missing required 'url' field")

        title = document_data.get("title", "")

        # Timestamps
        created_at = self._parse_linear_datetime(document_data.get("createdAt", "")) or 0
        updated_at = self._parse_linear_datetime(document_data.get("updatedAt", "")) or 0

        # Use updatedAt as external_revision_id
        external_revision_id = str(updated_at) if updated_at else None

        # Build record name
        record_name = title if title else f"Document {document_id[:8]}"

        # Handle versioning: use existing record's id and increment version if changed
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            self.logger.debug(f"📝 Document {document_id[:8]} changed, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        webpage = WebpageRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=record_name,
            record_type=RecordType.WEBPAGE,
            external_record_id=document_id,
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=issue_id,
            parent_record_type=parent_record_type,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=url,
            created_at=get_epoch_timestamp_in_ms(),
            updated_at=get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=updated_at,
            preview_renderable=False,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
        )

        return webpage

    async def _transform_file_url_to_file_record(
        self,
        file_url: str,
        filename: str,
        parent_external_id: str,
        parent_node_id: str,
        parent_record_type: RecordType,
        team_id: str,
        existing_record: Optional[Record] = None,
        parent_created_at: Optional[int] = None,
        parent_updated_at: Optional[int] = None,
        parent_weburl: Optional[str] = None
    ) -> FileRecord:
        """
        Transform a file URL to FileRecord.

        Args:
            file_url: URL of the file
            filename: Name of the file
            parent_external_id: External ID of parent (issue or comment)
            parent_node_id: Internal ID of parent record
            parent_record_type: Type of parent record (TICKET or COMMENT)
            team_id: Team ID for external_record_group_id
            existing_record: Existing record from DB (if any) for version handling
            parent_created_at: Source created timestamp of parent (in ms)
            parent_updated_at: Source updated timestamp of parent (in ms)
            parent_weburl: Web URL of parent record (used for file weburl)

        Returns:
            FileRecord: Transformed file record
        """
        # Use full file URL as external_record_id (for streaming)
        # Extract extension and determine mime type
        extension = None
        if "." in filename:
            extension = filename.split(".")[-1].lower()

        mime_type = self._get_mime_type_from_url(file_url, filename)

        # Handle versioning
        is_new = existing_record is None
        record_id = existing_record.id if existing_record else str(uuid4())

        if is_new:
            version = 0
        else:
            # For files, we don't track updatedAt, so keep same version unless URL changed
            version = existing_record.version if existing_record else 0

        # Get file size from URL using HEAD request — skip for existing records to
        # avoid an N-HEAD-request fan-out on full resync; reuse the cached value.
        size_in_bytes = 0
        if existing_record is not None:
            size_in_bytes = getattr(existing_record, "size_in_bytes", 0) or 0
        else:
            try:
                if self.data_source:
                    datasource = await self._get_fresh_datasource()
                    file_size = await datasource.get_file_size(file_url)
                    if file_size is not None:
                        size_in_bytes = file_size
            except Exception as e:
                # Log error but don't fail - use 0 as fallback
                self.logger.debug(f"⚠️ Could not fetch file size for {file_url}: {e}")

        now_ms = get_epoch_timestamp_in_ms()
        created_at = existing_record.created_at if existing_record and existing_record.created_at else now_ms

        file_record = FileRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            record_name=filename,
            record_type=RecordType.FILE,
            external_record_id=file_record_id(file_url, parent_external_id),
            external_revision_id=None,
            external_record_group_id=team_id,
            parent_external_record_id=parent_external_id,
            parent_record_type=parent_record_type,
            version=version,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=mime_type,
            weburl=parent_weburl or file_url,  # Use parent's weburl if available, otherwise fallback to file_url
            created_at=created_at,
            updated_at=now_ms,
            source_created_at=parent_created_at,
            source_updated_at=parent_updated_at,
            preview_renderable=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
            is_file=True,
            extension=extension,
            size_in_bytes=size_in_bytes,
        )

        return file_record

    # ==================== DATE FILTERS ====================

    def _apply_checkpoint_to_linear_filter(
        self,
        linear_filter: dict[str, Any],
        last_sync_time: int | None = None
    ) -> None:
        """Restrict a listing to what changed since the last completed run."""
        if last_sync_time:
            linear_datetime = self._linear_datetime_from_timestamp(last_sync_time)
            if linear_datetime:
                linear_filter["updatedAt"] = {"gt": linear_datetime}

    def _in_date_scope(self, record: Record) -> bool:
        """Whether the date filters cover an issue or project. One they leave out may still
        be stored (a full sync left it unlinked, readable by nobody); nothing under it is synced."""
        if not self.sync_filters:
            return True
        for key, stamp in ((SyncFilterKey.CREATED, record.source_created_at),
                           (SyncFilterKey.MODIFIED, record.source_updated_at)):
            date_filter = self.sync_filters.get(key)
            if not date_filter or stamp is None:
                continue
            after, before = date_filter.get_value(default=(None, None))
            if (after and stamp < after) or (before and stamp > before):
                return False
        return True

    def _team_in_filter(self, team_id: str) -> bool:
        """Whether the team filter covers a team (every team when it is unset or empty)."""
        team_filter = self.sync_filters.get("team_ids") if self.sync_filters else None
        selected = set(team_filter.get_value(default=[]) or []) if team_filter else set()
        if not selected:
            return True
        operator = team_filter.get_operator()
        excluding = getattr(operator, "value", operator) == "not_in"
        return (team_id not in selected) if excluding else (team_id in selected)

    def _dated_out(self, record: Record, synced_team_ids: set[str]) -> bool:
        """A full issue of a synced team that the date filters leave out."""
        return (
            record.record_type == RecordType.TICKET
            and getattr(record, "is_placeholder", False) is not True
            and record.external_record_group_id in synced_team_ids
            and not self._in_date_scope(record)
        )

    def _apply_date_filters_to_linear_filter(
        self,
        linear_filter: Dict[str, Any],
        last_sync_time: Optional[int] = None
    ) -> None:
        """
        Apply date filters (modified/created) to Linear filter dict.
        Merges filter values with checkpoint timestamp for incremental sync.

        Args:
            linear_filter: Linear filter dict to modify (in-place)
            last_sync_time: Last sync timestamp in ms (for incremental sync)
        """
        # Get modified filter from sync_filters
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        modified_after_ts = None
        modified_before_ts = None

        if modified_filter:
            modified_after_ts, modified_before_ts = modified_filter.get_value(default=(None, None))

        # Get created filter from sync_filters
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED) if self.sync_filters else None
        created_after_ts = None
        created_before_ts = None

        if created_filter:
            created_after_ts, created_before_ts = created_filter.get_value(default=(None, None))

        # Merge modified_after with checkpoint (use the latest)
        if modified_after_ts and last_sync_time:
            original_filter_ts = modified_after_ts
            modified_after_ts = max(modified_after_ts, last_sync_time)
            self.logger.info(f"🔄 Using latest modified_after: {modified_after_ts} (filter: {original_filter_ts}, checkpoint: {last_sync_time})")
        elif modified_after_ts:
            self.logger.info(f"🔍 Using filter: Fetching issues modified after {modified_after_ts}")
        elif last_sync_time:
            modified_after_ts = last_sync_time
            self.logger.info(f"🔄 Incremental sync: Fetching issues updated after {modified_after_ts}")

        # Apply modified date filters
        if modified_after_ts:
            linear_datetime = self._linear_datetime_from_timestamp(modified_after_ts)
            if linear_datetime:
                linear_filter["updatedAt"] = {"gt": linear_datetime}

        if modified_before_ts:
            linear_datetime = self._linear_datetime_from_timestamp(modified_before_ts)
            if linear_datetime:
                if "updatedAt" in linear_filter:
                    linear_filter["updatedAt"]["lte"] = linear_datetime
                else:
                    linear_filter["updatedAt"] = {"lte": linear_datetime}

        # Apply created date filters
        if created_after_ts:
            linear_datetime = self._linear_datetime_from_timestamp(created_after_ts)
            if linear_datetime:
                linear_filter["createdAt"] = {"gte": linear_datetime}

        if created_before_ts:
            linear_datetime = self._linear_datetime_from_timestamp(created_before_ts)
            if linear_datetime:
                if "createdAt" in linear_filter:
                    linear_filter["createdAt"]["lte"] = linear_datetime
                else:
                    linear_filter["createdAt"] = {"lte": linear_datetime}

    # ==================== DELETION SYNC ====================

    async def _sync_deleted_issues(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Remove issues that were moved to Linear's trash, with their attachments,
        documents and files. Sub-issues of a trashed issue are kept.

        The first run lists everything currently in the trash, so an issue trashed at
        any point before or during the first sync is still caught. Issues that left the
        trash for good are handled by the reconciler.
        """
        if not team_record_groups:
            return

        # Build team IDs list
        team_ids = [
            team_rg.external_group_id
            for team_rg, _ in team_record_groups
            if team_rg.external_group_id
        ]

        if not team_ids:
            return

        try:
            checkpoint = self._checkpoint(self.deletion_sync_point, "trashed_issues")
            run = await checkpoint.begin()
            self.logger.info("🗑️ Starting deleted issues sync")

            # Build filter for issues (can't filter by trashed in GraphQL, so we filter in code)
            issue_filter: dict[str, Any] = {"team": {"id": {"in": team_ids}}}
            if run.since:
                issue_filter["archivedAt"] = {"gte": self._linear_datetime_from_timestamp(run.since)}
            else:
                issue_filter["archivedAt"] = {"null": False}

            total_deleted = 0
            async for issues_list, end_cursor, _ in self._api.pages(
                "trashedIssues", ["issues"], page_size=100, after=run.cursor,
                fetch=lambda ds, first, after: ds.trashed_issues(first=first, after=after, filter=issue_filter),
            ):
                for issue in issues_list:
                    if not issue.get("trashed"):
                        continue
                    if await self._remover.remove(issue.get("id", "")):
                        self.logger.info(f"🗑️ Removed trashed issue {issue.get('identifier', '')}")
                        total_deleted += 1
                await checkpoint.advance(run, end_cursor)

            await checkpoint.finish(run)

            if total_deleted > 0:
                self.logger.info(f"✅ Marked {total_deleted} issues as deleted")
            else:
                self.logger.info("ℹ️ No deleted issues found")

        except Exception as e:
            if self._stops_the_sync(e):
                raise
            self.logger.error(f"❌ Error syncing deleted issues: {e}", exc_info=True)
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("couldn't check for deleted issues"),
                message=(
                    f"Reading Linear's trash failed: {str(e)[:200]}. "
                    "Deleted issues may stay searchable until the next sync."
                ),
            )

    async def _sync_deleted_projects(
        self,
        team_record_groups: List[Tuple[RecordGroup, List[Permission]]]
    ) -> None:
        """
        Remove projects that were trashed, deleted for good, or that no longer belong to
        a synced team, and refresh projects whose links or milestones changed. Linear
        cannot filter projects by deletion time, and a link or milestone does not change
        its project's ``updatedAt``, so a light list of every project is compared with
        what is stored.
        """
        if not team_record_groups:
            return
        team_ids = {g.external_group_id for g, _ in team_record_groups if g.external_group_id}
        try:
            removed, listed, stored = await self._reconciler.run_projects(team_ids)
            if removed:
                self.logger.info(f"✅ Removed {removed} projects that are gone from Linear")
            changed = []
            for project_id, record in stored.items():
                stamp = self._project_children_stamp(listed.get(project_id) or {})
                if (stamp and project_id not in self._refreshed_project_ids
                        and not (record.external_revision_id or "").endswith(f":{stamp}")):
                    changed.append(project_id)
            await self._refresh_projects(sorted(changed), team_ids)
        except Exception as e:
            if self._stops_the_sync(e):
                raise
            self.logger.error(f"❌ Error syncing deleted projects: {e}", exc_info=True)

    async def _mark_record_and_children_deleted(
        self,
        external_record_id: str,
        record_type: str
    ) -> None:
        """
        Remove a record and the records that only exist because of it.

        For issues: attachments (LinkRecord), documents (WebpageRecord) and files
        (FileRecord). Sub-issues are kept. For projects: documents, external links and
        files. Comments, milestones and updates are part of the parent's own content.
        """
        try:
            await self._remover.remove(external_record_id)
        except Exception as e:
            self.logger.error(f"❌ Error marking record {external_record_id} as deleted: {e}")

    # ==================== SYNC CHECKPOINTS ====================

    def _linear_datetime_from_timestamp(self, timestamp_ms: int) -> str:
        """
        Convert epoch timestamp (milliseconds) to Linear datetime format.

        Args:
            timestamp_ms: Epoch timestamp in milliseconds

        Returns:
            Linear datetime string in ISO 8601 format (e.g., "2024-01-15T10:30:00.000Z")
        """
        try:
            # Convert using UTC to avoid local timezone skew in incremental sync filters
            dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
            return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except (ValueError, OSError) as e:
            self.logger.warning(f"Failed to convert timestamp {timestamp_ms} to Linear datetime: {e}")
            return ""

    def _parse_linear_datetime(self, datetime_str: str) -> Optional[int]:
        """
        Parse Linear datetime string to epoch timestamp in milliseconds.

        Linear format: "2025-01-01T12:00:00.000Z" (ISO 8601 with Z suffix)

        Args:
            datetime_str: Linear datetime string

        Returns:
            int: Epoch timestamp in milliseconds or None if parsing fails
        """
        try:
            # Parse ISO 8601 format: '2025-01-01T12:00:00.000Z'
            # Replace 'Z' with '+00:00' for proper ISO format parsing
            dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.debug(f"Failed to parse Linear datetime '{datetime_str}': {e}")
            return None

    def _parse_linear_datetime_to_datetime(self, datetime_str: str) -> Optional[datetime]:
        """
        Parse Linear datetime string to datetime object.

        Linear format: "2025-01-01T12:00:00.000Z" (ISO 8601 with Z suffix)

        Args:
            datetime_str: Linear datetime string

        Returns:
            datetime: Datetime object or None if parsing fails
        """
        if not datetime_str:
            return None
        try:
            # Parse ISO 8601 format: '2025-01-01T12:00:00.000Z'
            # Replace 'Z' with '+00:00' for proper ISO format parsing
            return datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
        except Exception as e:
            self.logger.debug(f"Failed to parse Linear datetime '{datetime_str}': {e}")
            return None

    # ==================== PROJECT BLOCKS PARSER ====================

    def _organize_comments_by_thread(
        self,
        comments: List[BlockComment]
    ) -> List[List[BlockComment]]:
        """
        Organize comments into a 2D list grouped by thread_id, with each thread sorted by created_at.

        Args:
            comments: List of BlockComment objects

        Returns:
            List of lists, where each inner list contains comments for one thread,
            sorted by created_at (oldest first)
        """
        if not comments:
            return []

        # Group comments by thread_id
        threads_dict: Dict[str, List[BlockComment]] = {}
        for comment in comments:
            thread_id = comment.thread_id or "no_thread"
            if thread_id not in threads_dict:
                threads_dict[thread_id] = []
            threads_dict[thread_id].append(comment)

        # Sort each thread's comments by created_at (oldest first)
        organized_comments: List[List[BlockComment]] = []
        for thread_id, thread_comments in threads_dict.items():
            sorted_thread = sorted(
                thread_comments,
                key=lambda c: c.created_at if c.created_at else datetime.max.replace(tzinfo=timezone.utc)
            )
            organized_comments.append(sorted_thread)

        # Sort threads by the first comment's created_at (oldest thread first)
        organized_comments.sort(
            key=lambda thread: thread[0].created_at if thread[0].created_at else datetime.max.replace(tzinfo=timezone.utc)
        )

        return organized_comments

    def _create_blockgroup(
        self,
        name: str,
        weburl: str,
        data: str,
        group_type: GroupType = GroupType.TEXT_SECTION,
        group_subtype: Optional[GroupSubType] = None,
        description: Optional[str] = None,
        source_group_id: Optional[str] = None,
        format: DataFormat = DataFormat.MARKDOWN,
        index: Optional[int] = None,
        requires_processing: bool = True,
        comments: list[list[BlockComment]] | None = None,
    ) -> BlockGroup:
        """
        Create a BlockGroup object directly without using BlockGroupBuilder.

        Args:
            name: Name of the block group
            weburl: Web URL for the original source context (e.g., Linear project page)
            data: Content data (markdown/HTML) to be processed
            group_type: Type of the block group (defaults to TEXT_SECTION)
            group_subtype: Optional subtype of the block group (e.g., MILESTONE, UPDATE, CONTENT)
            description: Optional description of the block group
            source_group_id: Optional source group identifier
            format: Data format (defaults to MARKDOWN)
            index: Optional index for the block group
            requires_processing: Whether this block group requires further processing (defaults to True)
            comments: Optional 2D list of BlockComment objects (grouped by thread_id, sorted by created_at)

        Returns:
            BlockGroup instance

        Raises:
            ValueError: If weburl or data is not provided
        """
        if not weburl:
            raise ValueError("weburl is required when creating BlockGroup")
        if not data:
            raise ValueError("data is required when creating BlockGroup")

        return BlockGroup(
            name=name,
            type=group_type,
            sub_type=group_subtype,
            description=description,
            source_group_id=source_group_id,
            data=data,
            format=format,
            weburl=weburl,
            index=index,
            requires_processing=requires_processing,
            comments=comments or [],
        )

    async def _parse_project_to_blocks(
        self,
        project_data: Dict[str, Any],
        weburl: Optional[str] = None,
        related_external_record_ids: Optional[List[str]] = None,
    ) -> BlocksContainer:
        """
        Parse project data into multiple BlockGroups that will be processed by Docling.

        This creates separate BlockGroups with process_docling=True for:
        - Project Description/Content (if exists) - One BlockGroup
        - Project Comments (if exists) - One BlockGroup with comments as BlockComments (not embedded in markdown)
        - Each Project Milestone (if exists) - One BlockGroup per milestone
        - Each Project Update (if exists) - One BlockGroup per update with update comments as BlockComments

        Args:
            project_data: Full project data from Linear API (with nested data)
            weburl: Project web URL (required for docling BlockGroups)
            related_external_record_ids: List of external IDs of related records (issues, documents, external links)

        Returns:
            BlocksContainer with multiple BlockGroups (process_docling=True), one per section
        """
        project_id = project_data.get("id", "")
        project_name = project_data.get("name", "")
        project_description = project_data.get("description", "")

        if not weburl:
            raise ValueError("weburl is required when creating docling BlockGroup for projects")

        block_groups: List[BlockGroup] = []
        block_group_index = 0

        # 1. Project Description/Content BlockGroup
        # Include both description and content: description first, then content below
        content = project_data.get("content", "")
        description_sections = []

        # Add description first (if exists)
        if project_description:
            description_sections.append(f"# Project Description\n\n{project_description}")

        # Add content below description (if exists)
        if content:
            if description_sections:
                # If we have description, add content as a new section
                description_sections.append(f"\n# Project Content\n\n{content}")
            else:
                # If no description, content becomes the main section
                description_sections.append(f"# Project Content\n\n{content}")

        # A project with comments but no description or content still needs the block
        # the comments hang from.
        if not description_sections and ((project_data.get("comments") or {}).get("nodes") or []):
            description_sections.append(f"# {project_name or 'Project'}")

        # Create BlockGroup if we have either description or content
        if description_sections:
            combined_content = "\n".join(description_sections)

            # Convert images in project description/content to base64
            combined_content = await self._convert_images_to_base64_in_markdown(combined_content)

            # Build project comments as BlockComments
            block_comments: List[BlockComment] = []
            comments = ((project_data.get("comments") or {}).get("nodes") or [])
            if comments:
                for comment_data in comments:
                    comment_body = comment_data.get("body") or ""
                    if comment_body:
                        author = comment_data.get("user") or {}
                        author_id = author.get("id")
                        author_name = author.get("displayName") or author.get("name") or "Unknown"
                        comment_id = comment_data.get("id", "")
                        comment_url = comment_data.get("url", "")
                        comment_created_at_str = comment_data.get("createdAt", "")
                        comment_updated_at_str = comment_data.get("updatedAt", "")

                        # Parse datetime strings to datetime objects
                        comment_created_at = self._parse_linear_datetime_to_datetime(comment_created_at_str)
                        comment_updated_at = self._parse_linear_datetime_to_datetime(comment_updated_at_str)

                        # Convert images in comment body to base64
                        comment_body = await self._convert_images_to_base64_in_markdown(comment_body)

                        # Extract attachments from comment body (excluding images)
                        attachments = None
                        if comment_body:
                            file_urls = self._extract_file_urls_from_markdown(comment_body, exclude_images=True)
                            if file_urls:
                                attachments = [
                                    CommentAttachment(
                                        name=file_info.get("filename", ""),
                                        id=file_info.get("url", "")  # Use file URL as ID (same as external_record_id)
                                    )
                                    for file_info in file_urls
                                    if file_info.get("filename") and file_info.get("url")
                                ]

                        # Handle quoted_text if present
                        quoted_text = comment_data.get("quotedText") or comment_data.get("quoted_text")

                        # Get thread_id from parent comment id, or use comment's own id if top-level
                        parent_comment = comment_data.get("parent") or {}
                        thread_id = parent_comment.get("id") if parent_comment else comment_id

                        block_comments.append(
                            BlockComment(
                                text=comment_body,
                                format=DataFormat.MARKDOWN,
                                author_id=author_id,
                                author_name=author_name,
                                thread_id=thread_id,
                                resolution_status=None,
                                weburl=comment_url if comment_url else None,
                                created_at=comment_created_at,
                                updated_at=comment_updated_at,
                                attachments=attachments if attachments else None,
                                quoted_text=quoted_text,
                            )
                        )

            # Organize comments by thread_id and sort by created_at
            organized_comments = self._organize_comments_by_thread(block_comments)

            content_block_group = self._create_blockgroup(
                name=f"{project_name} - Description & Content" if project_name else "Project Description & Content",
                weburl=weburl,
                data=combined_content,
                group_type=GroupType.TEXT_SECTION,
                group_subtype=GroupSubType.CONTENT,
                description=project_description or "Project content and description",
                source_group_id=f"{project_id}_description_content",
                format=DataFormat.MARKDOWN,
                index=block_group_index,
                requires_processing=True,
                comments=organized_comments,
            )

            block_groups.append(content_block_group)
            block_group_index += 1

        # 3. Project Milestones - One BlockGroup per Milestone
        milestones = ((project_data.get("projectMilestones") or {}).get("nodes") or [])
        for milestone_data in milestones:
            milestone_id = milestone_data.get("id", "")
            milestone_name = milestone_data.get("name", "")
            milestone_description = milestone_data.get("description", "")

            if milestone_name or milestone_description:
                # Build markdown content for this milestone with project context for searchability
                milestone_markdown = f"# Project: {project_name}\n\n## Milestone: {milestone_name}\n"
                if milestone_description:
                    milestone_markdown += f"\n{milestone_description}\n"

                # Convert images in milestone description to base64
                milestone_markdown = await self._convert_images_to_base64_in_markdown(milestone_markdown)

                # Construct milestone-specific URL: {project_url}/overview#milestone-{milestone_id}
                milestone_weburl = f"{weburl.rstrip('/')}/overview#milestone-{milestone_id}" if milestone_id and weburl else weburl

                milestone_block_group = self._create_blockgroup(
                    name=f"{project_name} - Milestone: {milestone_name}" if project_name else f"Milestone: {milestone_name}",
                    weburl=milestone_weburl,
                    data=milestone_markdown,
                    group_type=GroupType.TEXT_SECTION,
                    group_subtype=GroupSubType.MILESTONE,
                    description=milestone_description or f"Milestone: {milestone_name}",
                    source_group_id=milestone_id or f"{project_id}_milestone_{block_group_index}",
                    format=DataFormat.MARKDOWN,
                    index=block_group_index,
                    requires_processing=True,
                )
                block_groups.append(milestone_block_group)
                block_group_index += 1

        # 4. Project Updates - One BlockGroup per Update (with comments as BlockComments)
        project_updates = ((project_data.get("projectUpdates") or {}).get("nodes") or [])
        for update_data in project_updates:
            update_id = update_data.get("id", "")
            update_body = update_data.get("body") or ""

            if update_body:
                # Build markdown content for this update with project context for searchability
                author = update_data.get("user") or {}
                author_name = author.get("displayName") or author.get("name") or "Unknown"
                update_created_at = update_data.get("createdAt", "")

                update_markdown = f"# Project: {project_name}\n\n## Project Update by {author_name}\n"
                if update_created_at:
                    update_markdown += f"*Created: {update_created_at}*\n"
                update_markdown += f"\n{update_body}\n"

                # Convert images in update body to base64
                update_markdown = await self._convert_images_to_base64_in_markdown(update_markdown)

                # Update URL is always provided by Linear API
                update_weburl = update_data.get("url") or weburl

                # Build update comments as BlockComments
                update_block_comments: List[BlockComment] = []
                update_comments = ((update_data.get("comments") or {}).get("nodes") or [])
                if update_comments:
                    for comment_data in update_comments:
                        comment_body = comment_data.get("body") or ""
                        if comment_body:
                            comment_author = comment_data.get("user") or {}
                            comment_author_id = comment_author.get("id")
                            comment_author_name = comment_author.get("displayName") or comment_author.get("name") or "Unknown"
                            comment_id = comment_data.get("id", "")
                            comment_url = comment_data.get("url", "")
                            comment_created_at_str = comment_data.get("createdAt", "")
                            comment_updated_at_str = comment_data.get("updatedAt", "")

                            # Parse datetime strings to datetime objects
                            comment_created_at = self._parse_linear_datetime_to_datetime(comment_created_at_str)
                            comment_updated_at = self._parse_linear_datetime_to_datetime(comment_updated_at_str)

                            # Convert images in comment body to base64
                            comment_body = await self._convert_images_to_base64_in_markdown(comment_body)

                            # Extract attachments from comment body (excluding images) using existing function
                            attachments = None
                            if comment_body:
                                file_urls = self._extract_file_urls_from_markdown(comment_body, exclude_images=True)
                                if file_urls:
                                    attachments = [
                                        CommentAttachment(
                                            name=file_info.get("filename", ""),
                                            id=file_info.get("url", "")  # Use file URL as ID (same as external_record_id)
                                        )
                                        for file_info in file_urls
                                        if file_info.get("filename") and file_info.get("url")
                                    ]

                            # Handle quoted_text if present
                            quoted_text = comment_data.get("quotedText") or comment_data.get("quoted_text")

                            # Get thread_id from parent comment id, or use comment's own id if top-level
                            parent_comment = comment_data.get("parent") or {}
                            thread_id = parent_comment.get("id") if parent_comment else comment_id

                            update_block_comments.append(
                                BlockComment(
                                    text=comment_body,
                                    format=DataFormat.MARKDOWN,
                                    author_id=comment_author_id,
                                    author_name=comment_author_name,
                                    thread_id=thread_id,
                                    resolution_status=None,
                                    weburl=comment_url if comment_url else None,
                                    created_at=comment_created_at,
                                    updated_at=comment_updated_at,
                                    attachments=attachments if attachments else None,
                                    quoted_text=quoted_text,
                                )
                            )

                # Organize comments by thread_id and sort by created_at
                organized_update_comments = self._organize_comments_by_thread(update_block_comments)

                update_block_group = self._create_blockgroup(
                    name=f"{project_name} - Update by {author_name}" if project_name else f"Update by {author_name}",
                    weburl=update_weburl,
                    data=update_markdown,
                    group_type=GroupType.TEXT_SECTION,
                    group_subtype=GroupSubType.UPDATE,
                    description=f"Project update by {author_name}",
                    source_group_id=update_id or f"{project_id}_update_{block_group_index}",
                    format=DataFormat.MARKDOWN,
                    index=block_group_index,
                    requires_processing=True,
                    comments=organized_update_comments,
                )
                block_groups.append(update_block_group)
                block_group_index += 1

        # If no blockgroups were created (no description, content, milestones, or updates),
        # create a minimal BlockGroup with just the project name
        if not block_groups:
            minimal_data = f"# {project_name}" if project_name else f"# Project {project_id}"
            minimal_data = await self._convert_images_to_base64_in_markdown(minimal_data)

            minimal_block_group = self._create_blockgroup(
                name=project_name or f"Project {project_id}",
                weburl=weburl,
                data=minimal_data,
                group_type=GroupType.TEXT_SECTION,
                group_subtype=GroupSubType.CONTENT,
                description="Project information",
                source_group_id=project_id,
                format=DataFormat.MARKDOWN,
                index=0,
                requires_processing=True,
            )
            block_groups.append(minimal_block_group)

        return BlocksContainer(blocks=[], block_groups=block_groups)

    def _organize_issue_comments_to_threads(self, comments_data: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        Group comments by thread (parent_id) and sort by created_at.
        Returns list of threads, each thread is a list of comments sorted by created_at.

        Args:
            comments_data: List of comment data from Linear API

        Returns:
            List of threads, each thread is a list of comments sorted by created_at (oldest first)
        """
        if not comments_data:
            return []

        threads: Dict[str, List[Dict[str, Any]]] = {}

        for comment in comments_data:
            parent = comment.get("parent", {})
            # Thread ID is parent's ID if it's a reply, or self ID if top-level
            thread_id = parent.get("id") if parent and parent.get("id") else comment.get("id", "")
            if not thread_id:
                continue

            if thread_id not in threads:
                threads[thread_id] = []
            threads[thread_id].append(comment)

        # Sort each thread by created_at (oldest first)
        for thread_id in threads:
            threads[thread_id].sort(
                key=lambda c: self._parse_linear_datetime(c.get("createdAt", "")) or 0
            )

        # Sort threads by first comment's created_at (oldest thread first)
        sorted_threads = sorted(
            threads.values(),
            key=lambda t: self._parse_linear_datetime(t[0].get("createdAt", "")) or 0 if t else 0
        )

        return sorted_threads

    async def _parse_issue_to_blocks(
        self,
        issue_data: Dict[str, Any],
        weburl: Optional[str] = None,
        children_records: Optional[List[ChildRecord]] = None,
        comment_file_children_map: dict[str, list[ChildRecord]] | None = None,
    ) -> BlocksContainer:
        """
        Parse issue data into BlockGroups that will be processed by Docling.

        This creates:
        - Description BlockGroup (index=0) with:
          - Issue description markdown
          - children_records (attachments, documents, files)
        - Thread BlockGroups (index=1,2,...) for each comment thread with:
          - parent_index=0 (pointing to Description BlockGroup)
        - Comment BlockGroups (sub_type=COMMENT) for each comment with:
          - parent_index pointing to thread BlockGroup index
          - requires_processing=True (so Docling processes them)
          - children_records (files from comment body)

        Args:
            issue_data: Full issue data from Linear API (with nested data)
            weburl: Issue web URL (required for docling BlockGroups)
            children_records: List of ChildRecord for attachments, documents, files (for description)
            comment_file_children_map: Dictionary mapping comment_id to List[ChildRecord] for files in comments

        Returns:
            BlocksContainer with Description BlockGroup, Thread BlockGroups, and Comment BlockGroups
        """
        issue_id = issue_data.get("id", "")
        issue_identifier = issue_data.get("identifier", "")
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")

        if not weburl:
            raise ValueError("weburl is required when creating docling BlockGroup for issues")

        # Build issue name with identifier in square brackets (matches record_name pattern)
        # e.g., "[ENG-30] My Issue Title" - identifier always in brackets when present
        if issue_identifier and issue_title:
            issue_name = f"[{issue_identifier}] {issue_title}"
        elif issue_identifier:
            issue_name = f"[{issue_identifier}]"
        elif issue_title:
            issue_name = issue_title
        else:
            issue_name = f"Issue {issue_id}"

        block_groups: List[BlockGroup] = []
        blocks: List[Block] = []
        block_group_index = 0

        # 1. Description BlockGroup (index=0)
        # Always prepend heading with issue_name (includes identifier in brackets)
        if issue_description:
            description_content = f"# {issue_name}\n\n{issue_description}"
        else:
            description_content = f"# {issue_name}"

        # Convert images in description to base64
        description_content = await self._convert_images_to_base64_in_markdown(description_content)

        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=block_group_index,
            name=issue_name,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Description for issue {issue_identifier}" if issue_identifier else "Issue description",
            source_group_id=f"{issue_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=weburl,
            requires_processing=True,
            children_records=children_records,
        )
        block_groups.append(description_block_group)
        block_group_index += 1

        # 2. Comment Thread BlockGroups (index=1,2,...) and Comment Blocks
        comments_data = ((issue_data.get("comments") or {}).get("nodes") or [])

        if comments_data:
            sorted_threads = self._organize_issue_comments_to_threads(comments_data)

            for thread_comments in sorted_threads:
                if not thread_comments:
                    continue

                # Get thread ID from first comment (either its ID if top-level, or parent ID if reply)
                first_comment = thread_comments[0]
                parent = first_comment.get("parent", {})
                thread_id = parent.get("id") if parent and parent.get("id") else first_comment.get("id", "")

                # Create thread BlockGroup with parent_index=0 (Description BlockGroup)
                thread_block_group = BlockGroup(
                    id=str(uuid4()),
                    index=block_group_index,
                    parent_index=0,  # Points to Description BlockGroup
                    name=f"Comment Thread - {thread_id[:8]}" if thread_id else "Comment Thread",
                    type=GroupType.TEXT_SECTION,
                    sub_type=GroupSubType.COMMENT_THREAD,
                    description=f"Comment thread for issue {issue_identifier}" if issue_identifier else "Comment thread",
                    source_group_id=f"{issue_id}_thread_{thread_id}" if thread_id else f"{issue_id}_thread_{block_group_index}",
                    weburl=thread_comments[0].get("url") if thread_comments else weburl,
                    requires_processing=False,
                )
                block_groups.append(thread_block_group)
                thread_block_group_index = block_group_index
                block_group_index += 1

                # Create BlockGroup objects for each comment in the thread
                for comment in thread_comments:
                    comment_body = comment.get("body") or ""
                    if not comment_body:
                        continue

                    comment_id = comment.get("id", "")
                    comment_url = comment.get("url", "")
                    comment_created_at_str = comment.get("createdAt", "")
                    comment_updated_at_str = comment.get("updatedAt", "")

                    # Parse datetime strings to datetime objects
                    self._parse_linear_datetime_to_datetime(comment_created_at_str)
                    self._parse_linear_datetime_to_datetime(comment_updated_at_str)

                    user = comment.get("user") or {}
                    author_name = user.get("displayName") or user.get("name") or "Unknown"

                    # Convert images in comment body to base64
                    comment_body = await self._convert_images_to_base64_in_markdown(comment_body)

                    # Get file children_records for this comment if any
                    comment_file_children = None
                    if comment_file_children_map and comment_id in comment_file_children_map:
                        comment_file_children = comment_file_children_map[comment_id]

                    # Create BlockGroup with sub_type=COMMENT
                    comment_block_group = BlockGroup(
                        id=str(uuid4()),
                        index=block_group_index,
                        parent_index=thread_block_group_index,  # Points to thread BlockGroup
                        name=f"Comment by {author_name}",
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.COMMENT,
                        description=f"Comment by {author_name}",
                        source_group_id=comment_id,
                        data=comment_body,
                        format=DataFormat.MARKDOWN,
                        weburl=comment_url if comment_url else None,
                        requires_processing=True,
                        children_records=comment_file_children,
                    )
                    block_groups.append(comment_block_group)
                    block_group_index += 1

        # If no blockgroups were created (no description and no comments), create minimal BlockGroup
        if not block_groups:
            minimal_data = f"# {issue_title}" if issue_title else f"# Issue {issue_identifier or issue_id}"
            minimal_data = await self._convert_images_to_base64_in_markdown(minimal_data)
            minimal_block_group = BlockGroup(
                id=str(uuid4()),
                index=0,
                name=f"{issue_identifier} - Description" if issue_identifier else "Issue Description",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                description=f"Description for issue {issue_identifier}" if issue_identifier else "Issue description",
                source_group_id=f"{issue_id}_description",
                data=minimal_data,
                format=DataFormat.MARKDOWN,
                weburl=weburl,
                requires_processing=True,
                children_records=children_records,
            )
            block_groups.append(minimal_block_group)

        # Populate children arrays for BlockGroups
        # Build a map of parent_index -> list of child BlockGroup indices
        blockgroup_children_map = defaultdict(list)  # Maps BlockGroup index -> list of child BlockGroup indices

        # Collect all BlockGroup children (thread groups that are children of description,
        # and comment groups that are children of thread groups)
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)

        # Now populate the children arrays using range-based structure
        for bg in block_groups:
            # Add child BlockGroups
            if bg.index in blockgroup_children_map:
                child_bg_indices = sorted(blockgroup_children_map[bg.index])
                # Convert to range-based structure
                bg.children = BlockGroupChildren.from_indices(block_group_indices=child_bg_indices)

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    def _stream_error(self, error: LinearApiError) -> HTTPException:
        """What a failed Linear call means to someone opening a record."""
        if isinstance(error, LinearNotFound):
            return not_found_at_source(self.display_name)
        if isinstance(error, LinearRateLimited):
            return map_source_status(HttpStatusCode.TOO_MANY_REQUESTS.value, connector=self.display_name)
        if isinstance(error, LinearForbidden):
            return map_source_status(HttpStatusCode.FORBIDDEN.value, connector=self.display_name)
        if isinstance(error, LinearAuthError):
            return map_source_status(HttpStatusCode.UNAUTHORIZED.value, connector=self.display_name)
        if error.status is None or error.status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value:
            return map_source_status(HttpStatusCode.SERVICE_UNAVAILABLE.value, connector=self.display_name)
        return map_source_status(error.status, connector=self.display_name)

    async def _fetch_for_stream(self, name: str, invoke: Invoke, key: str) -> dict[str, Any]:
        """Fetch one entity for streaming; an HTTPException says why it could not be."""
        try:
            data = await self._api.run(name, invoke)
        except LinearApiError as e:
            self.logger.warning("Failed to fetch %s for streaming: %s", name, e)
            raise self._stream_error(e) from e
        entity = data.get(key)
        if not entity:
            raise_for_stream_fetch(success=True, has_payload=False, connector=self.display_name)
        return entity

    async def _process_project_blockgroups_for_streaming(self, record: Record) -> bytes:
        """
        Process project BlockGroups for streaming by creating BlocksContainer on-demand.

        This function:
        1. Fetches project data from Linear API
        2. Parses project content to BlockGroups (description, milestones, updates)
        3. Processes external links and documents with BlockGroups
        4. Fetches FileRecords and adds them to description BlockGroup's children_records
        5. Combines everything into BlocksContainer
        6. Serializes BlocksContainer to JSON bytes for streaming

        Args:
            record: ProjectRecord to stream

        Returns:
            bytes: Serialized BlocksContainer as JSON bytes

        Raises:
            Exception: If project data cannot be fetched or processed
        """
        record_id = record.external_record_id
        try:
            project_data = await self._fetch_full_project(record_id)
        except LinearApiError as e:
            self.logger.warning("Failed to fetch project %s for streaming: %s", record_id, e)
            raise self._stream_error(e) from e
        if project_data is None:
            raise not_found_at_source(self.display_name)

        # Get project weburl for BlockGroup
        project_weburl = project_data.get("url") or f"https://linear.app/project/{record_id}"

        # Parse project content to BlockGroups
        blocks_container = await self._parse_project_to_blocks(
            project_data,
            weburl=project_weburl,
            related_external_record_ids=None
        )

        # Get external links, documents data from API
        external_links_data = ((project_data.get("externalLinks") or {}).get("nodes") or [])
        documents_data = ((project_data.get("documents") or {}).get("nodes") or [])
        project_content = project_data.get("content", "")

        link_block_groups: List[BlockGroup] = []
        document_block_groups: List[BlockGroup] = []
        file_children: List[ChildRecord] = []

        if external_links_data:
            # Process external links and create BlockGroups (with create_block_groups=True)
            link_records, link_block_groups = await self._process_project_external_links(
                external_links_data=external_links_data,
                project_id=record_id,
                project_node_id=record.id,
                team_id=record.external_record_group_id or "",
                create_block_groups=True
            )

        if documents_data:
            # Process documents and create BlockGroups (with create_block_groups=True)
            document_records, document_block_groups = await self._process_project_documents(
                documents_data=documents_data,
                project_id=record_id,
                project_node_id=record.id,
                team_id=record.external_record_group_id or "",
                create_block_groups=True
            )

        # Extract files from project content and create FileRecords if they don't exist
        if project_content:
            # Get project timestamps for file records
            project_created_at = self._parse_linear_datetime(project_data.get("createdAt", "")) or 0
            project_updated_at = self._parse_linear_datetime(project_data.get("updatedAt", "")) or 0

            file_children = await self._process_content_files_for_children(
                content=project_content,
                parent_external_id=record_id,
                parent_node_id=record.id,
                parent_record_type=RecordType.PROJECT,
                team_id=record.external_record_group_id or "",
                parent_created_at=project_created_at,
                parent_updated_at=project_updated_at,
                parent_weburl=project_weburl
            )

        # Add file children to the first BlockGroup's (description) children_records
        if file_children and blocks_container.block_groups:
            first_block_group = blocks_container.block_groups[0]
            # Append to existing children_records
            if first_block_group.children_records:
                first_block_group.children_records.extend(file_children)
            else:
                first_block_group.children_records = file_children

        # Add BlockGroups for external links and documents to the blocks container
        base_index = len(blocks_container.block_groups)
        for i, block_group in enumerate(link_block_groups + document_block_groups):
            block_group.index = base_index + i
            blocks_container.block_groups.append(block_group)

        # Serialize BlocksContainer to JSON bytes
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')

    async def _process_issue_blockgroups_for_streaming(self, record: Record) -> bytes:
        """
        Process issue BlockGroups for streaming by creating BlocksContainer on-demand.

        This function:
        1. Fetches issue data from Linear API
        2. Fetches related records from database (attachments, documents, files)
        3. Parses issue to BlocksContainer with Description and Thread BlockGroups
        4. Serializes BlocksContainer to JSON bytes for streaming

        Args:
            record: TicketRecord to stream

        Returns:
            bytes: Serialized BlocksContainer as JSON bytes

        Raises:
            Exception: If issue data cannot be fetched or processed
        """
        issue_id = record.external_record_id
        issue_data = await self._fetch_for_stream("issue", lambda ds: ds.issue(id=issue_id), "issue")
        try:
            # The single-issue query carries the first page of each list
            await self._api.complete(issue_data, "comments", "issueComments", ["issue", "comments"],
                                     page_size=NESTED_PAGE_SIZE)
            await self._api.complete(issue_data, "attachments", "issueAttachments", ["issue", "attachments"],
                                     page_size=NESTED_PAGE_SIZE)
            await self._api.complete(issue_data, "documents", "issueDocuments", ["issue", "documents"],
                                     page_size=NESTED_PAGE_SIZE)
        except LinearApiError as e:
            raise self._stream_error(e) from e

        issue_weburl = issue_data.get("url")
        issue_description = issue_data.get("description", "")

        # Get attachments, documents data from API response
        attachments_data = ((issue_data.get("attachments") or {}).get("nodes") or [])
        documents_data = ((issue_data.get("documents") or {}).get("nodes") or [])

        # Fetch child records from database
        all_children: List[ChildRecord] = []
        comment_file_children_map: Dict[str, List[ChildRecord]] = {}

        # Process attachments
        if attachments_data:
            attachment_children = await self._process_issue_attachments(
                attachments_data=attachments_data,
                issue_id=issue_id,
                issue_node_id=record.id,
                team_id=record.external_record_group_id or "",
            )
            all_children.extend(attachment_children)

        # Process documents
        if documents_data:
            document_children = await self._process_issue_documents(
                documents_data=documents_data,
                issue_id=issue_id,
                issue_node_id=record.id,
                team_id=record.external_record_group_id or "",
            )
            all_children.extend(document_children)

        # Process files from description (excluding images) and create FileRecords if they don't exist
        if issue_description:
            # Get issue timestamps for file records
            issue_created_at = self._parse_linear_datetime(issue_data.get("createdAt", "")) or 0
            issue_updated_at = self._parse_linear_datetime(issue_data.get("updatedAt", "")) or 0

            file_children = await self._process_content_files_for_children(
                content=issue_description,
                parent_external_id=issue_id,
                parent_node_id=record.id,
                parent_record_type=RecordType.TICKET,
                team_id=record.external_record_group_id or "",
                parent_created_at=issue_created_at,
                parent_updated_at=issue_updated_at,
                parent_weburl=issue_weburl
            )
            all_children.extend(file_children)

        # Process files from comment bodies and create FileRecords
        comments_data = ((issue_data.get("comments") or {}).get("nodes") or [])
        if comments_data:
            comment_file_children_map = await self._process_comment_files_for_children(
                comments_data=comments_data,
                issue_id=issue_id,
                issue_node_id=record.id,
                team_id=record.external_record_group_id or "",
                issue_weburl=issue_weburl,
            )

        # Parse issue to BlocksContainer
        blocks_container = await self._parse_issue_to_blocks(
            issue_data=issue_data,
            weburl=issue_weburl,
            children_records=all_children if all_children else None,
            comment_file_children_map=comment_file_children_map if comment_file_children_map else None
        )

        # Serialize BlocksContainer to JSON bytes
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')

    # ==================== CONTENT STREAMING HELPERS ====================

    async def _fetch_document_content(self, document_id: str) -> str:
        """Fetch document content for streaming.
        Args:
            document_id: Linear document ID
        Returns:
            Document content (markdown format) with images converted to base64
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        document_data = await self._fetch_for_stream(
            "document", lambda ds: ds.document(id=document_id), "document")

        # Get the content field (markdown)
        content = document_data.get("content", "")
        if not content:
            return ""

        # Convert images in document content to base64
        content = await self._convert_images_to_base64_in_markdown(content)
        return content

    # ==================== ABSTRACT METHODS ====================

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (issue, project, attachment, document, or file).

        Handles:
        - TICKET: Stream issue BlocksContainer (description BlockGroup + thread BlockGroups with comments as BlockGroups with sub_type=COMMENT)
        - PROJECT: Stream project BlocksContainer (description, milestones, updates BlockGroups)
        - LINK: Stream attachment/link from weburl
        - WEBPAGE: Stream document content (markdown)
        - FILE: Stream file content from weburl

        Note: Comments are handled as BlockGroups (sub_type=COMMENT) within
        issue BlocksContainer, not as separate records. Each comment BlockGroup has
        requires_processing=True so Docling processes the comment content.
        """
        try:
            if not self.data_source:
                await self.init()

            if getattr(record, "is_placeholder", False) is True:
                raise not_downloadable(
                    f"Cannot stream placeholder record {record.external_record_id}: "
                    "it is a stub for an out-of-scope ancestor and has no content",
                    connector=self.display_name,
                )

            if record.record_type == RecordType.PROJECT:
                # Project: Fetch and stream BlocksContainer (create on-demand, serialize to JSON)
                blocks_json_bytes = await self._process_project_blockgroups_for_streaming(record)

                return StreamingResponse(
                    iter([blocks_json_bytes]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}_blocks.json"'
                    }
                )

            elif record.record_type == RecordType.TICKET:
                # Issue: Fetch and stream BlocksContainer (like projects)
                # Comments are handled as Block objects within issue BlocksContainer
                blocks_json_bytes = await self._process_issue_blockgroups_for_streaming(record)

                return StreamingResponse(
                    iter([blocks_json_bytes]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}_blocks.json"'
                    }
                )

            elif record.record_type == RecordType.LINK:
                # Stream attachment/link as markdown (clickable link format)
                if not record.weburl:
                    raise not_downloadable(
                        f"LinkRecord {record.external_record_id} has no URL to open.",
                        connector=self.display_name,
                    )

                # Return simple markdown link format (same as issue/comment descriptions)
                link_name = record.record_name or 'Link'
                markdown_content = f"# {link_name}\n\n[{record.weburl}]({record.weburl})"

                return StreamingResponse(
                    iter([markdown_content.encode('utf-8')]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
                    }
                )

            elif record.record_type == RecordType.WEBPAGE:
                # Stream document content (markdown format)
                document_id = record.external_record_id
                content = await self._fetch_document_content(document_id)

                return StreamingResponse(
                    iter([content.encode('utf-8')]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
                    }
                )

            elif record.record_type == RecordType.FILE:
                # Stream file content from external_record_id (file URL)
                if not record.external_record_id:
                    raise not_downloadable(
                        f"FileRecord {record.id} has no source URL to download from.",
                        connector=self.display_name,
                    )

                # The record id is the upload URL (plus which parent links it)
                file_url = file_record_url(record.external_record_id)
                if not is_linear_upload_url(file_url):
                    raise not_downloadable(
                        f"FileRecord {record.id} does not point at a Linear upload.",
                        connector=self.display_name,
                    )

                # Download file content and stream it with authentication
                async def file_stream() -> AsyncGenerator[bytes, None]:
                    if not self.data_source:
                        await self.init()

                    datasource = await self._get_fresh_datasource()
                    try:
                        async for chunk in datasource.download_file(file_url):
                            yield chunk
                    except Exception as e:
                        self.logger.error(f"❌ Error downloading file for record {record.id}: {e}")
                        raise

                # Determine filename from record_name or the URL
                filename = record.record_name or file_url.rsplit("/", 1)[-1]
                # Safely access extension attribute (only exists on FileRecord)
                extension = getattr(record, 'extension', None)
                if extension:
                    filename = f"{filename}.{extension}" if not filename.endswith(f".{extension}") else filename

                # Use record's mime_type if available, otherwise detect from extension
                media_type = record.mime_type if record.mime_type else MimeTypes.UNKNOWN.value

                return StreamingResponse(
                    file_stream(),
                    media_type=media_type,
                    headers={
                        "Content-Disposition": content_disposition(filename)
                    }
                )

            else:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Unsupported record type for streaming: {record.record_type}",
                )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.external_record_id} ({record.record_type}): {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

    async def run_incremental_sync(self) -> None:
        """
        Incremental sync - calls run_sync which handles incremental logic.
        """
        self.logger.info(f"🔄 Starting Linear incremental sync for connector {self.connector_id}")
        await self.run_sync()
        self.logger.info("✅ Linear incremental sync completed")

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Linear using DataSource"""
        try:
            if not self.data_source:
                await self.init()

            # Test by fetching organization info (simple API call)
            datasource = await self._get_fresh_datasource()
            response = await self._with_transient_retry("organization", datasource.organization)

            if response.success and response.data is not None:
                self.logger.info("✅ Linear connection test successful")
                return True
            else:
                self.logger.error(f"❌ Connection test failed: {response.message if hasattr(response, 'message') else 'Unknown error'}")
                return False
        except Exception as e:
            self.logger.error(f"❌ Connection test failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific record"""
        # Linear doesn't support signed URLs, return empty string
        return ""

    async def handle_webhook_notification(self, notification: dict) -> None:
        """Handle webhook notifications from Linear"""
        # TODO: Implement webhook handling when Linear webhooks are configured
        pass

    async def cleanup(self) -> None:
        """Cleanup resources - close HTTP client connections properly"""
        try:
            self.logger.info("Cleaning up Linear connector resources")

            # Close HTTP client properly BEFORE event loop closes
            # This prevents Windows asyncio "Event loop is closed" errors
            if self.external_client:
                try:
                    internal_client = self.external_client.get_client()
                    if internal_client and hasattr(internal_client, 'close'):
                        await internal_client.close()
                        self.logger.debug("Closed Linear HTTP client connection")
                except Exception as e:
                    # Swallow errors during shutdown - client may already be closed
                    self.logger.debug(f"Error closing Linear client (may be expected during shutdown): {e}")

            self.logger.info("✅ Linear connector cleanup completed")
        except Exception as e:
            self.logger.warning(f"⚠️ Error during Linear connector cleanup: {e}")

    # ==================== REINDEXING METHODS ====================

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex a list of Linear records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor
        4. Skips reindex for records that are not properly typed (base Record class)"""
        try:
            if not record_results:
                return

            self.logger.info(f"Starting reindex for {len(record_results)} Linear records")

            # Ensure external clients are initialized
            if not self.data_source:
                await self.init()

            await self._get_fresh_datasource()
            # A reindex writes what changed at the source; what the filters leave out must not be
            try:
                self.sync_filters, self.indexing_filters = await load_connector_filters(
                    self.config_service, "linear", self.connector_id, self.logger)
            except Exception as e:
                self.logger.warning(f"Could not read the sync filters; reindexing with the last ones read: {e}")

            # Check records at source for updates
            updated_records = []
            non_updated_records = []

            for record in record_results:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(record)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB only for records that changed at source
            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB that changed at source")

            # Publish reindex events for non updated records
            if non_updated_records:
                reindexable_records = []
                skipped_count = 0

                for record in non_updated_records:
                    # Only reindex properly typed records (TicketRecord, ProjectRecord, etc.)
                    record_class_name = type(record).__name__
                    if record_class_name != 'Record':
                        reindexable_records.append(record)
                    else:
                        self.logger.warning(
                            f"Record {record.id} ({record.record_type}) is base Record class "
                            f"(not properly typed), skipping reindex"
                        )
                        skipped_count += 1

                if reindexable_records:
                    try:
                        await self.data_entities_processor.reindex_existing_records(reindexable_records)
                        self.logger.info(f"Published reindex events for {len(reindexable_records)} records")
                    except NotImplementedError as e:
                        self.logger.warning(
                            f"Cannot reindex records - to_kafka_record not implemented: {e}"
                        )

                if skipped_count > 0:
                    self.logger.warning(f"Skipped reindex for {skipped_count} records that are not properly typed")

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex Linear records: {e}")
            raise

    async def _check_and_fetch_updated_record(
        self, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch record from source and return data for reindexing.

        Args:
            record: Record to check

        Returns:
            Tuple of (Record, List[Permission]) if updated, None if not updated or error
        """
        try:
            # Handle ProjectRecord
            if record.record_type == RecordType.PROJECT:
                return await self._check_and_fetch_updated_project(record)

            # Handle TicketRecord: only issues now
            elif record.record_type == RecordType.TICKET:
                return await self._check_and_fetch_updated_issue(record)

            # Note: Comments are handled as blocks within issues, not as separate records

            # Handle WebpageRecord: only one type (documents)
            elif record.record_type == RecordType.WEBPAGE:
                return await self._check_and_fetch_updated_document(record)

            # Handle LinkRecord: can be from issue attachments or project external links
            elif record.record_type == RecordType.LINK:
                # Check parent to determine source
                parent_external_id = record.parent_external_record_id
                if not parent_external_id:
                    self.logger.warning(f"LinkRecord {record.external_record_id} has no parent, cannot determine source")
                    return None

                # Check if parent is a project by looking up parent record's record_type
                try:
                    parent_record = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id, parent_external_id
                    )
                    if parent_record:
                        if parent_record.record_type == RecordType.PROJECT:
                            # Project external link - check parent project
                            return await self._check_and_fetch_updated_project_link(record, parent_record)
                        else:
                            # Issue attachment - check parent issue
                            return await self._check_and_fetch_updated_issue_link(record, parent_record)
                    else:
                        self.logger.warning(f"LinkRecord {record.external_record_id} parent {parent_external_id} not found")
                        return None
                except Exception as e:
                    self.logger.debug(f"Could not determine LinkRecord source: {e}")
                    return None

            # Handle FileRecord: can be from issue descriptions, comment bodies, or project descriptions
            elif record.record_type == RecordType.FILE:
                # They don't change independently, so return None (no update needed)
                self.logger.debug(f"FileRecord {record.external_record_id} reindex: no update needed (handled by parent reindex)")
                return None

            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_issue(
        self, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch issue from source for reindexing."""
        try:
            issue_id = record.external_record_id

            # Fetch issue from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.issue(id=issue_id)

            if not response.success:
                self.logger.warning(f"Issue {issue_id} not found at source: {response.message}")
                return None

            issue_data = response.data.get("issue", {}) if response.data else {}
            if not issue_data:
                self.logger.warning(f"No issue data found for {issue_id}")
                return None

            # Check if updated timestamp changed
            current_updated_at = None
            updated_at_str = issue_data.get("updatedAt")
            if updated_at_str:
                current_updated_at = self._parse_linear_datetime(updated_at_str)

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at and current_updated_at:
                if record.source_updated_at == current_updated_at:
                    self.logger.debug(f"Issue {issue_id} has not changed at source")
                    return None

            self.logger.info(f"Issue {issue_id} has changed at source")

            # The issue's current team, which is not always the one it was stored under
            team_id = (issue_data.get("team") or {}).get("id") or record.external_record_group_id or ""
            if not team_id:
                self.logger.warning(f"Issue {issue_id} has no team_id, cannot reindex")
                return None
            if team_id != record.external_record_group_id:
                group = await self.data_entities_processor.get_record_group_by_external_id(
                    connector_id=self.connector_id, external_id=team_id)
                if group is None or not self._team_in_filter(team_id):
                    # Moved to a team this connector does not sync: it must not stay
                    # readable through its old team.
                    self.logger.info(f"Issue {issue_id} moved to a team outside the sync; removing it")
                    await self._remover.remove_record(record)
                    return None
            elif not self._team_in_filter(team_id):
                return None  # its team is left out by the filter; the record stays unlinked

            # Transform issue to ticket record
            ticket_record = self._transform_issue_to_ticket_record(issue_data, team_id, record)
            if not self._in_date_scope(ticket_record):
                return None  # left out by the date filters; the record stays unlinked

            # Extract permissions (empty list for now, Linear doesn't have explicit permissions)
            permissions = []

            return (ticket_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking issue {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_project(
        self, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """
        Fetch project from source for reindexing.

        Note: Projects use lazy block creation - blocks are created only during streaming,
        not during sync/reindexing. This method only updates the project record metadata.
        """
        try:
            project_id = record.external_record_id

            # Fetch project from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.project(id=project_id)

            if not response.success:
                self.logger.warning(f"Project {project_id} not found at source: {response.message}")
                return None

            project_data = response.data.get("project", {}) if response.data else {}
            if not project_data:
                self.logger.warning(f"No project data found for {project_id}")
                return None

            # Check if updated timestamp changed
            current_updated_at = None
            updated_at_str = project_data.get("updatedAt")
            if updated_at_str:
                current_updated_at = self._parse_linear_datetime(updated_at_str)

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at and current_updated_at:
                if record.source_updated_at == current_updated_at:
                    self.logger.debug(f"Project {project_id} has not changed at source")
                    return None

            self.logger.info(f"Project {project_id} has changed at source")

            # Get team_id from external_record_group_id
            team_id = record.external_record_group_id or ""
            if not team_id:
                self.logger.warning(f"Project {project_id} has no team_id, cannot reindex")
                return None

            # Transform project to ProjectRecord (without BlocksContainer - created only during streaming)
            project_record = self._transform_to_project_record(
                project_data, team_id, record
            )

            # Extract issues data directly from project_data and add to related_external_records
            issues_data = ((project_data.get("issues") or {}).get("nodes") or [])
            if issues_data:
                from app.models.entities import RelatedExternalRecord
                project_record.related_external_records = [
                    RelatedExternalRecord(
                        external_record_id=issue.get("id"),
                        record_type=RecordType.TICKET,
                        relation_type=RecordRelations.LINKED_TO
                    )
                    for issue in issues_data
                    if issue.get("id")
                ]

            # Records inherit permissions from RecordGroup (team), so pass empty list
            permissions = []

            return (project_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking project {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_document(
        self, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch document from source for reindexing."""
        try:
            document_id = record.external_record_id
            if not document_id:
                self.logger.warning(f"Document record {record.id} has no external_record_id")
                return None

            # Fetch document from Linear API
            datasource = await self._get_fresh_datasource()
            response = await datasource.document(id=document_id)

            if not response.success:
                self.logger.warning(f"Document {document_id} not found at source: {response.message}")
                return None

            document_data = response.data.get("document", {}) if response.data else {}
            if not document_data:
                return None

            # Check if document has been updated
            updated_at = self._parse_linear_datetime(document_data.get("updatedAt", ""))
            if not updated_at:
                return None

            # Compare with existing record's source_updated_at
            if hasattr(record, 'source_updated_at') and record.source_updated_at and updated_at:
                if record.source_updated_at == updated_at:
                    # Document hasn't changed
                    return None

            # Check if document is associated with an issue or a project
            issue_data = document_data.get("issue", {})
            project_data = document_data.get("project", {})

            # Determine parent type and ID (one of them must be present)
            parent_external_id = None

            if project_data:
                # Document is associated with a project
                parent_external_id = project_data.get("id")
            elif issue_data:
                # Document is associated with an issue
                parent_external_id = issue_data.get("id")
            else:
                # Neither issue nor project found - this should not happen
                self.logger.warning(f"Document {document_id} has neither issue nor project, cannot transform properly")
                return None

            if not parent_external_id:
                self.logger.warning(f"Document {document_id} has no parent ID, cannot transform properly")
                return None

            team_id = record.external_record_group_id or ""
            parent_node_id = record.parent_node_id

            if not parent_node_id:
                self.logger.warning(f"Document {document_id} has no parent_node_id, cannot transform properly")
                return None

            # Transform document to WebpageRecord
            webpage_record = self._transform_document_to_webpage_record(
                document_data=document_data,
                issue_id=parent_external_id,
                parent_node_id=parent_node_id,
                team_id=team_id,
                existing_record=record
            )

            permissions = []
            return (webpage_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking document {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_issue_link(
        self, record: Record, parent_record: Optional[Record] = None
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch issue attachment/link from source for reindexing."""
        try:
            attachment_id = record.external_record_id
            if not attachment_id:
                self.logger.warning(f"LinkRecord {record.id} has no external_record_id")
                return None

            # Fetch attachment from Linear API
            datasource = await self._get_fresh_datasource()
            response = await datasource.attachment(id=attachment_id)

            if not response.success:
                self.logger.warning(f"Attachment {attachment_id} not found at source: {response.message}")
                return None

            attachment_data = response.data.get("attachment", {}) if response.data else {}
            if not attachment_data:
                return None

            # Check if attachment has been updated
            updated_at = self._parse_linear_datetime(attachment_data.get("updatedAt", ""))
            if not updated_at:
                return None

            # Compare with existing record's source_updated_at
            if hasattr(record, 'source_updated_at') and record.source_updated_at and updated_at:
                if record.source_updated_at == updated_at:
                    # Attachment hasn't changed
                    return None

            # Attachment has changed, transform and return
            issue_data = attachment_data.get("issue", {})
            issue_id = issue_data.get("id", record.parent_external_record_id or "")
            team_id = record.external_record_group_id or ""

            # Get parent node ID - prefer from parent_record if provided, otherwise use record's parent_node_id
            parent_node_id = None
            if parent_record:
                parent_node_id = parent_record.id
            elif record.parent_node_id:
                parent_node_id = record.parent_node_id

            if not issue_id:
                self.logger.warning(f"Attachment {attachment_id} has no issue_id, cannot transform properly")
                return None

            if not parent_node_id:
                self.logger.warning(f"Attachment {attachment_id} has no parent_node_id, cannot transform properly")
                return None

            # Transform attachment to LinkRecord
            link_record = self._transform_attachment_to_link_record(
                attachment_data=attachment_data,
                issue_id=issue_id,
                parent_node_id=parent_node_id,
                team_id=team_id,
                existing_record=record
            )

            # Look up related record by weburl
            if link_record.weburl:
                try:
                    related_record = await self.data_entities_processor.get_record_by_weburl(
                        link_record.weburl
                    )
                    if related_record:
                        link_record.linked_record_id = related_record.id
                        self.logger.debug(f"🔗 Found related record {related_record.id} for attachment URL: {link_record.weburl}")
                except Exception as e:
                    self.logger.debug(f"⚠️ Could not fetch related record for URL {link_record.weburl}: {e}")

            permissions = []
            return (link_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking attachment {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_project_link(
        self, record: Record, parent_record: Optional[Record] = None
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch project external link from source for reindexing."""
        try:
            link_id = record.external_record_id
            if not link_id:
                self.logger.warning(f"LinkRecord {record.id} has no external_record_id")
                return None

            # Get project_id from parent_record if available, otherwise from record
            project_id = None
            if parent_record:
                project_id = parent_record.external_record_id
            else:
                project_id = record.parent_external_record_id or ""

            if not project_id:
                self.logger.warning(f"External link {link_id} has no project_id")
                return None

            # Fetch project with external links from Linear API
            datasource = await self._get_fresh_datasource()
            response = await datasource.project(id=project_id)

            if not response.success:
                self.logger.warning(f"Project {project_id} not found at source: {response.message}")
                return None

            project_data = response.data.get("project", {}) if response.data else {}
            if not project_data:
                return None

            # Find the specific external link by ID in project's externalLinks
            external_links = ((project_data.get("externalLinks") or {}).get("nodes") or [])
            link_data = None
            for link in external_links:
                if link.get("id") == link_id:
                    link_data = link
                    break

            if not link_data:
                self.logger.warning(f"External link {link_id} not found in project {project_id}")
                return None

            # Check if link has been updated
            updated_at = self._parse_linear_datetime(link_data.get("updatedAt", ""))
            if not updated_at:
                return None

            # Compare with existing record's source_updated_at
            if hasattr(record, 'source_updated_at') and record.source_updated_at and updated_at:
                if record.source_updated_at == updated_at:
                    # Link hasn't changed
                    return None

            # Link has changed, transform and return
            team_id = record.external_record_group_id or ""

            # Get parent node ID - prefer from parent_record if provided, otherwise use record's parent_node_id
            parent_node_id = None
            if parent_record:
                parent_node_id = parent_record.id
            elif record.parent_node_id:
                parent_node_id = record.parent_node_id

            if not parent_node_id:
                self.logger.warning(f"External link {link_id} has no parent_node_id, cannot transform properly")
                return None

            # Transform external link to LinkRecord
            link_record = self._transform_attachment_to_link_record(
                attachment_data=link_data,
                issue_id=project_id,  # Use project_id as parent
                parent_node_id=parent_node_id,
                team_id=team_id,
                existing_record=record
            )

            # Look up related record by weburl
            if link_record.weburl:
                try:
                    related_record = await self.data_entities_processor.get_record_by_weburl(
                        link_record.weburl
                    )
                    if related_record:
                        link_record.linked_record_id = related_record.id
                        self.logger.debug(f"🔗 Found related record {related_record.id} for external link URL: {link_record.weburl}")
                except Exception as e:
                    self.logger.debug(f"⚠️ Could not fetch related record for URL {link_record.weburl}: {e}")

            permissions = []
            return (link_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking external link {record.id} at source: {e}")
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
    ) -> "BaseConnector":
        """Factory method to create LinearConnector instance"""
        return LinearConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
