import asyncio
import base64
import inspect
import json
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from enum import Enum
from logging import Logger
from typing import Any
from urllib.parse import unquote, urlparse
from app.connectors.core.constants import (
    IconPaths,
)
from fastapi.responses import StreamingResponse
from gitlab.v4.objects import (
    GroupMember,
    Project,
    ProjectCommit,
    ProjectIssue,
    ProjectIssueNote,
    ProjectMergeRequest,
    ProjectMergeRequestNote,
)
from pydantic import BaseModel, Field

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    ExtensionTypes,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
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
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.sources.gitlab.common.apps import GitLabApp
from app.models.blocks import (
    Block,
    BlockComment,
    BlockGroup,
    BlocksContainer,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    CommentAttachment,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CodeFileRecord,
    FileRecord,
    ItemType,
    PullRequestRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.gitlab.gitlab import (
    GitLabClient,
    GitLabResponse,
)
from app.sources.external.gitlab.gitlab_ import GitLabDataSource
from app.utils.oauth_config import resolve_instance_url
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import (
    get_epoch_timestamp_in_ms,
    parse_timestamp,
    string_to_datetime,
)

GITLAB_CLOUD_URL = "https://gitlab.com"
GITLAB_SEARCH_MIN_PARTIAL_CHARS = 3
GITLAB_COMPARE_DIFF_LIMIT = 1000

# Per-logical-call wall-clock budget. ``GitLabClientViaToken`` already passes
# a per-HTTP-request ``timeout=30`` to python-gitlab, but a single
# ``get_all=True`` materialization fans out into many sequential HTTP calls;
# the per-request timeout does nothing to bound the *total* call duration.
# Without this ceiling, a misbehaving EE instance can keep the event loop
# blocked inside ``asyncio.to_thread`` for hours — that is the "stuck on
# fetching users/groups" symptom. When the budget fires we return
# ``success=False`` and the caller's fallback (creator-only permissions,
# next-page retry, etc.) takes over. The orphaned worker thread is left to
# unwind via python-gitlab's own per-request timeout and is not joined.
_GITLAB_OP_DEFAULT_TIMEOUT_SECONDS = 300.0

# Per-batch wall-clock budget for ``_paged_list``. The
# ``_GITLAB_OP_DEFAULT_TIMEOUT_SECONDS`` budget at ``_execute_gitlab_op``
# only wraps the *first* call that constructs the ``GitlabList``
# iterator; every subsequent ``next()`` inside the drain loop fans out
# into a fresh HTTP request (one per page boundary). Without a budget
# around each drain batch a single stuck page fetch — slow EE proxy,
# half-open TCP, network blip during keepalive — leaves the worker
# thread blocked on ``recv()`` and the asyncio task waiting on it
# indefinitely. The blocked thread also holds a slot in the loop's
# default ``ThreadPoolExecutor`` (~8 slots on a 4-core pod); enough
# stuck slots and the connector service itself stops accepting work
# because every HTTP handler that touches a sync API has to queue
# behind them. That is the "connector service frozen" symptom on
# RingCentral-sized tenants.
#
# Sized for the default ``progress_every=500`` against ``per_page=100``
# callers (5 page requests per batch) including python-gitlab's
# ``retry_transient_errors`` exponential backoff: ~30s per request
# best case, more under retry. 300s matches the precedent set by
# ``_GITLAB_OP_DEFAULT_TIMEOUT_SECONDS`` and leaves headroom for slow-
# but-functional EE deployments without producing false-positive
# timeouts on large groups.
#
# CAVEAT: ``asyncio.wait_for`` cancels the awaiting coroutine but
# CANNOT interrupt the worker thread. The orphaned thread is left to
# unwind via python-gitlab's per-request ``timeout=30`` and is not
# joined. The dedicated ``_gitlab_executor`` (see
# ``GitLabConnector.__init__``) keeps these orphaned threads off the
# loop's default executor so they cannot starve unrelated service
# work; this constant is what bounds how long any individual
# coroutine waits for them.
_GITLAB_PAGE_BATCH_TIMEOUT_SECONDS = 300.0

# Per-connector dedicated executor capacity. The connector service
# previously routed every blocking python-gitlab call through the
# loop's default ``ThreadPoolExecutor`` (~8 slots on a 4-core pod),
# which is shared with sync DB drivers, file I/O, OAuth callbacks,
# and FastAPI's request body parsing. A handful of stuck GitLab
# threads — common during EE-instance instability — would saturate
# that shared pool and the connector service would stop accepting
# any work, not just GitLab work. Isolating GitLab on its own pool
# means a misbehaving instance can only stall its own connector,
# never the rest of the service.
#
# 8 workers keeps cross-connector concurrency bounded (each connector
# instance owns its pool) while leaving headroom for the
# enrichment fan-out (see ``_enrich_members_with_full_user``) to
# pipeline a few in-flight ``GET /users/:id`` calls. Anything
# higher reintroduces the starvation risk; anything lower hurts
# enrichment throughput on tenants with thousands of members.
_GITLAB_EXECUTOR_MAX_WORKERS = 8

# Concurrency cap for the per-user ``GET /users/:id`` enrichment
# fan-out. Half of ``_GITLAB_EXECUTOR_MAX_WORKERS`` so picker
# requests, paged sweeps, and other GitLab calls from the same
# connector still get half the pool while enrichment runs. The
# original code fanned out 20 concurrent calls via
# ``asyncio.gather(batch_size=20)``; on the loop's default executor
# that single line accounted for most of the thread-pool
# starvation we saw in the freeze symptom.
_GITLAB_USER_ENRICHMENT_CONCURRENCY = 4


async def _stream_with_eager_first_chunk(
    source: AsyncGenerator[bytes, None],
) -> AsyncGenerator[bytes, None]:
    """Return a streaming generator after eagerly pulling its first chunk.

    Reading the first chunk *before* returning lets upstream auth / 404 /
    network errors surface here, where they can still be converted to a
    clean HTTP 5xx. Without this, an error raised on the first network
    read fires after ``StreamingResponse`` has already committed the
    status line, which produces a truncated chunked body and a client-side
    ``TransferEncodingError`` / "Not enough data to satisfy transfer length
    header".

    Only the first chunk is buffered — subsequent chunks stream lazily, so
    multi-GB attachments do not sit in connector RAM.
    """
    aiter = source.__aiter__()
    try:
        first = await aiter.__anext__()
    except StopAsyncIteration:
        # Empty source: return an immediately-exhausted async generator.
        async def _empty() -> AsyncGenerator[bytes, None]:
            return
            yield b""  # noqa: unreachable, marks this as an async generator
        return _empty()

    async def _gen() -> AsyncGenerator[bytes, None]:
        yield first
        async for chunk in aiter:
            yield chunk

    return _gen()

PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"}
# Extensions for documents/media that the UI can render as a preview (i.e. not
# raw source code). Anything outside this set is treated as a code file.
PREVIEW_RENDERABLE_EXTENSIONS = {ext.value for ext in ExtensionTypes}
UPLOAD_PATTERN = re.compile(
    r"""
    (?P<full>
                    (?:!\[.*?\]|\[.*?\])      # Image or link markdown
                    \(
                    (?P<href>
                        /uploads/
                        [a-f0-9]{32}/         # 32-char GitLab hash
                        (?P<filename>[^)\s]+) # filename
                    )
                    \)
                )
                """,
    re.VERBOSE | re.IGNORECASE,
)


class FileAttachment(BaseModel):
    """File attachment model"""

    href: str = Field(description="URL of the attachment", min_length=1)
    filename: str = Field(description="Name of the attachment", min_length=1)
    filetype: str = Field(description="Type of the attachment")
    category: str = Field(description="Category of the attachment image or file")


class RecordUpdate(BaseModel):
    """Tracks updates to a Record"""

    record: Record = Field(description="The record that was updated")
    is_new: bool = Field(description="Whether the record is new")
    is_updated: bool = Field(description="Whether the record is updated")
    is_deleted: bool = Field(description="Whether the record is deleted")
    metadata_changed: bool = Field(
        description="Whether the record's metadata has changed"
    )
    content_changed: bool = Field(
        description="Whether the record's content has changed"
    )
    permissions_changed: bool = Field(
        description="Whether the record's permissions have changed"
    )
    old_permissions: list[Permission] | None = Field(
        description="The old permissions of the record"
    )
    new_permissions: list[Permission] | None = Field(
        description="The new permissions of the record"
    )
    external_record_id: str | None = Field(
        description="The external record ID of the record"
    )


class GitlabLiterals(str, Enum):
    LAST_SYNC_TIME = "last_sync_time"
    LAST_COMMIT_SHA = "last_commit_sha"
    RECORD_GROUP = "record_group"
    GLOBAL = "global"
    UPDATED_AT = "updated_at"
    UTF_8 = "utf-8"
    IMAGE = "image"
    ATTACHMENT = "attachment"


def _filter_op_val(f: Any) -> str:
    """Lower-cased string value of a ``Filter``'s operator.

    Operators are exposed as enums in code but stored as strings on the
    persisted payload, so callers compare them against the lowercase
    ``FilterOperator.*`` string constants. Centralising the conversion
    keeps the multiple resolver helpers (``_resolve_projects_with_filters``,
    ``_resolve_user_sync_scope``) consistent.
    """
    op = f.operator
    return (op.value if hasattr(op, "value") else str(op)).lower()


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
                    team_sync=[
                        "read_user",
                        "read_api",
                        "read_repository",
                    ],
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
                name=SyncFilterKey.MODIFIED.value,
                display_name="Modified Date",
                filter_type=FilterType.DATETIME,
                category=FilterCategory.SYNC,
                description=(
                    "Filter issues and merge requests by last modification time"
                ),
                no_implicit_operator_default=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=SyncFilterKey.CREATED.value,
                display_name="Created Date",
                filter_type=FilterType.DATETIME,
                category=FilterCategory.SYNC,
                description="Filter issues and merge requests by creation time",
                no_implicit_operator_default=True,
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
        .with_admin_access_required(True, personal_connector_type="GitLab Personal")
        .with_agent_support(False)
    )
    .build_decorator()
)
class GitLabConnector(BaseConnector):
    """
    Connector for syncing data from Gitlab instance.
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
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_name = Connectors.GITLAB.value
        self.connector_id = connector_id
        self.data_source: GitLabDataSource | None = None
        self.external_client: GitLabClient | None = None
        self.batch_size = 5
        self.max_concurrent_batches = 5
        self._gitlab_base_url: str = GITLAB_CLOUD_URL
        self.sync_filters: FilterCollection | None = None
        self.indexing_filters: FilterCollection | None = None
        # Set during sync when group_ids IN filter is active (namespace paths)
        self._gitlab_included_group_paths: list[str] | None = None
        # Configuring user's GitLab numeric id, captured during ``init()`` via
        # ``_resolve_creator_identity`` from ``GET /user`` (authenticated user).
        # Used in ``_sync_users_from_projects_groups`` to bypass the
        # ``public_email`` gate for the connector creator so they always
        # land in PipesHub as a real ``AppUser`` (not a pseudo-group),
        # which is the only way they can search records they have access
        # to on GitLab. See _resolve_creator_identity / Patch #4.
        self._gitlab_user_id: int | None = None
        self._code_file_timestamp_backfill_task: asyncio.Task[None] | None = None
        # GitLab user-level access flags, captured from ``GET /user``.
        # ``is_admin``: all editions; ``is_auditor``: Premium/Ultimate only.
        # GitLab OMITS both attributes from the response when the caller
        # does not have them — ``getattr(..., default=False)`` is the safe
        # read. These flags pick the right ``list_groups`` scope flag:
        # regular users get ``min_access_level=10`` (membership), but
        # admins/auditors need ``all_available=True`` because their
        # cross-instance access flows from a USER-level flag, not from a
        # row in the per-group members table. See
        # ``_list_groups_scope_kwargs`` for the dispatch logic.
        self._is_admin: bool = False
        self._is_auditor: bool = False
        # Set the first time an auditor token returns 0 rows from a
        # role-scoped list endpoint, so we WARN the operator exactly
        # once per connector lifetime instead of on every page of
        # every sync. See ``_paged_list_groups_with_role_fallback`` /
        # ``_paged_list_projects_with_role_fallback``.
        self._auditor_fallback_warned: bool = False
        # Dedicated thread pool for blocking python-gitlab calls.
        # Replaces ``asyncio.to_thread`` (which routes through the
        # event loop's *default* executor that the rest of the
        # connector service shares). With a per-connector pool, a
        # GitLab instance hung on ``recv()`` can starve at most this
        # connector's GitLab work — never the service's HTTP routes,
        # other connectors, or the DB driver. See
        # ``_GITLAB_EXECUTOR_MAX_WORKERS`` for the sizing rationale
        # and ``cleanup`` for shutdown semantics.
        self._gitlab_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=_GITLAB_EXECUTOR_MAX_WORKERS,
            thread_name_prefix=f"gitlab-{connector_id[:8]}",
        )
        self._create_sync_points()

    def _create_sync_points(self) -> None:
        """Initialize sync points for different data types."""

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

    async def init(self) -> bool:
        """
        Initialize the Gitlab client and data source.
        Returns:
            bool: True if initialization is successful, False otherwise.
        """
        try:
            # Resolve the instance URL early so it's available before the client
            # is built (build_from_services also reads it, but we need it here
            # to pass to GitLabDataSource and for all URL construction later).
            # Falls back to the shared OAuth-app config when the per-instance
            # value is missing — keeps legacy GitLab EE installs working.
            config_path = f"/services/connectors/{self.connector_id}/config"
            raw_config = await self.config_service.get_config(config_path) or {}
            auth_cfg = raw_config.get("auth", {})
            instance_url = await resolve_instance_url(
                auth_cfg,
                self.config_service,
                default=GITLAB_CLOUD_URL,
                logger=self.logger,
            )
            self._gitlab_base_url = instance_url or GITLAB_CLOUD_URL

            # Build the API client (uses instanceUrl internally via build_from_services)
            self.external_client = await GitLabClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            # Pass base_url so GraphQL and direct HTTP calls target the right host
            self.data_source = GitLabDataSource(
                self.external_client, base_url=self._gitlab_base_url
            )
            # Resolve the configuring user's PipesHub email and their GitLab
            # numeric id so the sync can grant them direct access when the
            # source-of-truth member listing fails or returns nothing they're
            # in. Mirrors the inline lookup in JiraCloudConnector.init at
            # backend/python/app/connectors/sources/atlassian/jira_cloud/
            # connector.py:917-923. Both runs best-effort — a failure here
            # must never block connector init, but if it succeeds the
            # creator-permission fallback kicks in everywhere a member walk
            # could otherwise drop ACLs on the floor.
            await self._resolve_creator_identity()
            self.logger.info(
                f"Gitlab connector initialized successfully (instance: {self._gitlab_base_url})."
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Gitlab client: {e}", exc_info=True)
            return False

    async def _resolve_creator_identity(self) -> None:
        """Best-effort: cache the connector creator's PipesHub email, GitLab id, and role flags.

        The PipesHub email comes from the local user table via ``created_by``
        — that is the identity the user signs into PipesHub with and the
        one their search queries are scoped by.

        The GitLab numeric id comes from ``GET /user`` (authenticated user),
        which always returns the configuring user's own id regardless of
        their role on any group/project. We need it to recognise the
        creator's row inside a ``list_*_members_all`` response, where
        ``public_email`` is almost always empty — without the id-based
        bypass that row becomes a pseudo-group keyed by a numeric id
        the PipesHub-side identity never matches.

        ``is_admin`` (all editions) and ``is_auditor`` (Premium/Ultimate
        only) are read from the same ``GET /user`` response — GitLab
        OMITS these attributes when the caller does not have them, so
        ``getattr(..., default=False)`` is the safe access pattern.
        Both flags select the ``list_groups`` scope: admins/auditors
        require ``all_available=True`` because their cross-instance read
        access flows from a user-level flag rather than from a member
        row in the per-group members table; ``min_access_level`` filters
        out everyone whose membership row is absent, which is precisely
        the Auditor case.
        """
        if self.created_by:
            try:
                creator = await self.data_entities_processor.get_user_by_user_id(
                    self.created_by
                )
                if creator and getattr(creator, "email", None):
                    self.creator_email = creator.email
            except Exception as e:
                self.logger.warning(
                    "Could not resolve creator email for created_by %s: %s",
                    self.created_by,
                    e,
                )

        if self.data_source is None:
            return
        try:
            # ``get_user()`` (no args) authenticates the SDK and returns
            # the current user. python-gitlab caches the result on
            # ``self._sdk.user``; using it directly avoids a duplicate
            # ``GET /user`` round-trip on every sync.
            me_res = await self._ds_call(self.data_source.get_user)
            if me_res.success and me_res.data is not None:
                # ``is_admin`` / ``is_auditor`` are intentionally read
                # before the id check: a missing numeric id is rare but
                # would early-return here, and we still want the role
                # flags for the scope-picker if the rest of the response
                # came through. ``bool(getattr(..., False))`` because
                # GitLab returns these as JSON ``true``/``false`` which
                # python-gitlab surfaces as Python bools, but a defensive
                # ``bool()`` guards against an upstream SDK shape change.
                self._is_admin = bool(getattr(me_res.data, "is_admin", False))
                self._is_auditor = bool(getattr(me_res.data, "is_auditor", False))
                uid = getattr(me_res.data, "id", None)
                if isinstance(uid, int):
                    self._gitlab_user_id = uid
                    self.logger.info(
                        "GitLab connector creator resolved: pipeshub_email=%r, "
                        "gitlab_user_id=%s, is_admin=%s, is_auditor=%s",
                        self.creator_email,
                        self._gitlab_user_id,
                        self._is_admin,
                        self._is_auditor,
                    )
                    return
            self.logger.warning(
                "Could not resolve configuring user's GitLab id; "
                "creator-permission fallback will skip the id-based bypass "
                "(public_email-only path still applies). error=%s",
                getattr(me_res, "error", None),
            )
        except Exception as e:
            self.logger.warning(
                "Exception resolving configuring user's GitLab id: %s",
                e,
                exc_info=True,
            )

    def _list_projects_scope_kwargs(self) -> dict[str, object]:
        """Pick the right ``list_projects`` scope flag for the current user.

        Parallel to ``_list_groups_scope_kwargs`` but for the
        ``GET /projects`` endpoint, which has a different default-scope
        contract than ``GET /groups``:

        - ``membership=True`` filters to projects with a membership row.
          For a regular member this is exactly the right scope.
        - ``min_access_level=10`` filters to projects with a recorded
          ``access_level`` row. For a pure Auditor or Admin this returns
          ``[]`` for the same reason ``min_access_level`` does on the
          groups endpoint — their access does not flow through a
          per-resource access_level row.
        - No flag at all = "all visible projects to the authenticated
          user". For an Auditor / Admin this expands to every project
          visible via the ``is_auditor`` / ``is_admin`` user-level flag,
          which is the only path that surfaces non-member projects to
          them.

        Returning ``{}`` for admins/auditors is therefore the dual of
        returning ``{"all_available": True}`` for groups: it deliberately
        omits the scope flag so the API default ("all visible") applies.

        Note: GitLab's ``/projects`` endpoint has a long-standing quirk
        where ``membership=True`` silently omits Guest-level (access
        level 10) projects on some self-managed builds. Where that
        matters in the codebase (the picker), ``min_access_level=10``
        is used instead — see ``_gitlab_project_filter_options``. This
        helper is for the sync paths, where ``membership=True`` is the
        correct narrow default for a regular member.
        """
        if self._is_admin or self._is_auditor:
            return {}
        return {"membership": True}

    def _list_groups_scope_kwargs(self) -> dict[str, object]:
        """Pick the primary ``list_groups`` scope flag for the current user.

        GitLab has two orthogonal access dimensions:

        - Per-group membership rows (``access_level`` 10..50). This is
          what ``min_access_level`` queries.
        - User-level flags (``is_admin``, ``is_auditor``). These activate
          cross-instance read paths without populating any membership
          row, so ``min_access_level`` never sees them.

        For a regular member, ``min_access_level=10`` is correct: it
        scopes to groups they joined as Guest+ and excludes the
        internal/public groups they can technically read but did not
        join.

        For admins and Auditors (EE Premium/Ultimate), the primary scope
        is ``all_available=True``. Admin tokens reliably honour this and
        return every group on the instance.

        IMPORTANT — Auditor caveat: GitLab itself documents a known
        issue where the auditor user-level flag does NOT actually
        grant read access through most listing endpoints, even though
        ``is_auditor`` is set on the ``/user`` payload:

            "Due to a known issue, [auditor] users must have the
             Reporter, Developer, Maintainer, or Owner role to
             perform read-only tasks."
            — https://docs.gitlab.com/administration/auditor_users/

        In practice, ``list_groups(all_available=True)`` for a pure
        auditor often returns ``[]`` on affected instances. We still
        return ``all_available=True`` as the primary because (a) it
        IS the documented correct scope, (b) it works for admins, and
        (c) on un-affected EE versions it works for auditors too.
        ``_paged_list_groups_with_role_fallback`` retries with
        ``min_access_level=10`` when the primary returns empty for an
        auditor, surfacing every group where the auditor was given an
        explicit Reporter+ membership row as the documented workaround.

        Callers that need to pin this for tests can override the cached
        flags directly (``self._is_admin``, ``self._is_auditor``).
        """
        if self._is_admin or self._is_auditor:
            return {"all_available": True}
        return {"min_access_level": 10}

    def _warn_auditor_fallback_once(self, kind: str) -> None:
        """Log a single actionable WARN when the auditor primary scope yields nothing.

        ``kind`` is "groups" or "projects" so the operator can correlate
        the message with the listing that came back empty. We log once
        per connector lifetime (guarded by ``_auditor_fallback_warned``)
        because every paged sweep across every sync would otherwise spam
        the same line. The message points at the upstream GitLab known
        issue and the two operator-side workarounds (grant Reporter+ at
        the top-level group, or use an Admin token).
        """
        if self._auditor_fallback_warned:
            return
        self._auditor_fallback_warned = True
        self.logger.warning(
            "GitLab auditor token returned 0 %s with the documented "
            "auditor scope. This matches GitLab's known issue: "
            "'Due to a known issue, [auditor] users must have the "
            "Reporter, Developer, Maintainer, or Owner role to perform "
            "read-only tasks' "
            "(https://docs.gitlab.com/administration/auditor_users/). "
            "Falling back to membership-scoped listing so any %s where "
            "the auditor has an explicit Reporter+ row still sync. "
            "To sync the full instance, either grant the auditor user "
            "Reporter+ at the top-level group(s), or configure the "
            "connector with an Admin token instead.",
            kind,
            kind,
        )

    async def _paged_list_groups_with_role_fallback(
        self,
        *args: Any,
        progress_label: str,
        progress_every: int = 500,
        **kwargs: Any,
    ) -> GitLabResponse:
        """``list_groups`` with auditor-empty fallback.

        Calls ``list_groups`` with the primary role scope. If the caller
        is an auditor, the call succeeded, and the primary scope yielded
        zero rows, retries once with ``min_access_level=10`` to catch
        groups where the auditor has an explicit Reporter+ membership
        row (the GitLab-documented workaround for the auditor read-all
        known issue). See ``_list_groups_scope_kwargs`` for the
        underlying contract.

        Caller-supplied scope kwargs (``min_access_level``,
        ``all_available``, ``owned``) override the role default and
        also disable the fallback — when the caller has an opinion
        about scope, it stays in charge.
        """
        primary = self._list_groups_scope_kwargs()
        caller_overrode_scope = any(
            k in kwargs for k in ("min_access_level", "all_available", "owned")
        )
        merged: dict[str, Any] = {**primary, **kwargs}
        res = await self._paged_list(
            self.data_source.list_groups,
            *args,
            progress_label=progress_label,
            progress_every=progress_every,
            **merged,
        )
        if (
            not self._is_auditor
            or caller_overrode_scope
            or not res.success
            or res.data
        ):
            return res
        self._warn_auditor_fallback_once("groups")
        fallback = {k: v for k, v in kwargs.items() if k != "all_available"}
        fallback["min_access_level"] = 10
        fb_res = await self._paged_list(
            self.data_source.list_groups,
            *args,
            progress_label=f"{progress_label} [auditor membership fallback]",
            progress_every=progress_every,
            **fallback,
        )
        if not fb_res.success or not fb_res.data:
            return fb_res
        # ``min_access_level=10`` only returns groups with an explicit
        # member row, missing any subgroup whose Reporter+ access is
        # inherited from an ancestor. Walk descendants of each fallback
        # group to recover those — see GitLab !76556 for the historical
        # ``User#authorized_groups`` inheritance gap and
        # ``GitLabDataSource.list_descendant_groups`` for the endpoint.
        expanded = await self._expand_groups_with_descendants(
            list(fb_res.data),
            progress_label=f"{progress_label} [auditor descendants]",
            progress_every=progress_every,
        )
        return GitLabResponse(success=True, data=expanded)

    async def _expand_groups_with_descendants(
        self,
        base_groups: list[Any],
        *,
        progress_label: str,
        progress_every: int = 500,
        descendant_kwargs: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Merge each base group's descendant groups into a deduped list.

        Closes the GitLab gap where ``list_groups(min_access_level=10)``
        omits subgroups whose access is inherited from an ancestor's
        Reporter+ membership (see GitLab MR !76556 and the auditor-users
        known issue). Only invoked from auditor fallback paths; the extra
        roundtrips are bounded by the auditor's explicit-membership set,
        which is small by definition (else the primary
        ``all_available=True`` scope would not have returned zero).

        Dedupes by ``full_path`` (the canonical GitLab group identifier
        used everywhere else in this connector) and preserves the
        original order — base groups first, then descendants in the
        order returned by GitLab. Callers apply their own search/match
        filter to the merged result; the helper does not filter so an
        ancestor that fails the local match still anchors a descendant
        walk that might surface a matching subgroup.
        """
        if not base_groups:
            return base_groups

        def _key(g: Any) -> str | None:
            fp = getattr(g, "full_path", None)
            return str(fp) if fp else None

        seen: set[str] = set()
        out: list[Any] = []
        # Snapshot ``(parent_path, parent_id)`` for every base group up
        # front so newly-appended descendants are not re-walked: GitLab
        # returns the full subtree from any ancestor, and re-walking a
        # child would issue a redundant roundtrip and risk infinite
        # growth on a malformed response.
        parents: list[tuple[str, Any]] = []
        for g in base_groups:
            key = _key(g)
            if key is None or key in seen:
                continue
            seen.add(key)
            out.append(g)
            parents.append((key, getattr(g, "id", None) or key))
        kw = dict(descendant_kwargs or {})
        kw.setdefault("min_access_level", 10)
        for parent_key, parent_id in parents:
            res = await self._paged_list(
                self.data_source.list_descendant_groups,
                parent_id,
                progress_label=f"{progress_label} (parent={parent_key})",
                progress_every=progress_every,
                **kw,
            )
            if not res.success:
                self.logger.warning(
                    "%s: descendant walk failed for parent=%s: %s",
                    progress_label,
                    parent_key,
                    res.error,
                )
                continue
            for d in res.data or []:
                key = _key(d)
                if key is None or key in seen:
                    continue
                seen.add(key)
                out.append(d)
        return out

    @staticmethod
    def _picker_kwargs_for_auditor_groups_fallback(
        list_kwargs: dict[str, object],
    ) -> dict[str, object]:
        """Swap ``all_available`` for ``min_access_level=10`` in picker kwargs.

        Used by the group picker to retry once when the auditor primary
        scope returns 0 rows. See ``_list_groups_scope_kwargs`` for the
        underlying GitLab known issue.
        """
        out = {k: v for k, v in list_kwargs.items() if k != "all_available"}
        out["min_access_level"] = 10
        return out

    @staticmethod
    def _picker_kwargs_for_auditor_projects_fallback(
        list_kwargs: dict[str, object],
    ) -> dict[str, object]:
        """Swap auditor primary (no scope) for ``min_access_level=10`` in picker kwargs.

        ``min_access_level=10`` rather than ``membership=True`` because
        the picker explicitly avoids ``membership`` to dodge the GitLab
        Guest-omission quirk (see ``_gitlab_project_filter_options``).
        """
        out = dict(list_kwargs)
        out["min_access_level"] = 10
        return out

    async def _paged_list_projects_with_role_fallback(
        self,
        *args: Any,
        progress_label: str,
        progress_every: int = 500,
        **kwargs: Any,
    ) -> GitLabResponse:
        """``list_projects`` with auditor-empty fallback.

        Same contract as ``_paged_list_groups_with_role_fallback`` but
        for the projects endpoint. Primary auditor scope is no flag
        (rely on the documented auditor read-all). Fallback is
        ``membership=True`` to catch projects where the auditor has an
        explicit member row. Caller-supplied ``membership`` /
        ``min_access_level`` / ``owned`` disables the fallback.
        """
        primary = self._list_projects_scope_kwargs()
        caller_overrode_scope = any(
            k in kwargs for k in ("membership", "min_access_level", "owned")
        )
        merged: dict[str, Any] = {**primary, **kwargs}
        res = await self._paged_list(
            self.data_source.list_projects,
            *args,
            progress_label=progress_label,
            progress_every=progress_every,
            **merged,
        )
        if (
            not self._is_auditor
            or caller_overrode_scope
            or not res.success
            or res.data
        ):
            return res
        self._warn_auditor_fallback_once("projects")
        fallback = dict(kwargs)
        fallback["membership"] = True
        return await self._paged_list(
            self.data_source.list_projects,
            *args,
            progress_label=f"{progress_label} [auditor membership fallback]",
            progress_every=progress_every,
            **fallback,
        )

    def _creator_user_permission(self) -> Permission | None:
        """Build a single-USER permission for the configuring user, or ``None``.

        Mirrors the shape of ``_fallback_permissions_for_forbidden_scheme``
        in JiraCloudConnector. The permission grants the connector creator
        direct access to a record-group / record when the source listing
        we would normally derive ACLs from is unavailable (HTTP 403,
        timeout, empty result that we cannot distinguish from "members
        hidden"). Without this, the record-group is created with an
        empty principals set and is silently invisible to *every* user
        — including the one who just configured the sync.
        """
        if not self.creator_email:
            return None
        return Permission(
            entity_type=EntityType.USER,
            email=self.creator_email,
            type=PermissionType.OWNER,
        )

    async def test_connection_and_access(self) -> bool:
        """Test the connection and access to the Gitlab data source.
        Returns:
            bool: True if connection and access is successful, False otherwise.
        """
        if not self.data_source:
            return False
        try:
            await self._refresh_token_if_needed()
            response: GitLabResponse = await self._call_with_auth_retry(
                lambda: self.data_source.get_user()
            )
            if response.success and response.data:
                self.logger.info("GitLab connection test successful.")
                return True
            else:
                self.logger.error(f"GitLab connection test failed: {response.error}")
                return False
        except Exception as e:
            self.logger.error(f"GitLab connection test failed: {e}", exc_info=True)
            return False

    # python-gitlab serializes GitlabAuthenticationError as "401: <message>" when
    # caught by GitLabDataSource. Match defensively against common token-related
    # error tokens so we don't miss revoked / invalid_token variants.
    _AUTH_ERROR_MARKERS: tuple[str, ...] = (
        "401",
        "unauthorized",
        "invalid_token",
        "invalid_grant",
        "authentication",
    )

    @staticmethod
    def _is_auth_error(response: GitLabResponse | None) -> bool:
        """True when a failed GitLabResponse indicates an OAuth auth failure."""
        if response is None or response.success:
            return False
        err = (response.error or "").lower()
        return any(marker in err for marker in GitLabConnector._AUTH_ERROR_MARKERS)

    async def _force_refresh_oauth_token(self) -> bool:
        """Trigger an OAuth refresh via the central TokenRefreshService and sync
        the SDK with the rotated access token.

        Used reactively when a GitLab API call returns 401, so we don't wait
        for the background refresher to catch up. No-op for API_TOKEN auth.
        """
        try:
            from app.connectors.core.base.token_service.startup_service import (
                startup_service,
            )

            refresh_service = startup_service.get_token_refresh_service()
            if not refresh_service:
                self.logger.error(
                    "Token refresh service unavailable; cannot refresh GitLab token."
                )
                return False

            config_path = f"/services/connectors/{self.connector_id}/config"
            config = await self.config_service.get_config(config_path)
            if not config:
                self.logger.error(
                    "Connector config not found; cannot refresh GitLab token."
                )
                return False

            auth_config = config.get("auth", {}) or {}
            if auth_config.get("authType", "OAUTH") == "API_TOKEN":
                self.logger.debug("API_TOKEN auth does not use OAuth refresh.")
                return False

            refresh_token = (config.get("credentials") or {}).get("refresh_token")
            if not refresh_token:
                self.logger.error(
                    "No refresh token in connector config; cannot refresh GitLab."
                )
                return False

            connector_type = (
                self.connector_name.value
                if hasattr(self.connector_name, "value")
                else str(self.connector_name)
            )
            await refresh_service.refresh_now(
                self.connector_id, connector_type, refresh_token
            )
            # Sync the live SDK and GraphQL bearer token from etcd.
            await self._refresh_token_if_needed()
            return True
        except Exception as e:
            self.logger.error(
                f"GitLab OAuth token refresh failed: {e}", exc_info=True
            )
            return False

    def _apply_access_token_to_clients(self, access_token: str) -> None:
        """Push a refreshed access token to the REST SDK and GraphQL client."""
        if not access_token:
            return
        if self.external_client:
            internal_client = self.external_client.get_client()
            if internal_client.get_token() != access_token:
                internal_client.set_token(access_token)
        if self.data_source is not None:
            self.data_source.token = access_token

    async def _execute_gitlab_op(
        self,
        op: Callable[[], GitLabResponse | Awaitable[GitLabResponse]],
        *,
        timeout: float | None = None,
        op_label: str | None = None,
    ) -> GitLabResponse:
        """Run a GitLab data-source op without blocking the event loop.

        ``timeout`` is a wall-clock budget (seconds) for the entire logical
        operation. Defaults to ``_GITLAB_OP_DEFAULT_TIMEOUT_SECONDS``. On
        timeout we surface a ``success=False`` response so the caller's
        existing failure-handling path runs (creator fallback,
        skip-and-continue, etc.) instead of the asyncio task being stuck
        inside a ``get_all=True`` materialisation that fans out into
        thousands of sequential HTTP calls.

        Sync ops run on ``self._gitlab_executor`` rather than the loop's
        default executor (``asyncio.to_thread``). Once a GitLab instance
        starts hanging on ``recv()``, the orphaned worker threads still
        hold pool slots until python-gitlab's per-request ``timeout=30``
        unwinds them; routing through a per-connector pool keeps that
        backpressure off the shared pool that FastAPI handlers, sync
        DB drivers, and other connectors depend on.

        ``asyncio.wait_for`` cancels the *coroutine* we're awaiting; it
        cannot interrupt the worker thread on the executor. That thread
        is left to unwind via python-gitlab's own per-request
        ``timeout=30`` ceiling. We intentionally do not ``join`` it —
        the whole point of this budget is to free the event loop
        without making the operator wait for the worst-case-misbehaving
        server.
        """
        budget = timeout if timeout is not None else _GITLAB_OP_DEFAULT_TIMEOUT_SECONDS
        label = op_label or getattr(op, "__name__", "<gitlab op>")
        if inspect.iscoroutinefunction(op):

            async def _async_op() -> GitLabResponse:
                return await op()

            coro: Awaitable[GitLabResponse] = _async_op()
        else:
            def _invoke_sync_op() -> GitLabResponse:
                outcome = op()
                if inspect.isawaitable(outcome):
                    raise RuntimeError(
                        "GitLab sync op returned a coroutine; use _ds_call_async instead."
                    )
                return outcome

            loop = asyncio.get_running_loop()
            coro = loop.run_in_executor(self._gitlab_executor, _invoke_sync_op)

        try:
            return await asyncio.wait_for(coro, timeout=budget)
        except asyncio.TimeoutError:
            self.logger.error(
                "GitLab op %s exceeded %.0fs wall-clock budget; abandoning the "
                "in-flight worker and returning success=False so the caller can "
                "fall back. Increase the budget or scope the sync_filters down "
                "if this fires under normal load.",
                label,
                budget,
            )
            return GitLabResponse(
                success=False,
                data=None,
                error=f"GitLab op timed out after {budget:.0f}s",
            )

    async def _call_with_auth_retry(
        self,
        op: Callable[[], GitLabResponse | Awaitable[GitLabResponse]],
        *,
        timeout: float | None = None,
        op_label: str | None = None,
    ) -> GitLabResponse:
        """Run a GitLab data-source op; on a 401-style failure, refresh the OAuth
        token once and retry. Accepts both sync- and async-returning ops.

        ``timeout`` applies independently to each attempt — a stuck call
        that triggers a refresh-and-retry will not pay the budget twice
        and then *also* hang the second attempt forever.
        """
        response = await self._execute_gitlab_op(
            op, timeout=timeout, op_label=op_label
        )
        if not self._is_auth_error(response):
            return response

        self.logger.info(
            "GitLab API returned auth error; refreshing OAuth token and retrying once."
        )
        if not await self._force_refresh_oauth_token():
            return response

        return await self._execute_gitlab_op(
            op, timeout=timeout, op_label=op_label
        )

    async def _ds_call(
        self,
        method: Callable[..., GitLabResponse],
        /,
        *args: Any,
        _gitlab_timeout: float | None = None,
        **kwargs: Any,
    ) -> GitLabResponse:
        """Run a synchronous GitLabDataSource method with OAuth retry on 401.

        ``_gitlab_timeout`` lets callers override the default wall-clock
        budget for a single logical call (e.g. give a large membership
        sweep more headroom than a single ``get_user`` lookup). Underscore
        prefix avoids any collision with python-gitlab kwargs.
        """

        def op() -> GitLabResponse:
            return method(*args, **kwargs)

        return await self._call_with_auth_retry(
            op,
            timeout=_gitlab_timeout,
            op_label=getattr(method, "__name__", None),
        )

    async def _ds_call_async(
        self,
        method: Callable[..., Awaitable[GitLabResponse]],
        /,
        *args: Any,
        _gitlab_timeout: float | None = None,
        **kwargs: Any,
    ) -> GitLabResponse:
        """Run an async GitLabDataSource method (e.g. GraphQL) with OAuth retry."""

        async def op() -> GitLabResponse:
            return await method(*args, **kwargs)

        return await self._call_with_auth_retry(
            op,
            timeout=_gitlab_timeout,
            op_label=getattr(method, "__name__", None),
        )

    async def _paged_list(
        self,
        method: Callable[..., GitLabResponse],
        /,
        *args: Any,
        progress_label: str,
        progress_every: int = 500,
        **kwargs: Any,
    ) -> GitLabResponse:
        """Stream a ``list_*`` op page-by-page and log INFO progress.

        Replaces ``get_all=True`` on the big sweeps (``list_groups``,
        ``list_projects``, ``list_group_projects``). With ``get_all=True``
        python-gitlab materializes every page inside one ``asyncio.to_thread``
        call with no log output — on a RingCentral-sized EE tenant that
        single call can take an hour and looks indistinguishable from a
        hang. Here we ask the SDK for a ``GitlabList`` iterator and pull
        items in batches off-loop, emitting an INFO line every
        ``progress_every`` items so the operator can see forward motion.

        Returns a normal ``GitLabResponse`` whose ``data`` is a fully
        materialized ``list[...]`` to keep callers compatible. On a
        mid-iteration failure (e.g. a 5xx exhausting ``retry_transient_errors``
        in the middle of page 47) we return the partial list inside a
        ``success=False`` response with the error string, so the caller
        can still decide what to do — silent partial success would mark
        every missing user inactive on the next reconciliation.
        """
        iter_res = await self._ds_call(method, *args, iterator=True, **kwargs)
        if not iter_res.success or iter_res.data is None:
            return iter_res

        # python-gitlab returns a GitlabList iterator with ``iterator=True``,
        # but tests (and a few real call sites that pre-date this helper)
        # may hand back a plain list. ``iter()`` is the single canonical
        # entry point that works for both without a special case.
        paged_iter = iter(iter_res.data)

        # ``_drain`` accumulates into a fresh per-batch list and returns it
        # rather than mutating a shared one. The shared-list pattern was
        # unsafe once we wrapped ``run_in_executor`` in ``wait_for``
        # below: ``wait_for`` cancels the awaiting coroutine but cannot
        # stop the worker thread, so the thread can keep ``out.append()``
        # -ing into the caller's list after we have already moved on and
        # started another batch. Returning the batch keeps ownership
        # one-sided. The "no discard on mid-batch error" property still
        # holds at batch granularity — see the err branch below.
        items: list[Any] = []

        def _drain(it: Any, n: int) -> tuple[list[Any], bool, Exception | None]:
            out: list[Any] = []
            for _ in range(n):
                try:
                    out.append(next(it))
                except StopIteration:
                    return out, True, None
                except Exception as e:  # noqa: BLE001 — surface upstream
                    return out, True, e
            return out, False, None

        loop = asyncio.get_running_loop()
        while True:
            try:
                # Run on the connector's dedicated executor (not
                # ``asyncio.to_thread``) so a stuck page fetch only
                # consumes a slot in this connector's GitLab pool, not
                # the loop's shared default pool.
                batch, done, err = await asyncio.wait_for(
                    loop.run_in_executor(
                        self._gitlab_executor, _drain, paged_iter, progress_every
                    ),
                    timeout=_GITLAB_PAGE_BATCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                # The orphaned worker thread is left to unwind via
                # python-gitlab's per-request ``timeout=30``; we cannot
                # cancel it from here and we explicitly do not join it
                # — that is the whole point of this budget. Returning
                # the partial list under ``success=False`` lets the
                # caller distinguish "scan aborted midway" from "scan
                # completed and saw nothing".
                self.logger.error(
                    "%s: page batch exceeded %.0fs wall-clock budget after "
                    "%s items; abandoning the worker thread and surfacing a "
                    "timeout. The connector was likely frozen on a stuck "
                    "GitLab page fetch (slow EE proxy, half-open TCP, "
                    "network blip). Increase the budget or scope the "
                    "sync_filters down if this fires under normal load.",
                    progress_label,
                    _GITLAB_PAGE_BATCH_TIMEOUT_SECONDS,
                    len(items),
                )
                return GitLabResponse(
                    success=False,
                    data=items,
                    error=(
                        f"GitLab page batch timed out after "
                        f"{_GITLAB_PAGE_BATCH_TIMEOUT_SECONDS:.0f}s"
                    ),
                )

            items.extend(batch)
            if items:
                self.logger.info(
                    f"{progress_label}: fetched {len(items)} so far"
                )
            if err is not None:
                # Returning a partial list under success=False lets the
                # caller distinguish "scan aborted midway" from "scan
                # completed and saw nothing". The latter would otherwise
                # legitimately tombstone users; the former must not.
                self.logger.error(
                    f"{progress_label}: error after {len(items)} items: {err}",
                    exc_info=err,
                )
                return GitLabResponse(
                    success=False, data=items, error=str(err)
                )
            if done:
                break

        self.logger.info(f"{progress_label}: complete, total={len(items)}")
        return GitLabResponse(success=True, data=items)

    async def _refresh_token_if_needed(self) -> None:
        """Update the active client token from etcd when the background TokenRefreshService has rotated it.

        For API_TOKEN auth the token never expires via OAuth refresh, so this is a no-op.
        For OAUTH auth we compare the currently-held token with whatever is stored in etcd
        and call ``set_token()`` if they differ, so all subsequent API calls use the
        up-to-date credential without requiring a full client rebuild.
        """
        if not self.external_client:
            return

        try:
            config_path = f"/services/connectors/{self.connector_id}/config"
            config = await self.config_service.get_config(config_path)
            if not config:
                return

            auth_config = config.get("auth", {}) or {}
            auth_type = auth_config.get("authType", "OAUTH")

            # PAT-based auth does not use refresh tokens; nothing to do
            if auth_type == "API_TOKEN":
                return

            credentials = config.get("credentials", {}) or {}
            fresh_token = credentials.get("access_token", "")
            if not fresh_token:
                return

            internal_client = self.external_client.get_client()
            current_token = internal_client.get_token()

            if current_token != fresh_token:
                self.logger.debug("Updating GitLab client with refreshed OAuth token")
                self._apply_access_token_to_clients(fresh_token)
        except Exception as e:
            # Token refresh is best-effort; do not abort the calling operation
            self.logger.warning(f"Could not refresh GitLab token: {e}")

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream a record from Gitlab(Ticket, Pull Request, File, Code File).
        Args:
            record: Record object containing file/message information
        Returns:
            StreamingResponse with file/message content
        """
        try:
            await self._refresh_token_if_needed()
            if record.record_type == RecordType.TICKET:
                self.logger.info(" STREAM_TICKET_MARKER ")
                blocks_container = await self._build_ticket_blocks(record)

                return StreamingResponse(
                    content=iter([blocks_container]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={
                        "Content-Disposition": f"attachment; filename={record.record_name}"
                    },
                )
            elif record.record_type == RecordType.PULL_REQUEST:
                self.logger.info(" STREAM_MERGE_REQUEST_MARKER ")
                block_container = await self._build_pull_request_blocks(record)

                return StreamingResponse(
                    content=iter([block_container]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={
                        "Content-Disposition": f"attachment; filename={record.record_name}"
                    },
                )
            elif record.record_type == RecordType.FILE:
                self.logger.info(" STREAM-FILE-MARKER ")
                filename = record.record_name or f"{record.external_record_id}"
                # Eagerly pull the first chunk so GitLab API failures (404,
                # expired token, etc.) raise here — before StreamingResponse
                # commits headers — instead of corrupting the chunked stream
                # mid-flight. The rest of the attachment streams lazily so we
                # do not buffer large files in memory.
                primed_stream = await _stream_with_eager_first_chunk(
                    self._fetch_attachment_content(record)
                )
                return create_stream_record_response(
                    primed_stream,
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}",
                )
            elif record.record_type == RecordType.CODE_FILE:
                self.logger.info(" STREAM-CODE-FILE-MARKER ")
                if not isinstance(record, CodeFileRecord):
                    raise ValueError(
                        f"Expected CodeFileRecord for CODE_FILE stream, got {type(record).__name__}"
                    )
                filename = record.record_name or f"{record.external_record_id}"
                primed_stream = await _stream_with_eager_first_chunk(
                    self._fetch_code_file_content(record)
                )
                return create_stream_record_response(
                    primed_stream,
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}",
                )
            else:
                raise ValueError(
                    f"Unsupported record type for streaming: {record.record_type}"
                )
        except Exception as e:
            self.logger.error(
                f"Error streaming record {record.external_record_id}: {e}",
                exc_info=True,
            )
            raise

    # ------------------Sync Points-----------------------------------#
    async def _get_issues_sync_checkpoint(self, project_id: int) -> int | None:
        """
        Get project issues sync checkpoint.
        Returns: epoch last sync time in milliseconds
        """
        try:
            group_project_id = str(project_id) + "-work-items"
            sync_point_key = generate_record_sync_point_key(
                Connectors.GITLAB.value, group_project_id, ""
            )
            sync_point_data = await self.record_sync_point.read_sync_point(
                sync_point_key
            )
            return (
                sync_point_data.get(GitlabLiterals.LAST_SYNC_TIME.value)
                if sync_point_data
                else None
            )
        except Exception:
            return None

    async def _update_issues_sync_checkpoint(
        self, project_id: str, last_sync_time: str
    ) -> None:
        """
        Update project issues sync checkpoint.
        """
        sync_point_key = generate_record_sync_point_key(
            Connectors.GITLAB.value, project_id, ""
        )
        sync_point_data = {GitlabLiterals.LAST_SYNC_TIME.value: last_sync_time}
        await self.record_sync_point.update_sync_point(sync_point_key, sync_point_data)

    async def _get_mr_sync_checkpoint(self, project_id: int) -> int | None:
        """
        Get project merge requests sync checkpoint.
        Returns: epoch last sync time in milliseconds
        """
        try:
            group_project_id = str(project_id) + "-merge-requests"
            sync_point_key = generate_record_sync_point_key(
                Connectors.GITLAB.value, group_project_id, ""
            )
            sync_point_data = await self.record_sync_point.read_sync_point(
                sync_point_key
            )
            return (
                sync_point_data.get(GitlabLiterals.LAST_SYNC_TIME.value)
                if sync_point_data
                else None
            )
        except Exception:
            return None

    async def _update_mrs_sync_checkpoint(
        self, project_id: str, last_sync_time: str
    ) -> None:
        """
        Update project merge requests sync checkpoint.
        """
        sync_point_key = generate_record_sync_point_key(
            Connectors.GITLAB.value, project_id, ""
        )
        sync_point_data = {GitlabLiterals.LAST_SYNC_TIME.value: last_sync_time}
        await self.record_sync_point.update_sync_point(sync_point_key, sync_point_data)

    async def _get_code_repo_checkpoint(self, project_id: int) -> str | None:
        """Return the last synced HEAD commit SHA for a project's code repository."""
        try:
            group_project_id = f"{project_id}-code-repository"
            sync_point_key = generate_record_sync_point_key(
                Connectors.GITLAB.value, group_project_id, ""
            )
            sync_point_data = await self.record_sync_point.read_sync_point(
                sync_point_key
            )
            if not sync_point_data:
                return None
            sha = sync_point_data.get(GitlabLiterals.LAST_COMMIT_SHA.value)
            return str(sha) if sha else None
        except Exception:
            return None

    async def _update_code_repo_checkpoint(
        self, project_id: int, commit_sha: str
    ) -> None:
        """Persist the HEAD commit SHA after a successful code repository sync."""
        group_project_id = f"{project_id}-code-repository"
        sync_point_key = generate_record_sync_point_key(
            Connectors.GITLAB.value, group_project_id, ""
        )
        sync_point_data = {GitlabLiterals.LAST_COMMIT_SHA.value: commit_sha}
        await self.record_sync_point.update_sync_point(sync_point_key, sync_point_data)

    @staticmethod
    def _branch_head_commit_sha(branch_data: Any) -> str | None:
        """Extract commit id from a python-gitlab branch or REST dict."""
        commit = getattr(branch_data, "commit", None)
        if commit is None and isinstance(branch_data, dict):
            commit = branch_data.get("commit")
        if commit is None:
            return None
        if isinstance(commit, dict):
            sha = commit.get("id")
            return str(sha) if sha else None
        sha = getattr(commit, "id", None)
        return str(sha) if sha else None

    async def run_sync(self) -> None:
        """syncing various entities"""
        try:
            await self._cancel_code_file_timestamp_backfill()
            await self._refresh_token_if_needed()
            self.logger.info("⚒️⚒️ Starting GitLab sync")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "gitlab", self.connector_id, self.logger
            )
            self._gitlab_included_group_paths = None
            self.logger.info("Starting sync of Gitlab users")
            await self._sync_users()
            # TODO: sync members from user groups of gitlab if needed
            # TODO: projects belonging to a specific group same as projects belonging to a user group
            # TODO: what to consider these groups then link projects to these groups ?
            self.logger.info("🕛🕛 Starting sync of projects")
            await self._sync_all_project()
            self._schedule_code_file_timestamp_backfill_after_sync()
        except Exception as e:
            self.logger.error(f"Error in GitLab sync: {e}", exc_info=True)
            raise

    # ---------------------------Users Sync-----------------------------------#
    async def _resolve_user_sync_scope(
        self,
    ) -> tuple[list[str], list[str]] | None:
        """Resolve ``(group_targets, project_targets)`` for the user-sync walk.

        Mirrors ``_resolve_projects_with_filters`` so user discovery walks
        the same universe of groups and projects as project discovery.

        - ``PROJECT_IDS IN`` is authoritative when set: the user-sync
          walk only walks those projects (whose ``members_all`` includes
          inherited group members, so no users are missed). ``GROUP_IDS
          IN`` is ignored as a sync widener in this case — same contract
          as ``_resolve_projects_with_filters``. Without this short-
          circuit the user walk would enumerate every member of the
          listed group's project tree even though only a few projects
          actually sync, manufacturing ``AppUser`` rows with no records.
        - ``GROUP_IDS IN`` (without ``PROJECT_IDS IN``): walk members
          of the listed groups + every subgroup project they own.
        - ``GROUP_IDS`` / ``PROJECT_IDS`` ``NOT_IN``: materializes the
          set of visible groups/projects once and drops the excluded
          ones (plus subgroups under any excluded prefix). This is what
          ``_resolve_projects_with_filters`` already does for
          project resolution — without the same treatment here the
          user-sync walk falls through to ``_sync_users_unscoped`` and
          repeats the full-tenant scan that NOT_IN was meant to avoid.
        - Returns ``None`` when no group/project sync filter is
          configured; callers then fall back to the unscoped sweep over
          the bot's visible groups + projects.
        """
        sf = self.sync_filters
        if not sf:
            return None

        grp_f = sf.get(SyncFilterKey.GROUP_IDS)
        proj_f = sf.get(SyncFilterKey.PROJECT_IDS)
        grp_active = grp_f is not None and not grp_f.is_empty()
        proj_active = proj_f is not None and not proj_f.is_empty()

        if not grp_active and not proj_active:
            return None

        grp_op = _filter_op_val(grp_f) if grp_active else None
        proj_op = _filter_op_val(proj_f) if proj_active else None

        group_targets: list[str] = []
        project_targets: list[str] = []

        # PROJECT_IDS IN short-circuits GROUP_IDS IN (the group filter
        # was already used as picker scope on the UI; treating it as a
        # sync widener here would walk members of every project under
        # the group, even though only the listed projects actually
        # sync). Mirrors ``_resolve_projects_with_filters``.
        proj_in_short_circuits_grp_in = (
            proj_active and proj_op == FilterOperator.IN
        )

        if (
            grp_active
            and grp_op == FilterOperator.IN
            and not proj_in_short_circuits_grp_in
        ):
            group_targets = list(grp_f.value)  # type: ignore[arg-type]
        elif grp_active and grp_op == FilterOperator.NOT_IN:
            excluded = list(grp_f.value)  # type: ignore[arg-type]
            # Role-aware listing with auditor fallback. Regular members
            # get ``min_access_level=10``; admins / EE Auditors get
            # ``all_available=True`` (documented contract). Auditors
            # additionally fall back to ``min_access_level=10`` if the
            # primary scope returns empty — see GitLab's auditor known
            # issue documented on ``_list_groups_scope_kwargs``.
            groups_res = await self._paged_list_groups_with_role_fallback(
                per_page=100,
                progress_label="list_groups NOT_IN user-sync scope",
            )
            if not groups_res.success:
                self.logger.error(
                    "Could not list groups for NOT_IN user-sync scope: %s",
                    groups_res.error,
                )
            else:
                excluded_set = set(excluded)
                for g in groups_res.data or []:
                    gfp = getattr(g, "full_path", None)
                    if (
                        gfp
                        and gfp not in excluded_set
                        and not self._namespace_under_any_prefix(gfp, excluded)
                    ):
                        group_targets.append(gfp)

        if proj_active and proj_op == FilterOperator.IN:
            project_targets = list(proj_f.value)  # type: ignore[arg-type]
        elif proj_active and proj_op == FilterOperator.NOT_IN:
            excluded = list(proj_f.value)  # type: ignore[arg-type]
            # When GROUP_IDS NOT_IN is also configured, projects under
            # any excluded group prefix must be dropped here too, so the
            # user walk stays consistent with the project walk.
            group_prefixes = (
                list(grp_f.value)  # type: ignore[arg-type]
                if grp_active and grp_op == FilterOperator.NOT_IN
                else []
            )
            # Role-aware listing with auditor fallback. Regular members
            # narrow to ``membership=True``; admins / EE Auditors start
            # from "all visible" and rely on the documented auditor
            # read-all (with a ``membership=True`` fallback when the
            # auditor primary returns empty — see the GitLab known
            # issue documented on ``_list_groups_scope_kwargs``).
            projects_res = await self._paged_list_projects_with_role_fallback(
                pagination="keyset",
                order_by="id",
                sort="asc",
                per_page=100,
                progress_label="list_projects NOT_IN user-sync scope",
            )
            if not projects_res.success:
                self.logger.error(
                    "Could not list projects for NOT_IN user-sync scope: %s",
                    projects_res.error,
                )
            else:
                excluded_set = set(excluded)
                for p in projects_res.data or []:
                    pth = getattr(p, "path_with_namespace", None)
                    if not pth or pth in excluded_set:
                        continue
                    if group_prefixes and self._namespace_under_any_prefix(
                        self._namespace_full_path(p), group_prefixes
                    ):
                        continue
                    project_targets.append(pth)

        return group_targets, project_targets

    async def _sync_users(self) -> None:
        """Fetch all active Gitlab users of groups and projects.

        Honors ``sync_filters``: when ``GROUP_IDS`` or ``PROJECT_IDS``
        (``IN`` or ``NOT_IN``) is configured we only walk members of the
        in-scope entities. Without that early scoping a connector
        instance targeting a few top-level groups on a large EE
        deployment still scans every group the bot account is Guest+ on,
        which is the root cause of the "stuck after 'Starting sync of
        Gitlab users'" symptom on RingCentral-sized tenants. The
        ``NOT_IN`` branch trades the unscoped member sweep for a single
        ``list_groups`` / ``list_projects`` materialization plus a
        member walk over the surviving set — strictly cheaper.
        """
        scope = await self._resolve_user_sync_scope()
        if scope is not None:
            group_paths, project_paths = scope
            self.logger.info(
                "Scoped user sync: %s group(s), %s project(s) from sync_filters",
                len(group_paths),
                len(project_paths),
            )
            await self._sync_users_scoped(group_paths, project_paths)
            return

        await self._sync_users_unscoped()

    async def _sync_users_scoped(
        self, group_paths: list[str], project_paths: list[str]
    ) -> None:
        """Walk members of explicitly-configured groups/projects only.

        Member discovery must cover the same project universe as
        ``_resolve_projects_with_filters``. ``GET /groups/:id/members/all``
        only inherits from *ancestor* groups, not descendants, so a
        configured top-level group does not reach users added directly
        to one of its subgroups. Project sync, however, resolves every
        project under the configured group via
        ``list_group_projects(include_subgroups=True)`` and attributes
        permissions to those subgroup-direct members. If we don't walk
        the same project set here, ``_sync_users_from_projects_groups``
        never inserts those users as ``AppUser`` rows,
        ``_create_permission_from_principal`` then misses,
        ``create_pseudo_group_if_missing`` makes an empty pseudo-group,
        and the user silently loses access to records they can see on
        GitLab even when their ``public_email`` is set.

        If *every* configured group/project listing fails (403 from a
        non-admin token, an EE membership API bug, sustained 5xx, our
        own wall-clock timeout) we fall back to creating an ``AppUser``
        for the connector creator. The previous behaviour raised
        ``RuntimeError`` and aborted the whole sync, which on a freshly
        configured connector meant the operator could not see *any* of
        their own records. Granting creator-only access mirrors the
        Jira ``_fallback_permissions_for_forbidden_scheme`` pattern.
        """
        dict_member: dict[int, GroupMember] = {}
        total_groups_synced = 0
        total_groups_skipped = 0
        total_projects_synced = 0
        total_projects_skipped = 0
        total_member_rows_seen = 0
        any_success = False
        walked_project_ids: set[int] = set()

        async def _walk_project_members(
            project_id: int | str, label: str
        ) -> tuple[bool, int]:
            """Returns ``(succeeded_with_data, member_rows_returned)``.

            ``member_rows_returned`` is the raw row count from
            ``members_all`` for this project — it is NOT deduped against
            ``dict_member``. A persistently zero row count across many
            projects almost always means the bot's role on the parent
            group is too low to enumerate members (GitLab returns 200 OK
            with [] for Guest/Reporter on private projects rather than 403),
            and is the most common cause of "users don't see records they
            can see on GitLab".

            ``succeeded_with_data`` is False when the call succeeded but
            returned no rows — same semantics as ``listing_empty`` in
            ``_sync_project_members_as_pseudo`` — so the creator fallback
            can run when every listing is ``200 OK + []``.
            """
            try:
                pres = await self._ds_call(
                    self.data_source.list_project_members_all,
                    project_id=project_id,
                    get_all=True,
                )
                if not pres.success:
                    self.logger.error(
                        "Error fetching members for %s: %s", label, pres.error
                    )
                    return False, 0
                rows = list(pres.data or [])
                for member in rows:
                    dict_member[member.id] = member
                return bool(rows), len(rows)
            except Exception as e:
                self.logger.error(
                    f"Error in syncing users for {label}: {e}", exc_info=True
                )
                return False, 0

        for i, group_path in enumerate(group_paths, start=1):
            self.logger.info(
                "syncing users for configured group %s/%s (%s)",
                i,
                len(group_paths),
                group_path,
            )
            try:
                members_res = await self._ds_call(
                    self.data_source.list_group_members_all,
                    group_id=group_path,
                    get_all=True,
                )
                if not members_res.success:
                    self.logger.error(
                        f"Error fetching members for configured group {group_path}: "
                        f"{members_res.error}"
                    )
                    total_groups_skipped += 1
                else:
                    rows = list(members_res.data or [])
                    for member in rows:
                        dict_member[member.id] = member
                    # ``200 OK + []`` must not count as success — parity with
                    # ``listing_empty`` in ``_sync_project_members_as_pseudo``.
                    if rows:
                        any_success = True
                    total_groups_synced += 1
            except Exception as e:
                self.logger.error(
                    f"Error in syncing users for group {group_path}: {e}",
                    exc_info=True,
                )
                # Fall through to the subgroup-project walk: a bad
                # ancestor-members call must not silently drop every
                # user under the configured subtree.

            try:
                gres = await self._paged_list(
                    self.data_source.list_group_projects,
                    group_path,
                    include_subgroups=True,
                    progress_label=f"list_group_projects user-sync({group_path})",
                )
            except Exception as e:
                self.logger.error(
                    f"Error expanding subgroup projects for {group_path}: {e}",
                    exc_info=True,
                )
                continue
            if not gres.success:
                self.logger.error(
                    "Could not fully list subgroup projects for user sync of "
                    "group %s: %s. Walking the partial list (%s project(s) "
                    "collected so far).",
                    group_path,
                    gres.error,
                    len(gres.data or []),
                )
            projects = list(gres.data or [])
            if not projects:
                continue
            total = len(projects)
            group_member_rows = 0
            group_projects_walked = 0
            for j, p in enumerate(projects, start=1):
                pid = getattr(p, "id", None)
                if pid is None or pid in walked_project_ids:
                    continue
                walked_project_ids.add(pid)
                ppath = getattr(p, "path_with_namespace", None) or str(pid)
                self.logger.debug(
                    "syncing users for subgroup project %s/%s under %s (%s)",
                    j,
                    total,
                    group_path,
                    ppath,
                )
                ok, n_rows = await _walk_project_members(
                    pid, f"subgroup project {ppath}"
                )
                if ok:
                    any_success = True
                    total_projects_synced += 1
                    group_projects_walked += 1
                    group_member_rows += n_rows
                    total_member_rows_seen += n_rows
                else:
                    total_projects_skipped += 1
            self.logger.info(
                "Group %s subgroup-expansion summary: %s/%s project(s) walked, "
                "%s member row(s) returned across them (running unique members: %s)",
                group_path,
                group_projects_walked,
                total,
                group_member_rows,
                len(dict_member),
            )

        for i, project_path in enumerate(project_paths, start=1):
            self.logger.info(
                "syncing users for configured project %s/%s (%s)",
                i,
                len(project_paths),
                project_path,
            )
            # Path-keyed dedup against ids isn't possible without an
            # extra GET; a duplicate ``members_all`` call on a project
            # that already came in via group expansion is wasted work
            # but ``dict_member`` overwrites make it safe.
            ok, n_rows = await _walk_project_members(
                project_path, f"configured project {project_path}"
            )
            if ok:
                any_success = True
                total_projects_synced += 1
                total_member_rows_seen += n_rows
                self.logger.info(
                    "Configured project %s: members_all returned %s row(s)",
                    project_path,
                    n_rows,
                )
            else:
                total_projects_skipped += 1

        # Always inject the connector creator so Admin / EE Auditor
        # personas — whose cross-instance read access flows from a
        # user-level flag rather than a membership row — keep access
        # to records they configured the sync to ingest. ``setdefault``
        # in ``_inject_creator_member_into`` preserves any real row
        # found upstream (with its actual ``access_level`` /
        # ``public_email``), so this never downgrades a member row.
        creator_added = self._inject_creator_member_into(dict_member)
        # When every configured target failed AND we could not even
        # synthesize a creator stub, persisting an empty member set
        # would silently mark every user inactive on the next
        # reconciliation pass. Fail loudly instead.
        all_failed = bool(group_paths or project_paths) and not any_success
        if all_failed and not creator_added:
            raise RuntimeError(
                "GitLab user sync aborted: every configured group/project "
                "failed to enumerate members and no creator identity "
                "was resolved (cannot fall back)."
            )
        if all_failed:
            self.logger.warning(
                "GitLab user sync: every configured group/project failed "
                "to enumerate members; relying on creator-only access "
                "(%s) for this run.",
                self.creator_email,
            )

        self.logger.info(
            f"Total groups synced: {total_groups_synced}, Total groups skipped: {total_groups_skipped}"
        )
        self.logger.info(
            f"Total projects synced: {total_projects_synced}, Total projects skipped: {total_projects_skipped}"
        )
        # The ratio of (member rows seen) : (unique members) makes the
        # "bot can't enumerate members" failure mode visible. If
        # total_member_rows_seen is ~equal to the unique member count
        # and both are tiny relative to total_projects_synced, the bot
        # almost certainly lacks the role required to list members on
        # most projects (200 OK + [] for Guest/Reporter on private repos).
        avg_rows_per_project = (
            total_member_rows_seen / total_projects_synced
            if total_projects_synced
            else 0
        )
        self.logger.info(
            "Scoped user-sync membership summary: %s unique member(s) "
            "from %s member row(s) across %s project(s) "
            "(avg %.2f rows/project)",
            len(dict_member),
            total_member_rows_seen,
            total_projects_synced,
            avg_rows_per_project,
        )
        dict_member = await self._enrich_members_with_full_user(dict_member)
        await self._sync_users_from_projects_groups(dict_member)
        self.logger.info("Users sync and migration of pseudo groups complete")

    async def _sync_users_unscoped(self) -> None:
        """Fall-back path: scan every group/project visible to the bot.

        Streams via ``_paged_list`` so the operator sees per-page INFO
        progress instead of one opaque blocking call. Still expensive
        on large tenants — prefer scoping via ``GROUP_IDS`` /
        ``PROJECT_IDS`` sync filters.
        """
        # Include every group the user has at least Guest access to so we
        # discover members of groups they don't personally own.
        # NOTE: GitLab's REST API does NOT support keyset pagination on
        # ``/groups`` for authenticated requests (only for unauthenticated
        # listings of public groups with order_by=name). Use the iterator
        # so we can log per-page progress; each request still uses offset
        # pagination on the server side.
        # Role-aware listing with auditor-empty fallback. Regular
        # members get ``min_access_level=10``; admins / EE Auditors get
        # ``all_available=True``. For auditors, an empty primary result
        # silently retries with ``min_access_level=10`` — see the
        # GitLab known issue documented on ``_list_groups_scope_kwargs``.
        scope_kwargs = self._list_groups_scope_kwargs()
        groups_res = await self._paged_list_groups_with_role_fallback(
            per_page=100,
            progress_label=f"list_groups ({scope_kwargs})",
        )
        # TODO: check in enterprise edition do gitlab accounts have members directly in it
        total_groups_synced = 0
        total_groups_skipped = 0
        total_projects_synced = 0
        total_projects_skipped = 0
        dict_member: dict[int, GroupMember] = {}
        # dict of member_id -> member
        groups_failed = not groups_res.success
        if groups_failed:
            self.logger.error(
                f"Error in fetching groups: {groups_res.error}, continuing with projects members"
            )
        if groups_res.data:
            groups = groups_res.data
            total = len(groups)
            for i, group in enumerate(groups, start=1):
                try:
                    group_id = getattr(group, "id", None)
                    if group_id is None:
                        self.logger.warning("Group missing ID, skipping ")
                        total_groups_skipped += 1
                        continue
                    # Per-group INFO (1 per group) — DEBUG was invisible
                    # in production and made the inner loop look hung.
                    self.logger.info(
                        "syncing users for group %s/%s id=%s",
                        i,
                        total,
                        group_id,
                    )
                    members_res = await self._ds_call(
                        self.data_source.list_group_members_all,
                        group_id=group_id,
                        get_all=True,
                    )
                    if not members_res.success:
                        self.logger.info(
                            f"Error in fetching members for group {group_id}"
                        )
                        total_groups_skipped += 1
                        continue
                    members = members_res.data
                    for member in members:
                        dict_member[member.id] = member
                    total_groups_synced += 1
                except Exception as e:
                    self.logger.error(
                        f"Error in syncing users for group {group_id}: {e}",
                        exc_info=True,
                    )
                    continue
        # syncing from all projects

        # Keyset pagination keeps per-page cost constant on /projects.
        # Drive the iterator so we get per-page progress logs — on busy
        # instances this is the second most common spot for a
        # Puma-timeout 502.
        #
        # Role-aware listing with auditor-empty fallback. Regular
        # members get ``membership=True``; admins / EE Auditors get
        # the default "all visible" scope. For auditors, an empty
        # primary result silently retries with ``membership=True`` —
        # see the GitLab known issue documented on
        # ``_list_groups_scope_kwargs``.
        proj_scope_kwargs = self._list_projects_scope_kwargs()
        projects_res = await self._paged_list_projects_with_role_fallback(
            pagination="keyset",
            order_by="id",
            sort="asc",
            per_page=100,
            progress_label=f"list_projects ({proj_scope_kwargs})",
        )
        projects_failed = not projects_res.success
        if projects_failed:
            self.logger.error(f"Error in fetching projects: {projects_res.error}")
        # Always inject the connector creator (Admin / EE Auditor
        # personas have cross-instance read access via a user-level
        # flag, not a membership row, so the listings above will
        # systematically omit them). ``setdefault`` preserves any real
        # row already in the dict.
        creator_added = self._inject_creator_member_into(dict_member)
        # If both the groups call and the projects call fail AND we
        # could not even synthesize a creator stub, we have no source
        # of truth for membership this run. Persisting an empty member
        # set would silently mark every user inactive on the next
        # reconciliation pass. Fail loudly instead.
        if groups_failed and projects_failed and not creator_added:
            raise RuntimeError(
                "GitLab user sync aborted: both list_groups and list_projects "
                f"failed (groups: {groups_res.error}; projects: {projects_res.error}) "
                "and no creator identity was resolved (cannot fall back)."
            )
        if groups_failed and projects_failed:
            self.logger.warning(
                "GitLab user sync (unscoped): both list_groups (%s) and "
                "list_projects (%s) failed; relying on creator-only "
                "access (%s) for this run.",
                groups_res.error,
                projects_res.error,
                self.creator_email,
            )
        if projects_res.data:
            projects = projects_res.data
            total = len(projects)
            for i, project in enumerate(projects, start=1):
                try:
                    project_id = getattr(project, "id", None)
                    if project_id is None:
                        self.logger.warning("Project missing ID, skipping ")
                        total_projects_skipped += 1
                        continue
                    self.logger.info(
                        "syncing users for project %s/%s id=%s",
                        i,
                        total,
                        project_id,
                    )
                    members_res = await self._ds_call(
                        self.data_source.list_project_members_all,
                        project_id=project_id,
                        get_all=True,
                    )
                    if not members_res.success:
                        self.logger.error(
                            f"Error in fetching members for project {project_id}"
                        )
                        total_projects_skipped += 1
                        continue
                    members = members_res.data
                    for member in members:
                        dict_member[member.id] = member
                    total_projects_synced += 1
                except Exception as e:
                    self.logger.error(
                        f"Error in syncing users for project : {e}", exc_info=True
                    )
                    continue

        # TODO: for user_groups of gitlab bringing them as groups on our platform
        self.logger.info(
            f"Total groups synced: {total_groups_synced}, Total groups skipped: {total_groups_skipped}"
        )
        self.logger.info(
            f"Total projects synced: {total_projects_synced}, Total projects skipped: {total_projects_skipped}"
        )
        dict_member = await self._enrich_members_with_full_user(dict_member)
        await self._sync_users_from_projects_groups(dict_member)
        self.logger.info("Users sync and migration of pseudo groups complete")

    async def _enrich_members_with_full_user(
        self, dict_member: dict[int, GroupMember]
    ) -> dict[int, Any]:
        """
        Members API does not include ``public_email``; fetch ``GET /users/:id`` per unique user.

        Concurrency is bounded by a ``Semaphore`` sized at
        ``_GITLAB_USER_ENRICHMENT_CONCURRENCY`` (half the dedicated
        ``_gitlab_executor`` pool). Previously this method did
        ``asyncio.gather`` with ``batch_size=20`` on the loop's default
        executor, which would briefly fill ~20 of its ~8 thread slots
        and queue 12+ futures behind any GitLab call already stuck on
        ``recv()`` — that fan-out was the dominant contributor to the
        thread-pool starvation that froze the connector service.
        Bounding to ``_GITLAB_USER_ENRICHMENT_CONCURRENCY`` leaves
        headroom in this connector's pool for picker queries, paged
        sweeps, and other GitLab work to run alongside enrichment
        without queuing.

        Emits an INFO progress line every ``progress_every`` enriched users
        so that on large tenants (5k+ members) the operator can see
        forward motion. Each ``get_user`` call also goes through the
        per-call wall-clock budget installed on ``_ds_call``, so a single
        misbehaving user endpoint cannot anchor the whole sweep.
        """
        progress_every = 200
        total = len(dict_member)
        if total == 0:
            return {}

        sem = asyncio.Semaphore(_GITLAB_USER_ENRICHMENT_CONCURRENCY)

        async def fetch_full_user(member_id: int, member: GroupMember) -> tuple[int, Any]:
            async with sem:
                try:
                    # 60s per-call budget — ``get_user`` is one HTTP round-trip;
                    # if it takes longer the server is stuck and we should
                    # prefer the unenriched member payload over blocking
                    # the rest of the enrichment fan-out.
                    user_res = await self._ds_call(
                        self.data_source.get_user,
                        member_id,
                        _gitlab_timeout=120.0,
                    )
                    if user_res.success and user_res.data:
                        return member_id, user_res.data
                    self.logger.warning(
                        "Could not fetch full GitLab user id=%s (%s); using member payload.",
                        member_id,
                        getattr(user_res, "error", "unknown"),
                    )
                    return member_id, member
                except Exception as e:
                    self.logger.warning(
                        "Exception fetching GitLab user id=%s; using member payload: %s",
                        member_id,
                        e,
                        exc_info=True,
                    )
                    return member_id, member

        enriched: dict[int, Any] = {}
        last_progress_logged = 0
        # ``as_completed`` streams results in finish order so progress
        # logs reflect actual forward motion rather than batch barriers,
        # and the bounded ``Semaphore`` keeps the in-flight fan-out at
        # the configured concurrency regardless of ``total``.
        coros = [
            fetch_full_user(mid, mem) for mid, mem in dict_member.items()
        ]
        for fut in asyncio.as_completed(coros):
            member_id, user_obj = await fut
            enriched[member_id] = user_obj
            done = len(enriched)
            if done - last_progress_logged >= progress_every or done == total:
                self.logger.info(
                    "Enriching GitLab members with full user: %s/%s done",
                    done,
                    total,
                )
                last_progress_logged = done

        self.logger.info("Enriched %s GitLab members with full user objects", len(enriched))
        return enriched

    def _build_creator_member_stub(self) -> Any | None:
        """Return a ``GroupMember``-shaped object for the connector creator.

        Used as a last-resort entry in ``dict_member`` when every upstream
        member listing failed. We avoid constructing a real
        ``gitlab.v4.objects.GroupMember`` because that requires a python-
        gitlab manager and the SDK changes its constructor shape between
        major versions; a duck-typed namespace with the attributes the
        downstream code reads is both safer and easier to test.

        Returns ``None`` when we lack either the GitLab numeric id or the
        creator email — in that state we cannot make the AppUser lookup
        path round-trip cleanly, so the caller should keep the existing
        abort behaviour.
        """
        if not self.creator_email or self._gitlab_user_id is None:
            return None
        from types import SimpleNamespace
        return SimpleNamespace(
            id=self._gitlab_user_id,
            username=(self.creator_email.split("@")[0] or "creator"),
            name=self.creator_email,
            public_email=self.creator_email,
            email=self.creator_email,
            access_level=50,  # OWNER — creator owns the records they sync.
        )

    def _inject_creator_member_into(self, dict_member: dict[int, Any]) -> bool:
        """Ensure the connector creator is present in ``dict_member``.

        GitLab Admin and EE Auditor personas have cross-instance read
        access via a *user-level flag* (``is_admin`` / ``is_auditor``)
        rather than a row in any per-group / per-project members table.
        ``GET /groups/:id/members/all`` and ``GET /projects/:id/members/all``
        therefore omit them, even though they can read every record we
        sync. Without this injection an Auditor who configured the
        connector ends up with no ``AppUser`` row and loses access in
        PipesHub to data they can see in GitLab.

        Uses ``setdefault`` so a real member row already in the dict is
        preserved with its actual ``access_level`` / ``public_email``,
        rather than being clobbered by the OWNER-level stub. Returns
        ``True`` when the creator could be represented (stub built and
        either injected or already present), ``False`` when no creator
        identity was resolved during ``init`` and we cannot fall back.
        """
        creator = self._build_creator_member_stub()
        if creator is None:
            return False
        dict_member.setdefault(creator.id, creator)
        return True

    async def _sync_users_from_projects_groups(
        self, dict_member: dict[int, Any]
    ) -> None:
        """Create AppUsers from projects and groups.

        Special case: the row whose ``id`` matches the connector creator's
        cached GitLab id always becomes an ``AppUser`` using
        ``self.creator_email``, even if its ``public_email`` is empty.
        Without this bypass, when the creator has not enabled
        public-email visibility on their GitLab profile (the GitLab
        default), the configuring user becomes a pseudo-group keyed by
        a numeric id their PipesHub identity never matches, and they
        can't search records they configured the sync to ingest.
        """
        total_users_synced = 0
        total_users_skipped = 0
        app_users: list[AppUser] = []
        for member_id, member in dict_member.items():
            raw_email = getattr(member, "public_email", None)
            if isinstance(raw_email, str) and raw_email.strip():
                user_email = raw_email.strip()
            else:
                fallback = getattr(member, "email", None)
                user_email = fallback.strip() if isinstance(fallback, str) else ""

            # Creator bypass: if this row is the configuring user and
            # GitLab did not surface an email for them, fall back to
            # the PipesHub email captured in ``init()``. The id check
            # is exact-int because GitLab numeric ids are stable; we
            # do not coerce strings here to avoid accidentally
            # promoting an unrelated row.
            if (
                not user_email
                and self._gitlab_user_id is not None
                and member_id == self._gitlab_user_id
                and self.creator_email
            ):
                user_email = self.creator_email
                self.logger.info(
                    "Resolved creator AppUser %r via PipesHub identity "
                    "(public_email not set on GitLab profile, gitlab id=%s).",
                    user_email,
                    member_id,
                )
            if not user_email:
                total_users_skipped += 1
                self.logger.debug(
                    f"Email not found for user {member.username} with id {member_id}, skipping"
                )
            else:
                app_user = AppUser(
                    app_name=self.connector_name,
                    org_id=self.data_entities_processor.org_id,
                    connector_id=self.connector_id,
                    source_user_id=str(member_id),
                    is_active=True,
                    email=user_email,
                    full_name=member.name,
                )
                app_users.append(app_user)
        if app_users:
            await self.data_entities_processor.on_new_app_users(app_users)
            total_users_synced += len(app_users)
            # for appuser migrate previously created pseudo group permissions to app users
            for user in app_users:
                try:
                    await self.data_entities_processor.migrate_group_to_user_by_external_id(
                        group_external_id=user.source_user_id,
                        user_email=user.email,
                        connector_id=self.connector_id,
                    )
                except Exception as e:
                    # Log warning but continue with other users
                    self.logger.warning(
                        f"Failed to migrate pseudo-group permissions for user {user.email}: {e}",
                        exc_info=True,
                    )
                    continue
        self.logger.info(
            f"Total users synced: {total_users_synced}, Total users skipped: {total_users_skipped}"
        )

    @staticmethod
    def _namespace_full_path(project: Project) -> str | None:
        ns = getattr(project, "namespace", None)
        if ns is None:
            return None
        fp = getattr(ns, "full_path", None)
        if isinstance(fp, str):
            return fp
        if isinstance(ns, dict):
            return ns.get("full_path")
        return None

    @staticmethod
    def _namespace_is_group(project: Project) -> bool:
        """False for personal projects (user namespace); True for group namespaces."""
        ns = getattr(project, "namespace", None)
        if ns is None:
            return False
        kind = getattr(ns, "kind", None)
        if isinstance(ns, dict):
            kind = ns.get("kind")
        if kind == "user":
            return False
        return True

    @staticmethod
    def _gitlab_timestamp_to_ms(value: Any) -> int | None:
        """Normalize GitLab commit date strings or datetimes to epoch ms."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return int(value.timestamp() * 1000)
        if isinstance(value, str) and value.strip():
            try:
                return parse_timestamp(value)
            except (ValueError, TypeError):
                return None
        return None

    @staticmethod
    def _should_continue_repo_tree_pagination(
        raw_nodes_fetched: int,
        total_collected: int,
        page_info: dict[str, Any],
    ) -> tuple[bool, str]:
        """Whether to fetch another ``paginatedTree`` page and the next cursor.

        GitLab often keeps ``hasNextPage=true`` after the last tree/blob page,
        returning empty ``nodes`` for many trailing requests. Stop once we
        already have data and a page adds nothing new.
        """
        has_next = bool(page_info.get("hasNextPage"))
        end_cursor = page_info.get("endCursor") or ""
        if not has_next or not end_cursor:
            return False, ""
        if raw_nodes_fetched == 0 and total_collected > 0:
            return False, ""
        return True, end_cursor

    @staticmethod
    def _longest_matching_group_path(
        namespace_path: str | None, group_paths: list[str]
    ) -> str | None:
        if not namespace_path or not group_paths:
            return None
        best: str | None = None
        best_len = -1
        for p in group_paths:
            if namespace_path == p or namespace_path.startswith(p + "/"):
                if len(p) > best_len:
                    best = p
                    best_len = len(p)
        return best

    @staticmethod
    def _namespace_under_any_prefix(
        namespace_path: str | None, prefixes: list[str]
    ) -> bool:
        if not namespace_path:
            return False
        for p in prefixes:
            if namespace_path == p or namespace_path.startswith(p + "/"):
                return True
        return False

    def _datetime_range_from_sync_filter(
        self, key: SyncFilterKey
    ) -> tuple[datetime | None, datetime | None]:
        """UTC (after, before) bounds for GitLab list_* datetime parameters.

        Relies on the storage convention enforced by `Filter` for DATETIME values:
            - IS_AFTER   → (start_ms, None)
            - IS_BEFORE  → (None, end_ms)
            - IS_BETWEEN → (start_ms, end_ms)

        So the operator dispatch already lives inside `get_datetime_start` /
        `get_datetime_end`; we just convert the epochs to UTC datetimes.
        """
        if not self.sync_filters:
            return (None, None)
        f = self.sync_filters.get(key)
        if not f:
            return (None, None)
        start_ms = f.get_datetime_start()
        end_ms = f.get_datetime_end()
        after = (
            datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
            if start_ms is not None
            else None
        )
        before = (
            datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
            if end_ms is not None
            else None
        )
        return (after, before)

    def _comments_indexing_enabled(self) -> bool:
        if not self.indexing_filters:
            return True
        return self.indexing_filters.is_enabled(IndexingFilterKey.COMMENTS)

    def _issues_indexing_enabled(self) -> bool:
        if not self.indexing_filters:
            return True
        return self.indexing_filters.is_enabled(IndexingFilterKey.ISSUES)

    def _merge_requests_indexing_enabled(self) -> bool:
        if not self.indexing_filters:
            return True
        return self.indexing_filters.is_enabled(IndexingFilterKey.MERGE_REQUESTS)

    def _code_files_indexing_enabled(self) -> bool:
        if not self.indexing_filters:
            return True
        return self.indexing_filters.is_enabled(IndexingFilterKey.CODE_FILES)

    async def _code_file_source_timestamps(
        self,
        project_id: int,
        file_path: str,
        ref: str = "HEAD",
    ) -> tuple[int | None, int | None]:
        """Fetch created/updated timestamps from GitLab commit history for one path."""
        if not self.data_source or not file_path:
            return (None, None)
        res = await self._ds_call(
            self.data_source.list_commits_for_path,
            project_id=project_id,
            path=file_path,
            ref_name=ref,
        )
        if not res.success or not res.data:
            return (None, None)
        data = res.data
        created_ms = self._gitlab_timestamp_to_ms(data.get("oldest_committed_date"))
        updated_ms = self._gitlab_timestamp_to_ms(data.get("newest_committed_date"))
        return (created_ms, updated_ms)

    async def _fetch_code_file_timestamps_batch(
        self, project_id: int, paths: list[str]
    ) -> dict[str, tuple[int | None, int | None]]:
        """Fetch commit-history timestamps for many paths (bounded concurrency)."""
        semaphore = asyncio.Semaphore(10)
        results: dict[str, tuple[int | None, int | None]] = {}

        async def fetch_one(path: str) -> None:
            async with semaphore:
                try:
                    results[path] = await self._code_file_source_timestamps(
                        project_id, path
                    )
                except Exception as e:
                    self.logger.warning(
                        "Failed to fetch timestamps for path %s in project %s: %s",
                        path,
                        project_id,
                        e,
                    )
                    results[path] = (None, None)

        await asyncio.gather(*(fetch_one(path) for path in paths))
        return results

    async def _cancel_code_file_timestamp_backfill(self) -> None:
        """Stop the in-flight connector-wide backfill before starting a new sync."""
        task = self._code_file_timestamp_backfill_task
        self._code_file_timestamp_backfill_task = None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _schedule_code_file_timestamp_backfill_after_sync(self) -> None:
        """Fetch null-timestamp code files and backfill in one background task."""
        existing = self._code_file_timestamp_backfill_task
        if existing is not None and not existing.done():
            return

        self._code_file_timestamp_backfill_task = asyncio.create_task(
            self._backfill_code_file_timestamps_after_sync(),
            name=f"gitlab_code_file_ts_backfill_{self.connector_id}",
        )

    async def _backfill_code_file_timestamps_after_sync(self) -> None:
        """Background job: backfill commit-history timestamps for all synced projects."""
        try:
            await self._refresh_token_if_needed()
            projects = await self._resolve_projects_with_filters()
            for project in projects:
                await self._run_code_file_timestamp_backfill(project.id)
        except Exception as e:
            self.logger.error(
                "Code file timestamp backfill failed for connector %s: %s",
                self.connector_id,
                e,
                exc_info=True,
            )
        finally:
            self._code_file_timestamp_backfill_task = None

    async def _run_code_file_timestamp_backfill(self, project_id: int) -> None:
        batch_size = 100

        try:
            await self._refresh_token_if_needed()
            external_group_id = f"{project_id}-code-repository"
            async with self.data_store_provider.transaction() as tx_store:
                nodes = await tx_store.get_nodes_by_filters(
                    collection=CollectionNames.RECORDS.value,
                    filters={
                        "connectorId": self.connector_id,
                        "recordType": RecordType.CODE_FILE.value,
                        "externalGroupId": external_group_id,
                        "sourceCreatedAtTimestamp": None,
                        "sourceLastModifiedTimestamp": None,
                    },
                )

            path_to_records: dict[str, list[CodeFileRecord]] = {}
            for node in nodes:
                if node.get("isDeleted"):
                    continue
                code_file = CodeFileRecord.from_arango_record({}, node)
                file_path = code_file.file_path or self._repo_path_from_blob_web_url(
                    code_file.weburl
                )
                if not file_path:
                    continue
                path_to_records.setdefault(file_path, []).append(code_file)

            file_paths = list(path_to_records.keys())
            for offset in range(0, len(file_paths), batch_size):
                await self._refresh_token_if_needed()
                batch_paths = file_paths[offset : offset + batch_size]

                try:
                    timestamp_by_path = await self._fetch_code_file_timestamps_batch(
                        project_id, batch_paths
                    )
                except Exception as e:
                    self.logger.warning(
                        "Failed to fetch timestamps for connector %s project %s: %s",
                        self.connector_id,
                        project_id,
                        e,
                    )
                    continue

                for file_path in batch_paths:
                    created_ms, updated_ms = timestamp_by_path.get(
                        file_path, (None, None)
                    )
                    if created_ms is None and updated_ms is None:
                        continue

                    for record in path_to_records[file_path]:
                        if (
                            record.source_created_at == created_ms
                            and record.source_updated_at == updated_ms
                        ):
                            continue

                        try:
                            updated_record = record.model_copy(
                                update={
                                    "source_created_at": created_ms,
                                    "source_updated_at": updated_ms,
                                }
                            )
                            await self.data_entities_processor.on_record_metadata_update(
                                updated_record
                            )
                        except Exception as e:
                            self.logger.warning(
                                "Failed to update timestamps for connector %s project %s "
                                "record %s path %s: %s",
                                self.connector_id,
                                project_id,
                                record.id,
                                file_path,
                                e,
                            )

        except Exception as e:
            self.logger.error(
                "Code file timestamp backfill failed for connector %s project %s: %s",
                self.connector_id,
                project_id,
                e,
                exc_info=True,
            )

    async def _group_permissions_from_child_projects(
        self,
        group_path: str,
        candidate_projects: list[Project],
    ) -> list[Permission]:
        """Union child-project members into group-level USER permissions.

        Used as a middle-tier fallback by ``_ensure_gitlab_group_record_groups``
        when ``list_group_members_all`` returns nothing usable. The common
        trigger is a token whose user has direct project access (Reporter+)
        inside the group but no group-level membership — GitLab then
        accepts the per-project members endpoint but denies (or returns
        empty for) the group-members endpoint. The EE Auditor role is the
        same shape: read-only access via role, no membership row.

        We restrict to ``candidate_projects`` (the post-filter project
        set) rather than re-listing every project under the group, so:
        - We respect the operator's ``PROJECT_IDS NOT_IN`` exclusions.
        - We don't pay an extra ``list_group_projects`` call here when
          the caller already computed the project universe upstream.
        - We don't expose users whose access is gated on a project the
          operator chose to exclude.

        Dedup by ``id`` keeping ``max(access_level)``, mirroring the same
        rule ``_sync_project_members_as_pseudo`` uses so a user who is
        Reporter on one project and Maintainer on another lands at
        Maintainer on the group node.
        """
        prefixes = [group_path]
        child_projects = [
            p for p in candidate_projects
            if self._namespace_under_any_prefix(
                self._namespace_full_path(p), prefixes
            )
        ]
        if not child_projects:
            self.logger.debug(
                "child-project union: no candidate projects under group %s; "
                "nothing to derive group permissions from",
                group_path,
            )
            return []

        # uid → member object with the highest access_level seen across
        # the child projects we successfully listed. Tracking the access
        # level explicitly (instead of relying on dict overwrite order)
        # makes the merge deterministic regardless of project iteration
        # order and matches _sync_project_members_as_pseudo's rule.
        member_map: dict[int, Any] = {}
        listed_projects = 0
        failed_projects = 0
        for proj in child_projects:
            # Prefer the numeric id; fall back to path_with_namespace
            # because some tests + a few code paths construct ``Project``
            # without a populated id.
            proj_key = getattr(proj, "id", None) or getattr(
                proj, "path_with_namespace", None
            )
            if proj_key is None:
                continue
            pm_res = await self._ds_call(
                self.data_source.list_project_members_all,
                project_id=proj_key,
                get_all=True,
            )
            if not pm_res.success:
                failed_projects += 1
                self.logger.debug(
                    "child-project union: list_project_members_all(%s) "
                    "failed under group %s: %s",
                    proj_key, group_path, pm_res.error,
                )
                continue
            listed_projects += 1
            for m in pm_res.data or []:
                uid = getattr(m, "id", None)
                if uid is None:
                    continue
                m_level = getattr(m, "access_level", 0) or 0
                if m_level == 0:
                    continue
                existing = member_map.get(uid)
                existing_level = getattr(existing, "access_level", 0) or 0
                if existing is None or m_level > existing_level:
                    member_map[uid] = m

        permissions: list[Permission] = []
        for m in member_map.values():
            permission = await self._transform_restrictions_to_permisions(m)
            if permission:
                permissions.append(permission)

        self.logger.info(
            "child-project union for group %s: listed=%s failed=%s, "
            "%s unique member(s) -> %s permission(s)",
            group_path,
            listed_projects,
            failed_projects,
            len(member_map),
            len(permissions),
        )
        return permissions

    async def _ensure_gitlab_group_record_groups(
        self,
        group_paths: list[str],
        candidate_projects: list[Project] | None = None,
    ) -> None:
        """Create top-level GitLab group record groups before project groups reference them.

        Group members (including inherited members from parent groups) are attached as
        USER permissions on the group RecordGroup so that the knowledge-hub browse view
        (`_get_app_children_subquery` -> `_get_permission_role_aql`) admits the user at
        the group level. Without this, the group node has no PERMISSION edges, the app
        drilldown filters it out, and every project under it becomes unreachable in the
        browse tree even though the project_record_group beneath has its own permissions.

        Fallback chain when the direct group-members listing is unusable
        (403 / timeout / 200 + []):
          1. Union of members across ``candidate_projects`` whose
             namespace sits under ``group_path``. Wins whenever the
             token has project-level access but no group membership
             (the common EE-Auditor and "added directly to a repo"
             cases).
          2. Creator-only permission. Always works but yields the
             narrowest possible ACL; used only when (1) also produces
             nothing so the group node is still reachable for the
             operator who configured the sync.
        """
        if not self.data_source:
            return
        self.logger.info(f"Ensuring GitLab group record groups for {group_paths}")
        for group_path in group_paths:
            group_res = await self._ds_call(
                self.data_source.get_group, group_path
            )
            if not group_res.success or not group_res.data:
                creator_permission = self._creator_user_permission()
                if creator_permission is None:
                    self.logger.error(
                        "GitLab group %s not found/inaccessible (%s) and no "
                        "creator identity to fall back on; child projects "
                        "under this group will be orphaned in the browse "
                        "drilldown.",
                        group_path,
                        group_res.error,
                    )
                    continue
                self.logger.warning(
                    "GitLab group %s not found/inaccessible (%s); creating "
                    "creator-only RecordGroup so child projects stay "
                    "reachable.",
                    group_path,
                    group_res.error,
                )
                group_rg = RecordGroup(
                    org_id=self.data_entities_processor.org_id,
                    name=group_path,
                    group_type=RecordGroupType.PROJECT.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    external_group_id=group_path,
                    web_url=None,
                )
                await self.data_entities_processor.on_new_record_groups(
                    [(group_rg, [creator_permission])]
                )
                continue
            group = group_res.data
            full_path = getattr(group, "full_path", None) or str(
                getattr(group, "id", group_path)
            )

            group_permissions: list[Permission] = []
            members_res = await self._ds_call(
                self.data_source.list_group_members_all,
                group_id=group_path,
                get_all=True,
            )
            if not members_res.success:
                self.logger.warning(
                    "Could not list members for GitLab group %s: %s. "
                    "Will attempt child-project union, then creator-only "
                    "fallback so the group node stays reachable in the "
                    "browse view.",
                    group_path,
                    members_res.error,
                )
            else:
                for member in members_res.data or []:
                    # Mirror _sync_project_members_as_pseudo: any positive access level
                    # grants visibility on the group node. Stricter gating happens on
                    # the child project/code/work-items/MR RecordGroups.
                    if getattr(member, "access_level", 0) == 0:
                        continue
                    permission = await self._transform_restrictions_to_permisions(
                        member
                    )
                    if permission:
                        group_permissions.append(permission)

            # Tier 1 fallback: union members from child projects that
            # survived sync filtering. Covers the EE-Auditor / direct-
            # project-membership cases where the group-members endpoint
            # returned 403 OR ``200 OK + []`` (both indistinguishable
            # here, and both leave ``group_permissions`` empty).
            if not group_permissions and candidate_projects:
                group_permissions = await self._group_permissions_from_child_projects(
                    group_path=group_path,
                    candidate_projects=candidate_projects,
                )

            # Always ensure the connector creator has access to the
            # group node, even when other permissions were derived
            # successfully. GitLab Admin / EE Auditor personas read
            # everything via a user-level flag rather than a membership
            # row, so the listing above and the child-project union
            # below will both systematically omit them. Without this
            # the top-level group disappears from the browse tree for
            # the very persona that just configured the sync. Dedup by
            # email so we don't double-write the row when the creator
            # legitimately appears in the listing.
            creator_permission = self._creator_user_permission()
            if creator_permission is not None and not any(
                getattr(p, "email", None) == creator_permission.email
                for p in group_permissions
            ):
                if not group_permissions and not candidate_projects:
                    # Tier 2 fallback path: both group-members and the
                    # child-project union produced nothing. Logged at
                    # WARNING so operators can correlate the narrow ACL
                    # with the upstream 403 / empty listing.
                    self.logger.warning(
                        "GitLab group %s: group-members and child-project "
                        "union both produced 0 permissions; applying "
                        "creator-only fallback for %s so the group node "
                        "is still reachable in the browse view.",
                        group_path,
                        self.creator_email,
                    )
                group_permissions.append(creator_permission)

            group_rg = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=getattr(group, "name", full_path) or full_path,
                group_type=RecordGroupType.PROJECT.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                external_group_id=full_path,
                web_url=getattr(group, "web_url", None),
            )
            await self.data_entities_processor.on_new_record_groups(
                [(group_rg, group_permissions)]
            )

    async def _resolve_projects_with_filters(self) -> list[Project]:
        """Resolve projects to sync from sync filters (GitLab.com and self-managed).

        Semantics when both ``GROUP_IDS`` and ``PROJECT_IDS`` are set:

        - ``PROJECT_IDS IN`` is authoritative when set: only the listed
          projects sync. ``GROUP_IDS IN`` in this case acts purely as a
          picker-scope helper for the UI (already passed via the
          ``contextGroupPath`` query param on ``/filter-options``); it
          does NOT widen the sync to other projects under the group.
          The earlier "additive" behaviour silently synced every project
          in the listed group on top of the explicit project list, which
          surprised operators who selected a group only to narrow the
          project picker. See the original "PROJECT_IDS short-circuits"
          contract — restored here.
        - ``GROUP_IDS IN`` (without ``PROJECT_IDS IN``): expand to every
          project under each listed group / subgroup hierarchy.
        - ``NOT_IN`` filters are subtractive: from whatever candidate
          set the IN branch produced (or the unscoped fetch if no IN
          filter is set), drop any project whose path is in
          ``PROJECT_IDS NOT_IN`` OR whose namespace is under any
          ``GROUP_IDS NOT_IN`` prefix. NOT_IN composes with IN so a
          stale path explicitly listed under PROJECT_IDS IN can still
          be excluded by a GROUP_IDS NOT_IN rule.

        Also seeds ``self._gitlab_included_group_paths`` so each
        project's ``RecordGroup`` can be linked to a parent group node.
        Without that link the browse-view drilldown filters the project
        out of the tree (see ``_ensure_gitlab_group_record_groups``).
        """
        if not self.data_source:
            raise Exception("GitLab data source not initialized")
        self._gitlab_included_group_paths = None
        sf = self.sync_filters

        grp_f = sf.get(SyncFilterKey.GROUP_IDS) if sf else None
        proj_f = sf.get(SyncFilterKey.PROJECT_IDS) if sf else None
        grp_paths = (
            list(grp_f.value)  # type: ignore[arg-type]
            if (grp_f and not grp_f.is_empty())
            else []
        )
        proj_paths = (
            list(proj_f.value)  # type: ignore[arg-type]
            if (proj_f and not proj_f.is_empty())
            else []
        )
        grp_op = _filter_op_val(grp_f) if grp_paths else None
        proj_op = _filter_op_val(proj_f) if proj_paths else None

        grp_in = grp_paths if grp_op == FilterOperator.IN else []
        grp_not_in = grp_paths if grp_op == FilterOperator.NOT_IN else []
        proj_in = proj_paths if proj_op == FilterOperator.IN else []
        proj_not_in = proj_paths if proj_op == FilterOperator.NOT_IN else []

        by_id: dict[int, Project] = {}

        if proj_in:
            # PROJECT_IDS IN is authoritative: resolve each listed path
            # and skip the group expansion entirely. The group filter,
            # if any, has already done its job as picker scope on the
            # /filter-options endpoint; treating it as a sync widener
            # here would silently sync sibling projects the operator
            # never selected (see ``_resolve_projects_with_filters``
            # docstring).
            for pth in proj_in:
                res = await self._ds_call(
                    self.data_source.get_project, pth
                )
                if not res.success or not res.data:
                    self.logger.error(
                        f"Repository not found or inaccessible: {pth} ({res.error})"
                    )
                    continue
                by_id[int(res.data.id)] = res.data
        elif grp_in:
            # Group-only allow-list: enumerate every project under each
            # configured group / subgroup hierarchy.
            for gp in grp_in:
                # Stream via iterator so a large group with thousands of
                # projects (RingCentral-sized monorepo groups) logs
                # progress per page instead of materialising the full
                # list under one opaque to_thread call.
                gres = await self._paged_list(
                    self.data_source.list_group_projects,
                    gp,
                    include_subgroups=True,
                    progress_label=f"list_group_projects({gp})",
                )
                if not gres.success:
                    self.logger.error(
                        f"Could not list projects for group {gp}: {gres.error}"
                    )
                    continue
                for p in gres.data or []:
                    by_id[int(p.id)] = p
        else:
            # No IN filter: start from every project the caller can see
            # under their current scope, then apply NOT_IN exclusions
            # below. Raise on failure so the next reconciliation pass
            # doesn't tombstone every record.
            #
            # Role-aware listing with auditor-empty fallback. Regular
            # members stay on ``membership=True`` (avoids syncing the
            # entire instance's public/internal projects under their
            # token); admins / EE Auditors get the default "all
            # visible" scope. For auditors, an empty primary result
            # silently retries with ``membership=True`` — see the
            # GitLab known issue documented on
            # ``_list_groups_scope_kwargs``.
            res = await self._paged_list_projects_with_role_fallback(
                pagination="keyset",
                order_by="id",
                sort="asc",
                per_page=100,
                progress_label="list_projects unscoped",
            )
            if not res.success:
                raise Exception("❌❌ Error in fetching projects")
            for p in res.data or []:
                by_id[int(p.id)] = p

        candidates = list(by_id.values())
        if proj_not_in:
            excluded = set(proj_not_in)
            candidates = [
                p
                for p in candidates
                if getattr(p, "path_with_namespace", None) not in excluded
            ]
        if grp_not_in:
            candidates = [
                p
                for p in candidates
                if not self._namespace_under_any_prefix(
                    self._namespace_full_path(p), grp_not_in
                )
            ]

        if not candidates:
            return []

        included_group_paths = await self._build_included_group_hierarchy(
            candidates=candidates,
            grp_in=grp_in,
            grp_not_in=grp_not_in,
            proj_in=proj_in,
        )
        if included_group_paths:
            # Pass the post-filter project list so the group-permissions
            # builder can fall back to a child-project member union when
            # ``list_group_members_all`` returns 403/empty (EE Auditor,
            # direct project membership without group membership, etc.).
            await self._ensure_gitlab_group_record_groups(
                included_group_paths, candidate_projects=candidates
            )
            self._gitlab_included_group_paths = included_group_paths

        return candidates

    async def _build_included_group_hierarchy(
        self,
        *,
        candidates: list[Project],
        grp_in: list[str],
        grp_not_in: list[str],
        proj_in: list[str],
    ) -> list[str]:
        """Compute the namespace paths whose ``RecordGroup`` nodes must
        exist so candidate projects are reachable in the browse view.

        ``GROUP_IDS IN`` paths are emitted verbatim (the operator's
        intent). ``PROJECT_IDS IN`` derives one parent path per project
        namespace — without this each project's ``RecordGroup`` has
        ``parent_external_group_id=None`` and the drilldown hides it.
        ``GROUP_IDS NOT_IN`` (without IN) discovers the surviving
        top-level groups via ``list_groups``; this matches the previous
        behaviour for that branch.
        """
        seen: set[str] = set()
        ordered: list[str] = []

        def _add(path: str | None) -> None:
            if path and path not in seen:
                seen.add(path)
                ordered.append(path)

        for gp in grp_in:
            _add(gp)

        if proj_in:
            for p in candidates:
                # Personal projects (namespace.kind == "user") must not be
                # passed to groups.get — GitLab returns 404 Group Not Found.
                if self._namespace_is_group(p):
                    _add(self._namespace_full_path(p))

        if grp_not_in and not grp_in and not proj_in:
            # Keyset pagination is not supported by GitLab's /groups
            # endpoint for authenticated requests; the iterator at
            # least gets us per-page progress.
            #
            # Role-aware listing with auditor-empty fallback. Regular
            # members get ``min_access_level=10`` (no public/internal
            # leak); admins / EE Auditors get ``all_available=True``
            # so the NOT_IN subtraction has a non-empty starting set
            # to act on. For auditors, an empty primary result silently
            # retries with ``min_access_level=10`` — see the GitLab
            # known issue documented on ``_list_groups_scope_kwargs``.
            groups_res = await self._paged_list_groups_with_role_fallback(
                per_page=100,
                progress_label="list_groups group NOT_IN hierarchy",
            )
            if groups_res.success and groups_res.data:
                excluded_set = set(grp_not_in)
                for g in groups_res.data:
                    gfp = getattr(g, "full_path", None)
                    if (
                        gfp
                        and gfp not in excluded_set
                        and not self._namespace_under_any_prefix(
                            gfp, grp_not_in
                        )
                    ):
                        _add(gfp)

        return ordered

    # ---------------------------Project level Sync-----------------------------------#
    async def _sync_all_project(self) -> None:
        """
        Sync all owned projects.
        """
        # TODO: check api is since is supported modify code acc. as sync point depends
        current_timestamp = get_epoch_timestamp_in_ms()
        gitlab_record_group_sync_key = generate_record_sync_point_key(
            Connectors.GITLAB.value,
            GitlabLiterals.RECORD_GROUP.value,
            GitlabLiterals.GLOBAL.value,
        )
        await self._sync_projects()
        await self.record_sync_point.update_sync_point(
            gitlab_record_group_sync_key,
            {GitlabLiterals.LAST_SYNC_TIME.value: current_timestamp},
        )

    async def _sync_repo_main(
        self, project_id: int, project_path: str, default_branch: str
    ) -> None:
        """Sync code repository using incremental compare when a checkpoint exists."""
        branch_res = await self._ds_call(
            self.data_source.get_branch,
            project_id=project_id,
            branch=default_branch,
        )
        if not branch_res.success or not branch_res.data:
            self.logger.error(
                "Failed to fetch branch %s for project %s (%s): %s",
                default_branch,
                project_id,
                project_path,
                branch_res.error,
            )
            return

        current_sha = self._branch_head_commit_sha(branch_res.data)
        if not current_sha:
            self.logger.error(
                "No commit SHA on branch %s for project %s (%s)",
                default_branch,
                project_id,
                project_path,
            )
            return

        last_sha = await self._get_code_repo_checkpoint(project_id)
        if last_sha is None:
            self.logger.info(
                "No code-repo checkpoint for project %s; running full sync",
                project_id,
            )
            await self._sync_repo_full(project_id, project_path)
            await self._update_code_repo_checkpoint(project_id, current_sha)
            return

        if last_sha == current_sha:
            self.logger.info(
                "Code repo unchanged for project %s (HEAD %s); skipping",
                project_id,
                current_sha[:8],
            )
            return

        incremental_ok = await self._sync_repo_incremental(
            project_id, project_path, last_sha, current_sha
        )
        if incremental_ok:
            await self._update_code_repo_checkpoint(project_id, current_sha)
            return

        self.logger.warning(
            "Incremental code sync failed for project %s; falling back to full sync",
            project_id,
        )
        await self._sync_repo_full(project_id, project_path)
        await self._update_code_repo_checkpoint(project_id, current_sha)

    async def _sync_repo_full(self, project_id: int, project_path: str) -> None:
        """Full sync of default-branch folders and blobs via paginated GraphQL."""
        # fetching file tree
        tree_list = []
        after_cursor = ""
        while True:
            try:
                tree_res = await self._ds_call_async(
                    self.data_source.get_repo_tree_g,
                    project_id=project_path,
                    ref="HEAD",
                    after_cursor=after_cursor,
                )
            except Exception as e:
                self.logger.error(
                    f"Error in fetching tree skipping repo code files sync for {project_id}: {e}"
                )
                return
            if not tree_res.data:
                self.logger.info(f"No tree found for project {project_id}")
                return
            data: dict[str, Any] = json.loads(tree_res.data)
            # GitLab's GraphQL returns ``repository: null`` (and sometimes
            # ``project: null``) for empty/wiki-only projects, archived
            # projects without a default branch, or when the token lacks
            # ``read_repository`` scope. ``dict.get(k, {})`` only handles
            # missing keys — it returns ``None`` when the key exists with
            # a null value — so coalesce with ``or {}`` at every step.
            project = (data.get("data") or {}).get("project") or {}
            repository = project.get("repository") or {}
            paginated_tree = repository.get("paginatedTree") or {}
            if not paginated_tree:
                self.logger.info(
                    f"No repository tree for project {project_id} "
                    f"(empty repo, missing scope, or archived); skipping code sync"
                )
                return
            project_nodes = paginated_tree.get("nodes") or []
            page_info = paginated_tree.get("pageInfo") or {}
            if not project_nodes:
                if tree_list:
                    break
                self.logger.info(f"No project nodes found for project {project_id}")
                return
            t_nodes: dict[str, Any] = project_nodes[0]
            file_path_nodes: list[dict[str, Any]] = (
                (t_nodes.get("trees") or {}).get("nodes") or []
            )
            tree_list.extend(file_path_nodes)
            self.logger.debug(
                f"❗❗appended {len(file_path_nodes)} file path nodes via GQL"
            )
            continue_paging, after_cursor = self._should_continue_repo_tree_pagination(
                len(file_path_nodes), len(tree_list), page_info
            )
            if not continue_paging:
                if page_info.get("hasNextPage"):
                    self.logger.debug(
                        "Stopping folder pagination early: empty page received after data was collected"
                    )
                break

        # Group trees by path depth so we process top-down. This keeps
        # parents in DB before children, which lets _handle_parent_record
        # bind the PARENT_CHILD edge in a single pass instead of creating
        # placeholders that get patched later.
        external_group_id = f"{project_id}-code-repository"
        level_wise_files: dict[int, list[dict[str, Any]]] = {}
        for item in tree_list:
            if item.get("type") != "tree":
                continue
            level_wise_files.setdefault(
                (item.get("path") or "").count("/"), []
            ).append(item)

        for _level, files in sorted(level_wise_files.items()):
            list_records_new: list[RecordUpdate] = []
            for file in files:
                file_path = file.get("path") or ""
                file_name = file.get("name")
                file_hash = file.get("sha")
                external_record_id = file.get("webPath")
                weburl = file.get("webUrl")
                if not external_record_id or not file_name:
                    self.logger.warning(
                        f"⚠️ Skipping tree {file_path}: missing webPath/name "
                        f"in GitLab response"
                    )
                    continue
                # Derive parent's externalRecordId directly from the child's
                # webPath. The webPath shape is
                # ``/<group>/<project>/-/tree/<ref>/<path>`` and every
                # connector-saved tree uses the same shape, so chopping the
                # trailing ``/<name>`` yields the parent's externalRecordId
                # exactly. This replaces an AQL graph traversal that walked
                # by `recordName` only and could return the wrong vertex
                # when the same folder name appears under multiple parents
                # (e.g. `src/libs` vs `tests/libs`).
                parent_external_record_id = (
                    external_record_id.rpartition("/")[0]
                    if "/" in file_path
                    else None
                )
                tree_record = FileRecord(
                    # processor reuses the existing id when it finds a match
                    # by (connector_id, external_record_id); the UUID here is
                    # only used when this is genuinely a new record.
                    id=str(uuid.uuid4()),
                    org_id=self.data_entities_processor.org_id,
                    record_name=str(file_name),
                    record_type=RecordType.FILE.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    external_record_id=external_record_id,
                    version=0,
                    origin=OriginTypes.CONNECTOR.value,
                    record_group_type=RecordGroupType.PROJECT.value,
                    external_record_group_id=external_group_id,
                    mime_type=MimeTypes.FOLDER.value,
                    external_revision_id=str(file_hash),
                    preview_renderable=False,
                    parent_external_record_id=parent_external_record_id,
                    # Required for _handle_parent_record to materialize a
                    # placeholder folder if the parent isn't yet in DB
                    # (e.g. parent batch failed, or a child was synced
                    # before its parent). The next pass over the real
                    # parent collapses the placeholder via the upsert on
                    # (connector_id, external_record_id).
                    parent_record_type=(
                        RecordType.FILE if parent_external_record_id else None
                    ),
                    is_file=False,
                    inherit_permissions=True,
                    weburl=weburl,
                )
                record_update = RecordUpdate(
                    record=tree_record,
                    is_new=True,
                    is_updated=False,
                    is_deleted=False,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=False,
                    external_record_id=str(external_record_id),
                    new_permissions=[],
                    old_permissions=[],
                )
                list_records_new.append(record_update)
            if list_records_new:
                await self._process_new_records(list_records_new)
                self.logger.debug(
                    f"❗❗After processing new records {len(list_records_new)} records"
                )

        # fetching code files
        # processing as when recieved, as parent folders exist
        #
        # Depth-1 pipeline: GitLab's ``paginatedTree`` is cursor-based, so
        # page N+1 can't be fetched until page N hands back its ``endCursor``.
        # But the network fetch (read-only GraphQL) is independent of the DB
        # write side (``build_code_file_records``). So as soon as page N
        # returns we kick off the fetch for page N+1 as a task and process
        # page N concurrently — the next GraphQL round-trip overlaps the
        # current page's writes. We never run more than one fetch ahead, so
        # the cursor dependency is respected and pages are still processed
        # strictly in order (parent-folder ordering is preserved).
        after_cursor = ""
        blobs_processed = 0
        pending: asyncio.Task[tuple[str, list[dict[str, Any]], dict[str, Any]]] | None = (
            asyncio.create_task(
                self._fetch_blob_page(project_path, project_id, after_cursor)
            )
        )
        try:
            while pending is not None:
                kind, file_path_nodes, page_info = await pending
                pending = None
                if kind == "abort":
                    return
                if kind == "no_nodes":
                    if blobs_processed == 0:
                        self.logger.info(
                            f"No project nodes found for project {project_id}"
                        )
                    break
                # Decide on (and launch) the next fetch BEFORE processing this
                # page, so the network round-trip overlaps the DB writes below.
                # Pass the running total *including* this page to match the
                # original "increment then check" ordering.
                continue_paging, after_cursor = (
                    self._should_continue_repo_tree_pagination(
                        len(file_path_nodes),
                        blobs_processed + len(file_path_nodes),
                        page_info,
                    )
                )
                if continue_paging:
                    pending = asyncio.create_task(
                        self._fetch_blob_page(
                            project_path, project_id, after_cursor
                        )
                    )
                if file_path_nodes:
                    self.logger.debug(
                        f"❗❗ Files fetched via GQL: {len(file_path_nodes)} "
                    )
                    await self.build_code_file_records(
                        file_path_nodes, project_id, project_path
                    )
                    blobs_processed += len(file_path_nodes)
                if not continue_paging:
                    if not page_info.get("hasNextPage"):
                        self.logger.debug(
                            "✅✅ No more code file pages left, exiting"
                        )
                    else:
                        self.logger.debug(
                            "Stopping code file pagination early: empty page "
                            "received after data was collected"
                        )
        finally:
            # Defensive: if we bailed out (abort/break) with a prefetch still
            # in flight, cancel it so it doesn't dangle as an orphan task.
            if pending is not None and not pending.done():
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, Exception):
                    pass

    async def _fetch_blob_page(
        self, project_path: str, project_id: int, after_cursor: str
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """Fetch and parse a single ``paginatedTree`` blob page.

        Returns ``(kind, file_path_nodes, page_info)`` where ``kind`` is one of:

        * ``"data"``     — a usable page; ``file_path_nodes`` may still be the
          empty list for a trailing/empty page (the caller's pagination guard
          decides whether to stop).
        * ``"no_nodes"`` — ``paginatedTree.nodes`` was empty.
        * ``"abort"``    — fatal (network/JSON/GraphQL error, missing repo,
          missing scope, archived); the caller stops the whole code sync.

        This method never raises — failures are logged and mapped to
        ``"abort"`` — because it runs as a prefetch task whose result is
        awaited one iteration later; an escaping exception would otherwise
        surface detached from its context.
        """
        empty: list[dict[str, Any]] = []
        try:
            tree_res = await self._ds_call_async(
                self.data_source.get_file_tree_g,
                project_id=project_path,
                ref="HEAD",
                after_cursor=after_cursor,
            )
        except Exception as e:
            self.logger.error(
                f"Error in fetching file tree skipping repo code files sync "
                f"for {project_id}: {e}"
            )
            return "abort", empty, {}
        if not tree_res.success:
            self.logger.error(
                f"❌❌ Error in fetching file tree skipping repo code files "
                f"sync for {project_id}: {tree_res.error}"
            )
            return "abort", empty, {}
        if not tree_res.data:
            self.logger.info(f"❌❌ No file tree found for project {project_id}")
            return "abort", empty, {}
        try:
            data: dict[str, Any] = json.loads(tree_res.data)
        except json.JSONDecodeError as e:
            self.logger.error(
                f"❌ Failed to parse file tree JSON for {project_id}: {e}"
            )
            return "abort", empty, {}

        # Surface GraphQL errors so they don't get masked as "empty repo"
        if "errors" in data:
            self.logger.error(
                f"🚨 GraphQL errors for project {project_id}: "
                f"{json.dumps(data['errors'])}"
            )
            return "abort", empty, {}

        # Same null-coalescing rationale as ``_sync_repo_main``: GitLab
        # returns ``repository: null`` (and sometimes ``project: null``)
        # for empty/wiki-only projects or when the token lacks
        # ``read_repository`` scope; ``dict.get(k, {})`` doesn't handle
        # explicit ``None`` values, so coalesce with ``or {}``.
        project = (data.get("data") or {}).get("project") or {}
        repository = project.get("repository") or {}
        paginated_tree = repository.get("paginatedTree") or {}
        if not paginated_tree:
            self.logger.info(
                f"No repository tree for project {project_id} "
                f"(empty repo, missing scope, or archived); skipping code "
                f"files sync"
            )
            return "abort", empty, {}
        project_nodes = paginated_tree.get("nodes") or []
        page_info = paginated_tree.get("pageInfo") or {}
        if not project_nodes:
            return "no_nodes", empty, page_info
        t_nodes: dict[str, Any] = project_nodes[0]
        file_path_nodes: list[dict[str, Any]] = (
            (t_nodes.get("blobs") or {}).get("nodes") or []
        )
        return "data", file_path_nodes, page_info

    @staticmethod
    def _code_blob_web_path(
        project_path: str, repo_path: str, ref: str = "HEAD"
    ) -> str:
        return f"/{project_path}/-/blob/{ref}/{repo_path}"

    @staticmethod
    def _code_tree_web_path(
        project_path: str, repo_path: str, ref: str = "HEAD"
    ) -> str:
        return f"/{project_path}/-/tree/{ref}/{repo_path}"

    @staticmethod
    def _should_skip_dotfile_repo_path(repo_path: str) -> bool:
        basename = repo_path.rsplit("/", 1)[-1]
        return basename.startswith(".")

    @staticmethod
    def _compare_diff_entry(diff: Any, key: str) -> Any:
        if isinstance(diff, dict):
            return diff.get(key)
        return getattr(diff, key, None)

    def _classify_compare_diffs(
        self, diffs: list[Any]
    ) -> tuple[list[str], list[str], list[str], list[tuple[str, str]]]:
        """Classify compare-API diffs into four operation lists.

        Returns ``(deletes, adds, modifies, renames)`` where:

        * ``deletes``  — repo-relative paths of blobs that were removed.
        * ``adds``     — repo-relative paths of blobs that are new.
        * ``modifies`` — repo-relative paths of blobs whose content changed
                         in place (same path, no rename flag).
        * ``renames``  — list of ``(old_path, new_path)`` pairs where GitLab
                         reported ``renamed_file=true``.

        Dotfile-normalisation rules (applied here before any callers need to
        think about them):

        * If the **new** path of a rename is a dotfile (skipped) the rename is
          degraded to a plain delete of ``old_path``.
        * If the **old** path of a rename is a dotfile we never stored the
          source record, so the rename degrades to a plain add of ``new_path``.
        * Plain add/modify/delete of a dotfile path is silently dropped.

        All output lists are deduplicated in the order first seen.
        """
        deletes: list[str] = []
        adds: list[str] = []
        modifies: list[str] = []
        renames: list[tuple[str, str]] = []

        seen_delete: set[str] = set()
        seen_add: set[str] = set()
        seen_modify: set[str] = set()
        seen_rename_old: set[str] = set()

        def _add_delete(path: str) -> None:
            if path and path not in seen_delete and not self._should_skip_dotfile_repo_path(path):
                deletes.append(path)
                seen_delete.add(path)

        def _add_add(path: str) -> None:
            if path and path not in seen_add and not self._should_skip_dotfile_repo_path(path):
                adds.append(path)
                seen_add.add(path)

        def _add_modify(path: str) -> None:
            if path and path not in seen_modify and not self._should_skip_dotfile_repo_path(path):
                modifies.append(path)
                seen_modify.add(path)
        self.logger.debug("diffs: %s", diffs)
        for diff in diffs:
            old_path = self._compare_diff_entry(diff, "old_path") or ""
            new_path = self._compare_diff_entry(diff, "new_path") or ""
            is_deleted = bool(self._compare_diff_entry(diff, "deleted_file"))
            is_renamed = bool(self._compare_diff_entry(diff, "renamed_file"))
            is_new = bool(self._compare_diff_entry(diff, "new_file"))

            if is_deleted:
                _add_delete(old_path)
                continue

            if is_renamed:
                effective_old = old_path
                effective_new = new_path or old_path

                # Rename to a dotfile target → only delete the old record.
                if self._should_skip_dotfile_repo_path(effective_new):
                    _add_delete(effective_old)
                    continue

                # Rename from a dotfile source → we never stored it, treat as add.
                if self._should_skip_dotfile_repo_path(effective_old):
                    _add_add(effective_new)
                    continue

                if effective_old and effective_old not in seen_rename_old:
                    renames.append((effective_old, effective_new))
                    seen_rename_old.add(effective_old)
                continue

            # Plain add or modify.
            target = new_path or old_path
            if is_new:
                _add_add(target)
            else:
                _add_modify(target)

        return deletes, adds, modifies, renames

    async def _resolve_blob_sha_by_path(
        self,
        project_id: int,
        repo_paths: list[str],
    ) -> dict[str, str]:
        """Fetch blob SHAs for a list of repo-relative paths via list_repo_tree.

        Returns a mapping ``{repo_path: blob_sha}``.  Paths not found in the
        tree (deleted, wrong type, API error) are omitted silently.
        """
        by_parent: dict[str | None, list[str]] = {}
        for repo_path in repo_paths:
            parent = repo_path.rpartition("/")[0] if "/" in repo_path else None
            by_parent.setdefault(parent, []).append(repo_path)

        sha_map: dict[str, str] = {}
        for parent, child_paths in by_parent.items():
            tree_res = await self._ds_call(
                self.data_source.list_repo_tree,
                project_id=project_id,
                ref="HEAD",
                path=parent,
                recursive=False,
            )
            if not tree_res.success or not tree_res.data:
                continue
            for entry in tree_res.data:
                entry_path = (
                    entry.get("path") if isinstance(entry, dict) else getattr(entry, "path", None)
                )
                entry_type = (
                    entry.get("type") if isinstance(entry, dict) else getattr(entry, "type", None)
                )
                blob_sha = (
                    entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
                )
                if entry_path and entry_type == "blob" and blob_sha and str(entry_path) in child_paths:
                    sha_map[str(entry_path)] = str(blob_sha)
        return sha_map

    async def _reconcile_sha_moves(
        self,
        project_id: int,
        project_path: str,
        deletes: list[str],
        adds: list[str],
    ) -> tuple[list[str], list[str], list[tuple[str, str]]]:
        """Promote delete+add pairs sharing the same blob SHA into renames.

        GitLab only sets ``renamed_file`` when content similarity exceeds its
        threshold.  An identical-content move (copy then delete) shows up as a
        plain ``deleted_file`` + ``new_file`` pair.  We detect this case by
        comparing the SHA stored on the deleted record with the SHA of the new
        blob from the tree API, and promote matching pairs to in-place renames.

        Returns ``(remaining_deletes, remaining_adds, extra_renames)`` where
        the promoted paths are removed from the delete/add lists.
        """
        if not deletes or not adds:
            return deletes, adds, []

        # Resolve SHAs of the added paths from the current tree.
        added_sha_map = await self._resolve_blob_sha_by_path(project_id, adds)

        # Build a reverse map: SHA → new_path (first match wins for uniqueness).
        sha_to_new_path: dict[str, str] = {}
        for new_path, sha in added_sha_map.items():
            if sha not in sha_to_new_path:
                sha_to_new_path[sha] = new_path

        # Look up the blob SHAs that the deleted records currently have stored.
        extra_renames: list[tuple[str, str]] = []
        promoted_deletes: set[str] = set()
        promoted_adds: set[str] = set()

        for old_path in deletes:
            old_external_id = self._code_blob_web_path(project_path, old_path)
            existing = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, old_external_id
            )
            if existing is None:
                continue
            stored_sha = getattr(existing, "external_revision_id", None) or ""
            if not stored_sha:
                continue
            new_path = sha_to_new_path.get(stored_sha)
            if new_path is None or new_path in promoted_adds:
                continue
            # Same blob SHA → treat as a move/rename.
            extra_renames.append((old_path, new_path))
            promoted_deletes.add(old_path)
            promoted_adds.add(new_path)

        remaining_deletes = [p for p in deletes if p not in promoted_deletes]
        remaining_adds = [p for p in adds if p not in promoted_adds]
        return remaining_deletes, remaining_adds, extra_renames

    async def _apply_code_renames(
        self,
        project_id: int,
        project_path: str,
        renames: list[tuple[str, str]],
    ) -> None:
        """Apply in-place rename / move for a list of ``(old_path, new_path)`` pairs.

        For each pair:
        1. Ensures destination folder records exist so the new parent edge resolves.
        2. Fetches the current blob metadata from the tree API.
        3. Builds a ``CodeFileRecord`` with the new path/name/SHA.
        4. Delegates to ``on_records_moved`` which handles id reuse, edge swap,
           and conditional re-indexing.
        """
        if not renames:
            return

        new_paths = [new_p for _, new_p in renames]
        await self._ensure_folder_records_for_paths(project_id, project_path, new_paths)

        # Single list_repo_tree pass per parent directory to collect both
        # blob SHA and file name for all rename targets.
        by_parent: dict[str | None, list[str]] = {}
        for repo_path in new_paths:
            parent = repo_path.rpartition("/")[0] if "/" in repo_path else None
            by_parent.setdefault(parent, []).append(repo_path)

        new_sha_map: dict[str, str] = {}
        name_map: dict[str, str] = {}
        for parent, child_paths in by_parent.items():
            tree_res = await self._ds_call(
                self.data_source.list_repo_tree,
                project_id=project_id,
                ref="HEAD",
                path=parent,
                recursive=False,
            )
            if not tree_res.success or not tree_res.data:
                continue
            for entry in tree_res.data:
                entry_path = (
                    entry.get("path") if isinstance(entry, dict) else getattr(entry, "path", None)
                )
                entry_type = (
                    entry.get("type") if isinstance(entry, dict) else getattr(entry, "type", None)
                )
                entry_name = (
                    entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
                )
                blob_sha_e = (
                    entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
                )
                if entry_path and entry_type == "blob" and str(entry_path) in child_paths:
                    if blob_sha_e:
                        new_sha_map[str(entry_path)] = str(blob_sha_e)
                    if entry_name:
                        name_map[str(entry_path)] = str(entry_name)

        external_group_id = f"{project_id}-code-repository"
        code_files_enabled = self._code_files_indexing_enabled()
        current_timestamp = get_epoch_timestamp_in_ms()

        moves: list[tuple[str, Any, list[Any]]] = []
        for old_path, new_path in renames:
            blob_sha = new_sha_map.get(new_path)
            file_name = name_map.get(new_path)

            if not file_name:
                # Can't resolve new metadata → fall back: add the new path as a
                # normal upsert and delete the old one.
                self.logger.warning(
                    "Could not resolve name for rename target %r in project %s; "
                    "falling back to delete+add",
                    new_path,
                    project_id,
                )
                await self._delete_code_files_by_paths(project_id, project_path, [old_path])
                await self._upsert_code_files_by_paths(project_id, project_path, [new_path])
                continue

            # Skip dotfile targets (classifier should have already handled this,
            # but guard defensively for callers of _apply_code_renames directly).
            if self._should_skip_dotfile_repo_path(new_path):
                await self._delete_code_files_by_paths(project_id, project_path, [old_path])
                continue

            file_extension = file_name.split(".")[-1]
            file_mime = getattr(MimeTypes, file_extension.upper(), MimeTypes.PLAIN_TEXT).value
            preview_renderable = file_extension.lower() in PREVIEW_RENDERABLE_EXTENSIONS

            web_path = self._code_blob_web_path(project_path, new_path)
            weburl = f"{self._gitlab_base_url}{web_path}"

            parent_external_record_id: str | None
            if "/" in new_path:
                parent_blob_path = web_path.rpartition("/")[0]
                parent_external_record_id = parent_blob_path.replace("/-/blob/", "/-/tree/", 1)
            else:
                parent_external_record_id = None

            new_record = CodeFileRecord(
                id=str(uuid.uuid4()),
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.CODE_FILE.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                external_record_id=web_path,
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                record_group_type=RecordGroupType.PROJECT.value,
                external_record_group_id=external_group_id,
                mime_type=file_mime,
                external_revision_id=str(blob_sha) if blob_sha else "",
                preview_renderable=preview_renderable,
                file_path=new_path,
                file_hash=blob_sha,
                inherit_permissions=True,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=(RecordType.FILE if parent_external_record_id else None),
                weburl=weburl,
                source_created_at=current_timestamp,
                source_updated_at=current_timestamp,
            )
            if not code_files_enabled:
                new_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            old_external_id = self._code_blob_web_path(project_path, old_path)
            moves.append((old_external_id, new_record, []))

        if moves:
            await self.data_entities_processor.on_records_moved(moves)

    async def _sync_repo_incremental(
        self,
        project_id: int,
        project_path: str,
        from_sha: str,
        to_sha: str,
    ) -> bool:
        """Apply repository/compare diffs between two commits. Returns False to fall back.

        Uses ``straight=True`` on the compare API so diffs reflect the direct
        two-commit tree change rather than a merge-base diff, which is correct
        for normal pushes and also handles force-push / rebase correctly.

        Operation ordering:
        1. Deletes  — remove records that no longer exist.
        2. Renames  — in-place update (id preserved, parent edge swapped).
        3. Adds/modifies — upsert new / changed blobs.

        This ordering prevents a rename from accidentally matching a freshly
        upserted record with the same path.
        """
        compare_res = await self._ds_call(
            self.data_source.compare_commits,
            project_id=project_id,
            from_sha=from_sha,
            to_sha=to_sha,
            straight=True,
        )
        if not compare_res.success or compare_res.data is None:
            self.logger.warning(
                "compare_commits failed for project %s: %s",
                project_id,
                compare_res.error,
            )
            return False

        compare_data = compare_res.data
        if isinstance(compare_data, dict):
            diffs = compare_data.get("diffs") or []
        else:
            diffs = getattr(compare_data, "diffs", None) or []

        if len(diffs) >= GITLAB_COMPARE_DIFF_LIMIT:
            self.logger.warning(
                "Too many diffs (%s) for project %s; falling back to full sync",
                len(diffs),
                project_id,
            )
            return False

        deletes, adds, modifies, renames = self._classify_compare_diffs(diffs)

        self.logger.info(
            "Incremental code sync for project %s: %s deletes, %s adds, "
            "%s modifies, %s renames (before SHA reconcile)",
            project_id,
            len(deletes),
            len(adds),
            len(modifies),
            len(renames),
        )
        self.logger.debug("deletes=%s adds=%s modifies=%s renames=%s", deletes, adds, modifies, renames)

        # Content-SHA reconcile: promote identical-content delete+add pairs to
        # in-place renames (catches moves GitLab didn't flag as renamed_file).
        deletes, adds, extra_renames = await self._reconcile_sha_moves(
            project_id, project_path, deletes, adds
        )
        renames = renames + extra_renames

        if extra_renames:
            self.logger.info(
                "SHA reconcile promoted %s delete+add pair(s) to rename for project %s",
                len(extra_renames),
                project_id,
            )

        self.logger.info(
            "Incremental code sync for project %s (final): %s deletes, %s adds, "
            "%s modifies, %s renames",
            project_id,
            len(deletes),
            len(adds),
            len(modifies),
            len(renames),
        )

        # Apply in order: deletes first, then renames, then adds/modifies.
        if deletes:
            await self._delete_code_files_by_paths(project_id, project_path, deletes)

        if renames:
            await self._apply_code_renames(project_id, project_path, renames)

        upsert_paths = list(dict.fromkeys(adds + modifies))
        if upsert_paths:
            await self._upsert_code_files_by_paths(project_id, project_path, upsert_paths)

        # Folder cleanup: after all file operations are applied, delete folder
        # records that are now empty (cascade up to emptied ancestors).
        removed_paths = deletes + [old_p for old_p, _ in renames]
        if removed_paths:
            await self._cleanup_emptied_folders(project_id, project_path, removed_paths)

        return True

    async def _cleanup_emptied_folders(
        self, project_id: int, project_path: str, removed_paths: list[str]
    ) -> None:
        """Delete folder records that became empty after file deletes / renames.

        Git emits no directory diffs, so directory delete / rename / move is
        inferred from file-level diffs.  After all file operations are applied
        we look at every ancestor directory of any removed/renamed file path
        and delete the corresponding folder record if it now has no children in
        the DB.  Processing deepest-first gives the cascade for free: deleting
        ``a/sub`` lets ``a`` appear childless on the next iteration.

        Args:
            removed_paths: repo-relative file paths that were deleted or whose
                           old path was vacated by a rename / move.
        """
        # Build the set of candidate directory prefixes.
        candidate_dirs: set[str] = set()
        for repo_path in removed_paths:
            parts = repo_path.split("/")
            for i in range(1, len(parts)):
                candidate_dirs.add("/".join(parts[:i]))

        if not candidate_dirs:
            return

        # Process deepest directories first so parent folders see their
        # children already removed when it is their turn to be evaluated.
        sorted_dirs = sorted(candidate_dirs, key=lambda p: p.count("/"), reverse=True)

        deleted_count = 0
        for dir_prefix in sorted_dirs:
            tree_external_id = self._code_tree_web_path(project_path, dir_prefix)

            folder = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, tree_external_id
            )
            if folder is None:
                # Folder was never stored (dotfile dir, etc.) or already deleted.
                continue

            children = await self.data_entities_processor.get_records_by_parent(
                self.connector_id, tree_external_id
            )
            if children:
                # Partial move or untouched files still live here — keep the folder.
                self.logger.debug(
                    "Folder %r still has %d child(ren) in project %s; keeping",
                    dir_prefix,
                    len(children),
                    project_id,
                )
                continue

            self.logger.info(
                "Deleting emptied folder record %r (id=%s) for project %s",
                dir_prefix,
                folder.id,
                project_id,
            )
            await self.data_entities_processor.on_folder_deleted(folder.id)
            deleted_count += 1

        if deleted_count:
            self.logger.info(
                "Folder cleanup removed %d emptied folder record(s) for project %s",
                deleted_count,
                project_id,
            )

    async def _delete_code_files_by_paths(
        self, project_id: int, project_path: str, paths: list[str]
    ) -> None:
        for repo_path in paths:
            external_id = self._code_blob_web_path(project_path, repo_path)
            record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, external_id
            )
            if record:
                await self.data_entities_processor.on_record_deleted(record.id)

    async def _upsert_code_files_by_paths(
        self, project_id: int, project_path: str, paths: list[str]
    ) -> None:
        unique_paths = list(dict.fromkeys(paths))
        if not unique_paths:
            return

        await self._ensure_folder_records_for_paths(
            project_id, project_path, unique_paths
        )

        nodes: list[dict[str, Any]] = []
        by_parent: dict[str | None, list[str]] = {}
        for repo_path in unique_paths:
            if "/" in repo_path:
                parent = repo_path.rpartition("/")[0]
            else:
                parent = None
            by_parent.setdefault(parent, []).append(repo_path)

        for parent, child_paths in by_parent.items():
            tree_res = await self._ds_call(
                self.data_source.list_repo_tree,
                project_id=project_id,
                ref="HEAD",
                path=parent,
                recursive=False,
            )
            if not tree_res.success or not tree_res.data:
                self.logger.warning(
                    "list_repo_tree failed for project %s path %r: %s",
                    project_id,
                    parent,
                    tree_res.error,
                )
                continue

            entries_by_path: dict[str, Any] = {}
            for entry in tree_res.data:
                entry_path = (
                    entry.get("path")
                    if isinstance(entry, dict)
                    else getattr(entry, "path", None)
                )
                if entry_path:
                    entries_by_path[str(entry_path)] = entry

            for repo_path in child_paths:
                entry = entries_by_path.get(repo_path)
                if entry is None:
                    self.logger.warning(
                        "Blob %r not found under parent %r for project %s",
                        repo_path,
                        parent,
                        project_id,
                    )
                    continue
                entry_type = (
                    entry.get("type")
                    if isinstance(entry, dict)
                    else getattr(entry, "type", None)
                )
                if entry_type != "blob":
                    continue
                blob_sha = (
                    entry.get("id")
                    if isinstance(entry, dict)
                    else getattr(entry, "id", None)
                )
                name = (
                    entry.get("name")
                    if isinstance(entry, dict)
                    else getattr(entry, "name", None)
                )
                if not blob_sha or not name:
                    continue
                web_path = self._code_blob_web_path(project_path, repo_path)
                nodes.append(
                    {
                        "path": repo_path,
                        "name": name,
                        "sha": str(blob_sha),
                        "type": "blob",
                        "webPath": web_path,
                        "webUrl": f"{self._gitlab_base_url}{web_path}",
                    }
                )

        if nodes:
            await self.build_code_file_records(nodes, project_id, project_path)

    async def _ensure_folder_records_for_paths(
        self, project_id: int, project_path: str, file_paths: list[str]
    ) -> None:
        """Create missing folder records for parent directories of changed files."""
        prefixes: set[str] = set()
        for repo_path in file_paths:
            parts = repo_path.split("/")
            for i in range(1, len(parts)):
                prefixes.add("/".join(parts[:i]))

        if not prefixes:
            return

        external_group_id = f"{project_id}-code-repository"
        sorted_prefixes = sorted(prefixes, key=lambda p: p.count("/"))

        for prefix in sorted_prefixes:
            tree_external_id = self._code_tree_web_path(project_path, prefix)
            existing = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, tree_external_id
            )
            if existing:
                continue

            parent_prefix: str | None
            if "/" in prefix:
                parent_prefix = prefix.rpartition("/")[0]
            else:
                parent_prefix = None

            tree_res = await self._ds_call(
                self.data_source.list_repo_tree,
                project_id=project_id,
                ref="HEAD",
                path=parent_prefix,
                recursive=False,
            )
            if not tree_res.success or not tree_res.data:
                self.logger.warning(
                    "Could not list repo tree for folder %r in project %s",
                    prefix,
                    project_id,
                )
                continue

            folder_entry = None
            for entry in tree_res.data:
                entry_path = (
                    entry.get("path")
                    if isinstance(entry, dict)
                    else getattr(entry, "path", None)
                )
                if entry_path != prefix:
                    continue
                entry_type = (
                    entry.get("type")
                    if isinstance(entry, dict)
                    else getattr(entry, "type", None)
                )
                if entry_type == "tree":
                    folder_entry = entry
                    break

            if folder_entry is None:
                continue

            folder_name = (
                folder_entry.get("name")
                if isinstance(folder_entry, dict)
                else getattr(folder_entry, "name", None)
            )
            folder_hash = (
                folder_entry.get("id")
                if isinstance(folder_entry, dict)
                else getattr(folder_entry, "id", None)
            )
            if not folder_name:
                continue

            web_path = self._code_tree_web_path(project_path, prefix)
            weburl = f"{self._gitlab_base_url}{web_path}"
            if parent_prefix:
                parent_external_record_id = self._code_tree_web_path(
                    project_path, parent_prefix
                )
            else:
                parent_external_record_id = None

            tree_record = FileRecord(
                id=str(uuid.uuid4()),
                org_id=self.data_entities_processor.org_id,
                record_name=str(folder_name),
                record_type=RecordType.FILE.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                external_record_id=web_path,
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                record_group_type=RecordGroupType.PROJECT.value,
                external_record_group_id=external_group_id,
                mime_type=MimeTypes.FOLDER.value,
                external_revision_id=str(folder_hash) if folder_hash else "",
                preview_renderable=False,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=(
                    RecordType.FILE if parent_external_record_id else None
                ),
                is_file=False,
                inherit_permissions=True,
                weburl=weburl,
            )
            record_update = RecordUpdate(
                record=tree_record,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                external_record_id=web_path,
                new_permissions=[],
                old_permissions=[],
            )
            await self._process_new_records([record_update])

    async def build_code_file_records(
        self, code_file_list: list[dict[str, Any]], project_id: int, project_path: str
    ) -> None:
        """Process code file records and push to processing."""

        list_records_new: list[RecordUpdate] = []
        files_skipped = 0
        external_group_id = f"{project_id}-code-repository"
        # See _build_issue_records: indexing filters only suppress indexing.
        # Code files are always synced so the repo tree, parent folders, and
        # permissions stay in the graph regardless of the indexing toggle.
        code_files_enabled = self._code_files_indexing_enabled()
        # Leave source timestamps null during ingest (no per-file GitLab commit API).
        # Commit-history timestamps are filled in by the background backfill that
        # runs after the full connector sync completes.

        for file in code_file_list:
            file_path = file.get("path") or ""
            file_name = file.get("name")
            file_hash = file.get("sha")
            external_record_id = file.get("webPath")
            weburl = file.get("webUrl")

            if not external_record_id or not file_name:
                files_skipped += 1
                self.logger.warning(
                    f"⚠️ Skipping blob {file_path}: missing webPath/name "
                    f"in GitLab response"
                )
                continue
            # skippable files includes file names starting with . (period)
            if file_name.startswith("."):
                files_skipped += 1
                self.logger.info(
                    f"⚠️⚠️ Skipping file {file_name} as it starts with . (period)"
                )
                continue
            file_extension = file_name.split(".")[-1]
            file_mime = getattr(
                MimeTypes, file_extension.upper(), MimeTypes.PLAIN_TEXT
            ).value
            preview_renderable = (
                file_extension.lower() in PREVIEW_RENDERABLE_EXTENSIONS
            )
            # Derive parent (tree) externalRecordId from the child blob's
            # webPath. GitLab uses different URL segments for the two:
            #   trees:  /<group>/<project>/-/tree/<ref>/<path>
            #   blobs:  /<group>/<project>/-/blob/<ref>/<path>
            # so chopping the trailing "/<name>" off the blob URL is not
            # enough — we also have to swap "/-/blob/" for "/-/tree/", or
            # the lookup against the saved tree record misses and
            # _handle_parent_record materializes a phantom folder
            # placeholder named after the blob URL. See _sync_repo_main
            # for the tree→tree case where no swap is needed.
            if "/" in file_path:
                parent_blob_path = external_record_id.rpartition("/")[0]
                parent_external_record_id = parent_blob_path.replace(
                    "/-/blob/", "/-/tree/", 1
                )
            else:
                parent_external_record_id = None
            code_file_record = CodeFileRecord(
                # processor reuses the existing id when it finds a match by
                # (connector_id, external_record_id); UUID here is the
                # fallback for genuinely new records.
                id=str(uuid.uuid4()),
                org_id=self.data_entities_processor.org_id,
                record_name=str(file_name),
                record_type=RecordType.CODE_FILE.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                external_record_id=external_record_id,
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                record_group_type=RecordGroupType.PROJECT.value,
                external_record_group_id=external_group_id,
                mime_type=file_mime,
                external_revision_id=str(file_hash),
                preview_renderable=preview_renderable,
                file_path=file_path,
                file_hash=file_hash,
                inherit_permissions=True,
                parent_external_record_id=parent_external_record_id,
                # See _sync_repo_main: lets _handle_parent_record create a
                # placeholder if the parent folder hasn't been synced yet,
                # which is reused on the next pass instead of orphaning
                # this file.
                parent_record_type=(
                    RecordType.FILE if parent_external_record_id else None
                ),
                weburl=weburl,
                source_created_at=None,
                source_updated_at=None,
            )
            if not code_files_enabled:
                code_file_record.indexing_status = (
                    ProgressStatus.AUTO_INDEX_OFF.value
                )
            record_update = RecordUpdate(
                record=code_file_record,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                external_record_id=external_record_id,
                new_permissions=[],
                old_permissions=[],
            )
            list_records_new.append(record_update)
        if list_records_new:
            await self._process_new_records(list_records_new)
            self.logger.warning(f"⚠️⚠️ Skipped {files_skipped} files")
            self.logger.info(f"Processed new {len(list_records_new)} records")

    @staticmethod
    def _repo_path_from_blob_web_url(web_url: str | None) -> str | None:
        """Extract the repo-relative file path from a GitLab blob ``webUrl``.

        GitLab blob URLs have the shape
        ``https://<host>/<group>/<project>/-/blob/<ref>/<path>``. We strip
        the ``/-/blob/<ref>/`` prefix and percent-decode the remainder.
        Returns ``None`` if the URL doesn't look like a blob URL.

        Used as the source of truth in :meth:`_fetch_code_file_content` so
        that file renames / moves are picked up on the next sync (the
        connector re-saves ``weburl`` on every sync, whereas the stored
        ``file_path`` field can lag).
        """
        if not web_url:
            return None
        marker = "/-/blob/"
        idx = web_url.find(marker)
        if idx < 0:
            return None
        after = web_url[idx + len(marker):]
        # Strip the "<ref>/" segment. <ref> is whatever GitLab returned
        # (often "HEAD" or the resolved default branch).
        ref_sep = after.find("/")
        if ref_sep < 0:
            return None
        return unquote(after[ref_sep + 1:])

    async def _fetch_code_file_content(
        self, record: CodeFileRecord
    ) -> AsyncGenerator[bytes, None]:
        """Stream code file content from GitLab."""
        try:
            # Source of truth for the repo path is `webUrl`: the connector
            # re-saves it on every sync, so it tracks renames / moves once
            # the next sync picks them up. Fall back to the stored
            # `file_path` (older records / non-GitLab webUrl shapes), and
            # finally to a graph traversal — which is a last resort because
            # it depends on parent edges being intact.
            file_path = (
                self._repo_path_from_blob_web_url(record.weburl)
                or record.file_path
            )
            if not file_path:
                async with self.data_store_provider.transaction() as tx_store:
                    file_path = await tx_store.get_record_path(record.id)
            if not file_path:
                raise ValueError(
                    f"Cannot resolve repo path for record {record.id}: "
                    f"weburl={record.weburl!r}, file_path={record.file_path!r}"
                )

            self.logger.info(f"new record from stream : {file_path}")
            external_group_id = getattr(record, "external_record_group_id", None)
            if not external_group_id:
                raise ValueError("❌❌ Project id not found.")
            project_id = external_group_id.split("-")[0]

            file_res = await self._ds_call(
                self.data_source.get_file_content,
                project_id=project_id,
                file_path=file_path,
            )
            if not file_res.success:
                self.logger.error(f"error in fetching file content {file_res.error}")
                raise Exception(
                    f"Error in fetching file content for project {project_id} "
                    f"path {file_path}: {file_res.error}"
                )
            file_data = file_res.data
            if not file_data:
                raise Exception(
                    f"No file content returned by GitLab for project {project_id} "
                    f"path {file_path}"
                )
            # GitLab may return content="" or content=None for zero-byte files;
            # both are valid and must stream as empty bytes, not raise.
            content_b64 = getattr(file_data, "content", None)
            if content_b64 is None:
                yield b""
                return
            # Offload CPU-bound base64 decode so it doesn't stall the event
            # loop while decoding potentially multi-MB source files.
            decoded_bytes = await asyncio.to_thread(base64.b64decode, content_b64)
            yield decoded_bytes
        except Exception as e:
            raise Exception(
                f"Error fetching code content for record {record.id}: {e}"
            ) from e

    # ---------------------------Project Sync-----------------------------------#

    async def _sync_projects(self) -> None:
        """Sync all owned projects.
        1. Sync appUsers and Pseudo groups for each project with permissions.
        2.Sync issues with sync points
        3.Sync merge requests with sync points
        4.Sync repo code files
        """
        projects = await self._resolve_projects_with_filters()
        if not projects:
            self.logger.warning("No projects to sync after applying filters")
            return
        # NOTE: indexing filters (ISSUES / MERGE_REQUESTS / CODE_FILES) only
        # control whether records are indexed, not whether they are synced.
        # We always sync so the graph stays consistent (permissions,
        # parent/child links, record-group membership); the per-record
        # indexing_status is flipped to AUTO_INDEX_OFF inside the build
        # helpers when the corresponding filter is disabled. Same pattern
        # as _comments_indexing_enabled.
        # Each per-project step is isolated: a failure on issues must not
        # block MRs/code on the same project, and a failure on any step of
        # one project must not block the next project. Inside helpers we
        # already log+return on expected failures (e.g. 403); the
        # try/except here is the belt-and-braces for unexpected exceptions
        # (network blips, GraphQL shape changes, etc.).
        for project in projects:
            project_id: int = project.id
            project_path: str = project.path_with_namespace
            for step_name, step in (
                ("members", lambda: self._sync_project_members_as_pseudo(project)),
                ("issues", lambda: self._fetch_issues_batched(project_id)),
                ("merge_requests", lambda: self._fetch_prs_batched(project_id)),
                (
                    "code",
                    lambda: self._sync_repo_main(
                        project_id,
                        project_path,
                        project.default_branch or "main",
                    ),
                ),
            ):
                try:
                    await step()
                except Exception as e:
                    self.logger.error(
                        f"Unhandled error syncing {step_name} for project "
                        f"{project_id} ({project_path}); continuing with next step: {e}",
                        exc_info=True,
                    )

    async def _sync_project_members_as_pseudo(self, project: Project) -> None:
        """Sync users with permissions both with and without mail.

        If ``list_project_members_all`` fails (403 from a token that
        lacks Reporter+ on this project, sustained 5xx, our own
        wall-clock timeout) we used to silently ``return`` and create
        no RecordGroup. The project's records then existed in the
        graph with no PERMISSION edges and were invisible to every
        user — including the operator who explicitly configured this
        project to sync. Mirror the Jira
        ``_fallback_permissions_for_forbidden_scheme`` pattern instead:
        build a single-USER OWNER permission for the creator, attach
        it to all four RecordGroups (project / work items / MRs /
        code), and continue. Same treatment when the members listing
        succeeds but is empty — GitLab returns ``200 OK + []`` instead
        of 403 for users below the role required to enumerate members
        on a private project, which is indistinguishable from "truly
        empty" without context.

        Args:
            project (Project): Gitlab project details
        """
        project_id = project.id
        project_name = project.name
        dict_member: dict[int, GroupMember] = {}
        self.logger.info(f"Syncing users for project {project_name}")
        members_res = await self._ds_call(
            self.data_source.list_project_members_all,
            project_id=project_id,
            get_all=True,
        )
        listing_failed = not members_res.success
        listing_empty = (
            members_res.success and not (members_res.data or [])
        )
        if listing_failed or listing_empty:
            # Try creator fallback first; if we have no creator identity
            # we keep the original behaviour (log + early return) so
            # tests that exercise the "no creator configured" path stay
            # stable. Logging the original failure unconditionally here
            # would emit two messages for that same case.
            creator_perm = self._creator_user_permission()
            if creator_perm is None:
                if listing_failed:
                    self.logger.error(
                        "❌❌ Error fetching members for project %s (%s): %s",
                        project_id,
                        project_name,
                        getattr(members_res, "error", "unknown"),
                    )
                else:
                    self.logger.info(
                        "No members found for project %s ", project_id
                    )
                return
            # Have a creator — apply the fallback and continue. Logging
            # describes both *why* we are falling back and *what* we
            # are doing, so the operator can correlate the two when
            # diagnosing a sync that suddenly grants narrower ACLs.
            if listing_failed:
                self.logger.error(
                    "❌❌ Error fetching members for project %s (%s): %s. "
                    "Falling back to creator-only permissions.",
                    project_id,
                    project_name,
                    getattr(members_res, "error", "unknown"),
                )
            else:
                self.logger.warning(
                    "No members returned for project %s (%s); GitLab does this "
                    "for tokens below the role required to enumerate members on "
                    "a private project. Falling back to creator-only permissions "
                    "so the project is at least visible to the configuring user.",
                    project_id,
                    project_name,
                )
            await self._apply_creator_fallback_for_project(project)
            return

        members = members_res.data
        # Dedup by id keeping the HIGHEST access_level we've seen for that
        # user. ``/projects/:id/members/all`` can return the same user via
        # multiple sources (direct project membership + group inheritance +
        # ancestor-group inheritance); some GitLab versions surface every
        # row separately. The previous ``dict[k] = v`` last-write-wins
        # logic could downgrade a Maintainer-direct row to a Developer-
        # inherited row purely based on response ordering, which knocked
        # users out of the ``>= 15`` access-level dispatch below.
        for member in members:
            existing = dict_member.get(member.id)
            if existing is None:
                dict_member[member.id] = member
                continue
            new_level = getattr(member, "access_level", 0) or 0
            old_level = getattr(existing, "access_level", 0) or 0
            if new_level > old_level:
                dict_member[member.id] = member
        # Ensure the connector creator is represented in the project's
        # member set even when the listing succeeded but did not include
        # them. GitLab Admin / EE Auditor personas can read every
        # project via a user-level flag rather than a per-project
        # membership row, so they will be systematically absent from a
        # successful ``/projects/:id/members/all`` response. ``setdefault``
        # preserves any real row already present (and its actual
        # access_level) — only the truly-missing case lands at the
        # OWNER stub from ``_build_creator_member_stub``.
        self._inject_creator_member_into(dict_member)
        # make sudo permission groups of users with no email along with ones mails visible
        permission_project_level = []
        permission_work_items_level = []
        permission_code_repo_level = []
        permission_merge_requests_level = []
        for member in dict_member.values():
            permission = await self._transform_restrictions_to_permisions(member)
            if permission:
                permission_project_level.append(permission)
                external_member_level: int = getattr(member, "access_level", 0)
                if external_member_level == 0:
                    self.logger.info(
                        f"Member {member.name} has no access level, skipping"
                    )
                elif external_member_level == 10:
                    permission_work_items_level.append(permission)
                elif external_member_level >= 15:
                    permission_work_items_level.append(permission)
                    permission_merge_requests_level.append(permission)
                    permission_code_repo_level.append(permission)
                else:
                    self.logger.warning(
                        f"Member {member.name} has unrecognized access level {external_member_level}, skipping"
                    )

        (
            project_record_group,
            work_items_record_group,
            merge_requests_record_group,
            code_repo_record_group,
        ) = self._build_project_record_groups(project)
        self.logger.info("Creating work items record group")
        await self.data_entities_processor.on_new_record_groups(
            [
                (project_record_group, permission_project_level),
                (work_items_record_group, permission_work_items_level),
                (code_repo_record_group, permission_code_repo_level),
                (merge_requests_record_group, permission_merge_requests_level),
            ]
        )
        self.logger.info("Synced Permissions for all levels.")

    def _build_project_record_groups(
        self, project: Project
    ) -> tuple[RecordGroup, RecordGroup, RecordGroup, RecordGroup]:
        """Return ``(project, work_items, merge_requests, code_repo)`` record groups.

        Single source of truth for the four-RG shape so the creator-fallback
        path and the normal member-sync path cannot drift on
        ``external_group_id`` suffixes or parent linkage.
        """
        parent_for_project_rg: str | None = None
        if self._gitlab_included_group_paths:
            ns_path = self._namespace_full_path(project)
            parent_for_project_rg = self._longest_matching_group_path(
                ns_path, self._gitlab_included_group_paths
            )

        project_record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name=project.path_with_namespace,
            group_type=RecordGroupType.PROJECT.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=str(project.id),
            parent_external_group_id=parent_for_project_rg,
        )
        work_items_record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name="Work items",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=f"{project.id}-work-items",
            parent_external_group_id=str(project.id),
        )
        merge_requests_record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name="Merge requests",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=f"{project.id}-merge-requests",
            parent_external_group_id=str(project.id),
        )
        code_repo_record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name="Code repository",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=f"{project.id}-code-repository",
            parent_external_group_id=str(project.id),
        )
        return (
            project_record_group,
            work_items_record_group,
            merge_requests_record_group,
            code_repo_record_group,
        )

    async def _apply_creator_fallback_for_project(self, project: Project) -> None:
        """Create the four project ``RecordGroup`` nodes with creator-only ACLs.

        Called from ``_sync_project_members_as_pseudo`` when the
        ``list_project_members_all`` source-of-truth call fails or
        returns an empty list we cannot trust (GitLab returns
        ``200 OK + []`` instead of 403 for tokens below the role
        required to enumerate members on a private project). Without
        this, the project's records would be created with no
        PERMISSION edges and become invisible to every PipesHub user.

        Building the record groups here keeps downstream sync steps
        (issues / MRs / code) functional — they only need the parent
        RecordGroup to exist before they attach records to it.
        """
        creator_permission = self._creator_user_permission()
        if creator_permission is None:
            self.logger.error(
                "Cannot fall back to creator-only permissions for project "
                "%s (%s): no creator identity resolved during init. "
                "Skipping RecordGroup creation; downstream records on "
                "this project will not be ingested this run.",
                project.id,
                project.path_with_namespace,
            )
            return

        (
            project_record_group,
            work_items_record_group,
            merge_requests_record_group,
            code_repo_record_group,
        ) = self._build_project_record_groups(project)
        # Same permission object on all four groups — the configuring
        # user is the only principal we know is entitled to anything
        # on this project, and the per-level dispatch (work items vs
        # MRs vs code) is meaningless when we have a single principal.
        perms = [creator_permission]
        await self.data_entities_processor.on_new_record_groups(
            [
                (project_record_group, perms),
                (work_items_record_group, perms),
                (code_repo_record_group, perms),
                (merge_requests_record_group, perms),
            ]
        )
        self.logger.info(
            "Applied creator-only fallback permissions to project %s (%s) for %s",
            project.id,
            project.path_with_namespace,
            self.creator_email,
        )

    async def _transform_restrictions_to_permisions(
        self, member: GroupMember
    ) -> Permission | None:
        """Transform restrictions to permissions"""
        principal_id = str(member.id)
        permission_type = PermissionType.OWNER.value
        permission = await self._create_permission_from_principal(
            EntityType.USER.value,
            principal_id,
            permission_type,
            create_pseudo_group_if_missing=True,  # Enable pseudo-group creation for record-level permissions
        )
        if permission:
            return permission
        return None

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType,
        *,
        create_pseudo_group_if_missing: bool = False,
    ) -> Permission | None:
        """
        Create Permission object from principal data (user or group).

        This is a common function used by both space and page permission processing.

        Args:
            principal_type: "user" or "group"
            principal_id: accountId for users, groupId for groups
            permission_type: Mapped PermissionType enum
            create_pseudo_group_if_missing: If True and user not found, create a
                pseudo-group to preserve the permission. Used for record-level

        Returns:
            Permission object or None if principal not found in DB
        """
        try:
            if principal_type == EntityType.USER.value:
                entity_type = EntityType.USER
                # Lookup user by source_user_id (accountId) using transaction store
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_source_id(
                        source_user_id=principal_id,
                        connector_id=self.connector_id,
                    )
                    if user:
                        return Permission(
                            email=user.email,
                            type=permission_type,
                            entity_type=entity_type,
                        )

                    # User not found - check if pseudo-group exists or should be created
                    if create_pseudo_group_if_missing:
                        # Check for existing pseudo-group
                        pseudo_group = await tx_store.get_user_group_by_external_id(
                            connector_id=self.connector_id,
                            external_id=principal_id,
                        )

                        if not pseudo_group:
                            # Create pseudo-group on-the-fly
                            pseudo_group = await self._create_pseudo_group(principal_id)

                        if pseudo_group:
                            self.logger.debug(
                                f"Using pseudo-group for user {principal_id} (no email available)"
                            )
                            return Permission(
                                external_id=pseudo_group.source_user_group_id,
                                type=permission_type,
                                entity_type=EntityType.GROUP,
                            )

                    self.logger.debug(
                        f"  ⚠️ User {principal_id} not found in DB, skipping permission"
                    )
                    return None
        except Exception as e:
            self.logger.error(f"❌ Failed to create permission from principal: {e}")
            return None

    async def _create_pseudo_group(self, account_id: str) -> AppUserGroup | None:
        """
        Create a pseudo-group for a user without email.

        This preserves permissions for users who don't have email addresses yet.
        The pseudo-group uses the user's accountId as source_user_group_id.

        Args:
            account_id: Gitlab user accountId

        Returns:
            Created AppUserGroup or None if creation fails
        """
        try:
            pseudo_group = AppUserGroup(
                app_name=Connectors.GITLAB,
                connector_id=self.connector_id,
                source_user_group_id=account_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX}_{account_id}",
                org_id=self.data_entities_processor.org_id,
            )

            # Save to database (empty members list)
            await self.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            self.logger.info(
                f"Created pseudo-group for user without email: {account_id}"
            )

            return pseudo_group

        except Exception as e:
            self.logger.error(f"Failed to create pseudo-group for {account_id}: {e}")
            return None

    # ---------------------------Issues Sync-----------------------------------#

    async def _fetch_issues_batched(self, project_id: int) -> None:
        """
        Process: for each project read sync point, fetch work-items
        Args:
            last_sync_time (str): epoch second of last sync time
        """
        # get issue permissions as of now inherit them from RECORD_GROUP PROJECT
        last_sync_time: int | None = await self._get_issues_sync_checkpoint(project_id)
        if last_sync_time is not None:
            since_dt = datetime.fromtimestamp(last_sync_time / 1000, tz=timezone.utc)
        else:
            since_dt = None
        filter_after, filter_before = self._datetime_range_from_sync_filter(
            SyncFilterKey.MODIFIED
        )
        if filter_after is not None:
            if since_dt is None:
                since_dt = filter_after
            else:
                since_dt = max(since_dt, filter_after)
        updated_before = filter_before
        created_after, created_before = self._datetime_range_from_sync_filter(
            SyncFilterKey.CREATED
        )
        issues_res = await self._ds_call(
            self.data_source.list_issues,
            project_id=project_id,
            updated_after=since_dt,
            updated_before=updated_before,
            created_after=created_after,
            created_before=created_before,
            order_by=GitlabLiterals.UPDATED_AT.value,
            sort="asc",
            get_all=True,
        )
        if not issues_res.success:
            # Per-project, per-resource failures (e.g. 403 because the token
            # has read access to the project but not to its issue tracker)
            # must not abort the whole sync — other resources on this
            # project and every later project should still run. Log and
            # bail out of the issues phase only.
            self.logger.error(
                f"Error fetching issues for project {project_id}: {issues_res.error}"
            )
            return
        if not issues_res.data:
            self.logger.debug(f"No issues found for project {project_id}")
            return
        all_issues: list[ProjectIssue] = issues_res.data
        total_issues = len(all_issues)
        self.logger.info(f"📦 Fetched {total_issues} issues, processing in batches...")
        batch_size = self.batch_size
        batch_number = 0
        for i in range(0, total_issues, batch_size):
            batch_number += 1
            issues_batch = all_issues[i : i + batch_size]
            batch_records: list[RecordUpdate] = []
            self.logger.debug(
                f"📦 Processing batch {batch_number}: {len(issues_batch)} issues"
            )
            batch_records = await self._build_issue_records(issues_batch)
            # send batch results to process
            await self._process_new_records(batch_records)

    async def _process_new_records(self, batch_records: list[RecordUpdate]) -> None:
        """Send new records in batches to process"""
        # NOTE: all functions calling this ensures only tickets+files or pull_requests+files are sent here
        need_sync_update: bool = True
        for i in range(0, len(batch_records), self.batch_size):
            batch = batch_records[i : i + self.batch_size]
            batch_sent: list[tuple[Record, list[Permission]]] = [
                (record_update.record, record_update.new_permissions)
                for record_update in batch
            ]
            try:
                await self.data_entities_processor.on_new_records(batch_sent)
                if not need_sync_update:
                    continue
                last_sync_time = None
                project_id: int | None = None
                record_type: RecordType | None = None
                for record_update in batch:
                    if record_update.record.record_type == RecordType.TICKET:
                        record_type = RecordType.TICKET
                        last_sync_time = record_update.record.source_updated_at
                        project_id = record_update.record.external_record_group_id
                    elif record_update.record.record_type == RecordType.PULL_REQUEST:
                        record_type = RecordType.PULL_REQUEST
                        last_sync_time = record_update.record.source_updated_at
                        project_id = record_update.record.external_record_group_id
                    else:
                        continue
                if project_id and last_sync_time:
                    if record_type == RecordType.TICKET:
                        await self._update_issues_sync_checkpoint(
                            project_id, last_sync_time
                        )
                    elif record_type == RecordType.PULL_REQUEST:
                        await self._update_mrs_sync_checkpoint(
                            project_id, last_sync_time
                        )
            except Exception as e:
                self.logger.error(f"❌❌Error in processing set of records: {e}")
                need_sync_update = False

        self.logger.info(f"✅✅ Processed {len(batch_records)} records")

    async def _build_issue_records(
        self, issue_batch: list[ProjectIssue]
    ) -> list[RecordUpdate]:
        """Send new issue records for processing: Ticket records from issues, extract attachments from description, notes"""
        record_updates_batch: list[RecordUpdate] = []
        attachment_records_cnt = 0
        # Indexing filters only suppress indexing; the records themselves are
        # always synced so the graph (permissions, parent/child links) stays
        # complete. Attachments inherit the parent's indexing decision: if
        # ISSUES is off the description/notes attachments are off too;
        # otherwise note attachments still respect the COMMENTS filter.
        issues_enabled = self._issues_indexing_enabled()
        comments_enabled = self._comments_indexing_enabled()
        for issue in issue_batch:
            # consider ticket types-> issue, incident, task
            record_update = await self._process_issue_incident_task_to_ticket(issue)
            if not record_update:
                continue
            if not issues_enabled:
                record_update.record.indexing_status = (
                    ProgressStatus.AUTO_INDEX_OFF.value
                )
            record_updates_batch.append(record_update)
            # get the file attachments from issue data
            # make file records for all except images
            markdown_content_raw: str = getattr(issue, "description", "") or ""
            (
                attachments,
                markdown_content,
            ) = await self.parse_gitlab_uploads_clean_test(markdown_content_raw)
            if attachments:
                file_record_updates = await self.make_file_records_from_list(
                    attachments=attachments, record=record_update.record
                )
                if file_record_updates:
                    if not issues_enabled:
                        for ru in file_record_updates:
                            ru.record.indexing_status = (
                                ProgressStatus.AUTO_INDEX_OFF.value
                            )
                    record_updates_batch.extend(file_record_updates)
                    attachment_records_cnt += len(file_record_updates)
            # adding notes attachments — always sync the records so they exist
            # in the graph; when the COMMENTS (or parent ISSUES) indexing
            # filter is off we flip indexing_status to AUTO_INDEX_OFF so they
            # are not indexed.
            attachment_records = await self.make_files_records_from_notes(
                issue, record_update.record
            )
            if attachment_records:
                if not issues_enabled or not comments_enabled:
                    for ru in attachment_records:
                        ru.record.indexing_status = (
                            ProgressStatus.AUTO_INDEX_OFF.value
                        )
                record_updates_batch.extend(attachment_records)
                attachment_records_cnt += len(attachment_records)
        self.logger.debug(
            f"Added {attachment_records_cnt} attachments for issues batch"
        )
        return record_updates_batch

    async def _process_issue_incident_task_to_ticket(
        self, issue: ProjectIssue
    ) -> RecordUpdate | None:
        """Make Ticket Records of gitlab work-items
        Args:
            issue (Issue): Gitlab issues, incidents, tasks
        """
        try:
            # check if record already exists
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{issue.id}"
                )
            # detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            if existing_record:
                # TODO: add more changes especially body ones as of now default fallback to full body reindexing
                # check if title changed
                if existing_record.record_name != issue.title:
                    metadata_changed = True
                    is_updated = True
                # TODO: body changes check as of now True default
                content_changed = True
                is_updated = True

            issue_type = ItemType.ISSUE.value
            if issue.issue_type == ItemType.INCIDENT.value.lower():
                issue_type = ItemType.INCIDENT.value
            elif issue.issue_type == ItemType.TASK.value.lower():
                issue_type = ItemType.TASK.value

            label_names: list[str] = []
            for label in issue.labels:
                label_names.append(label)
            external_group_id = f"{issue.project_id}-work-items"
            ticket_record = TicketRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=issue.title,
                external_record_id=str(issue.id),
                record_type=RecordType.TICKET.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=parse_timestamp(issue.updated_at),
                source_created_at=parse_timestamp(issue.created_at),
                version=0,  # not used further so 0
                external_record_group_id=external_group_id,
                org_id=self.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=issue.web_url,
                status=issue.state,
                external_revision_id=str(parse_timestamp(issue.updated_at)),
                preview_renderable=False,
                type=issue_type,
                labels=label_names,
                inherit_permissions=True,
            )
            return RecordUpdate(
                record=ticket_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=[],
                new_permissions=[],
                external_record_id=str(issue.id),
            )
        except Exception as e:
            self.logger.error(
                f"Error in processing issue/task/incident to ticket: {e}", exc_info=True
            )
            return None

    async def _build_ticket_blocks(self, record: Record) -> bytes:
        """Build blocks for ticket record
        Block Group sequence
            1.Description BlockGroup
            2.Notes(Comments) BlockGroups
        Args:
            record (Record): Baseclass Record of Ticket Record
        Returns:
            Bytes: BlocksContainer in JSON format
        """
        raw_url = getattr(record, "weburl", "") or ""
        if not raw_url:
            raise ValueError("Web URL is required for indexing ticket")
        raw_url = raw_url.split("/")
        issue_number = int(raw_url[7])
        external_group_id: str = getattr(record, "external_record_group_id")
        if not external_group_id:
            raise Exception("❌❌ Project id not found.")
        project_id = external_group_id.split("-")[0]
        issue_res = await self._ds_call(
            self.data_source.get_issue, project_id=project_id, issue_iid=issue_number
        )
        if not issue_res.success:
            raise Exception(
                f"❌❌ Failed to fetch issue details for record {record.external_record_id}: {issue_res.error}"
            )
        if not issue_res.data:
            raise Exception(
                f"❌❌ No issue data found for record {record.external_record_id}"
            )
        base_project_url = f"{self._gitlab_base_url}/api/v4/projects/{project_id}"
        block_group_number = 0
        blocks: list[Block] = []
        block_groups: list[BlockGroup] = []
        issue = issue_res.data

        # getting modi. markdown  content with images as base64
        markdown_content_raw: str = getattr(issue, "description", "") or ""
        markdown_content_with_images_base64 = await self.embed_images_as_base64(
            markdown_content_raw, base_project_url
        )
        self.logger.debug(f"Processed markdown content for issue {issue.title}")
        # NOTE: Adding record name into Content for record name search Permanently FIX todo
        markdown_content_with_images_base64 = (
            f"# {issue.title}\n\n{markdown_content_with_images_base64}"
        )
        list_remaining_records: list[RecordUpdate] = []
        child_records, remaining_records = await self.make_child_records_of_attachments(
            markdown_raw=markdown_content_raw, record=record
        )
        list_remaining_records.extend(remaining_records)
        # bg of title and description/body
        bg_0 = BlockGroup(
            index=block_group_number,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.CONTENT.value,
            source_group_id=record.weburl,
            data=markdown_content_with_images_base64,
            source_modified_date=string_to_datetime(issue.updated_at),
            requires_processing=True,
            children_records=child_records,
        )
        block_groups.append(bg_0)
        # make blocks of issue comments
        if self._comments_indexing_enabled():
            comments_bg, remaining_records = await self._build_comment_blocks(
                issue_url=record.weburl, parent_index=block_group_number, record=record
            )
            block_groups.extend(comments_bg)
            block_group_number += len(comments_bg)
            list_remaining_records.extend(remaining_records)
        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        await self._process_new_records(list_remaining_records)

        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode(GitlabLiterals.UTF_8.value)

    async def _handle_record_updates(self, issue_update: RecordUpdate) -> None:
        """_summary_

        Args:
            issue_update (IssueUpdate): _description_
        """
        return

    def _gitlab_project_id_and_iid_from_record(self, record: Record) -> tuple[str, int] | None:
        """Resolve GitLab project id and issue/MR IID from synced record fields.

        Self-hosted GitLab can be served under a path prefix or under nested
        subgroups, so the issue/MR IID is *not* at a fixed index in
        ``weburl.split("/")``. Locate the ``/-/`` separator GitLab inserts
        between the project path and the resource path
        (``.../-/issues/<iid>`` or ``.../-/merge_requests/<iid>``) and read
        the IID relative to that.
        """
        external_group_id = getattr(record, "external_record_group_id", None) or ""
        if not external_group_id:
            return None
        project_part = external_group_id.split("-")[0]
        if not project_part:
            return None
        raw_url = getattr(record, "weburl", "") or ""
        if not raw_url:
            return None
        try:
            path = urlparse(raw_url).path
        except (TypeError, ValueError):
            return None
        # Strip empty segments so leading/trailing slashes don't shift indices.
        segments = [s for s in path.split("/") if s]
        try:
            dash_idx = segments.index("-")
        except ValueError:
            return None
        # Expect ``-/<resource>/<iid>`` after the project path. ``<resource>``
        # is ``issues`` for tickets and ``merge_requests`` for PRs; we accept
        # either since the caller already discriminates on ``record_type``.
        if dash_idx + 2 >= len(segments):
            return None
        resource = segments[dash_idx + 1]
        if resource not in ("issues", "merge_requests"):
            return None
        try:
            iid = int(segments[dash_idx + 2])
        except ValueError:
            return None
        return (project_part, iid)

    async def _check_and_fetch_updated_record_for_reindex(
        self, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Fetch TICKET or PULL_REQUEST from GitLab; return graph upsert data if source revision changed."""
        parsed = self._gitlab_project_id_and_iid_from_record(record)
        if not parsed:
            self.logger.warning(
                f"Cannot reindex-check GitLab record {record.id}: missing weburl or external_record_group_id"
            )
            return None
        project_id, iid = parsed

        if record.record_type == RecordType.TICKET:
            issue_res = await self._ds_call(
                self.data_source.get_issue, project_id=project_id, issue_iid=iid
            )
            if not issue_res.success or not issue_res.data:
                self.logger.error(
                    f"Failed to fetch GitLab issue for reindex {record.id}: {issue_res.error}"
                )
                return None
            issue = issue_res.data
            new_rev = str(parse_timestamp(issue.updated_at))
            prev_rev = getattr(record, "external_revision_id", None)
            if prev_rev and prev_rev == new_rev:
                return None
            ru = await self._process_issue_incident_task_to_ticket(issue)
            if not ru:
                return None
            return (ru.record, ru.new_permissions)

        if record.record_type == RecordType.PULL_REQUEST:
            mr_res = await self._ds_call(
                self.data_source.get_merge_request, project_id=project_id, mr_iid=iid
            )
            if not mr_res.success or not mr_res.data:
                self.logger.error(
                    f"Failed to fetch GitLab merge request for reindex {record.id}: {mr_res.error}"
                )
                return None
            mr = mr_res.data
            new_rev = str(parse_timestamp(mr.updated_at))
            prev_rev = getattr(record, "external_revision_id", None)
            if prev_rev and prev_rev == new_rev:
                return None
            ru = await self._process_mr_to_pull_request(mr)
            if not ru:
                return None
            return (ru.record, ru.new_permissions)

        # FILE / CODE_FILE / others: refresh not implemented; trigger reindex from existing graph row.
        return None

    async def reindex_records(self, records: list[Record]) -> None:
        """Reindex GitLab records: upsert work items that changed at source; re-queue others for indexing."""
        try:
            if not records:
                return

            await self._refresh_token_if_needed()
            if not self.data_source:
                raise Exception("DataSource not initialized. Call init() first.")

            self.logger.info(f"Starting reindex for {len(records)} GitLab records")

            updated_pairs: list[tuple[Record, list[Permission]]] = []
            non_updated: list[Record] = []

            for record in records:
                try:
                    fresh = await self._check_and_fetch_updated_record_for_reindex(record)
                    if fresh:
                        updated_pairs.append(fresh)
                    else:
                        non_updated.append(record)
                except Exception as e:
                    self.logger.error(
                        f"Error checking GitLab record {record.id} at source: {e}"
                    )
                    continue

            if updated_pairs:
                await self.data_entities_processor.on_new_records(updated_pairs)
                self.logger.info(
                    f"Updated {len(updated_pairs)} GitLab records in DB that changed at source"
                )

            if non_updated:
                reindexable: list[Record] = []
                skipped_untyped = 0
                skipped_folders = 0
                for r in non_updated:
                    if type(r).__name__ == "Record":
                        self.logger.warning(
                            f"Record {r.id} ({r.record_type}) is base Record class, skipping reindex"
                        )
                        skipped_untyped += 1
                        continue
                    # GitLab uses RecordType.FILE for two distinct things:
                    #   1) folder/tree nodes from repo sync — ``extension``
                    #      is never set (see ``_sync_repo_main`` where
                    #      FileRecord is built without an ``extension``
                    #      field; mime_type=FOLDER, is_file=False).
                    #   2) attachments uploaded in issues/MRs — always
                    #      have ``extension`` set from ``attach.filetype``
                    #      (see ``make_child_records_of_attachments``).
                    #
                    # Reindexing a folder triggers ``stream_record`` ->
                    # ``_fetch_attachment_content`` -> GitLab 404
                    # ("record not found"). Use ``extension`` as the
                    # discriminator: missing/empty == folder, skip.
                    #
                    # Scope this check to RecordType.FILE — CodeFileRecord
                    # has no ``extension`` field at all (see
                    # ``app/models/entities.py``), so a blanket check
                    # would skip every code file.
                    if r.record_type == RecordType.FILE:
                        extension = getattr(r, "extension", None)
                        if not extension or not str(extension).strip():
                            skipped_folders += 1
                            continue
                    reindexable.append(r)
                if reindexable:
                    try:
                        await self.data_entities_processor.reindex_existing_records(
                            reindexable
                        )
                        self.logger.info(
                            f"Published reindex events for {len(reindexable)} GitLab records"
                        )
                    except NotImplementedError as e:
                        self.logger.warning(
                            f"Cannot reindex records — to_kafka_record not implemented: {e}"
                        )
                if skipped_untyped:
                    self.logger.warning(
                        f"Skipped reindex for {skipped_untyped} records that are not properly typed"
                    )
                if skipped_folders:
                    self.logger.info(
                        f"Skipped reindex for {skipped_folders} folder records (no streamable content)"
                    )

        except Exception as e:
            self.logger.error(f"Error during GitLab reindex: {e}", exc_info=True)
            raise


    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    # ---------------------------Comments sync-----------------------------------#

    async def _build_comment_blocks(
        self, issue_url: str, parent_index: int, record: Record
    ) -> tuple[list[BlockGroup], list[RecordUpdate]]:
        """Build block groups for issue notes
        Args:
            issue_url (str): URL of issue
            parent_index (int): Index of parent block group
            record (Record): Baseclass Record of Ticket Record
        Returns:
            tuple[list[BlockGroup],list[RecordUpdate]]: List of block groups and remaining records
        """
        self.logger.debug(f"Building comment blocks for issue: {issue_url}")
        raw_url = issue_url.split("/")
        issue_number = int(raw_url[7])
        # Fetching issue comments if present
        # TODO: will date wise filtering be needed here, as of now None
        project_id = record.external_record_group_id.split("-")[0]
        comments_res = await self._ds_call(
            self.data_source.list_issue_notes,
            project_id=int(project_id),
            issue_iid=issue_number,
            get_all=True,
        )
        if not comments_res.success:
            raise Exception(
                f"Failed to fetch comments for issue {issue_url}: {comments_res.error}"
            )
        if not comments_res.data:
            self.logger.info(f"No comments found for issue {issue_url}")
        block_groups: list[BlockGroup] = []
        list_remaining_records: list[RecordUpdate] = []
        block_group_number = parent_index + 1
        comments: list[ProjectIssueNote] = comments_res.data
        self.logger.debug(
            f"Fetched {len(comments)} comments for issue {issue_url}, building blocks..."
        )
        base_project_url = f"{self._gitlab_base_url}/api/v4/projects/{project_id}"
        for comment in comments:
            raw_markdown_content: str = getattr(comment, "body", "") or ""
            (
                child_records,
                remaining_records,
            ) = await self.make_child_records_of_attachments(
                markdown_raw=raw_markdown_content, record=record
            )
            list_remaining_records.extend(remaining_records)
            markdown_content_with_images_base64 = await self.embed_images_as_base64(
                raw_markdown_content, base_project_url
            )
            # making comment name
            comment_name = ""
            comment_author = getattr(comment, "author", {}) or {}
            comment_username = comment_author.get("username")
            if comment_username:
                comment_name = f"Comment by {comment_username} on issue {issue_number}"
            else:
                comment_name = f"Comment on issue {issue_number}"
            bg = BlockGroup(
                index=block_group_number,
                parent_index=parent_index,
                name=comment_name,
                type=GroupType.TEXT_SECTION.value,
                format=DataFormat.MARKDOWN.value,
                sub_type=GroupSubType.COMMENT.value,
                data=markdown_content_with_images_base64,
                weburl=issue_url,
                requires_processing=True,
                children_records=child_records,
            )
            block_group_number += 1
            block_groups.append(bg)
        return block_groups, list_remaining_records

    async def _build_merge_request_comment_blocks(
        self, mr_url: str, parent_index: int, record: Record
    ) -> tuple[list[BlockGroup], list[RecordUpdate]]:
        """Build comment block groups for merge request
        Block Group sequence
        1.Description BlockGroup
        2.Notes(Comments) BlockGroups -> System comments, Generic notes, File comments (review comments)
        3.File commits blocks
        """
        self.logger.debug(
            f"Building comment block groups for merge request {record.record_name}"
        )
        raw_url = mr_url.split("/")
        mr_number = int(raw_url[7])
        project_id = record.external_record_group_id.split("-")[0]
        comments_res = await self._ds_call(
            self.data_source.list_merge_request_notes,
            project_id=int(project_id),
            mr_iid=mr_number,
            get_all=True,
        )
        if not comments_res.success:
            raise Exception(
                f"❌❌ Failed to fetch comments for merge request {mr_url}: {comments_res.error}"
            )
        if not comments_res.data:
            self.logger.info(f"No comments found for merge request {mr_url}")
        # handling usual comments and review comments together
        block_groups: list[BlockGroup] = []
        block_group_number = parent_index + 1
        comments: list[ProjectMergeRequestNote] = comments_res.data
        self.logger.debug(
            f"Fetched {len(comments)} comments for merge request {mr_url}, building blocks..."
        )
        list_remaining_attachments: list[RecordUpdate] = []
        map_file_r_comments: dict[str, list[BlockComment]] = {}
        base_project_url = f"{self._gitlab_base_url}/api/v4/projects/{project_id}"
        for comment in comments:
            # classify as system, usual or file based comment
            # make bg of usual comments at once, map r_comments with file
            is_system_comment = getattr(comment, "system", False)
            is_review_comment = getattr(comment, "position", None)
            if is_review_comment:
                # will need to get file changes per file, new  file content, then attach mapped r_comments
                raw_markdown_content: str = getattr(comment, "body", "") or ""
                markdown_content_with_images_base64 = await self.embed_images_as_base64(
                    raw_markdown_content, base_project_url
                )
                (
                    comment_attachments,
                    remaining_attachments,
                ) = await self.make_block_comment_of_attachments(
                    markdown_raw=raw_markdown_content, record=record
                )
                list_remaining_attachments.extend(remaining_attachments)
                position = getattr(comment, "position", {})
                file_path = position.get("new_path")
                comment_modified_date = getattr(
                    comment, GitlabLiterals.UPDATED_AT.value, ""
                )
                comment_created_date = getattr(comment, "created_at", "")
                source_modified_date = string_to_datetime(comment_modified_date)
                source_created_date = string_to_datetime(comment_created_date)
                block_comment = BlockComment(
                    text=markdown_content_with_images_base64,
                    format=DataFormat.MARKDOWN.value,
                    updated_at=source_modified_date,
                    created_at=source_created_date,
                    attachments=comment_attachments,
                )
                if file_path:
                    if file_path in map_file_r_comments:
                        map_file_r_comments[file_path].append(block_comment)
                    else:
                        map_file_r_comments[file_path] = [block_comment]
            else:
                raw_markdown_content: str = getattr(comment, "body", "") or ""
                markdown_content_with_images_base64 = await self.embed_images_as_base64(
                    raw_markdown_content, base_project_url
                )
                (
                    child_records,
                    remaining_attachments,
                ) = await self.make_child_records_of_attachments(
                    markdown_raw=raw_markdown_content, record=record
                )
                list_remaining_attachments.extend(remaining_attachments)
                comment_name = ""
                comment_author = getattr(comment, "author", {})
                comment_username = comment_author.get("username")
                data = markdown_content_with_images_base64
                if comment_username:
                    if is_system_comment:
                        comment_name = f"System Comment by {comment_username} on merge request {mr_number}"
                        data = (
                            f"System comment \n\n {markdown_content_with_images_base64}"
                        )
                    else:
                        comment_name = f"Comment by {comment_username} on merge request {mr_number}"
                else:
                    if is_system_comment:
                        comment_name = f"System Comment on merge request {mr_number}"
                        data = (
                            f"System comment \n\n {markdown_content_with_images_base64}"
                        )
                    else:
                        comment_name = f"Comment on merge request {mr_number}"
                comment_modified_date = getattr(
                    comment, GitlabLiterals.UPDATED_AT.value, ""
                )
                source_modified_date = string_to_datetime(comment_modified_date)
                bg = BlockGroup(
                    index=block_group_number,
                    parent_index=parent_index,
                    name=comment_name,
                    type=GroupType.TEXT_SECTION.value,
                    format=DataFormat.MARKDOWN.value,
                    sub_type=GroupSubType.COMMENT.value,
                    data=data,
                    weburl=mr_url,
                    source_modified_date=source_modified_date,
                    requires_processing=True,
                    children_records=child_records,
                )
                block_group_number += 1
                block_groups.append(bg)

        # fetching file changes of mr
        # iterate through each file changes, append with new file content
        # to get file content use mr -> sha as ref with path pf file
        file_changes_res = await self._ds_call(
            self.data_source.list_merge_request_changes,
            project_id=int(project_id),
            mr_iid=mr_number,
        )
        if not file_changes_res.success:
            self.logger.error(
                f"❌❌ Failed to fetch file changes for merge request {mr_url}: {file_changes_res.error}"
            )
            raise Exception(
                f"❌❌ Failed to fetch file changes for merge request {mr_url}: {file_changes_res.error}"
            )
        if not file_changes_res.data:
            self.logger.info(f"No file changes found for merge request {mr_url}")
        file_changes = file_changes_res.data
        # TODO: below call Can be avoided once Base SHA and head sha
        # are included as fields in pull request record while streaming
        # Also the additional properties of pr record included while calling stream record
        tmp_mr_res = await self._ds_call(
            self.data_source.get_merge_request,
            project_id=int(project_id),
            mr_iid=mr_number,
        )
        tmp_mr = tmp_mr_res.data
        tmp_mr_sha = getattr(tmp_mr, "sha", "")
        self.logger.debug(f"tmp_mr_sha : {tmp_mr_sha}")
        changes = file_changes.get("changes", [])
        for file_change in changes:
            file_path = file_change.get("new_path", "")
            diff_content = file_change.get("diff", "")
            is_new_file = file_change.get("new_file", False)
            is_deleted_file = file_change.get("deleted_file", False)
            is_generated_file = file_change.get("generated_file", False)
            is_truncated_diff = file_change.get("too_large", False)
            # fetching new file content only if new or changed
            new_file_content = ""
            if is_new_file or not is_deleted_file:
                new_file_content_res = await self._ds_call(
            self.data_source.get_file_content,
                    project_id=int(project_id),
                    file_path=file_path,
                    ref=tmp_mr_sha,
                )
                if not new_file_content_res.success:
                    self.logger.error(
                        f"❌❌ Failed to fetch new file content for file {file_path} in merge request {mr_url}: {new_file_content_res.error}"
                    )
                    continue
                if not new_file_content_res.data:
                    self.logger.debug(
                        f"No file content found for file {file_path} in merge request {mr_url}"
                    )
                new_file = new_file_content_res.data
                new_file_content = getattr(new_file, "content", "")
            try:
                # Offload CPU-bound base64 decode so large MR files don't
                # stall the event loop.
                file_content = (
                    await asyncio.to_thread(base64.b64decode, new_file_content)
                ).decode(GitlabLiterals.UTF_8.value)
            except Exception as e:
                self.logger.error(
                    f"Failed to decode code file content for {file_path}: {e}"
                )
                file_content = new_file_content
            data = ""
            if is_generated_file:
                data = f"[Generated file] \n\n {file_content} \n\n Diff content \n\n {diff_content}"
            elif is_new_file:
                data = f"[New file] \n\n {file_content} \n\n Diff content \n\n {diff_content}"
            elif is_deleted_file:
                data = f"[Deleted file] \n\n Diff content \n\n {diff_content}"
            else:
                # changes in existing file
                data = f"Existing file \n\n {file_content} \n\n Diff content \n\n {diff_content}"
            if is_truncated_diff:
                data = data + "\n\n[TRUNCATED] Diff"
            file_comments = map_file_r_comments.get(file_path, [])
            comments = [file_comments] if file_comments else []
            bg_n = BlockGroup(
                index=block_group_number,
                name=f"block for file {file_path}",
                type=GroupType.FULL_CODE_PATCH,
                format=DataFormat.MARKDOWN,
                sub_type=GroupSubType.PR_FILE_CHANGE,
                data=data,
                comments=comments,
                requires_processing=True,
            )
            block_groups.append(bg_n)
            block_group_number += 1
        return block_groups, list_remaining_attachments

    async def make_files_records_from_notes(
        self, issue: ProjectIssue, record: Record
    ) -> list[RecordUpdate]:
        """Make file records from notes body of issues."""
        notes_res = await self._ds_call(
            self.data_source.list_issue_notes,
            project_id=int(issue.project_id),
            issue_iid=issue.iid,
            get_all=True,
        )
        if not notes_res.success:
            raise Exception(
                f"❌❌ Failed to fetch notes for issue {issue.title}: {notes_res.error}"
            )
        if not notes_res.data:
            self.logger.debug(f"No notes found for issue {issue.title}")
            return None
        notes = notes_res.data
        record_updates_batch: list[RecordUpdate] = []
        for note in notes:
            note_content = getattr(note, "body", "") or ""
            attachments, _ = await self.parse_gitlab_uploads_clean_test(note_content)
            if attachments:
                file_record_updates = await self.make_file_records_from_list(
                    attachments=attachments, record=record
                )
                if file_record_updates:
                    record_updates_batch.extend(file_record_updates)
                    self.logger.debug(
                        f"Added {len(file_record_updates)} attachments for issue {issue.title}"
                    )
        return record_updates_batch

    async def make_files_records_from_notes_mr(
        self, mr: ProjectMergeRequest, record: Record
    ) -> list[RecordUpdate]:
        """Make file records from notes of merge request"""
        notes_res = await self._ds_call(
            self.data_source.list_merge_request_notes,
            project_id=int(mr.project_id),
            mr_iid=mr.iid,
            get_all=True,
        )
        if not notes_res.success:
            raise Exception(
                f"❌❌ Failed to fetch notes for merge request {mr.title}: {notes_res.error}"
            )
        if not notes_res.data:
            self.logger.debug(f"No notes found for merge request {mr.title}")
            return None
        notes = notes_res.data
        record_updates_batch: list[RecordUpdate] = []
        for note in notes:
            note_content = getattr(note, "body", "") or ""
            attachments, _ = await self.parse_gitlab_uploads_clean_test(note_content)
            if attachments:
                file_record_updates = await self.make_file_records_from_list(
                    attachments=attachments, record=record
                )
                if file_record_updates:
                    record_updates_batch.extend(file_record_updates)
                    self.logger.debug(
                        f"Added {len(file_record_updates)} attachments for merge request {mr.title}"
                    )
        return record_updates_batch

    # ---------------------------Pull Requests-----------------------------------#

    async def _fetch_prs_batched(self, project_id: int) -> None:
        """Syncing merge requests in batches based on sync point of last sync time"""
        last_sync_time = await self._get_mr_sync_checkpoint(project_id)
        if last_sync_time is not None:
            since_dt = datetime.fromtimestamp(last_sync_time / 1000, tz=timezone.utc)
        else:
            since_dt = None
        filter_after, filter_before = self._datetime_range_from_sync_filter(
            SyncFilterKey.MODIFIED
        )
        if filter_after is not None:
            if since_dt is None:
                since_dt = filter_after
            else:
                since_dt = max(since_dt, filter_after)
        updated_before = filter_before
        created_after, created_before = self._datetime_range_from_sync_filter(
            SyncFilterKey.CREATED
        )
        prs_res = await self._ds_call(
            self.data_source.list_merge_requests,
            project_id=project_id,
            updated_after=since_dt,
            updated_before=updated_before,
            created_after=created_after,
            created_before=created_before,
            order_by=GitlabLiterals.UPDATED_AT.value,
            sort="asc",
            get_all=True,
        )
        if not prs_res.success:
            self.logger.error(
                f"Error fetching merge requests for projectId {project_id}: {prs_res.error}"
            )
            return
        if not prs_res.data:
            self.logger.debug(f"No merge requests found for projectId {project_id}")
            return

        all_prs: list[ProjectMergeRequest] = prs_res.data
        total_prs = len(all_prs)
        self.logger.info(
            f"📦 Fetched {total_prs} merge requests, processing in batches..."
        )
        batch_size = self.batch_size
        batch_number = 0
        for i in range(0, total_prs, batch_size):
            batch_number += 1
            prs_batch = all_prs[i : i + batch_size]
            batch_records: list[RecordUpdate] = []
            self.logger.debug(
                f"📦 Processing batch {batch_number}: {len(prs_batch)} merge requests"
            )
            batch_records = await self._build_pr_records(prs_batch)
            # send batch results to process
            await self._process_new_records(batch_records)

    async def _build_pr_records(
        self, prs_batch: list[ProjectMergeRequest]
    ) -> list[RecordUpdate]:
        """Make merge requests of gitlab projects into PullRequestRecords"""
        record_updates_batch: list[RecordUpdate] = []
        attachments_count = 0
        # See _build_issue_records: indexing filters only suppress indexing,
        # not sync. Records still flow through so the graph stays complete.
        mrs_enabled = self._merge_requests_indexing_enabled()
        comments_enabled = self._comments_indexing_enabled()
        for pr in prs_batch:
            record_update = await self._process_mr_to_pull_request(pr)
            if record_update:
                if not mrs_enabled:
                    record_update.record.indexing_status = (
                        ProgressStatus.AUTO_INDEX_OFF.value
                    )
                record_updates_batch.append(record_update)
                # get the file attachments from mr data
                # make file records for all except images
                markdown_content_raw: str = getattr(pr, "description", "") or ""
                (
                    attachments,
                    markdown_content,
                ) = await self.parse_gitlab_uploads_clean_test(markdown_content_raw)
                self.logger.debug(f"Processed markdown content for mr {pr.title}")
                if attachments:
                    file_record_updates = await self.make_file_records_from_list(
                        attachments=attachments, record=record_update.record
                    )
                    if file_record_updates:
                        if not mrs_enabled:
                            for ru in file_record_updates:
                                ru.record.indexing_status = (
                                    ProgressStatus.AUTO_INDEX_OFF.value
                                )
                        record_updates_batch.extend(file_record_updates)
                        attachments_count += len(file_record_updates)
                # adding notes attachments — always sync the records so they
                # exist in the graph; when the COMMENTS (or parent
                # MERGE_REQUESTS) indexing filter is off we flip
                # indexing_status to AUTO_INDEX_OFF so they are not indexed.
                attachment_records = await self.make_files_records_from_notes_mr(
                    pr, record_update.record
                )
                if attachment_records:
                    if not mrs_enabled or not comments_enabled:
                        for ru in attachment_records:
                            ru.record.indexing_status = (
                                ProgressStatus.AUTO_INDEX_OFF.value
                            )
                    record_updates_batch.extend(attachment_records)
                    attachments_count += len(attachment_records)
        self.logger.debug(f"Added {attachments_count} attachments for merge requests ")
        return record_updates_batch

    async def _process_mr_to_pull_request(
        self, pr: ProjectMergeRequest
    ) -> RecordUpdate | None:
        """Process merge request to pull request record"""
        try:
            # check if record already exists
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{pr.id}"
                )
            # detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            if existing_record:
                # TODO: add more changes especially body ones as of now default fallback to full body reindexing
                # check if title changed
                if existing_record.record_name != pr.title:
                    metadata_changed = True
                    is_updated = True
                # TODO: body changes check as of now True default
                content_changed = True
                is_updated = True

            label_names: list[str] = []
            for label in pr.labels:
                label_names.append(label)
            assignee_list: list[str] = [
                assignees.get("username") for assignees in pr.assignees
            ]
            reviewer_names: list[str] = [
                reviewers.get("username") for reviewers in pr.reviewers
            ]
            merged_by: str = pr.merged_by.get("username") if pr.merged_by else None
            external_group_id = f"{pr.project_id}-merge-requests"
            merge_request_record = PullRequestRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=pr.title,
                external_record_id=str(pr.id),
                record_type=RecordType.PULL_REQUEST.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=parse_timestamp(pr.updated_at),
                source_created_at=parse_timestamp(pr.created_at),
                version=0,  # not used further so 0
                external_record_group_id=external_group_id,
                org_id=self.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=pr.web_url,
                status=pr.state,
                external_revision_id=str(parse_timestamp(pr.updated_at)),
                preview_renderable=False,
                mergeable=pr.merge_status,
                labels=label_names,
                inherit_permissions=True,
                assignee=assignee_list,
                merged_by=merged_by,
                review_name=reviewer_names,
            )
            return RecordUpdate(
                record=merge_request_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                old_permissions=[],
                new_permissions=[],
                external_record_id=str(pr.id),
            )
        except Exception as e:
            self.logger.error(
                f"❌❌ Error in processing merge request to pull request: {e}",
                exc_info=True,
            )
            raise

    async def _build_pull_request_blocks(self, record: Record) -> bytes:
        raw_url = getattr(record, "weburl", "") or ""
        if not raw_url:
            raise ValueError("Web URL is required for indexing merge request")
        raw_url = raw_url.split("/")
        mr_number = int(raw_url[7])
        external_group_id = getattr(record, "external_record_group_id")
        project_id = external_group_id.split("-")[0]
        if not external_group_id:
            raise Exception("❌❌ Project id not found.")
        mr_res = await self._ds_call(
            self.data_source.get_merge_request, project_id=project_id, mr_iid=mr_number
        )
        if not mr_res.success:
            raise Exception(
                f"❌❌ Failed to fetch merge request details for record {record.external_record_id}: {mr_res.error}"
            )

        if not mr_res.data:
            raise Exception(
                f"❌❌ No merge request data found for record {record.external_record_id}"
            )
        # TODO: when personal hosting base urls might be different
        base_project_url = f"{self._gitlab_base_url}/api/v4/projects/{project_id}"
        block_group_number = 0
        block_number = 0
        blocks: list[Block] = []
        block_groups: list[BlockGroup] = []
        list_remaining_attachments: list[RecordUpdate] = []
        mr = mr_res.data
        markdown_content_raw: str = getattr(mr, "description", "") or ""
        markdown_with_images_base64 = await self.embed_images_as_base64(
            markdown_content_raw, base_project_url
        )
        markdown_content_with_title = f"{mr.title}\n\n{markdown_with_images_base64}"
        (
            list_child_records,
            remaining_attachments,
        ) = await self.make_child_records_of_attachments(markdown_content_raw, record)
        list_remaining_attachments.extend(remaining_attachments)
        # bg of title and description of mr
        bg_0 = BlockGroup(
            index=block_group_number,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.CONTENT.value,
            source_group_id=record.weburl,
            data=markdown_content_with_title,
            source_modified_date=string_to_datetime(mr.updated_at),
            requires_processing=True,
            children_records=list_child_records,
        )
        self.logger.debug(
            f"block group for title and description created for merge request {mr_number}"
        )
        block_groups.append(bg_0)
        # make blocks of merge request comments and file wise review comments
        if self._comments_indexing_enabled():
            (
                comments_bg,
                remaining_attachments,
            ) = await self._build_merge_request_comment_blocks(
                mr_url=record.weburl, parent_index=block_group_number, record=record
            )
            block_groups.extend(comments_bg)
            block_group_number += len(comments_bg)
            list_remaining_attachments.extend(remaining_attachments)
        # list commits of mr
        mr_commits_res = await self._ds_call(
            self.data_source.list_merge_requests_commits,
            project_id=project_id,
            mr_iid=mr_number,
            get_all=True,
        )
        if not mr_commits_res.success:
            raise Exception(
                f"❌❌ Failed to fetch commits for merge request {mr_number}: {mr_commits_res.error}"
            )
        if not mr_commits_res.data:
            self.logger.debug(f"No commits found for merge request {mr_number}")
        mr_commits: list[ProjectCommit] = mr_commits_res.data
        for commit in mr_commits:
            commit_message = getattr(commit, "message", "")
            commit_title = getattr(commit, "title", "")
            commit_web_url = getattr(commit, "web_url", "")
            commit_id = getattr(commit, "id", "")
            commit_committed_date = getattr(commit, "committed_date", "")
            block = Block(
                index=block_number,
                parent_index=block_group_number,
                type=BlockType.TEXT.value,
                sub_type=BlockSubType.COMMIT.value,
                weburl=commit_web_url,
                format=DataFormat.MARKDOWN,
                data=commit_message,
                source_id=commit_id,
                name=commit_title,
                source_creation_date=string_to_datetime(commit_committed_date),
            )
            block_number += 1
            blocks.append(block)
        bg_new = BlockGroup(
            index=block_group_number,
            name="block group for commits",
            type=GroupType.COMMITS,
            description=f"List of commits for merge request : {mr_number}",
        )
        block_groups.append(bg_new)
        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        self.logger.debug(f"block and groups created for merge request {mr_number}")
        await self._process_new_records(list_remaining_attachments)
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode(GitlabLiterals.UTF_8.value)

    # ---------------------------Attachment functions-----------------------------------#

    EXTENSION_TO_MIME: dict[str, str] = {
        "png": "png",
        "jpg": "jpeg",
        "jpeg": "jpeg",
        "gif": "gif",
        "webp": "webp",
        "bmp": "bmp",
        "svg": "svg+xml",
    }

    async def embed_images_as_base64(
        self, body_content: str, base_project_url: str
    ) -> str:
        """
        getting raw markdown content, then getting images as base64 and appending in markdown content
        """
        self.logger.debug(
            "Embedding images as base64 in markdown content in embed_images_as_base64 function"
        )
        (
            attachments,
            markdown_content_clean,
        ) = await self.parse_gitlab_uploads_clean_test(body_content)
        if not attachments:
            return markdown_content_clean
        # Skip images larger than 4 MB: base64 encoding inflates size by ~33%,
        # so a 4 MB image produces a ~5.3 MB string embedded in the record —
        # beyond that the storage and indexing cost outweighs the value.
        _MAX_IMAGE_BYTES = 4 * 1024 * 1024
        for attach in attachments:
            if attach.category != GitlabLiterals.IMAGE.value:
                continue
            attachment_url = attach.href
            full_attachment_url = f"{base_project_url}{attachment_url}"
            try:
                response = await self.data_source.get_img_bytes(full_attachment_url)
                if response.success and response.data:
                    if len(response.data) > _MAX_IMAGE_BYTES:
                        self.logger.debug(
                            "Skipping image %s: size %d bytes exceeds limit",
                            attachment_url,
                            len(response.data),
                        )
                        continue
                    fmt = self.EXTENSION_TO_MIME.get(attach.filetype, "png")
                    # Offload CPU-bound base64 encode to the thread pool so
                    # large images don't stall the event loop.
                    raw_b64 = await asyncio.to_thread(base64.b64encode, response.data)
                    base64_data = raw_b64.decode(GitlabLiterals.UTF_8.value)
                    md_image_data = f"![Image](data:image/{fmt};base64,{base64_data})"
                    markdown_content_clean += f"{md_image_data}"
            except Exception as e:
                self.logger.warning(f"Error embedding image from {attachment_url}: {e}")
                continue
        return markdown_content_clean

    async def make_file_records_from_list(
        self, attachments: list[FileAttachment], record: Record
    ) -> list[RecordUpdate]:
        """Building file records from list of attachment links."""
        project_id = record.external_record_group_id.split("-")[0]
        base_url_for_attachments = f"{self._gitlab_base_url}/api/v4/projects/{project_id}"
        list_records_new: list[RecordUpdate] = []
        for attach in attachments:
            if attach.category == GitlabLiterals.IMAGE.value:
                continue
            # creating file record for each attachment
            attachment_url = attach.href
            full_attachment_url = f"{base_url_for_attachments}{attachment_url}"
            attachment_name = attach.filename
            attachment_type = attach.filetype
            self.logger.debug(
                f"Processing attachment: {attachment_name} of type {attachment_type} from URL: {attachment_url}"
            )
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{full_attachment_url}"
                )
            # detect changes
            record_id = str(uuid.uuid4())

            filerecord = FileRecord(
                id=existing_record.id if existing_record else record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=attachment_name,
                record_type=RecordType.FILE.value,
                external_record_id=str(full_attachment_url),
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR,
                weburl=str(full_attachment_url),
                record_group_type=RecordGroupType.PROJECT.value,
                parent_external_record_id=record.external_record_id,
                parent_record_type=record.record_type,
                external_record_group_id=record.external_record_group_id,
                mime_type=getattr(
                    MimeTypes, attachment_type.upper(), MimeTypes.UNKNOWN
                ).value,
                extension=attachment_type.lower(),
                is_file=True,
                inherit_permissions=True,
                preview_renderable=True,
                version=0,
                size_in_bytes=0,  # unknown
                source_created_at=get_epoch_timestamp_in_ms(),
                source_updated_at=get_epoch_timestamp_in_ms(),
            )

            record_update = RecordUpdate(
                record=filerecord,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                old_permissions=[],
                new_permissions=[],
                external_record_id=full_attachment_url,
            )
            list_records_new.append(record_update)

        return list_records_new

    async def _fetch_attachment_content(
        self, record: Record
    ) -> AsyncGenerator[bytes, None]:
        """stream attachment file content"""
        try:
            attachment_id = record.external_record_id
            if not attachment_id:
                raise Exception(f"No attachment ID available for record {record.id}")
            # make call to fetch attachment content
            record_url = record.weburl
            if not record_url:
                raise ValueError(f"No record URL available for record {record.id}")
            async for chunk in self.data_source.get_attachment_files_content(
                record_url
            ):
                yield chunk
        except Exception as e:
            raise Exception(
                f"Error fetching attachment content for record {record.id}: {e}"
            ) from e

    async def make_child_records_of_attachments(
        self, markdown_raw: str, record: Record
    ) -> tuple[list[ChildRecord], list[RecordUpdate]]:
        """make child records of attachments from markdown raw content"""
        attachments, markdown_content = await self.parse_gitlab_uploads_clean_test(
            markdown_raw
        )
        child_records: list[ChildRecord] = []
        remaining_attachments: list[RecordUpdate] = []
        project_id = record.external_record_group_id.split("-")[0]
        base_url_for_attachments = f"{self._gitlab_base_url}/api/v4/projects/{project_id}"
        for attach in attachments:
            if attach.category == GitlabLiterals.IMAGE.value:
                continue
            attachment_url = attach.href
            full_attachment_url = f"{base_url_for_attachments}{attachment_url}"
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{full_attachment_url}"
                )
            if existing_record:
                child_record = ChildRecord(
                    child_id=existing_record.id,
                    child_type=ChildType.RECORD,
                    child_name=existing_record.record_name,
                )
                child_records.append(child_record)
            else:
                remaining_attachment = await self.make_file_records_from_list(
                    [attach], record
                )
                remaining_attachments.extend(remaining_attachment)
                if remaining_attachment:
                    child_record = ChildRecord(
                        child_id=remaining_attachment[0].record.id,
                        child_type=ChildType.RECORD,
                        child_name=remaining_attachment[0].record.record_name,
                    )
                    child_records.append(child_record)
        return child_records, remaining_attachments

    async def make_block_comment_of_attachments(
        self, markdown_raw: str, record: Record
    ) -> tuple[list[CommentAttachment], list[RecordUpdate]]:
        """make comment attachments from markdown raw content for merge request review comments"""
        attachments, markdown_content = await self.parse_gitlab_uploads_clean_test(
            markdown_raw
        )
        comment_attachments: list[CommentAttachment] = []
        remaining_attachments: list[RecordUpdate] = []
        project_id = record.external_record_group_id.split("-")[0]
        base_url_for_attachments = f"{self._gitlab_base_url}/api/v4/projects/{project_id}"
        for attach in attachments:
            if attach.category == GitlabLiterals.IMAGE.value:
                continue
            attachment_url = attach.href
            full_attachment_url = f"{base_url_for_attachments}{attachment_url}"
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=f"{full_attachment_url}"
                )
            if existing_record:
                comment_attachment = CommentAttachment(
                    name=existing_record.record_name,
                    id=existing_record.id,
                )
                comment_attachments.append(comment_attachment)
            else:
                remaining_attachment = await self.make_file_records_from_list(
                    [attach], record
                )
                remaining_attachments.extend(remaining_attachment)
                if remaining_attachment:
                    comment_attachment = CommentAttachment(
                        name=remaining_attachment[0].record.record_name,
                        id=remaining_attachment[0].record.id,
                    )
                    comment_attachments.append(comment_attachment)
        return comment_attachments, remaining_attachments

    # ---------------------------insitu functions-----------------------------------#

    async def get_signed_url(self, record: Record) -> str | None:
        """Get signed URL for record access (optional - if API supports it)."""

        return None

    async def parse_gitlab_uploads_clean_test(
        self, text: str
    ) -> tuple[list[FileAttachment], str]:
        """
        Parses markdown content and returns cleaned markdown with images and attachments
        Returns:
            list[FileAttachment]: List of file attachments
            str: Cleaned markdown content
        """

        if not isinstance(text, str):
            return [], ""

        files = []
        cleaned_text = text

        matches = list(UPLOAD_PATTERN.finditer(text))

        for match in matches:
            full_match = match.group("full")
            href = match.group("href")
            filename = unquote(match.group("filename"))

            # Safety check for malformed filename
            if "." not in filename or filename.endswith("."):
                extension = "txt"
            else:
                extension = filename.rsplit(".", 1)[-1].lower()

            category = (
                GitlabLiterals.IMAGE.value
                if extension in IMAGE_EXTENSIONS
                else GitlabLiterals.ATTACHMENT.value
            )

            try:
                files.append(
                    FileAttachment(
                        href=href,
                        filename=filename,
                        filetype=extension,
                        category=category,
                    )
                )
            except Exception as e:
                self.logger.warning(
                    f"Skipping malformed attachment missing required fields: {e}"
                )
                continue

            # Remove from markdown
            cleaned_text = cleaned_text.replace(full_match, "")

        # Remove extra blank lines caused by removal
        cleaned_text = re.sub(r"\n\s*\n+", "\n\n", cleaned_text).strip()

        return files, cleaned_text

    def get_parent_path_from_path(self, file_path: str) -> list[str] | None:
        """Cleans and removes file name from path and returns it."""
        if not file_path:
            return []
        file_path_list = file_path.split("/")
        file_path_list.pop()
        return file_path_list

    async def handle_webhook_notification(self) -> bool:
        """Handle webhook notifications (optional - for real-time sync)."""
        return True

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        """Dynamic options for GitLab group and repository filters."""
        del cursor  # GitLab options use offset pagination only
        await self._refresh_token_if_needed()
        if not self.data_source:
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message="GitLab connector not initialized",
            )
        try:
            if filter_key == SyncFilterKey.GROUP_IDS.value:
                return await self._gitlab_group_filter_options(page, limit, search)
            if filter_key == SyncFilterKey.PROJECT_IDS.value:
                return await self._gitlab_project_filter_options(page, limit, search)
            raise ValueError(f"Unsupported filter key: {filter_key}")
        except ValueError:
            raise
        except Exception as e:
            self.logger.error(f"get_filter_options failed for {filter_key}: {e}", exc_info=True)
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(e),
            )
    # GitLab caps ``per_page`` at 100; clamp here so we never silently
    # truncate when callers ask for a larger page size.
    _FILTER_OPTIONS_MAX_PER_PAGE = 100
    _FILTER_OPTIONS_MAX_SCAN_PAGES = 20

    # GitLab's REST ``search=`` parameter is backed by
    # ``Gitlab::SQL::Pattern`` which only switches to substring matching
    # (``LIKE %term%``) when the query is at least 3 characters long;
    # shorter queries collapse to exact-match on name/path and silently
    # return ``[]``. That makes the picker look broken on the very first
    # keystroke (e.g. typing ``p`` against a group named ``pipeshub-ai``
    # returns nothing). Mirror the typeahead UX users expect by handling
    # short queries client-side instead of forwarding them to GitLab.
    _GITLAB_SEARCH_MIN_PARTIAL_CHARS = GITLAB_SEARCH_MIN_PARTIAL_CHARS

    @staticmethod
    def _clamp_per_page(limit: int) -> int:
        """Clamp UI-supplied limit into GitLab's per_page range."""
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 20
        if n <= 0:
            n = 20
        # Leave headroom for the +1 overfetch trick below.
        return min(n, GitLabConnector._FILTER_OPTIONS_MAX_PER_PAGE - 1)

    @classmethod
    def _is_short_search(cls, search: str | None) -> bool:
        """True when ``search`` is non-empty but below GitLab's partial-match threshold."""
        if search is None:
            return False
        return 0 < len(search) < cls._GITLAB_SEARCH_MIN_PARTIAL_CHARS

    @classmethod
    def _short_search_filter_options_response(
        cls, page: int, limit: int
    ) -> FilterOptionsResponse:
        return FilterOptionsResponse(
            success=True,
            options=[],
            page=page,
            limit=limit,
            has_more=False,
            message=(
                f"Type at least {cls._GITLAB_SEARCH_MIN_PARTIAL_CHARS} "
                "characters to search"
            ),
        )

    @staticmethod
    def _local_match_group(g: object, needle: str) -> bool:
        """Case-insensitive substring match on a Group's name and full_path."""
        name = (getattr(g, "name", None) or "")
        path = (getattr(g, "full_path", None) or "")
        return needle in name.casefold() or needle in path.casefold()

    @staticmethod
    def _local_match_project(p: object, needle: str) -> bool:
        """Case-insensitive substring match on a Project's name and path_with_namespace."""
        path = (getattr(p, "path_with_namespace", None) or "")
        name = (
            getattr(p, "name_with_namespace", None)
            or getattr(p, "name", None)
            or ""
        )
        return needle in name.casefold() or needle in path.casefold()

    async def _scan_filter_option_pages(
        self,
        method: Callable[..., GitLabResponse],
        /,
        *args: object,
        list_kwargs: dict[str, object],
        matcher: Callable[[object], bool],
        page: int,
        per_page: int,
        progress_label: str,
    ) -> tuple[list[object], bool, str | None]:
        """Scan paged GitLab option results until the requested match page exists.

        Search option lookups often need local post-filtering because GitLab's
        own ``search`` semantics differ across endpoints and query lengths.
        Filtering only one upstream page hides matches in large tenants, so
        this walks pages until it has one extra local match for ``has_more`` or
        GitLab returns a short final page.
        """
        target_count = (max(1, int(page)) * per_page) + 1
        matched: list[object] = []
        upstream_page = 1
        while True:
            kwargs = dict(list_kwargs)
            kwargs["page"] = upstream_page
            kwargs["per_page"] = GitLabConnector._FILTER_OPTIONS_MAX_PER_PAGE
            res = await self._ds_call(method, *args, **kwargs)
            if not res.success:
                return [], False, res.error
            items = list(res.data or [])
            matched.extend(item for item in items if matcher(item))
            if len(matched) >= target_count:
                break
            if len(items) < GitLabConnector._FILTER_OPTIONS_MAX_PER_PAGE:
                break
            if upstream_page >= GitLabConnector._FILTER_OPTIONS_MAX_SCAN_PAGES:
                self.logger.debug(
                    "%s: stopped after %s GitLab page(s), local matches=%s",
                    progress_label,
                    upstream_page,
                    len(matched),
                )
                break
            upstream_page += 1
            self.logger.debug(
                "%s: scanned %s GitLab page(s), local matches=%s",
                progress_label,
                upstream_page - 1,
                len(matched),
            )

        start = (max(1, int(page)) - 1) * per_page
        end = start + per_page
        return matched[start:end], len(matched) > end, None

    async def _gitlab_group_filter_options(
        self, page: int, limit: int, search: str | None
    ) -> FilterOptionsResponse:
        search = search or None  # treat empty string same as no search
        # Show every group the user has at least Guest access to. ``owned=True``
        # would hide groups where the user is only a Reporter/Developer/Maintainer.
        per_page = self._clamp_per_page(limit)
        # Overfetch by 1 to detect ``has_more`` without a second roundtrip;
        # GitLab disables ``X-Total`` headers on large instances, so we
        # cannot rely on totals.
        #
        # Two GitLab gotchas drive the request shape below:
        # 1. ``/groups`` rejects ``order_by=path`` whenever ``search`` is
        #    set on many self-managed EE deployments — it silently
        #    returns ``[]``. Drop ``order_by``/``sort`` on searched calls
        #    so GitLab uses its own default (``similarity`` when search
        #    is set).
        # 2. ``search`` requires at least 3 characters for partial
        #    (substring) matching; below that GitLab does exact match on
        #    name/path. Below the threshold we drop ``search`` from the
        #    upstream call and substring-filter client-side instead.
        # For any non-empty search: page through GitLab and filter locally.
        # This ensures namespace-path matches and case variations on
        # self-managed EE instances are caught consistently.  For short
        # queries (<3 chars) GitLab's ``search=`` does exact-match only, so
        # we drop it from the API call; for longer queries we keep it for
        # server-side pre-filtering efficiency.
        too_short = self._is_short_search(search)
        if too_short:
            return self._short_search_filter_options_response(page, limit)
        server_search = search
        # ``_list_groups_scope_kwargs`` picks the right scope flag:
        # regular members get ``min_access_level=10`` (membership-only,
        # avoids the public/internal-group leak in the picker),
        # admins / EE Auditors get ``all_available=True`` so their
        # role-accessible groups actually show up in the dropdown
        # (otherwise the membership filter returns [] for them).
        list_kwargs: dict[str, object] = {
            "search": server_search,
            "get_all": False,
            **self._list_groups_scope_kwargs(),
        }
        if not server_search:
            list_kwargs["order_by"] = "path"
            list_kwargs["sort"] = "asc"
        if search:
            needle = search.casefold()
            groups, has_more, error = await self._scan_filter_option_pages(
                self.data_source.list_groups,
                list_kwargs=list_kwargs,
                matcher=lambda g: self._local_match_group(g, needle),
                page=page,
                per_page=per_page,
                progress_label="GitLab group filter search",
            )
            if error:
                self.logger.warning(
                    "GitLab list_groups failed for filter options "
                    "(search=%r, page=%s): %s",
                    search,
                    page,
                    error,
                )
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=error,
                )
            # Auditor-empty fallback: primary scope returned 0 matches.
            # Retry with membership scope and walk descendants so the
            # auditor's explicit Reporter+ groups *and* any subgroups
            # whose access flows by inheritance both surface in the
            # dropdown. See ``_list_groups_scope_kwargs`` for the
            # GitLab known issue and ``_expand_groups_with_descendants``
            # for the inheritance gap closed here.
            if self._is_auditor and not groups:
                groups, has_more, error = (
                    await self._gitlab_group_picker_auditor_fallback(
                        list_kwargs=list_kwargs,
                        needle=needle,
                        page=page,
                        per_page=per_page,
                    )
                )
                if error:
                    self.logger.warning(
                        "GitLab list_groups auditor fallback failed for "
                        "filter options (search=%r, page=%s): %s",
                        search,
                        page,
                        error,
                    )
        else:
            list_kwargs["page"] = max(1, int(page))
            list_kwargs["per_page"] = per_page + 1
            res = await self._ds_call(self.data_source.list_groups, **list_kwargs)
            if not res.success:
                self.logger.warning(
                    "GitLab list_groups failed for filter options "
                    "(search=%r, page=%s): %s",
                    search,
                    page,
                    res.error,
                )
                return FilterOptionsResponse(
                    success=False,
                    options=[],
                    page=page,
                    limit=limit,
                    has_more=False,
                    message=res.error,
                )
            groups = list(res.data or [])
            has_more = len(groups) > per_page
            if has_more:
                groups = groups[:per_page]
            # Auditor-empty fallback: primary returned 0 rows on this
            # page. Retry with membership scope and walk descendants —
            # see ``_list_groups_scope_kwargs`` and
            # ``_expand_groups_with_descendants``.
            if self._is_auditor and not groups:
                groups, has_more, error = (
                    await self._gitlab_group_picker_auditor_fallback(
                        list_kwargs=list_kwargs,
                        needle=None,
                        page=page,
                        per_page=per_page,
                    )
                )
                if error:
                    self.logger.warning(
                        "GitLab list_groups auditor fallback failed for "
                        "filter options (search=%r, page=%s): %s",
                        search,
                        page,
                        error,
                    )
        opts = [
            FilterOption(
                id=str(g.full_path),
                # Show the namespace path so the user can see *why* a row
                # matched their query — the matcher checks both ``name``
                # and ``full_path``, but rendering only ``name`` made
                # subgroups under e.g. ``testing/`` look unrelated when
                # searching ``test`` (substring of "testing"). ``full_name``
                # is python-gitlab's hierarchical display (``Parent / Child``)
                # and aligns this picker with the project picker below.
                label=str(
                    getattr(g, "full_name", None) or g.full_path or g.name
                ),
            )
            for g in groups
        ]
        return FilterOptionsResponse(
            success=True,
            options=opts,
            page=page,
            limit=limit,
            has_more=has_more,
        )

    async def _gitlab_group_picker_auditor_fallback(
        self,
        *,
        list_kwargs: dict[str, object],
        needle: str | None,
        page: int,
        per_page: int,
    ) -> tuple[list[Any], bool, str | None]:
        """Auditor-only group picker fallback with descendant expansion.

        The primary ``all_available=True`` scope returned zero rows
        (documented GitLab known issue — see
        ``_list_groups_scope_kwargs``). Re-fetch with
        ``min_access_level=10`` to surface the auditor's explicit
        Reporter+ memberships, walk each one's descendant_groups so
        subgroups whose access flows by inheritance also appear, then
        locally filter and paginate.

        Drops any pagination keys from ``list_kwargs`` because we sweep
        the entire fallback set across pages here — the auditor's
        explicit-membership set is small by definition (else the
        primary scope would not have come back empty), so the
        fan-out is bounded.
        """
        self._warn_auditor_fallback_once("groups")
        fallback_kwargs = dict(
            self._picker_kwargs_for_auditor_groups_fallback(list_kwargs)
        )
        # Strip pagination keys: the descendant expansion needs every
        # fallback group, and we paginate locally below after merging.
        for k in ("page", "per_page", "get_all"):
            fallback_kwargs.pop(k, None)
        base_res = await self._paged_list(
            self.data_source.list_groups,
            progress_label="GitLab group filter [auditor fallback]",
            **fallback_kwargs,
        )
        if not base_res.success:
            return [], False, base_res.error
        base_groups = list(base_res.data or [])
        # Walk descendants for every base group, dedupe, then locally
        # filter the merged set so an ancestor that fails the search
        # still anchors a walk that surfaces a matching subgroup.
        merged = await self._expand_groups_with_descendants(
            base_groups,
            progress_label="GitLab group filter [auditor descendants]",
        )
        if needle:
            merged = [g for g in merged if self._local_match_group(g, needle)]
        merged.sort(
            key=lambda g: (getattr(g, "full_path", "") or "").casefold()
        )
        start = (max(1, int(page)) - 1) * per_page
        end = start + per_page
        return merged[start:end], len(merged) > end, None

    async def _gitlab_project_filter_options(
        self, page: int, limit: int, search: str | None
    ) -> FilterOptionsResponse:
        search = search or None  # treat empty string same as no search
        scope_paths: list[str] = [
            p
            for p in getattr(self, "_request_filter_context_group_paths", None) or []
            if p and str(p).strip()
        ]
        exclude_paths: list[str] = [
            p
            for p in getattr(
                self, "_request_filter_context_exclude_group_paths", None
            )
            or []
            if p and str(p).strip()
        ]
        per_page = self._clamp_per_page(limit)
        page_n = max(1, int(page))
        if self._is_short_search(search):
            return self._short_search_filter_options_response(page, limit)

        if scope_paths and self.data_source:
            # Scoped groups: fetch each group in parallel and merge.
            # We never pass the search term to the API here because
            # ``list_group_projects search=`` only matches project *names*.
            # Typing a group-name fragment (e.g. "frontend-team") would
            # silently return nothing even though every project under that
            # group is relevant. Filtering locally against
            # ``path_with_namespace`` catches name and namespace matches and
            # handles case variations on self-managed EE instances.
            async def _fetch(gp: str) -> tuple[str, GitLabResponse]:
                gp_kwargs: dict[str, object] = {
                    "include_subgroups": True,
                    "get_all": False,
                    "page": page_n,
                    "per_page": per_page + 1,
                    "simple": True,
                    "order_by": "path",
                    "sort": "asc",
                }
                gres = await self._ds_call(
                    self.data_source.list_group_projects,
                    gp,
                    **gp_kwargs,
                )
                return gp, gres

            by_id: dict[int, Project] = {}
            any_has_more = False
            if search:
                needle = search.casefold()

                async def _fetch_matches(gp: str) -> tuple[str, list[Project]]:
                    group_matches: list[Project] = []
                    upstream_page = 1
                    while True:
                        gp_kwargs = {
                            "include_subgroups": True,
                            "get_all": False,
                            "page": upstream_page,
                            "per_page": GitLabConnector._FILTER_OPTIONS_MAX_PER_PAGE,
                            "simple": True,
                            "order_by": "path",
                            "sort": "asc",
                        }
                        gres = await self._ds_call(
                            self.data_source.list_group_projects,
                            gp,
                            **gp_kwargs,
                        )
                        if not gres.success:
                            self.logger.warning(
                                f"Could not list projects for group {gp} "
                                f"(filter options): {gres.error}"
                            )
                            break
                        items = list(gres.data or [])
                        group_matches.extend(
                            p for p in items if self._local_match_project(p, needle)
                        )
                        if len(items) < GitLabConnector._FILTER_OPTIONS_MAX_PER_PAGE:
                            break
                        if (
                            upstream_page
                            >= GitLabConnector._FILTER_OPTIONS_MAX_SCAN_PAGES
                        ):
                            self.logger.debug(
                                "GitLab group project filter search: stopped "
                                "group %s after %s GitLab page(s), "
                                "local matches=%s",
                                gp,
                                upstream_page,
                                len(group_matches),
                            )
                            break
                        upstream_page += 1
                    return gp, group_matches

                results = await asyncio.gather(
                    *(_fetch_matches(gp) for gp in scope_paths)
                )
                for _gp, matches in results:
                    for p in matches:
                        by_id[int(p.id)] = p
            else:
                results = await asyncio.gather(*(_fetch(gp) for gp in scope_paths))
                for gp, gres in results:
                    if not gres.success:
                        self.logger.warning(
                            f"Could not list projects for group {gp} "
                            f"(filter options): {gres.error}"
                        )
                        continue
                    items = list(gres.data or [])
                    if len(items) > per_page:
                        any_has_more = True
                        items = items[:per_page]
                    for p in items:
                        by_id[int(p.id)] = p
            projects = list(by_id.values())
            projects.sort(key=lambda p: (p.path_with_namespace or "").lower())
            start = (page_n - 1) * per_page if search else 0
            end = start + per_page
            if len(projects) > end:
                any_has_more = True
            projects = projects[start:end]
            # Locally-filtered result from a bounded fetch; no reliable
            # next-page signal unless we scanned through all selected groups.
            has_more = any_has_more
        else:
            # Unscoped picker. Scope flag selection:
            #
            # Regular member: ``min_access_level=10`` (Guest+). We do
            # NOT use ``membership=True`` here because on some GitLab
            # versions ``membership`` silently omits Guest-level
            # projects, so users who are only readers of a repo would
            # never see it in the picker.
            #
            # Admin / EE Auditor: drop the scope flag entirely so the
            # API default ("all visible projects") applies. Both
            # ``membership=True`` and ``min_access_level=10`` collapse
            # to ``[]`` for them because their cross-instance read
            # access flows from a user-level flag (``is_admin`` /
            # ``is_auditor``), not from a per-project access_level row.
            # On a self-managed instance this surfaces every project
            # the Auditor can read, which is exactly the set they should
            # be able to scope the sync against.
            #
            # ``simple=True`` returns the smaller project payload which
            # is enough for the picker (id + path_with_namespace + name).
            # ``search_namespaces=True`` widens GitLab's project search
            # to match against the namespace (group) path in addition to
            # the project name — without it, typing a group name returns
            # nothing.
            server_search = search
            if exclude_paths and not search:
                # Exclusion is a client-side filter; overfetch so we still
                # have a full page after dropping excluded projects.
                fetch_page = page_n
                fetch_per_page = min(
                    GitLabConnector._FILTER_OPTIONS_MAX_PER_PAGE,
                    per_page * 2 + 1,
                )
            else:
                fetch_page = page_n
                fetch_per_page = per_page + 1
            # Picker uses ``min_access_level=10`` for regular members
            # (the Guest-omission workaround above) rather than the
            # ``membership=True`` that the sync paths use. Admins /
            # auditors still need the wider scope — same dispatch as
            # ``_list_projects_scope_kwargs`` but with the picker-
            # specific Guest+ default for regular members.
            if self._is_admin or self._is_auditor:
                proj_scope: dict[str, object] = {}
            else:
                proj_scope = {"min_access_level": 10}
            proj_kwargs: dict[str, object] = {
                "search": server_search,
                "get_all": False,
                "simple": True,
                **proj_scope,
            }
            if server_search:
                # Match project name AND namespace path server-side.
                proj_kwargs["search_namespaces"] = True
            if not server_search:
                # ``order_by=path`` silently returns [] when combined with
                # ``search=`` on many self-managed EE deployments; safe here
                # because we only reach this branch with no active search.
                proj_kwargs["order_by"] = "path"
                proj_kwargs["sort"] = "asc"
            if search:
                # Local post-filter across pages: catches namespace-path
                # matches that the API missed and handles case variations on
                # EE without hiding matches beyond the first GitLab page.
                needle = search.casefold()
                projects, has_more, error = await self._scan_filter_option_pages(
                    self.data_source.list_projects,
                    list_kwargs=proj_kwargs,
                    matcher=lambda p: self._local_match_project(p, needle)
                    and not self._namespace_under_any_prefix(
                        self._namespace_full_path(p), exclude_paths
                    ),
                    page=page_n,
                    per_page=per_page,
                    progress_label="GitLab project filter search",
                )
                if error:
                    self.logger.warning(
                        "GitLab list_projects failed for filter options "
                        "(search=%r, page=%s): %s",
                        search,
                        page,
                        error,
                    )
                    return FilterOptionsResponse(
                        success=False,
                        options=[],
                        page=page,
                        limit=limit,
                        has_more=False,
                        message=error,
                    )
                # Auditor-empty fallback for the search path. See
                # ``_list_groups_scope_kwargs`` for the GitLab known issue.
                if self._is_auditor and not projects:
                    self._warn_auditor_fallback_once("projects")
                    fb_kwargs = (
                        self._picker_kwargs_for_auditor_projects_fallback(
                            proj_kwargs
                        )
                    )
                    projects, has_more, error = await self._scan_filter_option_pages(
                        self.data_source.list_projects,
                        list_kwargs=fb_kwargs,
                        matcher=lambda p: self._local_match_project(p, needle)
                        and not self._namespace_under_any_prefix(
                            self._namespace_full_path(p), exclude_paths
                        ),
                        page=page_n,
                        per_page=per_page,
                        progress_label="GitLab project filter search [auditor fallback]",
                    )
                    if error:
                        self.logger.warning(
                            "GitLab list_projects auditor fallback failed for "
                            "filter options (search=%r, page=%s): %s",
                            search,
                            page,
                            error,
                        )
            else:
                proj_kwargs["page"] = fetch_page
                proj_kwargs["per_page"] = fetch_per_page
                res = await self._ds_call(
                    self.data_source.list_projects, **proj_kwargs
                )
                if not res.success:
                    self.logger.warning(
                        "GitLab list_projects failed for filter options "
                        "(search=%r, page=%s): %s",
                        search,
                        page,
                        res.error,
                    )
                    return FilterOptionsResponse(
                        success=False,
                        options=[],
                        page=page,
                        limit=limit,
                        has_more=False,
                        message=res.error,
                    )
                projects = list(res.data or [])
                raw_count = len(projects)
                # Auditor-empty fallback for the unsearched path. Run
                # before the exclude-paths filter so we don't conflate
                # "primary actually returned 0" with "primary returned
                # rows that were all excluded". See
                # ``_list_groups_scope_kwargs`` for the GitLab known issue.
                if self._is_auditor and raw_count == 0:
                    self._warn_auditor_fallback_once("projects")
                    fb_kwargs = (
                        self._picker_kwargs_for_auditor_projects_fallback(
                            proj_kwargs
                        )
                    )
                    fb_res = await self._ds_call(
                        self.data_source.list_projects, **fb_kwargs
                    )
                    if fb_res.success:
                        projects = list(fb_res.data or [])
                        raw_count = len(projects)
                if exclude_paths:
                    projects = [
                        p
                        for p in projects
                        if not self._namespace_under_any_prefix(
                            self._namespace_full_path(p), exclude_paths
                        )
                    ]
                # ``has_more`` is true whenever upstream returned a full
                # over-fetched batch, even if local exclusion trimmed the
                # page — there may still be more matching projects later.
                has_more = raw_count >= fetch_per_page
                if len(projects) > per_page:
                    projects = projects[:per_page]

        opts = [
            FilterOption(
                id=str(p.path_with_namespace),
                label=str(
                    getattr(p, "name_with_namespace", None) or p.path_with_namespace
                ),
            )
            for p in projects
        ]
        return FilterOptionsResponse(
            success=True,
            options=opts,
            page=page,
            limit=limit,
            has_more=has_more,
        )

    async def cleanup(self) -> None:
        """
        Cleanup resources used by the connector.

        ``_gitlab_executor.shutdown(wait=False, cancel_futures=True)``:
        we do *not* block on in-flight worker threads. python-gitlab's
        per-request ``timeout=30`` will let any sockets unwind on
        their own; a ``wait=True`` shutdown could itself hang the
        cleanup path on the same stuck ``recv()`` we built the
        per-batch timeout to escape from. ``cancel_futures=True``
        drops any queued (not yet started) work.
        """
        self.logger.info("Cleaning up GitLab connector resources.")
        await self._cancel_code_file_timestamp_backfill()
        if self.data_source is not None:
            try:
                await self.data_source.aclose()
            except Exception as e:
                self.logger.warning("Failed to close GitLab HTTP client: %s", e)
        self.data_source = None
        try:
            self._gitlab_executor.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            self.logger.warning(
                "GitLab executor shutdown raised; ignoring: %s", e
            )

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
        """
        Factory method to create a Gitlab connector instance.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider for database operations
            config_service: Configuration service for accessing credentials

        Returns:
            Initialized GitLabConnector instance
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return GitLabConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
