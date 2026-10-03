"""Which folders of a bucket, container or share a sync covers.

Built from the ``folder_paths`` sync filter. Paths are relative to the bucket,
container or share, use ``/`` and name folders, not arbitrary key prefixes:
``reports`` covers ``reports/2026/q1.pdf`` but not ``reports-old/a.pdf``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
)
from app.services.graph_db.common.record_visibility import RecordVisibility

if TYPE_CHECKING:
    import logging
    from collections.abc import AsyncIterator, Callable

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.interfaces.sync_point.isync_point import ISyncPoint
    from app.models.entities import Record

_PAGE_SIZE = 500


def _as_folder(path: str) -> str:
    """``'/a/b'`` -> ``'a/b/'``; ``''`` or ``'/'`` -> ``''`` (the whole store)."""
    cleaned = path.strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def path_in_container(container_name: str | None, external_record_id: str | None) -> str | None:
    """The ``<path>`` of a record id ``<container>/<path>``; None when the id is not in that container."""
    if not container_name or not external_record_id:
        return None
    prefix = f"{container_name}/"
    if not external_record_id.startswith(prefix):
        return None
    return external_record_id[len(prefix):] or None


@dataclass(frozen=True)
class FolderScope:
    """Folder prefixes (each ending in ``/``) and whether they are excluded."""

    folders: tuple[str, ...] = ()
    exclude: bool = False

    @classmethod
    def from_filters(cls, sync_filters: FilterCollection | None) -> FolderScope:
        folder_filter = sync_filters.get(SyncFilterKey.FOLDER_PATHS) if sync_filters else None
        if not folder_filter or folder_filter.is_empty():
            return cls()
        raw = folder_filter.value
        values = raw if isinstance(raw, list) else [raw]
        folders = {_as_folder(str(v)) for v in values if v is not None}
        exclude = folder_filter.operator_value == "not_in"
        if "" in folders:
            # The root folder was named: Include covers everything, Exclude nothing.
            return cls(("",), True) if exclude else cls()
        # A folder inside another listed folder adds nothing.
        kept = tuple(sorted(f for f in folders if not any(f != o and f.startswith(o) for o in folders)))
        return cls(kept, exclude)

    @property
    def is_everything(self) -> bool:
        return not self.folders

    @property
    def list_prefixes(self) -> list[str]:
        """Prefixes to list the store with; ``[""]`` means list all of it.

        Excluded folders are listed and then skipped, since the store APIs can
        only narrow a listing to a prefix, not leave one out.
        """
        if self.is_everything or self.exclude:
            return [""]
        return list(self.folders)

    def includes_file(self, path: str) -> bool:
        """Whether a file (its path within the store) is synced."""
        if self.is_everything:
            return True
        inside = any(path.lstrip("/").startswith(f) for f in self.folders)
        return not inside if self.exclude else inside

    def includes_folder(self, path: str) -> bool:
        """Whether a folder record is kept.

        With Include, the folders above a chosen folder are kept too, so the
        tree still leads to it; with Exclude, an excluded folder and everything
        under it is dropped.
        """
        if self.is_everything:
            return True
        folder = _as_folder(path)
        if self.exclude:
            return not any(folder.startswith(f) for f in self.folders)
        return any(folder.startswith(f) or f.startswith(folder) for f in self.folders)

    def key(self) -> str:
        """A stable string for this scope, to tell whether it has changed."""
        # JSON, not a joined string: folder names may contain any separator.
        return json.dumps({"exclude": self.exclude, "folders": sorted(self.folders)})

    def describe(self) -> str:
        if self.is_everything:
            return "all folders"
        names = ", ".join(f or "/" for f in self.folders)
        return f"all folders except {names}" if self.exclude else f"only {names}"


class CleanupResult(NamedTuple):
    removed: int
    failed: int


async def _records_in(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
) -> AsyncIterator[tuple[Record, str]]:
    """This connector's records in ``container_name`` with their paths, a page at a time.

    Record ids are ``<container>/<path>``. Trashed records are included: a removal
    scan must still delete one the source no longer has, and a listed object whose
    record is in the trash is a known object, not an unrecorded one.
    """
    prefix = f"{container_name}/"
    after_key = None
    while True:
        page = await data_entities_processor.get_records_in_record_group(
            connector_id, container_name, _PAGE_SIZE, after_key, visibility=RecordVisibility.ALL
        )
        for record in page:
            external_id = record.external_record_id or ""
            if external_id.startswith(prefix):
                yield record, external_id[len(prefix):]
        if len(page) < _PAGE_SIZE:
            break
        after_key = page[-1].id


async def recorded_ids(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
) -> set[str]:
    """The ids of this connector's records in ``container_name``, read before a listing."""
    return {
        record.external_record_id
        async for record, _ in _records_in(data_entities_processor, connector_id, container_name)
    }


