"""Shared sync/stream/filter-option operations for SMB and CIFS connectors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    not_downloadable,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOption,
    FilterOptionsResponse,
    IndexingFilterKey,
)
from app.connectors.core.registry.folder_scope import FolderScope, clean_up_scope
from app.connectors.sources.network_share.errors import ShareListingError
from app.connectors.sources.network_share.permissions import app_level_permissions
from app.connectors.sources.network_share.record_mapper import (
    RecordMapper,
    revision_id,
)
from app.connectors.sources.network_share.walker import ShareWalker, WalkResult
from app.models.entities import (
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.utils.streaming import create_stream_record_response

if TYPE_CHECKING:
    from logging import Logger

    from fastapi.responses import StreamingResponse

    from app.config.constants.arangodb import Connectors
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.sources.network_share.entry import ShareInfo
    from app.connectors.sources.network_share.protocol import INetworkShareDataSource
    from app.models.permission import Permission


def _disk_shares(shares: list[ShareInfo]) -> list[str]:
    names: list[str] = []
    for share in shares:
        if share.share_type in {"ipc", "print", "device"}:
            continue
        name = share.name.strip()
        if not name:
            continue
        names.append(name)
    return names


def _is_admin_share(name: str) -> bool:
    """Windows admin shares: ADMIN$, IPC$, and a drive letter plus $ (C$, D$)."""
    upper = name.upper()
    if upper in {"IPC$", "ADMIN$"}:
        return True
    return len(upper) == 2 and upper[0].isalpha() and upper[1] == "$"


def _drop_admin_shares(shares: list[ShareInfo]) -> list[ShareInfo]:
    return [share for share in shares if not _is_admin_share(share.name)]


async def resolve_shares(
    sync_filters: FilterCollection,
    configured_share: str | None,
) -> list[str]:
    """Names to crawl. An empty filter uses the configured share, not every listed disk."""
    share_filter = sync_filters.get("shares")
    selected = share_filter.value if share_filter and share_filter.value else []
    if selected:
        return [str(name) for name in selected if name]
    if configured_share:
        return [configured_share]
    return []


async def create_share_groups(
    *,
    share_names: list[str],
    processor: DataSourceEntitiesProcessor,
    connector_name: Connectors,
    connector_id: str,
    scope: str,
    created_by: str,
    creator_email: str | None,
    description_prefix: str,
) -> None:
    if not share_names:
        return
    permissions = app_level_permissions(
        scope=scope,
        org_id=processor.org_id,
        creator_email=creator_email,
        created_by=created_by,
    )
    groups = [
        (
            RecordGroup(
                name=name,
                external_group_id=name,
                group_type=RecordGroupType.FILE_SHARE,
                connector_name=connector_name,
                connector_id=connector_id,
                description=f"{description_prefix}: {name}",
                inherit_permissions=False,
            ),
            permissions,
        )
        for name in share_names
        if name
    ]
    if groups:
        await processor.on_new_record_groups(groups)


async def prune_unseen(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    seen: set[str],
    logger: Logger,
) -> None:
    records = await processor.get_records_by_record_type(connector_id, RecordType.FILE)
    stale = [r for r in records if r.external_record_id not in seen]
    if not stale:
        return
    logger.info("Removing %s records no longer in the synced shares", len(stale))
    for record in stale:
        try:
            await processor.on_record_deleted(record.id)
        except Exception as exc:
            logger.warning("Failed to delete record %s: %s", record.external_record_id, exc)


def make_walker(
    *,
    data_source: INetworkShareDataSource,
    processor: DataSourceEntitiesProcessor,
    mapper: RecordMapper,
    logger: Logger,
    connector_name: Connectors,
    connector_id: str,
    batch_size: int,
    scope: str,
    created_by: str,
    creator_email: str | None,
) -> ShareWalker:
    async def get_by_external_id(ext_id: str) -> FileRecord | None:
        record = await processor.get_record_by_external_id(connector_id, ext_id)
        return record if isinstance(record, FileRecord) else None

    async def get_by_revision(rev: str) -> FileRecord | None:
        record = await processor.get_record_by_external_revision_id(connector_id, rev)
        return record if isinstance(record, FileRecord) else None

    async def flush_upserts(batch: list[tuple[FileRecord, list[Permission]]]) -> None:
        await processor.on_new_records(batch)

    async def flush_moves(batch: list[tuple[str, FileRecord, list[Permission]]]) -> None:
        await processor.on_records_moved(batch)

    def permissions_for(_record: FileRecord) -> list[Permission]:
        return app_level_permissions(
            scope=scope,
            org_id=processor.org_id,
            creator_email=creator_email,
            created_by=created_by,
        )

    return ShareWalker(
        data_source=data_source,
        mapper=mapper,
        logger=logger,
        connector_name=connector_name,
        connector_id=connector_id,
        batch_size=batch_size,
        get_by_external_id=get_by_external_id,
        get_by_revision=get_by_revision,
        flush_upserts=flush_upserts,
        flush_moves=flush_moves,
        permissions_for=permissions_for,
    )


def _checkpoint_ms(stored: object) -> int:
    if not isinstance(stored, dict):
        return 0
    raw = stored.get("last_sync_time") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def _save_share_checkpoint(
    record_sync_point: SyncPoint,
    share_name: str,
    max_timestamp_ms: int,
) -> None:
    """Keep the watermark from moving backward. An unfinished walk does not call this."""
    key = generate_record_sync_point_key(RecordType.FILE.value, "share", share_name)
    previous = _checkpoint_ms(await record_sync_point.read_sync_point(key))
    watermark = max(previous, max_timestamp_ms)
    if watermark <= 0:
        return
    await record_sync_point.update_sync_point(key, {"last_sync_time": watermark})


async def walk_shares(
    *,
    data_source: INetworkShareDataSource,
    processor: DataSourceEntitiesProcessor,
    mapper: RecordMapper,
    logger: Logger,
    connector_name: Connectors,
    connector_id: str,
    batch_size: int,
    scope: str,
    created_by: str,
    creator_email: str | None,
    shares: list[str],
    sync_filters: FilterCollection,
    indexing_filters: FilterCollection,
    record_sync_point: SyncPoint,
    prune: bool,
) -> None:
    walker = make_walker(
        data_source=data_source,
        processor=processor,
        mapper=mapper,
        logger=logger,
        connector_name=connector_name,
        connector_id=connector_id,
        batch_size=batch_size,
        scope=scope,
        created_by=created_by,
        creator_email=creator_email,
    )
    seen: set[str] = set()
    complete = True
    folder_scope = FolderScope.from_filters(sync_filters)
    for share_name in shares:
        if not share_name:
            continue
        try:
            result: WalkResult = await walker.walk_share(
                share_name, sync_filters, indexing_filters
            )
            seen |= result.seen
            complete = complete and result.complete
            if result.complete:
                await clean_up_scope(
                    processor,
                    record_sync_point,
                    connector_id,
                    share_name,
                    folder_scope,
                    logger,
                )
                # A rename keeps the file's timestamps, and a directory's mtime
                # does not change when a file inside it is edited. The checkpoint
                # is a watermark, not a listing filter.
                await _save_share_checkpoint(
                    record_sync_point, share_name, result.max_timestamp_ms
                )
        except Exception:
            complete = False
            logger.exception("Error syncing share %s", share_name)
    if prune and complete:
        await prune_unseen(processor, connector_id, seen, logger)
    elif prune and not complete:
        logger.warning(
            "Some listings failed; not removing records that were not seen this sync"
        )


def io_share_and_path(record: Record) -> tuple[str, str] | None:
    """Path the server stored. ``record.path`` keeps that name; the external id is NFC."""
    share_name = record.external_record_group_id
    raw = getattr(record, "path", None)
    if share_name and isinstance(raw, str) and raw:
        return share_name, raw
    return extract_share_and_path(record)


def extract_share_and_path(record: Record) -> tuple[str, str] | None:
    share_name = record.external_record_group_id
    external_id = record.external_record_id
    if not share_name or not external_id:
        return None
    if external_id.startswith(f"{share_name}/"):
        path = external_id[len(share_name) + 1 :]
    else:
        path = external_id.lstrip("/")
    if not path:
        return None
    return share_name, path


async def stream_file(
    *,
    data_source: INetworkShareDataSource | None,
    record: Record,
    display_name: str,
) -> StreamingResponse:
    if isinstance(record, FileRecord) and not record.is_file:
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Cannot stream directory content",
        )
    if not data_source:
        raise connector_not_ready(display_name)
    path_info = io_share_and_path(record)
    if not path_info:
        raise not_downloadable(
            "This item is missing the share and path it belongs to and cannot be downloaded.",
            connector=display_name,
        )
    share_name, file_path = path_info
    try:
        return create_stream_record_response(
            data_source.read_file(share_name, file_path),
            filename=record.record_name,
            mime_type=record.mime_type or "application/octet-stream",
            fallback_filename=f"record_{record.id}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise to_stream_error(exc, connector=display_name) from exc


async def reindex_records(
    *,
    data_source: INetworkShareDataSource,
    processor: DataSourceEntitiesProcessor,
    mapper: RecordMapper,
    records: list[Record],
    connector_name: Connectors,
    connector_id: str,
    scope: str,
    created_by: str,
    creator_email: str | None,
    indexing_filters: FilterCollection,
    logger: Logger,
) -> None:
    if not records:
        return
    indexing_manual = not indexing_filters.is_enabled(
        IndexingFilterKey.FILES, default=True
    )
    updated: list[tuple[FileRecord, list[Permission]]] = []
    unchanged: list[Record] = []
    for record in records:
        try:
            identity = extract_share_and_path(record)
            io_path = io_share_and_path(record)
            if not identity or not io_path:
                unchanged.append(record)
                continue
            share_name, identity_path = identity
            _share, server_path = io_path
            entry = await data_source.stat(share_name, server_path)
            if entry is None:
                unchanged.append(record)
                continue
            rev = revision_id(share_name, entry, identity_path)
            if rev == record.external_revision_id:
                unchanged.append(record)
                continue
            existing = record if isinstance(record, FileRecord) else None
            built = mapper.build_record(
                entry=entry,
                share=share_name,
                nfc_path=identity_path,
                connector_name=connector_name,
                connector_id=connector_id,
                existing=existing,
                indexing_manual=indexing_manual,
                revision=rev,
                ext_id=f"{share_name}/{identity_path}",
                server_path=server_path,
            )
            if existing:
                built.id = existing.id
            perms = app_level_permissions(
                scope=scope,
                org_id=processor.org_id,
                creator_email=creator_email,
                created_by=created_by,
            )
            updated.append((built, perms))
        except Exception:
            logger.exception("Error checking record %s at source", record.id)
    if updated:
        await processor.on_new_records(updated)
    if unchanged:
        await processor.reindex_existing_records(unchanged)


async def share_filter_options(
    *,
    data_source: INetworkShareDataSource | None,
    configured_share: str | None,
    page: int,
    limit: int,
    search: str | None,
) -> FilterOptionsResponse:
    if not data_source:
        return FilterOptionsResponse(
            success=False,
            options=[],
            page=page,
            limit=limit,
            has_more=False,
            message="Connector is not initialized",
        )
    try:
        shares = _drop_admin_shares(await data_source.list_shares())
        names = _disk_shares(shares)
    except ShareListingError as exc:
        if configured_share:
            names = [configured_share]
        else:
            return FilterOptionsResponse(
                success=False,
                options=[],
                page=page,
                limit=limit,
                has_more=False,
                message=str(exc),
            )
    if search:
        needle = search.lower()
        names = [n for n in names if needle in n.lower()]
    start = max(page - 1, 0) * limit
    window = names[start : start + limit]
    return FilterOptionsResponse(
        success=True,
        options=[FilterOption(id=name, label=name) for name in window],
        page=page,
        limit=limit,
        has_more=start + limit < len(names),
    )
