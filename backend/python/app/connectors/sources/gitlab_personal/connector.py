from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, PermissionModel
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
from app.connectors.sources.gitlab.projects import ProjectsSync, _parent_when_synced
from app.connectors.sources.gitlab_personal.common.apps import GitLabPersonalApp
from app.models.entities import RecordGroup, RecordGroupType
from app.models.permission import Permission


class GitLabPersonalProjectsSync(ProjectsSync):
    """ProjectsSync variant for the personal connector.

    Project and GitLab-group record groups carry no grants. They inherit up to
    the app, which the creator reaches through their user-to-app edge.
    """

    async def _sync_project_members_as_pseudo(self, project: Project) -> None:
        """Skip GitLab membership. Project groups inherit the app."""
        await self._apply_creator_fallback_for_project(project)

    async def _apply_creator_fallback_for_project(self, project: Project) -> None:
        """Project groups inherit the app. No per-group grant."""
        groups = self._build_project_record_groups(project)
        for group in groups:
            group.inherit_permissions = True
        await self.c.data_entities_processor.on_new_record_groups([(group, []) for group in groups])

    async def _ensure_gitlab_group_record_groups(
        self,
        group_paths: list[str],
        candidate_projects: list[Project] | None = None,
    ) -> None:
        """GitLab-group record groups inherit the app. No grants of their own."""
        c = self.c
        if not c.data_source:
            return
        self.logger.info(
            "Ensuring GitLab personal group record groups for %s", group_paths
        )
        for group_path in sorted(group_paths, key=lambda path: str(path).count("/")):
            group_res = await c.runtime.ds_call(c.data_source.get_group, group_path)
            if group_res.success is True and group_res.data:
                group = group_res.data
                full_path = getattr(group, "full_path", None) or group_path
                name = getattr(group, "name", full_path) or full_path
                web_url = getattr(group, "web_url", None)
            else:
                full_path = group_path
                name = group_path
                web_url = None
            parent_path = _parent_when_synced(full_path, group_paths)
            group_rg = RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name=name,
                group_type=RecordGroupType.PROJECT.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=full_path,
                parent_external_group_id=parent_path,
                web_url=web_url,
                inherit_permissions=True,
            )
            await c.data_entities_processor.on_new_record_groups([(group_rg, [])])


@(
    ConnectorBuilder("GitLab Personal")
    .in_group("GitLab")
    .with_description("Sync content from your personal GitLab account")
    .with_categories(["Knowledge Management"])
    .with_scopes([ConnectorScope.PERSONAL.value])
    # APP_LEVEL: the only user-to-app edge this connector writes is the creator's
    # (ensure_creator_user_app_relation). Groups inherit that edge. A later grant
    # of the app to someone else would open every synced project.
    .with_permission_model(PermissionModel.APP_LEVEL)
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
                display_name="Repository",
                description="Select the repository to sync.",
                filter_type=FilterType.SELECT,
                category=FilterCategory.SYNC,
                option_source_type=OptionSourceType.DYNAMIC,
                required=True,
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
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_agent_support(False)
    )
    .build_decorator()
)
class GitLabPersonalConnector(GitLabConnector):
    """Personal GitLab connector.

    No GitLab membership list is synced. Record groups carry no grants and
    inherit up to the app. The creator reaches them through their user-to-app edge.
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
        self.projects = GitLabPersonalProjectsSync(self)

    def creator_user_permission(self) -> Permission | None:
        """Unused by the personal project sync, which inherits the app.

        Kept so a call that still reaches the base helper does not fall through
        to a per-user owner grant. Returns the cached group permission, or
        ``None`` when that cache has not been filled.
        """
        return self._connector_group_permission

    async def run_sync(self) -> None:
        """Sync GitLab content visible to the configured user. Groups inherit the app."""
        try:
            await self.runtime.refresh_token_if_needed()
            self.logger.info("Starting GitLab Personal sync")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "gitlabpersonal", self.connector_id, self.logger
            )
            self._gitlab_included_group_paths = None
            # The personal project path does not read this cache. Clear it so a
            # stray creator_user_permission call cannot reuse the previous run.
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
                # The creator's user-to-app edge is what inherited groups resolve.
                await self.ensure_creator_user_app_relation()

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
        data_entities_processor,
        **kwargs,
    ) -> BaseConnector:
        """Factory method to create a GitLab Personal connector instance."""
        return GitLabPersonalConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