async def _remove_records(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
    doomed: Callable[[Record, str], bool],
    reason: str,
    logger: logging.Logger,
) -> CleanupResult:
    """Delete this connector's records in ``container_name`` whose path ``doomed`` picks."""
    removed = failed = 0
    async for record, path in _records_in(data_entities_processor, connector_id, container_name):
        if not doomed(record, path):
            continue
        try:
            await data_entities_processor.on_record_deleted(record.id)
            removed += 1
        except Exception as e:  # noqa: BLE001 — one failed delete must not stop the rest
            failed += 1
            logger.warning(f"Failed to remove {record.external_record_id} {reason}: {e}")
    if removed:
        logger.info(f"Removed {removed} records in {container_name} {reason}")
    return CleanupResult(removed, failed)


async def remove_records_outside_scope(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
    scope: FolderScope,
    logger: logging.Logger,
) -> CleanupResult:
    """Delete this connector's records in ``container_name`` that ``scope`` leaves out.

    Narrowing the folders to sync stops new files from being indexed; this also
    takes out what was indexed before. Folder records are told apart by their
    folder MIME type.
    """
    if scope.is_everything:
        return CleanupResult(0, 0)

    from app.config.constants.arangodb import MimeTypes

    def outside(record: Record, path: str) -> bool:
        is_folder = record.mime_type == MimeTypes.FOLDER.value
        return not (scope.includes_folder(path) if is_folder else scope.includes_file(path))

    return await _remove_records(
        data_entities_processor, connector_id, container_name, outside,
        f"outside {scope.describe()}", logger,
    )


def listed_record_ids(container_name: str, path: str) -> set[str]:
    """The record ids a listed object keeps: its own and those of the folders above it.

    Folders are implicit in object keys, so ``a/b/c.txt`` keeps ``a`` and ``a/b``;
    a folder object ``a/b/`` keeps both ``a/b/`` and ``a/b``.
    """
    key = path.lstrip("/")
    parts = [p for p in key.rstrip("/").split("/") if p]
    ids = {f"{container_name}/{'/'.join(parts[:i])}" for i in range(1, len(parts) + 1)}
    if key:
        ids.add(f"{container_name}/{key}")
    return ids


def _under_prefix(path: str, prefix: str) -> bool:
    # A listed prefix "reports/" also covers its own folder record, stored as "reports".
    return path.startswith(prefix) or (bool(prefix) and path == prefix.rstrip("/"))


async def remove_records_not_listed(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
    prefixes: list[str],
    listed: set[str],
    logger: logging.Logger,
) -> CleanupResult:
    """Delete this connector's records under ``prefixes`` that their listings did not keep.

    This is how a deletion at the source, or a file a sync filter now excludes,
    leaves the index. ``listed`` holds the ids from ``listed_record_ids`` for
    every object the listings returned and the filters keep. Call it once, after
    every prefix of the container was listed from its first page to its last
    without an error: anything a partial listing missed would be deleted, and a
    rename into a prefix listed later is still stored under its old path until
    that prefix is processed.
    """
    return await _remove_records(
        data_entities_processor, connector_id, container_name,
        lambda record, path: any(_under_prefix(path, p) for p in prefixes)
        and f"{container_name}/{path}" not in listed,
        "missing from the latest listing", logger,
    )


