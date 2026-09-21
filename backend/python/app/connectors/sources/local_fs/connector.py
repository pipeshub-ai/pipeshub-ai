"""
Local FS connector — personal scope, local folder watched by the desktop app.

``run_sync`` is server-driven like every other connector: it pulls pages of
file-event *metadata* from the Electron desktop over an RPC relayed by the
Node service, and applies them. No file bytes cross this path — content is
fetched on demand in ``stream_record``. The server process never crawls
``sync_root_path`` itself.

Full vs incremental is derived from the sync point, per the platform
convention: no ``last_sync_time`` means a completed baseline does not exist
yet, so the next run is FULL.

Sync settings accept ``batchSize`` (preferred) or ``batch_size`` in etcd.
"""

import asyncio
import hashlib
import json
import mimetypes
import time
import unicodedata
import uuid
from logging import Logger
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import JsonValue

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorSyncSkippedError,
)
from app.connectors.core.constants import ConnectorErrorCodes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    internal_service_status,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    CustomField,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterOperator,
    FilterOptionsResponse,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import (
    NotificationSeverity,
    NotificationType,
)
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.jwt import generate_jwt
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

from .models import (
    LocalFsFileEvent,
    LocalFsFileEventBatchStats,
    LocalFsPullBatch,
)

# Canonical API / CLI connector type string (must match pipeshub-cli backend_client).
LOCAL_FS_CONNECTOR_NAME = "Local FS"
LOCAL_FS_ICON_PATH = "/icons/connectors/local-fs.png"
FULL_SYNC_RESET_BATCH_SIZE = 500
# How many owed deletions a sync point will carry. Two prune batches is far
# more than a healthy folder produces; past it the failures are systemic, and
# a full run — which retires everything it does not see — is the better
# remedy than an ever-growing list in the sync point.
LOCAL_FS_MAX_PENDING_DELETIONS = 2 * FULL_SYNC_RESET_BATCH_SIZE

# Sync config keys (flat under config["sync"] — same as RSS/Web custom fields).
SYNC_ROOT_PATH_KEY = "sync_root_path"
INCLUDE_SUBFOLDERS_KEY = "include_subfolders"
LOCAL_FS_STORAGE_PATH_PREFIX = "storage://"
# No total timeout on storage reads/writes for Local FS: large desktop sync
# payloads and slow links are expected (aligns with Node batch proxy timeout: 0).
LOCAL_FS_STORAGE_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=None)
LOCAL_FS_STORAGE_DELETE_TIMEOUT_SECONDS = 30

# --- Desktop pull RPC ---------------------------------------------------
LOCAL_FS_PULL_ROUTE = "/api/v1/desktop/internal/local-fs/file-events/pull"
LOCAL_FS_CONTENT_ROUTE = "/api/v1/desktop/internal/local-fs/content"
LOCAL_FS_DESKTOP_SCOPE = "desktop:command"
# Budget handed to the desktop. Each outer layer allows strictly more so a
# hang surfaces as a meaningful DESKTOP_TIMEOUT from Node rather than an
# opaque client-side abort: desktop 60s < Node emitWithAck 65s < us 90s.
LOCAL_FS_PULL_DESKTOP_BUDGET_MS = 60_000
LOCAL_FS_PULL_HTTP_TIMEOUT_SECONDS = 90
# Content gets its own, much larger budget: the pull numbers are sized for a
# page of metadata, and a large file streamed in 256KB frames blows straight
# through them. Same strict layering (desktop 180s < Node 185s < us 210s).
LOCAL_FS_CONTENT_DESKTOP_BUDGET_MS = 180_000
LOCAL_FS_CONTENT_HTTP_TIMEOUT_SECONDS = 210
# generate_jwt defaults to a 1h expiry and both a full sync and a connector
# instance parked in connectors_map outlive it.
LOCAL_FS_SERVICE_TOKEN_TTL_SECONDS = 45 * 60
# stream_record resolves the owner per indexed file; a short TTL spares the
# graph read without pinning a stale owner for long.
LOCAL_FS_OWNER_DEVICE_TTL_SECONDS = 60
LOCAL_FS_PULL_MAX_ATTEMPTS = 3
LOCAL_FS_PULL_RETRY_BASE_SECONDS = 2
# Runaway-run guards: a desktop that keeps answering "nothing yet, ask again"
# must not spin forever.
LOCAL_FS_MAX_EMPTY_BATCHES = 20
LOCAL_FS_MAX_RUN_SECONDS = 6 * 3600
LOCAL_FS_MAX_BATCHES_PER_RUN = 100_000
# The desktop streams file bytes over the socket relay, so stream_record can
# serve content and records are safe to hand to the indexing pipeline.
LOCAL_FS_DESKTOP_CONTENT_AVAILABLE = True

# Shown to whoever owns the connector when a run could not retire every record
# it meant to. Plain words and a next step: no ids, no exception text.
SOME_CLEANUP_FAILED = (
    "Some files that are no longer in your folder could not be removed from search "
    "({failed} of {attempted}). Everything else synced. PipesHub will try again on "
    "the next sync."
)
ALL_CLEANUP_FAILED = (
    "Files that are no longer in your folder could not be removed from search. "
    "Everything else synced. If the next sync does not clear them, ask your admin "
    "to check this connector's access."
)


class LocalFsRecordCleanupError(Exception):
    """A run finished its work but could not remove every retired record.

    Raised at the end of the run, not where the delete failed: one record the
    graph refuses must not cost the whole sync (that is what made a refused
    delete read as "Sync finished ... total time: 0.0s"). The run is still
    recorded as failed, because records for files that are gone are left
    behind, and the ids are carried in the sync point for the next run.
    """

    def __init__(self, failed: int, attempted: int) -> None:
        self.failed = failed
        self.attempted = attempted
        self.user_message = (
            ALL_CLEANUP_FAILED
            if attempted and failed >= attempted
            else SOME_CLEANUP_FAILED.format(failed=failed, attempted=attempted)
        )
        super().__init__(f"{failed} of {attempted} record deletion(s) failed")


class LocalFsRecordUnreadableError(Exception):
    """A record could not be read, so nothing can be concluded about it.

    The graph answers None both for a record that is not there and for a read
    that failed, and every delete path goes through that same lookup. Rather
    than guess, the id is reported as still owed: a deletion tried once more
    next run costs little, and retiring a record because a read failed cannot
    be undone.
    """

    def __init__(self, external_id: str) -> None:
        self.external_id = external_id
        super().__init__(f"could not read record {external_id}")


class LocalFsDesktopError(Exception):
    """The desktop agent could not serve a pull."""


class LocalFsDesktopOfflineError(ConnectorSyncSkippedError, LocalFsDesktopError):
    """No desktop is connected for this connector (Node answered 409)."""

    def __init__(self, message: str) -> None:
        super().__init__(ConnectorErrorCodes.DESKTOP_OFFLINE, message)


class LocalFsDesktopTimeoutError(LocalFsDesktopError):
    """The desktop did not answer within its budget (Node answered 504)."""


class LocalFsDesktopRemoteError(LocalFsDesktopError):
    """The desktop answered, but with a failure (Node answered 502)."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class LocalFsRootUnavailableError(LocalFsDesktopRemoteError):
    """The configured sync folder is gone or unreadable on the desktop.

    Terminal for this run. Indexed records stay: relative paths are still
    valid if the user points the connector at the new location.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, retryable=False)


class LocalFsDesktopUnreachableError(LocalFsDesktopRemoteError):
    """Every retry of a page failed for a reason other than "no socket".

    Distinct from ``LocalFsDesktopOfflineError`` because that one is a skip: a
    closed laptop is expected and must not notify on every scheduled run. A
    desktop that is connected but keeps timing out, or a relay answering 502,
    is a real fault the user can act on, so it is reported with the code that
    actually failed.
    """

    def __init__(self, attempts: int, last_error: Optional["LocalFsDesktopError"]) -> None:
        code = getattr(last_error, "code", None) or (
            "DESKTOP_TIMEOUT"
            if isinstance(last_error, LocalFsDesktopTimeoutError)
            else "DESKTOP_ERROR"
        )
        super().__init__(
            code,
            f"Desktop did not serve the page after {attempts} attempts: {last_error}",
            retryable=False,
        )
        self.attempts = attempts


class LocalFsDeviceUnclaimedError(LocalFsDesktopRemoteError):
    """No device owns this connector yet, so no machine can be asked for files.

    Reachable when sync was enabled outside the desktop app, or on a connector
    left active by a build that predates the owner field.
    """

    def __init__(self) -> None:
        super().__init__(
            ConnectorErrorCodes.DESKTOP_UNCLAIMED,
            "This connector has no owner device.",
            retryable=False,
        )


class LocalFsDeviceMismatchError(LocalFsDesktopRemoteError):
    """A machine other than the connector's owner device answered the pull.

    Node routes pulls to the owner's socket, so this is a backstop. Applying the
    page anyway would let the wrong machine's full run prune everything the
    owner synced.
    """

    def __init__(
        self, expected_device_id: str, actual_device_id: Optional[str]
    ) -> None:
        super().__init__(
            "DEVICE_MISMATCH",
            (
                f"Connector is owned by device {expected_device_id}, but device "
                f"{actual_device_id or 'unknown'} answered."
            ),
            retryable=False,
        )
        self.expected_device_id = expected_device_id
        self.actual_device_id = actual_device_id


def _get_datetime_filter_bounds_ms(
    fl: Filter,
) -> Tuple[Optional[int], Optional[int]]:
    if isinstance(fl.value, tuple):
        start, end = fl.value
        return (
            int(start) if start is not None else None,
            int(end) if end is not None else None,
        )
    after_iso, before_iso = fl.get_datetime_iso()
    return (
        parse_timestamp(after_iso) if after_iso else None,
        parse_timestamp(before_iso) if before_iso else None,
    )


def _event_file_time_ms(event: LocalFsFileEvent) -> int:
    """File mtime when the desktop sent one, else the observed event time.

    The fallback keeps pre-upgrade desktops and journaled replays working; for
    those, live events carry wall-clock rather than a file time.
    """
    if event.mtimeMs is not None and event.mtimeMs > 0:
        return int(event.mtimeMs)
    return int(event.timestamp)


