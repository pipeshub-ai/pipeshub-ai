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
import json
import mimetypes
import os
import unicodedata
import uuid
from logging import Logger
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Dict, List, Optional, Tuple

import aiohttp
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import JsonValue

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
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
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
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.jwt import generate_jwt
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import parse_timestamp

from .models import LocalFsFileEvent, LocalFsFileEventBatchStats

# Canonical API / CLI connector type string (must match pipeshub-cli backend_client).
LOCAL_FS_CONNECTOR_NAME = "Local FS"
LOCAL_FS_ICON_PATH = "/icons/connectors/local-fs.png"
FULL_SYNC_RESET_BATCH_SIZE = 500

# Sync config keys (flat under config["sync"] — same as RSS/Web custom fields).
SYNC_ROOT_PATH_KEY = "sync_root_path"
INCLUDE_SUBFOLDERS_KEY = "include_subfolders"
LOCAL_FS_STORAGE_PATH_PREFIX = "storage://"
LOCAL_FS_STORAGE_DOCUMENT_PATH_PREFIX = "local-fs"
# No total timeout on storage reads/writes for Local FS: large desktop sync
# payloads and slow links are expected (aligns with Node batch proxy timeout: 0).
LOCAL_FS_STORAGE_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=None)
LOCAL_FS_STORAGE_DELETE_TIMEOUT_SECONDS = 30
# Node may respond with these when storage uses presigned direct upload (Location).
STORAGE_UPLOAD_REDIRECT_STATUS_CODES = frozenset({301, 302, 307, 308})


def _get_created_timestamp_ms(st: os.stat_result) -> int:
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


