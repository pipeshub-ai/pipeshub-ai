"""
Local FS connector — personal scope, local folder on the connector host.

Primary flow: set the folder path and options in the web app, save, then run a
manual sync. No CLI is required for that path.

The optional Pipeshub CLI can still write ``daemon.json`` for a future local
agent; server-side ingest runs when the Python connector process can read
``sync_root_path`` (same machine or volume mount).

Sync settings accept ``batchSize`` (preferred) or ``batch_size`` in etcd.
"""

import asyncio
import hashlib
import mimetypes
import os
import uuid
from logging import Logger
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
    create_initialized_data_source_entities_processor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
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
from app.utils.time_conversion import parse_timestamp

from .models import LocalFsFileEvent, LocalFsFileEventBatchStats
from .utils import parse_sync_bool

# Canonical API / CLI connector type string (must match pipeshub-cli backend_client).
LOCAL_FS_CONNECTOR_NAME = "Local FS"
FULL_SYNC_RESET_BATCH_SIZE = 500

# Sync config keys (flat under config["sync"] — same as RSS/Web custom fields).
SYNC_ROOT_PATH_KEY = "sync_root_path"
INCLUDE_SUBFOLDERS_KEY = "include_subfolders"


def _stat_created_epoch_ms(st: os.stat_result) -> int:
    """
    Best-effort file creation time for sync filters.

    Uses st_birthtime when present (macOS/BSD). Otherwise uses st_ctime: on Linux
    this is metadata change time (not birth); on Windows ``st_ctime`` is creation
    time, which matches the intended “created” semantics.
    """
    birth = getattr(st, "st_birthtime", None)
    if birth is not None:
        return int(birth * 1000)
    return int(st.st_ctime * 1000)


def _bounds_ms_from_datetime_filter(fl: Filter) -> Tuple[Optional[int], Optional[int]]:
    after_iso, before_iso = fl.get_datetime_iso()
    return (
        parse_timestamp(after_iso) if after_iso else None,
        parse_timestamp(before_iso) if before_iso else None,
    )


def _local_fs_passes_date_filters(
    st: os.stat_result, sync_filters: FilterCollection
) -> bool:
    """Apply sync ``modified`` / ``created`` filters using local file times (epoch ms)."""
    mtime_ms = int(st.st_mtime * 1000)
    created_ms = _stat_created_epoch_ms(st)

    modified_f = sync_filters.get(SyncFilterKey.MODIFIED)
    if modified_f is not None and not modified_f.is_empty():
        after_ms, before_ms = _bounds_ms_from_datetime_filter(modified_f)
        if after_ms is not None and mtime_ms < after_ms:
            return False
        if before_ms is not None and mtime_ms > before_ms:
            return False

    created_f = sync_filters.get(SyncFilterKey.CREATED)
    if created_f is not None and not created_f.is_empty():
        after_ms, before_ms = _bounds_ms_from_datetime_filter(created_f)
        if after_ms is not None and created_ms < after_ms:
            return False
        if before_ms is not None and created_ms > before_ms:
            return False

    return True


def _parse_batch_size_from_sync(sync_cfg: Dict[str, Any]) -> int:
    raw = sync_cfg.get("batchSize")
    if raw is None or raw == "":
        raw = sync_cfg.get("batch_size", "50")
    try:
        return max(1, int(str(raw).strip() or "50"))
    except (TypeError, ValueError):
        return 50


