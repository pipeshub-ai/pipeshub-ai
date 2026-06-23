from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
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
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from gitlab.v4.objects import Project
from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.gitlab.constants import GITLAB_CLOUD_URL
from app.connectors.sources.gitlab.projects import ProjectsSync
from app.connectors.sources.gitlab_personal.common.apps import GitLabPersonalApp
from app.models.entities import RecordGroup, RecordGroupType
from app.models.permission import Permission


class GitLabPersonalProjectsSync(ProjectsSync):
    """ProjectsSync variant for the personal connector.

    Overrides the two permission-building hooks so every project and GitLab-group
    ``RecordGroup`` is granted access through the single internal ``ConnectorGroup``
    (materialized by ``ensure_connector_group_permission``) rather than by fanning
    out individual user edges derived from GitLab membership lists.
    """

    async def _sync_project_members_as_pseudo(self, project: Project) -> None:
        """Route all project access through the ConnectorGroup."""
        await self._apply_creator_fallback_for_project(project)

    async def _ensure_gitlab_group_record_groups(
        self,
        group_paths: list[str],
        candidate_projects: list[Project] | None = None,
    ) -> None:
        """Create GitLab-group record groups with ConnectorGroup permissions only."""
        c = self.c
        if not c.data_source:
            return
        group_permission = c.creator_user_permission()
        if group_permission is None:
            return
        self.logger.info(
            "Ensuring GitLab personal group record groups for %s", group_paths
        )
        for group_path in group_paths:
            group_res = await c.runtime.ds_call(c.data_source.get_group, group_path)
            if group_res.success and group_res.data:
                group = group_res.data
                full_path = getattr(group, "full_path", None) or group_path
                name = getattr(group, "name", full_path) or full_path
                web_url = getattr(group, "web_url", None)
            else:
                full_path = group_path
                name = group_path
                web_url = None
            group_rg = RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name=name,
                group_type=RecordGroupType.PROJECT.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=full_path,
                web_url=web_url,
            )
            await c.data_entities_processor.on_new_record_groups(
                [(group_rg, [group_permission])]
            )