async def _saved_selection(
    config_service: ConfigurationService, connector_id: str, filter_name: str, logger: logging.Logger,
) -> set[str] | None:
    """The containers the saved ``filter_name`` filter includes; None unless that is certain.

    Read here rather than through ``load_connector_filters``, which answers a
    failed or empty read with no filters, the same as a filter left unset.
    """
    try:
        config = await config_service.get_config(f"/services/connectors/{connector_id}/config")
    except Exception as e:  # an unreadable config removes nothing
        logger.warning(f"Not removing de-selected {filter_name}: the connector config could not be read: {e}")
        return None
    if not isinstance(config, dict) or not config.get("enabled", True):
        return None
    filters = config.get("filters")
    sync = filters.get("sync") if isinstance(filters, dict) else None
    values = sync.get("values") if isinstance(sync, dict) else None
    if not isinstance(values, dict):
        return None
    selection = FilterCollection.from_dict(values, logger).get(filter_name)
    if selection is None or selection.is_empty() or selection.operator_value != FilterOperator.IN:
        return None
    raw = selection.value if isinstance(selection.value, list) else [selection.value]
    return {name for name in raw if isinstance(name, str) and name}


async def remove_deselected_containers(
    data_entities_processor: DataSourceEntitiesProcessor,
    config_service: ConfigurationService,
    connector_id: str,
    filter_name: str,
    synced: list[str],
    logger: logging.Logger,
) -> None:
    """Delete the records, then the record group, of each stored bucket or container
    that the saved ``filter_name`` filter no longer includes.

    ``synced`` is the selection this sync runs with. The saved filter is read
    again and must match it, so a failed or empty read, a filter left unset (all
    of them are synced) or one edited mid-sync removes nothing. What the cloud
    API lists plays no part, so a failed listing is never taken for de-selection.
    """
    selected = await _saved_selection(config_service, connector_id, filter_name, logger)
    if not selected or selected != {name for name in synced if isinstance(name, str) and name}:
        return

    from app.config.constants.arangodb import CollectionNames
    from app.models.entities import RecordGroupType

    try:
        groups = await data_entities_processor.get_nodes_by_filters(
            collection=CollectionNames.RECORD_GROUPS.value,
            filters={"connectorId": connector_id, "groupType": RecordGroupType.BUCKET.value},
        )
    except Exception as e:  # retried on the next sync
        logger.warning(f"Not removing de-selected {filter_name}: the stored ones could not be read: {e}")
        return
    stale = sorted({g.get("externalGroupId") for g in groups if isinstance(g, dict)} - selected - {None, ""})
    for container_name in stale:
        try:
            result = await _remove_records(
                data_entities_processor, connector_id, container_name, lambda record, path: True,
                f"now that the {filter_name} filter leaves {container_name} out", logger,
            )
        except Exception as e:  # retried on the next sync
            logger.warning(f"Could not read the records of de-selected {container_name}: {e}")
            continue
        if result.failed:
            logger.warning(f"{result.failed} records of de-selected {container_name} could not be removed; retrying next sync")
            continue
        # The group goes last: while it stays, the next sync finds the container again.
        await data_entities_processor.on_record_group_deleted(container_name, connector_id)


async def clean_up_scope(
    data_entities_processor: DataSourceEntitiesProcessor,
    sync_point: ISyncPoint,
    connector_id: str,
    container_name: str,
    scope: FolderScope,
    logger: logging.Logger,
) -> None:
    """Remove records outside ``scope`` unless this scope was already cleaned up.

    The scope last cleaned without a failed delete is kept in a sync point, so a
    failed cleanup is retried on the next sync and an unchanged scope is not
    rescanned. Editing the filters deletes the connector's sync points, which
    clears it.
    """
    if scope.is_everything:
        return

    from app.connectors.core.base.sync_point.sync_point import (
        generate_record_sync_point_key,
    )
    from app.models.entities import RecordType

    key = generate_record_sync_point_key(RecordType.FILE.value, "folder_scope", container_name)
    cleaned = await sync_point.read_sync_point(key)
    if cleaned and cleaned.get("scope") == scope.key():
        return
    result = await remove_records_outside_scope(
        data_entities_processor, connector_id, container_name, scope, logger
    )
    if result.failed:
        logger.warning(
            f"{result.failed} records in {container_name} outside {scope.describe()} "
            "could not be removed; retrying next sync"
        )
        return
    await sync_point.update_sync_point(key, {"scope": scope.key()})
