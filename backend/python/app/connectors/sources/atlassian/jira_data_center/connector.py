"""Jira Data Center connector — sync stack aligned with Jira Cloud;
"""

import asyncio
import base64
import re
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from logging import Logger
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse
from uuid import uuid4

import httpx  # type: ignore
from bs4 import BeautifulSoup
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
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO, IconPaths
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
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
from app.connectors.sources.atlassian.core.apps import JiraDataCenterApp
from app.connectors.sources.atlassian.core.oauth import OAUTH_JIRA_CONFIG_PATH
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
    get_epoch_timestamp_in_ms,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.jira.jira import JiraClient
from app.sources.external.jira.jira import JiraDataSource
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.streaming import create_stream_record_response


# Pagination/constants
DEFAULT_MAX_RESULTS: int = 50
BATCH_PROCESSING_SIZE: int = 100
USER_PAGE_SIZE: int = 50
# /user/list supports up to 2000; use a moderate page to cut round-trips vs USER_PAGE_SIZE.
USER_LIST_PAGE_SIZE: int = 100
GROUP_MEMBER_PAGE_SIZE: int = 50
GROUPS_PICKER_MAX: int = 1000  # maxResults for GET /rest/api/2/groups/picker
AUDIT_PAGE_SIZE: int = 500  # page size for GET /rest/auditing/1.0/events
DC_AUDIT_ISSUE_DELETED_ACTIONS: str = "Issue deleted,Sub-task deleted"
DC_AUDIT_ISSUE_CATEGORY: str = "issue"

# JQL query constants
ISSUE_SEARCH_FIELDS: list[str] = [
    "summary", "description", "status", "priority",
    "creator", "reporter", "assignee", "created", "updated",
    "issuetype", "project", "parent", "attachment", "security",
    "issuelinks",
]

DC_EPIC_LINK_FIELD_NAME = "Epic Link"
DC_EPIC_LINK_SCHEMA_CUSTOM = "com.pyxis.greenhopper.jira:gh-epic-link"


def _normalize_jira_dc_group_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a DC groups/picker row to ``{name, groupId}`` (name is the DC group key)."""
    name = raw.get("name")
    if not name:
        return None
    gid = raw.get("groupId") or raw.get("id") or name
    return {"name": str(name), "groupId": str(gid)}


def _application_role_groups_from_dc_role(role: dict[str, Any]) -> list[dict[str, str]]:
    """Map DC ``GET /rest/api/2/applicationrole`` ``groups`` strings to ``{name, groupId}``."""
    normalized: list[dict[str, str]] = []
    for group_name in role.get("groups") or []:
        if not isinstance(group_name, str) or not group_name.strip():
            continue
        name = group_name.strip()
        normalized.append({"groupId": name, "name": name})
    return normalized


def _parse_jira_dc_user_list_page(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Parse ``GET /rest/api/2/user/list`` JSON into ``(users, next_cursor)``."""
    if isinstance(payload, list):
        return [u for u in payload if isinstance(u, dict)], None
    if not isinstance(payload, dict):
        return [], None

    raw_users = payload.get("values")
    if raw_users is None:
        raw_users = payload.get("users")
    users = [u for u in raw_users if isinstance(u, dict)] if isinstance(raw_users, list) else []

    next_cursor = payload.get("nextCursor")
    if not next_cursor and isinstance(payload.get("nextPage"), str):
        next_page = payload["nextPage"]
        if "cursor=" in next_page:
            qs = parse_qs(urlparse(next_page).query)
            cursor_vals = qs.get("cursor") or []
            next_cursor = cursor_vals[0] if cursor_vals else None
        elif next_page and not next_page.startswith("http"):
            next_cursor = next_page

    if payload.get("isLast") is True:
        next_cursor = None

    return users, next_cursor if isinstance(next_cursor, str) and next_cursor else None


