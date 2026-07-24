"""Jira Cloud Connector Implementation"""
import asyncio
import base64
import random
import re
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable
from html import escape as html_escape
from datetime import datetime, timezone, tzinfo
from logging import Logger
from typing import (
    Any,
    Optional,
)
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx  # type: ignore
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    ProgressStatus,
    RecordRelations,
    get_mime_type_for_extension,
    normalize_file_extension,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector, ConnectorInitError
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.constants import (
    CONNECTOR_EMAIL_IDENTITY_INFO,
    IconPaths,
)
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
from app.connectors.sources.atlassian.core.apps import JiraApp
from app.connectors.sources.atlassian.core.html_utils import (
    extract_attachment_ids,
    inline_images_as_base64,
)
from app.connectors.sources.atlassian.core.oauth import (
    OAUTH_JIRA_CONFIG_PATH,
    AtlassianScope,
)
from app.connectors.utils.value_mapper import ValueMapper, map_relationship_type
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    MimeTypes,
    OriginTypes,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import (
    NotificationRecipientRole,
    NotificationSeverity,
    NotificationType,
)
from app.sources.client.jira.jira import JiraClient
from app.sources.external.common.atlassian import AtlassianMultiSiteError
from app.sources.external.jira.jira import JiraDataSource
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# API URLs
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"

# Pagination/constants
DEFAULT_MAX_RESULTS: int = 50
# Jira's /search/jql caps a page at 100 issues; using the max halves pagination round-trips
# (and rate-limit pressure) vs the 50 default. Only for issue search — project search stays at
# 50 (projects are few and light).
ISSUE_PAGE_SIZE: int = 100
BATCH_PROCESSING_SIZE: int = 100
USER_PAGE_SIZE: int = 50
GROUP_PAGE_SIZE: int = 50
GROUP_MEMBER_PAGE_SIZE: int = 50
AUDIT_PAGE_SIZE: int = 500
# Max size of an image inlined as base64 into the indexed markdown. Larger images (and any
# non-image media) are represented as child FILE records instead, so a big file can't bloat
# the block (base64 also inflates ~33%).
MAX_INLINE_IMAGE_BYTES: int = 5 * 1024 * 1024  # 5 MiB
# Cap the wait honored for a Jira 429, so a large Retry-After can't stall a sync; if still
# throttled after retries, the project fails this run and resumes from checkpoint next sync.
RATE_LIMIT_MAX_DELAY_SEC: float = 60.0
# Bounded concurrency for read-only metadata fan-outs (project roles, permission schemes,
# group members). Modest on purpose: these calls don't go through the 429 retry helper, so a
# large fan-out would amplify rate-limit pressure. The single write stays batched after each
# fan-out, so there is no write concurrency here.
METADATA_FETCH_CONCURRENCY: int = 5

# JQL query constants
ISSUE_SEARCH_FIELDS: list[str] = [
    "summary", "description", "status", "priority",
    "creator", "reporter", "assignee", "created", "updated",
    "issuetype", "project", "parent", "attachment",
    "issuelinks"
]


@ConnectorBuilder("Jira")\
    .in_group(AppGroups.ATLASSIAN.value)\
    .with_description("Sync issues from Jira Cloud")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Jira",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            redirect_uri="connectors/oauth/callback/Jira",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=AtlassianScope.get_jira_read_access(),
                agent=AtlassianScope.get_jira_read_access()
            ),
            fields=[
                AuthField(
                    name="clientId",
                    display_name="Application (Client) ID",
                    placeholder="Enter your Atlassian Cloud Application ID",
                    description="The Application (Client) ID from Atlassian Developer Console"
                ),
                AuthField(
                    name="clientSecret",
                    display_name="Client Secret",
                    placeholder="Enter your Atlassian Cloud Client Secret",
                    description="The Client Secret from Atlassian Developer Console",
                    field_type="PASSWORD",
                    is_secret=True
                ),
            ],
            icon_path=IconPaths.connector_icon(Connectors.JIRA.value),
            app_group="Atlassian",
            app_description="OAuth application for accessing Jira Cloud API and issue tracking services",
            app_categories=["Storage"]
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
        "Users with private email visibility on Jira are automatically resolved if they exist in your PipesHub directory or any other connected source. Setting email visibility to Public makes the initial sync faster."
        + "\n\n"
        + CONNECTOR_EMAIL_IDENTITY_INFO
    )\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.JIRA.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Jira Cloud API Setup",
            "https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/jira/jira',
            'pipeshub'
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(
            name="project_keys",
            display_name="Project Keys",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter issues by project keys (e.g., PROJ1, PROJ2)",
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
            display_name="Index Issue and comment Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue attachments",
            default_value=True
        ))
    )\
    .build_decorator()