@(
    ConnectorBuilder("GitLab Personal")
    .in_group("GitLab")
    .with_description("Sync content from your personal GitLab account")
    .with_categories(["Knowledge Management"])
    .with_scopes([ConnectorScope.PERSONAL.value])
    .with_auth(
        [
            AuthBuilder.type(AuthType.OAUTH).oauth(
                connector_name="GitLab Personal",
                authorize_url=f"{GITLAB_CLOUD_URL}/oauth/authorize",
                token_url=f"{GITLAB_CLOUD_URL}/oauth/token",
                redirect_uri="connectors/oauth/callback/GitLab%20Personal",
                scopes=OAuthScopeConfig(
                    personal_sync=[
                        "read_user",
                        "read_api",
                        "read_repository",
                    ],
                    team_sync=[],
                    agent=[],
                ),
                fields=[
                    AuthField(
                        name="clientId",
                        display_name="Application (Client) ID",
                        placeholder="Enter your Gitlab Application ID",
                        description="The Application (Client) ID from Gitlab OAuth Registration",
                    ),
                    AuthField(
                        name="clientSecret",
                        display_name="Client Secret",
                        placeholder="Enter your Gitlab Client Secret",
                        description="The Client Secret from Gitlab OAuth Registration",
                        field_type="PASSWORD",
                        is_secret=True,
                    ),
                    AuthField(
                        name="instanceUrl",
                        display_name="GitLab Instance URL",
                        placeholder="https://gitlab.com",
                        description=(
                            "Base URL of your GitLab instance. "
                            "Leave blank or set to https://gitlab.com for GitLab.com (cloud). "
                            "Set to your self-managed host (e.g. https://gitlab.mycompany.com) for GitLab EE."
                        ),
                        required=False,
                    ),
                ],
                icon_path=IconPaths.connector_icon(Connectors.GITLAB.value),
                app_description="OAuth application for accessing Gitlab services",
                app_categories=["Knowledge Management"],
            )
        ]
    )
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)
    .configure(
        lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.GITLAB.value))
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "Gitlab API Docs", "https://docs.gitlab.com/api/rest/", "docs"
            )
        )
        .add_documentation_link(
            DocumentationLink(
                "Pipeshub Documentation",
                "https://docs.pipeshub.com/connectors/gitlab/gitlab",
                "pipeshub",
            )
        )
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_sync_support(True)
        .add_filter_field(
            FilterField(
                name=SyncFilterKey.GROUP_IDS.value,
                display_name="GitLab Groups",
                description=(
                    "Limit sync to projects in these GitLab groups or subgroups "
                    "(uses namespace path, e.g. my-org/engineering)"
                ),
                filter_type=FilterType.MULTISELECT,
                category=FilterCategory.SYNC,
                option_source_type=OptionSourceType.DYNAMIC,
            )
        )
        .add_filter_field(
            FilterField(
                name=SyncFilterKey.PROJECT_IDS.value,
                display_name="Repositories",
                description=(
                    "Limit sync to specific repositories "
                    "(path_with_namespace, e.g. my-org/my-repo)"
                ),
                filter_type=FilterType.MULTISELECT,
                category=FilterCategory.SYNC,
                option_source_type=OptionSourceType.DYNAMIC,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.ISSUES.value,
                display_name="Index Issues",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.MERGE_REQUESTS.value,
                display_name="Index Merge Requests",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.CODE_FILES.value,
                display_name="Index Code Files",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.COMMENTS.value,
                display_name="Index Comments",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                default_value=True,
            )
        )
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_agent_support(False)
    )
    .build_decorator()
)
class GitLabPersonalConnector(GitLabConnector):
    """Personal GitLab connector.

    Permission model:
      - No GitLab user directory is synced.
      - An internal ``AppUserGroup`` (name: ``ConnectorGroup``, scoped by
        ``connector_id``) is materialized once per sync. The connector's
        creator is the sole USER -> GROUP member edge.
      - Every project / GitLab-group ``RecordGroup`` receives a GROUP
        permission edge pointing at that internal group, instead of a
        direct USER edge to the creator.

    This keeps access revocation a single-edge operation (delete the
    creator -> ConnectorGroup edge) and avoids fanning out per-record
    permission churn on token swaps or creator changes.
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
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.app = GitLabPersonalApp(connector_id)
        self.connector_name = Connectors.GITLAB_PERSONAL.value
        # Replace the default ProjectsSync with the personal variant so the two
        # permission-building hooks route access through ConnectorGroup.
        self.projects = GitLabPersonalProjectsSync(self)

    def creator_user_permission(self) -> Permission | None:
        """Override: emit a GROUP permission on the shared ConnectorGroup.

        The workspace connector grants the creator direct USER access. For
        the personal connector we route every record-group permission
        through the single internal ``AppUserGroup`` (``ConnectorGroup``)
        materialized by ``ensure_connector_group_permission`` so that:

        - Adding / removing access to all synced content is a one-edge
          operation on the group, not an N-record fan-out.
        - The group is internal (not derived from any GitLab entity), so
          it does not collide with any real GitLab group.

        Returns ``None`` if the group has not been upserted yet (no
        ``creator_email`` resolved, or called before ``run_sync``);
        downstream callers (``_apply_creator_fallback_for_project``,
        ``_ensure_gitlab_group_record_groups``) already handle ``None``
        by skipping ACL creation.
        """
        return self._connector_group_permission

    async def run_sync(self) -> None:
        """Sync GitLab content visible to the configured user; access routed via ConnectorGroup."""
        try:
            await self.runtime.refresh_token_if_needed()
            self.logger.info("Starting GitLab Personal sync")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "gitlabpersonal", self.connector_id, self.logger
            )
            self._gitlab_included_group_paths = None
            # Force a fresh ConnectorGroup upsert each run so re-runs after the
            # creator email is rotated pick up the new identity instead of
            # reusing a stale cached permission.
            self._connector_group_permission = None

            if not self.creator_email and self.created_by:
                await self._resolve_creator_identity()

            if not self.creator_email:
                self.logger.warning(
                    "GitLab Personal connector %s: no creator email — "
                    "record groups will sync without user permissions",
                    self.connector_id,
                )
            else:
                # Materialize the ConnectorGroup + creator membership edge
                # before any record-group write, so the GROUP-permission
                # lookup in on_new_record_groups resolves on the first
                # write rather than dropping the permission for the first
                # project / group. creator_user_permission (overridden
                # above) reads the cached permission populated here.
                await self.ensure_connector_group_permission()

            self.logger.info("Starting sync of projects")
            await self.projects.sync_all_projects()
        except Exception as e:
            self.logger.error(f"Error in GitLab Personal sync: {e}", exc_info=True)
            raise

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
        """Factory method to create a GitLab Personal connector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return GitLabPersonalConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
