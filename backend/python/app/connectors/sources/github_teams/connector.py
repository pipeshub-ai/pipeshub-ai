"""
GitHub Teams connector — orchestration shell.

This module is intentionally thin: it wires together the focused helper
modules and exposes the ``BaseConnector`` interface. Business logic lives in:

- ``runtime.py``       — API call plumbing: timeout budget, auth-retry, search pacing
- ``users.py``         — principal discovery + 3-phase email resolution
- ``projects.py``      — repo -> RecordGroup sync, collaborator permissions
- ``repos.py``         — code repository (blob/tree) sync — shared with personal
- ``issues.py``        — issue (TICKET) sync + content streaming — shared
- ``pull_requests.py`` — PR (PULL_REQUEST) sync + content streaming — shared
- ``comments.py``      — comment block building — shared
- ``filters.py``       — dynamic filter-option pickers
- ``streaming.py``     — stream_record dispatch and reindex — shared

The existing personal GitHub connector (``github/connector.py``) is refactored
to extend ``GitHubTeamsConnector``, overriding only the permission hooks on
``ProjectsSync`` and the repo-discovery scope — exactly the pattern
``gitlab_personal/connector.py`` uses over ``gitlab/connector.py``.
"""

from __future__ import annotations

from logging import Logger

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
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
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.github_teams.common.apps import GitHubTeamsApp
from app.models.entities import Record
from app.sources.client.github.github import GitHubClient
from app.sources.external.github.github_async import GitHubAsyncDataSource

from .comments import CommentsHelper
from .filters import FiltersHelper
from .issues import IssuesSync
from .projects import ProjectsSync
from .pull_requests import PullRequestsSync
from .repos import ReposSync
from .runtime import RuntimeHelper
from .streaming import StreamingHelper
from .users import UsersSync

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"


