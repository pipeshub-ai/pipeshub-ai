"""Jira Data Center Personal connector — single-user sync without permission APIs."""

from logging import Logger
from typing import Any, Optional
from uuid import uuid4

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.connector.connector_service import BaseConnector, ConnectorInitError
from app.config.constants.arangodb import AppGroups, CollectionNames, Connectors, PermissionModel
from app.config.constants.http_status_code import HttpStatusCode
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
)
from app.models.entities import AppUser, Record, RecordGroup, RecordGroupType, RecordType
from app.models.permission import Permission
from app.services.notification.types import NotificationSeverity, NotificationType

_RECORD_SCAN_PAGE_SIZE = 1000
_ISSUE_ID_PAGE_SIZE = 100


def _excludes(project_keys_operator: FilterOperatorType | None) -> bool:
    if not project_keys_operator:
        return False
    value = (
        project_keys_operator.value
        if hasattr(project_keys_operator, "value")
        else str(project_keys_operator)
    )
    return value == "not_in"


@(
    ConnectorBuilder("Jira Data Center Personal")
    .in_group(AppGroups.ATLASSIAN.value)
    .with_description(
        "Sync Jira Data Center issues visible to your account into your personal workspace"
    )
    .with_categories(["IT Service Management", "Storage"])
    .with_scopes([ConnectorScope.PERSONAL.value])
    .with_permission_model(PermissionModel.APP_LEVEL)
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

            # The creator's user-app link is the gate. Projects inherit from the app.
            await self.ensure_creator_user_app_relation()

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

            await self._remove_issues_no_longer_visible(
                [group for group, _ in projects],
                set(sync_stats.get("failed_project_keys") or []),
                allowed_keys,
                project_keys_operator,
            )

            placeholders_backfilled = await self._sweep_placeholder_records(
                synced_project_ids={p.external_group_id for p, _ in projects},
                full_sync_project_ids=sync_stats.get("full_sync_project_ids") or set(),
            )

            failed_keys = sync_stats.get("failed_project_keys") or []
            if failed_keys:
                preview = ", ".join(failed_keys[:10])
                if len(failed_keys) > 10:
                    preview = f"{preview}, and {len(failed_keys) - 10} more"
                self.logger.warning(
                    "⚠️ Jira DC Personal sync: %s/%s project(s) failed to sync issues: %s",
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
                )

            self.logger.info(
                "✅ Jira DC Personal connector %s sync completed. Total: %s issues "
                "(New: %s, Updated: %s) across %s projects; placeholders backfilled: %s",
                self.connector_id,
                sync_stats["total_synced"],
                sync_stats["new_count"],
                sync_stats["updated_count"],
                len(projects),
                placeholders_backfilled,
            )

        except Exception as e:
            self.logger.error("Error during Jira DC Personal sync: %s", e, exc_info=True)
            if not isinstance(e, ConnectorInitError):
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title=self._notification_title("sync failed"),
                    message=(
                        f"The sync stopped due to an error: {str(e)[:200]}. Recent Jira changes "
                        "may not be reflected yet. Run the sync again; if it keeps failing, "
                        "check the connector's configuration."
                    ),
                )
            raise

    async def _fetch_projects(
        self,
        project_keys: Optional[list[str]] = None,
        project_keys_operator: Optional[FilterOperatorType] = None,
        jira_users: Optional[list[AppUser]] = None,
    ) -> tuple[list[tuple[RecordGroup, list[Permission]]], list[dict[str, Any]]]:
        """List projects. Each one inherits from the app and carries no grant."""
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

        # Keeps the creator's user-app link. The group permission is not written
        # on the project: an empty list clears one an older sync stored.
        if self.creator_email:
            await self.ensure_creator_user_app_relation()
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
                inherit_permissions=True,
            )

            record_groups.append((record_group, []))

        self.logger.info(
            "Jira DC Personal connector %s: _fetch_projects returning %s record groups (raw=%s)",
            self.connector_id,
            len(record_groups),
            len(projects),
        )
        return record_groups, projects

    async def _remove_issues_no_longer_visible(
        self,
        projects: list[RecordGroup],
        failed_project_keys: set[str],
        project_keys: list[str] | None,
        project_keys_operator: FilterOperatorType | None,
    ) -> None:
        """Remove issues and projects this account can no longer see.

        A dead token looks like an empty project list, so nothing is removed
        unless Jira first confirms the account.
        """
        if not await self._signed_in_to_jira():
            self.logger.warning(
                "Jira did not confirm this connector's account, so no issue is removed in this sync."
            )
            return
        try:
            await self._remove_projects_out_of_view(
                {project.external_group_id for project in projects},
                project_keys,
                project_keys_operator,
            )
        except Exception as e:
            self.logger.warning(
                "Could not check for projects that left this account's view; retrying next sync: %s", e
            )
        await self._remove_issues_gone_from_jira(
            [project for project in projects if project.short_name not in failed_project_keys]
        )

    async def _signed_in_to_jira(self) -> bool:
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_current_user_v2()
        except Exception as e:
            self.logger.warning("Could not ask Jira which account this connector uses: %s", e)
            return False
        if not response or response.status != HttpStatusCode.OK.value:
            return False
        profile = self._safe_json_parse(response, "GET /rest/api/2/myself")
        return isinstance(profile, dict) and bool(profile.get("key") or profile.get("name"))

    async def _remove_projects_out_of_view(
        self,
        listed_project_ids: set[str],
        project_keys: list[str] | None,
        project_keys_operator: FilterOperatorType | None,
    ) -> None:
        for project_id, project_key in await self._stored_projects():
            if project_id in listed_project_ids:
                continue
            if project_keys and (project_key in project_keys) == _excludes(project_keys_operator):
                continue
            try:
                await self._remove_project_out_of_view(project_id, project_key)
            except Exception as e:
                self.logger.warning(
                    "Could not finish removing project %s, which left this account's view; "
                    "retrying next sync: %s",
                    project_key, e,
                )

    async def _stored_projects(self) -> list[tuple[str, str]]:
        groups = await self.data_entities_processor.get_nodes_by_filters(
            collection=CollectionNames.RECORD_GROUPS.value,
            filters={"connectorId": self.connector_id, "groupType": RecordGroupType.PROJECT.value},
            return_fields=["externalGroupId", "shortName", "isDeletedAtSource"],
        )
        return [
            (str(group["externalGroupId"]), str(group["shortName"]))
            for group in groups or []
            if isinstance(group, dict)
            and group.get("externalGroupId")
            and group.get("shortName")
            and not group.get("isDeletedAtSource")
        ]

    async def _remove_project_out_of_view(self, project_id: str, project_key: str) -> None:
        datasource = await self._get_fresh_datasource()
        response = await datasource.get_project_v2(projectIdOrKey=project_id)
        if response.status not in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.GONE.value):
            self.logger.info(
                "Project %s is not in Jira's project list, but Jira did not say it is gone (HTTP %s); "
                "nothing removed",
                project_key, response.status,
            )
            return
        await self.issues_sync_point.delete_sync_point(f"project_{project_key}")
        stored = await self._stored_issues(project_id, with_placeholders=True)
        removed = await self._remove_unlisted_issues(project_key, stored, set())
        self.logger.info(
            "Project %s is no longer visible to this account in Jira: removed %d of its %d stored issue(s)",
            project_key, removed, len(stored),
        )
        if await self.data_entities_processor.get_records_in_record_group(self.connector_id, project_id, 1):
            return
        if not await self.data_entities_processor.on_record_group_deleted(project_id, self.connector_id):
            self.logger.warning("Could not remove the emptied project %s; retrying next sync", project_key)

    async def _remove_issues_gone_from_jira(self, projects: list[RecordGroup]) -> None:
        compared: set[str] = set()
        removed = 0
        for project in projects:
            project_id = project.external_group_id
            if not project_id or project_id in compared:
                continue
            compared.add(project_id)
            try:
                stored = await self._stored_issues(project_id)
            except Exception as e:
                self.logger.warning(
                    "Could not read the stored issues of project %s; nothing removed: %s",
                    project.short_name, e,
                )
                continue
            if not stored:
                continue
            listed = await self._list_project_issue_ids(project.short_name)
            if listed is None:
                continue
            removed += await self._remove_unlisted_issues(project.short_name, stored, listed)
        if removed:
            self.logger.info("Removed %d issue(s) Jira no longer has, found by comparing ids", removed)

    async def _list_project_issue_ids(self, project_key: str) -> set[str] | None:
        jql = f'project = "{project_key}" ORDER BY id ASC'
        ids: set[str] = set()
        start_at = 0
        while True:
            try:
                response = await self._search_issues_with_retry(
                    project_key=project_key,
                    jql=jql,
                    start_at=start_at,
                    max_results=_ISSUE_ID_PAGE_SIZE,
                    fields=["id"],
                )
            except Exception as e:
                self.logger.warning("Could not list the issues of project %s; nothing removed: %s", project_key, e)
                return None
            data = (
                self._safe_json_parse(response, f"issue id listing for {project_key}")
                if response.status == HttpStatusCode.OK.value else None
            )
            issues = data.get("issues") if isinstance(data, dict) else None
            total = data.get("total") if isinstance(data, dict) else None
            if not isinstance(issues, list) or not all(isinstance(i, dict) and i.get("id") for i in issues):
                self.logger.warning(
                    "Could not list the issues of project %s (HTTP %s); nothing removed",
                    project_key, response.status,
                )
                return None
            if not isinstance(total, int):
                self.logger.warning("Could not list the issues of project %s; nothing removed", project_key)
                return None
            page_ids = {str(i["id"]) for i in issues}
            if not issues or start_at + len(issues) >= total:
                return ids | page_ids
            if not (page_ids - ids):
                self.logger.warning("Can't follow the issue listing of project %s; nothing removed", project_key)
                return None
            ids |= page_ids
            start_at += len(issues)

    async def _stored_issues(self, project_id: str, *, with_placeholders: bool = False) -> list[Record]:
        stored: list[Record] = []
        after_key: str | None = None
        while True:
            page = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, project_id, _RECORD_SCAN_PAGE_SIZE, after_key,
            )
            stored.extend(
                r for r in page
                if r.record_type == RecordType.TICKET and (with_placeholders or not r.is_placeholder)
            )
            if len(page) < _RECORD_SCAN_PAGE_SIZE:
                return stored
            after_key = page[-1].id

    async def _remove_unlisted_issues(self, project_key: str, stored: list[Record], listed: set[str]) -> int:
        removed = 0
        for record in stored:
            if record.external_record_id in listed:
                continue
            try:
                if await self._issue_gone_from_jira(record.external_record_id):
                    await self.data_entities_processor.on_records_deleted_cascade(
                        [record.id], self.connector_id, cascade_children=False,
                    )
                    removed += 1
            except Exception as e:
                self.logger.warning(
                    "Could not remove issue %s of project %s; retrying next sync: %s",
                    record.external_record_id, project_key, e,
                )
        return removed

    async def _issue_gone_from_jira(self, issue_ref: str) -> bool:
        response = await self._get_issue_with_retry(issue_ref, fields=["id"])
        if response.status == HttpStatusCode.OK.value:
            return False
        if response.status not in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.GONE.value):
            raise Exception(
                f"Deletion of {issue_ref} unconfirmed: get_issue returned {response.status}"
            )
        return True

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
    ) -> BaseConnector:
        logger.info(
            "Jira DC Personal connector factory: create_connector(connector_id=%s, scope=%s, created_by=%s)",
            connector_id,
            scope,
            created_by,
        )
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
