import asyncio
import base64
import inspect
import json
import re
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
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
            self.logger.info(
                f"Gitlab connector initialized successfully (instance: {self._gitlab_base_url})."
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Gitlab client: {e}", exc_info=True)
            return False

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
    ) -> GitLabResponse:
        """Run a GitLab data-source op without blocking the event loop."""
        if inspect.iscoroutinefunction(op):

            async def _async_op() -> GitLabResponse:
                return await op()

            return await _async_op()

        def _invoke_sync_op() -> GitLabResponse:
            outcome = op()
            if inspect.isawaitable(outcome):
                raise RuntimeError(
                    "GitLab sync op returned a coroutine; use _ds_call_async instead."
                )
            return outcome

        return await asyncio.to_thread(_invoke_sync_op)

    async def _call_with_auth_retry(
        self,
        op: Callable[[], GitLabResponse | Awaitable[GitLabResponse]],
    ) -> GitLabResponse:
        """Run a GitLab data-source op; on a 401-style failure, refresh the OAuth
        token once and retry. Accepts both sync- and async-returning ops.
        """
        response = await self._execute_gitlab_op(op)
        if not self._is_auth_error(response):
            return response

        self.logger.info(
            "GitLab API returned auth error; refreshing OAuth token and retrying once."
        )
        if not await self._force_refresh_oauth_token():
            return response

        return await self._execute_gitlab_op(op)

    async def _ds_call(
        self,
        method: Callable[..., GitLabResponse],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> GitLabResponse:
        """Run a synchronous GitLabDataSource method with OAuth retry on 401."""

        def op() -> GitLabResponse:
            return method(*args, **kwargs)

        return await self._call_with_auth_retry(op)

    async def _ds_call_async(
        self,
        method: Callable[..., Awaitable[GitLabResponse]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> GitLabResponse:
        """Run an async GitLabDataSource method (e.g. GraphQL) with OAuth retry."""

        async def op() -> GitLabResponse:
            return await method(*args, **kwargs)

        return await self._call_with_auth_retry(op)

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

        # ``_drain`` mutates ``items`` directly so a mid-batch exception
        # does NOT discard the items already pulled in that batch. We
        # surface (done, error) and let the caller log + decide. Without
        # this, a network glitch on page 47 would erase progress from
        # pages 1-46 because the worker-thread's local ``out`` list goes
        # out of scope when ``next()`` raises.
        items: list[Any] = []

        def _drain(it: Any, out: list[Any], n: int) -> tuple[bool, Exception | None]:
            for _ in range(n):
                try:
                    out.append(next(it))
                except StopIteration:
                    return True, None
                except Exception as e:  # noqa: BLE001 — surface upstream
                    return True, e
            return False, None

        while True:
            done, err = await asyncio.to_thread(
                _drain, paged_iter, items, progress_every
            )
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

    async def run_sync(self) -> None:
        """syncing various entities"""
        try:
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

        - ``GROUP_IDS`` / ``PROJECT_IDS`` ``IN``: returns the configured
          paths verbatim and skips any tenant-wide list call.
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

        if grp_active and grp_op == FilterOperator.IN:
            group_targets = list(grp_f.value)  # type: ignore[arg-type]
        elif grp_active and grp_op == FilterOperator.NOT_IN:
            excluded = list(grp_f.value)  # type: ignore[arg-type]
            groups_res = await self._paged_list(
                self.data_source.list_groups,
                min_access_level=10,
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
            projects_res = await self._paged_list(
                self.data_source.list_projects,
                membership=True,
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
        """Walk members of explicitly-configured groups/projects only."""
        dict_member: dict[int, GroupMember] = {}
        total_groups_synced = 0
        total_groups_skipped = 0
        total_projects_synced = 0
        total_projects_skipped = 0
        any_success = False

        for i, group_path in enumerate(group_paths, start=1):
            try:
                self.logger.info(
                    "syncing users for configured group %s/%s (%s)",
                    i,
                    len(group_paths),
                    group_path,
                )
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
                    continue
                any_success = True
                for member in members_res.data or []:
                    dict_member[member.id] = member
                total_groups_synced += 1
            except Exception as e:
                self.logger.error(
                    f"Error in syncing users for group {group_path}: {e}",
                    exc_info=True,
                )
                continue

        for i, project_path in enumerate(project_paths, start=1):
            try:
                self.logger.info(
                    "syncing users for configured project %s/%s (%s)",
                    i,
                    len(project_paths),
                    project_path,
                )
                members_res = await self._ds_call(
                    self.data_source.list_project_members_all,
                    project_id=project_path,
                    get_all=True,
                )
                if not members_res.success:
                    self.logger.error(
                        f"Error fetching members for configured project {project_path}: "
                        f"{members_res.error}"
                    )
                    total_projects_skipped += 1
                    continue
                any_success = True
                for member in members_res.data or []:
                    dict_member[member.id] = member
                total_projects_synced += 1
            except Exception as e:
                self.logger.error(
                    f"Error in syncing users for project {project_path}: {e}",
                    exc_info=True,
                )
                continue

        # Mirror the unscoped path: if every configured target failed
        # this run we must not let downstream reconciliation tombstone
        # active users on the next pass.
        if (group_paths or project_paths) and not any_success:
            raise RuntimeError(
                "GitLab user sync aborted: every configured group/project "
                "failed to enumerate members"
            )

        self.logger.info(
            f"Total groups synced: {total_groups_synced}, Total groups skipped: {total_groups_skipped}"
        )
        self.logger.info(
            f"Total projects synced: {total_projects_synced}, Total projects skipped: {total_projects_skipped}"
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
        groups_res = await self._paged_list(
            self.data_source.list_groups,
            min_access_level=10,
            per_page=100,
            progress_label="list_groups (min_access_level=10)",
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

        # Keyset pagination keeps per-page cost constant on
        # /projects?membership=true. Drive the iterator so we get
        # per-page progress logs — on busy instances this is the second
        # most common spot for a Puma-timeout 502.
        projects_res = await self._paged_list(
            self.data_source.list_projects,
            membership=True,
            pagination="keyset",
            order_by="id",
            sort="asc",
            per_page=100,
            progress_label="list_projects (membership=True)",
        )
        projects_failed = not projects_res.success
        if projects_failed:
            self.logger.error(f"Error in fetching projects: {projects_res.error}")
        # If both the groups call and the projects call fail we have no
        # source of truth for membership this run. Persisting an empty
        # member set would silently mark every user inactive on the next
        # reconciliation pass, so fail loudly and let the sync be retried.
        if groups_failed and projects_failed:
            raise RuntimeError(
                "GitLab user sync aborted: both list_groups and list_projects "
                f"failed (groups: {groups_res.error}; projects: {projects_res.error})"
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
        Batched ``asyncio.gather`` limits concurrent outbound calls.
        """
        batch_size = 20

        async def fetch_full_user(member_id: int, member: GroupMember) -> tuple[int, Any]:
            try:
                user_res = await self._ds_call(
            self.data_source.get_user,
                    member_id,
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
        items = list(dict_member.items())
        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            results = await asyncio.gather(
                *[fetch_full_user(mid, mem) for mid, mem in batch]
            )
            for member_id, user_obj in results:
                enriched[member_id] = user_obj

        self.logger.info("Enriched %s GitLab members with full user objects", len(enriched))
        return enriched

    async def _sync_users_from_projects_groups(
        self, dict_member: dict[int, Any]
    ) -> None:
        """Create AppUsers from projects and groups."""
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

    async def _ensure_gitlab_group_record_groups(self, group_paths: list[str]) -> None:
        """Create top-level GitLab group record groups before project groups reference them.

        Group members (including inherited members from parent groups) are attached as
        USER permissions on the group RecordGroup so that the knowledge-hub browse view
        (`_get_app_children_subquery` -> `_get_permission_role_aql`) admits the user at
        the group level. Without this, the group node has no PERMISSION edges, the app
        drilldown filters it out, and every project under it becomes unreachable in the
        browse tree even though the project_record_group beneath has its own permissions.
        """
        if not self.data_source:
            return
        self.logger.info(f"Ensuring GitLab group record groups for {group_paths}")
        for group_path in group_paths:
            group_res = await self._ds_call(
                self.data_source.get_group, group_path
            )
            if not group_res.success or not group_res.data:
                self.logger.error(
                    f"GitLab group not found or inaccessible: {group_path} ({group_res.error})"
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
                    f"Could not list members for GitLab group {group_path}: "
                    f"{members_res.error}. Group node will be created without "
                    f"member permissions and may not be visible in the browse view."
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

        - ``IN`` filters are additive: include every project under any
          configured group AND every explicitly listed project. Previously
          ``PROJECT_IDS`` short-circuited and silently dropped every
          group-scoped project on the floor, while the user-sync walker
          still treated both filters as a union — that asymmetry created
          ``AppUser`` rows whose projects never synced.
        - ``NOT_IN`` filters are subtractive: from the candidate set,
          drop any project whose path is excluded OR whose namespace is
          under an excluded group prefix.

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

        if grp_in or proj_in:
            # Allow-list mode: union of group projects + explicit
            # projects. Skip the tenant-wide /projects scan entirely.
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
        else:
            # No IN filter: start from every membership project, then
            # apply NOT_IN exclusions below. Raise on failure so the
            # next reconciliation pass doesn't tombstone every record.
            res = await self._paged_list(
                self.data_source.list_projects,
                membership=True,
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
            await self._ensure_gitlab_group_record_groups(included_group_paths)
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
            groups_res = await self._paged_list(
                self.data_source.list_groups,
                min_access_level=10,
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

    async def _sync_repo_main(self, project_id: int, project_path: str) -> None:
        """Sync default branch files code.
        PROCESS: 1. Sync all folders level wise via paginated graphql api.
                 2. Sync all code repo. files via paginated graphql api.
        REASON:  both can be in same api call but pagination to be separate.
                 level wise files ordering not needed
        """
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
            if not page_info.get("hasNextPage"):
                break
            after_cursor = page_info.get("endCursor", "")
            if not after_cursor:
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
        after_cursor = ""
        while True:
            try:
                tree_res = await self._ds_call_async(
                    self.data_source.get_file_tree_g,
                    project_id=project_path,
                    ref="HEAD",
                    after_cursor=after_cursor,
                )
            except Exception as e:
                self.logger.error(
                    f"Error in fetching file tree skipping repo code files sync for {project_id}: {e}"
                )
                return
            if not tree_res.success:
                self.logger.error(
                    f"❌❌ Error in fetching file tree skipping repo code files sync for {project_id}: {tree_res.error}"
                )
                return
            if not tree_res.data:
                self.logger.info(f"❌❌ No file tree found for project {project_id}")
                return
            try:
                data: dict[str, Any] = json.loads(tree_res.data)
            except json.JSONDecodeError as e:
                self.logger.error(
                    f"❌ Failed to parse file tree JSON for {project_id}: {e}"
                )
                return
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
                    f"(empty repo, missing scope, or archived); skipping code files sync"
                )
                return
            project_nodes = paginated_tree.get("nodes") or []
            page_info = paginated_tree.get("pageInfo") or {}
            if not project_nodes:
                self.logger.info(f"No project nodes found for project {project_id}")
                return
            t_nodes: dict[str, Any] = project_nodes[0]
            file_path_nodes: list[dict[str, Any]] = (
                (t_nodes.get("blobs") or {}).get("nodes") or []
            )
            if file_path_nodes:
                self.logger.debug(
                    f"❗❗ Files fetched via GQL: {len(file_path_nodes)} "
                )
                await self.build_code_file_records(
                    file_path_nodes, project_id, project_path
                )
            if not page_info.get("hasNextPage"):
                self.logger.debug("✅✅ No more code file pages left, exiting")
                break
            after_cursor = page_info.get("endCursor", "")
            if not after_cursor:
                break

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
            decoded_bytes = base64.b64decode(content_b64)
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
                ("code", lambda: self._sync_repo_main(project_id, project_path)),
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
        if not members_res.success:
            self.logger.error(f"❌❌Error in fetching members for project {project_id}")
            return
        if not members_res.data:
            self.logger.info(f"No members found for project {project_id} ")
            return
        members = members_res.data
        for member in members:
            dict_member[member.id] = member
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
        # creating record group for issues to inherit permissions
        work_items_record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name="Work items",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=f"{project.id}-work-items",  # not a valid group id externally
            parent_external_group_id=str(project.id),
        )
        self.logger.info("Creating work items record group")
        merge_requests_record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name="Merge requests",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=f"{project.id}-merge-requests",  # not a valid group id externally
            parent_external_group_id=str(project.id),
        )
        code_repo_record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name="Code repository",
            group_type=RecordGroupType.PROJECT.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            external_group_id=f"{project.id}-code-repository",  # not a valid group id externally
            parent_external_group_id=str(project.id),
        )
        await self.data_entities_processor.on_new_record_groups(
            [
                (project_record_group, permission_project_level),
                (work_items_record_group, permission_work_items_level),
                (code_repo_record_group, permission_code_repo_level),
                (merge_requests_record_group, permission_merge_requests_level),
            ]
        )
        self.logger.info("Synced Permissions for all levels.")

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
        return

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
                # Decode base64 content from Gitlab API else add encoded content
                file_content = base64.b64decode(new_file_content).decode(
                    GitlabLiterals.UTF_8.value
                )
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
        for attach in attachments:
            if attach.category != GitlabLiterals.IMAGE.value:
                continue
            attachment_url = attach.href
            full_attachment_url = f"{base_project_url}{attachment_url}"
            try:
                response = await self.data_source.get_img_bytes(full_attachment_url)
                if response.success and response.data:
                    fmt = self.EXTENSION_TO_MIME.get(attach.filetype, "png")
                    base64_data = base64.b64encode(response.data).decode(
                        GitlabLiterals.UTF_8.value
                    )
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
        server_search = None if too_short else search
        list_kwargs: dict[str, object] = {
            "search": server_search,
            "min_access_level": 10,
            "get_all": False,
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
        opts = [
            FilterOption(id=str(g.full_path), label=str(g.name or g.full_path))
            for g in groups
        ]
        return FilterOptionsResponse(
            success=True,
            options=opts,
            page=page,
            limit=limit,
            has_more=has_more,
        )

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
                for gp in scope_paths:
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
                        for p in items:
                            if self._local_match_project(p, needle):
                                by_id[int(p.id)] = p
                        if len(items) < GitLabConnector._FILTER_OPTIONS_MAX_PER_PAGE:
                            break
                        upstream_page += 1
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
            elif search:
                projects = projects[start:end]
            # Locally-filtered result from a bounded fetch; no reliable
            # next-page signal unless we scanned through all selected groups.
            has_more = any_has_more
        else:
            # Unscoped: all membership projects.
            # ``simple=True`` returns the smaller project payload which is
            # enough for the picker (id + path_with_namespace + name).
            # ``search_namespaces=True`` widens GitLab's project search to
            # match against the namespace (group) path in addition to the
            # project name — without it, typing a group name returns nothing.
            # For short queries (<3 chars) GitLab does exact-match only, so
            # we drop ``search`` from the API call and filter client-side.
            too_short = self._is_short_search(search)
            server_search = None if too_short else search
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
            proj_kwargs: dict[str, object] = {
                "search": server_search,
                "membership": True,
                "get_all": False,
                "simple": True,
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
        """
        self.logger.info("Cleaning up GitLab connector resources.")
        self.data_source = None

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