def _event_created_time_ms(event: LocalFsFileEvent) -> int:
    """Inode birth time when the platform reported a usable one, else mtime.

    Linux reports either ctime or epoch 0 where the filesystem has no btime,
    so a non-positive value means "unavailable", not 1970.
    """
    if event.birthtimeMs is not None and event.birthtimeMs > 0:
        return int(event.birthtimeMs)
    return _event_file_time_ms(event)


def _get_sync_config_value(
    sync_cfg: JsonValue,
    key: str,
    default: JsonValue = None,
) -> JsonValue:
    """Read Local FS sync values from flat, values, or customValues config shapes."""
    if not isinstance(sync_cfg, dict):
        return default
    raw = sync_cfg.get(key)
    if raw is not None and raw != "":
        return raw
    for nested_key in ("values", "customValues"):
        nested = sync_cfg.get(nested_key)
        if isinstance(nested, dict):
            raw = nested.get(key)
            if raw is not None and raw != "":
                return raw
    return default


def _parse_sync_batch_size(sync_cfg: JsonValue) -> int:
    """Parse sync batch size with support for legacy and current key names."""
    raw = _get_sync_config_value(sync_cfg, "batchSize")
    if raw is None or raw == "":
        raw = _get_sync_config_value(sync_cfg, "batch_size", "50")
    try:
        return max(1, int(str(raw).strip() or "50"))
    except (TypeError, ValueError):
        return 50