@(
    ConnectorBuilder("Jira Data Center")
    .in_group(AppGroups.ATLASSIAN.value)
    .with_description("Sync issues from Jira Data Center")
    .with_categories(["IT Service Management", "Storage"])
    .with_scopes([ConnectorScope.TEAM.value])
    .with_auth(
        [
            AuthBuilder.type(AuthType.API_TOKEN).fields(
                [
                    AuthField(
                        name="baseUrl",
                        display_name="Base URL",
                        placeholder="https://jira.company.com",
                        description="Root URL of your Jira Data Center instance",
                        field_type="URL",
                        required=True,
                        max_length=2000,
                        is_secret=False,
                    ),
                    AuthField(
                        name="apiToken",
                        display_name="Personal Access Token",
                        placeholder="your-personal-access-token",
                        description="Personal access token for your Jira Data Center instance.",
                        field_type="PASSWORD",
                        required=True,
                        max_length=2000,
                        is_secret=True,
                    ),
                ]
            ),
            AuthBuilder.type(AuthType.BASIC_AUTH).fields(
                [
                    AuthField(
                        name="baseUrl",
                        display_name="Base URL",
                        placeholder="https://jira.company.com",
                        description="Root URL of your Jira Server or Data Center instance",
                        field_type="URL",
                        required=True,
                        max_length=2000,
                        is_secret=False,
                    ),
                    AuthField(
                        name="username",
                        display_name="Username",
                        placeholder="svc-jira-sync",
                        description="Username for HTTP basic authentication to Jira",
                        field_type="TEXT",
                        required=True,
                        max_length=500,
                        is_secret=False,
                    ),
                    AuthField(
                        name="password",
                        display_name="Password",
                        placeholder="password or app-password",
                        description="Password for HTTP basic authentication to Jira",
                        field_type="PASSWORD",
                        required=True,
                        max_length=2000,
                        is_secret=True,
                    ),
                ]
            ),
        ]
    )
    .with_info(
        "Users with private email visibility on Jira Data Center are automatically resolved "
        "if they exist in your PipesHub directory or any other connected source. "
        "Setting email visibility to Public makes the initial sync faster."
        + "\n\n"
        + CONNECTOR_EMAIL_IDENTITY_INFO
    )
    .configure(
        lambda builder: builder.with_icon(IconPaths.connector_icon(Connectors.JIRA_DATA_CENTER.value))
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "Jira Server/DC — Personal access tokens (platform)",
                "https://developer.atlassian.com/server/jira/platform/personal-access-token",
                "setup",
            )
        )
        .add_documentation_link(
            DocumentationLink(
                "Jira Server/DC — Basic authentication (REST)",
                "https://developer.atlassian.com/server/jira/platform/basic-authentication/",
                "setup",
            )
        )
        .add_documentation_link(
            DocumentationLink(
                "Pipeshub Documentation",
                "https://docs.pipeshub.com/connectors/jira/jira-data-center",
                "pipeshub",
            )
        )
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
        .add_filter_field(
            FilterField(
                name="project_keys",
                display_name="Project keys",
                filter_type=FilterType.LIST,
                category=FilterCategory.SYNC,
                description="Optional: limit sync to these Jira project keys (comma-separated in UI values).",
                option_source_type=OptionSourceType.DYNAMIC,
            )
        )
        .add_filter_field(CommonFields.modified_date_filter("Filter issues by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter issues by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(
            FilterField(
                name="issues",
                display_name="Index issues",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Enable indexing of issues",
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name="issue_attachments",
                display_name="Index issue and comment attachments",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Enable indexing of issue attachments",
                default_value=True,
            )
        )
    )
    .build_decorator()
)
class JiraDataCenterConnector(BaseConnector):
    """Jira Data Center connector: sync orchestration aligned with Jira Cloud"""

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
            JiraDataCenterApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.external_client: JiraClient | None = None
        self.data_source: JiraDataSource | None = None
        self.site_url: str | None = None

        org_id = self.data_entities_processor.org_id
        self.issues_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )
        self.sync_filters = None
        self.indexing_filters = None
        self.value_mapper = ValueMapper()
        self._issue_attachments_cache: dict[str, list[dict[str, Any]]] = {}

        self._app_roles_forbidden: bool = False  # GET /applicationrole returned 403
        self._user_bulk_forbidden: bool = False  # bulk user list/search returned 401/403
        # True when bulk fell back to /user/search (may be incomplete on Jira 10+) —
        # reverse lookup must sweep all PipesHub candidates, not only bulk gaps.
        self._user_bulk_incomplete: bool = False
        # DC username (``name``) -> source_user_id (``key``); built during _fetch_users
        self._dc_name_to_source_id: dict[str, str] = {}
        # Epic Link field id: None = before init, "" = not found, else customfield id
        self._epic_link_field_id: str | None = None
        # Epic key -> numeric id; cleared at start of each project sync
        self._issue_key_to_id_cache: dict[str, str] = {}

    async def init(self) -> bool:
        try:
            config_path = OAUTH_JIRA_CONFIG_PATH.format(connector_id=self.connector_id)
            config = await self.config_service.get_config(config_path)
            auth_config = (config or {}).get("auth", {}) or {}
            # ``authType`` must be explicit. Default-to-API_TOKEN diverges from
            # ``JiraClient.build_from_services`` (which defaults to BEARER_TOKEN, only
            # valid for Cloud) and would cause client construction to raise after init
            # silently succeeded.
            raw_auth_type = auth_config.get("authType")
            if not raw_auth_type:
                self.logger.error(
                    "Jira Data Center connector %s: authType is required in connector auth config "
                    "(expected API_TOKEN or BASIC_AUTH)",
                    self.connector_id,
                )
                return False
            auth_type = str(raw_auth_type).strip().upper()
            if auth_type not in {"API_TOKEN", "BASIC_AUTH"}:
                self.logger.error(
                    "Jira Data Center connector %s: unsupported authType %s (expected API_TOKEN or BASIC_AUTH)",
                    self.connector_id,
                    auth_type,
                )
                return False

            base_url = (auth_config.get("baseUrl") or "").strip().rstrip("/")
            if not base_url:
                self.logger.error(
                    "Jira Data Center connector %s: baseUrl is required in connector auth config",
                    self.connector_id,
                )
                return False
            self.site_url = base_url

            client = await JiraClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.external_client = client
            self.data_source = JiraDataSource(client)
            self.logger.info(
                "Jira Data Center connector %s initialized (authType=%s)",
                self.connector_id,
                auth_type,
            )

            if self.created_by:
                try:
                    creator = await self.data_entities_processor.get_user_by_user_id(self.created_by)
                    if creator and getattr(creator, "email", None):
                        self.creator_email = creator.email
                except Exception as e:
                    self.logger.warning("Could not resolve creator email for created_by %s: %s", self.created_by, e)

            await self._discover_epic_link_field_id()

            return True
        except Exception as e:
            self.logger.error("Failed to initialize Jira Data Center connector: %s", e, exc_info=True)
            return False
    # -------------------------------------------------------------------------
    # HTTP client & datasource (no OAuth refresh — credentials from connector config)
    # -------------------------------------------------------------------------

    async def _get_fresh_datasource(self) -> JiraDataSource:
        """Return a ``JiraDataSource`` for this connector's ``JiraClient``.

        Auth may be **PAT** (``API_TOKEN`` without email): ``Authorization: Bearer <PAT>`` against
        the configured instance ``baseUrl`` (no Atlassian Cloud ``ex/jira`` proxy). **Basic** auth
        (``BASIC_AUTH``) uses ``Authorization: Basic`` with the configured username and password.
        There is no OAuth refresh path for this connector; if credentials or ``baseUrl`` change,
        run ``init()`` again to rebuild the client.
        """
        if not self.external_client:
            raise RuntimeError("Jira client not initialized. Call init() first.")
        return JiraDataSource(self.external_client)

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

        Supports:
        - project_keys: All available Jira projects

        Args:
            filter_key: Filter field name
            page: Page number (1-indexed)
            limit: Items per page
            search: Search text to filter project names/keys
            cursor: Not used (Jira uses startAt-based pagination)

        Returns:
            FilterOptionsResponse with options and pagination metadata
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
        """Populate project-key filter options using Data Center REST.

        Uses ``GET /rest/api/2/project`` (lists projects visible to the user). Unlike Cloud's
        ``GET /rest/api/3/project/search``, DC returns a **JSON array** with no embedded pagination
        or ``query`` parameter — search and paging are applied in-process.

        Reference: `Jira Data Center REST — project resource
        <https://developer.atlassian.com/server/jira/platform/rest/v11003/api-group-project/#api-rest-api-2-project-get>`_.
        """
        datasource = await self._get_fresh_datasource()

        start_at = (page - 1) * limit

        try:
            response = await datasource.list_projects_get_v2()

            if not response or response.status != HttpStatusCode.OK.value:
                raise RuntimeError(
                    f"Failed to fetch projects: HTTP {response.status if response else 'No response'}"
                )

            raw = self._safe_json_parse(response, "project list DC")
            if raw is None:
                raise RuntimeError("Failed to parse project list response")
            if not isinstance(raw, list):
                raise RuntimeError(
                    f"Unexpected project list shape from /rest/api/2/project: {type(raw).__name__}"
                )

            projects_all: list[dict[str, Any]] = [p for p in raw if isinstance(p, dict)]

            needle = (search or "").strip().lower()
            if needle:

                def _matches(proj: dict[str, Any]) -> bool:
                    key = str(proj.get("key") or "").lower()
                    name = str(proj.get("name") or "").lower()
                    return needle in key or needle in name

                projects_filtered = [p for p in projects_all if _matches(p)]
            else:
                projects_filtered = projects_all

            page_slice = projects_filtered[start_at : start_at + limit]
            has_more = start_at + len(page_slice) < len(projects_filtered)

            options = [
                FilterOption(
                    id=str(project.get("key", "")),
                    label=f"{project.get('name', '')} ({project.get('key', '')})",
                )
                for project in page_slice
                if project.get("key") and project.get("name")
            ]

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=None,
            )
        except Exception as e:
            self.logger.error(f"❌ Error fetching projects: {e}")
            raise RuntimeError(f"Failed to fetch project options: {str(e)}")

    # ============================================================================
    # Sync Orchestration
    # ============================================================================

    async def run_sync(self) -> None:
        """
        Run sync of Jira projects and issues - only new/updated tickets
        """
        try:
            if not self.data_source:
                # ``init()`` returns False (rather than raising) on missing /
                # invalid auth config. Without this check we would proceed
                # against a None ``self.data_source`` and the first datasource
                # call would surface a ``ValueError("DataSource not
                # initialized")``, which is harder to diagnose than a clear
                # "init failed" at the top of the orchestration.
                if not await self.init():
                    raise RuntimeError(
                        f"Jira Data Center connector {self.connector_id} init failed; "
                        "check auth configuration (authType / baseUrl / credentials)"
                    )

            # Load sync and indexing filters (loaded in run_sync to ensure latest values)
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "jira",
                self.connector_id,
                self.logger
            )

            users = await self.data_entities_processor.get_all_active_users()

            if not users:
                self.logger.info("ℹ️ No users found")
                return

            # Fetch and sync users
            jira_users = await self._fetch_users()
            if jira_users:
                await self.data_entities_processor.on_new_app_users(jira_users)
                self.logger.info(f"👥 Synced {len(jira_users)} Jira users")

            # Fetch and sync user groups (returns mapping for role resolution)
            groups_members_map = await self._sync_user_groups(jira_users)

            app_roles_mapping = await self._fetch_application_roles_to_groups_mapping()

            # Get project_keys filter if configured (to fetch only those projects)
            allowed_keys = None
            project_keys_operator = None
            if self.sync_filters:
                project_keys_filter = self.sync_filters.get(SyncFilterKey.PROJECT_KEYS)
                if project_keys_filter:
                    allowed_keys = project_keys_filter.get_value(default=[])
                    project_keys_operator = project_keys_filter.get_operator()
                    if allowed_keys:
                        # Extract operator value string (handles both enum and string)
                        operator_value = project_keys_operator.value if hasattr(project_keys_operator, 'value') else str(project_keys_operator) if project_keys_operator else "in"
                        action = "Excluding" if operator_value == "not_in" else "Including"
                        self.logger.info(f"🔍 Project keys filter: {action} projects: {allowed_keys}")
                    else:
                        # Empty list = no restriction (same as Cloud); sync all visible projects
                        allowed_keys = None
                        self.logger.info("🔍 Project keys filter is empty — syncing all visible projects (DC)")
            # Fetch projects
            projects, raw_projects = await self._fetch_projects(
                allowed_keys, project_keys_operator, jira_users,
                app_roles_mapping=app_roles_mapping,
            )

            # Sync project roles BEFORE RecordGroups
            project_keys_for_roles = [proj.short_name for proj, _ in projects]
            await self._sync_project_roles(project_keys_for_roles, jira_users, groups_members_map)

            # Sync project lead roles
            await self._sync_project_lead_roles(raw_projects, jira_users)

            # Create RecordGroups and its permissions
            await self.data_entities_processor.on_new_record_groups(projects)

            # Sync issues for all projects
            last_sync_time = await self._get_issues_sync_checkpoint()
            sync_stats = await self._sync_all_project_issues(projects, jira_users, last_sync_time)

            await self._update_issues_sync_checkpoint(sync_stats, len(projects))

            await self._handle_issue_deletions(last_sync_time)

            self.logger.info(
                f"✅ Jira sync completed. Total: {sync_stats['total_synced']} issues "
                f"(New: {sync_stats['new_count']}, Updated: {sync_stats['updated_count']})"
            )

        except Exception as e:
            self.logger.error(f"❌ Error during Jira sync: {e}", exc_info=True)
            raise

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
        except Exception:
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

    async def _handle_issue_deletions(self, global_last_sync_time: Optional[int]) -> None:
        """Reconcile deleted issues via ``GET /rest/auditing/1.0/events`` (separate sync checkpoint)."""
        audit_sync_key = "issues_audit_deletions"

        try:
            audit_sync_point_data = await self.issues_sync_point.read_sync_point(audit_sync_key)
            audit_last_sync_time = audit_sync_point_data.get("last_sync_time") if audit_sync_point_data else None
        except Exception:
            audit_last_sync_time = None

        deletion_check_time = audit_last_sync_time or global_last_sync_time
        if not deletion_check_time:
            return

        try:
            await self._detect_and_handle_deletions(deletion_check_time)
        except Exception as e:
            self.logger.error(
                "❌ Audit deletion pass failed (window from %s): %s",
                deletion_check_time, e,
                exc_info=True,
            )
            return

        try:
            await self.issues_sync_point.update_sync_point(
                audit_sync_key,
                {"last_sync_time": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(
                "⚠️ Failed to advance audit deletion checkpoint: %s", e
            )

    # ============================================================================
    # Deletion Handling (DC audit log)
    # ============================================================================

    @staticmethod
    def _issue_key_from_auditing_event(entity: dict[str, Any]) -> str | None:
        """Extract an issue key from a ``/rest/auditing/1.0/events`` row."""
        for obj in entity.get("affectedObjects") or []:
            if not isinstance(obj, dict):
                continue
            if (obj.get("type") or "").upper() == "ISSUE":
                name = obj.get("name")
                if name:
                    return str(name)
        return None

    async def _detect_and_handle_deletions(self, last_sync_time: int) -> int:
        """Fetch deleted issue keys from the audit log and delete each one flat."""
        self.logger.info("🔍 Checking for deleted issues via Jira DC audit log...")

        deleted_issue_keys = await self._fetch_deleted_issues_from_audit(last_sync_time)
        if not deleted_issue_keys:
            self.logger.info("ℹ️ No deleted issues found in DC audit log")
            return 0

        deleted_count = 0
        for issue_key in deleted_issue_keys:
            try:
                await self._handle_deleted_issue(issue_key)
                deleted_count += 1
            except Exception as e:
                self.logger.error("❌ Error handling deleted issue %s: %s", issue_key, e)

        return deleted_count

    async def _fetch_deleted_issues_from_audit(self, last_sync_time: int) -> list[str]:
        """Return issue keys deleted since ``last_sync_time`` (admin-only auditing API)."""
        from_date = datetime.fromtimestamp(
            last_sync_time / 1000, tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        deleted_issue_keys: list[str] = []
        offset = 0
        limit = AUDIT_PAGE_SIZE

        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_auditing_events_v1(
                    offset=offset,
                    limit=limit,
                    from_=from_date,
                    to=to_date,
                    actions=DC_AUDIT_ISSUE_DELETED_ACTIONS,
                    categories=DC_AUDIT_ISSUE_CATEGORY,
                )

                if response.status in (
                    HttpStatusCode.UNAUTHORIZED.value,
                    HttpStatusCode.FORBIDDEN.value,
                ):
                    self.logger.warning(
                        "⚠️ DC /rest/auditing/1.0/events returned %s — configuring "
                        "user lacks Jira System Administrator. Skipping deletion "
                        "reconciliation.",
                        response.status,
                    )
                    return []

                if response.status == HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(
                        "⚠️ DC /rest/auditing/1.0/events unavailable (404). "
                        "Skipping deletion reconciliation."
                    )
                    return []

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(
                        "⚠️ Failed to fetch DC auditing events (HTTP %s): %s",
                        response.status, response.text(),
                    )
                    return []

                audit_data = response.json() or {}
                entities = audit_data.get("entities") or []
                if not entities:
                    break

                for entity in entities:
                    issue_key = self._issue_key_from_auditing_event(entity)
                    if issue_key:
                        deleted_issue_keys.append(issue_key)
                        self.logger.debug(
                            "Audit: Issue %s deleted at %s",
                            issue_key, entity.get("timestamp"),
                        )

                paging = audit_data.get("pagingInfo") or {}
                if paging.get("lastPage", True):
                    break

                next_offset = paging.get("nextPageOffset")
                if next_offset is not None:
                    offset = int(next_offset)
                else:
                    offset += len(entities)

            except Exception as e:
                self.logger.error(
                    "❌ Error fetching DC auditing events at offset %s: %s",
                    offset, e,
                )
                return list(dict.fromkeys(deleted_issue_keys))

        return list(dict.fromkeys(deleted_issue_keys))

    async def _handle_deleted_issue(self, issue_key: str) -> None:
        """Delete local records for one issue confirmed gone in Jira (flat)."""
        try:
            self.logger.info(f"🗑️ Handling deletion of issue {issue_key}")

            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_issue_v2(issueIdOrKey=issue_key)
                if response.status == HttpStatusCode.OK.value:
                    self.logger.warning(
                        "⚠️ Issue %s still exists in Jira (audit row was a "
                        "move/rename, not a deletion) — skipping",
                        issue_key,
                    )
                    return
            except Exception:
                pass

            async with self.data_store_provider.transaction() as tx_store:
                issue_record = await tx_store.get_record_by_issue_key(
                    connector_id=self.connector_id,
                    issue_key=issue_key,
                )

                if not issue_record:
                    self.logger.warning(
                        "⚠️ Issue %s not found in database "
                        "(already deleted or never synced?)",
                        issue_key,
                    )
                    return

                issue_id = issue_record.external_record_id
                record_internal_id = issue_record.id

                self.logger.info(
                    "✅ Found issue %s internal=%s external=%s",
                    issue_key, record_internal_id, issue_id,
                )

                attachment_count = await self._delete_direct_attachment_records(
                    issue_id, tx_store,
                )

                await tx_store.delete_records_and_relations(
                    record_key=record_internal_id,
                    hard_delete=True,
                )

                self.logger.info(
                    "🗑️ Deleted issue %s (%s direct attachments)",
                    issue_key, attachment_count,
                )

        except Exception as e:
            self.logger.error(
                "❌ Error handling deleted issue %s: %s", issue_key, e, exc_info=True,
            )

    async def _delete_direct_attachment_records(
        self,
        parent_issue_id: str,
        tx_store,
    ) -> int:
        """Delete direct FILE children (attachments) of ``parent_issue_id``."""
        try:
            deleted_count = 0
            child_records = await tx_store.get_records_by_parent(
                connector_id=self.connector_id,
                parent_external_record_id=parent_issue_id,
                record_type=RecordType.FILE.value,
            )

            for record in child_records:
                await tx_store.delete_records_and_relations(
                    record_key=record.id,
                    hard_delete=True,
                )
                deleted_count += 1
                self.logger.debug(
                    "  Deleted attachment %s", record.external_record_id,
                )

            return deleted_count

        except Exception as e:
            self.logger.error(
                "❌ Error deleting attachments for issue %s: %s",
                parent_issue_id, e,
            )
            return 0

    # ============================================================================
    # User & Group Management
    # ============================================================================

    async def _fetch_users(self) -> list[AppUser]:
        """
        Fetch and resolve active Jira DC users:

        1. Bulk enumerate via ``/user/list`` (preferred) or ``/user/search`` fallback
        2. Resolve visible-email + cached users in memory
        3. Reverse-lookup remaining PipesHub emails when bulk left gaps
           (hidden emails, incomplete search, or bulk forbidden)

        Identifier is ``key`` (immutable), falling back to ``accountId`` / ``name``.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        self._dc_name_to_source_id = {}

        cached_app_users = await self.data_entities_processor.get_all_app_users(self.connector_id)
        pipeshub_users = await self.data_entities_processor.get_all_active_users()

        cached_key_to_email: dict[str, str] = {
            u.source_user_id: u.email
            for u in cached_app_users
            if u.source_user_id and u.email
        }
        pipeshub_emails: set[str] = {
            u.email.lower() for u in pipeshub_users if u.email
        }

        raw_jira_users = await self._fetch_all_jira_users_bulk()

        all_active_user_keys: set[str] = set()
        visible_email_map: dict[str, str] = {}
        key_to_display: dict[str, str] = {}

        for user in raw_jira_users:
            if not user.get("active", True):
                continue
            user_key = user.get("accountId") or user.get("key") or user.get("name")
            if not user_key:
                continue

            all_active_user_keys.add(user_key)
            key_to_display[user_key] = user.get("displayName", "")
            username = user.get("name")
            if username:
                self._dc_name_to_source_id[username.lower()] = user_key
            email = user.get("emailAddress")
            if email:
                visible_email_map[email.lower()] = user_key

        self.logger.info(
            f"👥 Jira DC bulk: {len(all_active_user_keys)} active users, "
            f"{len(visible_email_map)} with visible email"
        )

        resolved: dict[str, AppUser] = {}
        org_id = self.data_entities_processor.org_id

        for email_lower, user_key in visible_email_map.items():
            resolved[user_key] = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=user_key,
                org_id=org_id,
                email=email_lower,
                full_name=key_to_display.get(user_key, email_lower),
                is_active=True,
            )

        for user_key, email in cached_key_to_email.items():
            if user_key in all_active_user_keys and user_key not in resolved:
                resolved[user_key] = AppUser(
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_user_id=user_key,
                    org_id=org_id,
                    email=email,
                    full_name=key_to_display.get(user_key, email),
                    is_active=True,
                )

        unresolved_user_keys = all_active_user_keys - resolved.keys()
        unresolved_count = len(unresolved_user_keys)
        candidate_emails = pipeshub_emails - {u.email.lower() for u in resolved.values()}
        candidate_count = len(candidate_emails)

        self.logger.info(
            f"👥 Resolution state: {len(resolved)} resolved, "
            f"{unresolved_count} unresolved Jira users, "
            f"{candidate_count} PipesHub candidate emails"
        )

        needs_reverse = (
            unresolved_count > 0
            or self._user_bulk_forbidden
            or self._user_bulk_incomplete
        )
        if needs_reverse and candidate_count > 0:
            new_found = await self._resolve_private_email_users(
                candidate_emails, unresolved_user_keys, resolved
            )
            self.logger.info(f"👥 Reverse lookup resolved {new_found} additional users")
        elif not needs_reverse:
            self.logger.info("👥 All Jira DC users resolved, no reverse lookup needed")

        self.logger.info(f"👥 Total: {len(resolved)} Jira DC AppUsers resolved")
        return list(resolved.values())

    async def _fetch_users_via_list(self) -> list[dict[str, Any]] | None:
        """
        Cursor-paginated fetch via ``GET /rest/api/2/user/list``.

        Returns users on success, ``None`` when the endpoint is unavailable
        (caller falls back to ``/user/search``).
        """
        users: list[dict[str, Any]] = []
        cursor: str | None = None
        # DC has no OAuth refresh — one datasource for the whole pagination loop.
        datasource = await self._get_fresh_datasource()

        while True:
            response = await datasource.get_user_list_v2(
                cursor=cursor,
                maxResults=USER_LIST_PAGE_SIZE,
                includeInactive=False,
            )

            if response.status in (
                HttpStatusCode.NOT_FOUND.value,
                HttpStatusCode.METHOD_NOT_ALLOWED.value,
            ):
                self.logger.info(
                    "ℹ️ DC /user/list returned %s — falling back to /user/search",
                    response.status,
                )
                return None

            if response.status != HttpStatusCode.OK.value:
                if response.status in (
                    HttpStatusCode.UNAUTHORIZED.value,
                    HttpStatusCode.FORBIDDEN.value,
                ):
                    self._user_bulk_forbidden = True
                    self.logger.warning(
                        "⚠️ DC /user/list returned %s — configuring user lacks "
                        "'Browse users and groups'. Returning %s users collected "
                        "so far; user resolution will degrade to PipesHub-directory "
                        "reverse lookup only.",
                        response.status, len(users),
                    )
                    return users
                raise Exception(f"Failed to fetch users via /user/list: {response.text()}")

            payload = self._safe_json_parse(response, "users list")
            if payload is None:
                self.logger.error("Failed to parse /user/list response, stopping list fetch")
                break

            batch_users, next_cursor = _parse_jira_dc_user_list_page(payload)
            if not batch_users and not next_cursor:
                break

            users.extend(batch_users)
            if not next_cursor:
                break
            cursor = next_cursor

        self.logger.info("👥 Jira DC /user/list returned %s users", len(users))
        return users

    async def _fetch_users_via_search(self) -> list[dict[str, Any]]:
        """
        Paginated fetch via ``GET /rest/api/2/user/search?username=.``.

        Pages until an empty or short page. Dedupes by user key so a stuck
        ``startAt`` (Jira 10+ JRASERVER-78660) cannot loop forever.
        """
        users: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        start_at = 0
        datasource = await self._get_fresh_datasource()

        while True:
            response = await datasource.get_user_search_v2(
                username=".",
                includeInactive=False,
                maxResults=USER_PAGE_SIZE,
                startAt=start_at,
            )

            if response.status != HttpStatusCode.OK.value:
                if response.status in (
                    HttpStatusCode.UNAUTHORIZED.value,
                    HttpStatusCode.FORBIDDEN.value,
                ):
                    self._user_bulk_forbidden = True
                    self.logger.warning(
                        "⚠️ DC /user/search returned %s — configuring user lacks "
                        "'Browse users and groups'. Returning %s users collected "
                        "so far; user resolution will degrade to PipesHub-directory "
                        "reverse lookup only.",
                        response.status, len(users),
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
                batch_users = (
                    users_batch.get("values", []) if isinstance(users_batch, dict) else []
                )

            if not batch_users:
                break

            new_in_page = 0
            for user in batch_users:
                user_key = user.get("accountId") or user.get("key") or user.get("name")
                if user_key and user_key in seen_keys:
                    continue
                if user_key:
                    seen_keys.add(user_key)
                users.append(user)
                new_in_page += 1

            if new_in_page == 0:
                self.logger.warning(
                    "⚠️ DC /user/search returned no new users at startAt=%s "
                    "(collected %s). Stopping pagination.",
                    start_at, len(users),
                )
                break

            if len(batch_users) < USER_PAGE_SIZE:
                break

            start_at += USER_PAGE_SIZE

        self.logger.info("👥 Jira DC /user/search returned %s users", len(users))
        return users

    async def _fetch_all_jira_users_bulk(self) -> list[dict[str, Any]]:
        """
        Fetch Jira DC users for sync matching.

        Prefers ``GET /rest/api/2/user/list`` (cursor pagination). Falls back to
        ``GET /rest/api/2/user/search?username=.`` when ``/list`` is unavailable;
        search results are marked incomplete so reverse lookup can fill gaps.

        Endpoint behaviour by Jira DC/Server version (JRASERVER-78660):

        - ``/user/list`` — full directory enumeration (cursor pagination, up to
          2000/page). Available on Jira 10.3.13+ (back-ported to the 10.3 LTS) and
          11.0.0+. Returns 404 on 8.x, 9.x, 10.0–10.3.12, and 10.4+ feature releases
          that predate the back-port; those fall back to ``/user/search``.

        - ``/user/search`` — relevance/substring endpoint, not a documented
          directory-enumeration API. ``username="."`` matches against name /
          username / email, and in practice nearly every user matches (emails
          contain a ``.``), so it usually returns the whole directory. But on
          Jira 10+ it is hard-capped at the first 100 results (``startAt`` cannot
          page past); on 9.x and below ``startAt`` paginates normally (up to 1000
          per request). Because completeness is not guaranteed by contract (and the
          100-cap actively truncates on Jira 10+), this path sets
          ``_user_bulk_incomplete`` and the caller sweeps PipesHub candidate emails
          via per-email reverse lookup to fill any gap.
        """
        self._user_bulk_forbidden = False
        self._user_bulk_incomplete = False

        list_users = await self._fetch_users_via_list()
        if list_users is not None:
            return list_users

        self._user_bulk_incomplete = True
        self.logger.info(
            "👥 Using /user/search for bulk users (incomplete on Jira 10+; "
            "reverse lookup will sweep PipesHub candidates)"
        )
        return await self._fetch_users_via_search()

    async def _resolve_private_email_users(
        self,
        candidate_emails: set[str],
        unresolved_user_keys: set[str],
        resolved: dict[str, "AppUser"],
        name_to_source_id: dict[str, str] | None = None,
    ) -> int:
        """
        Reverse-lookup PipesHub emails against Jira DC to resolve hidden-email users.
        Uses GET /rest/api/2/user/search?username=<email> for each candidate.
        Bounded concurrency and early termination.
        Returns the number of newly resolved users.
        """
        unresolved_count = len(unresolved_user_keys)
        new_found = 0
        semaphore = asyncio.Semaphore(10)
        datasource = await self._get_fresh_datasource()

        async def try_resolve_email(email: str) -> Optional[tuple[str, str, str]]:
            """Returns (user_key, email, displayName) if found, else None."""
            async with semaphore:
                try:
                    response = await datasource.get_user_search_v2(
                        username=email,
                        maxResults=50,
                    )

                    if response.status != HttpStatusCode.OK.value:
                        return None

                    results = self._safe_json_parse(response, f"user_search({email})")
                    if not results or not isinstance(results, list):
                        return None

                    # ``username=<email>`` is a substring match over name/username/
                    # email and can return several users, so don't trust results[0]:
                    # pick the exact email match. Otherwise trust a lone hit only
                    # when its email is hidden (absent) — a lone hit with a visible
                    # *mismatching* email is a fuzzy name/username match, not us.
                    matches = [u for u in results if isinstance(u, dict)]
                    user = next(
                        (u for u in matches if (u.get("emailAddress") or "").lower() == email.lower()),
                        None,
                    )
                    if user is None:
                        if len(matches) != 1:
                            return None
                        lone_email = matches[0].get("emailAddress")
                        if lone_email and lone_email.lower() != email.lower():
                            return None
                        user = matches[0]
                    user_key = user.get("accountId") or user.get("key") or user.get("name")
                    if not user_key:
                        return None
                    username = user.get("name")
                    if username:
                        self._dc_name_to_source_id[username.lower()] = user_key
                    display_name = user.get("displayName") or email
                    return (user_key, email, display_name)
                except Exception as e:
                    self.logger.debug(f"⚠️ Reverse lookup failed for {email}: {e}")
                    return None

        batch_size = 20
        email_list = list(candidate_emails)
        # When bulk was forbidden or incomplete, ``unresolved_count`` only reflects
        # the (partial) bulk set — disable early-exit and sweep all candidates.
        skip_early_exit = self._user_bulk_forbidden or self._user_bulk_incomplete

        for i in range(0, len(email_list), batch_size):
            if not skip_early_exit and new_found >= unresolved_count:
                break

            batch = email_list[i:i + batch_size]
            tasks = [try_resolve_email(email) for email in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception) or result is None:
                    continue
                user_key, email, display_name = result
                if user_key not in resolved:
                    resolved[user_key] = AppUser(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_id=user_key,
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
        Fetch all application roles and their associated groups via
        ``GET /rest/api/2/applicationrole`` (Data Center).
        Always fetches fresh data from the API so that group membership
        changes in Jira are picked up on every sync.
        """
        mapping: dict[str, list[dict[str, str]]] = {}
        self._app_roles_forbidden = False

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_all_application_roles_v2()

            if response.status != HttpStatusCode.OK.value:
                if response.status == HttpStatusCode.FORBIDDEN.value:
                    self._app_roles_forbidden = True
                    self.logger.warning(
                        "⚠️ Application roles API returned 403 — configuring user is not a Jira admin. "
                        "Projects whose permission scheme uses applicationRole holders will "
                        "grant the configuring user direct access instead."
                    )
                else:
                    self.logger.warning(
                        "⚠️ Failed to fetch application roles (HTTP %s)", response.status
                    )
                return {}

            roles_data = response.json()
            if not isinstance(roles_data, list):
                roles_data = []

            for role in roles_data:
                if not isinstance(role, dict):
                    continue
                role_key = role.get("key")
                if not role_key:
                    continue

                normalized = _application_role_groups_from_dc_role(role)
                if normalized:
                    mapping[role_key] = normalized
                    self.logger.debug(
                        "ApplicationRole '%s' → %s groups", role_key, len(normalized),
                    )

            self.logger.info(f"🔐 Fetched {len(mapping)} application roles with group mappings")

        except Exception as e:
            self.logger.error(f"❌ Error fetching application roles: {e}", exc_info=True)

        return mapping

    def _fallback_permissions_for_forbidden_scheme(
        self,
        project_key: str,
        status: int,
        stage: str,
    ) -> list[Permission]:
        """Build a single-user BROWSE permission for the configuring user when
        the permission-scheme endpoints return 401/403 for this project.

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
        user_by_key: dict[str, "AppUser"] = None
    ) -> list[Permission]:
        """
        Fetch permission holders for a project from its Permission Scheme (Data Center).

        Two calls, uniform across builds: ``GET /project/{key}/permissionscheme``
        (no expand) for the scheme id, then ``GET /permissionscheme/{schemeId}/permission``
        for the grants. ``?expand=all`` on the project endpoint is avoided because it
        500s on some DC/Server builds (internal RequestScope NPE from lazy holder
        expansion); the standalone grants endpoint expands no holders inline.

        Permission Schemes grant permissions (like BROWSE_PROJECTS) through different holder types:
        - group: Direct group permissions (e.g., "jira-software-users")
        - applicationRole: Product access (e.g., "jira-software") - resolved to associated groups
        - user: Individual user permissions (by accountId/email)
        - anyone: All authenticated users (org-level access)
        - projectRole: Project-specific roles (e.g., "Administrators", "Developers") inside that user or groups in role
        - projectLead: The project's designated lead user
        - sd.customer.portal.only: JSM portal customers (external users)
        - groupCustomField/userCustomField: Dynamic permissions based on issue fields

        """
        permissions: list[Permission] = []

        try:
            datasource = await self._get_fresh_datasource()

            # Resolve the scheme in two steps instead of one ``?expand=all`` call.
            # ``GET /project/{key}/permissionscheme?expand=all`` 500s on some
            # DC/Server builds (internal RequestScope NPE — holders are expanded
            # lazily during serialization, after the request scope is torn down).
            # A bare scheme lookup + the standalone grants endpoint expands no
            # holders inline, so it works uniformly across builds.

            # Step 1: the scheme id assigned to this project (no expand).
            scheme_response = await datasource.get_assigned_permission_scheme_v2(
                projectKeyOrId=project_key,
            )

            if scheme_response.status != HttpStatusCode.OK.value:
                # ``GET /project/{key}/permissionscheme`` requires *Administer
                # Projects* (or global *Administer Jira*). Non-admin sync users
                # get a 401/403 with ``"You cannot edit the configuration of
                # this project."`` — same shape as the applicationroles 403
                # handled in ``_fetch_application_roles_to_groups_mapping``.
                # Mirror that fallback: rather than silently dropping all
                # BROWSE holders (which leaves the project indexed with empty
                # ACLs), grant the configuring user direct BROWSE so they can
                # at least see their own project content.
                if scheme_response.status in (
                    HttpStatusCode.UNAUTHORIZED.value,
                    HttpStatusCode.FORBIDDEN.value,
                ):
                    return self._fallback_permissions_for_forbidden_scheme(
                        project_key=project_key,
                        status=scheme_response.status,
                        stage="permission scheme",
                    )
                self.logger.warning(f"⚠️ Failed to fetch permission scheme for {project_key}: {scheme_response.text()}")
                return []

            scheme_id = scheme_response.json().get("id")
            if not scheme_id:
                self.logger.warning(
                    "⚠️ Permission scheme for %s has no id — cannot fetch grants",
                    project_key,
                )
                return []

            # Step 2: grants from the standalone endpoint. No expand — the grant
            # ``holder.parameter`` (group name / user key / role id) is always
            # present, user emails resolve via ``user_by_key``, and expanding
            # holders inline is what triggers the NPE above.
            grants_response = await datasource.get_permission_scheme_grants_v2(
                schemeId=scheme_id,
            )

            if grants_response.status != HttpStatusCode.OK.value:
                if grants_response.status in (
                    HttpStatusCode.UNAUTHORIZED.value,
                    HttpStatusCode.FORBIDDEN.value,
                ):
                    return self._fallback_permissions_for_forbidden_scheme(
                        project_key=project_key,
                        status=grants_response.status,
                        stage=f"permission grants (scheme {scheme_id})",
                    )
                self.logger.warning(
                    "⚠️ Failed to fetch permission grants for scheme %s: %s",
                    scheme_id,
                    grants_response.text(),
                )
                return []

            permission_grants = grants_response.json().get("permissions", [])
            if not isinstance(permission_grants, list):
                permission_grants = []

            # Step 3: Filter for BROWSE_PROJECTS permission (determines who can see the project)
            relevant_permission_types = ["BROWSE_PROJECTS"]

            seen_holders = set()

            for grant in permission_grants:
                permission_name = grant.get("permission")

                if permission_name not in relevant_permission_types:
                    continue

                holder = grant.get("holder", {})
                holder_type = holder.get("type")
                holder_param = holder.get("parameter")
                holder_value = holder.get("value")

                # Create unique key for deduplication
                holder_key = f"{holder_type}:{holder_value or holder_param}"
                if holder_key in seen_holders:
                    continue
                seen_holders.add(holder_key)

                # Process different holder types and create Permission objects
                if holder_type == "group":
                    # DC uses ``parameter``; Cloud often uses ``value`` — accept either.
                    group_id = holder_param or holder_value
                    if group_id:
                        permissions.append(Permission(
                            entity_type=EntityType.GROUP,
                            external_id=group_id,
                            type=PermissionType.READ
                        ))

                elif holder_type == "applicationRole":
                    role_key = holder_param

                    if role_key and app_roles_mapping and role_key in app_roles_mapping:
                        role_groups = app_roles_mapping[role_key]
                        for group_info in role_groups:
                            # Match the convention in ``_normalize_jira_dc_group_row``:
                            # use ``groupId`` when the server returned a distinct id,
                            # otherwise fall back to ``name``. This keeps the Permission's
                            # ``external_id`` aligned with ``AppUserGroup.source_user_group_id``
                            # so ``on_new_record_groups`` can resolve the edge.
                            group_key_value = group_info.get("groupId") or group_info.get("name")
                            if group_key_value:
                                group_key = f"group:{group_key_value}"
                                if group_key not in seen_holders:
                                    seen_holders.add(group_key)
                                    permissions.append(Permission(
                                        entity_type=EntityType.GROUP,
                                        external_id=group_key_value,
                                        type=PermissionType.READ
                                    ))
                    elif not role_key:
                        # Bare applicationRole (no parameter) = "any licensed user"
                        permissions.append(Permission(
                            entity_type=EntityType.ORG,
                            external_id="all_licensed_users",
                            type=PermissionType.READ
                        ))
                    elif self._app_roles_forbidden and self.creator_email:
                        # API returned 403 — can't resolve role to groups,
                        # grant only the configuring user instead of over-granting to ORG
                        user_key = f"user:{self.creator_email.lower()}"
                        if user_key not in seen_holders:
                            seen_holders.add(user_key)
                            permissions.append(Permission(
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
                    # holder_param is the user key; resolve via AppUser map first, fall back to email
                    user_data = holder.get("user", {})
                    user_email = user_data.get("emailAddress")

                    resolved_email = None
                    if user_by_key and holder_param in user_by_key:
                        resolved_email = user_by_key[holder_param].email
                    elif user_email:
                        resolved_email = user_email

                    if resolved_email:
                        permissions.append(Permission(
                            entity_type=EntityType.USER,
                            email=resolved_email,
                            type=PermissionType.READ
                        ))
                    else:
                        self.logger.debug(f"  {project_key}: User permission skipped - cannot resolve key '{holder_param}'")

                elif holder_type == "anyone":
                    # All authenticated users have access handle public condition
                    permissions.append(Permission(
                        entity_type=EntityType.ORG,
                        external_id="anyone_authenticated",
                        type=PermissionType.READ
                    ))

                elif holder_type == "projectRole":
                    project_role = holder.get("projectRole", {})
                    role_name = project_role.get("name", f"Role_{holder_param}")
                    role_id = holder_param or project_role.get("id")

                    if role_name == "atlassian-addons-project-access":
                        continue

                    permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        external_id=f"{project_key}_{role_id}",
                        type=PermissionType.READ
                    ))

                elif holder_type == "sd.customer.portal.only":
                    # JSM Service Desk customers (portal access)
                    # These are external customers who only access via the service desk portal
                    # Their access is limited to their own tickets through the portal UI
                    self.logger.debug(f"  {project_key}: Skipping JSM portal customers (external users, not synced)")

                elif holder_type == "projectLead":
                    permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        external_id=f"{project_key}_projectLead",
                        type=PermissionType.READ
                    ))

                elif holder_type in ("groupCustomField", "userCustomField"):
                    continue

                else:
                    self.logger.warning(f"⚠️  {project_key}: Unknown holder type '{holder_type}' with param '{holder_param}' - skipping")

            return permissions

        except Exception as e:
            self.logger.error(f"❌ Error fetching permission scheme for project {project_key}: {e}", exc_info=True)
            return []

    async def _sync_user_groups(self, jira_users: list[AppUser]) -> dict[str, list[AppUser]]:
        """
        Sync user groups and return a mapping of group_id/name -> list of AppUser members.
        This mapping is used to resolve group members for project roles.
        """
        try:
            self.logger.info("🚀 Starting Jira user group synchronization")

            # Fetch all groups
            groups = await self._fetch_groups()
            if not groups:
                self.logger.info("ℹ️ No groups found in Jira")
                return {}

            self.logger.info(f"👥 Found {len(groups)} groups. Fetching members...")

            # Create user_key -> AppUser lookup for efficient matching
            user_by_key = {user.source_user_id: user for user in jira_users if user.source_user_id}

            user_groups_batch = []
            # Mapping: group_id -> members, group_name -> members (for role actor lookup)
            groups_members_map: dict[str, list[AppUser]] = {}

            for group in groups:
                try:
                    group_id = group.get("groupId")
                    group_name = group.get("name")

                    if not group_id or not group_name:
                        continue

                    self.logger.debug(f"Processing group: {group_name} ({group_id})")

                    # Create AppUserGroup (always create, even if no members)
                    user_group = AppUserGroup(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_group_id=group_id,
                        name=group_name,
                        org_id=self.data_entities_processor.org_id,
                        description=f"Jira user group: {group_name}"
                    )

                    # Fetch member keys for this group
                    member_keys = await self._fetch_group_members(group_id, group_name)

                    # Map member keys to AppUser objects
                    app_users = []
                    skipped_members = 0
                    if member_keys:
                        for user_key in member_keys:
                            user = user_by_key.get(user_key)
                            if user:
                                app_users.append(user)
                            else:
                                skipped_members += 1

                    if skipped_members:
                        self.logger.debug(
                            "Group %s: %s member(s) skipped (no AppUser; hidden email or not in PipesHub)",
                            group_name,
                            skipped_members,
                        )

                    # Store mapping by both group_id and group_name for flexible lookup
                    groups_members_map[group_id] = app_users
                    groups_members_map[group_name] = app_users

                    # Add group to batch (with or without members)
                    user_groups_batch.append((user_group, app_users))

                    if app_users:
                        self.logger.debug(f"Group {group_name}: {len(app_users)} members")
                    else:
                        self.logger.debug(f"Group {group_name}: no members with email")

                except Exception as group_error:
                    self.logger.error(f"❌ Failed to process group {group.get('name')}: {group_error}")
                    continue

            # Save all groups in one batch
            if user_groups_batch:
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)
            else:
                self.logger.info("ℹ️ No groups with valid members to sync")

            return groups_members_map

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}")
            return {}

    async def _fetch_groups(self) -> list[dict[str, Any]]:
        """List DC groups via ``GET /rest/api/2/groups/picker?query=&maxResults=1000``."""
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.groups_picker_get_v2(
                query="",
                maxResults=GROUPS_PICKER_MAX,
            )

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(
                    "DC /groups/picker failed (%s): %s",
                    response.status,
                    response.text()[:300],
                )
                return []

            payload = response.json() or {}
            if not isinstance(payload, dict):
                return []

            raw_groups = payload.get("groups") or []
            if not isinstance(raw_groups, list):
                return []

            groups = [
                norm for row in raw_groups
                if (norm := _normalize_jira_dc_group_row(row))
            ]

            self.logger.info(
                "👥 Fetched %s groups from /groups/picker (server total=%s)",
                len(groups),
                payload.get("total"),
            )
            return groups

        except Exception as e:
            self.logger.error("❌ Error fetching groups via /groups/picker: %s", e)
            return []

    async def _fetch_group_members(self, group_id: str, group_name: str) -> list[str]:
        """
        Fetch group members via Data Center ``GET /rest/api/2/group/member``.

        Returns list of user keys (accountId/key/name) which are always present
        in the response regardless of email visibility settings.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")
        if not group_name:
            self.logger.warning(
                "⚠️ Skipping group members fetch (missing group name) for id=%s", group_id
            )
            return []

        member_keys: list[str] = []
        start_at = 0
        max_results = GROUP_MEMBER_PAGE_SIZE

        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_users_from_group_v2(
                    groupname=group_name,
                    includeInactiveUsers=False,
                    startAt=start_at,
                    maxResults=max_results,
                )

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(
                        "⚠️ Failed to fetch members for group %s: %s",
                        group_name,
                        response.text()[:500],
                    )
                    break

                payload = response.json()
                if isinstance(payload, list):
                    batch_members = payload
                    is_last = None
                elif isinstance(payload, dict):
                    vals = payload.get("values", [])
                    batch_members = vals if isinstance(vals, list) else []
                    is_last = payload.get("isLast")
                else:
                    batch_members = []
                    is_last = True

                if not batch_members:
                    break

                for member in batch_members:
                    user_key = member.get("accountId") or member.get("key") or member.get("name")
                    if user_key:
                        member_keys.append(user_key)

                if is_last is True:
                    break
                if is_last is None:
                    if len(batch_members) < max_results:
                        break
                    start_at += len(batch_members)
                    continue
                start_at += len(batch_members)
                if len(batch_members) < max_results:
                    break

            except Exception as e:
                self.logger.error("❌ Error fetching members for group %s: %s", group_name, e)
                break

        return member_keys

    def _resolve_dc_app_user_from_role_actor(
        self,
        actor: dict[str, Any],
        user_by_account_id: dict[str, AppUser],
        user_by_email: dict[str, AppUser],
        name_to_source_id: dict[str, str] | None = None,
    ) -> AppUser | None:
        """Resolve a DC project-role user actor to a synced ``AppUser``.

        DC often omits nested ``actorUser`` and exposes only top-level ``name``
        (username). ``_fetch_users`` stores ``source_user_id`` as the immutable
        ``key``, so fall back through ``name_to_source_id`` when needed.
        """
        actor_user = actor.get("actorUser") or {}
        if not isinstance(actor_user, dict):
            actor_user = {}

        for identifier in (
            actor_user.get("accountId"),
            actor_user.get("key"),
            actor_user.get("name"),
        ):
            if identifier:
                user = user_by_account_id.get(identifier)
                if user:
                    return user

        email = actor_user.get("emailAddress")
        if email:
            user = user_by_email.get(email.lower())
            if user:
                return user

        for identifier in (actor.get("key"), actor.get("name")):
            if identifier:
                user = user_by_account_id.get(identifier)
                if user:
                    return user

        actor_name = actor.get("name")
        if actor_name and name_to_source_id:
            source_id = name_to_source_id.get(actor_name.lower())
            if source_id:
                return user_by_account_id.get(source_id)

        return None

    async def _sync_project_roles(
        self,
        project_keys: list[str],
        jira_users: list[AppUser],
        groups_members_map: dict[str, list[AppUser]] = None
    ) -> None:
        """
        Sync project roles as AppRole entities using DC
        ``GET /rest/api/2/project/.../role`` and ``GET /rest/api/2/project/.../role/{id}``.

        groups_members_map: Mapping of group_id/name -> list of AppUser members (from _sync_user_groups)
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        if groups_members_map is None:
            groups_members_map = {}

        self.logger.info(f"🔐 Syncing project roles for {len(project_keys)} projects (DC GET /rest/api/2/project/.../role)...")

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

        for project_key in project_keys:
            try:
                # Step 1: List role URLs for this project (GET /rest/api/2/project/{key}/role)
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_project_roles_v2(projectIdOrKey=project_key)

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(f"⚠️ Failed to fetch roles for project {project_key}: {response.status}")
                    continue

                roles_dict = response.json()

                if not roles_dict:
                    self.logger.debug(f"No roles found for project {project_key}")
                    continue

                # Step 2: For each role, fetch role details including actors
                for role_name, role_url in roles_dict.items():
                    try:
                        # Skip app-only roles
                        if role_name == "atlassian-addons-project-access":
                            continue

                        # Extract role id from URL (string; DC may use non-numeric ids)
                        role_id = role_url.rstrip('/').split('/')[-1]

                        # Fetch role details with actors (GET /rest/api/2/project/{key}/role/{id})
                        role_datasource = await self._get_fresh_datasource()
                        role_response = await role_datasource.get_project_role_v2(
                            projectIdOrKey=project_key,
                            id=role_id,
                            excludeInactiveUsers=True,
                        )

                        if role_response.status != HttpStatusCode.OK.value:
                            self.logger.warning(f"  {project_key}: Failed to fetch role {role_name}: {role_response.status}")
                            continue

                        role_data = role_response.json()
                        actors = role_data.get("actors", [])

                        # Extract available fields from role_data
                        role_name_display = role_data.get("name", role_name)

                        # Build AppRole with external_id matching Permission format
                        app_role = AppRole(
                            app_name=self.connector_name,
                            connector_id=self.connector_id,
                            source_role_id=f"{project_key}_{role_id}",
                            name=f"{project_key} - {role_name_display}",
                            org_id=self.data_entities_processor.org_id,
                            source_created_at=None,  # Jira API doesn't provide role creation timestamp
                            source_updated_at=None   # Jira API doesn't provide role update timestamp
                        )

                        # Step 3: Extract member users from actors
                        member_users: list[AppUser] = []

                        for actor in actors:
                            actor_type = actor.get("type", "")

                            if actor_type == "atlassian-user-role-actor":
                                user = self._resolve_dc_app_user_from_role_actor(
                                    actor,
                                    user_by_account_id,
                                    user_by_email,
                                    self._dc_name_to_source_id,
                                )

                                if user:
                                    member_users.append(user)
                                else:
                                    actor_user = actor.get("actorUser") or {}
                                    self.logger.debug(
                                        f"  {project_key}/{role_name}: User not found - "
                                        f"accountId={actor_user.get('accountId')}, "
                                        f"key={actor_user.get('key') or actor.get('key')}, "
                                        f"name={actor_user.get('name') or actor.get('name')}, "
                                        f"email={actor_user.get('emailAddress')}"
                                    )

                            elif actor_type == "atlassian-group-role-actor":
                                # Group actor - get group members from already-fetched groups
                                group_name = actor.get("name") or actor.get("displayName")
                                group_id = actor.get("groupId")

                                # Try to find group members by group_id first, then by name
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

                                # Add all group members directly to role members (USER->ROLE, not GROUP->ROLE)
                                member_users.extend(group_members)

                        roles_to_sync.append((app_role, member_users))
                        total_roles += 1
                        total_members += len(member_users)

                    except Exception as role_error:
                        self.logger.error(
                            f"  {project_key}: Error processing role {role_name}: {role_error}"
                        )
                        continue

            except Exception as project_error:
                self.logger.error(f"❌ Error syncing roles for project {project_key}: {project_error}")
                continue

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
                    # Classic DC projects expose ``lead`` with ``key``/``name`` only;
                    # newer DC builds may also expose ``accountId``. ``_fetch_users``
                    # populates ``source_user_id`` from ``accountId or key or name``
                    # (~1326), so accept the same fallback chain here.
                    lead_identifier = (
                        lead_data.get("accountId")
                        or lead_data.get("key")
                        or lead_data.get("name")
                    )
                    lead_display_name = lead_data.get("displayName", "Unknown")

                    if lead_identifier:
                        lead_user = user_by_account_id.get(lead_identifier)

                        if not lead_user:
                            self.logger.warning(f"Project lead {lead_display_name} not found in synced users for {project_key}")
                    else:
                        self.logger.warning(f"No identifier (accountId/key/name) for project lead in {project_key}")
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

    async def _list_all_projects_dc(self) -> list[dict[str, Any]]:
        """Fetch all projects visible to the integration user via ``GET /rest/api/2/project``."""
        if not self.data_source:
            raise ValueError("DataSource not initialized")
        datasource = await self._get_fresh_datasource()
        response = await datasource.list_projects_get_v2(
            expand=["description", "url", "issueTypes", "lead"],
        )
        if response.status != HttpStatusCode.OK.value:
            raise Exception(f"Failed to fetch projects: {response.text()}")
        raw = self._safe_json_parse(response, "project list DC")
        if raw is None:
            raise Exception("Failed to parse project list response")
        if not isinstance(raw, list):
            raise Exception(f"Unexpected project list shape from /rest/api/2/project: {type(raw).__name__}")
        return [p for p in raw if isinstance(p, dict)]

    async def _fetch_projects(
        self,
        project_keys: Optional[list[str]] = None,
        project_keys_operator: Optional[FilterOperatorType] = None,
        jira_users: list["AppUser"] = None,
        app_roles_mapping: Optional[dict[str, list[dict[str, str]]]] = None,
    ) -> tuple[list[tuple[RecordGroup, list[Permission]]], list[dict[str, Any]]]:
        """
        Fetch projects via one ``GET /rest/api/2/project`` call, then apply project-key filters
        in-process (include / exclude lists). Only the resulting rows are turned into
        ``RecordGroup``s and synced.

        Args:
            project_keys: ``None`` or empty list = sync all visible projects; non-empty list =
                keys to include (IN) or exclude (NOT_IN).
            project_keys_operator: IN vs NOT_IN when ``project_keys`` is non-empty.
            app_roles_mapping: Pre-computed application-role → groups mapping
                from ``run_sync``. Falls back to a fresh fetch when ``None`` so
                that callers (e.g. the personal-variant override or tests) that
                bypass ``run_sync`` continue to work.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Single GET /rest/api/2/project, then IN / NOT_IN / none applied in-process (DC has no
        # server-side project key filter on this endpoint).
        all_projects = await self._list_all_projects_dc()

        is_exclude = False
        if project_keys_operator:
            operator_value = (
                project_keys_operator.value
                if hasattr(project_keys_operator, "value")
                else str(project_keys_operator)
            )
            is_exclude = operator_value == "not_in"

        if project_keys:
            if is_exclude:
                self.logger.info(f"📁 Excluding project keys (client-side filter): {project_keys}")
                excluded_keys = set(project_keys)
                projects = [
                    p for p in all_projects
                    if p.get("key") and p.get("key") not in excluded_keys
                ]
            else:
                self.logger.info(f"📁 Including only project keys (client-side filter): {project_keys}")
                allowed = set(project_keys)
                projects = [
                    p for p in all_projects
                    if p.get("key") and p.get("key") in allowed
                ]
        else:
            self.logger.info("📁 No project key filter — syncing all visible projects (DC)")
            projects = list(all_projects)

        if app_roles_mapping is None:
            app_roles_mapping = await self._fetch_application_roles_to_groups_mapping()

        perm_user_by_key: dict[str, AppUser] = {}
        if jira_users:
            perm_user_by_key = {u.source_user_id: u for u in jira_users if u.source_user_id}

        record_groups: list[tuple[RecordGroup, list[Permission]]] = []
        for project in projects:
            project_id = project.get("id")
            project_name = project.get("name")
            project_key = project.get("key")

            description = project.get("description")
            if not description or not isinstance(description, str):
                description = None

            record_group = RecordGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                external_group_id=project_id,
                connector_id=self.connector_id,
                connector_name=self.connector_name,
                name=project_name,
                short_name=project_key,
                group_type=RecordGroupType.PROJECT,
                description=description,
                web_url=project.get("url"),
            )

            # This determines which groups/users can access the project
            project_permissions = await self._fetch_project_permission_scheme(
                project_key, app_roles_mapping, perm_user_by_key
            )

            record_groups.append((record_group, project_permissions))

            if project_permissions:
                self.logger.info(f"🔐 Project {project_key}: {len(project_permissions)} permission grants from scheme")

        return record_groups, projects

    # ============================================================================
    # Issue Sync
    # ============================================================================

    async def _sync_all_project_issues(
        self,
        projects: list[tuple[RecordGroup, list[Permission]]],
        jira_users: list[AppUser],
        last_sync_time: Optional[int]
    ) -> dict[str, int]:
        """Sync issues for all projects and return statistics."""
        total_synced = 0
        new_count = 0
        updated_count = 0

        for project, _ in projects:
            try:
                project_stats = await self._sync_project_issues(
                    project, jira_users, last_sync_time
                )
                total_synced += project_stats["total_synced"]
                new_count += project_stats["new_count"]
                updated_count += project_stats["updated_count"]
            except Exception as e:
                self.logger.error(f"❌ Error processing issues for project {project.short_name}: {e}", exc_info=True)
                continue

        return {
            "total_synced": total_synced,
            "new_count": new_count,
            "updated_count": updated_count
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

        # Per-project sync: reset Epic Link key→id cache
        self._issue_key_to_id_cache.clear()

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

        Uses **POST /rest/api/2/search** (``search_issues_post_v2``) with ``startAt`` /
        ``maxResults`` and (by default) ``fieldsByKeys: false``. DC search does not
        accept ``expand`` in the body; use ``get_issue_v2(..., expand=...)`` when you
        need per-issue data not returned by search.

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

        # JQL date literals like ``"2024-01-15 10:30"`` are interpreted in the **Jira
        # server's local timezone**, not UTC. Formatting UTC-based checkpoints with
        # ``strftime`` and quoting them as JQL silently shifts the boundary by the
        # server's UTC offset and causes missed/duplicated issues at every incremental
        # sync. Use Jira's **relative-time** syntax (``-Nm``) instead: durations are
        # timezone-independent so the same query yields the same result regardless of
        # where the server is configured.
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        # Safety buffer absorbs clock skew between the connector host and the Jira
        # server, plus the minute-level rounding inherent in ``-Nm``. Downstream
        # ``_process_new_records`` dedupes unchanged issues by ``source_updated_at``,
        # so a small overlap is harmless.
        _jql_buffer_minutes = 5

        def _jql_minutes_ago(epoch_ms: int) -> Optional[int]:
            """Convert ``epoch_ms`` to ``N`` for JQL ``-Nm``.

            Returns ``None`` for timestamps at or in the future (degenerate filter
            inputs); callers should omit the JQL condition in that case rather than
            emit a meaningless ``-Nm``.
            """
            delta_ms = now_ms - epoch_ms
            if delta_ms <= 0:
                return None
            return max(1, (delta_ms // 60_000) + _jql_buffer_minutes)

        if modified_after:
            mins = _jql_minutes_ago(modified_after)
            if mins is not None:
                jql_conditions.append(f'updated >= -{mins}m')

        if modified_before:
            mins = _jql_minutes_ago(modified_before)
            if mins is not None:
                jql_conditions.append(f'updated <= -{mins}m')

        if created_after:
            mins = _jql_minutes_ago(created_after)
            if mins is not None:
                jql_conditions.append(f'created >= -{mins}m')

        if created_before:
            mins = _jql_minutes_ago(created_before)
            if mins is not None:
                jql_conditions.append(f'created <= -{mins}m')

        # Build final JQL (ORDER BY required for consistent pagination)
        # Add id ASC as secondary sort for stable ordering when timestamps are equal
        jql = " AND ".join(jql_conditions) + " ORDER BY updated ASC, id ASC"
        self.logger.info(f"🔍 JQL Query for {project_key}: {jql}")

        page_count = 0
        start_at = 0
        # Track last issue updated timestamp for resume (starts with resume_from_timestamp if resuming)
        last_issue_updated = resume_from_timestamp
        search_fields = self._get_issue_search_fields()

        while True:
            page_count += 1

            try:
                response = await self._search_issues_with_retry(
                    project_key=project_key,
                    jql=jql,
                    start_at=start_at,
                    max_results=DEFAULT_MAX_RESULTS,
                    fields=search_fields,
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
            total_matching = int(issues_batch_response.get("total", 0) or 0)

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

            # Build records for this batch
            async with self.data_store_provider.transaction() as tx_store:
                records_batch = await self._build_issue_records(
                    batch_issues, project_id, users, tx_store,
                    is_new_project=is_new_project,
                )

            self.logger.debug(
                f"📦 Fetched batch {page_count}: {len(batch_issues)} issues -> {len(records_batch)} records "
                f"(startAt={start_at}, total={total_matching}, last updated: {last_issue_updated})"
            )

            next_start = start_at + len(batch_issues)
            has_more = next_start < total_matching

            # Yield this batch with resume info
            # But we store last_issue_updated timestamp for resume on next sync
            yield records_batch, has_more, last_issue_updated

            if not has_more:
                break

            start_at = next_start

    async def _process_new_records(
        self,
        records_with_permissions: list[tuple[Record, list[Permission]]],
        project_name: str,
        stats: dict[str, int]
    ) -> None:
        """
        Process records (new and updated) in batches.
        on_new_records internally handles both new and updated.
        """
        # Sort records: records without parent_external_record_id (Epics) come first
        sorted_records = sorted(
            records_with_permissions,
            key=lambda x: (x[0].parent_external_record_id is not None, x[0].parent_external_record_id or "")
        )

        batch_size = BATCH_PROCESSING_SIZE

        for i in range(0, len(sorted_records), batch_size):
            batch = sorted_records[i:i + batch_size]
            await self.data_entities_processor.on_new_records(batch)

            # Update stats
            new_in_batch = sum(1 for r, _ in batch if r.version == 0)
            stats["new_count"] += new_in_batch
            stats["updated_count"] += len(batch) - new_in_batch

            # Log batch summary
            issues_count = sum(1 for r, _ in batch if isinstance(r, TicketRecord))
            files_count = sum(1 for r, _ in batch if isinstance(r, FileRecord))
            self.logger.info(
                f"📦 Batch {i//batch_size + 1}: {issues_count} issues, "
                f"{files_count} attachments ({new_in_batch} new, {len(batch) - new_in_batch} updated)"
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

    async def _discover_epic_link_field_id(self) -> None:
        """Discover Epic Link custom field id via GET /rest/api/2/field (once at init)."""
        if self._epic_link_field_id is not None:
            return
        self._epic_link_field_id = ""
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_fields_v2()
            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(
                    "Failed to discover Epic Link field: HTTP %s", response.status
                )
                return
            fields_list = self._safe_json_parse(response, "Epic Link field discovery")
            if isinstance(fields_list, list):
                for field in fields_list:
                    if not isinstance(field, dict):
                        continue
                    name = field.get("name")
                    schema = field.get("schema") or {}
                    custom = schema.get("custom") if isinstance(schema, dict) else None
                    if name == DC_EPIC_LINK_FIELD_NAME or custom == DC_EPIC_LINK_SCHEMA_CUSTOM:
                        field_id = field.get("id")
                        if field_id:
                            self._epic_link_field_id = str(field_id)
                            self.logger.info(
                                "Discovered Epic Link field: %s", self._epic_link_field_id
                            )
                        return
            self.logger.debug(
                "Epic Link field not found (non-Scrum or custom epic link configuration)"
            )
        except Exception as e:
            self.logger.warning("Epic Link field discovery failed: %s", e)

    def _get_issue_search_fields(self) -> list[str]:
        if self._epic_link_field_id:
            return ISSUE_SEARCH_FIELDS + [self._epic_link_field_id]
        return list(ISSUE_SEARCH_FIELDS)

    async def _resolve_hierarchy_parent_id(
        self,
        fields: dict[str, Any],
        *,
        is_subtask: bool,
        is_epic: bool,
        parent_from_parent_field: str | None,
    ) -> str | None:
        """Resolve parent id from fields.parent (sub-tasks) or Epic Link (stories)."""
        if is_epic:
            return None
        if is_subtask or parent_from_parent_field:
            return parent_from_parent_field

        epic_link_field_id = self._epic_link_field_id
        if not epic_link_field_id:
            return None

        raw = fields.get(epic_link_field_id)
        if not raw:
            return None

        epic_key: str | None = None
        epic_id: str | None = None
        if isinstance(raw, str) and raw.strip():
            epic_key = raw.strip()
        elif isinstance(raw, dict):
            key = raw.get("key")
            inline_id = raw.get("id")
            epic_key = str(key).strip() if key else None
            epic_id = str(inline_id) if inline_id else None

        if epic_id:
            if epic_key:
                self._issue_key_to_id_cache[epic_key] = epic_id
            return epic_id
        if not epic_key:
            return None

        cached = self._issue_key_to_id_cache.get(epic_key)
        if cached:
            return cached

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_issue_v2(issueIdOrKey=epic_key, fields=["id"])
            if response.status == HttpStatusCode.NOT_FOUND.value:
                self.logger.debug("Epic Link target issue %s not found", epic_key)
                return None
            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(
                    "Failed to resolve Epic Link key %s: HTTP %s", epic_key, response.status
                )
                return None
            issue = self._safe_json_parse(response, f"Epic Link resolve {epic_key}")
            resolved_id = issue.get("id") if issue else None
        except Exception as e:
            self.logger.warning("Failed to resolve Epic Link key %s: %s", epic_key, e)
            return None
        if not resolved_id:
            return None
        resolved_id = str(resolved_id)
        self._issue_key_to_id_cache[epic_key] = resolved_id
        return resolved_id

    async def _extract_issue_data_with_parent(
        self,
        issue: dict[str, Any],
        user_by_account_id: dict[str, AppUser],
    ) -> dict[str, Any]:
        """Extract issue fields and resolve hierarchy parent (Epic Link + parent field)."""
        issue_data = self._extract_issue_data(issue, user_by_account_id)
        fields = issue.get("fields", {}) or {}
        issue_data["parent_external_id"] = await self._resolve_hierarchy_parent_id(
            fields,
            is_subtask=issue_data["is_subtask"],
            is_epic=issue_data["is_epic"],
            parent_from_parent_field=issue_data["parent_external_id"],
        )
        return issue_data

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

        # DC returns description as wiki/plain string (not ADF).
        description_raw = fields.get("description")
        if isinstance(description_raw, str) and description_raw.strip():
            description_text = description_raw
        else:
            description_text = None

        # Extract issue type information
        issue_type_obj = fields.get("issuetype", {}) or {}
        raw_issue_type = issue_type_obj.get("name") if issue_type_obj else None
        # Map issue type to standardized value using value mapper
        issue_type = self.value_mapper.map_type(raw_issue_type)

        # Extract parent issue information (sub-task parent at extract time; Epic Link resolved later)
        parent_obj = fields.get("parent")
        parent_external_id = parent_obj.get("id") if parent_obj else None
        parent_key = parent_obj.get("key") if parent_obj else None

        is_subtask = bool(issue_type_obj.get("subtask"))
        is_epic = (raw_issue_type or "").strip().lower() == "epic"

        # Build record name with issue key in square brackets at start for better searchability
        issue_name = f"[{issue_key}] {issue_summary}" if issue_key else issue_summary

        # Add issue type to description for full searchability
        if issue_type and description_text:
            description = f"Issue Type: {issue_type}\n\n{description_text}"
        elif issue_type:
            description = f"Issue Type: {issue_type}"
        else:
            description = description_text

        # Extract and map status to standardized value
        status_obj = fields.get("status", {})
        raw_status = status_obj.get("name") if status_obj else None
        status = self.value_mapper.map_status(raw_status)

        # Extract and map priority to standardized value
        priority_obj = fields.get("priority", {})
        raw_priority = priority_obj.get("name") if priority_obj else None
        priority = self.value_mapper.map_priority(raw_priority)

        # Extract user information. Email is not available on issue user references,
        # so resolve via the synced ``source_user_id`` map. DC may expose only
        # ``key``/``name`` on legacy projects (no ``accountId``) — mirror the
        # ``accountId or key or name`` fallback from ``_fetch_users`` (~1326).
        def _dc_user_identifier(user_ref: Optional[dict[str, Any]]) -> Optional[str]:
            if not user_ref:
                return None
            return (
                user_ref.get("accountId")
                or user_ref.get("key")
                or user_ref.get("name")
            )

        creator = fields.get("creator")
        creator_account_id = _dc_user_identifier(creator)
        creator_name = creator.get("displayName") if creator else None
        creator_email = (
            user_by_account_id[creator_account_id].email
            if creator_account_id and creator_account_id in user_by_account_id
            else None
        )

        # Reporter (can be changed, unlike creator which is immutable)
        reporter = fields.get("reporter")
        reporter_account_id = _dc_user_identifier(reporter)
        reporter_name = reporter.get("displayName") if reporter else None
        reporter_email = (
            user_by_account_id[reporter_account_id].email
            if reporter_account_id and reporter_account_id in user_by_account_id
            else None
        )

        assignee = fields.get("assignee")
        assignee_account_id = _dc_user_identifier(assignee)
        assignee_name = assignee.get("displayName") if assignee else None
        assignee_email = (
            user_by_account_id[assignee_account_id].email
            if assignee_account_id and assignee_account_id in user_by_account_id
            else None
        )

        created_at = self._parse_jira_timestamp(fields.get("created"))
        updated_at = self._parse_jira_timestamp(fields.get("updated"))

        return {
            "issue_id": issue_id,
            "issue_key": issue_key,
            "issue_name": issue_name,
            "description": description,
            "issue_type": issue_type,
            "is_epic": is_epic,
            "is_subtask": is_subtask,
            "parent_external_id": parent_external_id,
            "parent_key": parent_key,
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
    ) -> list[tuple[Record, list[Permission]]]:
        """
        Build issue records with permissions from raw issue data, respecting Jira hierarchy.

        When is_new_project is True (full sync wiped sync points), the "skip unchanged
        issues" short-circuit is bypassed so every issue flows through _process_record
        and its BELONGS_TO / RECORD_RELATIONS / PERMISSION / ENTITY_RELATIONS edges are
        recreated after full-sync edge deletion.
        """
        all_records: list[tuple[Record, list[Permission]]] = []
        skipped_unchanged_count = 0

        # Use the user-facing site URL for weburl construction
        atlassian_domain = self.site_url if self.site_url else ""

        # Create accountId -> AppUser lookup for matching issue creators/assignees
        user_by_account_id = {user.source_user_id: user for user in users if user.source_user_id}

        for issue in issues:
            issue_data = await self._extract_issue_data_with_parent(issue, user_by_account_id)

            issue_id = issue_data["issue_id"]
            issue_key = issue_data["issue_key"]
            issue_name = issue_data["issue_name"]
            issue_type = issue_data["issue_type"]
            is_epic = issue_data["is_epic"]
            is_subtask = issue_data["is_subtask"]
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

            fields = issue.get("fields", {})

            # Check for existing record (works for both Epics and regular issues)
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=issue_id
            )

            record_id = existing_record.id if existing_record else str(uuid4())
            is_new = existing_record is None

            # Only increment version if issue content actually changed
            is_issue_changed = False
            if is_new:
                version = 0
                is_issue_changed = True
                self.logger.debug(f"🆕 New issue found: {issue_key} (external_id: {issue_id})")
            elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
                version = existing_record.version + 1
                is_issue_changed = True
                self.logger.debug(f"📝 Issue {issue_key} updated, incrementing version to {version}")
            else:
                version = existing_record.version if existing_record else 0
                # Skip unchanged issues silently - no need to log every unchanged issue

            # Skip processing if issue is unchanged, unless this is a full sync
            # (is_new_project=True means sync points were wiped, so edges need to be
            # recreated even for unchanged issues; _process_record is idempotent).
            if not is_issue_changed and not is_new_project:
                skipped_unchanged_count += 1
                continue

            # Set parent relationships and record group
            external_record_group_id = project_id
            record_group_type = RecordGroupType.PROJECT
            parent_record_id = None
            parent_record_type = None

            if is_epic:
                # Epic is a Record that belongs to Project RecordGroup
                pass
            elif parent_external_id and not is_subtask:
                # Story/Task with Epic parent → Epic is now a Record, not RecordGroup
                parent_record_id = parent_external_id
                parent_record_type = RecordType.TICKET
            elif is_subtask and parent_external_id:
                # Sub-task → has parent Record (creates PARENT_CHILD edge in recordRelations)
                parent_record_id = parent_external_id
                parent_record_type = RecordType.TICKET

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

            all_records.append((issue_record, permissions))

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
                    all_records.extend(attachment_records)
            except Exception as e:
                self.logger.error(f"❌ Failed to fetch attachments for issue {issue_key}: {e}")

        # Log summary only if there were skipped issues
        if skipped_unchanged_count > 0:
            self.logger.debug(f"⏭️ Skipped {skipped_unchanged_count} unchanged issue(s)")

        return all_records

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

                # Check for existing attachment record
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=f"attachment_{attachment_id}"
                )

                # Get attachment metadata
                filename = attachment.get("filename", "unknown")
                file_size = attachment.get("size", 0)
                mime_type = attachment.get("mimeType", MimeTypes.UNKNOWN.value)

                # Parse timestamps
                created_str = attachment.get("created")
                created_at = self._parse_jira_timestamp(created_str) if created_str else 0

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

            self.logger.info(f"📎 Returning {len(attachment_records)} attachment records for issue {issue_key}")
            return attachment_records

        except Exception as e:
            self.logger.error(f"Failed to fetch attachments for issue {issue_key}: {e}", exc_info=True)
            return []

    # ============================================================================
    # BlockGroups & Blocks Parsing
    # ============================================================================

    def _organize_issue_comments_to_threads(
        self,
        comments_data: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """
        Group Jira comments by thread (parent comment) and sort by created timestamp.
        Returns list of threads, each thread is a list of comments sorted by created.

        Jira supports threaded comments via 'parent' field on comment objects.
        - Top-level comments (no parent) start their own thread
        - Replies grouped under their parent's thread_id
        - Each thread sorted by created timestamp (oldest first)
        - Threads sorted by first comment's created timestamp
        """
        if not comments_data:
            return []

        threads: dict[str, list[dict[str, Any]]] = {}

        for comment in comments_data:
            comment_id = comment.get("id", "")
            parent = comment.get("parent", {})
            # Thread ID is parent's ID if it's a reply, or self ID if top-level
            thread_id = parent.get("id") if parent and parent.get("id") else comment_id
            if not thread_id:
                continue

            if thread_id not in threads:
                threads[thread_id] = []
            threads[thread_id].append(comment)

        # Sort each thread by created timestamp (oldest first)
        for thread_id in threads:
            threads[thread_id].sort(
                key=lambda c: self._parse_jira_timestamp(c.get("created", "")) or 0
            )

        # Sort threads by first comment's created timestamp (oldest thread first)
        return sorted(
            threads.values(),
            key=lambda t: self._parse_jira_timestamp(t[0].get("created", "")) or 0 if t else 0
        )


    async def _parse_issue_to_blocks(
        self,
        issue_data: dict[str, Any],
        issue_key: str,
        weburl: Optional[str] = None,
        attachment_children_map: Optional[dict[str, ChildRecord]] = None,
        attachment_mime_types: Optional[dict[str, str]] = None,
        rendered_fields: Optional[dict[str, Any]] = None,
    ) -> BlocksContainer:
        """
        Parse Jira issue data into BlocksContainer with BlockGroups and Blocks.

        Uses rendered HTML from ``renderedFields`` (via ``?expand=renderedFields``)
        for description and comments. Images are fetched via authenticated API and
        inlined as base64 data URIs before storing.

        This creates:
        - Description BlockGroup (index=0) with:
          - Issue description HTML (images converted to base64)
          - children_records: non-image files linked inline in description HTML
        - Thread BlockGroups (index=1,2,...) for each comment thread
        - Comment BlockGroup objects (sub_type=COMMENT) for each comment
        """
        issue_id = issue_data.get("id", "")
        fields = issue_data.get("fields", {})
        resolved_issue_key = issue_key or issue_data.get("key", "") or ""
        issue_summary = fields.get("summary") or f"Issue {resolved_issue_key or issue_id}"
        issue_name = (
            f"[{resolved_issue_key}] {issue_summary}" if resolved_issue_key else issue_summary
        )

        if not weburl:
            raise ValueError("weburl is required when creating BlockGroup for issues")

        block_groups: list[BlockGroup] = []
        blocks: list[Block] = []
        block_group_index = 0

        _attachment_mime_types = dict(attachment_mime_types or {})
        raw_attachments = fields.get("attachment") or []
        if not isinstance(raw_attachments, list):
            raw_attachments = []

        def is_image_attachment(attachment_id: str) -> bool:
            mime_type = _attachment_mime_types.get(attachment_id, "")
            return mime_type.startswith("image/")

        # Get rendered HTML from renderedFields
        rendered = rendered_fields or {}
        rendered_description_html = rendered.get("description") or ""

        # Build rendered comments lookup by comment ID
        rendered_comments_field = rendered.get("comment", {})
        rendered_comments_list: list[dict[str, Any]] = []
        if isinstance(rendered_comments_field, dict):
            rendered_comments_list = rendered_comments_field.get("comments", []) or []
        elif isinstance(rendered_comments_field, list):
            rendered_comments_list = rendered_comments_field
        rendered_comment_body_by_id: dict[str, str] = {}
        for rc in rendered_comments_list:
            cid = rc.get("id", "")
            body = rc.get("body", "")
            if cid and body:
                rendered_comment_body_by_id[cid] = body

        comments_data = issue_data.get("comments", [])
        if isinstance(comments_data, dict):
            comments_data = comments_data.get("comments", [])

        # 1. Description BlockGroup (index=0) — rendered HTML
        if isinstance(rendered_description_html, str) and rendered_description_html.strip():
            description_content, _desc_inlined = await self._process_html_images_with_auth(
                rendered_description_html, issue_id, raw_attachments
            )
        else:
            description_content = ""

        if not description_content:
            description_content = f"<h1>{issue_name}</h1>"
        else:
            description_content = f"<h1>{issue_name}</h1>\n{description_content}"

        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=block_group_index,
            name=issue_name,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Description for issue {issue_key}" if issue_key else "Issue description",
            source_group_id=f"{issue_id}_description",
            data=description_content,
            format=DataFormat.HTML,
            weburl=weburl,
            requires_processing=True,
        )
        block_groups.append(description_block_group)
        block_group_index += 1

        # 2. Comment Thread BlockGroups (index=1,2,...) and Comment Blocks
        if comments_data:
            sorted_threads = self._organize_issue_comments_to_threads(comments_data)

            for thread_comments in sorted_threads:
                if not thread_comments:
                    continue

                first_comment = thread_comments[0]
                parent = first_comment.get("parent", {})
                first_comment_id = first_comment.get("id", "")
                thread_id = parent.get("id") if parent and parent.get("id") else first_comment_id

                thread_block_group_index = block_group_index
                thread_block_group = BlockGroup(
                    id=str(uuid4()),
                    index=thread_block_group_index,
                    parent_index=0,
                    name=f"Comment Thread - {thread_id[:8]}" if thread_id else "Comment Thread",
                    type=GroupType.TEXT_SECTION,
                    sub_type=GroupSubType.COMMENT_THREAD,
                    description=f"Comment thread for issue {issue_key}" if issue_key else "Comment thread",
                    source_group_id=f"{issue_id}_thread_{thread_id}" if thread_id else f"{issue_id}_thread_{thread_block_group_index}",
                    weburl=weburl,
                    requires_processing=False,
                )
                block_groups.append(thread_block_group)
                block_group_index += 1

                for comment in thread_comments:
                    comment_id = comment.get("id", "")

                    # Use rendered HTML: prefer renderedBody (from paginated fetch), then lookup
                    comment_html = (
                        comment.get("renderedBody")
                        or rendered_comment_body_by_id.get(comment_id, "")
                    )
                    if not comment_html or not isinstance(comment_html, str) or not comment_html.strip():
                        continue

                    # Process images in comment HTML
                    comment_body, _comment_inlined = await self._process_html_images_with_auth(
                        comment_html, issue_id, raw_attachments
                    )

                    if not comment_body:
                        continue

                    # Build comment weburl
                    if self.site_url and issue_key and comment_id:
                        comment_weburl = f"{self.site_url}/browse/{issue_key}?focusedCommentId={comment_id}"
                    else:
                        comment_weburl = weburl

                    author = comment.get("author", {})
                    author_name = author.get("displayName", "Unknown")

                    # Detect non-image attachment links in this comment's HTML
                    comment_children: list[ChildRecord] = []
                    if attachment_children_map:
                        link_ids = self._extract_attachment_ids_from_html_links(comment_html)
                        for att_id in link_ids:
                            if (
                                att_id in attachment_children_map
                                and not is_image_attachment(att_id)
                            ):
                                comment_children.append(attachment_children_map[att_id])

                    comment_block_group = BlockGroup(
                        id=str(uuid4()),
                        index=block_group_index,
                        parent_index=thread_block_group_index,
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.COMMENT,
                        name=f"Comment by {author_name}",
                        description=f"Comment by {author_name}",
                        source_group_id=comment_id,
                        data=comment_body,
                        format=DataFormat.HTML,
                        weburl=comment_weburl,
                        requires_processing=True,
                        children_records=comment_children if comment_children else None,
                    )
                    block_groups.append(comment_block_group)
                    block_group_index += 1

        # Non-image files linked inline in description HTML only (not standalone attachments)
        description_children: list[ChildRecord] = []
        if attachment_children_map and rendered_description_html:
            link_ids = self._extract_attachment_ids_from_html_links(rendered_description_html)
            for att_id in link_ids:
                if (
                    att_id in attachment_children_map
                    and not is_image_attachment(att_id)
                ):
                    description_children.append(attachment_children_map[att_id])

        if description_children:
            description_block_group.children_records = description_children

        # Populate children arrays for BlockGroups
        blockgroup_children_map: dict[int, list[int]] = defaultdict(list)
        block_children_map: dict[int, list[int]] = defaultdict(list)

        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)

        for b in blocks:
            if b.parent_index is not None:
                block_children_map[b.parent_index].append(b.index)

        for bg in block_groups:
            child_block_indices = []
            child_bg_indices = []

            if bg.index in blockgroup_children_map:
                child_bg_indices = sorted(blockgroup_children_map[bg.index])

            if bg.index in block_children_map:
                child_block_indices = sorted(block_children_map[bg.index])

            if child_block_indices or child_bg_indices:
                bg.children = BlockGroupChildren.from_indices(
                    block_indices=child_block_indices,
                    block_group_indices=child_bg_indices
                )

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

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
                    filename = attachment.get("filename", "unknown")
                    file_size = attachment.get("size", 0)
                    mime_type = attachment.get("mimeType", MimeTypes.UNKNOWN.value)
                    created_str = attachment.get("created")
                    created_at = self._parse_jira_timestamp(created_str) if created_str else 0

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

    async def _search_issues_with_retry(
        self,
        *,
        project_key: str,
        jql: str,
        start_at: int,
        max_results: int,
        fields: list[str],
        max_attempts: int = 3,
    ) -> Any:
        """Search Jira issues, retrying on transient httpx transport errors.

        Targeted at stale keep-alive sockets that raise ``RemoteProtocolError``
        before any HTTP response is received during paginated sync. Replaying the
        same JQL and ``startAt`` is safe when no response was received — search is
        read-only and httpx evicts the broken connection on failure.
        """
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                datasource = await self._get_fresh_datasource()
                return await datasource.search_issues_post_v2(
                    jql=jql,
                    startAt=start_at,
                    maxResults=max_results,
                    fields=fields,
                )
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
                    "Transient transport error searching issues for project %s "
                    "(startAt=%s, attempt %s/%s): %s — retrying in %.1fs",
                    project_key, start_at, attempt + 1, max_attempts, e, backoff,
                )
                await asyncio.sleep(backoff)

        raise Exception(
            f"Failed to fetch issues for {project_key} (startAt={start_at}) "
            f"after {max_attempts} attempts: {last_exc}"
        ) from last_exc

    async def _get_issue_with_retry(
        self,
        issue_id: str,
        fields: list[str],
        expand: list[str] | None = None,
        max_attempts: int = 3,
    ) -> Any:
        """Fetch a Jira issue, retrying on transient httpx transport errors.

        Targeted at the failure mode where the httpx connection pool reuses a
        socket that the LB/proxy in front of Jira DC has already half-closed
        past its idle timeout, raising ``RemoteProtocolError`` before any
        response is received. GETs are idempotent, so retrying with backoff is
        safe; httpx evicts the broken connection on failure, so the retry hits
        a fresh socket.
        """
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                datasource = await self._get_fresh_datasource()
                return await datasource.get_issue_v2(
                    issueIdOrKey=issue_id,
                    fields=fields,
                    expand=expand,
                )
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
                    "Transient transport error fetching issue %s "
                    "(attempt %s/%s): %s — retrying in %.1fs",
                    issue_id, attempt + 1, max_attempts, e, backoff,
                )
                await asyncio.sleep(backoff)

        raise Exception(
            f"Failed to fetch issue {issue_id} after {max_attempts} attempts: {last_exc}"
        ) from last_exc

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

        # Fetch issue with comments. ``"comments"`` is not a valid v2 ``expand`` value
        # (valid expands: ``renderedFields``, ``names``, ``schema``, ``transitions``,
        # ``operations``, ``editmeta``, ``changelog``, ``versionedRepresentations``).
        # Comments are returned via ``fields=comment``.
        #
        # The httpx pool occasionally hands out a keep-alive socket that the
        # Jira DC LB / nginx has already half-closed past its idle timeout. The
        # very next request raises ``httpx.RemoteProtocolError`` ("Server
        # disconnected without sending a response") before any HTTP response is
        # received. The same shape also surfaces as ``httpx.ReadError`` /
        # ``ConnectError`` for transient infra blips. These are safe to retry —
        # a GET that never reached the server is idempotent — and the failing
        # socket gets evicted from the pool automatically, so the retry picks
        # up a fresh connection. Without this, the indexing consumer burns its
        # tenacity retries on a single dead-keepalive and surfaces a misleading
        # 500 to the user.
        response = await self._get_issue_with_retry(
            issue_id=issue_id,
            fields=["summary", "description", "attachment", "comment", "project"],
            expand=["renderedFields"],
        )
        if response.status != HttpStatusCode.OK.value:
            raise Exception(f"Failed to fetch issue content: {response.text()}")

        issue_data = response.json()
        if not issue_data:
            raise Exception(f"No issue data found for ID: {issue_id}")

        fields = issue_data.get("fields", {})
        rendered_fields = issue_data.get("renderedFields", {})

        # Get issue key from API response
        issue_key = issue_data.get("key", "")

        # Build issue weburl
        if self.site_url and issue_key:
            issue_weburl = f"{self.site_url}/browse/{issue_key}"
        else:
            issue_weburl = record.weburl

        # Get attachments and comments from issue data
        attachments_data = fields.get("attachment", [])

        # Handle comments — embedded in ``fields.comment`` as
        # ``{startAt, maxResults, total, comments: [...]}`` (or as a bare list on
        # some legacy DC builds). The embedded page uses the server's default size
        # (typically 20–50), so when ``total`` exceeds that we paginate the rest via
        # the dedicated ``/rest/api/2/issue/{idOrKey}/comment`` endpoint to avoid
        # silently dropping later comments.
        comments_field = fields.get("comment", {})
        if isinstance(comments_field, dict):
            comments_data = list(comments_field.get("comments", []) or [])
            embedded_total = comments_field.get("total")
            if isinstance(embedded_total, int) and embedded_total > len(comments_data):
                comments_data = await self._fetch_all_issue_comments(
                    issue_id_or_key=issue_id,
                    already_fetched=comments_data,
                    expected_total=embedded_total,
                )
        elif isinstance(comments_field, list):
            comments_data = list(comments_field)
        else:
            comments_data = []

        # Resolve project for new attachment FileRecords. Prefer the stored ticket
        # group id; fall back to Jira ``fields.project`` when legacy rows have an
        # empty ``external_record_group_id``.
        project_id = record.external_record_group_id or ""
        if not project_id:
            project = fields.get("project") or {}
            project_id = project.get("id") or ""

        # Build attachment_id -> mimeType map (wiki inline images vs file attachments).
        attachment_mime_types: dict[str, str] = {}
        for attachment in attachments_data:
            att_id = attachment.get("id", "")
            mime_type = attachment.get("mimeType", "")
            if att_id:
                attachment_mime_types[str(att_id)] = mime_type

        # Fetch child records from database - get map of attachment_id -> ChildRecord
        attachment_children_map: dict[str, ChildRecord] = {}

        async with self.data_store_provider.transaction() as tx_store:
            # Process attachments (including images)
            if attachments_data:
                attachment_children_map = await self._process_issue_attachments_for_children(
                    attachments_data=attachments_data,
                    issue_id=issue_id,
                    issue_node_id=record.id,
                    project_id=project_id,
                    issue_weburl=issue_weburl,
                    tx_store=tx_store
                )

        # Add comments to issue_data for parsing
        issue_data["comments"] = comments_data

        # Parse issue to BlocksContainer using rendered HTML
        # attachment_children_map maps attachment_id -> ChildRecord
        # attachment_mime_types maps attachment_id -> mimeType (for image detection)
        # This allows proper mapping: comment attachments -> comment block, others -> description
        blocks_container = await self._parse_issue_to_blocks(
            issue_data=issue_data,
            issue_key=issue_key,
            weburl=issue_weburl,
            attachment_children_map=attachment_children_map if attachment_children_map else None,
            attachment_mime_types=attachment_mime_types if attachment_mime_types else None,
            rendered_fields=rendered_fields,
        )

        # Serialize BlocksContainer to JSON bytes
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')

    # ============================================================================
    # Media & Streaming
    # ============================================================================

    async def _get_issue_attachments_cached(self, issue_id: str) -> list[dict[str, Any]]:
        """
        Fetch issue attachments with per-issue caching to avoid repeated API calls.
        """
        if issue_id in self._issue_attachments_cache:
            return self._issue_attachments_cache[issue_id]

        datasource = await self._get_fresh_datasource()
        response = await datasource.get_issue_v2(
            issueIdOrKey=issue_id,
            fields=["attachment"]
        )
        if response.status != HttpStatusCode.OK.value:
            self.logger.warning(f"⚠️ Failed to fetch issue {issue_id} for media: {response.status}")
            return []

        issue_details = self._safe_json_parse(response, f"issue attachments for {issue_id}")
        if not issue_details:
            return []

        attachments = issue_details.get("fields", {}).get("attachment", []) or []
        self._issue_attachments_cache[issue_id] = attachments
        return attachments

    async def _fetch_media_as_base64(
        self,
        issue_id: str,
        media_id: str,
        media_alt: str
    ) -> Optional[str]:
        """
        Fetch attachment content and return as base64 data URI.

        Matches by attachment id first, then by filename (exact and partial).
        """
        try:
            # Get issue attachments (cached per issue to avoid repeated calls)
            attachments = await self._get_issue_attachments_cached(issue_id)

            if not attachments:
                self.logger.debug(f"No attachments found for issue {issue_id}")
                return None

            # First, try to find attachment by media_id (most reliable - per Jira API docs)
            target_attachment = None
            if media_id:
                for attachment in attachments:
                    if str(attachment.get("id", "")) == str(media_id):
                        target_attachment = attachment
                        self.logger.debug(f"Found attachment by media_id: {media_id}")
                        break

            # Fallback: find attachment matching the filename (alt text)
            if not target_attachment and media_alt:
                for attachment in attachments:
                    filename = attachment.get("filename", "")
                    if filename == media_alt:
                        target_attachment = attachment
                        self.logger.debug(f"Found attachment by exact filename: {media_alt}")
                        break

            # Fallback: try partial filename match
            if not target_attachment and media_alt:
                for attachment in attachments:
                    filename = attachment.get("filename", "")
                    if media_alt in filename or filename in media_alt:
                        target_attachment = attachment
                        self.logger.debug(f"Found attachment by partial filename match: {media_alt} ~ {filename}")
                        break

            if not target_attachment:
                self.logger.debug(f"No attachment found matching media_id='{media_id}' or filename='{media_alt}' in issue {issue_id}")
                return None

            # Fetch attachment content via secure URL (client base_url)
            attachment_id = target_attachment.get("id")
            mime_type = target_attachment.get("mimeType", "application/octet-stream")
            filename = target_attachment.get("filename")
            if not attachment_id or not filename:
                self.logger.debug(
                    f"Missing attachment id or filename for media_id='{media_id}' "
                    f"filename='{media_alt}' in issue {issue_id}"
                )
                return None

            datasource = await self._get_fresh_datasource()
            content_response = await datasource.get_secure_attachment_v2(
                id=str(attachment_id),
                filename=str(filename),
            )

            if content_response.status != HttpStatusCode.OK.value:
                self.logger.warning(
                    "Failed to fetch attachment content for inline media: "
                    "attachment_id=%s issue_id=%s filename=%s status=%s response=%s",
                    attachment_id,
                    issue_id,
                    filename,
                    content_response.status,
                    content_response.text()[:500],
                )
                return None

            # Convert to base64
            content_bytes = content_response.bytes()
            base64_data = base64.b64encode(content_bytes).decode('utf-8')

            # Create data URI
            data_uri = f"data:{mime_type};base64,{base64_data}"

            self.logger.debug(f"Successfully converted attachment '{media_alt or media_id}' to base64 ({len(base64_data)} chars)")
            return data_uri

        except Exception as e:
            self.logger.warning(f"⚠️ Error fetching media (id='{media_id}', alt='{media_alt}') for issue {issue_id}: {e}")
            return None

    # Regex to extract attachment IDs from Jira rendered HTML img/a URLs
    _RENDERED_ATTACHMENT_URL_RE = re.compile(
        r'/secure/(?:attachment|thumbnail)/(\d+)/'
    )

    async def _process_html_images_with_auth(
        self,
        html_content: str,
        issue_id: str,
        attachments_data: list[dict[str, Any]],
    ) -> tuple[str, set[str]]:
        """Replace Jira attachment image URLs with base64 data URIs.

        For each ``<img>`` pointing to a Jira attachment/thumbnail URL:
        1. Fetches the binary via authenticated API → base64 data URI
        2. Sets ``alt="Image_N"`` so the HTML parser can resolve it
        3. Unwraps Jira's ``<span class="image-wrap">`` / ``<a>`` wrappers
           so the ``<img>`` becomes a direct child of the block element

        Returns:
            Tuple of (modified_html, set_of_inlined_attachment_ids).
        """
        inlined_ids: set[str] = set()
        if not html_content:
            return html_content, inlined_ids

        attachment_by_id: dict[str, dict[str, Any]] = {}
        for att in attachments_data:
            att_id = str(att.get("id", ""))
            if att_id:
                attachment_by_id[att_id] = att

        soup = BeautifulSoup(html_content, "html.parser")
        image_counter = 1

        for img_tag in soup.find_all("img"):
            src = img_tag.get("src", "")
            match = self._RENDERED_ATTACHMENT_URL_RE.search(src)
            if not match:
                continue
            attachment_id = match.group(1)
            if attachment_id not in attachment_by_id:
                continue
            att_meta = attachment_by_id[attachment_id]
            if not att_meta.get("mimeType", "").startswith("image/"):
                continue

            data_uri = await self._fetch_media_as_base64(
                issue_id, attachment_id, att_meta.get("filename", "")
            )
            if not data_uri:
                continue

            img_tag["src"] = data_uri
            img_tag["alt"] = f"Image_{image_counter}"
            image_counter += 1
            inlined_ids.add(attachment_id)

            # Unwrap Jira wrappers: <span class="image-wrap"><a>…</a></span>
            # so <img> becomes a direct child of the block element (p, td, li)
            parent = img_tag.parent
            if parent and parent.name == "a":
                parent.unwrap()
                parent = img_tag.parent
            if parent and parent.name == "span" and "image-wrap" in (
                parent.get("class") or []
            ):
                parent.unwrap()

        # Remove Jira UI icons (rendericon class) that aren't content images.
        for icon in soup.find_all("img", class_="rendericon"):
            icon.decompose()

        return str(soup), inlined_ids

    def _extract_attachment_ids_from_html_links(self, html_content: str) -> set[str]:
        """Extract attachment IDs referenced in <a href="..."> links in rendered HTML.

        Used to detect non-image attachments (PDFs, docs) embedded in comment/description HTML.
        """
        if not html_content:
            return set()
        ids: set[str] = set()
        for match in self._RENDERED_ATTACHMENT_URL_RE.finditer(html_content):
            ids.add(match.group(1))
        return ids

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

    async def _fetch_all_issue_comments(
        self,
        issue_id_or_key: str,
        already_fetched: list[dict[str, Any]],
        expected_total: int,
    ) -> list[dict[str, Any]]:
        """Page out the remaining comments for an issue using DC v2.

        ``fields.comment`` embedded in ``get_issue_v2`` returns only the server's
        default first page; this paginates ``/rest/api/2/issue/{idOrKey}/comment``
        until ``expected_total`` comments are gathered (or the server stops returning
        rows). The caller passes in the comments already extracted from the embedded
        page so we don't refetch them.
        """
        if expected_total <= len(already_fetched):
            return already_fetched

        all_comments: list[dict[str, Any]] = list(already_fetched)
        # Continue from where the embedded page ended.
        start_at = len(all_comments)
        # Use a reasonable page size; DC typically caps this around 1000 but we keep
        # it modest to avoid oversized responses for issues with thousands of comments.
        page_size = 100

        datasource = await self._get_fresh_datasource()
        # Hard stop to defend against servers reporting a wrong ``total``.
        safety_limit = max(expected_total, len(all_comments)) + page_size

        while len(all_comments) < expected_total and start_at < safety_limit:
            response = await datasource.get_issue_comments_v2(
                issueIdOrKey=issue_id_or_key,
                startAt=start_at,
                maxResults=page_size,
                expand="renderedBody",
            )
            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(
                    "⚠️ Comment pagination stopped for issue %s at startAt=%s: HTTP %s",
                    issue_id_or_key,
                    start_at,
                    response.status,
                )
                break

            payload = response.json() or {}
            batch = payload.get("comments", []) if isinstance(payload, dict) else []
            if not isinstance(batch, list) or not batch:
                break

            all_comments.extend(batch)
            advanced = len(batch)
            start_at += advanced
            # Stop if the server returned fewer than requested (last page).
            if advanced < page_size:
                break

        if len(all_comments) < expected_total:
            self.logger.warning(
                "⚠️ Fetched %s/%s comments for issue %s (server returned fewer rows than reported total)",
                len(all_comments),
                expected_total,
                issue_id_or_key,
            )
        return all_comments

    async def handle_webhook_notification(self, notification: dict) -> None:
        pass

    # ============================================================================
    # Public API Methods
    # ============================================================================

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific record"""
        return ""

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Jira using DataSource"""
        try:
            if not self.data_source:
                await self.init()

            # Test by fetching user info (GET /rest/api/2/myself — DC)
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_current_user_v2()
            return response.status == HttpStatusCode.OK.value
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
            # Clear attachments cache
            self._issue_attachments_cache.clear()

            self.logger.info("Jira connector cleanup completed")
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")

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
                # Stream attachment content via secure URL (client base_url)
                attachment_id = record.external_record_id.replace("attachment_", "")
                filename = record.record_name if hasattr(record, 'record_name') else None
                if not filename:
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_REQUEST.value,
                        detail=f"Attachment {attachment_id} is missing filename for download",
                    )

                datasource = await self._get_fresh_datasource()
                response = await datasource.get_secure_attachment_v2(
                    id=attachment_id,
                    filename=filename,
                )

                if response.status != HttpStatusCode.OK.value:
                    error_body = response.text()
                    detail = f"Failed to fetch attachment content: {error_body}"
                    if response.status == HttpStatusCode.NOT_FOUND.value:
                        self.logger.warning(
                            f"Attachment {attachment_id} not found at source "
                            f"(record {record.external_record_id}) — likely deleted in Jira"
                        )
                    else:
                        self.logger.error(
                            "Failed to fetch Jira attachment content for stream_record: "
                            "attachment_id=%s record_id=%s filename=%s status=%s response=%s",
                            attachment_id,
                            record.id,
                            filename,
                            response.status,
                            error_body[:500],
                        )
                    raise HTTPException(status_code=response.status, detail=detail)

                # Stream the attachment content
                async def generate_attachment() -> AsyncGenerator[bytes, None]:
                    content_bytes = response.bytes()
                    yield content_bytes

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

                return create_stream_record_response(
                    generate_attachment(),
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
            # Already a structured HTTP error (e.g. 404 from a deleted
            # attachment) — surface it unchanged so the router preserves
            # the upstream status instead of collapsing it into a 500.
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
        self, record: Record
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch record from source and return data for reindexing.

        Note: Comments are no longer separate records - they are processed as Blocks
        within the issue's BlocksContainer during streaming.
        """
        try:
            if record.record_type == RecordType.TICKET:
                return await self._check_and_fetch_updated_issue(record)
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_issue(
        self, record: Record
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
            response = await datasource.get_issue_v2(
                issueIdOrKey=issue_id,
                expand=[]
            )
            if response.status == HttpStatusCode.GONE.value or response.status == HttpStatusCode.BAD_REQUEST.value:
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

            # Build user lookup from emailAddress if available (for _extract_issue_data).
            # Legacy DC issues expose only ``key``/``name`` on user references (no
            # ``accountId``); ``_fetch_users`` keys ``source_user_id`` by
            # ``accountId or key or name`` (~1326), so use the same fallback here.
            user_by_account_id = {}
            for user_field in ["creator", "reporter", "assignee"]:
                # Jira returns JSON ``null`` for unassigned users; ``get(k, {})`` keeps
                # ``None`` when the key exists — normalize to an empty dict.
                user_obj = fields.get(user_field) or {}
                identifier = (
                    user_obj.get("accountId")
                    or user_obj.get("key")
                    or user_obj.get("name")
                )
                email = user_obj.get("emailAddress")
                if identifier and email:
                    user_by_account_id[identifier] = AppUser(
                        id="",
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        email=email,
                        full_name=user_obj.get("displayName") or email,
                        source_user_id=identifier
                    )

            issue_data = await self._extract_issue_data_with_parent(issue, user_by_account_id)
            parent_external_id = issue_data["parent_external_id"]

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
                parent_external_record_id=parent_external_id,
                parent_record_type=RecordType.TICKET if parent_external_id else None,
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
            response = await datasource.get_issue_v2(
                issueIdOrKey=issue_id,
                expand=[]
            )
            if response.status == HttpStatusCode.GONE.value or response.status == HttpStatusCode.BAD_REQUEST.value:
                self.logger.warning(f"Parent issue {issue_id} not found at source")
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

            # Check if created timestamp changed (attachments don't have updated field)
            current_created = attachment_data.get("created")
            current_created_at = self._parse_jira_timestamp(current_created) if current_created else 0

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at == current_created_at:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source")
                return None

            self.logger.info(f"🔄 Attachment {attachment_id} has changed at source")

            # Get attachment metadata
            filename = attachment_data.get("filename", "unknown")
            file_size = attachment_data.get("size", 0)
            mime_type = attachment_data.get("mimeType", MimeTypes.UNKNOWN.value)

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

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> BaseConnector:
        dep = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await dep.initialize()
        return cls(logger, dep, data_store_provider, config_service, connector_id, scope, created_by)