def _validate_host_path(root: str) -> Tuple[bool, str]:
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
        "Index a folder on the machine that runs the connector service. "
        "Set the path below; if your app requires it, turn the connector off to save sync settings. "
        "Use manual sync (CLI or app) or scheduled sync at an interval. "
        "Local FS does not crawl on “Active” alone without a sync run or schedule. "
        "CLI is optional."
    )
    .with_categories(["Storage", "Local"])
    .with_scopes([ConnectorScope.PERSONAL.value])
    .configure(
        lambda builder: builder.with_icon("/assets/icons/connectors/local_fs.svg")
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
        .with_sync_strategies([SyncStrategy.MANUAL, SyncStrategy.SCHEDULED])
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

            sync_cfg = config.get("sync", {}) or {}
            root = str(sync_cfg.get(SYNC_ROOT_PATH_KEY, "")).strip()
            self.sync_root_path = root
            self.include_subfolders = parse_sync_bool(
                sync_cfg.get(INCLUDE_SUBFOLDERS_KEY, True), True
            )
            self.batch_size = _parse_batch_size_from_sync(sync_cfg)

            if not root:
                self.logger.info(
                    "Local FS: sync_root_path not configured; complete setup in the app or CLI."
                )
            else:
                ok, detail = _validate_host_path(root)
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
        ok, _detail = _validate_host_path(self.sync_root_path)
        if not ok:
            self.logger.warning(
                "Local FS: connection test failed — %s", _detail
            )
        return ok

    async def get_signed_url(self, record: Record) -> Optional[str]:
        return None

    def _record_group_external_id(self) -> str:
        return f"local_fs:{self.connector_id}"

    def _external_record_id_for_rel_path(self, rel_path: str) -> str:
        normalized = rel_path.strip().replace("\\", "/")
        return hashlib.sha256(
            f"{self.connector_id}:{normalized}".encode("utf-8")
        ).hexdigest()

    def _resolve_event_file_path(self, root: Path, rel_path: str) -> Path:
        candidate = (root / rel_path).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Path escapes sync root: {rel_path}",
            ) from exc
        return candidate

    async def _reload_sync_settings(self) -> None:
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        sync_cfg = (config or {}).get("sync", {}) or {}
        self.sync_root_path = str(sync_cfg.get(SYNC_ROOT_PATH_KEY, "")).strip()
        self.include_subfolders = parse_sync_bool(
            sync_cfg.get(INCLUDE_SUBFOLDERS_KEY, True), True
        )
        self.batch_size = _parse_batch_size_from_sync(sync_cfg)

    @staticmethod
    def _coerce_user(raw: Any) -> Optional[User]:
        """Graph providers return user dicts; GraphTransactionStore may type them as User."""
        if raw is None:
            return None
        if isinstance(raw, User):
            return raw
        if isinstance(raw, dict):
            return User.from_arango_user(raw)
        return None

    async def _resolve_owner_user(self) -> Optional[User]:
        async with self.data_store_provider.transaction() as tx_store:
            app_doc = await tx_store.graph_provider.get_document(
                self.connector_id,
                CollectionNames.APPS.value,
                transaction=tx_store.txn,
            )
            if not app_doc:
                self.logger.error("Local FS: connector app %s not found in graph", self.connector_id)
                return None
            created_by = app_doc.get("createdBy") or app_doc.get("created_by")
            if not created_by:
                self.logger.error("Local FS: connector %s has no createdBy", self.connector_id)
                return None
            raw = await tx_store.get_user_by_user_id(str(created_by))
            user = self._coerce_user(raw)
            if not user:
                self.logger.error("Local FS: user %s not found or could not be loaded", created_by)
            return user

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

    def _iter_file_paths(self, root: Path) -> List[Path]:
        out: List[Path] = []
        if self.include_subfolders:
            for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
                for name in filenames:
                    out.append(Path(dirpath) / name)
        else:
            for name in os.listdir(root):
                p = root / name
                if p.is_file():
                    out.append(p)
        return out

    def _build_file_record(
        self,
        abs_path: Path,
        root: Path,
        external_record_group_id: str,
        indexing_filters: FilterCollection,
        st: Optional[os.stat_result] = None,
    ) -> Tuple[FileRecord, List[Permission]]:
        rel = abs_path.relative_to(root).as_posix()
        ext_id = hashlib.sha256(
            f"{self.connector_id}:{rel}".encode("utf-8")
        ).hexdigest()
        if st is None:
            st = abs_path.stat()
        mtime_ms = int(st.st_mtime * 1000)
        revision = f"{mtime_ms}:{st.st_size}"
        guessed, _ = mimetypes.guess_type(abs_path.name)
        mime = guessed or MimeTypes.UNKNOWN.value
        ext = abs_path.suffix.lower().lstrip(".") or None

        record_id = str(uuid.uuid4())
        file_record = FileRecord(
            id=record_id,
            record_name=abs_path.name,
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE,
            external_record_id=ext_id,
            external_revision_id=revision,
            external_record_group_id=external_record_group_id,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            created_at=mtime_ms,
            updated_at=mtime_ms,
            source_created_at=mtime_ms,
            source_updated_at=mtime_ms,
            # Same-origin app route (see UPLOAD webUrl in kb_controllers); a bare filesystem path is
            # interpreted by the browser as a path on the web host and returns 404.
            weburl=f"/record/{record_id}",
            size_in_bytes=st.st_size,
            is_file=True,
            extension=ext,
            path=str(abs_path.resolve()),
            mime_type=mime,
            preview_renderable=True,
        )

        if not indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        owner = self._owner_user_for_permissions
        perms: List[Permission] = []
        if owner:
            perms.append(
                Permission(
                    external_id=owner.id,
                    email=owner.email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER,
                )
            )
        return file_record, perms

    async def _ensure_owner_and_record_group(
        self,
        root: Path,
    ) -> tuple[User, FilterCollection, FilterCollection, str]:
        owner = await self._resolve_owner_user()
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
            name=f"Local FS — {root.name}",
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
                            external_id=owner.id,
                            email=owner.email,
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER,
                        )
                    ],
                )
            ]
        )

        return owner, sync_filters, indexing_filters, rg_external

    async def _delete_rel_path(self, rel_path: str, user_id: str) -> None:
        external_id = self._external_record_id_for_rel_path(rel_path)
        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.delete_record_by_external_id(
                self.connector_id, external_id, user_id
            )

    async def _upsert_rel_path(
        self,
        root: Path,
        rel_path: str,
        rg_external: str,
        sync_filters: FilterCollection,
        indexing_filters: FilterCollection,
    ) -> bool:
        abs_path = self._resolve_event_file_path(root, rel_path)
        if abs_path.is_symlink() or not abs_path.is_file():
            return False
        if not self._extension_allowed(abs_path, sync_filters):
            return False
        st = abs_path.stat()
        if not _local_fs_passes_date_filters(st, sync_filters):
            return False
        await self.data_entities_processor.on_new_records(
            [self._build_file_record(abs_path, root, rg_external, indexing_filters, st=st)]
        )
        return True

    async def _has_existing_records(self) -> bool:
        """True if any records already exist for this connector.

        Used to decide whether ``run_sync`` should do a first-time full
        filesystem walk, or skip it because subsequent deltas arrive via
        ``apply_file_event_batch`` (Electron app journal / CLI replay).
        """
        try:
            status_filters = [status.value for status in ProgressStatus]
            async with self.data_store_provider.transaction() as tx_store:
                records = await tx_store.get_records_by_status(
                    self.data_entities_processor.org_id,
                    self.connector_id,
                    status_filters,
                    limit=1,
                    offset=0,
                )
            return bool(records)
        except Exception as e:
            self.logger.warning(
                "Local FS: existing-records check failed (%s); proceeding with full walk",
                e,
            )
            return False

    async def _reset_existing_records(self, owner_user_id: str) -> int:
        status_filters = [status.value for status in ProgressStatus]
        deleted = 0

        while True:
            async with self.data_store_provider.transaction() as tx_store:
                records = await tx_store.get_records_by_status(
                    self.data_entities_processor.org_id,
                    self.connector_id,
                    status_filters,
                    limit=FULL_SYNC_RESET_BATCH_SIZE,
                    offset=0,
                )
                if not records:
                    return deleted

                deleted_this_round = 0
                for record in records:
                    external_id = getattr(record, "external_record_id", None)
                    if not external_id:
                        continue
                    await tx_store.delete_record_by_external_id(
                        self.connector_id,
                        external_id,
                        owner_user_id,
                    )
                    deleted += 1
                    deleted_this_round += 1

                if deleted_this_round == 0:
                    return deleted

    async def apply_file_event_batch(
        self,
        events: List[LocalFsFileEvent],
        reset_before_apply: bool = False,
    ) -> LocalFsFileEventBatchStats:
        await self._reload_sync_settings()
        root_raw = self.sync_root_path.strip()
        if not root_raw:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Local FS sync_root_path is not configured",
            )

        ok_path, detail = _validate_host_path(root_raw)
        if not ok_path:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Local FS cannot use sync_root_path: {detail}",
            )

        root = Path(detail)
        processed = 0
        deleted = 0

        try:
            owner, sync_filters, indexing_filters, rg_external = (
                await self._ensure_owner_and_record_group(root)
            )

            if reset_before_apply:
                deleted += await self._reset_existing_records(owner.id)

            for event in events:
                event_type = event.type.strip().upper()
                rel_path = event.path.strip().replace("\\", "/")
                old_rel_path = (
                    event.oldPath.strip().replace("\\", "/") if event.oldPath else ""
                )

                if not rel_path:
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_REQUEST.value,
                        detail="File event path is required",
                    )
                if event.isDirectory:
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_REQUEST.value,
                        detail="Directory events must be expanded client-side before replay",
                    )

                if event_type in {"CREATED", "MODIFIED"}:
                    if await self._upsert_rel_path(
                        root,
                        rel_path,
                        rg_external,
                        sync_filters,
                        indexing_filters,
                    ):
                        processed += 1
                    continue

                if event_type == "DELETED":
                    await self._delete_rel_path(rel_path, owner.id)
                    deleted += 1
                    continue

                if event_type in {"RENAMED", "MOVED"}:
                    if old_rel_path:
                        await self._delete_rel_path(old_rel_path, owner.id)
                        deleted += 1
                    if await self._upsert_rel_path(
                        root,
                        rel_path,
                        rg_external,
                        sync_filters,
                        indexing_filters,
                    ):
                        processed += 1
                    continue

                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Unsupported Local FS file event type: {event_type}",
                )

            return LocalFsFileEventBatchStats(processed=processed, deleted=deleted)
        finally:
            self._owner_user_for_permissions = None

    async def stream_record(
        self,
        record: Record,
        user_id: Optional[str] = None,
        convertTo: Optional[str] = None,
    ) -> StreamingResponse:
        if not isinstance(record, FileRecord) or not record.path:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Not a Local FS file record or path missing",
            )
        await self._reload_sync_settings()
        root_raw = self.sync_root_path.strip()
        if not root_raw:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Local FS sync_root_path is not configured",
            )
        ok_path, detail = _validate_host_path(root_raw)
        if not ok_path:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Local FS cannot use sync_root_path: {detail}",
            )
        root = Path(detail).resolve(strict=False)
        raw_path = Path(record.path)
        try:
            if raw_path.is_absolute():
                candidate = raw_path.expanduser().resolve(strict=False)
            else:
                candidate = (root / raw_path).resolve(strict=False)
        except (OSError, ValueError) as e:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Invalid file path: {e}",
            ) from e
        if not candidate.is_relative_to(root):
            raise HTTPException(
                status_code=HttpStatusCode.FORBIDDEN.value,
                detail="File path is outside the configured Local FS folder",
            )
        if not candidate.is_file():
            raise HTTPException(
                status_code=HttpStatusCode.NOT_FOUND.value,
                detail="Local file not found for this record",
            )

        p = candidate

        def _read() -> bytes:
            return p.read_bytes()

        try:
            data = await asyncio.to_thread(_read)
        except OSError as e:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Cannot read file: {e}",
            ) from e

        media = record.mime_type or "application/octet-stream"
        return StreamingResponse(
            iter([data]),
            media_type=media,
            headers={"Content-Disposition": f'inline; filename="{record.record_name}"'},
        )

    async def run_sync(self) -> None:
        await self._reload_sync_settings()

        # Once seeded, deltas flow exclusively through apply_file_event_batch
        # (Electron app journal replay or CLI). Skip before path validation so
        # Electron-mode connectors — where sync_root_path lives on the user's
        # machine, not the backend host — return cleanly without warnings.
        if await self._has_existing_records():
            self.logger.info(
                "Local FS: existing records detected for %s; skipping full re-scan. "
                "Incremental changes are applied via the file event batch endpoint.",
                self.connector_id,
            )
            return

        root_raw = self.sync_root_path.strip()
        if not root_raw:
            self.logger.warning(
                "Local FS: sync_root_path is empty; set Local folder path in the app or run pipeshub setup."
            )
            return

        ok_path, detail = _validate_host_path(root_raw)
        if not ok_path:
            # Expected for Electron-managed connectors: the path lives on the
            # user's desktop, not the backend host. The Electron watcher seeds
            # records via apply_file_event_batch, so this is informational.
            self.logger.info(
                "Local FS: backend cannot read sync_root_path (%s); "
                "deferring initial seed to the client (Electron app / CLI).",
                detail,
            )
            return
        root = Path(detail)

        try:
            self._owner_user_for_permissions = await self._resolve_owner_user()
            owner = self._owner_user_for_permissions
            if not owner:
                return

            sync_filters, indexing_filters = await load_connector_filters(
                self.config_service, "localfs", self.connector_id, self.logger
            )

            await self.data_entities_processor.on_new_app_users([self._to_app_user(owner)])

            rg_external = self._record_group_external_id()
            rg_name = f"Local FS — {root.name}"
            record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=rg_name,
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
                                external_id=owner.id,
                                email=owner.email,
                                type=PermissionType.OWNER,
                                entity_type=EntityType.USER,
                            )
                        ],
                    )
                ]
            )

            paths = self._iter_file_paths(root)
            batch: List[Tuple[FileRecord, List[Permission]]] = []
            processed = 0
            for abs_path in paths:
                try:
                    if abs_path.is_symlink():
                        continue
                    if not abs_path.is_file():
                        continue
                    if not self._extension_allowed(abs_path, sync_filters):
                        continue
                    st = abs_path.stat()
                    if not _local_fs_passes_date_filters(st, sync_filters):
                        continue
                    batch.append(
                        self._build_file_record(
                            abs_path, root, rg_external, indexing_filters, st=st
                        )
                    )
                    processed += 1
                    if len(batch) >= self.batch_size:
                        await self.data_entities_processor.on_new_records(batch)
                        batch = []
                        await asyncio.sleep(0)
                except Exception as e:
                    self.logger.warning("Local FS: skip %s: %s", abs_path, e)
                    continue

            if batch:
                await self.data_entities_processor.on_new_records(batch)

            self.logger.info(
                "Local FS: finished sync from %s (%d file(s) processed)",
                root,
                processed,
            )
        except Exception as e:
            self.logger.error("Local FS run_sync failed: %s", e, exc_info=True)
            raise
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
        **kwargs,
    ) -> "LocalFsConnector":
        data_entities_processor = await create_initialized_data_source_entities_processor(
            logger, data_store_provider, config_service
        )
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
