"""Apply a Dropbox delete, which the API reports by path only.

``DeletedMetadata`` carries no id, so the records stored at that path are found through the file path the sync
wrote. Dropbox may report a deleted folder alone or with its children, and also reports a move as a delete at the
old path, or a shared folder a member left as a delete in that member's listing. So a record is removed only once
its own id no longer resolves, and it goes with its subtree.

Paths are relative to the namespace a listing is rooted at, so two namespaces can hold the same path; only the
records of the listing's own record group are considered.

The same rule covers a path whose item was replaced: Dropbox's sync guidance is to store each listed entry at its
path, "replacing what was there" (files.stone, list_folder), so a new id listed at a path where a record with
another id is stored means that record's item is gone, once its id is confirmed not to resolve.

A delete that could not be confirmed is not dropped: its record id is returned, kept by the caller, and checked
again on the next run, since Dropbox lists a change only once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.models.entities import Record
    from app.sources.client.dropbox.dropbox_ import DropboxResponse

# How the Dropbox client reports a lookup of an id that no longer exists.
_NOT_FOUND = "not_found"


def item_still_exists(response: DropboxResponse) -> bool | None:
    """Read a ``files_get_metadata(<id>)`` answer: True, False when Dropbox says not_found, None when unknown."""
    if response.success:
        # The SDK returns DeletedMetadata (no id) only with include_deleted, which is not asked for.
        return getattr(response.data, "id", None) is not None
    return False if _NOT_FOUND in (response.error or "") else None


@dataclass
class DeleteOutcome:
    removed: list[str] = field(default_factory=list)  # record ids removed
    unsure: list[str] = field(default_factory=list)  # record ids kept for want of an answer
    unsure_external_ids: list[str] = field(default_factory=list)
    failed: bool = False  # a removal Dropbox confirmed could not be written

    @property
    def done(self) -> bool:
        return not self.unsure and not self.failed


async def _remove_gone(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    candidates: list[Record],
    still_exists: Callable[[Record], Awaitable[bool | None]],
    logger: logging.Logger,
    what: str,
) -> DeleteOutcome:
    outcome = DeleteOutcome()
    gone: list[str] = []
    for record in candidates:
        exists = await still_exists(record)
        if exists is None:
            logger.warning(f"Could not confirm Dropbox deleted {what} ({record.external_record_id}); keeping it for now")
            outcome.unsure.append(record.id)
            outcome.unsure_external_ids.append(record.external_record_id)
        elif exists:
            logger.debug(f"{what} still exists as {record.external_record_id} (moved or still shared); keeping it")
        else:
            gone.append(record.id)
    if gone:
        result = await processor.on_records_deleted_cascade(gone, connector_id, include_trashed_roots=True)
        if not (result or {}).get("success", False):
            logger.warning(f"Failed to remove the records deleted at {what}: {(result or {}).get('reason')}")
            outcome.failed = True
            # Dropbox lists the delete once: kept as owed, it is written on the next run.
            gone_ids = set(gone)
            outcome.unsure_external_ids.extend(r.external_record_id for r in candidates if r.id in gone_ids)
        else:
            outcome.removed.extend(gone)
    return outcome


def _in_group(record: Record, record_group_id: str | None) -> bool:
    return record_group_id is None or record.external_record_group_id == record_group_id


async def apply_deleted_entry(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    path_lower: str,
    still_exists: Callable[[Record], Awaitable[bool | None]],
    logger: logging.Logger,
    *,
    record_group_id: str | None = None,
) -> DeleteOutcome:
    """Remove the records at ``path_lower`` (in the listing's record group) whose item is gone, with their subtrees."""
    candidates = [r for r in await processor.get_records_by_file_path(connector_id, path_lower)
                  if _in_group(r, record_group_id)]
    return await _remove_gone(processor, connector_id, candidates, still_exists, logger, path_lower)


async def apply_replaced_entry(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    path_lower: str,
    listed_id: str,
    record_group_id: str,
    still_exists: Callable[[Record], Awaitable[bool | None]],
    logger: logging.Logger,
) -> DeleteOutcome:
    """Remove the records stored at a path where Dropbox now lists another item, once their own ids are gone."""
    candidates = [r for r in await processor.get_records_by_file_path(connector_id, path_lower)
                  if _in_group(r, record_group_id) and r.external_record_id != listed_id]
    if not candidates:
        return DeleteOutcome()
    return await _remove_gone(processor, connector_id, candidates, still_exists, logger, path_lower)


async def _listed_now(record: Record) -> bool:
    return False


async def apply_out_of_scope_entry(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    external_id: str,
    logger: logging.Logger,
) -> DeleteOutcome:
    """Remove the stored record of a file Dropbox lists now but the sync filters leave out (renamed to an excluded
    extension, edited out of a date window). The listing is the definite answer: it carries the file's current name
    and dates, so Dropbox is not asked again. A full sync drops such a record too, but none runs until the filters
    change, so without this it would stay searchable."""
    record = await processor.get_record_by_external_id(connector_id, external_id)
    if record is None:
        return DeleteOutcome()
    return await _remove_gone(processor, connector_id, [record], _listed_now, logger,
                              f"{external_id} (outside the sync filters)")


async def recheck_owed_deletes(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    external_ids: list[str],
    still_exists: Callable[[Record], Awaitable[bool | None]],
    logger: logging.Logger,
) -> list[str]:
    """Check again the deletes an earlier run could not confirm. Returns the external ids that are still owed."""
    candidates = []
    for external_id in external_ids:
        record = await processor.get_record_by_external_id(connector_id, external_id)
        if record is not None:
            candidates.append(record)
    if not candidates:
        return []
    outcome = await _remove_gone(processor, connector_id, candidates, still_exists, logger, "an earlier delete")
    owed = set(outcome.unsure)
    if outcome.failed:
        owed |= {r.id for r in candidates if r.id not in outcome.removed}
    return [r.external_record_id for r in candidates if r.id in owed]
