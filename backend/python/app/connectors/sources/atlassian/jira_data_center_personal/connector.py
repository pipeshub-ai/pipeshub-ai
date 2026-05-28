"""Jira Data Center Personal connector — single-user sync without permission APIs."""

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
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.atlassian.core.apps import JiraDataCenterPersonalApp
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
    adf_to_text,
)
from app.models.entities import AppUser, RecordGroup, RecordGroupType
from app.models.permission import Permission


@(
    ConnectorBuilder("Jira Data Center Personal")
    .in_group(AppGroups.ATLASSIAN.value)
    .with_description(
        "Sync Jira Data Center issues visible to your account into your personal workspace"
    )
    .with_categories(["IT Service Management", "Storage"])
    .with_scopes([ConnectorScope.PERSONAL.value])
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
                        placeholder="your-jira-username",
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
        "Syncs only projects and issues visible to the credentials you provide. "
        "Access is limited to you — the user who created this connector — without "
        "reading Jira permission schemes or other users."
        + "\n\n"
        + CONNECTOR_EMAIL_IDENTITY_INFO
    )
    .configure(
        lambda builder: builder.with_icon(
            IconPaths.connector_icon(Connectors.JIRA_DATA_CENTER_PERSONAL.value)
        )
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
class JiraDataCenterPersonalConnector(JiraDataCenterConnector):
    """Personal Jira DC: creator-only permissions, no user/group/role/scheme API calls."""

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
        self.app = JiraDataCenterPersonalApp(connector_id)
        self.connector_name = self.app.get_app_name()
        self.logger.info(
            "Jira DC Personal connector %s instantiated (scope=%s, created_by=%s, app=%s)",
            self.connector_id,
            scope,
            created_by,
            self.connector_name,
        )

    async def run_sync(self) -> None:
        """Sync projects and issues visible to the configured user; no permission APIs."""
        try:
            self.logger.info(
                "▶️ Jira DC Personal connector %s: run_sync starting (data_source_ready=%s, site_url=%s)",
                self.connector_id,
                bool(self.data_source),
                self.site_url,
            )
            if not self.data_source:
                # ``init()`` returns False on missing/invalid auth config; check
                # the return so a misconfigured connector raises here instead of
                # surfacing ``ValueError("DataSource not initialized")`` from
                # the first datasource call several layers down.
                self.logger.info(
                    "Jira DC Personal connector %s: data source not initialized — calling init()",
                    self.connector_id,
                )
                if not await self.init():
                    raise RuntimeError(
                        f"Jira Data Center Personal connector {self.connector_id} init failed; "
                        "check auth configuration (authType / baseUrl / credentials)"
                    )
                self.logger.info(
                    "Jira DC Personal connector %s: init() succeeded (site_url=%s)",
                    self.connector_id,
                    self.site_url,
                )

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
                        "Jira Data Center Personal connector %s: could not resolve creator "
                        "email for created_by %s: %s",
                        self.connector_id,
                        self.created_by,
                        e,
                    )

            if not self.creator_email:
                self.logger.warning(
                    "Jira Data Center Personal connector %s: no creator email — "
                    "projects will sync without user permissions",
                    self.connector_id,
                )
            else:
                self.logger.info(
                    "Jira DC Personal connector %s: creator_email=%s",
                    self.connector_id,
                    self.creator_email,
                )

            # Upsert the pseudo ConnectorGroup (creator becomes a member) and cache
            # the GROUP permission for use on every project record group below.
            # Routing access via this internal group lets new users be added later
            # without rewriting per-project ACLs.
            group_permission = await self.ensure_connector_group_permission()
            self.logger.info(
                "Jira DC Personal connector %s: connector group permission ready (granted=%s)",
                self.connector_id,
                bool(group_permission),
            )

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "jira",
                self.connector_id,
                self.logger,
            )
            self.logger.info(
                "Jira DC Personal connector %s: filters loaded (sync_keys=%s, indexing_keys=%s)",
                self.connector_id,
                list((self.sync_filters or {}).keys()),
                list((self.indexing_filters or {}).keys()),
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
                            "Project keys filter is empty — syncing all visible projects (DC Personal)"
                        )

            projects, _raw_projects = await self._fetch_projects(
                allowed_keys, project_keys_operator, []
            )
            self.logger.info(
                "Jira DC Personal connector %s: %s project record groups prepared for sync",
                self.connector_id,
                len(projects),
            )

            await self.data_entities_processor.on_new_record_groups(projects)

            last_sync_time = await self._get_issues_sync_checkpoint()
            self.logger.info(
                "Jira DC Personal connector %s: issues checkpoint=%s — starting issue sync",
                self.connector_id,
                last_sync_time,
            )
            sync_stats = await self._sync_all_project_issues(projects, [], last_sync_time)

            await self._update_issues_sync_checkpoint(sync_stats, len(projects))

            self.logger.info(
                "✅ Jira DC Personal connector %s sync completed. Total: %s issues "
                "(New: %s, Updated: %s) across %s projects",
                self.connector_id,
                sync_stats["total_synced"],
                sync_stats["new_count"],
                sync_stats["updated_count"],
                len(projects),
            )

        except Exception as e:
            self.logger.error("Error during Jira DC Personal sync: %s", e, exc_info=True)
            raise

    async def _fetch_projects(
        self,
        project_keys: Optional[list[str]] = None,
        project_keys_operator: Optional[FilterOperatorType] = None,
        jira_users: Optional[list[AppUser]] = None,
    ) -> tuple[list[tuple[RecordGroup, list[Permission]]], list[dict[str, Any]]]:
        """List projects via GET /rest/api/2/project; grant creator READ only."""
        del jira_users  # unused — personal connector does not sync Jira users

        if not self.data_source:
            raise ValueError("DataSource not initialized")

        self.logger.info(
            "Jira DC Personal connector %s: _fetch_projects called (project_keys=%s, operator=%s)",
            self.connector_id,
            project_keys,
            project_keys_operator,
        )

        all_projects = await self._list_all_projects_dc()
        self.logger.info(
            "Jira DC Personal connector %s: fetched %s projects from /rest/api/2/project",
            self.connector_id,
            len(all_projects),
        )

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
                self.logger.info(
                    "Excluding project keys (client-side filter): %s", project_keys
                )
                excluded_keys = set(project_keys)
                projects = [
                    p
                    for p in all_projects
                    if p.get("key") and p.get("key") not in excluded_keys
                ]
            else:
                self.logger.info(
                    "Including only project keys (client-side filter): %s", project_keys
                )
                allowed = set(project_keys)
                projects = [
                    p for p in all_projects if p.get("key") and p.get("key") in allowed
                ]
        else:
            self.logger.info("No project key filter — syncing all visible projects (DC Personal)")
            projects = list(all_projects)

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

        self.logger.info(
            "Jira DC Personal connector %s: _fetch_projects returning %s record groups (raw=%s)",
            self.connector_id,
            len(record_groups),
            len(projects),
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
    ) -> BaseConnector:
        logger.info(
            "Jira DC Personal connector factory: create_connector(connector_id=%s, scope=%s, created_by=%s)",
            connector_id,
            scope,
            created_by,
        )
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
