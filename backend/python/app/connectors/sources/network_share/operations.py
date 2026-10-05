"""Shared sync/stream/filter-option operations for SMB and CIFS connectors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    not_downloadable,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.filters import FilterCollection, IndexingFilterKey
from app.connectors.core.registry.folder_scope import FolderScope, clean_up_scope
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
    from app.connectors.sources.network_share.protocol import INetworkShareDataSource
    from app.models.permission import Permission


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
    # Both stores hand back a plain Record here, never a FileRecord, so an
    # isinstance(FileRecord) check drops every hit and turns a rename into a
    # new record plus a prune of the old one.
    def _file_only(record: Record | None) -> Record | None:
        return record if record is not None and record.record_type == RecordType.FILE else None

    async def get_by_external_id(ext_id: str) -> Record | None:
        return _file_only(await processor.get_record_by_external_id(connector_id, ext_id))

    async def get_by_revision(rev: str) -> Record | None:
        return _file_only(
            await processor.get_record_by_external_revision_id(connector_id, rev)
        )

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
) -> list[str]:
    """Walk and reconcile ``shares``. Returns the shares where nothing could be listed."""
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
    unreadable: list[str] = []
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
            if not result.complete and result.seen == {share_name}:
                unreadable.append(share_name)
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
            unreadable.append(share_name)
            logger.exception("Error syncing share %s", share_name)
    if prune and complete:
        await prune_unseen(processor, connector_id, seen, logger)
    elif prune and not complete:
        logger.warning(
            "Some listings failed; not removing records that were not seen this sync"
        )
    return unreadable


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
        # read_file opens the file on its first chunk, after this function has
        # returned, where a missing file becomes a generic 500.
        if await data_source.stat(share_name, file_path) is None:
            raise not_found_at_source(display_name)
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