class JiraConnector(BaseConnector):
    """
    Jira connector for syncing projects, issues, groups, roles and users from Jira
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
            JiraApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.external_client: Optional[JiraClient] = None
        self.data_source: Optional[JiraDataSource] = None
        self.site_url: Optional[str] = None
        self.connector_id = connector_id
        self.connector_name = Connectors.JIRA

        org_id = self.data_entities_processor.org_id

        self.issues_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.sync_filters = None
        self.indexing_filters = None

        self.value_mapper = ValueMapper()
        # True when /applicationrole returned 403 (sync user is not a Jira admin)
        self._app_roles_forbidden: bool = False
        # True when /users/search returned 401/403 — bulk user listing is unavailable, so user
        # resolution degrades to the PipesHub-directory reverse lookup.
        self._user_bulk_forbidden: bool = False
        # True ONLY when /group/bulk returned 403 (account genuinely lacks Browse users and
        # groups). A 401 is an auth/token failure, not a permission problem, so it stays False.
        self._group_bulk_forbidden: bool = False
        # Email + timezone from GET /rest/api/3/myself (cached in init). Jira reads
        # bare JQL datetimes in the account timezone (see _jql_datetime, C5).
        self._authenticated_jira_email: Optional[str] = None
        self._jql_timezone: tzinfo = timezone.utc

    def _cache_authenticated_jira_profile(self, response: Any) -> None:
        """Cache the account email and timezone from GET /rest/api/3/myself.

        Jira reads bare JQL datetimes in the account timezone, so formatting date
        cuts in UTC skews every date filter and incremental window by the account's
        UTC offset (C5). Timezone falls back to UTC.
        """
        if not response:
            return
        try:
            if response.status != HttpStatusCode.OK.value:
                return
        except Exception as e:
            self.logger.debug("Could not read Jira /myself status: %s", e)
            return

        data = self._safe_json_parse(response, "GET /myself")
        if not data:
            return

        email = data.get("emailAddress")
        if email:
            self._authenticated_jira_email = email.strip()

        tz_name = data.get("timeZone")
        if tz_name:
            try:
                self._jql_timezone = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, OSError) as e:
                self.logger.warning(
                    "Jira account timezone %r unavailable (%s); JQL datetimes fall back to UTC, "
                    "which can skew date filters and incremental windows by the account's UTC "
                    "offset. Ensure 'tzdata' is installed where the OS lacks a tz database.",
                    tz_name, e,
                )

    def _jql_datetime(self, epoch_ms: int) -> str:
        """Render an epoch-ms cut as a JQL datetime string in the Jira account's
        timezone. Jira reads bare JQL datetimes in the authenticated user's zone,
        so the cut must be formatted there — not UTC — to mean the same instant.
        """
        dt = datetime.fromtimestamp(epoch_ms / 1000, tz=self._jql_timezone)
        return dt.strftime("%Y-%m-%d %H:%M")

    # ============================================================================
    # Initialization & Configuration
    # ============================================================================

    async def init(self) -> bool:
        """Initialize Jira client and DataSource from connector auth config."""
        try:
            client = await JiraClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )
            self.external_client = client
            self.data_source = JiraDataSource(client)
            # Site already resolved in build_from_services (multi-site OAuth rejected there)
            self.site_url = client.get_site_url()
            self.logger.info("✅ Jira client initialized (site: %s)", self.site_url or "unknown")

            if self.created_by:
                try:
                    creator = await self.data_entities_processor.get_user_by_user_id(self.created_by)
                    if creator and getattr(creator, "email", None):
                        self.creator_email = creator.email
                except Exception as e:
                    self.logger.warning("Could not resolve creator email for created_by %s: %s", self.created_by, e)

            try:
                myself_response = await self.data_source.get_current_user()
                self._cache_authenticated_jira_profile(myself_response)
            except Exception as e:
                self.logger.debug("Could not fetch authenticated Jira user during init: %s", e)

            return True

        except AtlassianMultiSiteError as e:
            # Notify + raise so the API returns the multi-site message, not a generic init error
            await self._notify_multi_site_ambiguity(e)
            raise ConnectorInitError(str(e)) from e
        except Exception as e:
            # Setup/HTTP callers surface this via return False → FE error. Background
            # run_sync notifies on init failure (avoid duplicate inbox alerts on connect).
            self.logger.error(f"❌ Failed to initialize Jira client: {e}")
            return False

    async def _notify_multi_site_ambiguity(self, error: AtlassianMultiSiteError) -> None:
        """Notify admin: account-level app reaches multiple sites — needs a new
        Resource-restricted OAuth app (grants cannot be changed on an existing app)."""
        self.logger.error(
            "❌ Jira connector %s: OAuth app can access multiple Atlassian sites: %s",
            self.connector_id, error,
        )
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title=self._notification_title("requires a single-site OAuth app"),
            message=(
                "This OAuth app has access to multiple Jira sites. "
                "Create a single-site (resource-restricted) OAuth app in the "
                "Atlassian Developer Console, then reconnect."
            ),
            recipient_user_ids=[self.created_by],
        )

    def _notification_title(self, event: str) -> str:
        """Title like '{instance or Jira} connector {event}' for multi-instance clarity."""
        return f"{self.connector_instance_name or 'Jira'} connector {event}"

    # ============================================================================
    # Authentication & Token Management
    # ============================================================================

    async def _get_fresh_datasource(self) -> JiraDataSource:
        """Return a DataSource; for OAuth, refresh the access token from config if it changed."""
        if not self.external_client:
            raise Exception("Jira client not initialized. Call init() first.")

        config_path = OAUTH_JIRA_CONFIG_PATH.format(connector_id=self.connector_id)
        config = await self.config_service.get_config(config_path)
        if not config:
            raise Exception("Jira configuration not found")

        auth_config = config.get("auth", {}) or {}
        auth_type = auth_config.get("authType", "OAUTH")
        if auth_type == "API_TOKEN":
            return JiraDataSource(self.external_client)

        credentials_config = config.get("credentials", {}) or {}
        fresh_token = credentials_config.get("access_token", "")
        if not fresh_token:
            raise Exception("No OAuth access token available")

        internal_client = self.external_client.get_client()
        if internal_client.get_token() != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.set_token(fresh_token)

        return JiraDataSource(self.external_client)

    # ============================================================================
    # Sync Orchestration
    # ============================================================================

    async def run_sync(self) -> None:
        """Main sync flow: users → groups → projects/roles → issues → deletions."""
        try:
            # 1. Ensure client is ready. Init itself does not notify (FE shows setup
            # errors); notify here for background sync when auth/config is broken.
            if not self.data_source:
                if not await self.init():
                    await self.notify(
                        type=NotificationType.CONNECTOR_AUTH_ERROR,
                        severity=NotificationSeverity.ERROR,
                        title=self._notification_title("connection failed"),
                        message=(
                            f"PipesHub couldn't connect to Jira during sync. "
                            "Verify the connector's credentials and configuration, "
                            "re-authenticate if needed, then sync again."
                        ),
                        recipient_roles=[NotificationRecipientRole.ADMIN],
                    )
                    init_error = RuntimeError(
                        f"Jira connector {self.connector_id} init failed; check auth configuration"
                    )
                    init_error._notification_sent = True
                    raise init_error

            # 2. Load latest sync/indexing filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "jira",
                self.connector_id,
                self.logger
            )

            # 3. Require active PipesHub users (ACL targets)//
            users = await self.data_entities_processor.get_all_active_users()
            if not users:
                self.logger.info("ℹ️ No users found")
                return

            # 4. Sync Jira users and groups (reuse the active-user list from the guard above)
            jira_users = await self._fetch_users(active_pipeshub_users=users)
            if jira_users:
                await self.data_entities_processor.on_new_app_users(jira_users)
                self.logger.info(f"👥 Synced {len(jira_users)} Jira users")
            groups_members_map = await self._sync_user_groups(jira_users)

            # 5. Resolve project filter, then fetch projects
            projects, raw_projects = await self._fetch_filtered_projects(jira_users)

            # 6. Sync roles, then record groups (projects + permissions)
            project_keys_for_roles = [proj.short_name for proj, _ in projects]
            await self._sync_project_roles(project_keys_for_roles, jira_users, groups_members_map)
            await self._sync_project_lead_roles(raw_projects, jira_users)
            await self.data_entities_processor.on_new_record_groups(projects)

            # 7. Sync issues (incremental via checkpoint), then deletions
            last_sync_time = await self._get_issues_sync_checkpoint()
            sync_stats = await self._sync_all_project_issues(projects, jira_users, last_sync_time)
            await self._update_issues_sync_checkpoint(sync_stats, len(projects))
            await self._handle_issue_deletions(last_sync_time)

            # 8. Outcome: notify when any project failed issue sync (include keys)
            failed_keys = sync_stats.get("failed_project_keys") or []
            if failed_keys:
                preview = ", ".join(failed_keys[:10])
                if len(failed_keys) > 10:
                    preview = f"{preview}, and {len(failed_keys) - 10} more"
                self.logger.error(
                    "❌ Jira sync: %s/%s project(s) failed to sync issues: %s",
                    len(failed_keys), len(projects), preview,
                )
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title=self._notification_title("couldn't sync some projects"),
                    message=(
                        f"Couldn't sync issues for {len(failed_keys)} project(s): {preview}. "
                        "Retry sync; check Jira access if it keeps failing."
                    ),
                    recipient_roles=[NotificationRecipientRole.ADMIN],
                )

            self.logger.info(
                f"✅ Jira sync completed. Total: {sync_stats['total_synced']} issues "
                f"(New: {sync_stats['new_count']}, Updated: {sync_stats['updated_count']})"
            )

        except Exception as e:
            self.logger.error(f"❌ Error during Jira sync: {e}", exc_info=True)
            # Skip if init/multi-site already notified for this failure
            if not isinstance(e, ConnectorInitError) and not getattr(e, "_notification_sent", False):
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title=self._notification_title("sync failed"),
                    message=(
                        f"The sync stopped due to an error: {str(e)[:200]}. Recent Jira changes "
                        "may not be reflected yet. Run the sync again; if it keeps failing, "
                        "check the connector's configuration."
                    ),
                    recipient_roles=[NotificationRecipientRole.ADMIN],
                )
            raise

    # ============================================================================
    # Filter Options
    # ============================================================================

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for Jira filters with pagination.
        """
        if filter_key == "project_keys":
            return await self._get_project_options(page, limit, search)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_project_options(
        self,
        page: int,
        limit: int,
        search: Optional[str]
    ) -> FilterOptionsResponse:
        """Fetch available Jira projects with pagination.

        Uses search_projects API with optional search term filtering.
        Jira uses startAt/maxResults pagination (not cursor-based).
        """
        # Get fresh datasource with refreshed OAuth token
        datasource = await self._get_fresh_datasource()

        # Calculate startAt for pagination (Jira uses 0-based startAt)
        start_at = (page - 1) * limit

        try:
            query = search

            # Fetch projects using the search_projects API.
            # No expand parameter needed - we only use 'key' and 'name' which are in default response.
            response = await datasource.search_projects(
                maxResults=limit,
                startAt=start_at,
                query=query
            )

            if not response or response.status != HttpStatusCode.OK.value:
                raise RuntimeError(
                    f"Failed to fetch projects: HTTP {response.status if response else 'No response'}"
                )

            response_data = self._safe_json_parse(response, "project search")
            if response_data is None:
                raise RuntimeError("Failed to parse project search response")
            projects_list = response_data.get("values", [])

            # Use Jira's isLast flag as the source of truth for pagination.
            is_last = response_data.get("isLast", False)
            has_more = not is_last

            # Convert to FilterOption objects.
            options = [
                FilterOption(
                    id=project.get("key"),  # Use key as id since filter expects keys.
                    label=f"{project.get('name', '')} ({project.get('key', '')})"
                )
                for project in projects_list
                if project.get("key") and project.get("name")
            ]

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=None  # Jira doesn't use cursor-based pagination.
            )
        except Exception as e:
            self.logger.error(f"❌ Error fetching projects: {e}")
            raise RuntimeError(f"Failed to fetch project options: {str(e)}")

    # ============================================================================
    # Sync Points & Checkpoints
    # ============================================================================

    async def _get_issues_sync_checkpoint(self) -> Optional[int]:
        """
        Get global sync checkpoint.
        """
        try:
            sync_point_data = await self.issues_sync_point.read_sync_point("issues_global")
            return sync_point_data.get("last_sync_time") if sync_point_data else None
        except Exception as e:
            self.logger.debug("Could not read global issues sync checkpoint (treating as no checkpoint): %s", e)
            return None

    async def _update_issues_sync_checkpoint(self, stats: dict[str, int], project_count: int) -> None:
        """
        Update global sync checkpoint.
        """
        if stats["total_synced"] > 0 or project_count > 0:
            current_time = get_epoch_timestamp_in_ms()
            sync_point_data = {
                "last_sync_time": current_time
            }
            await self.issues_sync_point.update_sync_point("issues_global", sync_point_data)

    async def _get_project_sync_checkpoint(self, project_key: str) -> dict[str, Any]:
        """
        Get project-specific sync checkpoint.

        Returns:
            Dict with last_sync_time and last_issue_updated
        """
        sync_point_key = f"project_{project_key}"
        return await self.issues_sync_point.read_sync_point(sync_point_key)

    async def _update_project_sync_checkpoint(
        self,
        project_key: str,
        last_sync_time: Optional[int] = None,
        last_issue_updated: Optional[int] = None
    ) -> None:
        """
        Update project-specific sync checkpoint.

        Args:
            project_key: Project key (e.g., "PROJ")
            last_sync_time: Timestamp when checkpoint was updated (metadata only)
            last_issue_updated: Updated timestamp of last processed issue (used for resume AND next incremental sync)
        """
        sync_point_key = f"project_{project_key}"

        # Read existing to preserve values not being updated
        existing = await self._get_project_sync_checkpoint(project_key)

        sync_point_data = {
            "last_sync_time": last_sync_time if last_sync_time is not None else existing.get("last_sync_time"),
            "last_issue_updated": last_issue_updated if last_issue_updated is not None else existing.get("last_issue_updated")
        }

        await self.issues_sync_point.update_sync_point(sync_point_key, sync_point_data)

    # ============================================================================
    # Deletion Handling
    # ============================================================================

    async def _handle_issue_deletions(self, global_last_sync_time: Optional[int]) -> None:
        """
        Detect and handle issue deletions via Audit API.
        """
        audit_sync_key = "issues_audit_deletions"

        try:
            audit_sync_point_data = await self.issues_sync_point.read_sync_point(audit_sync_key)
            audit_last_sync_time = audit_sync_point_data.get("last_sync_time") if audit_sync_point_data else None
        except Exception as e:
            self.logger.debug("Could not read deletion audit checkpoint (falling back to global sync time): %s", e)
            audit_last_sync_time = None

        deletion_check_time = audit_last_sync_time or global_last_sync_time

        if deletion_check_time:
            checkpoint_ms, success = await self._detect_and_handle_deletions(deletion_check_time)

            # Advance the checkpoint only on a clean run, and only to the window
            # actually queried — so a failed/partial run retries the same window next
            # sync and no deletion falls into an unqueried gap.
            if success:
                await self.issues_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time": checkpoint_ms}
                )

    async def _detect_and_handle_deletions(self, last_sync_time: int) -> tuple[int, bool]:
        """
        Detect and handle deleted issues using Jira Audit API.

        Returns (checkpoint_ms, success). ``checkpoint_ms`` is the exact upper bound
        that was queried — the caller stores it as the next window's start so there is
        no gap. ``success`` is False if the audit fetch was incomplete or any deletion
        failed to process, so the caller can leave the checkpoint where it is and retry
        the whole window next sync (deletion is idempotent, so re-processing is safe).
        """
        # One timestamp for BOTH the query upper bound and the stored checkpoint, so
        # consecutive windows are contiguous — nothing can be deleted in an unqueried gap.
        checkpoint_ms = get_epoch_timestamp_in_ms()
        try:
            self.logger.info("🔍 Checking for deleted issues via Audit API...")

            from_date = datetime.fromtimestamp(
                last_sync_time / 1000,
                tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            to_date = datetime.fromtimestamp(
                checkpoint_ms / 1000,
                tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            deleted_issue_keys, fetch_ok = await self._fetch_deleted_issues_from_audit(from_date, to_date)

            if not fetch_ok:
                # Window not fully read — don't advance past deletions we may have missed.
                return checkpoint_ms, False

            if not deleted_issue_keys:
                self.logger.info("ℹ️ No deleted issues found in audit log")
                return checkpoint_ms, True

            all_handled = True
            for issue_key in deleted_issue_keys:
                try:
                    await self._handle_deleted_issue(issue_key)
                except Exception:
                    # _handle_deleted_issue already logged with a traceback; record the
                    # failure so the checkpoint holds and this window is retried.
                    all_handled = False

            return checkpoint_ms, all_handled

        except Exception as e:
            self.logger.error(f"❌ Error detecting deletions: {e}", exc_info=True)
            return checkpoint_ms, False

    async def _fetch_deleted_issues_from_audit(
        self,
        from_date: str,
        to_date: str
    ) -> tuple[list[str], bool]:
        """
        Fetch deleted issue keys from Jira Audit API.

        Returns (issue_keys, ok). ``ok`` is False if any page failed to fetch, so the
        caller can avoid advancing the deletion checkpoint past a window whose
        deletions were not fully read.
        """
        deleted_issue_keys = []
        offset = 0
        limit = AUDIT_PAGE_SIZE
        ok = True

        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_audit_records(
                    offset=offset,
                    limit=limit,
                    from_=from_date,
                    to=to_date
                )

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(f"⚠️ Failed to fetch audit records: {response.text()}")
                    # Only a 403 means the account lacks the Administrator permission for the audit
                    # log. A 401 is an auth/token failure (reported by the connection/sync-failed
                    # notification) and 5xx/429 are transient — don't misreport either as a missing
                    # permission.
                    if response.status == HttpStatusCode.FORBIDDEN.value:
                        await self.notify(
                            type=NotificationType.CONNECTOR_WARNING,
                            severity=NotificationSeverity.WARNING,
                            title=self._notification_title("is missing the audit log permission"),
                            message=(
                                "The connector's Jira account lacks the Administrator permission "
                                "needed to read the audit log, so issues deleted in Jira may still "
                                "appear in search. Ask a Jira admin to grant it to enable deletion "
                                "detection."
                            ),
                            recipient_roles=[NotificationRecipientRole.ADMIN],
                            payload={
                                "redirect_link": None,
                            }
                        )
                    ok = False
                    break

                audit_data = response.json()
                records = audit_data.get("records", [])

                if not records:
                    break

                # Filter for issue deletion events
                for record in records:
                    object_item = record.get("objectItem", {})
                    type_name = object_item.get("typeName")

                    # Check if this is an issue deletion
                    if type_name == "ISSUE_DELETE":
                        issue_key = object_item.get("name")
                        if issue_key:
                            deleted_issue_keys.append(issue_key)
                            self.logger.debug(f"Audit: Issue {issue_key} deleted at {record.get('created')}")

                # Check pagination
                total = audit_data.get("total", 0)
                if offset + len(records) >= total:
                    break

                offset += limit

            except Exception as e:
                self.logger.error(f"❌ Error fetching audit records at offset {offset}: {e}")
                ok = False
                break

        return deleted_issue_keys, ok

    async def _handle_deleted_issue(self, issue_key: str) -> None:
        """
        Hard-delete a source-deleted issue and its owned attachments.

        Jira logs a separate ISSUE_DELETE audit event for EVERY deleted issue —
        including each sub-task when a parent is deleted (verified) — so every
        deleted issue reaches this method on its own and no hierarchy cascade is
        needed. Levels that are not deleted (an epic's stories, a story's tasks)
        never appear in the audit and are correctly left untouched.

        Uses ``cascade_children=False`` so only ATTACHMENT edges are traversed —
        the issue's FILE records are deleted together with it, but child tickets
        (stories under an epic, subtasks under a story) are not touched.  Their
        outgoing PARENT_CHILD edges are swept by the edge cleanup, so the child
        tickets simply lose their parent link and remain otherwise intact.
        """
        try:
            self.logger.info(f"🗑️ Handling deletion of issue {issue_key}")

            response = await self._get_issue_with_retry(issue_key, fields=["id"])
            if response.status == HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Issue {issue_key} still exists in Jira (not deleted, maybe moved?)")
                return
            if response.status not in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.GONE.value):
                raise Exception(
                    f"Deletion of {issue_key} unconfirmed: get_issue returned "
                    f"{response.status} (expected a definitive 404/410) — retrying next sync"
                )

            async with self.data_store_provider.transaction() as tx_store:
                issue_record = await tx_store.get_record_by_issue_key(
                    connector_id=self.connector_id,
                    issue_key=issue_key
                )

            if not issue_record:
                self.logger.warning(f"⚠️ Issue {issue_key} not found in database (already deleted or never synced?)")
                return

            self.logger.info(
                f"✅ Found issue {issue_key} with internal ID {issue_record.id}, "
                f"external ID {issue_record.external_record_id}"
            )

            result = await self.data_entities_processor.on_records_deleted_cascade(
                [issue_record.id], self.connector_id, cascade_children=False,
            )

            deleted_count = result.get("successfully_deleted", 0)
            self.logger.info(f"🗑️ Deleted issue {issue_key} and {deleted_count - 1} attachment(s)")

        except Exception as e:
            self.logger.error(f"❌ Error handling deleted issue {issue_key}: {e}", exc_info=True)
            raise

    # ============================================================================
    # Users, Roles, Groups and Projects Management
    # ============================================================================

    async def _fetch_users(
        self, active_pipeshub_users: Optional[list[AppUser]] = None
    ) -> list[AppUser]:
        """
        Fetch and resolve all active Jira users using a two-pass strategy:
        1. Bulk fetch from Jira (public-email users resolved directly)
        2. Reverse lookup for private-email users using PipesHub directory emails

        ``active_pipeshub_users`` lets the caller pass the active-user list it already
        loaded (run_sync fetches it for its own guard) so we don't read it twice per sync;
        falls back to a fresh fetch when called without it.
        """

        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # ====================================================================
        # Phase 1: DB reads (0 API calls)
        # ====================================================================
        cached_app_users = await self.data_entities_processor.get_all_app_users(self.connector_id)
        pipeshub_users = (
            active_pipeshub_users
            if active_pipeshub_users is not None
            else await self.data_entities_processor.get_all_active_users()
        )

        cached_account_id_to_email: dict[str, str] = {
            u.source_user_id: u.email
            for u in cached_app_users
            if u.source_user_id and u.email
        }

        pipeshub_emails: set[str] = {
            u.email.lower() for u in pipeshub_users if u.email
        }

        # ====================================================================
        # Phase 2: Jira bulk fetch (1 paginated API call)
        # ====================================================================
        raw_jira_users = await self._fetch_all_jira_users_bulk()

        all_active_account_ids: set[str] = set()
        visible_email_map: dict[str, str] = {}  # email.lower() -> accountId
        account_id_to_display: dict[str, str] = {}

        for user in raw_jira_users:
            account_type = user.get("accountType", "")
            if account_type != "atlassian":
                continue
            if not user.get("active", True):
                continue

            account_id = user.get("accountId")
            if not account_id:
                continue

            all_active_account_ids.add(account_id)
            account_id_to_display[account_id] = user.get("displayName", "")

            email = user.get("emailAddress")
            if email:
                visible_email_map[email.lower()] = account_id

        self.logger.info(
            f"👥 Jira bulk: {len(all_active_account_ids)} active atlassian users, "
            f"{len(visible_email_map)} with visible email"
        )

        # ====================================================================
        # Phase 3: Merge into resolved set (in-memory, 0 API calls)
        # ====================================================================
        resolved: dict[str, AppUser] = {}  # accountId -> AppUser

        # 3A: Public-email users from bulk (freshest data)
        for email_lower, account_id in visible_email_map.items():
            resolved[account_id] = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=account_id,
                org_id=self.data_entities_processor.org_id,
                email=email_lower,
                full_name=account_id_to_display.get(account_id, email_lower),
                is_active=True
            )

        # 3B: Valid cached users (prior syncs, still active in Jira)
        for account_id, email in cached_account_id_to_email.items():
            if account_id in all_active_account_ids and account_id not in resolved:
                resolved[account_id] = AppUser(
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_user_id=account_id,
                    org_id=self.data_entities_processor.org_id,
                    email=email,
                    full_name=account_id_to_display.get(account_id, email),
                    is_active=True
                )

        # ====================================================================
        # Phase 4: Determine if reverse lookup is needed
        # ====================================================================
        unresolved_account_ids = all_active_account_ids - set(resolved.keys())
        unresolved_count = len(unresolved_account_ids)

        resolved_emails = {u.email.lower() for u in resolved.values()}
        candidate_emails = pipeshub_emails - resolved_emails
        candidate_count = len(candidate_emails)

        self.logger.info(
            f"👥 Resolution state: {len(resolved)} resolved, "
            f"{unresolved_count} unresolved Jira users, "
            f"{candidate_count} PipesHub candidate emails"
        )

        # Phase 5 — reverse lookup: fill gaps via per-email find_users, which is usually allowed
        # even when bulk enumeration is forbidden (then unresolved_count is 0 yet nobody was resolved).
        if (unresolved_count > 0 or self._user_bulk_forbidden) and candidate_count > 0:
            new_found = await self._resolve_private_email_users(
                candidate_emails, unresolved_account_ids, resolved
            )
            self.logger.info(
                f"👥 Reverse lookup resolved {new_found} additional users"
            )
        elif unresolved_count == 0 and not self._user_bulk_forbidden:
            self.logger.info("👥 All Jira users resolved, no reverse lookup needed")

        self.logger.info(f"👥 Total: {len(resolved)} Jira AppUsers resolved")
        return list(resolved.values())

    async def _fetch_all_jira_users_bulk(self) -> list[dict[str, Any]]:
        """
        Paginated fetch of all Jira users via /rest/api/3/users/search.
        Returns raw user dicts (unfiltered).
        """
        users: list[dict[str, Any]] = []
        start_at = 0
        max_results_per_request = USER_PAGE_SIZE
        self._user_bulk_forbidden = False

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_all_users(
                query='',
                maxResults=max_results_per_request,
                startAt=start_at
            )

            if response.status != HttpStatusCode.OK.value:
                # Both 401/403 fall back to PipesHub reverse lookup; only 403 notifies
                # (missing Browse users and groups). 401 is auth failure, already reported.
                if response.status in (
                    HttpStatusCode.UNAUTHORIZED.value,
                    HttpStatusCode.FORBIDDEN.value,
                ):
                    self._user_bulk_forbidden = True
                    self.logger.warning(
                        "⚠️ /users/search returned %s — user resolution degrades to the "
                        "PipesHub-directory reverse lookup (%s users collected so far)",
                        response.status, len(users),
                    )
                    if response.status == HttpStatusCode.FORBIDDEN.value:
                        await self.notify(
                            type=NotificationType.CONNECTOR_WARNING,
                            severity=NotificationSeverity.WARNING,
                            title=self._notification_title("couldn't list users"),
                            message=(
                                "Couldn't list users from Jira. The connector's Jira account "
                                "needs the Browse users and groups global permission."
                            ),
                            recipient_roles=[NotificationRecipientRole.ADMIN],
                        )
                    return users
                raise Exception(f"Failed to fetch users: {response.text()}")

            users_batch = self._safe_json_parse(response, "users fetch")
            if users_batch is None:
                self.logger.error("Failed to parse users response, stopping user fetch")
                break

            if isinstance(users_batch, list):
                batch_users = users_batch
            else:
                batch_users = users_batch.get("values", [])

            if not batch_users:
                break

            users.extend(batch_users)

            if len(batch_users) < max_results_per_request:
                break

            start_at += max_results_per_request

        return users

    async def _resolve_private_email_users(
        self,
        candidate_emails: set[str],
        unresolved_account_ids: set[str],
        resolved: dict[str, "AppUser"]
    ) -> int:
        """
        Reverse-lookup PipesHub emails against Jira to resolve private-email users.
        Uses bounded concurrency and early termination.
        Returns the number of newly resolved users.
        """
        unresolved_count = len(unresolved_account_ids)
        new_found = 0
        semaphore = asyncio.Semaphore(10)
        datasource = await self._get_fresh_datasource()

        async def try_resolve_email(email: str) -> Optional[tuple[str, str, str]]:
            """Returns (accountId, email, displayName) if found, else None."""
            async with semaphore:
                try:
                    response = await datasource.find_users(query=email, maxResults=50)

                    if response.status != HttpStatusCode.OK.value:
                        return None

                    results = self._safe_json_parse(response, f"find_users({email})")
                    if not results or not isinstance(results, list):
                        return None

                    user = results[0]
                    if not user:
                        return None
                    account_id = user.get("accountId")
                    if not account_id:
                        return None
                    display_name = user.get("displayName") or email
                    return (account_id, email, display_name)
                except Exception as e:
                    self.logger.debug(f"⚠️ Reverse lookup failed for {email}: {e}")
                    return None

        # Process in batches to allow early termination
        batch_size = 20
        email_list = list(candidate_emails)
        # Bulk-forbidden leaves unresolved_count at 0; skip early-exit so we
        # still walk candidates and resolve via the per-email endpoint.
        skip_early_exit = self._user_bulk_forbidden

        for i in range(0, len(email_list), batch_size):
            if not skip_early_exit and new_found >= unresolved_count:
                break

            batch = email_list[i:i + batch_size]
            tasks = [try_resolve_email(email) for email in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception) or result is None:
                    continue
                account_id, email, display_name = result
                if account_id not in resolved:
                    resolved[account_id] = AppUser(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_id=account_id,
                        org_id=self.data_entities_processor.org_id,
                        email=email,
                        full_name=display_name,
                        is_active=True
                    )
                    new_found += 1

            if not skip_early_exit and new_found >= unresolved_count:
                break

        return new_found

    async def _fetch_application_roles_to_groups_mapping(self) -> dict[str, list[dict[str, str]]]:
        """
        Fetch all application roles and their associated groups.
        Always fetches fresh data from the API so that group membership
        changes in Jira are picked up on every sync.
        """
        mapping: dict[str, list[dict[str, str]]] = {}
        self._app_roles_forbidden = False

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_all_application_roles()

            if response.status != HttpStatusCode.OK.value:
                if response.status == HttpStatusCode.FORBIDDEN.value:
                    self._app_roles_forbidden = True
                    self.logger.warning(
                        "⚠️ Application roles API returned 403 — configuring user is not a Jira admin. "
                        "Projects whose permission scheme uses applicationRole holders will "
                        "grant the configuring user direct access instead."
                    )
                    await self.notify(
                        type=NotificationType.CONNECTOR_WARNING,
                        severity=NotificationSeverity.WARNING,
                        title=self._notification_title("is missing the admin permission for application roles"),
                        message=(
                            "The connector's Jira account doesn't have Jira admin access, "
                            "so some users may not see all the Jira issues they can access "
                            "in Jira. Ask a Jira admin to grant it."
                        ),
                        recipient_roles=[NotificationRecipientRole.ADMIN],
                        payload={
                            "redirect_link": None
                        }
                    )
                else:
                    self.logger.warning(
                        "⚠️ Failed to fetch application roles (HTTP %s)", response.status
                    )
                return {}

            roles_data = response.json()

            for role in roles_data:
                role_key = role.get("key")
                group_details = role.get("groupDetails", [])

                if role_key and group_details:
                    mapping[role_key] = [
                        {"groupId": g.get("groupId"), "name": g.get("name")}
                        for g in group_details
                        if g.get("groupId")
                    ]
                    self.logger.debug(f"ApplicationRole '{role_key}' → {len(mapping[role_key])} groups")

            self.logger.info(f"🔐 Fetched {len(mapping)} application roles with group mappings")

        except Exception as e:
            self.logger.error(f"❌ Error fetching application roles: {e}", exc_info=True)

        return mapping

    async def _fallback_permissions_for_forbidden_scheme(
        self,
        project_key: str,
        status: int,
        stage: str,
    ) -> list[Permission]:
        """Build a single-user BROWSE permission for the configuring user when
        the permission-scheme endpoints return 403 for this project (the account
        isn't a project admin). 401/transient failures return None from the
        scheme fetch; the caller syncs the RecordGroup with an empty ACL.

        Mirrors the ``_app_roles_forbidden`` fallback in
        ``_fetch_application_roles_to_groups_mapping``: rather than indexing
        the project with no ACLs (which would silently hide it from search
        results across the org), give the configuring user direct READ access
        so they can still discover their own data.
        """
        if self.creator_email:
            self.logger.warning(
                "⚠️ %s for %s returned %s — configuring user lacks Administer "
                "Projects. Granting configuring user '%s' direct BROWSE access "
                "instead of dropping all ACLs for this project.",
                stage, project_key, status, self.creator_email,
            )
            jira_email = self._authenticated_jira_email
            if jira_email:
                notify_message = (
                    f"The connector's Jira account ({jira_email}) can't read the permission scheme "
                    f"for {project_key}. Grant it project admin on {project_key}; until then, only the "
                    "connector owner can access this project's issues in PipesHub."
                )
            else:
                notify_message = (
                    f"The connector's Jira account can't read the permission scheme for {project_key}. "
                    "Grant it project admin access; until then, only the connector owner can access "
                    "this project's issues in PipesHub."
                )
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title(f"couldn't read permissions for project {project_key}"),
                message=notify_message,
                payload={
                    "redirect_link": f"{self.site_url}/plugins/servlet/project-config/{project_key}/permissions",
                },
                recipient_roles=[NotificationRecipientRole.ADMIN],
            )
            return [Permission(
                entity_type=EntityType.USER,
                email=self.creator_email,
                type=PermissionType.READ,
            )]

        self.logger.warning(
            "⚠️ %s for %s returned %s and no configuring user email resolved — "
            "project will be indexed with no BROWSE permissions.",
            stage, project_key, status,
        )
        return []

    async def _fetch_project_permission_scheme(
        self,
        project_key: str,
        app_roles_mapping: dict[str, list[dict[str, str]]] = None,
        user_by_account_id: dict[str, "AppUser"] = None
    ) -> Optional[list[Permission]]:
        """
        Fetch permission holders for a project from its Permission Scheme.

        Permission Schemes grant permissions (like BROWSE_PROJECTS) through different holder types:
        - group: Direct group permissions (e.g., "jira-software-users")
        - applicationRole: Product access (e.g., "jira-software") - resolved to associated groups
        - user: Individual user permissions (by accountId/email)
        - anyone: All authenticated users (org-level access)
        - projectRole: Project-specific roles (e.g., "Administrators", "Developers") inside that user or groups in role
        - projectLead: The project's designated lead user
        - sd.customer.portal.only: JSM portal customers (external users)
        - groupCustomField/userCustomField: Dynamic permissions based on issue fields

        Returns the BROWSE holders, or ``None`` when the scheme couldn't be determined due to a
        transient failure (429 after retries / 5xx / parse error). The caller treats ``None`` as
        an empty permission list and still syncs the RecordGroup. An empty list also means the
        scheme was read and legitimately grants BROWSE to no one.
        """
        permissions: list[Permission] = []

        try:
            # Step 1: Get the permission scheme assigned to this project (transport + 429 retry)
            scheme_response = await self._call_with_retry(
                lambda ds: ds.get_assigned_permission_scheme(projectKeyOrId=project_key, expand="all"),
                ctx=f"permission scheme for {project_key}",
            )

            if scheme_response.status != HttpStatusCode.OK.value:
                # Only a 403 is a genuine permission problem (the account isn't a project admin) →
                # grant the creator direct BROWSE so the project isn't hidden, and notify. A 401
                # (auth/token) or a 5xx/429 is transient — return None; caller still syncs the
                # RecordGroup with an empty ACL this run.
                if scheme_response.status == HttpStatusCode.FORBIDDEN.value:
                    return await self._fallback_permissions_for_forbidden_scheme(
                        project_key=project_key,
                        status=scheme_response.status,
                        stage="permission scheme",
                    )
                self.logger.warning(
                    f"⚠️ Could not fetch permission scheme for {project_key} "
                    f"(HTTP {scheme_response.status}); returning None so caller syncs with empty ACL"
                )
                return None

            scheme_data = scheme_response.json()
            scheme_id = scheme_data.get("id")

            # Step 2: Get all permission grants in this scheme (transport + 429 retry)
            grants_response = await self._call_with_retry(
                lambda ds: ds.get_permission_scheme_grants(schemeId=scheme_id, expand="all"),
                ctx=f"permission grants (scheme {scheme_id}) for {project_key}",
            )

            if grants_response.status != HttpStatusCode.OK.value:
                # Same rule as the scheme fetch above: 403 → creator-browse fallback + notify;
                # 401/5xx/429 → None; caller syncs RecordGroup with empty ACL.
                if grants_response.status == HttpStatusCode.FORBIDDEN.value:
                    return await self._fallback_permissions_for_forbidden_scheme(
                        project_key=project_key,
                        status=grants_response.status,
                        stage=f"permission grants (scheme {scheme_id})",
                    )
                self.logger.warning(
                    f"⚠️ Could not fetch permission grants for scheme {scheme_id} "
                    f"({project_key}, HTTP {grants_response.status}); "
                    "returning None so caller syncs with empty ACL"
                )
                return None

            grants_data = grants_response.json()
            permission_grants = grants_data.get("permissions", [])

            # Step 3: Resolve each BROWSE_PROJECTS grant to holders. Isolate per-grant so a
            # single malformed grant can't collapse the whole project's ACL to empty.
            seen_holders: set[str] = set()

            for grant in permission_grants:
                try:
                    permissions.extend(
                        self._permissions_for_browse_grant(
                            grant, project_key, app_roles_mapping or {}, user_by_account_id or {}, seen_holders
                        )
                    )
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ {project_key}: skipping a malformed permission grant: {e}", exc_info=True
                    )
                    continue

            return permissions

        except Exception as e:
            # Couldn't determine the scheme (transport exhaustion / parse error): caller syncs
            # the RecordGroup with empty permissions.
            self.logger.error(f"❌ Error fetching permission scheme for project {project_key}: {e}", exc_info=True)
            return None

    def _permissions_for_browse_grant(
        self,
        grant: dict[str, Any],
        project_key: str,
        app_roles_mapping: dict[str, list[dict[str, str]]],
        user_by_account_id: dict[str, "AppUser"],
        seen_holders: set,
    ) -> list[Permission]:
        """Resolve a single permission-scheme grant to the BROWSE Permission(s) it implies.

        Only BROWSE_PROJECTS grants produce permissions; anything else returns []. Mutates
        ``seen_holders`` for cross-grant dedup. Kept isolated (per-grant try/except in the
        caller) so one malformed grant can't drop the whole project's ACL.
        """
        grant_permissions: list[Permission] = []

        if grant.get("permission") != "BROWSE_PROJECTS":
            return grant_permissions

        holder = grant.get("holder", {}) or {}
        holder_type = holder.get("type")
        holder_param = holder.get("parameter")
        holder_value = holder.get("value")

        # Dedup identical holders across grants
        holder_key = f"{holder_type}:{holder_value or holder_param}"
        if holder_key in seen_holders:
            return grant_permissions
        seen_holders.add(holder_key)

        if holder_type == "group" and holder_value:
            grant_permissions.append(Permission(
                entity_type=EntityType.GROUP,
                external_id=holder_value,
                type=PermissionType.READ
            ))

        elif holder_type == "applicationRole":
            role_key = holder_param

            if role_key and app_roles_mapping and role_key in app_roles_mapping:
                for group_info in app_roles_mapping[role_key]:
                    group_id = group_info.get("groupId")
                    if group_id:
                        group_key = f"group:{group_id}"
                        if group_key not in seen_holders:
                            seen_holders.add(group_key)
                            grant_permissions.append(Permission(
                                entity_type=EntityType.GROUP,
                                external_id=group_id,
                                type=PermissionType.READ
                            ))
            elif not role_key:
                # Bare applicationRole (no parameter) = "any licensed user"
                grant_permissions.append(Permission(
                    entity_type=EntityType.ORG,
                    external_id="all_licensed_users",
                    type=PermissionType.READ
                ))
            elif self._app_roles_forbidden and self.creator_email:
                # API returned 403 — can't resolve role to groups; grant only the
                # configuring user instead of over-granting to ORG
                user_key = f"user:{self.creator_email.lower()}"
                if user_key not in seen_holders:
                    seen_holders.add(user_key)
                    grant_permissions.append(Permission(
                        entity_type=EntityType.USER,
                        email=self.creator_email,
                        type=PermissionType.READ,
                    ))
                    self.logger.info(
                        "applicationRole '%s' unresolvable (403) — granting configuring user '%s' direct access on %s",
                        role_key, self.creator_email, project_key
                    )
            else:
                self.logger.warning(
                    "Cannot resolve applicationRole '%s' for project %s — skipping",
                    role_key, project_key
                )

        elif holder_type == "user" and holder_param:
            # holder_param is the accountId; resolve via AppUser map first, fall back to email
            user_data = holder.get("user") or {}
            user_email = user_data.get("emailAddress")

            resolved_email = None
            if user_by_account_id and holder_param in user_by_account_id:
                resolved_email = user_by_account_id[holder_param].email
            elif user_email:
                resolved_email = user_email

            if resolved_email:
                grant_permissions.append(Permission(
                    entity_type=EntityType.USER,
                    email=resolved_email,
                    type=PermissionType.READ
                ))
            else:
                self.logger.debug(f"  {project_key}: User permission skipped - cannot resolve accountId '{holder_param}'")

        elif holder_type == "anyone":
            # All authenticated users have access — handle public condition
            grant_permissions.append(Permission(
                entity_type=EntityType.ORG,
                external_id="anyone_authenticated",
                type=PermissionType.READ
            ))

        elif holder_type == "projectRole":
            project_role = holder.get("projectRole", {}) or {}
            role_name = project_role.get("name", f"Role_{holder_param}")
            role_id = holder_param or project_role.get("id")

            if role_name == "atlassian-addons-project-access":
                return grant_permissions

            grant_permissions.append(Permission(
                entity_type=EntityType.ROLE,
                external_id=f"{project_key}_{role_id}",
                type=PermissionType.READ
            ))

        elif holder_type == "sd.customer.portal.only":
            # JSM Service Desk customers (portal access) — external, not synced
            self.logger.debug(f"  {project_key}: Skipping JSM portal customers (external users, not synced)")

        elif holder_type == "projectLead":
            grant_permissions.append(Permission(
                entity_type=EntityType.ROLE,
                external_id=f"{project_key}_projectLead",
                type=PermissionType.READ
            ))

        elif holder_type in ("groupCustomField", "userCustomField"):
            return grant_permissions

        else:
            self.logger.warning(f"⚠️  {project_key}: Unknown holder type '{holder_type}' with param '{holder_param}' - skipping")

        return grant_permissions

    async def _notify_group_sync_failed(self) -> None:
        await self.notify(
            type=NotificationType.CONNECTOR_GROUP_SYNC_ERROR,
            severity=NotificationSeverity.WARNING,
            title=self._notification_title("couldn't sync user groups"),
            message=(
                "Couldn't load groups from Jira. The connector's Jira account needs "
                "the Browse users and groups global permission."
            ),
            recipient_roles=[NotificationRecipientRole.ADMIN],
        )

    async def _map_bounded(
        self,
        items: list[Any],
        worker: Callable[[Any], Awaitable[Any]],
        concurrency: int = METADATA_FETCH_CONCURRENCY,
    ) -> list[Any]:
        """Run ``worker`` over ``items`` with bounded concurrency, preserving input order.

        Read-only fan-out helper: use only where the per-item work merely *reads* (an API fetch)
        and the single write is batched by the caller afterwards — so there is no write concurrency
        and no shared-state race. Each worker owns its error handling and must not raise (a raise
        aborts the fan-out); results come back in ``items`` order.

        Runs a fixed pool of ``concurrency`` consumer coroutines pulling from a shared iterator, so
        only ``concurrency`` coroutines are ever live even for thousands of items (vs one task per
        item up front), while keeping full pipelining — a consumer grabs the next item the moment it
        frees up.
        """
        if not items:
            return []

        results: list[Any] = [None] * len(items)
        work = iter(enumerate(items))

        async def _consumer() -> None:
            # next() on the shared iterator is atomic under the single-threaded event loop, so no
            # two consumers ever pull the same item.
            for idx, item in work:
                results[idx] = await worker(item)

        pool_size = min(max(1, concurrency), len(items))
        await asyncio.gather(*[_consumer() for _ in range(pool_size)])
        return results

    async def _process_group(
        self,
        group: dict[str, Any],
        user_by_account_id: dict[str, "AppUser"],
    ) -> Optional[tuple[str, str, AppUserGroup, list["AppUser"]]]:
        """Build an AppUserGroup and resolve its members.

        Returns ``(group_id, group_name, user_group, app_users)`` or ``None``
        when the group should be skipped.
        """
        try:
            group_id = group.get("groupId")
            group_name = group.get("name")

            if not group_id or not group_name:
                return None

            if group_name.startswith("atlassian-addons"):
                self.logger.debug("Skipping system group: %s", group_name)
                return None

            self.logger.debug(f"Processing group: {group_name} ({group_id})")

            user_group = AppUserGroup(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_group_id=group_id,
                name=group_name,
                org_id=self.data_entities_processor.org_id,
                description=f"Jira user group: {group_name}",
            )

            member_account_ids, members_ok = await self._fetch_group_members(group_id, group_name)
            # Transient membership failure → still sync the group (empty members) so it is
            # not dropped this run. Next successful membership fetch refreshes members.
            if not members_ok:
                self.logger.warning(
                    f"⚠️ Membership unavailable for group {group_name}; "
                    "syncing group with empty members this run"
                )
                member_account_ids = []

            app_users: list[AppUser] = []
            skipped_members = 0
            if member_account_ids:
                for account_id in member_account_ids:
                    user = user_by_account_id.get(account_id)
                    if user:
                        app_users.append(user)
                    else:
                        skipped_members += 1

            if skipped_members:
                self.logger.debug(
                    "Group %s: %s member(s) skipped (no AppUser; private email or not in PipesHub)",
                    group_name,
                    skipped_members,
                )

            if app_users:
                self.logger.debug(f"Group {group_name}: {len(app_users)} members")
            else:
                self.logger.debug(f"Group {group_name}: no resolved members")

            return (group_id, group_name, user_group, app_users)

        except Exception as group_error:
            self.logger.error(f"❌ Failed to process group {group.get('name')}: {group_error}")
            return None

    async def _sync_user_groups(self, jira_users: list[AppUser]) -> dict[str, list[AppUser]]:
        """
        Sync user groups and return a mapping of group_id/name -> list of AppUser members.
        This mapping is used to resolve group members for project roles.
        """
        try:
            self.logger.info("🚀 Starting Jira user group synchronization")

            groups, fetch_failed = await self._fetch_groups()
            if fetch_failed and not groups:
                # Notify about a missing permission only on a genuine 403; a 401 auth/token
                # failure is already reported by the connection/sync-failed notification.
                if self._group_bulk_forbidden:
                    await self._notify_group_sync_failed()
                return {}
            if not groups:
                self.logger.info("ℹ️ No groups found in Jira")
                return {}

            self.logger.info(f"👥 Found {len(groups)} groups. Fetching members...")

            # Create accountId -> AppUser lookup (accountId is always present in group member responses)
            user_by_account_id = {user.source_user_id: user for user in jira_users if user.source_user_id}

            # Fetch members concurrently (read-only); assemble + single write below.
            results = await self._map_bounded(
                groups, lambda g: self._process_group(g, user_by_account_id)
            )

            user_groups_batch = []
            # Mapping: group_id -> members, group_name -> members (for role actor lookup)
            groups_members_map: dict[str, list[AppUser]] = {}

            for res in results:
                if res is None:
                    continue
                group_id, group_name, user_group, app_users = res
                # Store mapping by both group_id and group_name for flexible lookup
                groups_members_map[group_id] = app_users
                groups_members_map[group_name] = app_users
                # Add group to batch (with or without members)
                user_groups_batch.append((user_group, app_users))

            # Save all groups in one batch
            if user_groups_batch:
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)
            else:
                self.logger.info("ℹ️ No groups with valid members to sync")

            return groups_members_map

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}")
            await self._notify_group_sync_failed()
            return {}

    async def _fetch_groups(self) -> tuple[list[dict[str, Any]], bool]:
        """
        Fetch all Jira groups using the bulk_get_groups API.

        Returns:
            (groups, fetch_failed) — fetch_failed is True when the groups API failed
            before any groups were collected (permission/API error), not when Jira
            simply has an empty group list.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        groups: list[dict[str, Any]] = []
        fetch_failed = False
        # Only a 403 means the account lacks Browse users and groups; a 401 is an auth/token
        # failure reported by the connection/sync-failed notification. Track it so the caller
        # notifies about a missing permission only for a genuine 403.
        self._group_bulk_forbidden = False
        start_at = 0
        max_results = GROUP_PAGE_SIZE

        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.bulk_get_groups(
                    startAt=start_at,
                    maxResults=max_results
                )

                if response.status != HttpStatusCode.OK.value:
                    self.logger.error(f"Failed to fetch groups: {response.text()}")
                    if not groups:
                        fetch_failed = True
                        self._group_bulk_forbidden = (
                            response.status == HttpStatusCode.FORBIDDEN.value
                        )
                    break

                groups_data = response.json()
                batch_groups = groups_data.get("values", [])

                if not batch_groups:
                    break

                groups.extend(batch_groups)

                # Check pagination
                is_last = groups_data.get("isLast", False)
                if is_last:
                    break

                start_at += len(batch_groups)

                # Also break if we got less than requested (safety check)
                if len(batch_groups) < max_results:
                    break

            except Exception as e:
                self.logger.error(f"❌ Error fetching groups at offset {start_at}: {e}")
                if not groups:
                    fetch_failed = True
                break

        self.logger.info(f"👥 Fetched {len(groups)} total groups")
        return groups, fetch_failed

    async def _fetch_group_members(self, group_id: str, group_name: str) -> tuple[list[str], bool]:
        """
        Fetch all members of a Jira group.

        Returns ``(account_ids, ok)``. ``ok`` is False when a page couldn't be read (429 after
        retries / 5xx / transport); the caller still syncs the group with empty members rather
        than dropping it. ``ok`` is True on a clean read even if the group genuinely has no members.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        member_account_ids: list[str] = []
        start_at = 0
        max_results = GROUP_MEMBER_PAGE_SIZE
        ok = True

        while True:
            try:
                response = await self._call_with_retry(
                    lambda ds: ds.get_users_from_group(
                        groupname=group_name,
                        includeInactiveUsers=False,
                        startAt=start_at,
                        maxResults=max_results,
                    ),
                    ctx=f"members of group {group_name}",
                )

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(f"⚠️ Failed to fetch members for group {group_name}: HTTP {response.status}")
                    ok = False
                    break

                members_data = response.json()
                batch_members = members_data.get("values", [])

                if not batch_members:
                    break

                for member in batch_members:
                    account_id = member.get("accountId")
                    if account_id:
                        member_account_ids.append(account_id)

                # Check pagination
                is_last = members_data.get("isLast", False)
                if is_last:
                    break

                start_at += len(batch_members)

                if len(batch_members) < max_results:
                    break

            except Exception as e:
                self.logger.error(f"❌ Error fetching members for group {group_name}: {e}")
                ok = False
                break

        return member_account_ids, ok

    async def _fetch_project_roles(
        self,
        project_key: str,
        user_by_email: dict[str, "AppUser"],
        user_by_account_id: dict[str, "AppUser"],
        groups_members_map: dict[str, list["AppUser"]],
    ) -> tuple[str, list[tuple[AppRole, list["AppUser"]]], bool]:
        """Fetch a project's roles + actors and build ``(AppRole, members)`` tuples.

        Returns ``(project_key, roles, failed)``.
        """
        project_roles: list[tuple[AppRole, list[AppUser]]] = []
        try:
            response = await self._call_with_retry(
                lambda ds: ds.get_project_roles(projectIdOrKey=project_key),
                ctx=f"roles for project {project_key}",
            )

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch roles for project {project_key}: HTTP {response.status}")
                return project_key, project_roles, True

            roles_dict = response.json()

            if not roles_dict:
                self.logger.debug(f"No roles found for project {project_key}")
                return project_key, project_roles, False

            for role_name, role_url in roles_dict.items():
                try:
                    if role_name == "atlassian-addons-project-access":
                        continue

                    role_id = role_url.rstrip('/').split('/')[-1]

                    role_response = await self._call_with_retry(
                        lambda ds: ds.get_project_role(
                            projectIdOrKey=project_key,
                            id=int(role_id),
                            excludeInactiveUsers=True,
                        ),
                        ctx=f"role {role_name} for project {project_key}",
                    )

                    if role_response.status != HttpStatusCode.OK.value:
                        self.logger.warning(f"  {project_key}: Failed to fetch role {role_name}: {role_response.status}")
                        continue

                    role_data = role_response.json()
                    actors = role_data.get("actors", [])
                    role_name_display = role_data.get("name", role_name)

                    app_role = AppRole(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_role_id=f"{project_key}_{role_id}",
                        name=f"{project_key} - {role_name_display}",
                        org_id=self.data_entities_processor.org_id,
                        source_created_at=None,
                        source_updated_at=None,
                    )

                    member_users: list[AppUser] = []

                    for actor in actors:
                        actor_type = actor.get("type", "")

                        if actor_type == "atlassian-user-role-actor":
                            actor_user = actor.get("actorUser", {})
                            account_id = actor_user.get("accountId")
                            email = actor_user.get("emailAddress")

                            user = None
                            if account_id:
                                user = user_by_account_id.get(account_id)
                            if not user and email:
                                user = user_by_email.get(email.lower())

                            if user:
                                member_users.append(user)
                            else:
                                self.logger.debug(
                                    f"  {project_key}/{role_name}: User not found - "
                                    f"accountId={account_id}, email={email}"
                                )

                        elif actor_type == "atlassian-group-role-actor":
                            group_name = actor.get("name") or actor.get("displayName")
                            group_id = actor.get("groupId")

                            group_members = []
                            if group_id and group_id in groups_members_map:
                                group_members = groups_members_map[group_id]
                                self.logger.debug(
                                    f"  {project_key}/{role_name}: Group actor '{group_name}' (id: {group_id}) "
                                    f"found {len(group_members)} members"
                                )
                            elif group_name and group_name in groups_members_map:
                                group_members = groups_members_map[group_name]
                                self.logger.debug(
                                    f"  {project_key}/{role_name}: Group actor '{group_name}' "
                                    f"found {len(group_members)} members"
                                )
                            else:
                                self.logger.debug(
                                    f"  {project_key}/{role_name}: Group actor '{group_name}' "
                                    f"(id: {group_id}) not found in synced groups"
                                )

                            member_users.extend(group_members)

                    project_roles.append((app_role, member_users))

                except Exception as role_error:
                    self.logger.error(
                        f"  {project_key}: Error processing role {role_name}: {role_error}"
                    )
                    continue

            return project_key, project_roles, False

        except Exception as project_error:
            self.logger.error(f"❌ Error syncing roles for project {project_key}: {project_error}")
            return project_key, project_roles, True

    async def _sync_project_roles(
        self,
        project_keys: list[str],
        jira_users: list[AppUser],
        groups_members_map: dict[str, list[AppUser]] = None
    ) -> None:
        """
        Sync project roles as AppRole entities.
        groups_members_map: Mapping of group_id/name -> list of AppUser members (from _sync_user_groups)
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        if groups_members_map is None:
            groups_members_map = {}

        self.logger.info(f"🔐 Syncing project roles for {len(project_keys)} projects...")

        # Build email -> AppUser lookup for fast member resolution
        user_by_email: dict[str, AppUser] = {
            user.email.lower(): user
            for user in jira_users
            if user.email
        }

        # Also build accountId -> AppUser lookup
        user_by_account_id: dict[str, AppUser] = {
            user.source_user_id: user
            for user in jira_users
            if user.source_user_id
        }

        roles_to_sync: list[tuple[AppRole, list[AppUser]]] = []
        total_roles = 0
        total_members = 0
        failed_project_keys: list[str] = []

        # Fetch each project's roles concurrently (read-only); batch the single write below.
        role_fetch_results = await self._map_bounded(
            project_keys,
            lambda pk: self._fetch_project_roles(
                pk, user_by_email, user_by_account_id, groups_members_map
            ),
        )
        for project_key, project_roles, failed in role_fetch_results:
            if failed:
                failed_project_keys.append(project_key)
            roles_to_sync.extend(project_roles)
            total_roles += len(project_roles)
            total_members += sum(len(members) for _, members in project_roles)

        if failed_project_keys:
            preview = ", ".join(failed_project_keys[:10])
            if len(failed_project_keys) > 10:
                preview = f"{preview}, and {len(failed_project_keys) - 10} more"
            self.logger.error(
                "❌ Project role sync failed for %s/%s projects: %s",
                len(failed_project_keys), len(project_keys), preview,
            )
            await self.notify(
                type=NotificationType.CONNECTOR_ROLE_SYNC_ERROR,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("couldn't sync project roles"),
                message=(
                    f"Couldn't sync roles for: {preview}. "
                    "This is usually because the connector's Jira account lacks Administer Projects "
                    "on those projects, but can also be temporary rate limiting — existing roles are "
                    "preserved and will retry next sync."
                ),
                recipient_roles=[NotificationRecipientRole.ADMIN],
            )

        # Step 4: Sync all roles in batch
        if roles_to_sync:
            await self.data_entities_processor.on_new_app_roles(roles_to_sync)
            self.logger.info(
                f"✅ Synced {total_roles} project roles with {total_members} direct user members"
            )
        else:
            self.logger.info("ℹ️ No project roles to sync")

    async def _sync_project_lead_roles(
        self,
        raw_projects: list[dict[str, Any]],
        jira_users: list[AppUser]
    ) -> None:
        """
        Sync project lead as AppRole for each project.
        """

        # Build accountId -> AppUser lookup
        user_by_account_id: dict[str, AppUser] = {
            user.source_user_id: user
            for user in jira_users
            if user.source_user_id
        }

        lead_roles_to_sync: list[tuple[AppRole, list[AppUser]]] = []
        total_leads = 0

        # Iterate through raw project data already fetched with lead
        for project in raw_projects:
            try:
                project_key = project.get("key")
                lead_data = project.get("lead")

                # Create AppRole for project lead (even if no lead exists - to clean up old edges)
                # Extract project timestamps if available
                project_created = project.get("createdAt")
                project_updated = project.get("updatedAt")

                app_role = AppRole(
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_role_id=f"{project_key}_projectLead",
                    name=f"{project_key} - Project Lead",
                    org_id=self.data_entities_processor.org_id,
                    source_created_at=self._parse_jira_timestamp(project_created) if project_created else None,
                    source_updated_at=self._parse_jira_timestamp(project_updated) if project_updated else None
                )

                # Determine lead user (if any)
                lead_user = None
                if lead_data:
                    lead_account_id = lead_data.get("accountId")
                    lead_display_name = lead_data.get("displayName", "Unknown")

                    if lead_account_id:
                        # Find the lead user in synced users
                        lead_user = user_by_account_id.get(lead_account_id)

                        if not lead_user:
                            self.logger.warning(f"Project lead {lead_display_name} not found in synced users for {project_key}")
                    else:
                        self.logger.warning(f"No accountId for project lead in {project_key}")
                else:
                    self.logger.debug(f"No lead for project {project_key} - syncing role to clean up old edges")

                # Always sync the role (even with empty members list) to ensure old edges are deleted
                members = [lead_user] if lead_user else []
                lead_roles_to_sync.append((app_role, members))
                total_leads += 1


            except Exception as lead_error:
                self.logger.error(f"Error processing lead for project {project.get('key')}: {lead_error}")
                continue

        # Sync all project lead roles in batch
        if lead_roles_to_sync:
            await self.data_entities_processor.on_new_app_roles(lead_roles_to_sync)
        else:
            self.logger.info("No project leads to sync")

    # ============================================================================
    # Project Management
    # ============================================================================

    async def _list_projects_with_filter(
        self,
        project_keys: Optional[list[str]] = None,
        project_keys_operator: Optional[FilterOperatorType] = None,
    ) -> list[dict[str, Any]]:
        """Paginate through ``search_projects`` and apply the project-key filter.

        Returns the raw project dicts as Jira sends them (``id``, ``key``,
        ``name``, ``description``, ``url`` …) — permission resolution / record
        group construction is the caller's responsibility.

        Filter semantics:
          * ``project_keys=None`` or ``[]``: fetch every visible project.
          * ``project_keys=[…]`` with ``IN`` (default): use the server-side
            ``keys=`` filter so we only round-trip the matching pages.
          * ``project_keys=[…]`` with ``NOT_IN``: server has no exclusion
            filter, so we fetch everything and exclude client-side.

        Extracted from ``_fetch_projects`` so the personal-scope subclass can
        reuse the listing without inheriting the application-role and
        permission-scheme calls that follow it in the workspace flow.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        is_exclude = bool(project_keys_operator) and (
            (
                project_keys_operator.value
                if hasattr(project_keys_operator, "value")
                else str(project_keys_operator)
            )
            == "not_in"
        )

        if project_keys and not is_exclude:
            self.logger.info(f"📁 Fetching specific projects using keys filter: {project_keys}")
            return await self._paginate_project_search(keys=project_keys)

        if project_keys and is_exclude:
            self.logger.info(f"📁 Fetching all projects, excluding: {project_keys}")
            excluded_keys_set = set(project_keys)
            return [
                project
                for project in await self._paginate_project_search()
                if project.get("key") and project.get("key") not in excluded_keys_set
            ]

        self.logger.info("📁 Fetching all projects")
        return await self._paginate_project_search()

    async def _paginate_project_search(
        self, keys: Optional[list[str]] = None
    ) -> list[dict[str, Any]]:
        """Page through ``search_projects``, returning every project (optionally
        server-side filtered to ``keys``). Single loop shared by all filter modes.
        """
        projects: list[dict[str, Any]] = []
        start_at = 0

        while True:
            search_kwargs: dict[str, Any] = {
                "maxResults": DEFAULT_MAX_RESULTS,
                "startAt": start_at,
                "expand": ["description", "url", "lead"],
            }
            if keys:
                search_kwargs["keys"] = keys
            # Retry transport errors + 429 (honoring Retry-After), same as issue search.
            response = await self._call_with_retry(
                lambda ds: ds.search_projects(**search_kwargs),
                ctx="fetching projects",
            )

            if response.status != HttpStatusCode.OK.value:
                raise Exception(f"Failed to fetch projects: {response.text()}")

            projects_batch = self._safe_json_parse(response, "project search")
            if projects_batch is None:
                raise Exception("Failed to parse project search response")
            batch_projects = projects_batch.get("values", [])

            if not batch_projects:
                break

            projects.extend(batch_projects)
            start_at += len(batch_projects)

            total = projects_batch.get("total", 0)
            if projects_batch.get("isLast", False) or (total > 0 and start_at >= total):
                break

        return projects

    async def _build_project_record_group(
        self,
        project: dict[str, Any],
        app_roles_mapping: dict[str, Any],
        perm_user_by_account_id: dict[str, "AppUser"],
    ) -> Optional[tuple[RecordGroup, list[Permission]]]:
        """Build a project's RecordGroup and fetch its BROWSE permissions."""
        try:
            project_id = project.get("id")
            project_name = project.get("name")
            project_key = project.get("key")

            record_group = RecordGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                external_group_id=project_id,
                connector_id=self.connector_id,
                connector_name=self.connector_name,
                name=project_name,
                short_name=project_key,
                group_type=RecordGroupType.PROJECT,
                web_url=project.get("url"),
            )

            project_permissions = await self._fetch_project_permission_scheme(
                project_key, app_roles_mapping, perm_user_by_account_id
            )

            # Transient scheme failure returns None — still sync the RecordGroup so the
            # project/issues are not dropped this run. Empty ACL; next successful scheme
            # fetch will refresh permissions.
            if project_permissions is None:
                self.logger.warning(
                    f"⚠️ Permission scheme unavailable for {project_key}; "
                    "syncing project with empty permissions this run"
                )
                project_permissions = []

            if project_permissions:
                self.logger.info(f"🔐 Project {project_key}: {len(project_permissions)} permission grants from scheme")

            return (record_group, project_permissions)

        except Exception as e:
            self.logger.error(
                f"❌ Failed to build record group for project {project.get('key')}: {e}", exc_info=True
            )
            return None

    async def _fetch_filtered_projects(
        self,
        jira_users: list["AppUser"],
    ) -> tuple[list[tuple[RecordGroup, list[Permission]]], list[dict[str, Any]]]:
        """Resolve the project-keys sync filter, then fetch matching projects."""
        allowed_keys = None
        project_keys_operator = None
        if self.sync_filters:
            project_keys_filter = self.sync_filters.get(SyncFilterKey.PROJECT_KEYS)
            if project_keys_filter:
                allowed_keys = project_keys_filter.get_value(default=[])
                project_keys_operator = project_keys_filter.get_operator()
                if allowed_keys:
                    operator_value = (
                        project_keys_operator.value
                        if hasattr(project_keys_operator, "value")
                        else str(project_keys_operator) if project_keys_operator else "in"
                    )
                    action = "Excluding" if operator_value == "not_in" else "Including"
                    self.logger.info(f"🔍 Project keys filter: {action} projects: {allowed_keys}")
                else:
                    self.logger.info("🔍 Project keys filter is empty, will fetch all projects")
        return await self._fetch_projects(allowed_keys, project_keys_operator, jira_users)

    async def _fetch_projects(
        self,
        project_keys: Optional[list[str]] = None,
        project_keys_operator: Optional[FilterOperatorType] = None,
        jira_users: Optional[list["AppUser"]] = None
    ) -> tuple[list[tuple[RecordGroup, list[Permission]]], list[dict[str, Any]]]:
        """
        Fetch projects using DataSource. Returns (record_groups, raw_projects).

        Args:
            project_keys: Optional list of project keys to include/exclude
            project_keys_operator: Optional filter operator (IN or NOT_IN)
        """
        projects = await self._list_projects_with_filter(
            project_keys, project_keys_operator
        )

        # Fetch application roles → groups mapping once (cached)
        app_roles_mapping = await self._fetch_application_roles_to_groups_mapping()

        # Build accountId -> AppUser lookup for permission scheme resolution
        perm_user_by_account_id: dict[str, AppUser] = {}
        if jira_users:
            perm_user_by_account_id = {
                u.source_user_id: u for u in jira_users if u.source_user_id
            }

        # Fetch each project's permission scheme concurrently (read-only); the single write
        # (on_new_record_groups) is done by the caller after this returns.
        rg_results = await self._map_bounded(
            projects,
            lambda p: self._build_project_record_group(p, app_roles_mapping, perm_user_by_account_id),
        )
        record_groups: list[tuple[RecordGroup, list[Permission]]] = [r for r in rg_results if r is not None]

        # Surface any project skipped this sync (transient scheme failure or a bad RecordGroup) so
        # a persistently-failing project isn't silently and indefinitely excluded. Its existing
        # record group / ACL are preserved (on_new_record_groups only touches listed projects).
        built_keys = {rg.short_name for rg, _ in record_groups}
        skipped_keys = [p.get("key") for p in projects if p.get("key") and p.get("key") not in built_keys]
        if skipped_keys:
            preview = ", ".join(skipped_keys[:10])
            if len(skipped_keys) > 10:
                preview = f"{preview}, and {len(skipped_keys) - 10} more"
            self.logger.warning(
                "⚠️ Skipped %s/%s project(s) this sync (record group/permissions unavailable): %s",
                len(skipped_keys), len(projects), preview,
            )
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=self._notification_title("couldn't sync some projects this run"),
                message=(
                    f"Couldn't refresh {len(skipped_keys)} project(s) this sync ({preview}), likely "
                    "due to Jira rate limiting or a temporary error. Their existing access is "
                    "preserved and they'll retry on the next sync."
                ),
                recipient_roles=[NotificationRecipientRole.ADMIN],
            )

        return record_groups, projects

    # ============================================================================
    # Issue Sync
    # ============================================================================

    async def _sync_all_project_issues(
        self,
        projects: list[tuple[RecordGroup, list[Permission]]],
        jira_users: list[AppUser],
        last_sync_time: Optional[int]
    ) -> dict[str, Any]:
        """Sync issues for all projects and return statistics."""
        total_synced = 0
        new_count = 0
        updated_count = 0
        failed_project_keys: list[str] = []

        for project, _ in projects:
            try:
                project_stats = await self._sync_project_issues(
                    project, jira_users, last_sync_time
                )
                total_synced += project_stats["total_synced"]
                new_count += project_stats["new_count"]
                updated_count += project_stats["updated_count"]
            except Exception as e:
                # Per-project failures self-heal: the project checkpoint only advances on
                # success, so the next sync resumes it. run_sync notifies with the keys.
                failed_project_keys.append(project.short_name)
                self.logger.error(f"❌ Error processing issues for project {project.short_name}: {e}", exc_info=True)
                continue

        return {
            "total_synced": total_synced,
            "new_count": new_count,
            "updated_count": updated_count,
            "failed_count": len(failed_project_keys),
            "failed_project_keys": failed_project_keys,
        }

    async def _sync_project_issues(
        self,
        project: RecordGroup,
        jira_users: list[AppUser],
        global_last_sync_time: Optional[int]
    ) -> dict[str, int]:
        """
        Sync issues for a single project with project-level sync points.
        Processes in batches and updates sync point after each batch for fault tolerance.
        """
        project_key = project.short_name
        project_id = project.external_group_id

        # Read project sync point
        project_sync_data = await self._get_project_sync_checkpoint(project_key)

        # Check if this is a new project (no checkpoint exists)
        is_new_project = not project_sync_data or (
            not project_sync_data.get("last_issue_updated") and
            not project_sync_data.get("last_sync_time")
        )

        # Use last_issue_updated if available (works for both resume and incremental sync)
        # For new projects, don't use any timestamp to fetch ALL issues
        # Fall back to project sync time, then global sync time (only for existing projects)
        resume_from_timestamp = None
        if not is_new_project:
            resume_from_timestamp = project_sync_data.get("last_issue_updated")
            if not resume_from_timestamp:
                resume_from_timestamp = project_sync_data.get("last_sync_time") or global_last_sync_time

        # Set project_last_sync_time for fallback in _fetch_issues_batched
        project_last_sync_time = project_sync_data.get("last_sync_time") or global_last_sync_time if not is_new_project else None

        if is_new_project:
            self.logger.info(f"🆕 New project detected: {project_key}. Fetching ALL issues (no timestamp filter).")
        elif resume_from_timestamp:
            self.logger.info(f"🔄 Starting sync for project {project_key} from timestamp {resume_from_timestamp}")

        # Fetch and process issues in batches
        total_issues_processed = 0
        batch_number = 0
        last_issue_updated_in_batch = None
        stats = {"new_count": 0, "updated_count": 0}

        async for issues_batch, _has_more, last_issue_timestamp in self._fetch_issues_batched(
            project_key,
            project_id,
            jira_users,
            project_last_sync_time,
            resume_from_timestamp,
            is_new_project=is_new_project,
        ):
            batch_number += 1
            batch_size = len(issues_batch)

            # Track last issue updated timestamp for resume capability
            if last_issue_timestamp:
                last_issue_updated_in_batch = last_issue_timestamp

            # Skip processing if no actual changes (all issues filtered out as unchanged)
            if not issues_batch:
                # Update checkpoint for skipped batch to advance timestamp and prevent re-fetch
                # Safe because these issues were already in DB (just unchanged)
                if last_issue_updated_in_batch:
                    current_time = get_epoch_timestamp_in_ms()
                    await self._update_project_sync_checkpoint(
                        project_key,
                        last_sync_time=current_time,
                        last_issue_updated=last_issue_updated_in_batch
                    )
                continue

            self.logger.info(f"📦 Processing batch {batch_number} for project {project_key}: {batch_size} records")

            # Process this batch
            await self._process_new_records(issues_batch, project_key, stats)
            total_issues_processed += batch_size

            # Update checkpoint AFTER successful processing
            if last_issue_updated_in_batch:
                current_time = get_epoch_timestamp_in_ms()
                await self._update_project_sync_checkpoint(
                    project_key,
                    last_sync_time=current_time,
                    last_issue_updated=last_issue_updated_in_batch
                )

        # Final checkpoint update if we processed any issues (ensures last_sync_time stays close to last_issue_updated)
        if last_issue_updated_in_batch:
            current_time = get_epoch_timestamp_in_ms()
            await self._update_project_sync_checkpoint(
                project_key,
                last_sync_time=current_time,
                last_issue_updated=last_issue_updated_in_batch
            )

        if total_issues_processed == 0:
            self.logger.info(f"ℹ️ No new/updated issues for project {project_key}")
        else:
            self.logger.info(f"✅ Synced {total_issues_processed} records for project {project_key}")

        return {
            "total_synced": total_issues_processed,
            "new_count": stats["new_count"],
            "updated_count": stats["updated_count"]
        }

    async def _fetch_issues_batched(
        self,
        project_key: str,
        project_id: str,
        users: list[AppUser],
        last_sync_time: Optional[int] = None,
        resume_from_timestamp: Optional[int] = None,
        is_new_project: bool = False,
    ) -> AsyncGenerator[tuple[list[tuple[Record, list[Permission]]], bool, Optional[int]], None]:
        """
        Fetch issues for a project in batches, yielding processed records.
        Uses timestamp-based pagination for reliable resume capability.

        Yields:
            Tuple of (records_batch, has_more, last_issue_updated)
            - records_batch: List of (Record, permissions) tuples for this batch
            - has_more: True if there are more batches, False if this is the last batch
            - last_issue_updated: Updated timestamp of last issue in this batch (for resume)
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Build JQL query
        jql_conditions = [f'project = "{project_key}"']

        # Get modified filter
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        modified_after = None
        modified_before = None

        if modified_filter:
            modified_after, modified_before = modified_filter.get_value(default=(None, None))

        # Get created filter
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED) if self.sync_filters else None
        created_after = None
        created_before = None

        if created_filter:
            created_after, created_before = created_filter.get_value(default=(None, None))

        # Determine modified_after from filter and/or checkpoint
        # resume_from_timestamp can be from last_issue_updated (resume) or last_sync_time (incremental)
        if resume_from_timestamp:
            # Use checkpoint timestamp (works for both resume and incremental sync)
            modified_after = resume_from_timestamp
            self.logger.info(f"🔄 Starting from timestamp: {resume_from_timestamp}")
        elif modified_after:
            if last_sync_time:
                modified_after = max(modified_after, last_sync_time)
        elif last_sync_time:
            modified_after = last_sync_time

        if modified_after:
            # Always use > to avoid reprocessing the last issue (works for both resume and incremental)
            jql_conditions.append(f'updated > "{self._jql_datetime(modified_after)}"')

        if modified_before:
            jql_conditions.append(f'updated <= "{self._jql_datetime(modified_before)}"')

        if created_after:
            jql_conditions.append(f'created >= "{self._jql_datetime(created_after)}"')

        if created_before:
            jql_conditions.append(f'created <= "{self._jql_datetime(created_before)}"')

        # Build final JQL (ORDER BY required for consistent pagination)
        # Add id ASC as secondary sort for stable ordering when timestamps are equal
        jql = " AND ".join(jql_conditions) + " ORDER BY updated ASC, id ASC"
        self.logger.info(f"🔍 JQL Query for {project_key}: {jql}")

        page_count = 0
        next_page_token = None
        # Track last issue updated timestamp for resume (starts with resume_from_timestamp if resuming)
        last_issue_updated = resume_from_timestamp

        while True:
            page_count += 1

            try:
                response = await self._search_issues_with_retry(
                    project_key=project_key,
                    jql=jql,
                    next_page_token=next_page_token,
                    max_results=ISSUE_PAGE_SIZE,
                    fields=ISSUE_SEARCH_FIELDS,
                    expand="changelog",
                )

                if response.status != HttpStatusCode.OK.value:
                    raise Exception(f"Failed to fetch issues: {response.text()}")

                issues_batch_response = self._safe_json_parse(response, f"issues fetch for {project_key}")
                if issues_batch_response is None:
                    raise Exception(f"Failed to parse issues response for project {project_key}")

            except Exception as e:
                self.logger.error(f"❌ Failed to fetch issues for project {project_key}: {e}")
                raise

            batch_issues = issues_batch_response.get("issues", [])
            new_page_token = issues_batch_response.get("nextPageToken")

            if not batch_issues:
                # No more issues - yield empty to signal completion
                yield [], False, last_issue_updated
                break

            # Get updated timestamp of last issue in this batch (for resume capability)
            if batch_issues:
                last_issue = batch_issues[-1]
                fields = last_issue.get("fields", {})
                updated_str = fields.get("updated")
                if updated_str:
                    last_issue_updated = self._parse_jira_timestamp(updated_str)

            # Build records for this batch (read-only transaction)
            async with self.data_store_provider.transaction() as tx_store:
                records_batch, delete_ids = await self._build_issue_records(
                    batch_issues, project_id, users, tx_store,
                    is_new_project=is_new_project,
                )

            # Hard-delete source-removed attachments (with Qdrant vector cleanup).
            # cascade_children=False is safe: FILE records have no outgoing
            # PARENT_CHILD or ATTACHMENT edges, so only the files themselves are deleted.
            if delete_ids:
                await self.data_entities_processor.on_records_deleted_cascade(
                    delete_ids, self.connector_id, cascade_children=False,
                )

            self.logger.debug(f"📦 Fetched batch {page_count}: {len(batch_issues)} issues -> {len(records_batch)} records (last updated: {last_issue_updated})")

            # Determine if there are more pages
            has_more = new_page_token and new_page_token != next_page_token

            # Yield this batch with resume info
            # But we store last_issue_updated timestamp for resume on next sync
            yield records_batch, has_more, last_issue_updated

            if not has_more:
                break

            # Use token for next page (valid during this sync session)
            next_page_token = new_page_token

    async def _process_new_records(
        self,
        records_with_permissions: list[tuple[Record, list[Permission]]],
        project_name: str,
        stats: dict[str, int]
    ) -> None:
        """
        Process records in batches, routing new and updated records to the
        correct processor method so the indexer receives the right event type.

        New records (version 0) → on_new_records  → "newRecord" event.
        Updated records (version > 0) → on_record_content_update → "updateRecord"
        event, which enables diff-based block reconciliation in the indexer.
        """
        # Sort records: parentless issues (Initiative / Epic roots) before children
        sorted_records = sorted(
            records_with_permissions,
            key=lambda x: (x[0].parent_external_record_id is not None, x[0].parent_external_record_id or "")
        )

        batch_size = BATCH_PROCESSING_SIZE

        for i in range(0, len(sorted_records), batch_size):
            batch = sorted_records[i:i + batch_size]

            new_records = [(r, p) for r, p in batch if r.version == 0]
            updated_records = [r for r, _ in batch if r.version > 0]

            if new_records:
                await self.data_entities_processor.on_new_records(new_records)

            for record in updated_records:
                await self.data_entities_processor.on_record_content_update(record)

            stats["new_count"] += len(new_records)
            stats["updated_count"] += len(updated_records)

            issues_count = sum(1 for r, _ in batch if isinstance(r, TicketRecord))
            files_count = sum(1 for r, _ in batch if isinstance(r, FileRecord))
            self.logger.info(
                f"📦 Batch {i//batch_size + 1}: {issues_count} issues, "
                f"{files_count} attachments ({len(new_records)} new, {len(updated_records)} updated)"
            )

    # ============================================================================
    # Issue Processing
    # ============================================================================

    def _parse_issue_links(self, issue: dict[str, Any]) -> list[RelatedExternalRecord]:
        """
        Parse issue links from Jira API response and convert to RelatedExternalRecord objects.

        Only processes OUTWARD links to avoid creating duplicate edges.
        When Issue A has a link to Issue B:
        - Issue A has outwardIssue: B (we create A → B edge here)
        - Issue B has inwardIssue: A (we skip this to avoid duplicate)

        Relation types mapped:
        - "blocks" → BLOCKS
        - "duplicates" → DUPLICATES
        - "clones" → CLONES
        - "depends on" → DEPENDS_ON
        - "causes" → CAUSES
        - "relates to" → RELATED
        - Unknown types → RELATED (fallback)

        This ensures exactly one edge is created per link relationship.
        """
        related_records: list[RelatedExternalRecord] = []

        # Handle edge case where issue might be None or not a dict
        if not issue or not isinstance(issue, dict):
            return related_records

        fields = issue.get("fields", {})
        if not fields or not isinstance(fields, dict):
            return related_records

        issue_links = fields.get("issuelinks", [])

        # Handle edge case where issuelinks might not be a list
        if not issue_links or not isinstance(issue_links, list):
            return related_records

        for link in issue_links:
            # Skip if link is not a dict
            if not isinstance(link, dict):
                continue
            try:
                # Get link type information
                link_type = link.get("type", {})
                if not link_type:
                    continue

                # Only process outward links to create a single edge per relationship
                # Skip inward links to avoid creating duplicate edges
                if "outwardIssue" not in link:
                    continue

                linked_issue = link.get("outwardIssue")
                if not linked_issue:
                    continue

                linked_issue_id = linked_issue.get("id")
                if not linked_issue_id:
                    continue

                # Use outward description for relation type mapping
                raw_tag = link_type.get("outward", link_type.get("name", ""))

                # Map Jira link type description to standard RecordRelations enum
                mapped_relation_type = map_relationship_type(raw_tag)

                # Use mapped type if valid enum, otherwise use RELATED as fallback
                if isinstance(mapped_relation_type, RecordRelations):
                    relation_type = mapped_relation_type
                else:
                    # Fallback to RELATED for unmapped/custom link types
                    relation_type = RecordRelations.RELATED

                related_record = RelatedExternalRecord(
                    external_record_id=linked_issue_id,
                    record_type=RecordType.TICKET,
                    relation_type=relation_type,
                )
                related_records.append(related_record)

            except Exception as e:
                self.logger.warning(f"Failed to parse issue link: {e}")
                continue

        return related_records

    def _extract_issue_data(
        self,
        issue: dict[str, Any],
        user_by_account_id: dict[str, AppUser]
    ) -> dict[str, Any]:
        """
        Extract and process issue data from raw Jira issue dictionary.
        """
        issue_id = issue.get("id")
        issue_key = issue.get("key")
        fields = issue.get("fields", {})
        issue_summary = fields.get("summary") or f"Issue {issue_key}"

        issue_type_obj = fields.get("issuetype", {})
        raw_issue_type = issue_type_obj.get("name") if issue_type_obj else None
        issue_type = self.value_mapper.map_type(raw_issue_type)

        parent_obj = fields.get("parent")
        parent_external_id = parent_obj.get("id") if parent_obj else None

        # Build record name with issue key in square brackets at start for better searchability
        issue_name = f"[{issue_key}] {issue_summary}" if issue_key else issue_summary

        # Extract and map status to standardized value
        status_obj = fields.get("status", {})
        raw_status = status_obj.get("name") if status_obj else None
        status = self.value_mapper.map_status(raw_status)

        # Extract and map priority to standardized value
        priority_obj = fields.get("priority", {})
        raw_priority = priority_obj.get("name") if priority_obj else None
        priority = self.value_mapper.map_priority(raw_priority)

        # Extract user information by accountId (email not available in issue fields)
        creator = fields.get("creator")
        creator_account_id = creator.get("accountId") if creator else None
        creator_name = creator.get("displayName") if creator else None
        creator_email = None
        if creator_account_id and creator_account_id in user_by_account_id:
            creator_email = user_by_account_id[creator_account_id].email

        # Reporter (can be changed, unlike creator which is immutable)
        reporter = fields.get("reporter")
        reporter_account_id = reporter.get("accountId") if reporter else None
        reporter_name = reporter.get("displayName") if reporter else None
        reporter_email = None
        if reporter_account_id and reporter_account_id in user_by_account_id:
            reporter_email = user_by_account_id[reporter_account_id].email

        assignee = fields.get("assignee")
        assignee_account_id = assignee.get("accountId") if assignee else None
        assignee_name = assignee.get("displayName") if assignee else None
        assignee_email = None
        if assignee_account_id and assignee_account_id in user_by_account_id:
            assignee_email = user_by_account_id[assignee_account_id].email

        created_at = self._parse_jira_timestamp(fields.get("created"))
        updated_at = self._parse_jira_timestamp(fields.get("updated"))

        return {
            "issue_id": issue_id,
            "issue_key": issue_key,
            "issue_name": issue_name,
            "issue_type": issue_type,
            "parent_external_id": parent_external_id,
            "status": status,
            "priority": priority,
            "creator_email": creator_email,
            "creator_name": creator_name,
            "reporter_email": reporter_email,
            "reporter_name": reporter_name,
            "assignee_email": assignee_email,
            "assignee_name": assignee_name,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    async def _build_issue_records(
        self,
        issues: list[dict[str, Any]],
        project_id: str,
        users: list[AppUser],
        tx_store,
        is_new_project: bool = False,
    ) -> tuple[list[tuple[Record, list[Permission]]], list[str]]:
        """
        Build issue records with permissions from raw issue data, respecting Jira hierarchy.

        When is_new_project is True (full sync wiped sync points), the "skip unchanged
        issues" short-circuit is bypassed so every issue flows through _process_record
        and its BELONGS_TO / RECORD_RELATIONS / PERMISSION / ENTITY_RELATIONS edges are
        recreated after full-sync edge deletion.

        Returns the built records plus the internal ids of attachment records to
        delete (attachments removed at source, detected via changelog); the caller
        performs the deletion via the processor after this read transaction.
        """
        all_records: list[tuple[Record, list[Permission]]] = []
        deferred_delete_ids: list[str] = []
        skipped_unchanged_count = 0

        # Use the user-facing site URL for weburl construction
        atlassian_domain = self.site_url if self.site_url else ""

        # Create accountId -> AppUser lookup for matching issue creators/assignees
        user_by_account_id = {user.source_user_id: user for user in users if user.source_user_id}

        for issue in issues:
            # Isolate per-issue failures: a single malformed issue must not abort the whole
            # page. Aborting rolls back the batch, and because the project checkpoint only
            # advances on success, the same page would be re-fetched and re-fail every sync,
            # wedging the project (and starving every issue with a newer `updated`).
            try:
                issue_records, issue_delete_ids, skipped = await self._build_records_for_issue(
                    issue, project_id, user_by_account_id, atlassian_domain, tx_store, is_new_project
                )
            except Exception as e:
                self.logger.error(
                    f"❌ Skipping issue {issue.get('key') or issue.get('id')} — failed to build record: {e}",
                    exc_info=True,
                )
                continue

            deferred_delete_ids.extend(issue_delete_ids)
            all_records.extend(issue_records)
            if skipped:
                skipped_unchanged_count += 1

        # Log summary only if there were skipped issues
        if skipped_unchanged_count > 0:
            self.logger.debug(f"⏭️ Skipped {skipped_unchanged_count} unchanged issue(s)")

        return all_records, deferred_delete_ids

    async def _build_records_for_issue(
        self,
        issue: dict[str, Any],
        project_id: str,
        user_by_account_id: dict[str, AppUser],
        atlassian_domain: str,
        tx_store,
        is_new_project: bool,
    ) -> tuple[list[tuple[Record, list[Permission]]], list[str], bool]:
        """Build the records for a single issue — its TicketRecord plus attachment
        FileRecords — and collect the ids of attachments removed at source (via changelog)
        for the caller to delete.

        Returns ``(records, delete_ids, skipped_unchanged)``. Raising propagates to
        ``_build_issue_records``, which isolates the failure and skips just this issue so a
        single malformed record can't abort (and wedge) the whole project's sync.
        """
        records: list[tuple[Record, list[Permission]]] = []
        delete_ids: list[str] = []

        # Extract and process issue data
        issue_data = self._extract_issue_data(issue, user_by_account_id)

        issue_id = issue_data["issue_id"]
        issue_key = issue_data["issue_key"]
        issue_name = issue_data["issue_name"]
        issue_type = issue_data["issue_type"]
        parent_external_id = issue_data["parent_external_id"]
        status = issue_data["status"]
        priority = issue_data["priority"]
        creator_email = issue_data["creator_email"]
        creator_name = issue_data["creator_name"]
        reporter_email = issue_data["reporter_email"]
        reporter_name = issue_data["reporter_name"]
        assignee_email = issue_data["assignee_email"]
        assignee_name = issue_data["assignee_name"]
        created_at = issue_data["created_at"]
        updated_at = issue_data["updated_at"]

        # Permissions: empty list - records inherit project-level permissions via inherit_permissions=True
        permissions = []

        # Get fields for attachments (needed by _fetch_issue_attachments)
        fields = issue.get("fields", {})

        # Collect attachments removed at source (via changelog) for deletion
        delete_ids.extend(
            await self._handle_attachment_deletions_from_changelog(issue, tx_store)
        )

        # Check for existing record (works for both Epics and regular issues)
        existing_record = await tx_store.get_record_by_external_id(
            connector_id=self.connector_id,
            external_id=issue_id
        )

        record_id = existing_record.id if existing_record else str(uuid4())
        is_new = existing_record is None

        # A placeholder is a stub created by _handle_parent_record when a child
        # arrives before its parent. Promoting a placeholder to a real record is
        # semantically "new", not "updated" — keep version 0 so the record is
        # routed through on_new_records and counts stay correct.
        is_placeholder = (
            existing_record is not None
            and getattr(existing_record, 'source_updated_at', None) in (0, None)
            and getattr(existing_record, 'mime_type', None) == MimeTypes.UNKNOWN.value
        )

        is_issue_changed = False
        if is_new or is_placeholder:
            version = 0
            is_issue_changed = True
            self.logger.debug(f"🆕 New issue found: {issue_key} (external_id: {issue_id})")
        elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
            version = existing_record.version + 1
            is_issue_changed = True
            self.logger.debug(f"📝 Issue {issue_key} updated, incrementing version to {version}")
        else:
            version = existing_record.version if existing_record else 0

        # Skip processing if issue is unchanged, unless this is a full sync
        # (is_new_project=True means sync points were wiped, so edges need to be
        # recreated even for unchanged issues; _process_record is idempotent).
        if not is_issue_changed and not is_new_project:
            return records, delete_ids, True

        # Record group is always the project. Parent ticket link follows Jira's
        # parent field for any hierarchy level (Initiative→Epic→Story→Subtask).
        external_record_group_id = project_id
        record_group_type = RecordGroupType.PROJECT
        parent_record_id = parent_external_id
        parent_record_type = RecordType.TICKET if parent_external_id else None

        # Every ticket is a root node
        issue_record = TicketRecord(
            id=record_id,
            org_id=self.data_entities_processor.org_id,
            priority=priority,
            status=status,
            type=issue_type,
            creator_email=creator_email,
            creator_name=creator_name,
            reporter_email=reporter_email,
            reporter_name=reporter_name,
            assignee=assignee_name,
            assignee_email=assignee_email,
            external_record_id=issue_id,
            external_revision_id=str(updated_at) if updated_at else None,
            record_name=issue_name,
            record_type=RecordType.TICKET,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            record_group_type=record_group_type,
            external_record_group_id=external_record_group_id,
            parent_external_record_id=parent_record_id,
            parent_record_type=parent_record_type,
            version=version,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=f"{atlassian_domain}/browse/{issue_key}" if atlassian_domain else None,
            source_created_at=created_at,
            source_updated_at=updated_at,
            created_at=created_at,
            updated_at=updated_at,
            inherit_permissions=True,
            preview_renderable=False,
            is_dependent_node=False,  # Tickets are not dependent
            parent_node_id=None,  # Tickets have no parent node
        )

        # Set indexing status based on filters
        if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUES):
            issue_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        # Parse issue links and set related_external_records for creating LINKED_TO edges
        related_external_records = self._parse_issue_links(issue)
        if related_external_records:
            issue_record.related_external_records = related_external_records
            self.logger.debug(f"🔗 Issue {issue_key} has {len(related_external_records)} linked issues")

        records.append((issue_record, permissions))

        # Fetch attachments and create FileRecords
        try:
            attachment_records = await self._fetch_issue_attachments(
                issue_id,
                issue_key,
                fields,
                permissions,
                external_record_group_id,
                record_group_type,
                tx_store,
                parent_node_id=issue_record.id,
            )
            if attachment_records:
                records.extend(attachment_records)
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch attachments for issue {issue_key}: {e}")

        return records, delete_ids, False

    # ============================================================================
    # Attachments
    # ============================================================================

    async def _fetch_issue_attachments(
        self,
        issue_id: str,
        issue_key: str,
        issue_fields: dict[str, Any],
        parent_permissions: list[Permission],
        parent_record_group_id: str,
        parent_record_group_type: RecordGroupType,
        tx_store,
        parent_node_id: Optional[str] = None,
    ) -> list[tuple[FileRecord, list[Permission]]]:
        """
        Fetch attachments for an issue from issue fields.
        All attachments have the issue as their parent.
        """
        attachment_records: list[tuple[FileRecord, list[Permission]]] = []

        try:
            # Get attachments from issue fields (already fetched in ISSUE_SEARCH_FIELDS)
            attachments = issue_fields.get("attachment", [])

            if not attachments:
                return []

            # Construct web URL for attachments - use issue browse URL
            weburl = None
            if self.site_url and issue_key:
                weburl = f"{self.site_url}/browse/{issue_key}"

            for attachment in attachments:
                attachment_id = attachment.get("id")
                if not attachment_id:
                    continue

                # Isolate per-attachment failures so one malformed attachment doesn't drop
                # every attachment on the issue (the outer handler returns []).
                try:
                    # Check for existing attachment record
                    existing_record = await tx_store.get_record_by_external_id(
                        connector_id=self.connector_id,
                        external_id=f"attachment_{attachment_id}"
                    )

                    filename, mime_type, file_size, created_at = self._parse_attachment_metadata(attachment)

                    # Determine version (increment if file was updated)
                    record_id = existing_record.id if existing_record else None
                    is_new = existing_record is None

                    if is_new:
                        version = 0
                    elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != created_at:
                        version = existing_record.version + 1
                    else:
                        version = existing_record.version if existing_record else 0

                    # Create FileRecord using helper method
                    attachment_record = self._create_attachment_file_record(
                        attachment_id=str(attachment_id),
                        filename=filename,
                        mime_type=mime_type,
                        file_size=file_size,
                        created_at=created_at,
                        parent_issue_id=issue_id,
                        parent_node_id=parent_node_id,
                        project_id=parent_record_group_id,
                        weburl=weburl,
                        record_id=record_id,
                        version=version,
                    )

                    # Attachments inherit permissions from parent issue
                    attachment_permissions = parent_permissions.copy()

                    attachment_records.append((attachment_record, attachment_permissions))
                except Exception as e:
                    self.logger.error(
                        f"❌ Skipping attachment {attachment_id} on issue {issue_key} — failed to build record: {e}",
                        exc_info=True,
                    )
                    continue

            self.logger.debug(f"📎 Returning {len(attachment_records)} attachment records for issue {issue_key}")
            return attachment_records

        except Exception as e:
            self.logger.error(f"Failed to fetch attachments for issue {issue_key}: {e}", exc_info=True)
            return []

    def _extract_attachment_filenames_from_wiki(self, text: str) -> set[str]:
        """
        Extract attachment filenames from Jira wiki markup.
        Pattern: !filename.ext|...!
        """
        filenames = set()
        for match in re.finditer(r"!([^!]+)!", text):
            inner = match.group(1)
            filename_part = inner.split("|", 1)[0].strip()
            if filename_part:
                filenames.add(filename_part.lower())
        return filenames

    async def _find_attachment_record_by_id(
        self,
        attachment_id: str,
        tx_store
    ) -> Optional[Record]:
        """
        Find attachment record by ID
        """
        external_id = f"attachment_{attachment_id}"

        # First try new-style external ID (attachment_<id>)
        return await tx_store.get_record_by_external_id(
            connector_id=self.connector_id,
            external_id=external_id,
        )


    async def _handle_attachment_deletions_from_changelog(
        self,
        issue: dict[str, Any],
        tx_store,
    ) -> list[str]:
        """
        Detect attachments removed from an issue (via its changelog) and return the
        internal ids of the matching FileRecords for hard-deletion by the caller.

        Description wiki unlinks alone do not delete attachments that are still on
        the issue — only Attachment-field removals or graph orphans missing from
        ``fields.attachment`` are deleted.
        """
        delete_ids: list[str] = []
        try:
            changelog = issue.get("changelog")
            if not changelog:
                return delete_ids

            histories = changelog.get("histories", [])
            if not histories:
                return delete_ids

            issue_key = issue.get("key")
            issue_id = issue.get("id")
            if not issue_id:
                return delete_ids

            # Get current attachments once (used in multiple places)
            fields = issue.get("fields", {}) or {}
            attachments = fields.get("attachment", []) or []

            current_attachment_ids: set[str] = set()
            current_filenames: set[str] = set()

            for att in attachments:
                att_id = att.get("id")
                filename = att.get("filename")
                if att_id:
                    current_attachment_ids.add(str(att_id))
                if filename:
                    current_filenames.add(str(filename).lower())

            # Collect unique deleted attachment IDs from changelog
            deleted_attachment_ids: set[str] = set()
            unmatched_removed_filenames: set[str] = set()
            has_description_change = False

            # Parse changelog to find deleted attachments
            for history in histories:
                items = history.get("items", [])
                for item in items:
                    field = item.get("field")
                    field_id = item.get("fieldId")

                    # Description wiki unlink ≠ attachment deletion. Only queue
                    # filenames that are no longer among current attachments.
                    if field_id == "description" or field in ("description", "Description"):
                        has_description_change = True
                        from_str = item.get("fromString") or ""
                        to_str = item.get("toString") or ""

                        from_filenames = self._extract_attachment_filenames_from_wiki(from_str)
                        to_filenames = self._extract_attachment_filenames_from_wiki(to_str)
                        removed_filenames = from_filenames - to_filenames

                        for filename_key in removed_filenames:
                            if filename_key in current_filenames:
                                continue
                            unmatched_removed_filenames.add(filename_key)

                    # Explicit attachment deletion events
                    if field in ("Attachment", "attachment") or field_id == "attachment":
                        from_id = item.get("from")
                        to_id = item.get("to")
                        if from_id and (to_id is None or to_id == ""):
                            attachment_id = str(from_id)
                            # Still present at source (e.g. stale changelog) — keep it
                            if attachment_id not in current_attachment_ids:
                                deleted_attachment_ids.add(attachment_id)

            # Case 1: Delete attachments with explicit IDs from changelog
            deleted_count = 0
            for attachment_id in deleted_attachment_ids:
                record = await self._find_attachment_record_by_id(attachment_id, tx_store)
                if not record:
                    self.logger.debug(
                        f"Attachment attachment_{attachment_id} referenced in changelog for issue {issue_key} "
                        "but no matching FileRecord found"
                    )
                    continue

                delete_ids.append(record.id)
                deleted_count += 1

            # Early return if no unmatched filenames / description reconciliation needed
            if not unmatched_removed_filenames and not has_description_change:
                if deleted_count > 0:
                    self.logger.info(
                        f"🗑️ Deleting {deleted_count} attachment(s) for issue {issue_key} based on changelog events"
                    )
                return delete_ids

            # Case 2: filename match for removed-but-gone files + orphan ID diff
            existing_records = await tx_store.get_records_by_parent(
                connector_id=self.connector_id,
                parent_external_record_id=issue_id,
                record_type=RecordType.FILE.value
            )

            deleted_by_filename = 0
            for record in existing_records:
                record_filename_lower = record.record_name.lower() if record.record_name else ""
                if unmatched_removed_filenames and record_filename_lower in unmatched_removed_filenames:
                    delete_ids.append(record.id)
                    deleted_count += 1
                    deleted_by_filename += 1
                    continue

                # Extract attachment ID from external_record_id (handles both "attachment_<id>" and legacy formats)
                external_id = record.external_record_id
                attachment_id = external_id.replace("attachment_", "") if external_id.startswith("attachment_") else external_id
                if attachment_id in current_attachment_ids:
                    continue

                # Attachment no longer exists at source -> delete
                delete_ids.append(record.id)
                deleted_count += 1

            if deleted_count > 0:
                if deleted_by_filename > 0:
                    self.logger.info(
                        f"🗑️ Deleting {deleted_count} attachment(s) for issue {issue_key} "
                        f"({deleted_by_filename} by filename match, {deleted_count - deleted_by_filename} by ID diff)"
                    )
                else:
                    self.logger.info(
                        f"🗑️ Deleting {deleted_count} attachment(s) for issue {issue_key} that were removed from Jira"
                    )

            return delete_ids

        except Exception as e:
            issue_key = issue.get("key", "unknown")
            self.logger.error(
                f"❌ Error handling attachment deletions from changelog for issue {issue_key}: {e}",
                exc_info=True,
            )
            return delete_ids

    # ============================================================================
    # BlockGroups & Blocks Parsing
    # ============================================================================

    def _organize_issue_comments_to_threads(
        self,
        comments_data: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """Return all comments as a single flat thread sorted by created timestamp.

        Jira Cloud's REST API does not expose parent-child relationships for
        comments (replies appear as independent top-level objects). Until
        Atlassian adds threading support to the API, all comments are grouped
        into one thread ordered chronologically.
        """
        valid = [c for c in comments_data if c.get("id")]
        if not valid:
            return []

        valid.sort(
            key=lambda c: self._parse_jira_timestamp(c.get("created", "")) or 0
        )
        return [valid]

    async def _build_description_block_group(
        self,
        issue_id: str,
        issue_key: str,
        issue_name: str,
        weburl: str,
        rendered_fields: dict[str, Any],
        attachments_by_id: dict[str, dict[str, Any]],
        is_image: Callable[[str], bool],
        fetch_base64: Callable[[dict[str, Any]], Awaitable[Optional[str]]],
    ) -> tuple[BlockGroup, set[str]]:
        """Build the description BlockGroup (index 0) and return referenced attachment IDs."""
        raw_description_html = rendered_fields.get("description") or ""
        desc_referenced = extract_attachment_ids(raw_description_html)
        description_html, _ = await inline_images_as_base64(
            raw_description_html, attachments_by_id, is_image, fetch_base64
        )
        heading = f"<h1>{html_escape(issue_name)}</h1>"
        description_data = f"{heading}\n{description_html}" if description_html else heading

        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=issue_name,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Description for issue {issue_key}" if issue_key else "Issue description",
            source_group_id=f"{issue_id}_description",
            data=description_data,
            format=DataFormat.HTML,
            weburl=weburl,
            requires_processing=True,
        )
        return description_block_group, desc_referenced

    async def _build_comment_block_groups(
        self,
        issue_id: str,
        issue_key: str,
        weburl: str,
        comments_data: list[dict[str, Any]],
        rendered_body_by_id: dict[str, str],
        attachments_by_id: dict[str, dict[str, Any]],
        children_map: dict[str, ChildRecord],
        is_image: Callable[[str], bool],
        fetch_base64: Callable[[dict[str, Any]], Awaitable[Optional[str]]],
        start_index: int,
    ) -> tuple[list[BlockGroup], set[str], int]:
        """Build comment thread + comment BlockGroups.

        Returns ``(block_groups, comment_referenced_ids, next_index)``.
        """
        block_groups: list[BlockGroup] = []
        block_group_index = start_index
        comment_referenced_ids: set[str] = set()

        for thread_comments in self._organize_issue_comments_to_threads(comments_data):
            if not thread_comments:
                continue

            thread_block_group_index = block_group_index
            thread_block_group = BlockGroup(
                id=str(uuid4()),
                index=thread_block_group_index,
                parent_index=0,
                name="Comments",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT_THREAD,
                description=f"Comments for issue {issue_key}" if issue_key else "Comments",
                source_group_id=f"{issue_id}_comments",
                weburl=weburl,
                requires_processing=False,
            )
            block_groups.append(thread_block_group)
            block_group_index += 1

            for comment in thread_comments:
                comment_id = str(comment.get("id", ""))
                raw_comment_html = comment.get("renderedBody") or rendered_body_by_id.get(comment_id, "")
                if not raw_comment_html:
                    continue

                c_referenced = extract_attachment_ids(raw_comment_html)
                comment_referenced_ids |= c_referenced
                comment_html, _ = await inline_images_as_base64(
                    raw_comment_html, attachments_by_id, is_image, fetch_base64
                )
                comment_children = [
                    children_map[i]
                    for i in children_map
                    if i in c_referenced and not is_image(i)
                ]

                if self.site_url and issue_key and comment_id:
                    comment_weburl = f"{self.site_url}/browse/{issue_key}?focusedCommentId={comment_id}"
                else:
                    comment_weburl = weburl

                author = comment.get("author", {})
                author_name = author.get("displayName", "Unknown")

                comment_block_group = BlockGroup(
                    id=str(uuid4()),
                    index=block_group_index,
                    parent_index=thread_block_group_index,
                    type=GroupType.TEXT_SECTION,
                    sub_type=GroupSubType.COMMENT,
                    name=f"Comment by {author_name}",
                    description=f"Comment by {author_name}",
                    source_group_id=comment_id,
                    data=comment_html,
                    format=DataFormat.HTML,
                    weburl=comment_weburl,
                    requires_processing=True,
                    children_records=comment_children or None,
                )
                block_groups.append(comment_block_group)
                block_group_index += 1

        return block_groups, comment_referenced_ids, block_group_index

    async def _parse_issue_to_blocks(
        self,
        issue_data: dict[str, Any],
        issue_key: str,
        weburl: Optional[str],
        rendered_fields: dict[str, Any],
        comments_data: list[dict[str, Any]],
        attachment_children_map: Optional[dict[str, ChildRecord]] = None,
    ) -> BlocksContainer:
        """Parse a Jira issue into a BlocksContainer from Jira's *rendered* HTML.

        Structure:
        - Description BlockGroup (index 0): the issue's rendered-HTML description with image
          attachments inlined as base64. children_records = non-image files linked in the
          description (not owned by a comment) plus any standalone attachments (attached to the
          issue but referenced nowhere).
        - One thread BlockGroup per comment thread (parent_index 0).
        - One comment BlockGroup per comment (sub_type COMMENT). children_records = non-image
          files linked in that comment; inline images are base64'd into the comment HTML.

        Routing rule (Jira renders image = ``<img>``, file = ``<a>``):
        - image → base64-inlined into the HTML it appears in; never a child.
        - non-image linked in a comment → child of that comment.
        - non-image linked only in the description → description child.
        - attachment referenced nowhere → description child.
        """
        if not weburl:
            raise ValueError("weburl is required when creating BlockGroup for issues")

        issue_id = issue_data.get("id", "")
        fields = issue_data.get("fields", {})
        issue_attachments = fields.get("attachment", []) or []
        resolved_issue_key = issue_key or issue_data.get("key", "") or ""
        issue_summary = fields.get("summary") or f"Issue {resolved_issue_key or issue_id}"
        issue_name = (
            f"[{resolved_issue_key}] {issue_summary}" if resolved_issue_key else issue_summary
        )
        children_map = attachment_children_map or {}

        attachments_by_id = {
            str(a.get("id")): a for a in issue_attachments if a.get("id") is not None
        }
        mime_by_id = {
            str(a.get("id")): str(a.get("mimeType", "")) for a in issue_attachments if a.get("id") is not None
        }

        def is_image(att_id: str) -> bool:
            return mime_by_id.get(att_id, "").startswith("image/")

        base64_cache: dict[str, Optional[str]] = {}

        async def fetch_base64(attachment: dict[str, Any]) -> Optional[str]:
            return await self._fetch_attachment_as_base64(attachment, base64_cache)

        # 1. Description BlockGroup (index 0). Children are assigned after comments are parsed,
        #    so a file linked in both a comment and the description is owned by the comment.
        description_block_group, desc_referenced = await self._build_description_block_group(
            issue_id, resolved_issue_key, issue_name, weburl,
            rendered_fields, attachments_by_id, is_image, fetch_base64,
        )
        block_groups: list[BlockGroup] = [description_block_group]

        # 2. Comment thread + comment BlockGroups
        rendered_body_by_id = {
            str(c.get("id")): (c.get("body") or "")
            for c in ((rendered_fields.get("comment") or {}).get("comments") or [])
            if c.get("id") is not None
        }
        comment_referenced_ids: set[str] = set()

        if comments_data:
            comment_bgs, comment_referenced_ids, _ = await self._build_comment_block_groups(
                issue_id, resolved_issue_key, weburl, comments_data,
                rendered_body_by_id, attachments_by_id, children_map,
                is_image, fetch_base64, start_index=1,
            )
            block_groups.extend(comment_bgs)

        # 3. Description children: non-image files linked only in the description (a comment,
        #    if it also links the file, owns it) plus standalone attachments referenced nowhere.
        all_referenced = desc_referenced | comment_referenced_ids
        description_child_ids: set[str] = set()
        for att_id in desc_referenced:
            if att_id in children_map and not is_image(att_id) and att_id not in comment_referenced_ids:
                description_child_ids.add(att_id)
        for att_id in children_map:
            if att_id not in all_referenced:
                description_child_ids.add(att_id)

        description_children = [children_map[i] for i in children_map if i in description_child_ids]
        if description_children:
            description_block_group.children_records = description_children

        # Wire BlockGroup parent/child indices
        blockgroup_children_map: dict[int, list[int]] = defaultdict(list)
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)

        for bg in block_groups:
            child_bg_indices = sorted(blockgroup_children_map.get(bg.index, []))
            if child_bg_indices:
                bg.children = BlockGroupChildren.from_indices(
                    block_indices=[],
                    block_group_indices=child_bg_indices,
                )

        return BlocksContainer(blocks=[], block_groups=block_groups)

    async def _process_issue_attachments_for_children(
        self,
        attachments_data: list[dict[str, Any]],
        issue_id: str,
        issue_node_id: str,
        project_id: str,
        issue_weburl: Optional[str],
        tx_store,
    ) -> dict[str, ChildRecord]:
        """
        Process issue attachments and create ChildRecords for TableRowMetadata.
        Creates FileRecords if they don't exist (for new attachments added after sync).

        ALL attachments are processed including images.
        Returns a MAP of attachment_id -> ChildRecord for proper mapping to description/comments.

        Args:
            attachments_data: List of attachment data from Jira API
            issue_id: Issue external ID
            issue_node_id: Internal record ID of issue
            project_id: Project ID for external_record_group_id
            issue_weburl: Issue web URL (used as weburl for FileRecords)
            tx_store: Transaction store for looking up existing records

        Returns:
            Dict mapping attachment_id -> ChildRecord for proper location assignment
        """
        attachment_children_map: dict[str, ChildRecord] = {}
        new_file_records: list[tuple[FileRecord, list[Permission]]] = []

        for attachment in attachments_data:
            try:
                attachment_id = attachment.get("id", "")
                if not attachment_id:
                    continue

                # Look up existing attachment record from database
                external_id = f"attachment_{attachment_id}"
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_id
                )

                # Create FileRecord if it doesn't exist (new attachment added after sync)
                if not existing_record:
                    filename, mime_type, file_size, created_at = self._parse_attachment_metadata(attachment)

                    # Create FileRecord using helper method
                    file_record = self._create_attachment_file_record(
                        attachment_id=str(attachment_id),
                        filename=filename,
                        mime_type=mime_type,
                        file_size=file_size,
                        created_at=created_at,
                        parent_issue_id=issue_id,
                        parent_node_id=issue_node_id,
                        project_id=project_id,
                        weburl=issue_weburl,
                    )

                    new_file_records.append((file_record, []))
                    existing_record = file_record

                if existing_record:
                    attachment_children_map[str(attachment_id)] = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name
                    )

            except Exception as e:
                attachment_id = attachment.get("id", "unknown")
                self.logger.error(f"❌ Error processing issue attachment {attachment_id} for children_records: {e}", exc_info=True)
                continue

        # Save any new FileRecords
        if new_file_records:
            await self.data_entities_processor.on_new_records(new_file_records)
            self.logger.info(f"📎 Created {len(new_file_records)} new FileRecords for attachments added after sync")

        return attachment_children_map

    def _rate_limit_delay(self, response: Any, attempt: int) -> float:
        """Seconds to wait before retrying a Jira 429. Honor ``Retry-After`` (Jira Cloud returns
        seconds) as a FLOOR when present — never wait less than Jira asked — otherwise use
        exponential backoff from a 2s base (jittered). Capped at ``RATE_LIMIT_MAX_DELAY_SEC`` so a
        very large Retry-After can't stall the sync (the call then fails this run and resumes /
        skip-preserves next sync). Ref: https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
        """
        headers = getattr(response, "headers", None) or {}
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after is not None:
            try:
                # Honor Retry-After exactly (as a floor); do not jitter it below what Jira asked.
                return min(float(retry_after), RATE_LIMIT_MAX_DELAY_SEC)
            except (ValueError, TypeError):
                pass
        base = 2.0 * (2 ** attempt)  # exponential backoff, 2s base, doubling per attempt
        return min(base * random.uniform(0.7, 1.3), RATE_LIMIT_MAX_DELAY_SEC)

    async def _call_with_retry(
        self,
        call: Callable[[JiraDataSource], Awaitable[Any]],
        ctx: str,
        max_attempts: int = 4,
    ) -> Any:
        """Invoke a read-only Jira datasource call, retrying transient transport errors AND HTTP
        429 (honoring Retry-After via :meth:`_rate_limit_delay`). ``call`` receives a fresh
        datasource and returns the response coroutine; it MUST be an idempotent GET so replay is
        safe. Returns the final response (which may still be non-OK — e.g. a 429 that survived all
        attempts, for the caller to handle); raises only when every attempt hit a transport error.
        """
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                datasource = await self._get_fresh_datasource()
                response = await call(datasource)
            except (
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.ConnectError,
                httpx.PoolTimeout,
                httpx.ReadTimeout,
            ) as e:
                last_exc = e
                if attempt == max_attempts - 1:
                    break
                backoff = 0.5 * (2 ** attempt)  # 0.5s, 1.0s, ...
                self.logger.warning(
                    "Transient transport error (%s, attempt %s/%s): %s — retrying in %.1fs",
                    ctx, attempt + 1, max_attempts, e, backoff,
                )
                await asyncio.sleep(backoff)
                continue

            if response.status == HttpStatusCode.TOO_MANY_REQUESTS.value and attempt < max_attempts - 1:
                delay = self._rate_limit_delay(response, attempt)
                self.logger.warning(
                    "Jira rate-limited (429) (%s, attempt %s/%s): waiting %.1fs",
                    ctx, attempt + 1, max_attempts, delay,
                )
                await asyncio.sleep(delay)
                continue

            return response

        raise Exception(f"Failed {ctx} after {max_attempts} attempts: {last_exc}") from last_exc

    async def _search_issues_with_retry(
        self,
        *,
        project_key: str,
        jql: str,
        next_page_token: str | None,
        max_results: int,
        fields: list[str],
        expand: str,
        max_attempts: int = 4,
    ) -> Any:
        """Search Jira issues with transport + 429 retry (see :meth:`_call_with_retry`)."""
        return await self._call_with_retry(
            lambda ds: ds.search_and_reconsile_issues_using_jql_post(
                jql=jql,
                maxResults=max_results,
                nextPageToken=next_page_token,
                fields=fields,
                expand=expand,
            ),
            ctx=f"searching issues for project {project_key}",
            max_attempts=max_attempts,
        )

    async def _get_issue_with_retry(
        self,
        issue_id: str,
        fields: list[str],
        expand: list[str] | None = None,
        max_attempts: int = 4,
    ) -> Any:
        """Fetch a Jira issue with transport + 429 retry (see :meth:`_call_with_retry`)."""
        return await self._call_with_retry(
            lambda ds: ds.get_issue(issueIdOrKey=issue_id, fields=fields, expand=expand),
            ctx=f"fetching issue {issue_id}",
            max_attempts=max_attempts,
        )

    async def _process_issue_blockgroups_for_streaming(self, record: Record) -> bytes:
        """
        Process issue BlockGroups for streaming by creating BlocksContainer on-demand.

        This function:
        1. Fetches issue data from Jira API (including comments, attachments)
        2. Fetches related FileRecords from database (for ChildRecords)
        3. Creates new FileRecords if any new attachments/files added since sync
        4. Parses issue to BlocksContainer with Description and Thread BlockGroups
        5. Serializes BlocksContainer to JSON bytes for streaming

        Args:
            record: TicketRecord to stream

        Returns:
            bytes: Serialized BlocksContainer as JSON bytes

        Raises:
            Exception: If issue data cannot be fetched or processed
        """
        issue_id = record.external_record_id

        # Fetch issue with comments. The httpx pool occasionally hands out a
        # keep-alive socket that the LB/proxy has already half-closed, raising
        # ``RemoteProtocolError`` ("Server disconnected without sending a response")
        # before any HTTP response is received. Retry via ``_get_issue_with_retry``.
        response = await self._get_issue_with_retry(
            issue_id=issue_id,
            fields=["summary", "description", "attachment", "comment", "project"],
            expand=["renderedFields"],
        )

        if response.status == HttpStatusCode.NOT_FOUND.value:
            # Streamed after the source issue was deleted. Return a clean 404, not a 500.
            self.logger.warning(
                f"Issue {issue_id} not found at source (record {record.external_record_id}) "
                "— likely deleted in Jira"
            )
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail=f"Issue {issue_id} no longer exists in Jira (deleted)",
            )

        if response.status != HttpStatusCode.OK.value:
            raise Exception(f"Failed to fetch issue content: {response.text()}")

        issue_data = response.json()
        if not issue_data:
            raise Exception(f"No issue data found for ID: {issue_id}")

        fields = issue_data.get("fields", {})

        # Get issue key from API response
        issue_key = issue_data.get("key", "")

        # Build issue weburl
        if self.site_url and issue_key:
            issue_weburl = f"{self.site_url}/browse/{issue_key}"
        else:
            issue_weburl = record.weburl

        # Get attachments and comments from issue data
        attachments_data = fields.get("attachment", [])

        # Handle comments - can be nested in "comment" field with "comments" array
        comments_field = fields.get("comment", {})
        if isinstance(comments_field, dict):
            comments_data = comments_field.get("comments", [])
        else:
            comments_data = []

        # Resolve project for new attachment FileRecords (see DC streaming path).
        project_id = record.external_record_group_id or ""
        if not project_id:
            project = fields.get("project") or {}
            project_id = project.get("id") or ""

        # Fetch child records from database - get map of attachment_id -> ChildRecord.
        # Also creates FileRecords for any attachments added since the last sync.
        attachment_children_map: dict[str, ChildRecord] = {}

        async with self.data_store_provider.transaction() as tx_store:
            if attachments_data:
                attachment_children_map = await self._process_issue_attachments_for_children(
                    attachments_data=attachments_data,
                    issue_id=issue_id,
                    issue_node_id=record.id,
                    project_id=project_id,
                    issue_weburl=issue_weburl,
                    tx_store=tx_store
                )

        # Parse issue to BlocksContainer from Jira's rendered HTML (renderedFields).
        rendered_fields = issue_data.get("renderedFields") or {}
        blocks_container = await self._parse_issue_to_blocks(
            issue_data=issue_data,
            issue_key=issue_key,
            weburl=issue_weburl,
            rendered_fields=rendered_fields,
            comments_data=comments_data,
            attachment_children_map=attachment_children_map if attachment_children_map else None,
        )

        # Serialize BlocksContainer to JSON bytes
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')

    # ============================================================================
    # Media & Streaming
    # ============================================================================

    async def _fetch_attachment_as_base64(
        self,
        attachment: dict[str, Any],
        cache: dict[str, Optional[str]],
    ) -> Optional[str]:
        """Fetch an image attachment and return it as a base64 ``data:`` URI (or ``None``).

        The rendered HTML gives us the exact numeric attachment id, so no filename matching is
        needed — ``attachment`` is the resolved metadata dict from ``fields.attachment``. Only
        images are inlined (for multimodal indexing); non-image files are represented as child
        FILE records instead. Oversized images (metadata size or actual bytes over
        ``MAX_INLINE_IMAGE_BYTES``) and fetch failures return ``None`` so the caller keeps the
        alt text rather than a data URI. ``cache`` memoises per-issue results (including
        ``None``) so an image reused across the description and comments is fetched once.
        """
        att_id = str(attachment.get("id", ""))
        if att_id in cache:
            return cache[att_id]

        result: Optional[str] = None
        try:
            mime_type = str(attachment.get("mimeType", ""))
            if not mime_type.startswith("image/"):
                cache[att_id] = None
                return None

            size_bytes = int(attachment.get("size") or 0)
            if size_bytes and size_bytes > MAX_INLINE_IMAGE_BYTES:
                self.logger.debug(f"Skipping large inline image {att_id} ({size_bytes} bytes) — not inlined")
                cache[att_id] = None
                return None

            datasource = await self._get_fresh_datasource()
            content_response = await datasource.get_attachment_content(id=attachment.get("id"), redirect=False)
            if content_response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch attachment content {att_id}: {content_response.status}")
                cache[att_id] = None
                return None

            content_bytes = content_response.bytes()
            if len(content_bytes) > MAX_INLINE_IMAGE_BYTES:
                self.logger.debug(f"Skipping large inline image {att_id} ({len(content_bytes)} bytes) — not inlined")
                cache[att_id] = None
                return None

            result = f"data:{mime_type};base64,{base64.b64encode(content_bytes).decode('utf-8')}"
        except Exception as e:
            self.logger.warning(f"⚠️ Error fetching inline image attachment {att_id}: {e}")
            result = None

        cache[att_id] = result
        return result

    def _parse_attachment_metadata(self, attachment: dict[str, Any]) -> tuple[str, str, int, int]:
        """Extract ``(filename, mime_type, file_size, created_at_ms)`` from a Jira attachment dict."""
        created_str = attachment.get("created")
        return (
            attachment.get("filename", "unknown"),
            attachment.get("mimeType", MimeTypes.UNKNOWN.value),
            attachment.get("size", 0),
            self._parse_jira_timestamp(created_str) if created_str else 0,
        )

    def _create_attachment_file_record(
        self,
        attachment_id: str,
        filename: str,
        mime_type: str,
        file_size: int,
        created_at: int,
        parent_issue_id: str,
        parent_node_id: Optional[str],
        project_id: str,
        weburl: Optional[str],
        record_id: Optional[str] = None,
        version: int = 0,
        external_id_prefix: str = "attachment_",
        skip_filter_check: bool = False,
    ) -> FileRecord:
        """
        Create a FileRecord for an attachment with consistent settings.

        This helper consolidates FileRecord creation logic to avoid duplication
        and ensure consistency across sync, streaming, and reindexing flows.

        Args:
            skip_filter_check: If True, skip filter checks (used during reindexing).
                              If False, apply indexing filter checks (default for sync/streaming).

        Returns:
            FileRecord with consistent field settings
        """
        # Extract extension from filename and resolve MIME type
        extension = normalize_file_extension(
            filename.rsplit(".", 1)[-1] if "." in filename else None
        )
        resolved_mime_type = get_mime_type_for_extension(
            extension,
            fallback=mime_type or MimeTypes.UNKNOWN.value,
        )

        # Build external_record_id
        external_record_id = f"{external_id_prefix}{attachment_id}"

        file_record = FileRecord(
            id=record_id or str(uuid4()),
            org_id=self.data_entities_processor.org_id,
            record_name=filename,
            record_type=RecordType.FILE,
            external_record_id=external_record_id,
            external_revision_id=str(created_at) if created_at else None,
            parent_external_record_id=parent_issue_id,
            parent_record_type=RecordType.TICKET,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            origin=OriginTypes.CONNECTOR,
            version=version,
            mime_type=resolved_mime_type,
            extension=extension or None,
            size_in_bytes=file_size,
            record_group_type=RecordGroupType.PROJECT,
            external_record_group_id=project_id,
            created_at=created_at or get_epoch_timestamp_in_ms(),
            updated_at=created_at or get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=created_at,
            weburl=weburl,
            inherit_permissions=True,
            is_file=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
        )

        # Set indexing status based on filters (if loaded and not skipping filter check)
        # Skip filter check during reindexing to allow reindexing regardless of filter settings
        if not skip_filter_check and self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        return file_record

    # ============================================================================
    # Utility Methods
    # ============================================================================

    def _parse_jira_timestamp(self, timestamp_str: Optional[str]) -> int:
        """
        Parse Jira timestamp to epoch milliseconds.

        Supports multiple Jira timestamp formats:
        - With milliseconds: 2024-01-15T10:30:45.123+0000
        - Without milliseconds: 2024-01-15T10:30:45+0000
        - ISO format with colon in timezone: 2024-01-15T10:30:45.123+00:00
        - Z suffix: 2024-01-15T10:30:45.123Z
        """
        if not timestamp_str:
            return 0

        # Normalize to ISO format that fromisoformat() can handle
        # Replace Z with +00:00, and +0000/-0000 with +00:00/-00:00
        normalized = timestamp_str.replace('Z', '+00:00')
        # Convert +0000 or -0000 format to +00:00 or -00:00 (fromisoformat requires colon)
        normalized = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', normalized)

        try:
            dt = datetime.fromisoformat(normalized)
            return int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            # Fallback to strptime for edge cases (requires +0000 format, not +00:00)
            normalized_strptime = re.sub(r'([+-])(\d{2}):(\d{2})$', r'\1\2\3', normalized)
            for fmt in ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"]:
                try:
                    dt = datetime.strptime(normalized_strptime, fmt)
                    return int(dt.timestamp() * 1000)
                except ValueError:
                    continue

        self.logger.warning(f"⚠️ Failed to parse timestamp '{timestamp_str}'")
        return 0

    def _safe_json_parse(self, response, context: str = "API response") -> Optional[dict[str, Any]]:
        """
        Safely parse JSON response with error handling.

        Args:
            response: HTTP response object with .json() method
            context: Description of what we're parsing for error messages

        Returns:
            Parsed JSON as dict, or None if parsing fails
        """
        try:
            return response.json()
        except Exception as e:
            self.logger.error(f"❌ Failed to parse JSON from {context}: {e}")
            return None

    async def handle_webhook_notification(self, notification: dict) -> None:
        pass

    # ============================================================================
    # Public API Methods
    # ============================================================================

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific record"""
        return ""

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Jira using DataSource.

        Used only on the sync HTTP setup path; callers map False → FE error.
        No inbox notification here (avoids duplicate alerts with the API response).
        """
        try:
            # init() always runs (and must succeed) before this in the connector setup
            # flow, so the client/datasource are already built — fail fast if not,
            # rather than re-initializing (mirrors the Confluence connector).
            if not self.data_source:
                self.logger.error("Jira connector not initialized")
                return False

            # Test by fetching user info (simple API call)
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_current_user()
            if response.status != HttpStatusCode.OK.value:
                self.logger.error(
                    "❌ Connection test failed: /myself returned %s", response.status
                )
                return False
            return True
        except Exception as e:
            self.logger.error(f"❌ Connection test failed: {e}")
            return False

    async def run_incremental_sync(self) -> None:
        """Run incremental sync - calls run_sync which handles incremental logic"""
        await self.run_sync()

    async def cleanup(self) -> None:
        """Cleanup resources - close HTTP client connections properly"""
        try:
            self.logger.info("Cleaning up Jira connector resources")

            # Close HTTP client properly BEFORE event loop closes
            # This prevents Windows asyncio "Event loop is closed" errors
            if self.external_client:
                try:
                    internal_client = self.external_client.get_client()
                    if internal_client and hasattr(internal_client, 'close'):
                        await internal_client.close()
                        self.logger.debug("Closed Jira HTTP client connection")
                except Exception as e:
                    # Swallow errors during shutdown - client may already be closed
                    self.logger.debug(f"Error closing Jira client (may be expected during shutdown): {e}")
                finally:
                    self.external_client = None

            # Clear data source reference
            self.data_source = None

            self.logger.info("Jira connector cleanup completed")
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")

    async def _stream_attachment_content(
        self, attachment_id: str, external_record_id: str
    ) -> AsyncGenerator[bytes, None]:
        """Stream a Jira attachment's bytes in chunks (large-file safe) rather than buffering
        the whole file in memory. Delegates to the datasource's streaming download.

        A non-success source status surfaces as ``httpx.HTTPStatusError`` on the first chunk;
        because the StreamingResponse has already begun, this ends the stream (the caller sees a
        truncated body) rather than a pre-flight HTTP error — the diagnostic (e.g. 404 = deleted)
        is preserved in the logs.
        """
        try:
            datasource = await self._get_fresh_datasource()
            async for chunk in datasource.download_attachment_content(attachment_id=attachment_id):
                yield chunk
        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == HttpStatusCode.NOT_FOUND.value:
                self.logger.warning(
                    f"Attachment {attachment_id} not found at source "
                    f"(record {external_record_id}) — likely deleted in Jira"
                )
            raise HTTPException(
                status_code=status,
                detail=f"Failed to fetch attachment content: HTTP {status}",
            ) from e
        except Exception as e:
            self.logger.error(
                f"Error streaming attachment {attachment_id} (record {external_record_id}): {e}"
            )
            raise

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (issue, comment, or attachment).
        """
        try:
            if not self.data_source:
                await self.init()

            if record.record_type == RecordType.TICKET:
                # Stream BlocksContainer as JSON
                content_bytes = await self._process_issue_blockgroups_for_streaming(record)

                return StreamingResponse(
                    iter([content_bytes]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.json"'
                    }
                )

            elif record.record_type == RecordType.FILE:
                # Stream attachment content in chunks — never buffer the whole file in memory.
                attachment_id = record.external_record_id.replace("attachment_", "")

                # Determine filename from record name
                filename = record.record_name if hasattr(record, 'record_name') else f"attachment_{attachment_id}"

                # Replace non-ASCII characters to avoid latin-1 encoding errors
                safe_filename = sanitize_filename_for_content_disposition(
                    filename,
                    fallback=f"attachment_{attachment_id}"
                )
                encoded_filename = quote(filename)

                # Jira requires UTF-8 encoded filename in addition to the safe filename
                additional_headers = {
                    "Content-Disposition": f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_filename}'
                }

                # Prime the first chunk so a source-404 (deleted attachment) surfaces as a
                # clean 404 here — once the stream starts, 200 headers are already sent and
                # the status can no longer change (which is why a mid-stream raise tracebacks).
                attachment_stream = self._stream_attachment_content(attachment_id, record.external_record_id)
                try:
                    first_chunk = await attachment_stream.__anext__()
                except StopAsyncIteration:
                    first_chunk = b""

                async def _attachment_body() -> AsyncGenerator[bytes, None]:
                    yield first_chunk
                    async for chunk in attachment_stream:
                        yield chunk

                return create_stream_record_response(
                    _attachment_body(),
                    filename=filename,
                    mime_type=record.mime_type if hasattr(record, 'mime_type') else MimeTypes.UNKNOWN.value,
                    fallback_filename=f"attachment_{attachment_id}",
                    additional_headers=additional_headers
                )

            else:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Unsupported record type for streaming: {record.record_type}",
                )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Error streaming record {record.external_record_id} ({record.record_type}): {e}")
            raise

    # ============================================================================
    # Reindexing
    # ============================================================================

    async def reindex_records(self, record_results: list[Record]) -> None:
        """Reindex a list of Jira records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor
        4. Skips reindex for records that are not properly typed (base Record class)"""
        try:
            if not record_results:
                return

            self.logger.info(f"Starting reindex for {len(record_results)} Jira records")

            # Ensure external clients are initialized
            if not self.data_source:
                raise Exception("DataSource not initialized. Call init() first.")

            # Resolve emails against the synced user directory once. Jira's issue GET usually
            # omits emailAddress, so without this the reindex path would null out already-resolved
            # creator/reporter/assignee emails (and degrade the ticket-user edges).
            synced_users = await self.data_entities_processor.get_all_app_users(self.connector_id)
            synced_user_by_account_id: dict[str, AppUser] = {
                u.source_user_id: u for u in synced_users if u.source_user_id
            }

            # Check records at source for updates
            updated_records = []
            non_updated_records = []

            for record in record_results:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(
                        record, synced_user_by_account_id
                    )
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
                    # Only reindex properly typed records (TicketRecord, FileRecord)
                    # Check if it's a subclass of Record but not the base Record class itself
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
            self.logger.error(f"Error during Jira reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, record: Record, synced_user_by_account_id: Optional[dict[str, "AppUser"]] = None
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch record from source and return data for reindexing.

        Note: Comments are no longer separate records - they are processed as Blocks
        within the issue's BlocksContainer during streaming.
        """
        try:
            if record.record_type == RecordType.TICKET:
                return await self._check_and_fetch_updated_issue(record, synced_user_by_account_id)
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_issue(
        self, record: Record, synced_user_by_account_id: Optional[dict[str, "AppUser"]] = None
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch issue from source for reindexing."""
        try:
            # Load indexing filters if not already loaded (needed for reindexing context)
            if self.indexing_filters is None:
                _, self.indexing_filters = await load_connector_filters(
                    self.config_service,
                    "jira",
                    self.connector_id,
                    self.logger
                )

            issue_id = record.external_record_id

            # Fetch issue from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_issue(
                issueIdOrKey=issue_id,
                expand=[]
            )

            if response.status in (
                HttpStatusCode.NOT_FOUND.value,
                HttpStatusCode.GONE.value,
                HttpStatusCode.BAD_REQUEST.value,
            ):
                self.logger.warning(f"Issue {issue_id} not found at source, may have been deleted")
                return None

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"Failed to fetch issue {issue_id}: HTTP {response.status}")
                return None

            issue = response.json()
            fields = issue.get("fields", {})

            # Check if updated timestamp changed
            current_updated_at = self._parse_jira_timestamp(fields.get("updated")) if fields.get("updated") else 0

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at == current_updated_at:
                self.logger.debug(f"Issue {issue_id} has not changed at source")
                return None

            self.logger.info(f"Issue {issue_id} has changed at source (timestamp: {record.source_updated_at if hasattr(record, 'source_updated_at') else 'N/A'} -> {current_updated_at})")

            # Resolve creator/reporter/assignee emails from the synced user directory first
            # (Jira's issue GET usually omits emailAddress); an inline emailAddress, when present,
            # overrides. A shallow copy so inline overrides don't mutate the shared map.
            user_by_account_id: dict[str, AppUser] = dict(synced_user_by_account_id or {})
            for user_field in ["creator", "reporter", "assignee"]:
                user_obj = fields.get(user_field) or {}
                account_id = user_obj.get("accountId")
                email = user_obj.get("emailAddress")
                if account_id and email:
                    user_by_account_id[account_id] = AppUser(
                        id="",
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        email=email,
                        full_name=user_obj.get("displayName") or email,
                        source_user_id=account_id
                    )

            # Extract issue data using existing function
            issue_data = self._extract_issue_data(issue, user_by_account_id)

            # Get project info
            project = fields.get("project") or {}
            project_id = project.get("id", "")

            # Increment version
            version = record.version + 1 if hasattr(record, 'version') else 1

            # Create updated TicketRecord preserving record ID and existing relationships
            issue_record = TicketRecord(
                id=record.id,
                org_id=self.data_entities_processor.org_id,
                priority=issue_data["priority"],
                status=issue_data["status"],
                type=issue_data.get("issue_type"),
                creator_email=issue_data["creator_email"],
                creator_name=issue_data["creator_name"],
                reporter_email=issue_data["reporter_email"],
                reporter_name=issue_data["reporter_name"],
                assignee=issue_data["assignee_name"],
                assignee_email=issue_data["assignee_email"],
                external_record_id=issue_id,
                external_revision_id=str(current_updated_at) if current_updated_at else None,
                record_name=issue_data["issue_name"],
                record_type=RecordType.TICKET,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                record_group_type=record.record_group_type if hasattr(record, 'record_group_type') else RecordGroupType.PROJECT,
                external_record_group_id=record.external_record_group_id if hasattr(record, 'external_record_group_id') else project_id,
                parent_external_record_id=issue_data.get("parent_external_id"),
                parent_record_type=RecordType.TICKET if issue_data.get("parent_external_id") else None,
                version=version,
                mime_type=MimeTypes.BLOCKS.value,  # Use BLOCKS for blockgroups/blocks streaming
                weburl=record.weburl if hasattr(record, 'weburl') else None,
                source_created_at=issue_data["created_at"],
                source_updated_at=current_updated_at,
                created_at=issue_data["created_at"],
                updated_at=current_updated_at,
                preview_renderable=False,
                is_dependent_node=False,  # Tickets are not dependent
                parent_node_id=None,  # Tickets have no parent node
            )

            # Refresh issue links: _process_record deletes-and-recreates ALL link edges from
            # related_external_records, so omitting this on reindex wipes the ticket's links.
            issue_record.related_external_records = self._parse_issue_links(issue)

            # Permissions: empty list - records inherit project-level permissions via inherit_permissions=True
            permissions = []

            return (issue_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching issue {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_attachment(
        self, record: Record
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch attachment from source for reindexing."""
        try:
            # Load indexing filters if not already loaded (needed for reindexing context)
            if self.indexing_filters is None:
                _, self.indexing_filters = await load_connector_filters(
                    self.config_service,
                    "jira",
                    self.connector_id,
                    self.logger
                )

            # Extract attachment ID (remove "attachment_" prefix)
            external_id = record.external_record_id
            if external_id.startswith("attachment_"):
                attachment_id = external_id.replace("attachment_", "")
            else:
                attachment_id = external_id

            # Get parent issue ID (external)
            issue_id = record.parent_external_record_id if hasattr(record, 'parent_external_record_id') else None
            if not issue_id:
                self.logger.warning(f"Attachment {attachment_id} missing parent issue ID")
                return None

            # Get parent ticket's internal record ID
            async with self.data_store_provider.transaction() as tx_store:
                parent_ticket_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=issue_id
                )
            parent_node_id = parent_ticket_record.id if parent_ticket_record else None

            # Fetch issue to get attachment metadata
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_issue(
                issueIdOrKey=issue_id,
                expand=[]
            )

            if response.status in (
                HttpStatusCode.NOT_FOUND.value,
                HttpStatusCode.GONE.value,
                HttpStatusCode.BAD_REQUEST.value,
            ):
                self.logger.warning(f"Parent issue {issue_id} not found at source, may have been deleted")
                return None

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"Failed to fetch parent issue {issue_id}: HTTP {response.status}")
                return None

            issue_data = response.json()
            # Get issue key from the response (it's at the top level, not in fields)
            issue_key = issue_data.get("key")  # Fallback to None if key not found
            fields = issue_data.get("fields", {})
            attachments = fields.get("attachment", [])

            # Find the specific attachment
            attachment_data = None
            for att in attachments:
                if str(att.get("id")) == str(attachment_id):
                    attachment_data = att
                    break

            if not attachment_data:
                self.logger.warning(f"Attachment {attachment_id} not found in issue {issue_id}, may have been deleted")
                return None

            # Attachments have no 'updated' field — 'created' changes when the file is replaced.
            filename, mime_type, file_size, current_created_at = self._parse_attachment_metadata(attachment_data)

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at == current_created_at:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source")
                return None

            self.logger.info(f"🔄 Attachment {attachment_id} has changed at source")

            # Increment version
            version = record.version + 1 if hasattr(record, 'version') else 1

            # Construct web URL for attachment - use issue browse URL since attachments are visible there
            weburl = None
            if self.site_url and issue_key:
                weburl = f"{self.site_url}/browse/{issue_key}"

            # Get project ID from existing record
            project_id = record.external_record_group_id if hasattr(record, 'external_record_group_id') else ""

            # Create updated FileRecord using helper method (preserving record ID)
            # Skip filter check during reindexing to allow reindexing regardless of filter settings
            attachment_record = self._create_attachment_file_record(
                attachment_id=attachment_id,
                filename=filename,
                mime_type=mime_type,
                file_size=file_size,
                created_at=current_created_at,
                parent_issue_id=issue_id,
                parent_node_id=parent_node_id,
                project_id=project_id,
                weburl=weburl,
                record_id=record.id,
                version=version,
                skip_filter_check=True,
            )

            # Permissions: empty list - records inherit project-level permissions via inherit_permissions=True
            permissions = []

            return (attachment_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    # ============================================================================
    # Factory Method
    # ============================================================================

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        **kwargs,
    ) -> "BaseConnector":
        """Factory method to create JiraConnector instance"""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return JiraConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
