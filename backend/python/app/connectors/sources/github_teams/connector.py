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
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorInitError,
)
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
from app.connectors.core.registry.code_indexing_flags import (
    branch_filter_field,
    exclude_paths_filter_field,
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
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.github_teams.common.apps import GitHubTeamsApp
from app.connectors.sources.github_teams.common.utils import (
    sync_window_start,
    updated_since_filter_field,
)
from app.models.entities import Record
from app.sources.client.github.github import GitHubClient
from app.sources.external.github.github_async import GitHubAsyncDataSource

from .comments import CommentsHelper
from .constants import HTTP_TOO_MANY_REQUESTS, HTTP_UNAUTHORIZED
from .filters import FiltersHelper
from .issue_reconciler import IssueReconciler
from .issues import IssuesSync
from .projects import ProjectsSync
from .pull_requests import PullRequestsSync
from .record_removal import RecordRemover
from .repo_scope import RepoScopeReconciler
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
            ),
            # A GitHub personal access token as an alternative to OAuth. The field
            # is named "token" because GitHubClient.build_from_services reads the
            # API_TOKEN value from auth.token.
            AuthBuilder.type(AuthType.API_TOKEN).fields(
                [
                    CommonFields.api_token(
                        token_name="Personal Access Token",
                        placeholder="Enter a GitHub personal access token",
                        field_name="token",
                    ),
                ]
            ),
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
            display_name="Repository",
            description="Select the repository to sync.",
            filter_type=FilterType.SELECT, category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC,
            required=True,
        ))
        .add_filter_field(branch_filter_field())
        .add_filter_field(updated_since_filter_field())
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
        .add_filter_field(exclude_paths_filter_field())
        .add_filter_field(FilterField(
            name=IndexingFilterKey.CODE_FILES.value,
            display_name="Index Code Files",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.TEST_FILES.value,
            display_name="Index Test Files",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=False,
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

        # Removal of records that left GitHub or the sync's scope
        self.record_remover = RecordRemover(data_entities_processor, connector_id, logger)
        self.repo_scope = RepoScopeReconciler(
            data_entities_processor, self.record_remover, self.record_sync_point, connector_id, logger,
        )

        # Helper modules — instantiated once, hold a reference back to self
        self.runtime = RuntimeHelper(self)
        self.issue_reconciler = IssueReconciler(
            self.runtime, lambda: self.data_source, data_entities_processor,
            self.record_remover, connector_id, logger,
        )
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
            if response.status_code == HTTP_UNAUTHORIZED:
                # Say why, rather than the generic "verify your credentials".
                raise ConnectorInitError(
                    f"GitHub rejected the credentials ({response.api_message or 'Bad credentials'}). "
                    "Reconnect this connector with a valid token."
                )
            if response.status_code == HTTP_TOO_MANY_REQUESTS:
                # The token is fine: GitHub's hourly limit is spent, or PipesHub is holding back
                # the reserve it leaves for the account's other tools. "Verify your credentials"
                # sent admins to rotate a working token.
                wait = response.retry_after_seconds
                when = f"in about {max(1, round(wait / 60))} min" if wait else "after GitHub's hourly reset"
                raise ConnectorInitError(
                    "GitHub's API rate limit for this token is used up for now "
                    f"({response.api_message or response.error}). The credentials are fine; "
                    f"try again {when}."
                )
            return False
        except ConnectorInitError:
            raise
        except Exception as e:
            self.logger.error("GitHub Teams connection test failed: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    async def _register_authenticated_identity(self) -> None:
        """Record which source account this connector is authenticated as, so a creator whose
        PipesHub email differs still resolves that account's permissions for this connector.

        Runs after user sync: the email that sync bound the account to is the one its grants
        carry. ``GET /user`` gives only the public profile email, which is empty for most
        accounts and may be a personal address that holds no grant."""
        if not self.data_source:
            return
        email, source_user_id = None, None
        try:
            me_res = await self.runtime.ds_call(self.data_source.get_authenticated)
            if me_res.success and me_res.data is not None:
                email = getattr(me_res.data, "email", None)
                source_user_id = getattr(me_res.data, "id", None)
        except Exception as e:
            self.logger.debug("Could not read the authenticated GitHub account: %s", e)
            return
        if isinstance(source_user_id, int):
            email = self.users.resolved_email(source_user_id) or email
        await self.register_authenticated_source_user(
            email.strip() if isinstance(email, str) else None,
            str(source_user_id) if source_user_id is not None else None,
        )

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
            self._require_scope_filter()
            self.logger.info("Starting sync of GitHub org members")
            await self.users.sync_users()
            await self._register_authenticated_identity()
            self.logger.info("Starting sync of GitHub repositories")
            await self.projects.sync_all_repos()
            self.repos.timestamps.schedule()
            if self.projects.failures:
                raise RuntimeError(
                    "GitHub Teams sync did not complete: " + "; ".join(self.projects.failures[:10])
                )
        except Exception as e:
            self.logger.error("Error in GitHub Teams sync: %s", e, exc_info=True)
            raise

    def full_sync_coverage(self) -> FullSyncCoverage:
        """An issue or pull request last updated before the "updated since" window is not
        listed, not gone: one created before the window keeps its links after a full
        sync. A sync that skipped part of GitHub raises (``run_sync``), so a full sync
        that returns read everything it was asked to."""
        start = sync_window_start(self.sync_filters)
        return FullSyncCoverage(window_start_ms=int(start.timestamp() * 1000) if start else None)

    def _require_scope_filter(self) -> None:
        """Refuse to sync without a repository or organization filter.

        The repository field is required, so a sync without one means the filters
        could not be read (``load_connector_filters`` answers a config-store failure
        with empty filters). Falling back to every repository the token can see would
        index, and grant access to, repositories the admin never selected.
        """
        sf = self.sync_filters
        for key in (SyncFilterKey.REPO_IDS, SyncFilterKey.ORG_IDS):
            f = sf.get(key) if sf else None
            if f is not None and not f.is_empty():
                return
        raise RuntimeError(
            "GitHub sync filters name no repository (or could not be read); "
            "not syncing every repository the token can see."
        )

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
