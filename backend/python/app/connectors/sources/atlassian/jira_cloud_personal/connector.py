"""Jira Cloud Personal connector — single-user sync without permission APIs.

Subclasses ``JiraConnector`` (Jira Cloud) and overrides only the user / permission
plumbing so the personal variant:

  * Never lists Jira users, groups, application roles, or project permission
    schemes (no ``/rest/api/3/users``, ``/group``, ``/applicationrole``,
    ``/project/{key}/permissionscheme`` calls).
  * Routes every project ``RecordGroup`` ACL through the shared internal
    ``ConnectorGroup`` (see ``BaseConnector.ensure_connector_group_permission``)
    so the connector creator — and only the creator — has read access.

All issue fetching / ADF parsing / attachment handling / streaming / reindexing
logic is inherited unchanged from the workspace connector.
"""

from logging import Logger
from typing import Any, Optional
from uuid import uuid4

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
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
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.atlassian.core.apps import JiraCloudPersonalApp
from app.connectors.sources.atlassian.core.oauth import AtlassianScope
from app.connectors.sources.atlassian.jira_cloud.connector import (
    AUTHORIZE_URL,
    TOKEN_URL,
    JiraConnector,
    adf_to_text,
)
from app.models.entities import AppUser, RecordGroup, RecordGroupType
from app.models.permission import Permission


