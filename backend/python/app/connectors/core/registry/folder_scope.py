"""Which folders of a bucket, container or share a sync covers.

Built from the ``folder_paths`` sync filter. Paths are relative to the bucket,
container or share, use ``/`` and name folders, not arbitrary key prefixes:
``reports`` covers ``reports/2026/q1.pdf`` but not ``reports-old/a.pdf``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from app.connectors.core.registry.filters import FilterCollection, SyncFilterKey

if TYPE_CHECKING:
    import logging

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.interfaces.sync_point.isync_point import ISyncPoint

_PAGE_SIZE = 500


def _as_folder(path: str) -> str:
    """``'/a/b'`` -> ``'a/b/'``; ``''`` or ``'/'`` -> ``''`` (the whole store)."""
    cleaned = path.strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


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


async def remove_records_outside_scope(
    data_entities_processor: DataSourceEntitiesProcessor,
    connector_id: str,
    container_name: str,
    scope: FolderScope,
    logger: logging.Logger,
) -> CleanupResult:
    """Delete this connector's records in ``container_name`` that ``scope`` leaves out.

    Narrowing the folders to sync stops new files from being indexed; this also
    takes out what was indexed before. Record ids are ``<container>/<path>``;
    folder records are told apart by their folder MIME type.
    """
    if scope.is_everything:
        return CleanupResult(0, 0)

    from app.config.constants.arangodb import MimeTypes

    prefix = f"{container_name}/"
    removed = failed = 0
    after_key = None
    while True:
        page = await data_entities_processor.get_records_in_record_group(
            connector_id, container_name, _PAGE_SIZE, after_key
        )
        for record in page:
            external_id = record.external_record_id or ""
            if not external_id.startswith(prefix):
                continue
            path = external_id[len(prefix):]
            is_folder = record.mime_type == MimeTypes.FOLDER.value
            if (scope.includes_folder(path) if is_folder else scope.includes_file(path)):
                continue
            try:
                await data_entities_processor.on_record_deleted(record.id)
                removed += 1
            except Exception as e:  # noqa: BLE001 — one failed delete must not stop the rest
                failed += 1
                logger.warning(f"Failed to remove {external_id} outside the synced folders: {e}")
        if len(page) < _PAGE_SIZE:
            break
        after_key = page[-1].id
    if removed:
        logger.info(f"Removed {removed} records in {container_name} outside {scope.describe()}")
    return CleanupResult(removed, failed)


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
