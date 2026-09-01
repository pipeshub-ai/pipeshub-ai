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
import os
import time
import unicodedata
import uuid
from logging import Logger
from pathlib import Path
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
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOptionsResponse,
    FilterType,
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
# generate_jwt defaults to a 1h expiry and a full sync outlives it.
LOCAL_FS_DESKTOP_TOKEN_TTL_SECONDS = 45 * 60
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
        self.retryable = retryable


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


def _validate_sync_root_path(root: str) -> Tuple[bool, str]:
    """
    Return (ok, detail) for whether this process can use ``root`` as a sync root.
    ``detail`` is a resolved path when ok, or a short reason when not.

    Works on Windows and POSIX: :class:`pathlib.Path` resolves drive and UNC
    paths; ``os.access`` is used for readability. On Windows, execute
    permission is not checked the same way as on Unix, but the read check still
    reflects typical access failures.
    """
    raw = root.strip()
    if not raw:
        return True, ""
    try:
        p = Path(raw).expanduser().resolve(strict=False)
    except (OSError, ValueError) as e:
        return False, str(e)
    if not p.exists():
        return False, f"path does not exist: {p}"
    if not p.is_dir():
        return False, f"not a directory: {p}"
    if not os.access(p, os.R_OK):
        return False, f"not readable: {p}"
    if not os.access(p, os.X_OK):
        return False, f"not searchable (execute bit): {p}"
    return True, str(p)


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
                    "Choose the folder on this machine where the connector service runs. "
                    "Use “Choose folder” — then save and run a manual sync. "
                    "The CLI is optional."
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
        .add_filter_field(
            CommonFields.created_date_filter(
                "Only sync files created within this range (optional)."
            )
        )
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.FILES.value,
                display_name="Index files",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Index file content from this folder.",
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.DOCUMENTS.value,
                display_name="Index documents",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Index document types (PDF, Office, etc.).",
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.IMAGES.value,
                display_name="Index images",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Index image files.",
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.VIDEOS.value,
                display_name="Index videos",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Index video files.",
                default_value=True,
            )
        )
        .add_filter_field(
            FilterField(
                name=IndexingFilterKey.ATTACHMENTS.value,
                display_name="Index attachments",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                description="Index attachment-like files when applicable.",
                default_value=True,
            )
        )
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
        self._desktop_token_cache: Optional[str] = None
        self._desktop_token_minted_at: float = 0.0
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
            else:
                ok, detail = _validate_sync_root_path(root)
                if not ok:
                    self.logger.warning(
                        "Local FS: sync_root_path is not usable by this process (%s). "
                        "If the path exists on your laptop but the connector runs in Docker, "
                        "mount the folder into the container and use the in-container path.",
                        detail,
                    )
                else:
                    self.logger.info(
                        "Local FS: sync_root_path OK at %s (include_subfolders=%s)",
                        detail,
                        self.include_subfolders,
                    )
            return True
        except Exception as e:
            self.logger.error("Local FS init failed: %s", e, exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        if not self.sync_root_path.strip():
            return True
        ok, _detail = _validate_sync_root_path(self.sync_root_path)
        if not ok:
            self.logger.warning(
                "Local FS: backend cannot access sync_root_path during toggle (%s); "
                "allowing activation and deferring validation/sync to client watcher or CLI.",
                _detail,
            )
            return True
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
    def _extension_allowed(path: Path, sync_filters: FilterCollection) -> bool:
        raw = sync_filters.get_value(SyncFilterKey.FILE_EXTENSIONS)
        if not raw:
            return True
        items = raw if isinstance(raw, (list, tuple, set)) else [raw]
        allowed = {str(x).lower().lstrip(".") for x in items}
        ext = path.suffix.lower().lstrip(".") or ""
        return ext in allowed

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
        timestamp_ms: int,
        owner: Optional[User] = None,
    ) -> Tuple[FileRecord, List[Permission]]:
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
            source_created_at=timestamp_ms,
            source_updated_at=timestamp_ms,
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
        timestamp_ms: int,
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
                    timestamp_ms,
                    owner=owner,
                )
            )
        return records

    def _append_folder_upsert_records(
        self,
        upsert_buffer: List[Tuple[FileRecord, List[Permission]]],
        rel_path: str,
        root: Path,
        external_record_group_id: str,
        timestamp_ms: int,
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
                timestamp_ms,
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
                timestamp_ms,
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
        timestamp_ms: int,
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
                timestamp_ms,
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
                    timestamp_ms,
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
                timestamp_ms,
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
            < LOCAL_FS_DESKTOP_TOKEN_TTL_SECONDS
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

    @staticmethod
    def _desktop_error_for(
        status: int, body: Dict[str, Any], context: str
    ) -> LocalFsDesktopError:
        if status == HttpStatusCode.CONFLICT.value:
            return LocalFsDesktopOfflineError(f"No desktop connected ({context})")
        if status == HttpStatusCode.GATEWAY_TIMEOUT.value:
            return LocalFsDesktopTimeoutError(f"Desktop did not answer ({context})")
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        return LocalFsDesktopRemoteError(
            str(error.get("code") or body.get("code") or f"HTTP_{status}"),
            str(error.get("message") or body.get("message") or context),
            retryable=bool(
                error.get(
                    "retryable",
                    status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                )
            ),
        )

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
        expected_device_id: Optional[str] = None,
    ) -> LocalFsPullBatch:
        """Pull one page of file-event metadata from the desktop via Node.

        Retries are deliberately NOT handled here — the caller re-sends the
        same ``(runId, batchIndex)``, which the desktop answers from its
        idempotency cache without advancing. A retry layer inside this method
        would be invisible to that contract.

        ``expected_device_id`` is checked on the *ack*, not asserted in the
        request: the relay already routes to the registered socket, so an
        expected device in the request would only restate that guarantee.
        """
        base_url = await self._nodejs_base_url()
        token = await self._desktop_token()
        request_payload = {
            "connectorId": self.connector_id,
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

        # The relay broadcasts to whatever socket claims this user; a reply
        # from a different connector or a superseded run means the wrong
        # machine answered and must not be applied.
        if batch.connectorId != self.connector_id or batch.runId != run_id:
            raise LocalFsDesktopRemoteError(
                "RESPONSE_MISMATCH",
                (
                    f"Expected connector={self.connector_id} run={run_id}, "
                    f"got connector={batch.connectorId} run={batch.runId}"
                ),
                retryable=False,
            )
        # Registration alone cannot hold ownership: when the owning machine
        # disconnects the claim frees, a second machine takes it, and the next
        # full run prunes everything the first one synced. The device pinned on
        # the sync point is what makes changing machines an explicit act.
        if expected_device_id and batch.deviceId != expected_device_id:
            raise LocalFsDesktopRemoteError(
                "RESPONSE_MISMATCH",
                (
                    f"Sync point is owned by device {expected_device_id}, but "
                    f"device {batch.deviceId} answered. Clear the sync point to "
                    "re-seed from this machine."
                ),
                retryable=False,
            )
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
        cached = getattr(self, "_batch_storage_token_cache", None)
        if cached is not None:
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
    def _event_matches_date_filters(
        event: LocalFsFileEvent, sync_filters: FilterCollection
    ) -> bool:
        """Apply sync date filters to Local FS watcher event timestamps."""
        timestamp_ms = int(event.timestamp)
        modified_f = sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_f is not None and not modified_f.is_empty():
            after_ms, before_ms = _get_datetime_filter_bounds_ms(modified_f)
            if after_ms is not None and timestamp_ms < after_ms:
                return False
            if before_ms is not None and timestamp_ms > before_ms:
                return False

        created_f = sync_filters.get(SyncFilterKey.CREATED)
        if created_f is not None and not created_f.is_empty():
            after_ms, before_ms = _get_datetime_filter_bounds_ms(created_f)
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
            source_created_at=timestamp_ms,
            source_updated_at=timestamp_ms,
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
        elif not indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
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
    ) -> tuple[User, FilterCollection, FilterCollection, str]:
        owner_id = self.created_by

        owner = await self.data_entities_processor.get_user_by_user_id(user_id=owner_id)
        if not owner:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Local FS owner could not be resolved",
            )
        self._owner_user_for_permissions = owner

        sync_filters, indexing_filters = await load_connector_filters(
            self.config_service, "localfs", self.connector_id, self.logger
        )

        await self.data_entities_processor.on_new_app_users([self._to_app_user(owner)])

        rg_external = self._record_group_external_id()
        record_group = RecordGroup(
            org_id=self.data_entities_processor.org_id,
            name=root.name or str(root),
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

        return owner, sync_filters, indexing_filters, rg_external

    async def _delete_external_ids(
        self, external_ids: List[str], user_id: str
    ) -> None:
        if not external_ids:
            return
        # Resolve storage blobs before the graph rows disappear. Only records
        # created by the retired push flow carry storage:// paths, so this is
        # a no-op for anything synced since.
        existing_records = await self._bulk_get_records_by_external_ids(external_ids)
        for external_id in external_ids:
            record = existing_records.get(external_id)
            if record is None:
                continue
            document_id = self._storage_document_id_from_path(
                getattr(record, "path", None)
            )
            await self.data_entities_processor.on_record_deleted(
                record_id=record.id,
            )
            if document_id:
                await self._delete_storage_document(document_id)

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
                for _old_ext_id, record, _perms in move_buffer:
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
            await self._delete_external_ids(list(delete_only_buffer), owner.id)
            deleted += len(delete_only_buffer)
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
                    timestamp_ms=int(event.timestamp),
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

            if not self._extension_allowed(Path(rel_path), sync_filters):
                skipped += 1
                continue
            if not self._event_matches_date_filters(event, sync_filters):
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
                    int(event.timestamp),
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
            processed=processed, deleted=deleted, skipped=skipped
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
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_GATEWAY.value,
                            detail=(
                                "Storage service could not return Local FS record: "
                                f"{await response.text()}"
                            ),
                        )
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
            raise HTTPException(
                status_code=HttpStatusCode.BAD_GATEWAY.value,
                detail=(
                    f"Could not reach storage service at {storage_url} for Local FS "
                    f"stream ({record.record_name}): {exc}"
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
        base_url = await self._nodejs_base_url()
        token = await self._desktop_token()
        rel_path = record.local_fs_relative_path or record.path
        payload = {
            "connectorId": self.connector_id,
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

        raise self._desktop_error_for(status, body, f"content {rel_path}")

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
        except LocalFsDesktopError as exc:
            # 503 classifies as TRANSIENT for the indexing consumer, so the
            # record is retried once the machine is back rather than failed.
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
        device_id: Optional[str] = None,
    ) -> None:
        """Persist run progress.

        ``update_sync_point`` rewrites the whole document, so an incremental
        run has to carry ``last_sync_time`` and ``device_id`` forward
        explicitly — dropping the former would silently demote the next run to
        a destructive FULL, and dropping the latter would let any machine take
        the folder over on its next tick.
        """
        payload: Dict[str, Any] = {
            "cursor": cursor,
            "last_batch_index": batch_index,
            "run_id": run_id,
        }
        if device_id:
            payload["device_id"] = device_id
        if last_sync_time is not None:
            payload["last_sync_time"] = last_sync_time
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
        expected_device_id: Optional[str] = None,
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
            except LocalFsDesktopRemoteError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
            except LocalFsDesktopTimeoutError as exc:
                last_error = exc
            if attempt < LOCAL_FS_PULL_MAX_ATTEMPTS - 1:
                await asyncio.sleep(LOCAL_FS_PULL_RETRY_BASE_SECONDS * 2**attempt)
        raise LocalFsDesktopOfflineError(
            f"Desktop unreachable after {LOCAL_FS_PULL_MAX_ATTEMPTS} attempts: "
            f"{last_error}"
        )

    async def _prune_unseen_records(
        self, owner_user_id: str, seen_external_ids: set[str]
    ) -> int:
        """Delete records a completed FULL run never observed.

        Only reached after the desktop reported ``hasMore=false``, so a run
        that dies midway prunes nothing and the previous snapshot stays live.
        """
        status_filters = [status.value for status in ProgressStatus]
        stale: List[str] = []
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
                if external_id and external_id not in seen_external_ids:
                    stale.append(external_id)
            offset += len(records)

        if not stale:
            return 0
        self.logger.info(
            "Local FS: pruning %d record(s) absent from the full run", len(stale)
        )
        for start in range(0, len(stale), FULL_SYNC_RESET_BATCH_SIZE):
            await self._delete_external_ids(
                stale[start : start + FULL_SYNC_RESET_BATCH_SIZE], owner_user_id
            )
        return len(stale)

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
        mode = "INCREMENTAL" if last_sync_time else "FULL"
        cursor = sync_point.get("cursor") if mode == "INCREMENTAL" else None
        # The machine that owns this folder. Empty on a first run: the first
        # device to answer claims it, and only clearing the sync point (which
        # forces a full re-seed) hands it to another.
        device_id = sync_point.get("device_id") or None
        run_id = str(uuid.uuid4())

        root_for_display = Path(self.sync_root_path.strip() or "Local FS")
        emitted_folder_paths: set[str] = set()
        seen_external_ids: Optional[set[str]] = set() if mode == "FULL" else None
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
            owner, sync_filters, indexing_filters, rg_external = (
                await self._ensure_owner_and_record_group(root_for_display)
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
                            expected_device_id=device_id,
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
                        seen_external_ids = set()
                        continue
                    # First page of a never-owned folder pins the answering
                    # machine; from here on _request_file_event_batch rejects
                    # anyone else.
                    if device_id is None and batch.deviceId:
                        device_id = batch.deviceId
                        self.logger.info(
                            "Local FS: connector %s is now owned by device %s",
                            self.connector_id,
                            device_id,
                        )
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
                    cursor = batch.cursor

                    # Per-batch, so a crash costs at most one page of re-work.
                    await self._write_sync_point(
                        cursor=cursor,
                        run_id=run_id,
                        batch_index=batch_index,
                        last_sync_time=last_sync_time,
                        device_id=device_id,
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

            if seen_external_ids is not None:
                deleted += await self._prune_unseen_records(
                    owner.id, seen_external_ids
                )

            await self._write_sync_point(
                cursor=cursor,
                run_id=run_id,
                batch_index=batch_index,
                last_sync_time=get_epoch_timestamp_in_ms(),
                device_id=device_id,
            )
            self.logger.info(
                "Local FS: %s sync complete (run=%s batches=%d processed=%d "
                "deleted=%d skipped=%d)",
                mode,
                run_id,
                batch_index,
                processed,
                deleted,
                skipped,
            )
        except asyncio.CancelledError:
            raise
        except LocalFsDesktopOfflineError:
            raise
        except LocalFsDesktopError as exc:
            self.logger.warning("Local FS: sync aborted — %s", exc)
        finally:
            self._owner_user_for_permissions = None

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    def handle_webhook_notification(self, notification: Dict) -> None:
        self.logger.debug("Local FS does not use webhooks")

    async def cleanup(self) -> None:
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
        return FilterOptionsResponse(
            success=True,
            options=[],
            page=page,
            limit=limit,
            has_more=False,
        )