def _file_stat_matches_date_filters(
    st: os.stat_result, sync_filters: FilterCollection
) -> bool:
    """Apply sync ``modified`` / ``created`` filters using local file times (epoch ms)."""
    mtime_ms = int(st.st_mtime * 1000)
    created_ms = _get_created_timestamp_ms(st)

    modified_f = sync_filters.get(SyncFilterKey.MODIFIED)
    if modified_f is not None and not modified_f.is_empty():
        after_ms, before_ms = _get_datetime_filter_bounds_ms(modified_f)
        if after_ms is not None and mtime_ms < after_ms:
            return False
        if before_ms is not None and mtime_ms > before_ms:
            return False

    created_f = sync_filters.get(SyncFilterKey.CREATED)
    if created_f is not None and not created_f.is_empty():
        after_ms, before_ms = _get_datetime_filter_bounds_ms(created_f)
        if after_ms is not None and created_ms < after_ms:
            return False
        if before_ms is not None and created_ms > before_ms:
            return False

    return True


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
            user = self._parse_user_from_graph_result(raw)
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
        owner: Optional[User] = None,
    ) -> Tuple[FileRecord, List[Permission]]:
        rel = abs_path.relative_to(root).as_posix()
        ext_id = self._external_record_id_for_rel_path(rel)
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

        effective_owner = owner or self._owner_user_for_permissions
        perms: List[Permission] = []
        if effective_owner:
            perms.append(
                Permission(
                    external_id=effective_owner.id,
                    email=effective_owner.email,
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER,
                )
            )
        return file_record, perms

    @staticmethod
    def _storage_document_id_from_path(record_path: str | None) -> str | None:
        if not record_path or not record_path.startswith(LOCAL_FS_STORAGE_PATH_PREFIX):
            return None
        document_id = record_path[len(LOCAL_FS_STORAGE_PATH_PREFIX) :].strip()
        return document_id or None

    @staticmethod
    def _extract_storage_document_id(response_payload: JsonValue) -> str:
        """
        Walk the storage service's upload response and return the document id.

        Tolerated shapes (in priority order):
          {"_id": "<id>"} | {"id": "<id>"} | {"documentId": "<id>"}
          {"_id": {"$oid": "<id>"}}                    # MongoDB extended JSON
          {"data": {...}}, {"document": {...}}         # wrapped responses
        """
        seen: set[int] = set()

        def _walk(node: JsonValue) -> Optional[str]:
            if not isinstance(node, dict) or id(node) in seen:
                return None
            seen.add(id(node))
            for key in ("_id", "id", "documentId"):
                raw = node.get(key)
                if isinstance(raw, str) and raw:
                    return raw
                if isinstance(raw, dict):
                    oid = raw.get("$oid") or raw.get("oid")
                    if isinstance(oid, str) and oid:
                        return oid
            for wrap_key in ("data", "document", "result"):
                inner = node.get(wrap_key)
                if isinstance(inner, dict):
                    found = _walk(inner)
                    if found:
                        return found
            return None

        found = _walk(response_payload)
        if found:
            return found
        raise HTTPException(
            status_code=HttpStatusCode.BAD_GATEWAY.value,
            detail="Storage service did not return a recognizable document id",
        )

    @staticmethod
    def _build_storage_document_name(rel_path: str) -> str:
        """Build a storage-safe display name from a relative path."""
        name = Path(rel_path.replace("\\", "/")).name or "file"
        stem = Path(name).stem or name
        safe = "".join(ch if ch not in {"/", "\\"} else "_" for ch in stem).strip()
        return (safe or "file")[:180]

    @staticmethod
    def _build_storage_upload_filename(rel_path: str, mime_type: str | None) -> str:
        """Build upload filename and enforce a binary extension when unknown."""
        name = Path(rel_path.replace("\\", "/")).name or "file"
        if not Path(name).suffix:
            return f"{name}.bin"
        guessed, _ = mimetypes.guess_type(name)
        if not guessed and (
            not mime_type
            or mime_type in {MimeTypes.UNKNOWN.value, "application/octet-stream"}
        ):
            return f"{Path(name).stem or 'file'}.bin"
        return name.replace("/", "_").replace("\\", "_")

    def _require_org_id(self) -> str:
        """Org id must be present before any storage upload — the JWT signs it."""
        org_id = getattr(self.data_entities_processor, "org_id", None)
        if not org_id:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=(
                    "Local FS connector is not initialized for an org "
                    "(data_entities_processor.org_id is unset); "
                    "cannot dispatch storage uploads"
                ),
            )
        return str(org_id)

    async def _bulk_get_records_by_external_ids(
        self, external_ids: List[str]
    ) -> Dict[str, Record]:
        """One transaction, one lookup per id — beats opening a fresh
        transaction inside the per-event upload loop.
        """
        result: Dict[str, Record] = {}
        unique_ids = [eid for eid in {*external_ids} if eid]
        if not unique_ids:
            return result
        async with self.data_store_provider.transaction() as tx_store:
            for ext_id in unique_ids:
                record = await tx_store.get_record_by_external_id(
                    self.connector_id, ext_id
                )
                if record is not None:
                    result[ext_id] = record
        return result

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

    async def _upload_storage_file(
        self,
        *,
        rel_path: str,
        content: bytes,
        mime_type: str | None,
        existing_document_id: str | None = None,
        org_id: str | None = None,
        storage_url: str | None = None,
        storage_token: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> str:
        """Upload bytes to the storage service; return the document id.

        ``org_id``, ``storage_url``, ``storage_token``, ``session`` may be
        passed in to avoid re-fetching etcd config and re-establishing TLS for
        every event in a batch.
        """
        if storage_url is None:
            storage_url = await self._storage_base_url()
        if storage_token is None:
            storage_token = await self._storage_token()
        if org_id is None:
            org_id = self._require_org_id()
        upload_filename = self._build_storage_upload_filename(rel_path, mime_type)
        guessed, _ = mimetypes.guess_type(upload_filename)
        upload_mime = mime_type or guessed or MimeTypes.UNKNOWN.value

        form = aiohttp.FormData()
        if existing_document_id:
            url = (
                f"{storage_url}/api/v1/document/internal/"
                f"{existing_document_id}/uploadNextVersion"
            )
            form.add_field("nextVersionNote", "Local FS desktop sync")
        else:
            url = f"{storage_url}/api/v1/document/internal/upload"
            form.add_field("documentName", self._build_storage_document_name(rel_path))
            form.add_field(
                "documentPath",
                (
                    f"{LOCAL_FS_STORAGE_DOCUMENT_PATH_PREFIX}/"
                    f"{org_id}/{self.connector_id}"
                ),
            )
            form.add_field("isVersionedFile", "true")
        form.add_field(
            "file",
            content,
            filename=upload_filename,
            content_type=upload_mime,
        )

        timeout = LOCAL_FS_STORAGE_HTTP_TIMEOUT
        try:
            if session is None:
                async with aiohttp.ClientSession(timeout=timeout) as owned_session:
                    return await self._execute_storage_upload_request(
                        owned_session,
                        url,
                        form,
                        storage_token,
                        existing_document_id,
                        content,
                    )
            return await self._execute_storage_upload_request(
                session,
                url,
                form,
                storage_token,
                existing_document_id,
                content,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=HttpStatusCode.GATEWAY_TIMEOUT.value,
                detail=(
                    f"Storage service timed out during Local FS upload ({rel_path})"
                ),
            ) from exc
        except aiohttp.ClientError as exc:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_GATEWAY.value,
                detail=(
                    f"Could not reach storage service at {storage_url} for Local FS "
                    f"upload ({rel_path}): {exc}"
                ),
            ) from exc

    async def _execute_storage_upload_request(
        self,
        session: aiohttp.ClientSession,
        url: str,
        form: aiohttp.FormData,
        storage_token: str,
        existing_document_id: str | None,
        file_content: bytes,
    ) -> str:
        """Execute upload call and return resolved storage document id."""
        # Do not follow 301/302: aiohttp would re-issue as GET to Location, which
        # breaks presigned PUT URLs (see aiohttp issue #3082).
        async with session.post(
            url,
            data=form,
            headers={"Authorization": f"Bearer {storage_token}"},
            allow_redirects=False,
        ) as response:
            raw_text = await response.text()
            try:
                payload: JsonValue = json.loads(raw_text) if raw_text else {}
            except json.JSONDecodeError:
                payload = raw_text

            if response.status in STORAGE_UPLOAD_REDIRECT_STATUS_CODES:
                location = response.headers.get("Location") or response.headers.get(
                    "location"
                )
                if not location:
                    raise HTTPException(
                        status_code=HttpStatusCode.BAD_GATEWAY.value,
                        detail={
                            "message": (
                                "Storage direct-upload redirect missing Location "
                                "header"
                            ),
                            "status": response.status,
                            "response": payload,
                            "url": url,
                        },
                    )
                put_headers = {"Content-Length": str(len(file_content))}
                async with session.put(
                    location,
                    data=file_content,
                    headers=put_headers,
                ) as put_resp:
                    put_text = await put_resp.text()
                    if put_resp.status < 200 or put_resp.status >= 300:
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_GATEWAY.value,
                            detail={
                                "message": (
                                    "Direct upload to storage (presigned URL) failed"
                                ),
                                "status": put_resp.status,
                                "response": put_text,
                                "location": location,
                            },
                        )
                if existing_document_id:
                    return existing_document_id
                doc_hdr = response.headers.get("x-document-id") or response.headers.get(
                    "X-Document-Id"
                )
                if isinstance(doc_hdr, str) and doc_hdr.strip():
                    return doc_hdr.strip()
                if isinstance(payload, dict):
                    return self._extract_storage_document_id(payload)
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_GATEWAY.value,
                    detail=(
                        "Storage direct-upload response missing document id "
                        "(expected x-document-id header or JSON body)"
                    ),
                )

            if response.status < 200 or response.status >= 300:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_GATEWAY.value,
                    detail={
                        "message": "Storage service rejected Local FS file upload",
                        "status": response.status,
                        "response": payload,
                        "url": url,
                    },
                )
            if existing_document_id:
                return existing_document_id
            return self._extract_storage_document_id(payload)

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
            # Best-effort: a failed cleanup must not block the rename/delete
            # event from being marked synced; the orphaned blob can be GC'd later.
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

    async def _get_record_by_external_id(self, external_id: str) -> Optional[Record]:
        async with self.data_store_provider.transaction() as tx_store:
            return await tx_store.get_record_by_external_id(
                self.connector_id, external_id
            )

    async def _storage_document_id_for_external_id(self, external_id: str) -> str | None:
        record = await self._get_record_by_external_id(external_id)
        return self._storage_document_id_from_path(
            getattr(record, "path", None)
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

    def _build_storage_file_record(
        self,
        rel_path: str,
        event: LocalFsFileEvent,
        storage_document_id: str,
        external_record_group_id: str,
        indexing_filters: FilterCollection,
        content_size: int,
        owner: Optional[User] = None,
    ) -> Tuple[FileRecord, List[Permission]]:
        normalized_rel_path = rel_path.strip().replace("\\", "/")
        ext_id = self._external_record_id_for_rel_path(normalized_rel_path)
        name = Path(normalized_rel_path).name or "file"
        timestamp_ms = int(event.timestamp)
        size = event.size if event.size is not None else content_size
        guessed, _ = mimetypes.guess_type(name)
        mime = event.mimeType or guessed or MimeTypes.UNKNOWN.value
        ext = Path(name).suffix.lower().lstrip(".") or None
        revision = event.sha256 or f"{timestamp_ms}:{size}"

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
            weburl=f"/record/{record_id}",
            size_in_bytes=size,
            is_file=True,
            extension=ext,
            path=f"{LOCAL_FS_STORAGE_PATH_PREFIX}{storage_document_id}",
            mime_type=mime,
            preview_renderable=True,
            sha256_hash=event.sha256,
        )

        if not indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True):
            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        effective_owner = owner or self._owner_user_for_permissions
        perms: List[Permission] = []
        if effective_owner:
            perms.append(
                Permission(
                    external_id=effective_owner.id,
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

    async def _delete_external_ids(
        self, external_ids: List[str], user_id: str
    ) -> None:
        if not external_ids:
            return
        async with self.data_store_provider.transaction() as tx_store:
            for external_id in external_ids:
                await tx_store.delete_record_by_external_id(
                    self.connector_id, external_id, user_id
                )

    def _prepare_upsert_record(
        self,
        root: Path,
        rel_path: str,
        rg_external: str,
        sync_filters: FilterCollection,
        indexing_filters: FilterCollection,
        owner: Optional[User] = None,
    ) -> Optional[Tuple[FileRecord, List[Permission]]]:
        abs_path = self._resolve_event_file_path(root, rel_path)
        if abs_path.is_symlink() or not abs_path.is_file():
            return None
        if not self._extension_allowed(abs_path, sync_filters):
            return None
        st = abs_path.stat()
        if not _file_stat_matches_date_filters(st, sync_filters):
            return None
        return self._build_file_record(
            abs_path, root, rg_external, indexing_filters, st=st, owner=owner
        )

    async def _reset_existing_records(
        self, owner_user_id: str, delete_storage_documents: bool = False
    ) -> int:
        status_filters = [status.value for status in ProgressStatus]
        deleted = 0

        while True:
            storage_document_ids: List[str] = []
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
                    if delete_storage_documents:
                        document_id = self._storage_document_id_from_path(
                            getattr(record, "path", None)
                        )
                        if document_id:
                            storage_document_ids.append(document_id)
                    await tx_store.delete_record_by_external_id(
                        self.connector_id,
                        external_id,
                        owner_user_id,
                    )
                    deleted += 1
                    deleted_this_round += 1

                if deleted_this_round == 0:
                    return deleted
            for document_id in storage_document_ids:
                await self._delete_storage_document(document_id)

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

        ok_path, detail = _validate_sync_root_path(root_raw)
        if not ok_path:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Local FS cannot use sync_root_path: {detail}",
            )

        root = Path(detail)
        processed = 0
        deleted = 0
        upsert_buffer: List[Tuple[FileRecord, List[Permission]]] = []
        # See apply_uploaded_file_event_batch for ordering rationale: hold
        # the OLD-path delete from each rename until its replacement upsert
        # has flushed, so a mid-batch failure can never drop a record before
        # its successor exists.
        delete_after_upsert_buffer: List[str] = []
        delete_only_buffer: List[str] = []
        batch_size = max(1, self.batch_size)

        try:
            owner, sync_filters, indexing_filters, rg_external = (
                await self._ensure_owner_and_record_group(root)
            )

            async def flush_upserts() -> None:
                nonlocal processed, deleted
                if not upsert_buffer:
                    return
                await self.data_entities_processor.on_new_records(list(upsert_buffer))
                processed += len(upsert_buffer)
                upsert_buffer.clear()
                if delete_after_upsert_buffer:
                    await self._delete_external_ids(
                        list(delete_after_upsert_buffer), owner.id
                    )
                    deleted += len(delete_after_upsert_buffer)
                    delete_after_upsert_buffer.clear()

            async def flush_delete_only() -> None:
                nonlocal deleted
                if not delete_only_buffer:
                    return
                await self._delete_external_ids(
                    list(delete_only_buffer), owner.id
                )
                deleted += len(delete_only_buffer)
                delete_only_buffer.clear()

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
                    record = self._prepare_upsert_record(
                        root, rel_path, rg_external, sync_filters,
                        indexing_filters, owner=owner,
                    )
                    if record is not None:
                        upsert_buffer.append(record)
                        if len(upsert_buffer) >= batch_size:
                            await flush_upserts()
                    continue

                if event_type == "DELETED":
                    delete_only_buffer.append(
                        self._external_record_id_for_rel_path(rel_path)
                    )
                    if len(delete_only_buffer) >= batch_size:
                        await flush_delete_only()
                    continue

                if event_type in {"RENAMED", "MOVED"}:
                    record = self._prepare_upsert_record(
                        root, rel_path, rg_external, sync_filters,
                        indexing_filters, owner=owner,
                    )
                    if record is not None:
                        upsert_buffer.append(record)
                        if old_rel_path:
                            # Validate the old path the same way as the new
                            # one so a hostile rename can't sneak a path
                            # escape past us.
                            self._resolve_event_file_path(root, old_rel_path)
                            old_ext_id = self._external_record_id_for_rel_path(
                                old_rel_path
                            )
                            new_ext_id = self._external_record_id_for_rel_path(
                                rel_path
                            )
                            if old_ext_id != new_ext_id:
                                delete_after_upsert_buffer.append(old_ext_id)
                        if len(upsert_buffer) >= batch_size:
                            await flush_upserts()
                    elif old_rel_path:
                        # New file vanished or was filtered out; downgrade
                        # the rename to a plain DELETE of the old path.
                        self._resolve_event_file_path(root, old_rel_path)
                        delete_only_buffer.append(
                            self._external_record_id_for_rel_path(old_rel_path)
                        )
                        if len(delete_only_buffer) >= batch_size:
                            await flush_delete_only()
                    continue

                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Unsupported Local FS file event type: {event_type}",
                )

            await flush_upserts()
            await flush_delete_only()

            return LocalFsFileEventBatchStats(processed=processed, deleted=deleted)
        finally:
            self._owner_user_for_permissions = None

    @staticmethod
    def _normalize_uploaded_rel_path(raw_path: str) -> str:
        rel_path = raw_path.strip().replace("\\", "/")
        parts = rel_path.split("/")
        if (
            not rel_path
            or rel_path.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Invalid relative Local FS path: {raw_path}",
            )
        return rel_path

    async def apply_uploaded_file_event_batch(
        self,
        events: List[LocalFsFileEvent],
        files_by_field: Dict[str, bytes],
        reset_before_apply: bool = False,
    ) -> LocalFsFileEventBatchStats:
        """Apply desktop-uploaded Local FS events whose bytes are included in multipart.

        Ordering invariants (so a failure can't lose data):
          1. RENAMED/MOVED: upload new content + upsert new record FIRST,
             then delete the old DB record. The old storage blob is GC'd
             best-effort after the upsert succeeds.
          2. SHA mismatch on a single event ⇒ skip just that event with a
             warning. The remaining events in the batch still run; the
             watcher will resend a fresh MODIFIED for the changed file.
          3. The owner is threaded as a local var (no instance state during
             record building), so concurrent requests can't observe each
             other's owner.
        """
        await self._reload_sync_settings()
        org_id = self._require_org_id()
        root_for_display = Path(self.sync_root_path.strip() or "Local FS")
        processed = 0
        deleted = 0
        upsert_buffer: List[Tuple[FileRecord, List[Permission]]] = []
        # Old DB records to remove AFTER their replacement upsert succeeds —
        # for RENAMED/MOVED. Held separate from delete_only_buffer so we never
        # flush an old-path delete before its new-path upsert lands.
        delete_after_upsert_buffer: List[str] = []
        # External ids from explicit DELETED events.
        delete_only_buffer: List[str] = []
        # Old storage blobs to GC after the corresponding DB rows are gone.
        storage_blobs_to_gc: List[str] = []
        batch_size = max(1, self.batch_size)
        upload_timeout = LOCAL_FS_STORAGE_HTTP_TIMEOUT

        try:
            owner, sync_filters, indexing_filters, rg_external = (
                await self._ensure_owner_and_record_group(root_for_display)
            )

            # Memoize storage URL + JWT for the duration of this batch so the
            # per-event _upload_storage_file/_delete_storage_document calls
            # share one etcd lookup. A delete-only batch never trips this
            # because the cache stays empty.
            self._batch_storage_url_cache = None
            self._batch_storage_token_cache = None

            async def flush_upserts() -> None:
                nonlocal processed, deleted
                if not upsert_buffer:
                    return
                await self.data_entities_processor.on_new_records(list(upsert_buffer))
                processed += len(upsert_buffer)
                upsert_buffer.clear()
                # Once upserts land, it is safe to retire the matching
                # old-path DB rows. Do it before any further upserts so a
                # later mid-batch failure can't strand them as still-pending.
                if delete_after_upsert_buffer:
                    await self._delete_external_ids(
                        list(delete_after_upsert_buffer), owner.id
                    )
                    deleted += len(delete_after_upsert_buffer)
                    delete_after_upsert_buffer.clear()

            async def flush_delete_only() -> None:
                nonlocal deleted
                if not delete_only_buffer:
                    return
                await self._delete_external_ids(
                    list(delete_only_buffer), owner.id
                )
                deleted += len(delete_only_buffer)
                delete_only_buffer.clear()

            if reset_before_apply:
                deleted += await self._reset_existing_records(
                    owner.id, delete_storage_documents=True
                )

            # Bulk pre-fetch existing records for paths we'll need them for.
            relevant_ext_ids: List[str] = []
            for event in events:
                if event.isDirectory:
                    continue
                event_type = event.type.strip().upper()
                if event_type in {"CREATED", "MODIFIED", "RENAMED", "MOVED"}:
                    rel_path = self._normalize_uploaded_rel_path(event.path)
                    relevant_ext_ids.append(
                        self._external_record_id_for_rel_path(rel_path)
                    )
                if event_type in {"DELETED", "RENAMED", "MOVED"} and event.oldPath:
                    old_rel = self._normalize_uploaded_rel_path(event.oldPath)
                    relevant_ext_ids.append(
                        self._external_record_id_for_rel_path(old_rel)
                    )
                if event_type == "DELETED":
                    rel_path = self._normalize_uploaded_rel_path(event.path)
                    relevant_ext_ids.append(
                        self._external_record_id_for_rel_path(rel_path)
                    )
            existing_by_ext_id = await self._bulk_get_records_by_external_ids(
                relevant_ext_ids
            )

            async with aiohttp.ClientSession(timeout=upload_timeout) as session:
                for event in events:
                    event_type = event.type.strip().upper()
                    rel_path = self._normalize_uploaded_rel_path(event.path)
                    old_rel_path = (
                        self._normalize_uploaded_rel_path(event.oldPath)
                        if event.oldPath
                        else ""
                    )

                    if event.isDirectory:
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_REQUEST.value,
                            detail="Directory events must be expanded client-side before replay",
                        )

                    if event_type == "DELETED":
                        ext_id = self._external_record_id_for_rel_path(rel_path)
                        existing = existing_by_ext_id.get(ext_id)
                        document_id = self._storage_document_id_from_path(
                            getattr(existing, "path", None)
                        )
                        if document_id:
                            storage_blobs_to_gc.append(document_id)
                        delete_only_buffer.append(ext_id)
                        if len(delete_only_buffer) >= batch_size:
                            await flush_delete_only()
                        continue

                    if event_type not in {"CREATED", "MODIFIED", "RENAMED", "MOVED"}:
                        raise HTTPException(
                            status_code=HttpStatusCode.BAD_REQUEST.value,
                            detail=f"Unsupported Local FS file event type: {event_type}",
                        )

                    # Validate the multipart part is present before doing
                    # anything destructive (no DB writes on a malformed event).
                    if not event.contentField:
                        raise HTTPException(
                            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
                            detail=f"Uploaded Local FS event for {rel_path} is missing contentField",
                        )
                    if event.contentField not in files_by_field:
                        raise HTTPException(
                            status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
                            detail=f"Missing upload part for contentField {event.contentField}",
                        )
                    content = files_by_field[event.contentField]

                    if event.sha256:
                        actual_sha = hashlib.sha256(content).hexdigest()
                        if actual_sha.lower() != event.sha256.lower():
                            # The user edited the file between the desktop
                            # read and our check. Skip this event; the
                            # watcher will resend a fresh MODIFIED. Don't
                            # poison the rest of the batch.
                            self.logger.warning(
                                "Local FS: SHA-256 mismatch for %s "
                                "(file modified mid-flight); skipping this event",
                                rel_path,
                            )
                            continue

                    if not self._extension_allowed(Path(rel_path), sync_filters):
                        continue
                    if not self._event_matches_date_filters(
                        event, sync_filters
                    ):
                        continue

                    new_ext_id = self._external_record_id_for_rel_path(rel_path)
                    existing = existing_by_ext_id.get(new_ext_id)
                    existing_document_id = self._storage_document_id_from_path(
                        getattr(existing, "path", None)
                    )

                    storage_document_id = await self._upload_storage_file(
                        rel_path=rel_path,
                        content=content,
                        mime_type=event.mimeType,
                        existing_document_id=existing_document_id,
                        org_id=org_id,
                        # storage_url / storage_token are lazily resolved by
                        # the helper on first need so a delete-only batch
                        # avoids the etcd round trip; downstream tests can
                        # also mock _upload_storage_file without having to
                        # mock the JWT generator.
                        session=session,
                    )

                    upsert_buffer.append(
                        self._build_storage_file_record(
                            rel_path,
                            event,
                            storage_document_id,
                            rg_external,
                            indexing_filters,
                            len(content),
                            owner=owner,
                        )
                    )

                    # For renames at a different path, queue the OLD path's
                    # DB row for deletion AFTER the upsert flushes (not
                    # before — would otherwise drop the previous record
                    # before its replacement is persisted, leaving a brief
                    # window of data loss visible to search).
                    if (
                        event_type in {"RENAMED", "MOVED"}
                        and old_rel_path
                    ):
                        old_ext_id = self._external_record_id_for_rel_path(
                            old_rel_path
                        )
                        if old_ext_id != new_ext_id:
                            old_existing = existing_by_ext_id.get(old_ext_id)
                            old_doc_id = self._storage_document_id_from_path(
                                getattr(old_existing, "path", None)
                            )
                            if old_doc_id and old_doc_id != storage_document_id:
                                storage_blobs_to_gc.append(old_doc_id)
                            delete_after_upsert_buffer.append(old_ext_id)

                    if len(upsert_buffer) >= batch_size:
                        await flush_upserts()

                # End of loop: drain any remaining buffers in the right order.
                await flush_upserts()
                await flush_delete_only()

                # Best-effort GC of orphaned storage blobs after the DB rows
                # that referenced them are gone. Reuse the open session.
                for document_id in storage_blobs_to_gc:
                    await self._delete_storage_document(
                        document_id,
                        session=session,
                    )

            return LocalFsFileEventBatchStats(processed=processed, deleted=deleted)
        finally:
            self._owner_user_for_permissions = None
            self._batch_storage_url_cache = None
            self._batch_storage_token_cache = None

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
        storage_document_id = self._storage_document_id_from_path(record.path)
        if storage_document_id:
            return await self._stream_storage_record(record, storage_document_id)
        await self._reload_sync_settings()
        root_raw = self.sync_root_path.strip()
        if not root_raw:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Local FS sync_root_path is not configured",
            )
        ok_path, detail = _validate_sync_root_path(root_raw)
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

        try:
            handle = await asyncio.to_thread(p.open, "rb")
        except OSError as e:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail=f"Cannot read file: {e}",
            ) from e

        async def _stream_chunks() -> AsyncIterator[bytes]:
            chunk_size = 1 << 20  # 1 MiB
            try:
                while True:
                    chunk = await asyncio.to_thread(handle.read, chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await asyncio.to_thread(handle.close)

        media = record.mime_type or "application/octet-stream"
        return create_stream_record_response(
            _stream_chunks(),
            record.record_name,
            media,
            fallback_filename="file",
        )

    async def run_sync(self) -> None:
        await self._reload_sync_settings()

        root_raw = self.sync_root_path.strip()
        if not root_raw:
            self.logger.warning(
                "Local FS: sync_root_path is empty; set Local folder path in the app or run pipeshub setup."
            )
            return

        ok_path, detail = _validate_sync_root_path(root_raw)
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

            deleted = await self._reset_existing_records(
                owner.id, delete_storage_documents=True
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
                    if not _file_stat_matches_date_filters(st, sync_filters):
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
                "Local FS: finished sync from %s (%d file(s) processed, %d stale record(s) deleted)",
                root,
                processed,
                deleted,
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
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
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