@(
    ConnectorBuilder("Jira Cloud Personal")
    .in_group(AppGroups.ATLASSIAN.value)
    .with_description(
        "Sync Jira Cloud issues visible to your account into your personal workspace"
    )
    .with_categories(["IT Service Management", "Storage"])
    .with_scopes([ConnectorScope.PERSONAL.value])
    .with_auth(
        [
            AuthBuilder.type(AuthType.OAUTH).oauth(
                connector_name="Jira Cloud Personal",
                authorize_url=AUTHORIZE_URL,
                token_url=TOKEN_URL,
                redirect_uri="connectors/oauth/callback/JiraCloudPersonal",
                # ``personal_sync`` uses a deliberately narrower scope set
                # than the workspace connector — see
                # ``AtlassianScope.get_jira_personal_read_access`` for which
                # workspace-only scopes (groups, roles, audit log, app roles)
                # are dropped. ``team_sync`` mirrors ``personal_sync`` so the
                # OAuth-flow scope resolver in ``_build_oauth_flow_config``
                # still emits a non-empty ``scope=`` parameter even if the
                # per-instance ``connectorScope`` somehow defaults to ``team``
                # — an empty ``scope`` is what ``auth.atlassian.com`` rejects
                # with ``error=server_error``.
                scopes=OAuthScopeConfig(
                    personal_sync=AtlassianScope.get_jira_personal_read_access(),
                    team_sync=AtlassianScope.get_jira_personal_read_access(),
                    agent=[],
                ),
                fields=[
                    AuthField(
                        name="clientId",
                        display_name="Application (Client) ID",
                        placeholder="Enter your Atlassian Cloud Application ID",
                        description="The Application (Client) ID from Atlassian Developer Console",
                    ),
                    AuthField(
                        name="clientSecret",
                        display_name="Client Secret",
                        placeholder="Enter your Atlassian Cloud Client Secret",
                        description="The Client Secret from Atlassian Developer Console",
                        field_type="PASSWORD",
                        is_secret=True,
                    ),
                    AuthField(
                        name="baseUrl",
                        display_name="Atlassian site URL",
                        placeholder="https://yourcompany.atlassian.net",
                        description="Atlassian site URL to use. Must match the Jira site you want to sync.",
                        field_type="URL",
                        required=True,
                        max_length=2000,
                        is_secret=False,
                    ),
                ],
                icon_path=IconPaths.connector_icon(Connectors.JIRA_PERSONAL.value),
                app_group="Atlassian",
                app_description="OAuth application for accessing Jira Cloud as a single user",
                app_categories=["IT Service Management", "Storage"],
            ),
            AuthBuilder.type(AuthType.API_TOKEN).fields(
                [
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
                ]
            ),
        ]
    )
    .with_info(
        "Syncs only projects and issues visible to the credentials you provide. "
        "Access is limited to you — the user who created this connector — without "
        "reading Jira permission schemes, groups, or other users."
        + "\n\n"
        + CONNECTOR_EMAIL_IDENTITY_INFO
    )
    .configure(
        lambda builder: builder.with_icon(
            IconPaths.connector_icon(Connectors.JIRA_PERSONAL.value)
        )
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "Jira Cloud API Setup",
                "https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/",
                "setup",
            )
        )
        .add_documentation_link(
            DocumentationLink(
                "Pipeshub Documentation",
                "https://docs.pipeshub.com/connectors/jira/jira",
                "pipeshub",
            )
        )
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
        .add_filter_field(
            FilterField(
                name=SyncFilterKey.PROJECT_KEYS.value,
                display_name="Project Keys",
                filter_type=FilterType.LIST,
                category=FilterCategory.SYNC,
                description="Filter issues by project keys (e.g., PROJ1, PROJ2)",
                option_source_type=OptionSourceType.DYNAMIC,
            )
        )
        .add_filter_field(
            CommonFields.modified_date_filter("Filter issues by modification date.")
        )
        .add_filter_field(
            CommonFields.created_date_filter("Filter issues by creation date.")
        )
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.ISSUES.value,
                display_name="Index Issues",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Enable indexing of issues",
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.ISSUE_ATTACHMENTS.value,
                display_name="Index Issue and comment Attachments",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Enable indexing of issue attachments",
                default_value=True,
            )
        )
    )
    .build_decorator()
)
class JiraCloudPersonalConnector(JiraConnector):
    """Personal Jira Cloud: creator-only permissions, no user/group/role/scheme API calls."""

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
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        # Override the parent's JiraApp / Connectors.JIRA defaults so every
        # downstream RecordGroup / AppUser / TicketRecord / FileRecord built
        # by inherited helpers is stamped with the personal connector name.
        # The parent was refactored to use ``self.connector_name`` instead of
        # the hardcoded ``Connectors.JIRA`` constant for exactly this reason.
        self.app = JiraCloudPersonalApp(connector_id)
        self.connector_name = self.app.get_app_name()

    async def run_sync(self) -> None:
        """Sync projects and issues visible to the configured user; no permission APIs."""
        try:
            if not self.data_source:
                await self.init()

            # Force a fresh ConnectorGroup upsert each run so re-runs after the
            # creator email is rotated pick up the new identity instead of
            # reusing a stale cached permission.
            self._connector_group_permission = None

            if not self.creator_email and self.created_by:
                try:
                    creator = await self.data_entities_processor.get_user_by_user_id(
                        self.created_by
                    )
                    if creator and getattr(creator, "email", None):
                        self.creator_email = creator.email
                except Exception as e:
                    self.logger.warning(
                        "Jira Cloud Personal connector %s: could not resolve creator "
                        "email for created_by %s: %s",
                        self.connector_id,
                        self.created_by,
                        e,
                    )

            if not self.creator_email:
                self.logger.warning(
                    "Jira Cloud Personal connector %s: no creator email — "
                    "projects will sync without user permissions",
                    self.connector_id,
                )

            # Upsert the pseudo ConnectorGroup (creator becomes a member) and cache
            # the GROUP permission for use on every project record group below.
            # Routing access via this internal group lets new users be added later
            # without rewriting per-project ACLs.
            await self.ensure_connector_group_permission()

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "jira",
                self.connector_id,
                self.logger,
            )

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
                            else str(project_keys_operator)
                            if project_keys_operator
                            else "in"
                        )
                        action = "Excluding" if operator_value == "not_in" else "Including"
                        self.logger.info(
                            "Project keys filter: %s projects: %s", action, allowed_keys
                        )
                    else:
                        allowed_keys = None
                        self.logger.info(
                            "Project keys filter is empty — syncing all visible projects (Cloud Personal)"
                        )

            projects, _raw_projects = await self._fetch_projects(
                allowed_keys, project_keys_operator, []
            )

            await self.data_entities_processor.on_new_record_groups(projects)

            last_sync_time = await self._get_issues_sync_checkpoint()
            sync_stats = await self._sync_all_project_issues(projects, [], last_sync_time)

            await self._update_issues_sync_checkpoint(sync_stats, len(projects))

            self.logger.info(
                "Jira Cloud Personal sync completed. Total: %s issues "
                "(New: %s, Updated: %s)",
                sync_stats["total_synced"],
                sync_stats["new_count"],
                sync_stats["updated_count"],
            )

        except Exception as e:
            self.logger.error(
                "Error during Jira Cloud Personal sync: %s", e, exc_info=True
            )
            raise

    async def _fetch_projects(
        self,
        project_keys: Optional[list[str]] = None,
        project_keys_operator: Optional[FilterOperatorType] = None,
        jira_users: Optional[list[AppUser]] = None,
    ) -> tuple[list[tuple[RecordGroup, list[Permission]]], list[dict[str, Any]]]:
        """List projects via paginated ``search_projects``; grant ConnectorGroup READ only.

        Reuses the parent's ``_list_projects_with_filter`` helper for the actual
        listing + pagination + client-side exclusion. Skips
        ``_fetch_application_roles_to_groups_mapping`` and
        ``_fetch_project_permission_scheme`` — both of which require admin-level
        scopes that the personal flow intentionally does not request.
        """
        del jira_users  # unused — personal connector does not sync Jira users

        projects = await self._list_projects_with_filter(
            project_keys, project_keys_operator
        )

        group_permission = self._connector_group_permission
        if group_permission is None:
            # Idempotent — returns the cached permission if already created upstream.
            group_permission = await self.ensure_connector_group_permission()
        project_permissions: list[Permission] = (
            [group_permission] if group_permission else []
        )

        record_groups: list[tuple[RecordGroup, list[Permission]]] = []
        for project in projects:
            project_id = project.get("id")
            project_name = project.get("name")
            project_key = project.get("key")

            description = project.get("description")
            if description and isinstance(description, dict):
                description = adf_to_text(description)
            elif not description:
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

            record_groups.append((record_group, list(project_permissions)))

            if project_permissions:
                self.logger.debug(
                    "Project %s: granted access via ConnectorGroup",
                    project_key,
                )

        return record_groups, projects

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
    ) -> BaseConnector:
        dep = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await dep.initialize()
        return cls(
            logger,
            dep,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
