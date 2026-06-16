"""
GitLab connector — orchestration shell.

This module is intentionally thin: it wires together the focused helper
modules and exposes the ``BaseConnector`` interface. Business logic lives in:

- ``runtime.py``     — API calls, auth-retry, paged list
- ``scope.py``       — group/project scope selection for admin/auditor roles
- ``users.py``       — AppUser + pseudo-group sync
- ``projects.py``    — project / RecordGroup sync
- ``repos.py``       — code repository (blob/tree) sync
- ``issues.py``      — issue (TICKET) sync + content streaming
- ``merge_requests.py`` — MR (PULL_REQUEST) sync + content streaming
- ``comments.py``    — comment block building
- ``attachments.py`` — attachment parsing and file-record building
- ``filters.py``     — dynamic filter-option pickers
- ``streaming.py``   — stream_record dispatch and reindex
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from logging import Logger
from typing import Any

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
from app.connectors.core.constants import IconPaths
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
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.sources.gitlab1.common.apps import GitLabApp
from app.models.entities import Record
from app.sources.client.gitlab.gitlab import GitLabClient
from app.sources.external.gitlab.gitlab_data_source import GitLabDataSource
from app.utils.oauth_config import resolve_instance_url

from .attachments import AttachmentsHelper
from .comments import CommentsHelper
from .constants import GITLAB_CLOUD_URL
from .filters import FiltersHelper
from .issues import IssuesSync
from .merge_requests import MergeRequestsSync
from .models import GitlabLiterals
from .projects import ProjectsSync
from .repos import ReposSync
from .runtime import RuntimeHelper
from .scope import ScopeHelper
from .streaming import StreamingHelper
from .users import UsersSync


_GITLAB_EXECUTOR_MAX_WORKERS = 8


@(
    ConnectorBuilder("GitLab")
    .in_group("GitLab")
    .with_description("Sync content from your GitLab instance")
    .with_categories(["Knowledge Management"])
    .with_scopes([ConnectorScope.TEAM.value])
    .with_auth(
        [
            AuthBuilder.type(AuthType.OAUTH).oauth(
                connector_name="GitLab",
                authorize_url=f"{GITLAB_CLOUD_URL}/oauth/authorize",
                token_url=f"{GITLAB_CLOUD_URL}/oauth/token",
                redirect_uri="connectors/oauth/callback/Gitlab",
                scopes=OAuthScopeConfig(
                    team_sync=["read_user", "read_api", "read_repository"],
                    personal_sync=[],
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
        .add_documentation_link(DocumentationLink("Gitlab API Docs", "https://docs.gitlab.com/api/rest/", "docs"))
        .add_documentation_link(DocumentationLink("Pipeshub Documentation", "https://docs.pipeshub.com/connectors/gitlab/gitlab", "pipeshub"))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_sync_support(True)
        .add_filter_field(FilterField(
            name=SyncFilterKey.GROUP_IDS.value,
            display_name="GitLab Groups",
            description="Limit sync to projects in these GitLab groups or subgroups (uses namespace path, e.g. my-org/engineering)",
            filter_type=FilterType.MULTISELECT, category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC,
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.PROJECT_IDS.value,
            display_name="Repositories",
            description="Limit sync to specific repositories (path_with_namespace, e.g. my-org/my-repo)",
            filter_type=FilterType.MULTISELECT, category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC,
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.MODIFIED.value,
            display_name="Modified Date",
            filter_type=FilterType.DATETIME, category=FilterCategory.SYNC,
            description="Filter issues and merge requests by last modification time",
            no_implicit_operator_default=True,
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.CREATED.value,
            display_name="Created Date",
            filter_type=FilterType.DATETIME, category=FilterCategory.SYNC,
            description="Filter issues and merge requests by creation time",
            no_implicit_operator_default=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.ISSUES.value,
            display_name="Index Issues",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.MERGE_REQUESTS.value,
            display_name="Index Merge Requests",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.CODE_FILES.value,
            display_name="Index Code Files",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.COMMENTS.value,
            display_name="Index Comments",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING, default_value=True,
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_admin_access_required(True, personal_connector_type="GitLab Personal")
        .with_agent_support(False)
    )
    .build_decorator()
)
class GitLabConnector(BaseConnector):
    """Connector for syncing data from a GitLab instance.

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
            GitLabApp(connector_id),
            logger, data_entities_processor, data_store_provider,
            config_service, connector_id, scope, created_by,
        )
        self.connector_name = Connectors.GITLAB.value
        self.connector_id = connector_id
        self.data_source: GitLabDataSource | None = None
        self.external_client: GitLabClient | None = None
        self.batch_size = 5
        self.max_concurrent_batches = 5
        self._gitlab_base_url: str = GITLAB_CLOUD_URL
        self.sync_filters = None
        self.indexing_filters = None

        # Runtime state set during init/sync
        self._gitlab_included_group_paths: list[str] | None = None
        self._gitlab_user_id: int | None = None
        self._is_admin: bool = False
        self._is_auditor: bool = False
        self._auditor_fallback_warned: bool = False
        self._code_file_timestamp_backfill_task = None

        # Dedicated executor: isolates blocking python-gitlab threads from the
        # shared loop executor so a stuck EE instance cannot freeze the service.
        self._gitlab_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=_GITLAB_EXECUTOR_MAX_WORKERS,
            thread_name_prefix=f"gitlab-{connector_id[:8]}",
        )

        # Sync point for checkpoint management
        self.record_sync_point = SyncPoint(
            connector_id=connector_id,
            org_id=data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )

        # Helper modules — instantiated once, hold a reference back to self
        self.runtime = RuntimeHelper(self)
        self.scope = ScopeHelper(self)
        self.users = UsersSync(self)
        self.projects = ProjectsSync(self)
        self.repos = ReposSync(self)
        self.issues = IssuesSync(self)
        self.merge_requests = MergeRequestsSync(self)
        self.comments = CommentsHelper(self)
        self.attachments = AttachmentsHelper(self)
        self.filters = FiltersHelper(self)
        self.streaming = StreamingHelper(self)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> bool:
        """Initialise the GitLab client, data source, and creator identity."""
        try:
            config_path = f"/services/connectors/{self.connector_id}/config"
            raw_config = await self.config_service.get_config(config_path) or {}
            auth_cfg = raw_config.get("auth", {})
            instance_url = await resolve_instance_url(
                auth_cfg, self.config_service, default=GITLAB_CLOUD_URL, logger=self.logger,
            )
            self._gitlab_base_url = instance_url or GITLAB_CLOUD_URL

            self.external_client = await GitLabClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = GitLabDataSource(self.external_client, base_url=self._gitlab_base_url)
            await self._resolve_creator_identity()
            self.logger.info("GitLab connector initialized (instance: %s).", self._gitlab_base_url)
            return True
        except Exception as e:
            self.logger.error("Failed to initialize GitLab client: %s", e, exc_info=True)
            return False

    async def _resolve_creator_identity(self) -> None:
        """Cache the connector creator's email, GitLab id, and role flags (best-effort)."""
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
            me_res = await self.runtime.ds_call(self.data_source.get_user)
            if me_res.success and me_res.data is not None:
                self._is_admin = bool(getattr(me_res.data, "is_admin", False))
                self._is_auditor = bool(getattr(me_res.data, "is_auditor", False))
                uid = getattr(me_res.data, "id", None)
                if isinstance(uid, int):
                    self._gitlab_user_id = uid
                    self.logger.info(
                        "GitLab creator resolved: pipeshub_email=%r, gitlab_user_id=%s, is_admin=%s, is_auditor=%s",
                        self.creator_email, self._gitlab_user_id, self._is_admin, self._is_auditor,
                    )
                    return
            self.logger.warning(
                "Could not resolve configuring user's GitLab id; creator-permission fallback will use public_email-only path."
            )
        except Exception as e:
            self.logger.warning("Exception resolving configuring user's GitLab id: %s", e, exc_info=True)

    async def test_connection_and_access(self) -> bool:
        """Test the connection and access to the GitLab data source."""
        if not self.data_source:
            return False
        try:
            await self.runtime.refresh_token_if_needed()
            response = await self.runtime.call_with_auth_retry(lambda: self.data_source.get_user())
            if response.success and response.data:
                self.logger.info("GitLab connection test successful.")
                return True
            self.logger.error("GitLab connection test failed: %s", response.error)
            return False
        except Exception as e:
            self.logger.error("GitLab connection test failed: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    async def run_sync(self) -> None:
        """Run a full GitLab sync (users → projects → issues/MRs/repos)."""
        try:
            await self.repos.cancel_timestamp_backfill()
            await self.runtime.refresh_token_if_needed()
            self.logger.info("Starting GitLab sync")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "gitlab", self.connector_id, self.logger
            )
            self._gitlab_included_group_paths = None
            self.logger.info("Starting sync of GitLab users")
            await self.users.sync_users()
            self.logger.info("Starting sync of GitLab projects")
            await self.projects.sync_all_projects()
            self.repos.schedule_timestamp_backfill()
        except Exception as e:
            self.logger.error("Error in GitLab sync: %s", e, exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """Incremental sync delegates to the same full sync (GitLab handles deltas via checkpoints)."""
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
        """Return dynamic picker options for the GROUP_IDS and PROJECT_IDS filters."""
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
        """Release connector resources (background tasks, HTTP client, thread pool)."""
        self.logger.info("Cleaning up GitLab connector resources.")
        await self.repos.cancel_timestamp_backfill()
        if self.data_source is not None:
            try:
                await self.data_source.aclose()
            except Exception as e:
                self.logger.warning("Failed to close GitLab HTTP client: %s", e)
        self.data_source = None
        try:
            self._gitlab_executor.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            self.logger.warning("GitLab executor shutdown raised; ignoring: %s", e)

    # ------------------------------------------------------------------
    # Datetime filter helper (used by issues.py and merge_requests.py)
    # ------------------------------------------------------------------

    def datetime_range_from_sync_filter(
        self, key: str
    ) -> tuple[datetime | None, datetime | None]:
        """Return UTC (after, before) bounds from a sync filter for the given key."""
        if not self.sync_filters:
            return (None, None)
        from app.connectors.core.registry.filters import SyncFilterKey
        _key_map = {
            "modified": SyncFilterKey.MODIFIED,
            "created": SyncFilterKey.CREATED,
        }
        sf_key = _key_map.get(key)
        if sf_key is None:
            return (None, None)
        f = self.sync_filters.get(sf_key)
        if not f:
            return (None, None)
        start_ms = f.get_datetime_start()
        end_ms = f.get_datetime_end()
        after = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc) if start_ms is not None else None
        before = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) if end_ms is not None else None
        return (after, before)

    # ------------------------------------------------------------------
    # Creator permission helper (used by projects.py)
    # ------------------------------------------------------------------

    def creator_user_permission(self) -> Any | None:
        """Return an OWNER Permission for the connector creator, or None if unavailable."""
        from app.models.permission import EntityType, Permission, PermissionType
        if not self.creator_email:
            return None
        return Permission(
            entity_type=EntityType.USER,
            email=self.creator_email,
            type=PermissionType.OWNER,
        )

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
    ) -> "BaseConnector":
        """Factory method to create and return an initialized GitLabConnector."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return GitLabConnector(
            logger, data_entities_processor, data_store_provider,
            config_service, connector_id, scope, created_by,
        )