@(
    ConnectorBuilder("GitHub Teams")
    .in_group("Github")
    .with_description("Sync content, issues, pull requests, and code from your GitHub organization")
    .with_categories(["Knowledge Management"])
    .with_scopes([ConnectorScope.TEAM.value])
    .with_auth(
        [
            AuthBuilder.type(AuthType.OAUTH).oauth(
                connector_name="GitHub Teams",
                authorize_url=AUTHORIZE_URL,
                token_url=TOKEN_URL,
                redirect_uri="connectors/oauth/callback/Github%20Teams",
                scopes=OAuthScopeConfig(
                    team_sync=["read:org", "repo", "user:email"],
                    personal_sync=[],
                    agent=[],
                ),
                fields=[
                    AuthField(
                        name="clientId",
                        display_name="Application (Client) ID",
                        placeholder="Enter your Github OAuth App Client ID",
                        description="The Client ID from your Github OAuth App registration",
                    ),
                    AuthField(
                        name="clientSecret",
                        display_name="Client Secret",
                        placeholder="Enter your Github OAuth App Client Secret",
                        description="The Client Secret from your Github OAuth App registration",
                        field_type="PASSWORD",
                        is_secret=True,
                    ),
                ],
                app_description="OAuth application for accessing Github organization data",
                app_categories=["Knowledge Management"],
            )
        ]
    )
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)
    .configure(
        lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.GITHUB.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink("Github API Docs", "https://docs.github.com/en/rest", "docs"))
        .add_documentation_link(DocumentationLink("Pipeshub Documentation", "https://docs.pipeshub.com/connectors/github/github", "pipeshub"))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_sync_support(True)
        .add_filter_field(FilterField(
            name=SyncFilterKey.ORG_IDS.value,
            display_name="Github Organizations",
            description="Limit sync to these Github organizations (uses org login, e.g. my-org)",
            filter_type=FilterType.MULTISELECT, category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC,
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.REPO_IDS.value,
            display_name="Repositories",
            description="Limit sync to specific repositories (full_name, e.g. my-org/my-repo)",
            filter_type=FilterType.MULTISELECT, category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.ISSUES.value,
            display_name="Index Issues",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.MERGE_REQUESTS.value,
            display_name="Index Pull Requests",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.CODE_FILES.value,
            display_name="Index Code Files",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_admin_access_required(True, personal_connector_type="Github")
        .with_agent_support(False)
    )
    .build_decorator()
)
class GitHubTeamsConnector(BaseConnector):
    """Connector for syncing data from a Github organization (team scope).

    All heavy-lifting is delegated to focused helper modules; this class owns
    connector lifecycle (``init``, ``run_sync``, ``cleanup``), credential
    resolution, and the ``BaseConnector`` interface methods.
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
            GitHubTeamsApp(connector_id),
            logger, data_entities_processor, data_store_provider,
            config_service, connector_id, scope, created_by,
        )
        self.connector_name = Connectors.GITHUB_TEAMS.value
        self.connector_id = connector_id
        self.data_source: GitHubAsyncDataSource | None = None
        self.external_client: GitHubClient | None = None
        # Code-file batching only: issues and PRs take one API page as one
        # batch, so they no longer consult this. The old value of 5 was sized
        # for a per-PR fetch that no longer exists.
        self.batch_size = 100
        self.sync_filters = None
        self.indexing_filters = None

        # Runtime state set during init/sync
        self._github_login: str | None = None

        # Sync point for checkpoint management
        self.record_sync_point = SyncPoint(
            connector_id=connector_id,
            org_id=data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )

        # Helper modules — instantiated once, hold a reference back to self
        self.runtime = RuntimeHelper(self)
        self.users = UsersSync(self)
        self.projects = ProjectsSync(self)
        self.repos = ReposSync(self)
        self.issues = IssuesSync(self)
        self.pull_requests = PullRequestsSync(self)
        self.comments = CommentsHelper(self)
        self.filters = FiltersHelper(self)
        self.streaming = StreamingHelper(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> bool:
        """Initialise the GitHub client, data source, and creator identity."""
        try:
            self.external_client = await GitHubClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = GitHubAsyncDataSource(self.external_client)
            await self._resolve_creator_identity()
            self.logger.info("GitHub Teams connector initialized.")
            return True
        except Exception as e:
            self.logger.error("Failed to initialize GitHub Teams client: %s", e, exc_info=True)
            return False

    async def _resolve_creator_identity(self) -> None:
        """Cache the configuring user's GitHub login (best-effort).

        The team connector derives every permission from GitHub org membership,
        so it does not use ``creator_email`` — that is resolved here for the
        personal subclass, which routes all access through the ConnectorGroup.
        ``_github_login`` is used by the repo picker's ``user:`` search qualifier.
        """
        if self.created_by:
            try:
                creator = await self.data_entities_processor.get_user_by_user_id(self.created_by)
                if creator and getattr(creator, "email", None):
                    self.creator_email = creator.email
            except Exception as e:
                self.logger.warning("Could not resolve creator email for %s: %s", self.created_by, e)

        if self.data_source is None:
            return
        try:
            me_res = await self.runtime.ds_call(self.data_source.get_authenticated)
            if me_res.success and me_res.data is not None:
                login = getattr(me_res.data, "login", None)
                if isinstance(login, str) and login:
                    self._github_login = login
                    self.logger.info(
                        "GitHub creator resolved: creator_email_resolved=%s",
                        bool(self.creator_email),
                    )
                    return
            self.logger.warning(
                "Could not resolve configuring user's GitHub login; the repo picker's "
                "user-scope qualifier will be omitted."
            )
        except Exception as e:
            self.logger.warning("Exception resolving configuring user's GitHub login: %s", e, exc_info=True)

    async def test_connection_and_access(self) -> bool:
        """Test the connection and access to the GitHub data source."""
        if not self.data_source:
            return False
        try:
            await self.runtime.refresh_token_if_needed()
            response = await self.runtime.ds_call(self.data_source.get_authenticated)
            if response.success and response.data:
                self.logger.info("GitHub Teams connection test successful.")
                return True
            self.logger.error("GitHub Teams connection test failed: %s", response.error)
            return False
        except Exception as e:
            self.logger.error("GitHub Teams connection test failed: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    async def run_sync(self) -> None:
        """Run a full GitHub sync (users -> repos -> issues/PRs/code)."""
        try:
            self.record_sync_point.org_id = self.data_entities_processor.org_id
            await self.repos.timestamps.cancel()
            await self.runtime.refresh_token_if_needed()
            self.logger.info("Starting GitHub Teams sync")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "githubteams", self.connector_id, self.logger
            )
            # PipesHub users reach this connector through the org's "All" team,
            # not a per-user edge. The record-access query pre-filters on
            # `connectorId IN user_apps_ids`, which is satisfied via
            # (User)-[:PERMISSION]->(Teams)-[:USER_APP_RELATION]->(App) — so
            # without this edge a public repo's ORG grant is unreachable for
            # anyone whose GitHub account never resolved to an AppUser. The edge
            # grants nothing by itself; every access path still requires a real
            # PERMISSION edge.
            async with self.data_store_provider.transaction() as tx_store:
                await tx_store.ensure_team_app_edge(
                    self.connector_id, self.data_entities_processor.org_id,
                )

            self.logger.info("Starting sync of GitHub org members")
            await self.users.sync_users()
            self.logger.info("Starting sync of GitHub repositories")
            await self.projects.sync_all_repos()
            self.repos.timestamps.schedule()
        except Exception as e:
            self.logger.error("Error in GitHub Teams sync: %s", e, exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """Incremental sync delegates to the same full sync (deltas are handled via checkpoints)."""
        await self.run_sync()

    # ------------------------------------------------------------------
    # Content streaming
    # ------------------------------------------------------------------

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Delegate content streaming to ``StreamingHelper.stream_record``."""
        return await self.streaming.stream_record(record)

    # ------------------------------------------------------------------
    # Filter options
    # ------------------------------------------------------------------

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        """Return dynamic picker options for the ORG_IDS and REPO_IDS filters."""
        return await self.filters.get_filter_options(filter_key, page, limit, search, cursor)

    # ------------------------------------------------------------------
    # Reindex
    # ------------------------------------------------------------------

    async def reindex_records(self, records: list[Record]) -> None:
        """Reindex a list of records: refresh changed work items, re-queue others."""
        await self.streaming.reindex_records(records)

    # ------------------------------------------------------------------
    # Signed URL / webhooks (not implemented)
    # ------------------------------------------------------------------

    async def get_signed_url(self, record: Record) -> str | None:
        return None

    async def handle_webhook_notification(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self) -> None:
        """Release connector resources (background tasks, data source)."""
        self.logger.info("Cleaning up GitHub Teams connector resources.")
        await self.repos.timestamps.cancel()
        if self.data_source is not None:
            try:
                await self.data_source.aclose()
            except Exception as e:
                self.logger.warning("Failed to close GitHub HTTP client: %s", e)
        self.data_source = None

    # ------------------------------------------------------------------
    # Factory method
    # ------------------------------------------------------------------

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
        """Factory method to create and return an initialized GitHubTeamsConnector."""
        return GitHubTeamsConnector(
            logger, data_entities_processor, data_store_provider,
            config_service, connector_id, scope, created_by,
        )
