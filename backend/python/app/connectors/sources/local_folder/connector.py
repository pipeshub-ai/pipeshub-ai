"""Directory sync: index a directory the server itself owns (the base of the Local Repository connector).

One connector instance crawls one root that the server chooses; the root is never taken from the client.

Incremental sync compares a stored manifest of ``(path, mtime_ns, size, sha256)``
with a fresh walk. Only files whose mtime or size moved are re-hashed, and only
a changed hash becomes a record update. A path missing from the walk is deleted.
Filesystem watching is not implemented: a scheduled rescan is cheap with the manifest.
"""

import asyncio
import json
import mimetypes
import os
import unicodedata
import uuid
from collections.abc import AsyncGenerator
from logging import Logger
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
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
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    FilterOptionsResponse,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.local_folder import scan
from app.models.entities import (
    AppUser,
    CodeFileRecord,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.modules.parsers.code_parser.file_role import should_index_code_file
from app.modules.parsers.code_parser.lang_config import detect_language
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.utils.time_conversion import get_epoch_timestamp_in_ms

IGNORE_KEY = "ignore_patterns"
GITIGNORE_KEY = "respect_gitignore"
MAX_MB_KEY = "max_file_size_mb"
PRUNE_PAGE = 500
# Office/PDF files are zip or compressed streams with NUL bytes; the normal parsers read them by mime type.
# The legacy OLE formats have a parser branch in app/events/events.py too (DOC, XLS, PPT).
DOCUMENT_MIMES = {
    m.value for m in (MimeTypes.PDF, MimeTypes.DOCX, MimeTypes.XLSX, MimeTypes.PPTX,
                      MimeTypes.DOC, MimeTypes.XLS, MimeTypes.PPT)
}
CHUNK = 256 * 1024


def _cfg(sync_cfg: Any, key: str, default: Any = None) -> Any:  # noqa: ANN401
    """Read a sync value from the flat, ``values`` or ``customValues`` config shapes."""
    if not isinstance(sync_cfg, dict):
        return default
    for src in (sync_cfg, sync_cfg.get("values"), sync_cfg.get("customValues")):
        if isinstance(src, dict) and src.get(key) not in (None, ""):
            return src[key]
    return default


def _decode_rows(raw: object) -> list:
    """The manifest rows of a sync point: a JSON string (current), or the list of lists an earlier version wrote."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return raw if isinstance(raw, list) else []


def _as_bool(raw: Any, default: bool) -> bool:  # noqa: ANN401, FBT001
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return default


def _patterns(raw: Any) -> list[str]:  # noqa: ANN401
    if isinstance(raw, list):
        raw = "\n".join(str(x) for x in raw)
    return [p.strip() for p in str(raw or "").replace(",", "\n").splitlines() if p.strip()]


def external_id(rel: str) -> str:
    """Immutable id of a file or directory: its root-relative path, NFC-normalised (macOS gives NFD)."""
    return unicodedata.normalize("NFC", rel)


def _mime(name: str, ext: str | None) -> str:
    if ext is None:
        return MimeTypes.PLAIN_TEXT.value
    named = MimeTypes.__members__.get(ext.upper())
    if named is not None:
        return named.value
    guessed, _ = mimetypes.guess_type(name)
    return guessed or MimeTypes.PLAIN_TEXT.value


def _ancestors(rel: str) -> list[str]:
    parts = rel.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _ext_allowed(rel: str, sync_filters: FilterCollection) -> bool:
    flt = sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)
    if flt is None or flt.is_empty() or not isinstance(flt.value, list):
        return True
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel.rsplit("/", 1)[-1] else None
    wanted = {e.lower().lstrip(".") for e in flt.value}
    op = flt.get_operator()
    op = op.value if hasattr(op, "value") else str(op)
    if op == FilterOperator.IN:
        return ext in wanted
    if op == FilterOperator.NOT_IN:
        return ext not in wanted
    return True


class FolderApp(App):
    def __init__(self, connector_id: str, connector: Connectors = Connectors.LOCAL_REPO) -> None:
        super().__init__(connector, AppGroups.LOCAL_STORAGE, connector_id)


class FolderConnector(BaseConnector):
    """The walk, ignore rules, manifest reconcile, deletes, records and content read-back for one root directory.

    Not registered as a connector of its own: a root must be chosen by the server (``_resolve_root``), never by a client, and the only
    subclass is the Local Repository connector, whose root is the server's mirror of the files a person pushed.
    """
    MAX_LINKS = 1  # hard links a file may have: one, unless the root is the server's own
    CONNECTOR = Connectors.LOCAL_REPO
    FILTER_KEY = "localrepository"
    DEFAULT_IGNORES = scan.DEFAULT_IGNORES

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
            FolderApp(connector_id, self.CONNECTOR), logger, data_entities_processor, data_store_provider,
            config_service, connector_id, scope, created_by,
        )
        self.connector_name = self.CONNECTOR
        self.repo_name: str | None = None   # shown as the repository name when the whole root is one repository
        self.web_base = ""
        self.connector_id = connector_id
        self.root: Path | None = None
        self.user_patterns: list[str] = []
        self.use_gitignore = True
        self.max_bytes = scan.DEFAULT_MAX_FILE_BYTES
        self.batch_size = 50
        self.record_sync_point = SyncPoint(
            connector_id=connector_id,
            org_id=data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )

    # ------------------------------------------------------------- config
    async def _load_config(self) -> None:
        """Re-read config; raises ``scan.PathRefused`` if the root is not an allowed one."""
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")
        sync_cfg = (config or {}).get("sync", {}) or {}
        # The allow-list comes from the environment on every load, never from stored config.
        self.web_base = ""
        self.root = await asyncio.to_thread(self._resolve_root, sync_cfg)  # a subclass may take a lock or read the disk: not on the shared loop
        self.web_base = self.web_base or f"file://{self.root}"
        self.user_patterns = _patterns(_cfg(sync_cfg, IGNORE_KEY, ""))
        self.use_gitignore = _as_bool(_cfg(sync_cfg, GITIGNORE_KEY, True), True)
        try:
            mb = float(_cfg(sync_cfg, MAX_MB_KEY, 5))
        except (TypeError, ValueError):
            mb = 5
        self.max_bytes = int(max(1, min(mb, 100)) * 1024 * 1024)
        try:
            self.batch_size = max(1, int(_cfg(sync_cfg, "batchSize", _cfg(sync_cfg, "batch_size", 50))))
        except (TypeError, ValueError):
            self.batch_size = 50

    def _resolve_root(self, sync_cfg: Any) -> Path:  # noqa: ANN401
        raise NotImplementedError

    async def init(self) -> bool:
        try:
            await self._load_config()
        except scan.PathRefused as exc:
            # Saving config before choosing a folder is normal; sync reports it.
            self.logger.info("Folder: no usable root yet (%s)", exc)
        except Exception:
            self.logger.error("Folder init failed", exc_info=True)
            return False
        return True

    async def test_connection_and_access(self) -> bool:
        try:
            await self._load_config()
            return bool(self.root and os.access(self.root, os.R_OK | os.X_OK))
        except scan.PathRefused as exc:
            self.logger.warning("Folder: %s", exc)
            return False

    async def get_signed_url(self, record: Record) -> str | None:
        return None

    # --------------------------------------------------------- builders
    def _group_id(self) -> str:
        return f"folder:{self.connector_id}"

    def _repo_of(self, rel: str) -> str | None:
        """The git repository (a directory holding ``.git``, relative to the root; "" is the root itself) a path lives in, if any."""
        best = None
        for repo in getattr(self, "_repos", ()):
            if (repo == "" or rel.startswith(repo + "/")) and (best is None or len(repo) > len(best)):
                best = repo
        return best

    def _repo_group_id(self, repo: str) -> str:
        # The suffix is what the code-graph tools list repositories by (see agents/actions/code_graph/repos.py)
        return f"{self._group_id()}:{repo or '.'}-code-repository"

    def _find_repos(self, entries: list[scan.Entry]) -> tuple[str, ...]:
        candidates = {""} | {d for e in entries for d in _ancestors(e.rel)}
        return tuple(sorted(d for d in candidates if os.path.exists(os.path.join(self.root, d, ".git"))))

    def _common(self, rel: str, name: str) -> dict[str, Any]:
        parent = _ancestors(rel)[-1] if "/" in rel else None
        now = get_epoch_timestamp_in_ms()
        return dict(  # noqa: C408
            id=str(uuid.uuid4()),
            org_id=self.data_entities_processor.org_id,
            record_name=name,
            external_record_id=external_id(rel),
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            record_group_type=RecordGroupType.DRIVE,
            external_record_group_id=self._group_id(),
            parent_external_record_id=external_id(parent) if parent else None,
            parent_record_type=RecordType.FILE if parent else None,
            created_at=now,
            updated_at=now,
            weburl=f"{self.web_base}/{rel}",
            hide_weburl=True,
        )

    def _folder_record(self, rel: str) -> FileRecord:
        return FileRecord(
            **self._common(rel, rel.rsplit("/", 1)[-1]),
            record_type=RecordType.FILE, is_file=False, is_internal=True,
            mime_type=MimeTypes.FOLDER.value, preview_renderable=False,
            external_revision_id=None, size_in_bytes=0, path=rel,
        )

    def _file_record(
        self, entry: scan.Entry, sha: str, manual_only: bool  # noqa: FBT001
    ) -> Record | None:
        name = entry.rel.rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else None
        mtime_ms = entry.mtime_ns // 1_000_000
        common = self._common(entry.rel, name)
        common.update(
            external_revision_id=sha, mime_type=_mime(name, ext),
            source_updated_at=mtime_ms, source_created_at=mtime_ms,
            size_in_bytes=entry.size,
        )
        language = detect_language(name)
        if language:
            ok, role = self._may_index_code(entry, name)
            if not ok:
                return None
            rec: Record = CodeFileRecord(
                **common, record_type=RecordType.CODE_FILE, extension=ext, language=language,
                file_role=role.value, file_path=entry.rel, file_hash=sha, preview_renderable=True,
            )
            repo = self._repo_of(entry.rel)
            if repo is not None:      # code in a git repository is filed under the repository's own group
                rec.external_record_group_id = self._repo_group_id(repo)
        else:
            rec = FileRecord(
                **common, record_type=RecordType.FILE, is_file=True, extension=ext,
                path=entry.rel, sha256_hash=sha, preview_renderable=True,
            )
        if manual_only:
            rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return rec

    async def _owner_and_group(self) -> None:
        raw = await self.data_entities_processor.get_user_by_user_id(user_id=self.created_by)
        if raw is None:
            raise HTTPException(HttpStatusCode.BAD_REQUEST.value, "Folder owner could not be resolved")
        user = raw if isinstance(raw, User) else User.from_arango_user(raw)
        await self.data_entities_processor.on_new_app_users([AppUser(
            app_name=self.connector_name, connector_id=self.connector_id, source_user_id=user.id,
            org_id=user.org_id or self.data_entities_processor.org_id, email=user.email,
            full_name=user.full_name or user.email,
            is_active=user.is_active if user.is_active is not None else True,
        )])
        # Container rule: one record group per root, owned by the user who configured it.
        group = RecordGroup(
            org_id=self.data_entities_processor.org_id, name=self.repo_name or self.root.name or str(self.root),
            external_group_id=self._group_id(), connector_name=self.connector_name,
            connector_id=self.connector_id, group_type=RecordGroupType.DRIVE,
            web_url=self.web_base, inherit_permissions=False,
        )
        owner = Permission(email=user.email, type=PermissionType.OWNER, entity_type=EntityType.USER)
        self._owner_permission = owner
        await self.data_entities_processor.on_new_record_groups([(group, [owner])])

    async def _repo_groups(self, repos: tuple[str, ...]) -> None:
        """One record group per git repository, owned like the root group: its code files are what the code graph is built from."""
        groups = [(RecordGroup(
            org_id=self.data_entities_processor.org_id, name=repo or self.repo_name or self.root.name or str(self.root),
            external_group_id=self._repo_group_id(repo), connector_name=self.connector_name, connector_id=self.connector_id,
            # The same single grant as the root group (the creator), so the repository is never reachable by more people. It is not a child of the
            # root group: a code repository with a parent is named after that parent.
            group_type=RecordGroupType.DRIVE, web_url=f"{self.web_base}/{repo}".rstrip("/"), inherit_permissions=False,
        ), [self._owner_permission]) for repo in repos]
        if groups:
            await self.data_entities_processor.on_new_record_groups(groups)

    # ------------------------------------------------------------- sync
    def _sync_key(self) -> str:
        return generate_record_sync_point_key(RecordType.FILE.value, "folder", self.connector_id)

    def _inspect(self, entry: scan.Entry) -> tuple[str, bool]:
        """sha256 of the file and whether it looks binary (blocking; run in a thread)."""
        fd = scan.safe_open(self.root, entry.rel, self.MAX_LINKS)
        try:
            binary = scan.looks_binary(fd)
        except BaseException:
            os.close(fd)
            raise
        return scan.sha256_fd(fd), binary

    async def run_sync(self) -> None:
        try:
            await self._load_config()
        except scan.PathRefused as exc:
            self.logger.error("Folder sync refused: %s", exc)
            raise ValueError(f"Folder: {exc}. Choose one of the allowed folders in the connector settings.") from exc
        sync_filters, indexing_filters = await load_connector_filters(
            self.config_service, self.FILTER_KEY, self.connector_id, self.logger
        )
        manual_only = indexing_filters.is_enabled(IndexingFilterKey.ENABLE_MANUAL_SYNC, default=False)
        await self._owner_and_group()

        point = await self.record_sync_point.read_sync_point(self._sync_key())
        # Rows, not a dict: a sync point update merges nested objects and would never drop a key.
        prev = {r[0]: (r[1], r[2], r[3]) for r in _decode_rows(point.get("files"))}
        prev_dirs = set(point.get("dirs") or [])
        if point.get("root") != self._root_identity():
            # A different root (or a sync point from before roots were recorded): the manifest is not about this tree.
            prev, prev_dirs = {}, set()
        stored = await self._stored_ids()
        if stored is not None:
            # Records lost from the store (purge, restore) are rewritten even when the file is unchanged.
            prev = {k: v for k, v in prev.items() if not v[2] or external_id(k) in stored}
            prev_dirs = {d for d in prev_dirs if external_id(d) in stored}
        started = get_epoch_timestamp_in_ms()

        entries = await asyncio.to_thread(
            lambda: [e for e in scan.walk(self.root, self.user_patterns, self.max_bytes, self.use_gitignore, self.DEFAULT_IGNORES, self.MAX_LINKS)
                     if _ext_allowed(e.rel, sync_filters)]
        )
        self._repos = await asyncio.to_thread(self._find_repos, entries)
        await self._repo_groups(self._repos)
        manifest: dict[str, tuple[int, int, str]] = {}
        batch: list[tuple[Record, list[Permission]]] = []
        failed = 0
        unreadable: set[str] = set()
        changed = 0

        async def flush() -> None:
            nonlocal batch
            if batch:
                await self.data_entities_processor.on_new_records(batch)
                batch = []

        # Directories first, shallow to deep, so a parent exists before its children.
        # A directory counts only if a file survived; those are known after the loop,
        # so files are collected and written in a second pass.
        pending: list[tuple[scan.Entry, str]] = []
        for e in entries:
            old = prev.get(e.rel)
            if old and old[0] == e.mtime_ns and old[1] == e.size:
                manifest[e.rel] = old
                continue
            try:
                sha, binary = await asyncio.to_thread(self._inspect, e)
            except (scan.PathRefused, OSError) as exc:
                failed += 1
                unreadable.add(e.rel)
                self.logger.warning("Folder: cannot read %s: %s", e.rel, exc)
                if old:
                    manifest[e.rel] = old  # keep it; do not delete on a read error
                continue
            has_ext = "." in e.rel.rsplit("/", 1)[-1]
            name = e.rel.rsplit("/", 1)[-1]
            is_doc = has_ext and _mime(name, name.rsplit(".", 1)[-1].lower()) in DOCUMENT_MIMES
            if binary and not is_doc and not (has_ext and detect_language(e.rel)):
                manifest[e.rel] = (e.mtime_ns, e.size, "")  # known skip
                continue
            manifest[e.rel] = (e.mtime_ns, e.size, sha)
            if old and old[2] == sha:
                continue  # touched, content identical
            pending.append((e, sha))

        live = {r for r, v in manifest.items() if v[2]}
        dirs = {d for r in live for d in _ancestors(r)}
        for d in sorted(dirs - prev_dirs, key=lambda x: x.count("/")):
            batch.append((self._folder_record(d), []))
            if len(batch) >= self.batch_size:
                await flush()
        await flush()
        for e, sha in self._in_index_order(pending):
            rec = await asyncio.to_thread(self._file_record, e, sha, manual_only)  # it may read the file (is it generated?): not on the shared loop
            if rec is None:
                manifest[e.rel] = (e.mtime_ns, e.size, "")
                live.discard(e.rel)
                continue
            batch.append((rec, []))
            changed += 1
            if len(batch) >= self.batch_size:
                await flush()
        await flush()

        # A path that changed between file and directory is live under its new kind: never delete it.
        gone = [r for r, v in prev.items() if v[2] and r not in live and r not in dirs]
        gone_dirs = prev_dirs - dirs - live
        undeleted = await self._delete([*gone, *sorted(gone_dirs, key=lambda x: -x.count("/"))])
        for r in undeleted:  # retry next run
            if r in prev:
                manifest[r] = prev[r]
        deleted_fail = len(undeleted)

        # Every run: records for paths the walk no longer sees (old root, an earlier instance, a lost delete).
        # Paths that failed to read this run are kept, so a transient error never deletes.
        keep = live | dirs | unreadable | {a for u in unreadable for a in _ancestors(u)} | set(undeleted)
        deleted_fail += len(await self._prune_unseen(keep, stored))

        self.logger.info(
            "Folder sync of %s: %d walked, %d changed, %d removed, %d unreadable, %d delete failures",
            self.root, len(entries), changed, len(gone) + len(gone_dirs) - deleted_fail, failed, deleted_fail,
        )
        await self._sync_finished({r: manifest[r][1] for r in live if r in manifest}, set(manifest))
        await self.record_sync_point.update_sync_point(self._sync_key(), {
            # A JSON string: the Neo4j store refuses a list of lists as a property.
            "files": json.dumps([[r, *v] for r, v in manifest.items()]),
            "dirs": sorted(dirs | {d for d in prev_dirs if d in undeleted}),
            "root": self._root_identity(),
            "last_sync_time": started,
        })

    def _root_identity(self) -> str:
        """What the sync point records as 'the tree these files belong to'; a different one resets the file list."""
        return str(self.root)

    def _may_index_code(self, entry: scan.Entry, name: str) -> tuple[bool, Any]:  # noqa: ANN401, RUF100
        """Whether a code file gets a record, and its role; a connector with more to go on than the path overrides it."""
        return should_index_code_file(entry.rel, name)

    def _in_index_order(self, pending: list[tuple[scan.Entry, str]]) -> list[tuple[scan.Entry, str]]:
        return pending

    async def _sync_finished(self, live: dict[str, int], walked: set[str] | None = None) -> None:
        """Called at the end of a sync with the size of every file that has a record, and every path the walk saw."""

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    async def _delete(self, ext_ids: list[str]) -> list[str]:
        """Retire records by external id; return the ids that failed."""
        failed = []
        for x in ext_ids:
            try:
                rec = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, external_id(x)
                )
                if rec is not None:
                    await self.data_entities_processor.on_record_deleted(record_id=rec.id)
            except asyncio.CancelledError:
                raise
            except Exception:
                failed.append(x)
                self.logger.error("Folder: could not delete record for %s", x, exc_info=True)
        return failed

    async def _stored_ids(self) -> set[str] | None:
        """External ids of every record the store holds for this connector, or None if it cannot be listed."""
        ids: set[str] = set()
        offset = 0
        statuses = [s.value for s in ProgressStatus]
        try:
            while True:
                page = await self.data_entities_processor.get_records_by_status(
                    self.connector_id, statuses, limit=PRUNE_PAGE, offset=offset,
                    visibility=RecordVisibility.ALL,
                )
                if not page:
                    return ids
                ids.update(r.external_record_id for r in page)
                offset += len(page)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.warning("Folder: could not list stored records; skipping reconcile", exc_info=True)
            return None

    async def _prune_unseen(self, seen: set[str], stored: set[str] | None) -> list[str]:
        """After a first full walk, retire records the walk did not see (an earlier instance left them)."""
        seen_ids = {external_id(s) for s in seen}
        return await self._delete([x for x in (stored or ()) if x not in seen_ids])

    # ---------------------------------------------------------- content
    async def stream_record(
        self, record: Record, user_id: str | None = None, convertTo: str | None = None
    ) -> StreamingResponse:
        try:
            await self._load_config()
        except scan.PathRefused as exc:
            raise HTTPException(HttpStatusCode.NOT_FOUND.value, f"Folder is not available: {exc}") from exc
        rel = record.external_record_id or ""
        fd = None
        # The id is NFC; the disk name may be NFD (macOS-created files on Linux).
        for cand in (rel, unicodedata.normalize("NFD", rel)):
            try:
                fd = scan.safe_open(self.root, cand, self.MAX_LINKS)
                break
            except scan.PathRefused as exc:
                last = exc
        if fd is None:
            raise HTTPException(HttpStatusCode.NOT_FOUND.value, f"File unavailable: {last}")
        name = rel.rsplit("/", 1)[-1]

        async def body() -> AsyncGenerator[bytes, None]:
            with os.fdopen(fd, "rb") as f:
                while chunk := await asyncio.to_thread(f.read, CHUNK):
                    yield chunk

        return StreamingResponse(
            body(), media_type=record.mime_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name.encode("ascii", "replace").decode().replace(chr(34), "_")}"'},
        )

    # -------------------------------------------------------- lifecycle
    def handle_webhook_notification(self, notification: dict) -> None:
        raise NotImplementedError("Folder connector does not use webhooks")

    async def cleanup(self) -> None:
        self.root = None

    async def reindex_records(self, record_results: list[Record]) -> None:
        if record_results:
            await self.data_entities_processor.reindex_existing_records(record_results)

    @classmethod
    async def create_connector(
        cls, logger: Logger, data_store_provider: DataStoreProvider,
        config_service: ConfigurationService, connector_id: str, scope: str, created_by: str,
        data_entities_processor, **kwargs,  # noqa: ANN001, ANN003
    ) -> "FolderConnector":
        return cls(
            logger, data_entities_processor, data_store_provider, config_service,
            connector_id, scope, created_by,
        )

    async def get_filter_options(
        self, filter_key: str, page: int = 1, limit: int = 20,
        search: str | None = None, cursor: str | None = None,
    ) -> FilterOptionsResponse:
        raise NotImplementedError("Folder connector does not support dynamic filter options")