def _parse_sync_bool(raw: JsonValue, default: bool) -> bool:
    """Parse Local FS sync boolean settings from bools/strings with default fallback."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return default


def _parse_sync_settings(
    config: Dict[str, JsonValue] | None,
) -> Tuple[str, bool, int]:
    """Return sync root path, include_subfolders flag, and batch size."""
    sync_cfg = (config or {}).get("sync", {}) or {}
    root = str(_get_sync_config_value(sync_cfg, SYNC_ROOT_PATH_KEY, "") or "").strip()
    include = _parse_sync_bool(
        _get_sync_config_value(sync_cfg, INCLUDE_SUBFOLDERS_KEY, True), True
    )
    return root, include, _parse_sync_batch_size(sync_cfg)


def _client_path_leaf_name(raw: str, *, default: str = "Local FS") -> str:
    """Last component of a desktop path, independent of this process's OS.

    ``sync_root_path`` lives on the watcher machine. Host ``Path`` treats
    ``\\`` as data on POSIX, so a Windows folder would be stored as the
    full path on a Linux connector. ``PureWindowsPath`` splits on both
    ``\\`` and ``/``.
    """
    text = (raw or "").strip()
    if not text:
        return default
    return PureWindowsPath(text).name or text


def _client_path_for_display(raw: str) -> Path:
    """Display Path for a desktop sync root that may not exist on this host."""
    text = (raw or "").strip().replace("\\", "/") or "Local FS"
    return Path(text)


class LocalFsApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.LOCAL_FS, AppGroups.LOCAL_STORAGE, connector_id)


@(
    ConnectorBuilder(LOCAL_FS_CONNECTOR_NAME)
    .in_group(AppGroups.LOCAL_STORAGE.value)
    .with_supported_auth_types("NONE")
    .with_description(
        "Index a folder on the machine running the connector. "
        "Choose a path below, then run manual or scheduled sync—listing as Active alone does not index files."
    )
    .with_info(
        "Scheduled syncs require the PipesHub desktop app to be running and connected. "
        "If your machine is offline, the sync will be skipped and automatically resume "
        "from where it left off when you're back online.\n\n"
        "While PipesHub desktop app is running with connector enabled, "
        "OS may block renaming or moving the synced folders especially on Windows because the desktop app "
        "keeps a watch on it. Turn sync off for that folder's connector, or quit the desktop "
        "app, then try again."
    )
    .with_categories(["Storage", "Local"])
    .with_scopes([ConnectorScope.PERSONAL.value])
    .with_permission_model(PermissionModel.APP_LEVEL)
    .configure(
        lambda builder: builder.with_icon(LOCAL_FS_ICON_PATH)
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "Local FS",
                "https://docs.pipeshub.com/connectors/overview",
                "setup",
            )
        )
        .add_documentation_link(
            DocumentationLink(
                "Pipeshub documentation",
                "https://docs.pipeshub.com",
                "pipeshub",
            )
        )
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL], selected=SyncStrategy.SCHEDULED)
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
        .with_hide_connector(False)
        .add_sync_custom_field(
            CustomField(
                name=SYNC_ROOT_PATH_KEY,
                display_name="Local folder",
                field_type="FOLDER",
                required=True,
                description=(
                    "Choose the folder on this machine to sync."
                ),
            )
        )
        .add_sync_custom_field(
            CustomField(
                name=INCLUDE_SUBFOLDERS_KEY,
                display_name="Include subfolders",
                field_type="BOOLEAN",
                required=False,
                default_value="true",
                description="Recurse into subdirectories when syncing.",
            )
        )
        .add_sync_custom_field(CommonFields.batch_size_field())
        .add_filter_field(
            CommonFields.modified_date_filter(
                "Only sync files modified within this range (optional)."
            )
        )
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(CommonFields.file_extension_filter())
    )
    .build_decorator()
)
class LocalFsConnector(BaseConnector):
    """Local FS: ingest runs on the connector host when the path is readable."""

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
            LocalFsApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_name = Connectors.LOCAL_FS
        self.connector_id = connector_id
        self.sync_root_path: str = ""
        self.include_subfolders: bool = True
        self.batch_size: int = 50
        self._owner_user_for_permissions: Optional[User] = None
        # Seeded here so the memoization in _storage_base_url/_storage_token
        # (which only caches when the attribute already exists) actually takes.
        self._batch_storage_url_cache: Optional[str] = None
        self._batch_storage_token_cache: Optional[str] = None
        self._batch_storage_token_minted_at: float = 0.0
        self._desktop_token_cache: Optional[str] = None
        self._desktop_token_minted_at: float = 0.0
        self._owner_device_cache: tuple[str, str | None] | None = None
        self._owner_device_fetched_at: float = 0.0
        self.record_sync_point = SyncPoint(
            connector_id=connector_id,
            org_id=data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )

    async def init(self) -> bool:
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.warning(
                    "Local FS: no connector config yet; set sync fields in the app or pipeshub setup."
                )
                return True

            root, include_subfolders, batch_size = _parse_sync_settings(config)
            self.sync_root_path = root
            self.include_subfolders = include_subfolders
            self.batch_size = batch_size

            if not root:
                self.logger.info(
                    "Local FS: sync_root_path not configured; complete setup in the app or CLI."
                )
            return True
        except Exception as e:
            self.logger.error("Local FS init failed: %s", e, exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        # The folder lives on the desktop, not this process. Node already
        # refuses toggle when the app is offline; ROOT_MISSING is a pull error.
        return True

    async def get_signed_url(self, record: Record) -> Optional[str]:
        return None

    def _record_group_external_id(self) -> str:
        return f"local_fs:{self.connector_id}"

    def _external_record_id_for_rel_path(self, rel_path: str) -> str:
        # NFC normalization so macOS HFS+/APFS NFD paths and user-space NFC
        # paths hash identically. Without this, a CREATED in NFC followed by a
        # RENAMED whose oldPath chokidar reports in NFD produces two distinct
        # external_record_ids for the same logical file, and the rename
        # silently becomes "delete-old + create-new" with the wrong id.
        normalized = unicodedata.normalize(
            "NFC", rel_path.strip().replace("\\", "/")
        )
        return hashlib.sha256(
            f"{self.connector_id}:{normalized}".encode("utf-8")
        ).hexdigest()

    async def _reload_sync_settings(self) -> None:
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        root, include_subfolders, batch_size = _parse_sync_settings(config)
        self.sync_root_path = root
        self.include_subfolders = include_subfolders
        self.batch_size = batch_size

    @staticmethod
    def _parse_user_from_graph_result(
        raw: User | Dict[str, JsonValue] | None,
    ) -> Optional[User]:
        """Graph providers return user dicts; GraphTransactionStore may type them as User."""
        if raw is None:
            return None
        if isinstance(raw, User):
            return raw
        return User.from_arango_user(raw)

    def _to_app_user(self, user: User) -> AppUser:
        return AppUser(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_user_id=user.id,
            org_id=user.org_id or self.data_entities_processor.org_id,
            email=user.email,
            full_name=user.full_name or user.email,
            is_active=user.is_active if user.is_active is not None else True,
        )

    @staticmethod
    def _pass_extension_filters(
        path: Path,
        sync_filters: FilterCollection,
        is_directory: bool = False,
    ) -> bool:
        if is_directory:
            return True
        extensions_filter = sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        allowed_values = extensions_filter.value
        if not isinstance(allowed_values, list):
            return True

        operator = extensions_filter.get_operator()
        operator_str = operator.value if hasattr(operator, "value") else str(operator)

        file_name = path.name
        if "." in file_name:
            file_extension = file_name.rsplit(".", 1)[-1].lower().lstrip(".")
        else:
            file_extension = None

        # No extension: IN never matches; NOT_IN passes (not in the excluded list).
        if file_extension is None:
            return operator_str == FilterOperator.NOT_IN

        normalized_extensions = [
            ext.lower().lstrip(".") for ext in allowed_values
        ]

        if operator_str == FilterOperator.IN:
            return file_extension in normalized_extensions
        if operator_str == FilterOperator.NOT_IN:
            return file_extension not in normalized_extensions

        return True

    @staticmethod
    def _parent_folder_rel_paths_for_file(rel_path: str) -> List[str]:
        normalized = rel_path.strip().replace("\\", "/").strip("/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) <= 1:
            return []
        return ["/".join(parts[:i]) for i in range(1, len(parts))]

    def _build_folder_record(
        self,
        rel_path: str,
        root: Path,
        external_record_group_id: str,
        event: LocalFsFileEvent,
        owner: Optional[User] = None,
        *,
        is_ancestor_placeholder: bool = False,
    ) -> Tuple[FileRecord, List[Permission]]:
        timestamp_ms = int(event.timestamp)
        if is_ancestor_placeholder:
            # if the folder is a placeholder, we don't have it's event 
            # hence no idea about its timestamps
            file_time_ms = None
            created_time_ms = None
        else:
            file_time_ms = _event_file_time_ms(event)
            created_time_ms = _event_created_time_ms(event)
        normalized_rel_path = rel_path.strip().replace("\\", "/").strip("/")
        parent_rel_path = (
            "/".join(normalized_rel_path.split("/")[:-1])
            if "/" in normalized_rel_path
            else None
        )
        folder_path = (root / normalized_rel_path).resolve(strict=False)
        record_id = str(uuid.uuid4())
        folder_record = FileRecord(
            id=record_id,
            record_name=Path(normalized_rel_path).name,
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE,
            external_record_id=self._external_record_id_for_rel_path(normalized_rel_path),
            external_revision_id=None,
            external_record_group_id=external_record_group_id,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            created_at=timestamp_ms,
            updated_at=timestamp_ms,
            source_created_at=created_time_ms,
            source_updated_at=file_time_ms,
            weburl=None,
            hide_weburl=True,
            is_internal=True,
            parent_external_record_id=(
                self._external_record_id_for_rel_path(parent_rel_path)
                if parent_rel_path
                else None
            ),
            parent_record_type=RecordType.FILE if parent_rel_path else None,
            size_in_bytes=0,
            is_file=False,
            extension=None,
            path=str(folder_path),
            local_fs_relative_path=normalized_rel_path,
            mime_type=MimeTypes.FOLDER.value,
            preview_renderable=False,
        )

        effective_owner = owner or self._owner_user_for_permissions
        perms: List[Permission] = []
        if effective_owner:
            perms.append(
                Permission(
                    email=effective_owner.email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER,
                )
            )
        return folder_record, perms

    def _build_parent_folder_records(
        self,
        rel_path: str,
        root: Path,
        external_record_group_id: str,
        event: LocalFsFileEvent,
        emitted_folder_paths: set[str],
        owner: Optional[User] = None,
    ) -> List[Tuple[FileRecord, List[Permission]]]:
        records: List[Tuple[FileRecord, List[Permission]]] = []
        for folder_rel_path in self._parent_folder_rel_paths_for_file(rel_path):
            if folder_rel_path in emitted_folder_paths:
                continue
            emitted_folder_paths.add(folder_rel_path)
            records.append(
                self._build_folder_record(
                    folder_rel_path,
                    root,
                    external_record_group_id,
                    event,
                    owner=owner,
                    is_ancestor_placeholder=True,
                )
            )
        return records

    def _append_folder_upsert_records(
        self,
        upsert_buffer: List[Tuple[FileRecord, List[Permission]]],
        rel_path: str,
        root: Path,
        external_record_group_id: str,
        event: LocalFsFileEvent,
        emitted_folder_paths: set[str],
        owner: Optional[User] = None,
    ) -> None:
        normalized_rel_path = rel_path.strip().replace("\\", "/").strip("/")
        if not normalized_rel_path:
            return
        upsert_buffer.extend(
            self._build_parent_folder_records(
                normalized_rel_path,
                root,
                external_record_group_id,
                event,
                emitted_folder_paths,
                owner=owner,
            )
        )
        if normalized_rel_path in emitted_folder_paths:
            return
        emitted_folder_paths.add(normalized_rel_path)
        upsert_buffer.append(
            self._build_folder_record(
                normalized_rel_path,
                root,
                external_record_group_id,
                event,
                owner=owner,
            )
        )

    @staticmethod
    def _count_processed_file_records(
        records: List[Tuple[FileRecord, List[Permission]]],
    ) -> int:
        count = 0
        for item in records:
            record = item[0] if isinstance(item, tuple) and item else item
            if getattr(record, "is_file", True):
                count += 1
        return count

    async def _handle_directory_event_for_batch(
        self,
        *,
        event_type: str,
        rel_path: str,
        old_rel_path: str,
        root: Path,
        external_record_group_id: str,
        event: LocalFsFileEvent,
        owner: User,
        upsert_buffer: List[Tuple[FileRecord, List[Permission]]],
        move_buffer: List[Tuple[str, FileRecord, List[Permission]]],
        delete_only_buffer: List[str],
        emitted_folder_paths: set[str],
        flush_upserts,
        flush_moves,
        flush_delete_only,
        batch_size: int,
    ) -> bool:
        """Returns False when the event was skipped (unknown type)."""
        if event_type in {"DIR_CREATED", "CREATED", "MODIFIED"}:
            self._append_folder_upsert_records(
                upsert_buffer,
                rel_path,
                root,
                external_record_group_id,
                event,
                emitted_folder_paths,
                owner=owner,
            )
            if len(upsert_buffer) >= batch_size:
                await flush_upserts()
            return True

        if event_type in {"DIR_DELETED", "DELETED"}:
            delete_only_buffer.append(self._external_record_id_for_rel_path(rel_path))
            if len(delete_only_buffer) >= batch_size:
                await flush_delete_only()
            return True

        if event_type in {"DIR_RENAMED", "DIR_MOVED", "RENAMED", "MOVED"}:
            normalized_rel_path = rel_path.strip().replace("\\", "/").strip("/")
            upsert_buffer.extend(
                self._build_parent_folder_records(
                    normalized_rel_path,
                    root,
                    external_record_group_id,
                    event,
                    emitted_folder_paths,
                    owner=owner,
                )
            )

            old_ext_id: Optional[str] = None
            if old_rel_path:
                new_ext_id = self._external_record_id_for_rel_path(normalized_rel_path)
                candidate_old_ext_id = self._external_record_id_for_rel_path(old_rel_path)
                if candidate_old_ext_id != new_ext_id:
                    old_ext_id = candidate_old_ext_id

            # A move needs its own folder record even if this exact path was
            # already emitted earlier in the batch (e.g. as another event's
            # ancestor folder) — retiring the old vertex takes priority over
            # skipping a redundant re-upsert of identical fields.
            already_emitted = normalized_rel_path in emitted_folder_paths
            if already_emitted and old_ext_id is None:
                if len(upsert_buffer) >= batch_size:
                    await flush_upserts()
                return True
            emitted_folder_paths.add(normalized_rel_path)

            folder_record, folder_perms = self._build_folder_record(
                normalized_rel_path,
                root,
                external_record_group_id,
                event,
                owner=owner,
            )
            if old_ext_id:
                move_buffer.append((old_ext_id, folder_record, folder_perms))
                if len(move_buffer) >= batch_size:
                    await flush_moves()
            else:
                upsert_buffer.append((folder_record, folder_perms))
                if len(upsert_buffer) >= batch_size:
                    await flush_upserts()
            return True

        self.logger.warning(
            "Local FS: skipping unsupported directory event type %s (%s)",
            event_type,
            rel_path,
        )
        return False

    @staticmethod
    def _storage_document_id_from_path(record_path: str | None) -> str | None:
        if not record_path or not record_path.startswith(LOCAL_FS_STORAGE_PATH_PREFIX):
            return None
        document_id = record_path[len(LOCAL_FS_STORAGE_PATH_PREFIX) :].strip()
        return document_id or None

    async def _bulk_get_records_by_external_ids(
        self, external_ids: List[str]
    ) -> Dict[str, Record]:
        """One lookup per id via the processor's record cache/wrapper."""
        result: Dict[str, Record] = {}
        unique_ids = [eid for eid in {*external_ids} if eid]
        if not unique_ids:
            return result
        for ext_id in unique_ids:
            record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=ext_id,
            )
            if record is not None:
                result[ext_id] = record
        return result

    async def _nodejs_base_url(self) -> str:
        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        if isinstance(endpoints, str):
            try:
                endpoints = json.loads(endpoints)
            except json.JSONDecodeError:
                endpoints = {}
        node_url = (
            ((endpoints or {}).get("nodejs") or {}).get("endpoint")
            if isinstance(endpoints, dict)
            else None
        )
        return str(node_url or DefaultEndpoints.NODEJS_ENDPOINT.value).rstrip("/")

    async def _desktop_token(self) -> str:
        now = time.monotonic()
        if (
            self._desktop_token_cache
            and now - self._desktop_token_minted_at
            < LOCAL_FS_SERVICE_TOKEN_TTL_SECONDS
        ):
            return self._desktop_token_cache
        token = await generate_jwt(
            self.config_service,
            {
                "orgId": self.data_entities_processor.org_id,
                "userId": self.created_by,
                "scopes": [LOCAL_FS_DESKTOP_SCOPE],
            },
        )
        self._desktop_token_cache = token
        self._desktop_token_minted_at = now
        return token

    async def _owner_device(self) -> tuple[str, str | None]:
        """``(ownerDeviceId, ownerDeviceName)`` from the app document.

        An unclaimed connector is not cached, so the first enable from the
        desktop takes effect on the next call.
        """
        now = time.monotonic()
        if (
            self._owner_device_cache
            and now - self._owner_device_fetched_at < LOCAL_FS_OWNER_DEVICE_TTL_SECONDS
        ):
            return self._owner_device_cache
        app = await self.data_entities_processor.get_app_by_id(self.connector_id)
        owner_id = (app.owner_device_id or "").strip() if app else ""
        if not owner_id:
            raise LocalFsDeviceUnclaimedError()
        self._owner_device_cache = (owner_id, app.owner_device_name or None)
        self._owner_device_fetched_at = now
        return self._owner_device_cache

    @staticmethod
    def _owning_desktop_offline_message(
        subject: str, owner_device_name: str | None
    ) -> str:
        owner = f'"{owner_device_name}" ' if owner_device_name else ""
        return f"The desktop {owner}that owns this {subject} is not connected"

    @staticmethod
    def _desktop_error_for(
        status: int,
        body: Dict[str, Any],
        context: str,
        *,
        offline_message: str = (
            "The desktop that owns this folder is not connected"
        ),
    ) -> LocalFsDesktopError:
        if status == HttpStatusCode.CONFLICT.value:
            return LocalFsDesktopOfflineError(offline_message)
        if status == HttpStatusCode.GATEWAY_TIMEOUT.value:
            return LocalFsDesktopTimeoutError(f"Desktop did not answer ({context})")
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = str(error.get("code") or body.get("code") or f"HTTP_{status}")
        message = str(error.get("message") or body.get("message") or context)
        if code in ("ROOT_MISSING", "ROOT_UNREADABLE"):
            return LocalFsRootUnavailableError(code, message)
        return LocalFsDesktopRemoteError(
            code,
            message,
            retryable=bool(
                error.get(
                    "retryable",
                    status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                )
            ),
        )

    @staticmethod
    def _device_id_from_error_body(body: Dict[str, Any]) -> Optional[str]:
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        device_id = str(error.get("deviceId") or "").strip()
        return device_id or None

    @staticmethod
    async def _read_json_body(response: aiohttp.ClientResponse) -> Dict[str, Any]:
        try:
            parsed = await response.json(content_type=None)
        except (aiohttp.ClientError, json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def _request_file_event_batch(
        self,
        *,
        run_id: str,
        batch_index: int,
        cursor: Optional[str],
        mode: str,
        session: aiohttp.ClientSession,
        expected_device_id: str,
    ) -> LocalFsPullBatch:
        """Pull one page of file-event metadata from the desktop via Node.

        Retries are deliberately NOT handled here — the caller re-sends the
        same ``(runId, batchIndex)``, which the desktop answers from its
        idempotency cache without advancing. A retry layer inside this method
        would be invisible to that contract.

        ``expected_device_id`` routes the pull: Node relays it only to that
        device's socket. The ack is still checked against it, so a relay bug
        cannot apply another machine's page.
        """
        base_url = await self._nodejs_base_url()
        token = await self._desktop_token()
        request_payload = {
            "connectorId": self.connector_id,
            "deviceId": expected_device_id,
            "runId": run_id,
            "batchIndex": batch_index,
            "mode": mode,
            "cursor": cursor,
            "maxEvents": max(1, self.batch_size),
            "timeoutMs": LOCAL_FS_PULL_DESKTOP_BUDGET_MS,
        }

        try:
            async with session.post(
                f"{base_url}{LOCAL_FS_PULL_ROUTE}",
                json=request_payload,
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                status = response.status
                body = await self._read_json_body(response)
        except asyncio.TimeoutError as exc:
            raise LocalFsDesktopTimeoutError(
                f"Desktop pull timed out after {LOCAL_FS_PULL_HTTP_TIMEOUT_SECONDS}s "
                f"(run={run_id} batch={batch_index})"
            ) from exc
        except aiohttp.ClientError as exc:
            raise LocalFsDesktopRemoteError(
                "NODE_UNREACHABLE",
                f"Could not reach the desktop relay at {base_url}: {exc}",
                retryable=True,
            ) from exc

        if status != HttpStatusCode.SUCCESS.value:
            # Checked before the failure is interpreted: a non-owner usually
            # fails the pull (ROOT_MISSING, CONFIG_MISMATCH) because the owner's
            # folder is not on its disk, which would otherwise mask the mismatch.
            refused_by = self._device_id_from_error_body(body)
            if refused_by and refused_by != expected_device_id:
                raise LocalFsDeviceMismatchError(expected_device_id, refused_by)
            raise self._desktop_error_for(
                status, body, f"run={run_id} batch={batch_index}"
            )

        data = body.get("data")
        if not isinstance(data, dict):
            raise LocalFsDesktopRemoteError(
                "MALFORMED_RESPONSE",
                "Desktop pull response had no data object",
                retryable=False,
            )
        batch = LocalFsPullBatch.model_validate(data)

        # A reply for a different connector or a superseded run must not be
        # applied, whatever socket it came from.
        if batch.connectorId != self.connector_id or batch.runId != run_id:
            raise LocalFsDesktopRemoteError(
                "RESPONSE_MISMATCH",
                (
                    f"Expected connector={self.connector_id} run={run_id}, "
                    f"got connector={batch.connectorId} run={batch.runId}"
                ),
                retryable=False,
            )
        if batch.deviceId != expected_device_id:
            raise LocalFsDeviceMismatchError(expected_device_id, batch.deviceId)
        return batch

    async def _storage_base_url(self) -> str:
        cached = getattr(self, "_batch_storage_url_cache", None)
        if cached is not None:
            return cached
        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        if isinstance(endpoints, str):
            try:
                endpoints = json.loads(endpoints)
            except json.JSONDecodeError:
                endpoints = {}
        storage_url = (
            ((endpoints or {}).get("storage") or {}).get("endpoint")
            if isinstance(endpoints, dict)
            else None
        )
        resolved = str(
            storage_url or DefaultEndpoints.STORAGE_ENDPOINT.value
        ).rstrip("/")
        if hasattr(self, "_batch_storage_url_cache"):
            self._batch_storage_url_cache = resolved
        return resolved

    async def _storage_token(self) -> str:
        now = time.monotonic()
        cached = getattr(self, "_batch_storage_token_cache", None)
        if (
            cached is not None
            and now - getattr(self, "_batch_storage_token_minted_at", 0.0)
            < LOCAL_FS_SERVICE_TOKEN_TTL_SECONDS
        ):
            return cached
        token = await generate_jwt(
            self.config_service,
            {
                "orgId": self.data_entities_processor.org_id,
                "scopes": ["storage:token"],
            },
        )
        if hasattr(self, "_batch_storage_token_cache"):
            self._batch_storage_token_cache = token
            self._batch_storage_token_minted_at = now
        return token

    async def _delete_storage_document(
        self,
        document_id: str | None,
        *,
        storage_url: str | None = None,
        storage_token: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if not document_id:
            return
        timeout = aiohttp.ClientTimeout(
            total=LOCAL_FS_STORAGE_DELETE_TIMEOUT_SECONDS
        )
        try:
            if storage_url is None:
                storage_url = await self._storage_base_url()
            if storage_token is None:
                storage_token = await self._storage_token()
            if session is None:
                async with aiohttp.ClientSession(timeout=timeout) as owned_session:
                    await self._execute_storage_delete_request(
                        owned_session, storage_url, storage_token, document_id,
                    )
            else:
                await self._execute_storage_delete_request(
                    session, storage_url, storage_token, document_id,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # Best-effort: a failed cleanup must not block the delete event
            # from being marked synced; the orphaned blob can be GC'd later.
            self.logger.warning(
                "Local FS: could not delete storage document %s: %s",
                document_id,
                exc,
            )

    async def _execute_storage_delete_request(
        self,
        session: aiohttp.ClientSession,
        storage_url: str,
        storage_token: str,
        document_id: str,
    ) -> None:
        """Execute storage delete request for a specific document id."""
        async with session.delete(
            f"{storage_url}/api/v1/document/internal/{document_id}/",
            headers={"Authorization": f"Bearer {storage_token}"},
        ) as response:
            if response.status >= 400:
                self.logger.warning(
                    "Local FS: storage document delete failed for %s "
                    "(status=%s): %s",
                    document_id,
                    response.status,
                    await response.text(),
                )

    @staticmethod
    def _pass_date_filters(
        event: LocalFsFileEvent, sync_filters: FilterCollection
    ) -> bool:
        """Apply the modified-date sync filter to the event's file mtime.

        A filesystem has no per-file creation date that survives a copy, so
        there is deliberately no created-date filter to pair with this one.
        """
        if event.isDirectory:
            return True
        modified_f = sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_f is None or modified_f.is_empty():
            return True

        after_ms, before_ms = _get_datetime_filter_bounds_ms(modified_f)
        timestamp_ms = _event_file_time_ms(event)
        if after_ms is not None and timestamp_ms < after_ms:
            return False
        if before_ms is not None and timestamp_ms > before_ms:
            return False
        return True

    def _build_file_record(
        self,
        rel_path: str,
        event: LocalFsFileEvent,
        external_record_group_id: str,
        indexing_filters: FilterCollection,
        owner: Optional[User] = None,
    ) -> Tuple[FileRecord, List[Permission]]:
        normalized_rel_path = rel_path.strip().replace("\\", "/")
        ext_id = self._external_record_id_for_rel_path(normalized_rel_path)
        parent_rel_path = (
            "/".join(normalized_rel_path.split("/")[:-1])
            if "/" in normalized_rel_path
            else None
        )
        name = Path(normalized_rel_path).name or "file"
        timestamp_ms = int(event.timestamp)
        file_time_ms = _event_file_time_ms(event)
        created_time_ms = _event_created_time_ms(event)
        size = event.size if event.size is not None else 0
        guessed, _ = mimetypes.guess_type(name)
        mime = event.mimeType or guessed or MimeTypes.UNKNOWN.value
        ext = Path(name).suffix.lower().lstrip(".") or None
        # No bytes reach the server during sync, so the desktop's full-content
        # hash is the only change-detection signal we have — the caller
        # already skips any event that arrives without one, so there is no
        # timestamp/size fallback here to silently mask a missing hash.
        revision = event.sha256

        record_id = str(uuid.uuid4())
        file_record = FileRecord(
            id=record_id,
            record_name=name,
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE,
            external_record_id=ext_id,
            external_revision_id=revision,
            external_record_group_id=external_record_group_id,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            created_at=timestamp_ms,
            updated_at=timestamp_ms,
            source_created_at=created_time_ms,
            source_updated_at=file_time_ms,
            weburl=None,
            hide_weburl=True,
            parent_external_record_id=(
                self._external_record_id_for_rel_path(parent_rel_path)
                if parent_rel_path
                else None
            ),
            parent_record_type=RecordType.FILE if parent_rel_path else None,
            size_in_bytes=size,
            is_file=True,
            extension=ext,
            # Must stay non-empty: stream_record rejects a blank path with a
            # 400, and 4xx is terminal to the indexing consumer.
            path=normalized_rel_path,
            local_fs_relative_path=normalized_rel_path,
            mime_type=mime,
            preview_renderable=True,
            sha256_hash=event.sha256,
        )

        if not LOCAL_FS_DESKTOP_CONTENT_AVAILABLE:
            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        elif indexing_filters.is_enabled(
            IndexingFilterKey.ENABLE_MANUAL_SYNC, default=False
        ):
            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        effective_owner = owner or self._owner_user_for_permissions
        perms: List[Permission] = []
        if effective_owner:
            perms.append(
                Permission(
                    email=effective_owner.email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER,
                )
            )
        return file_record, perms

    async def _ensure_owner_and_record_group(
        self,
        root: Path,
    ) -> tuple[User, str]:
        owner_id = self.created_by

        owner = await self.data_entities_processor.get_user_by_user_id(user_id=owner_id)
        if not owner:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Local FS owner could not be resolved",
            )
        self._owner_user_for_permissions = owner

        await self.data_entities_processor.on_new_app_users([self._to_app_user(owner)])

        rg_external = self._record_group_external_id()
        record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name=_client_path_leaf_name(str(root)),
            external_group_id=rg_external,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            group_type=RecordGroupType.DRIVE,
            web_url=f"file://{root}",
        )
        await self.data_entities_processor.on_new_record_groups(
            [
                (
                    record_group,
                    [
                        Permission(
                            email=owner.email,
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER,
                        )
                    ],
                )
            ]
        )

        return owner, rg_external

    async def _delete_external_ids(
        self,
        external_ids: List[str],
        user_id: str,
        listed: Optional[dict[str, Record]] = None,
        ids_known_to_exist: bool = False,
    ) -> list[str]:
        """Retire records for the given external ids; return the ones that failed.

        One record the graph refuses to delete (a permission rule, a transient
        store failure) used to raise here and end the whole run. The rest of
        the deletions are worth doing, so each id is attempted on its own and
        the failures are reported to the caller instead.
        """
        if not external_ids:
            return []
        # Resolve storage blobs before the graph rows disappear. Only records
        # created by the retired push flow carry storage:// paths, so this is
        # a no-op for anything synced since.
        # ``listed`` is the records a caller already has in hand. Looking them
        # up again would be the weaker path: both providers answer None when the
        # read itself failed, which is indistinguishable from the record being
        # gone.
        existing_records = (
            listed
            if listed is not None
            else await self._bulk_get_records_by_external_ids(external_ids)
        )
        failed: list[str] = []
        for external_id in external_ids:
            record = existing_records.get(external_id)
            try:
                if record is not None:
                    document_id = self._storage_document_id_from_path(
                        getattr(record, "path", None)
                    )
                    await self.data_entities_processor.on_record_deleted(
                        record_id=record.id,
                    )
                    if document_id:
                        await self._delete_storage_document(document_id)
                elif ids_known_to_exist:
                    # This id came from the sync point, so a record for it did
                    # exist. Nothing coming back now is more likely a read that
                    # failed than a record that vanished - the lookup answers
                    # None either way, and deleting by external id would not
                    # settle it, because both providers run the same lookup
                    # inside that call and return quietly. So it stays owed and
                    # is tried again next run.
                    raise LocalFsRecordUnreadableError(external_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                failed.append(external_id)
                self.logger.error(
                    "Local FS: could not retire record for %s",
                    external_id,
                    exc_info=True,
                )
        return failed

    @staticmethod
    def _normalize_event_rel_path(raw_path: str) -> Optional[str]:
        """Normalize a desktop-supplied relative path, or None if untrustworthy."""
        rel_path = raw_path.strip().replace("\\", "/")
        parts = rel_path.split("/")
        if (
            not rel_path
            or rel_path.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            return None
        return rel_path

    @staticmethod
    def _classify_event_kind(event_type: str, is_directory: bool) -> Optional[str]:
        """Which buffer an event will land in, or None when it will be skipped.

        RENAMED/MOVED classify as "move" even when the old path resolves to the
        same external id and the event degenerates to a plain upsert. The bias is
        only safe in that direction: a drain writes upserts before moves, so a
        move-classified upsert still lands in the right slot, whereas an
        upsert-classified move would be applied too early.
        """
        if is_directory:
            if event_type in {"DIR_CREATED", "CREATED", "MODIFIED"}:
                return "upsert"
            if event_type in {"DIR_DELETED", "DELETED"}:
                return "delete"
            if event_type in {"DIR_RENAMED", "DIR_MOVED", "RENAMED", "MOVED"}:
                return "move"
            return None
        if event_type == "DELETED":
            return "delete"
        if event_type in {"CREATED", "MODIFIED"}:
            return "upsert"
        if event_type in {"RENAMED", "MOVED"}:
            return "move"
        return None

    async def _apply_file_event_batch(
        self,
        events: List[LocalFsFileEvent],
        *,
        owner: User,
        sync_filters: FilterCollection,
        indexing_filters: FilterCollection,
        external_record_group_id: str,
        root_for_display: Path,
        emitted_folder_paths: set[str],
        seen_external_ids: Optional[set[str]] = None,
    ) -> LocalFsFileEventBatchStats:
        """Apply one page of file-event metadata pulled from the desktop.

        RENAMED/MOVED events update the existing DB record in place via
        ``on_records_moved`` — same vertex id, re-pointed parent edge — instead
        of deleting the old row and creating a new one. That preserves
        permissions/graph edges across a rename and only triggers re-indexing
        when the content hash (``external_revision_id``) actually changed, so
        a same-content rename produces no indexing events at all.

        A malformed or unsupported single event is skipped and counted, never
        raised — this runs inside a background task, and one bad path must not
        abandon the rest of the run.
        """
        processed = 0
        deleted = 0
        skipped = 0
        failed_deletions: list[str] = []
        deleted_external_ids: list[str] = []
        upsert_buffer: List[Tuple[FileRecord, List[Permission]]] = []
        # (old_external_id, new_record, permissions) for RENAMED/MOVED.
        # on_records_moved retires the old row and upserts the new one
        # atomically in one transaction, so there is no upsert-then-delete
        # ordering to manage across buffers.
        move_buffer: List[Tuple[str, FileRecord, List[Permission]]] = []
        # External ids from explicit DELETED events.
        delete_only_buffer: List[str] = []
        batch_size = max(1, self.batch_size)

        async def flush_upserts() -> None:
            nonlocal processed
            if not upsert_buffer:
                return
            if seen_external_ids is not None:
                # Folder records count as seen too, or the full-run prune
                # would delete every directory it just created.
                for record, _perms in upsert_buffer:
                    seen_external_ids.add(record.external_record_id)
            await self.data_entities_processor.on_new_records(list(upsert_buffer))
            processed += self._count_processed_file_records(upsert_buffer)
            upsert_buffer.clear()

        async def flush_moves() -> None:
            nonlocal processed
            if not move_buffer:
                return
            # The parent folder records for these moves are sitting in
            # upsert_buffer; a moved record must never be written before the
            # folder its edge points at.
            await flush_upserts()
            if seen_external_ids is not None:
                for old_ext_id, record, _perms in move_buffer:
                    # on_records_moved retires the old row, so that id is no
                    # longer one of this run's live records.
                    seen_external_ids.discard(old_ext_id)
                    seen_external_ids.add(record.external_record_id)
            await self.data_entities_processor.on_records_moved(list(move_buffer))
            processed += self._count_processed_file_records(
                [(record, perms) for _old_ext_id, record, perms in move_buffer]
            )
            move_buffer.clear()

        async def flush_delete_only() -> None:
            nonlocal deleted
            if not delete_only_buffer:
                return
            if seen_external_ids is not None:
                # The set says which records this run leaves live, not which
                # it ever touched. A file created and then deleted in one run
                # is gone at the end of it, so its id drops out — otherwise a
                # failed delete would look like a restored file and never be
                # retried, and a full run would skip it when pruning.
                for external_id in delete_only_buffer:
                    seen_external_ids.discard(external_id)
            failed = await self._delete_external_ids(
                list(delete_only_buffer), owner.id
            )
            failed_deletions.extend(failed)
            deleted_external_ids.extend(
                external_id
                for external_id in delete_only_buffer
                if external_id not in failed
            )
            deleted += len(delete_only_buffer) - len(failed)
            delete_only_buffer.clear()

        async def drain_all() -> None:
            await flush_upserts()
            await flush_moves()
            await flush_delete_only()

        # The three buffers are drained in a fixed order, so a page containing
        # more than one kind of event would otherwise be applied out of order --
        # e.g. a CREATED for a path would mint a record before the MOVED that
        # re-keys the existing one onto it. Draining on every kind change keeps
        # the desktop's ordering while still batching runs of the same kind.
        prev_kind: Optional[str] = None

        self.logger.info("Local FS: applying batch of %d event(s)", len(events))
        for event in events:
            event_type = event.type.strip().upper()
            rel_path = self._normalize_event_rel_path(event.path)
            if rel_path is None:
                self.logger.warning(
                    "Local FS: skipping event with unusable path %r", event.path
                )
                skipped += 1
                continue

            old_rel_path = ""
            if event.oldPath:
                old_rel_path = self._normalize_event_rel_path(event.oldPath) or ""
                if not old_rel_path:
                    self.logger.warning(
                        "Local FS: ignoring unusable oldPath %r for %s; the "
                        "previous record will be pruned by the next full run",
                        event.oldPath,
                        rel_path,
                    )

            if old_rel_path:
                self.logger.debug(
                    "Local FS: event %s for %s %r <- %r",
                    event_type,
                    "dir" if event.isDirectory else "file",
                    rel_path,
                    old_rel_path,
                )
            else:
                self.logger.debug(
                    "Local FS: event %s for %s %r",
                    event_type,
                    "dir" if event.isDirectory else "file",
                    rel_path,
                )

            kind = self._classify_event_kind(event_type, event.isDirectory)
            if kind is not None and prev_kind is not None and kind != prev_kind:
                await drain_all()

            if event.isDirectory:
                handled = await self._handle_directory_event_for_batch(
                    event_type=event_type,
                    rel_path=rel_path,
                    old_rel_path=old_rel_path,
                    root=root_for_display,
                    external_record_group_id=external_record_group_id,
                    event=event,
                    owner=owner,
                    upsert_buffer=upsert_buffer,
                    move_buffer=move_buffer,
                    delete_only_buffer=delete_only_buffer,
                    emitted_folder_paths=emitted_folder_paths,
                    flush_upserts=flush_upserts,
                    flush_moves=flush_moves,
                    flush_delete_only=flush_delete_only,
                    batch_size=batch_size,
                )
                if not handled:
                    skipped += 1
                else:
                    prev_kind = kind
                continue

            if event_type == "DELETED":
                delete_only_buffer.append(
                    self._external_record_id_for_rel_path(rel_path)
                )
                prev_kind = kind
                if len(delete_only_buffer) >= batch_size:
                    await flush_delete_only()
                continue

            if event_type not in {"CREATED", "MODIFIED", "RENAMED", "MOVED"}:
                self.logger.warning(
                    "Local FS: skipping unsupported file event type %s (%s)",
                    event_type,
                    rel_path,
                )
                skipped += 1
                continue

            if not event.sha256:
                # The watcher always hashes full content now; a missing hash
                # means a pre-upgrade desktop client or a failed local read,
                # neither of which we can safely substitute a fake revision
                # for without masking the real problem.
                self.logger.warning(
                    "Local FS: skipping event %s for %s — desktop sent no "
                    "content hash",
                    event_type,
                    rel_path,
                )
                skipped += 1
                continue

            if not self._pass_extension_filters(
                Path(rel_path), sync_filters, is_directory=event.isDirectory
            ):
                self.logger.debug(
                    "Local FS: skipping %s — extension filter",
                    rel_path,
                )
                skipped += 1
                continue
            if not self._pass_date_filters(event, sync_filters):
                self.logger.debug(
                    "Local FS: skipping %s — date filter",
                    rel_path,
                )
                skipped += 1
                continue

            new_ext_id = self._external_record_id_for_rel_path(rel_path)

            # A rename/move only needs the in-place-update path when the old
            # path actually resolves to a different external id (a missing/
            # unusable oldPath, or the NFC-normalization same-id edge case,
            # falls back to a plain upsert — the stale old row, if any, gets
            # pruned by the next full run).
            old_ext_id: Optional[str] = None
            if event_type in {"RENAMED", "MOVED"} and old_rel_path:
                candidate_old_ext_id = self._external_record_id_for_rel_path(old_rel_path)
                if candidate_old_ext_id != new_ext_id:
                    old_ext_id = candidate_old_ext_id

            upsert_buffer.extend(
                self._build_parent_folder_records(
                    rel_path,
                    root_for_display,
                    external_record_group_id,
                    event,
                    emitted_folder_paths,
                    owner=owner,
                )
            )
            file_record, file_permissions = self._build_file_record(
                rel_path,
                event,
                external_record_group_id,
                indexing_filters,
                owner=owner,
            )

            prev_kind = kind
            if old_ext_id:
                move_buffer.append((old_ext_id, file_record, file_permissions))
                if len(move_buffer) >= batch_size:
                    await flush_moves()
            else:
                upsert_buffer.append((file_record, file_permissions))
                if len(upsert_buffer) >= batch_size:
                    await flush_upserts()

        # End of page: drain every buffer. _delete_external_ids GCs any
        # storage blob left by the retired push flow as it deletes; moves are
        # retired atomically inside on_records_moved itself.
        await drain_all()

        return LocalFsFileEventBatchStats(
            processed=processed,
            deleted=deleted,
            skipped=skipped,
            failed_deletions=failed_deletions,
            deleted_external_ids=deleted_external_ids,
        )

    @staticmethod
    def _decode_storage_buffer_payload(
        payload: JsonValue | bytes | bytearray,
    ) -> bytes:
        """
        The storage service's GET /buffer route returns
        ``res.json(buffer)``. Across local / S3 / Azure providers, that
        always serializes a Node Buffer as ``{"type":"Buffer","data":[...]}``.

        Some legacy callers wrap it once more as ``{"data": <buffer>}``.
        """
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        if isinstance(payload, dict):
            if (
                payload.get("type") == "Buffer"
                and isinstance(payload.get("data"), list)
            ):
                return bytes(payload["data"])
            inner = payload.get("data")
            if isinstance(inner, list):
                return bytes(inner)
            if isinstance(inner, dict):
                return LocalFsConnector._decode_storage_buffer_payload(inner)
            if isinstance(inner, (bytes, bytearray)):
                return bytes(inner)
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail="Storage service returned an unrecognized buffer payload shape",
        )

    async def _stream_storage_record(
        self, record: FileRecord, storage_document_id: str
    ) -> Response:
        storage_url = await self._storage_base_url()
        storage_token = await self._storage_token()
        buffer_url = (
            f"{storage_url}/api/v1/document/internal/{storage_document_id}/buffer"
        )

        timeout = LOCAL_FS_STORAGE_HTTP_TIMEOUT
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    buffer_url,
                    headers={"Authorization": f"Bearer {storage_token}"},
                ) as response:
                    if response.status < 200 or response.status >= 300:
                        # The body can carry internal paths and tokens; log it,
                        # return only the mapped status.
                        self.logger.error(
                            "Storage service returned %s for Local FS record %s: %s",
                            response.status, record.record_name, (await response.text())[:500],
                        )
                        raise internal_service_status(response.status)
                    raw_text = await response.text()
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=HttpStatusCode.GATEWAY_TIMEOUT.value,
                detail=(
                    f"Storage service timed out during Local FS stream "
                    f"({record.record_name})"
                ),
            ) from exc
        except aiohttp.ClientError as exc:
            # The internal storage URL and the client error stay in the log.
            self.logger.error(
                "Could not reach storage service at %s for Local FS stream (%s): %s",
                storage_url, record.record_name, exc,
            )
            raise HTTPException(
                status_code=HttpStatusCode.BAD_GATEWAY.value,
                detail=(
                    f"Could not reach the storage service for '{record.record_name}'. "
                    "Please try again later."
                ),
            ) from exc

        payload: JsonValue | bytes
        try:
            payload = json.loads(raw_text) if raw_text else {}
        except json.JSONDecodeError:
            payload = raw_text.encode("utf-8")

        body = self._decode_storage_buffer_payload(payload)
        media = record.mime_type or "application/octet-stream"
        safe_filename = sanitize_filename_for_content_disposition(
            record.record_name or "",
            fallback="file",
        )
        return Response(
            content=body,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
            },
        )

    async def _fetch_desktop_content(self, record: FileRecord) -> bytes:
        """Fetch one file's bytes from the desktop, relayed by Node."""
        owner_device_id, owner_device_name = await self._owner_device()
        base_url = await self._nodejs_base_url()
        token = await self._desktop_token()
        rel_path = record.local_fs_relative_path or record.path
        payload = {
            "connectorId": self.connector_id,
            "deviceId": owner_device_id,
            "relPath": rel_path,
            "externalRecordId": record.external_record_id,
            "sha256": record.sha256_hash,
            "timeoutMs": LOCAL_FS_CONTENT_DESKTOP_BUDGET_MS,
        }
        timeout = aiohttp.ClientTimeout(total=LOCAL_FS_CONTENT_HTTP_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{base_url}{LOCAL_FS_CONTENT_ROUTE}",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    if response.status == HttpStatusCode.SUCCESS.value:
                        return await response.read()
                    status = response.status
                    body = await self._read_json_body(response)
        except asyncio.TimeoutError as exc:
            raise LocalFsDesktopTimeoutError(
                f"Desktop content fetch timed out ({rel_path})"
            ) from exc
        except aiohttp.ClientError as exc:
            raise LocalFsDesktopRemoteError(
                "NODE_UNREACHABLE",
                f"Could not reach the desktop relay at {base_url}: {exc}",
                retryable=True,
            ) from exc

        raise self._desktop_error_for(
            status,
            body,
            f"content {rel_path}",
            offline_message=self._owning_desktop_offline_message(
                "file", owner_device_name
            ),
        )

    async def stream_record(
        self,
        record: Record,
        user_id: Optional[str] = None,
        convertTo: Optional[str] = None,
    ) -> Response:
        if not isinstance(record, FileRecord) or not record.path:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Not a Local FS file record or path missing",
            )
        # Records created by the retired push flow still have a storage blob.
        storage_document_id = self._storage_document_id_from_path(record.path)
        if storage_document_id:
            return await self._stream_storage_record(record, storage_document_id)

        try:
            content = await self._fetch_desktop_content(record)
        except LocalFsDesktopRemoteError as exc:
            if not exc.retryable:
                # A file the desktop can no longer read is terminal for this
                # record — 404 lets the indexing consumer stop retrying.
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=f"Local FS content unavailable: {exc}",
                ) from exc
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail=f"Local FS desktop could not serve content: {exc}",
            ) from exc
        except LocalFsDesktopOfflineError as exc:
            # 503 classifies as TRANSIENT for the indexing consumer, so the
            # record is retried once the machine is back rather than failed.
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail=str(exc),
            ) from exc
        except LocalFsDesktopError as exc:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail=f"Local FS desktop is not available: {exc}",
            ) from exc

        safe_filename = sanitize_filename_for_content_disposition(
            record.record_name or "",
            fallback="file",
        )
        return Response(
            content=content,
            media_type=record.mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
            },
        )

    def _sync_point_key(self) -> str:
        return generate_record_sync_point_key(
            RecordType.FILE.value, "localfs", self.connector_id
        )

    async def _write_sync_point(
        self,
        *,
        cursor: Optional[str],
        run_id: str,
        batch_index: int,
        last_sync_time: Optional[int],
        pending_deletions: list[str] | None = None,
        deletions_overflowed: bool = False,
    ) -> None:
        """Persist run progress.

        ``update_sync_point`` MERGES into the stored document — ArangoDB does
        ``UPDATE doc WITH @data``, Neo4j ``SET sp += $data`` — so leaving a key
        out keeps whatever was there before. Every key this run decides is
        written explicitly, including the ones being cleared: a missing
        ``last_sync_time`` would leave an old baseline in place and quietly
        keep the next run incremental, and a missing ``pending_deletions``
        would leave ids that have since been retired.
        """
        payload: Dict[str, Any] = {
            "cursor": cursor,
            "last_batch_index": batch_index,
            "run_id": run_id,
            # Empty means "nothing is owed", which has to overwrite whatever
            # the last run stored.
            "pending_deletions": pending_deletions or [],
            # True keeps runs FULL until a prune has really retired the ids
            # dropped by the cap; False is what ends that, so both are written.
            "deletions_overflowed": deletions_overflowed,
            # None forces the next run to be FULL. Written, not omitted.
            "last_sync_time": last_sync_time,
        }
        if last_sync_time is not None:
            payload["last_run_id"] = run_id
        await self.record_sync_point.update_sync_point(self._sync_point_key(), payload)

    async def _pull_with_retry(
        self,
        *,
        run_id: str,
        batch_index: int,
        cursor: Optional[str],
        mode: str,
        session: aiohttp.ClientSession,
        expected_device_id: str,
    ) -> LocalFsPullBatch:
        """Retry one page on transient faults.

        The same ``(runId, batchIndex)`` is re-sent deliberately: the desktop
        answers a repeat from its idempotency cache without advancing, so a
        timeout that had actually succeeded cannot skip a page.
        """
        last_error: Optional[LocalFsDesktopError] = None
        for attempt in range(LOCAL_FS_PULL_MAX_ATTEMPTS):
            try:
                return await self._request_file_event_batch(
                    run_id=run_id,
                    batch_index=batch_index,
                    cursor=cursor,
                    mode=mode,
                    session=session,
                    expected_device_id=expected_device_id,
                )
            except LocalFsDesktopOfflineError:
                raise
            except LocalFsRootUnavailableError:
                raise
            except LocalFsDesktopRemoteError as exc:
                if exc.code in ("ROOT_MISSING", "ROOT_UNREADABLE"):
                    raise LocalFsRootUnavailableError(exc.code, exc.message) from exc
                if not exc.retryable:
                    raise
                last_error = exc
            except LocalFsDesktopTimeoutError as exc:
                last_error = exc
            if attempt < LOCAL_FS_PULL_MAX_ATTEMPTS - 1:
                await asyncio.sleep(LOCAL_FS_PULL_RETRY_BASE_SECONDS * 2**attempt)
        # Not remapped to DESKTOP_OFFLINE: that code means Node found no socket
        # and is skipped silently, which would hide a wedged desktop, an
        # unreachable relay or a bad node endpoint behind "laptop is closed".
        raise LocalFsDesktopUnreachableError(LOCAL_FS_PULL_MAX_ATTEMPTS, last_error)

    async def _prune_unseen_records(
        self, owner_user_id: str, seen_external_ids: set[str]
    ) -> tuple[list[str], list[str]]:
        """Delete records a completed FULL run never observed.

        Only reached after the desktop reported ``hasMore=false``, so a run
        that dies midway prunes nothing and the previous snapshot stays live.

        Returns the external ids retired and those that could not be. It
        deliberately reports nothing about whether its listing was complete,
        because it cannot know: both graph providers answer a failed page with
        an empty list, which is also what ends the paging loop, so a lost page
        and the end of the records look identical from here. Retiring the
        records it did see is still right — each was listed and this completed
        crawl did not find its file — but the absence of anything further is
        not evidence, and callers must not read it as any.
        """
        status_filters = [status.value for status in ProgressStatus]
        stale: List[str] = []
        listed: dict[str, Record] = {}
        offset = 0
        while True:
            records = await self.data_entities_processor.get_records_by_status(
                self.connector_id,
                status_filters,
                limit=FULL_SYNC_RESET_BATCH_SIZE,
                offset=offset,
            )
            if not records:
                break
            for record in records:
                external_id = getattr(record, "external_record_id", None)
                if not external_id:
                    continue
                if external_id not in seen_external_ids:
                    stale.append(external_id)
                    listed[external_id] = record
            offset += len(records)

        if not stale:
            return [], []
        self.logger.info(
            "Local FS: pruning %d record(s) absent from the full run", len(stale)
        )
        failed: list[str] = []
        for start in range(0, len(stale), FULL_SYNC_RESET_BATCH_SIZE):
            failed.extend(
                await self._delete_external_ids(
                    stale[start : start + FULL_SYNC_RESET_BATCH_SIZE],
                    owner_user_id,
                    listed=listed,
                )
            )
        return [e for e in stale if e not in failed], failed

    async def _notify_root_unavailable(
        self, exc: LocalFsRootUnavailableError
    ) -> None:
        """Tell the user the synced folder is gone; only they can point it elsewhere.

        Title and message stay stable across scheduled ticks so
        ``BaseConnector._suppress_notification`` collapses repeats.
        """
        missing = exc.code == "ROOT_MISSING"
        await self.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title=(
                "Local FS sync stopped — the synced folder is missing"
                if missing
                else "Local FS sync stopped — the synced folder could not be read"
            ),
            message=(
                (
                    f"'{self.sync_root_path}' was moved, renamed or deleted on this machine. "
                    "Indexed files are kept. If it was moved or renamed, update the folder path in connector "
                    "settings to resume sync."
                )
                if missing
                else (
                    f"The desktop could not read '{self.sync_root_path}'. "
                    "Check that the folder still exists and you can access it."
                )
            ),
            payload={
                "connector_id": self.connector_id,
                "connector_name": self.connector_name.value,
                "connector_scope": self.scope,
                "error_code": exc.code,
                "sync_root_path": self.sync_root_path,
            },
        )

    async def _notify_device_mismatch(
        self, exc: LocalFsDeviceMismatchError, owner_device_name: str | None
    ) -> None:
        """Tell the user a different machine answered for this connector.

        Title and message are kept free of the device ids so
        ``BaseConnector._suppress_notification`` collapses the identical failure
        every scheduled run produces into one notification.
        """
        owner = (
            f"'{owner_device_name}'" if owner_device_name else "the device that owns it"
        )
        await self.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title="Local FS sync stopped — this connector is owned by another device",
            message=(
                f"'{self.sync_root_path}' is connected to a different computer ({owner}). "
                "To sync it, open the desktop app on that computer and try again.\n "
                "If you want to sync this folder from this computer instead, " 
                "delete the existing connector and create a new one here."
            ),
            payload={
                "connector_id": self.connector_id,
                "connector_name": self.connector_name.value,
                "connector_scope": self.scope,
                "error_code": exc.code,
                "expected_device_id": exc.expected_device_id,
                "actual_device_id": exc.actual_device_id,
            },
        )

    async def _notify_device_unclaimed(self) -> None:
        """Tell the user no machine is set up to serve this connector's folder."""
        await self.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title="Local FS sync stopped — no desktop is set up for this connector",
            message=(
                f"No computer is set up to sync '{self.sync_root_path}'. "
                "Open the desktop app on the machine that has this folder, turn "
                "sync off for this connector and enable it again there."
            ),
            payload={
                "connector_id": self.connector_id,
                "connector_name": self.connector_name.value,
                "connector_scope": self.scope,
                "error_code": ConnectorErrorCodes.DESKTOP_UNCLAIMED,
                "sync_root_path": self.sync_root_path,
            },
        )

    async def _notify_cleanup_failed(
        self, error: "LocalFsRecordCleanupError"
    ) -> None:
        """Tell the user which part of the run did not finish.

        Deliberately not phrased as "sync failed": the files in the folder did
        sync, so the message says what is stale and that the next run retries.
        """
        await self.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.WARNING,
            title="Local FS sync finished, but some old files are still in search",
            message=error.user_message,
        )

    async def _notify_sync_aborted(
        self, exc: LocalFsDesktopError, *, restarts_from_scratch: bool = False
    ) -> None:
        """Tell the user a run stopped on a desktop-side failure.

        Unlike the two helpers above, the detail is carried in the message so a
        support request has something to work with. ``_suppress_notification``
        keys on title + message, so the few errors whose text embeds a run id
        (RESPONSE_MISMATCH) re-notify per run instead of collapsing — accepted,
        since those mean two machines are racing for one folder.

        ``restarts_from_scratch`` is set for a FULL run, which keeps no resume
        point: promising that the next run continues where this one stopped
        would leave a folder too large to finish looking like it just needs
        more time.
        """
        code = getattr(exc, "code", None) or (
            "DESKTOP_TIMEOUT"
            if isinstance(exc, LocalFsDesktopTimeoutError)
            else "DESKTOP_ERROR"
        )
        resume = (
            "The next run starts this folder over from the beginning."
            if restarts_from_scratch
            else "The next run resumes from where this one stopped."
        )
        await self.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title="Local FS sync stopped — the desktop app could not finish this run",
            message=(
                f"The sync of '{self.sync_root_path}' stopped due to an error "
                f"({code}): {str(exc)[:200]}. Indexed files are kept. {resume} "
                "If it keeps happening, check that the desktop app is running "
                "and up to date."
            ),
            payload={
                "connector_id": self.connector_id,
                "connector_name": self.connector_name.value,
                "connector_scope": self.scope,
                "error_code": code,
                "error": str(exc),
            },
        )

    async def run_sync(self) -> None:
        """Pull file-event metadata from the desktop and apply it.

        FULL vs INCREMENTAL comes from the sync point: no ``last_sync_time``
        means no completed baseline exists yet. ``event_service`` deletes sync
        points before a user-requested full sync.
        """
        await self._reload_sync_settings()
        sync_point = await self.record_sync_point.read_sync_point(
            self._sync_point_key()
        )
        last_sync_time = sync_point.get("last_sync_time")
        pending_deletions: list[str] = list(
            sync_point.get("pending_deletions") or []
        )
        # Every record this run failed to retire, plus anything still owed from
        # a previous run. Carried to the sync point and reported at the end.
        failed_deletions: list[str] = []
        # External ids, not attempts: an id retried before the prune and then
        # seen again by it is one record, and counting it twice could tip the
        # summary into "none of them could be removed".
        attempted_deletions: set[str] = set()

        # Set once the owed list has been truncated. The ids it dropped exist
        # nowhere else, so while it is set no baseline is written and every
        # run is FULL, whose prune retires whatever is still stale.
        #
        # Nothing here clears it. Clearing needs proof that a full listing of
        # the records really completed, and this connector cannot get that
        # proof: a failed page and the end of the records are both an empty
        # list. Every cheaper signal is a guess that fails in the shape that
        # matters — a small folder whose live records all fit on the first
        # page, with the stale tail behind a page that died. Full syncs cost
        # time; retiring records because a page failed costs the records.
        overflow_pending = bool(sync_point.get("deletions_overflowed"))
        if overflow_pending:
            self.logger.warning(
                "Local FS: still running full syncs after an earlier cleanup "
                "left more records behind than this connector tracks between "
                "runs. Each full sync retires what is still stale, but the "
                "marker only lifts once the record listing can report its own "
                "failures."
            )

        def owe_a_full_run() -> bool:
            return overflow_pending

        def capped(ids: list[str]) -> list[str]:
            """The owed list as it will be stored, trimmed to what it may hold.

            Applied at every checkpoint, not only at the end: a long run can
            pass the cap mid-way, and a run interrupted after that would
            otherwise leave a baseline behind and lose the dropped ids.
            """
            nonlocal overflow_pending
            if len(ids) <= LOCAL_FS_MAX_PENDING_DELETIONS:
                return ids
            if not overflow_pending:
                self.logger.warning(
                    "Local FS: %d record(s) could not be removed, more than the "
                    "%d this connector tracks between runs. The next sync will "
                    "be a full one, which retires every file that is no longer "
                    "in the folder.",
                    len(ids),
                    LOCAL_FS_MAX_PENDING_DELETIONS,
                )
            overflow_pending = True
            return ids[:LOCAL_FS_MAX_PENDING_DELETIONS]

        def still_owed(ids: list[str]) -> list[str]:
            """Ids still worth deleting: those not live at the end of this run.

            A refused delete stays pending, but the file may come back before
            the retry runs. Deleting it then would remove a file that is on
            disk, and on an incremental run nothing would put it back. The
            test is liveness, not "was it touched": a file indexed and then
            deleted in the same run is still owed its deletion.
            """
            owed: list[str] = []
            for external_id in ids:
                if external_id in seen_external_ids or external_id in owed:
                    continue
                owed.append(external_id)
            return owed
        # Owed overflow forces a full run in its own right, rather than
        # relying on the baseline having been cleared: the sync point merges,
        # so one missing write would otherwise leave an old baseline behind
        # and strand the ids the cap dropped.
        mode = "INCREMENTAL" if last_sync_time and not overflow_pending else "FULL"
        cursor = sync_point.get("cursor") if mode == "INCREMENTAL" else None
        owner_device_name: str | None = None
        run_id = str(uuid.uuid4())

        root_for_display = _client_path_for_display(self.sync_root_path)
        emitted_folder_paths: set[str] = set()
        # The records this run leaves live: ids are added as files are
        # indexed and removed again as they are deleted or moved away, so at
        # the end it says what is present rather than what was touched. A
        # FULL run prunes against it, and every run uses it to keep a pending
        # deletion from removing a file that has since come back.
        seen_external_ids: set[str] = set()
        processed = 0
        deleted = 0
        skipped = 0
        batch_index = 0
        empty_streak = 0
        # One-shot, so a desktop that rejects every cursor cannot loop.
        restarted_as_full = False
        started = time.monotonic()

        self.logger.info(
            "Local FS: starting %s sync (connector=%s run=%s)",
            mode,
            self.connector_id,
            run_id,
        )
        try:
            owner_device_id, owner_device_name = await self._owner_device()
            owner, rg_external = await self._ensure_owner_and_record_group(
                root_for_display
            )
            sync_filters, indexing_filters = await load_connector_filters(
                self.config_service, "localfs", self.connector_id, self.logger
            )
            timeout = aiohttp.ClientTimeout(total=LOCAL_FS_PULL_HTTP_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while True:
                    try:
                        batch = await self._pull_with_retry(
                            run_id=run_id,
                            batch_index=batch_index,
                            cursor=cursor,
                            mode=mode,
                            session=session,
                            expected_device_id=owner_device_id,
                        )
                    except LocalFsDesktopRemoteError as exc:
                        # The desktop lost the journal position our cursor
                        # names (reinstall, pruned journal, upgraded token
                        # format). Without this the same dead cursor is re-sent
                        # every run and the connector never syncs again.
                        if exc.code != "CURSOR_UNKNOWN" or restarted_as_full:
                            raise
                        self.logger.warning(
                            "Local FS: desktop rejected cursor %r; restarting "
                            "as a full run",
                            cursor,
                        )
                        restarted_as_full = True
                        mode = "FULL"
                        cursor = None
                        # The old baseline is unusable, so the sync point must
                        # not claim one until this full run completes.
                        last_sync_time = None
                        run_id = str(uuid.uuid4())
                        batch_index = 0
                        empty_streak = 0
                        emitted_folder_paths = set()
                        # The restart re-crawls from scratch, so what the
                        # abandoned attempt applied no longer counts.
                        seen_external_ids = set()
                        continue
                    stats = await self._apply_file_event_batch(
                        batch.events,
                        owner=owner,
                        sync_filters=sync_filters,
                        indexing_filters=indexing_filters,
                        external_record_group_id=rg_external,
                        root_for_display=root_for_display,
                        emitted_folder_paths=emitted_folder_paths,
                        seen_external_ids=seen_external_ids,
                    )
                    processed += stats.processed
                    deleted += stats.deleted
                    skipped += stats.skipped
                    attempted_deletions.update(stats.failed_deletions)
                    attempted_deletions.update(stats.deleted_external_ids)
                    failed_deletions.extend(stats.failed_deletions)
                    cursor = batch.cursor

                    # Per-batch, so a crash costs at most one page of re-work.
                    # The owed list is built first: capping it is what decides
                    # whether a baseline may be written, and keyword arguments
                    # are evaluated left to right, so reading the flag inline
                    # would use its value from before this call's truncation.
                    owed = capped(
                        still_owed(pending_deletions + failed_deletions)
                    )
                    await self._write_sync_point(
                        cursor=cursor,
                        run_id=run_id,
                        batch_index=batch_index,
                        last_sync_time=None if owe_a_full_run() else last_sync_time,
                        pending_deletions=owed,
                        deletions_overflowed=owe_a_full_run(),
                    )

                    empty_streak = 0 if batch.events else empty_streak + 1
                    batch_index += 1
                    if not batch.hasMore:
                        break
                    if empty_streak >= LOCAL_FS_MAX_EMPTY_BATCHES:
                        raise LocalFsDesktopRemoteError(
                            "STALLED",
                            f"{empty_streak} consecutive empty batches with "
                            "hasMore still set",
                            retryable=False,
                        )
                    if time.monotonic() - started > LOCAL_FS_MAX_RUN_SECONDS:
                        raise LocalFsDesktopRemoteError(
                            "RUN_TOO_LONG",
                            f"run exceeded {LOCAL_FS_MAX_RUN_SECONDS}s",
                            retryable=False,
                        )
                    if batch_index >= LOCAL_FS_MAX_BATCHES_PER_RUN:
                        raise LocalFsDesktopRemoteError(
                            "TOO_MANY_BATCHES",
                            f"run exceeded {LOCAL_FS_MAX_BATCHES_PER_RUN} batches",
                            retryable=False,
                        )

            # Records a previous run could not retire, retried by external id
            # before this run's own prune.
            #
            # A FULL run looks like it makes this redundant — its prune retires
            # everything it did not see, which is exactly these ids — and this
            # was skipped on FULL for one round. It is deliberately back: both
            # graph providers catch a listing failure in get_records_by_status
            # and return an empty list, which is indistinguishable from "nothing
            # stale". The prune would then retire nothing, the run would write a
            # baseline and report success, and these ids would be lost for good,
            # since the next run is incremental. Looking each one up by id finds
            # the record whatever the listing did, and it is also what clears
            # the ids dropped by the cap below. Duplicated work on a healthy
            # prune only inflates the attempted count.
            #
            # Either way, anything this run indexed is no longer owed a delete:
            # the file is back, and nothing later would recreate it.
            retryable = still_owed(pending_deletions)
            pending_deletions = []
            if retryable:
                self.logger.info(
                    "Local FS: retrying %d record deletion(s) left over from an "
                    "earlier run",
                    len(retryable),
                )
                attempted_deletions.update(retryable)
                still_failing = await self._delete_external_ids(
                    retryable, owner.id, ids_known_to_exist=True
                )
                deleted += len(retryable) - len(still_failing)
                failed_deletions.extend(still_failing)

            if mode == "FULL":
                pruned, prune_failures = await self._prune_unseen_records(
                    owner.id, seen_external_ids
                )
                deleted += len(pruned)
                attempted_deletions.update(pruned)
                attempted_deletions.update(prune_failures)
                failed_deletions.extend(prune_failures)

            outstanding = still_owed(failed_deletions)
            # An unbounded list would grow in the sync point run after run.
            carry_forward = capped(outstanding)
            baseline: Optional[int] = (
                None if owe_a_full_run() else get_epoch_timestamp_in_ms()
            )
            # Written before the partial-failure raise below: the crawl itself
            # finished, and repeating it would cost the whole folder again.
            # The ids that failed ride along so the next run retries them.
            await self._write_sync_point(
                cursor=cursor,
                run_id=run_id,
                batch_index=batch_index,
                last_sync_time=baseline,
                pending_deletions=carry_forward,
                deletions_overflowed=overflow_pending,
            )
            self.logger.info(
                "Local FS: %s sync complete (run=%s batches=%d processed=%d "
                "deleted=%d skipped=%d failed_deletions=%d)",
                mode,
                run_id,
                batch_index,
                processed,
                deleted,
                skipped,
                len(outstanding),
            )
            if outstanding:
                # The crawl finished and its sync point is saved, so this does
                # not cost the folder a re-crawl. It still fails the run: a
                # record for a file that is gone is wrong, and a run that
                # reported success would leave nobody to tell.
                error = LocalFsRecordCleanupError(
                    len(outstanding), len(attempted_deletions)
                )
                await self._notify_cleanup_failed(error)
                raise error
        except asyncio.CancelledError:
            raise
        except LocalFsDesktopOfflineError:
            raise
        except LocalFsRootUnavailableError as exc:
            self.logger.error("Local FS: sync aborted — %s", exc)
            await self._notify_root_unavailable(exc)
            raise
        except LocalFsDeviceMismatchError as exc:
            self.logger.error("Local FS: sync aborted — %s", exc)
            await self._notify_device_mismatch(exc, owner_device_name)
            raise
        except LocalFsDeviceUnclaimedError:
            self.logger.error(
                "Local FS: sync aborted — connector %s has no owner device",
                self.connector_id,
            )
            await self._notify_device_unclaimed()
            raise
        except LocalFsDesktopError as exc:
            # Re-raised like the arms above so the run is recorded as failed
            self.logger.warning("Local FS: sync aborted — %s", exc)
            await self._notify_sync_aborted(exc, restarts_from_scratch=mode == "FULL")
            raise
        finally:
            self._owner_user_for_permissions = None

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    def handle_webhook_notification(self, notification: Dict) -> None:
        self.logger.debug("Local FS does not use webhooks")

    async def cleanup(self) -> None:
        # No client or session to close: every HTTP session here is opened and
        # closed per request. Drop the minted service tokens so a retired
        # instance stops holding live credentials.
        self._desktop_token_cache = None
        self._desktop_token_minted_at = 0.0
        self._owner_device_cache = None
        self._owner_device_fetched_at = 0.0
        self._batch_storage_token_cache = None
        self._batch_storage_token_minted_at = 0.0
        self._batch_storage_url_cache = None
        self._owner_user_for_permissions = None
        self.logger.info("Local FS connector cleanup completed")

    async def reindex_records(self, record_results: List[Record]) -> None:
        """
        Queue indexing for existing records (e.g. manual sync / AUTO_INDEX_OFF).

        Sync already created graph rows without publishing index jobs; this path
        publishes ``reindexRecord`` events like other connectors.
        """
        if not record_results:
            self.logger.info("Local FS: reindex called with no records")
            return
        self.logger.info(
            "Local FS: publishing reindex for %d record(s)",
            len(record_results),
        )
        await self.data_entities_processor.reindex_existing_records(record_results)

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
    ) -> "LocalFsConnector":
        return LocalFsConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        """Local FS connector does not support dynamic filter options."""
        raise NotImplementedError( "Local FS connector does not support dynamic filter options")
