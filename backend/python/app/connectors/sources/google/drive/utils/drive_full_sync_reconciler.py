"""Drop records a completed full sync did not see.

A full resync deletes the connector's permission and parent edges, then
rebuilds them only for items the new listing visits. Anything it did not visit
is left in the graph with no edges, so it disappears from the product while
still occupying a record. This sweep deletes those records, and only when the
listing actually finished: a partial run must not treat "not seen" as "deleted".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.connectors.core.base.connector.connector_service import IncompleteSyncError
from app.connectors.sources.google.common.connector_google_exceptions import (
    DriveSyncIncomplete,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

RECONCILE_PAGE_SIZE = 1000
_DELETE_BATCH = 100
# More than this fraction of existing records missing from the listing means the
# listing is the thing that failed, not the files.
_MAX_DELETE_FRACTION = 0.5


class FullSyncLedger:
    """What one connector run listed, and whether that listing is complete."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.incomplete = False
        self.incremental = False
        self.item_failures = 0
        # Items that failed to read, by id. A failure with no id leaves the run incomplete instead.
        self.failed_ids: set[str] = set()
        self.skipped_drives = 0
        self.full_listings = 0

    def see(self, external_id: str | None) -> None:
        if external_id:
            self.seen.add(str(external_id))

    def fail_item(self, external_id: str | None = None) -> None:
        self.item_failures += 1
        if external_id:
            self.failed_ids.add(str(external_id))
        else:
            self.incomplete = True

    def note_incomplete(self) -> None:
        self.incomplete = True

    def note_incremental(self) -> None:
        self.incremental = True

    def note_full_listing(self) -> None:
        self.full_listings += 1

    def note_skipped_drive(self) -> None:
        self.skipped_drives += 1
        self.incomplete = True

    @property
    def has_gaps(self) -> bool:
        """Whether this run left part of Drive unread, so it must not count as a complete read."""
        return self.incomplete or self.item_failures > 0 or self.skipped_drives > 0

    @property
    def unlink_safe(self) -> bool:
        """Whether a full sync may unlink what this run did not see: every gap is an item it names, which
        ``keep_failed_items_linked`` kept as stored."""
        return not self.incomplete and self.skipped_drives == 0

    @property
    def can_reconcile(self) -> bool:
        return (
            self.full_listings > 0
            and not self.incremental
            and not self.incomplete
            and self.item_failures == 0
            and self.skipped_drives == 0
            and bool(self.seen)
        )


class DriveItemsUnread(DriveSyncIncomplete, IncompleteSyncError):
    """A run whose only gaps are items it failed to read and kept as stored: it fails, and a full sync still unlinks
    what it did not see."""


def incomplete_run_error(ledger: FullSyncLedger) -> DriveSyncIncomplete:
    error = DriveItemsUnread if ledger.unlink_safe else DriveSyncIncomplete
    return error("Part of Google Drive could not be read in this run; it is read again on the next run")


async def keep_failed_items_linked(
    processor: DataSourceEntitiesProcessor, connector_id: str, ledger: FullSyncLedger, logger: Logger
) -> None:
    """In a full sync the runner started, note the stored record of each item this run failed to read as kept, so
    one unreadable item keeps its own links instead of holding back the unlink of everything the run did not see.
    A lookup that fails marks the run incomplete, so nothing is unlinked."""
    if not ledger.failed_ids or processor.full_sync_running(connector_id) is not True:
        return
    kept: list[str] = []
    try:
        for external_id in sorted(ledger.failed_ids):
            record = await processor.get_record_by_external_id(
                connector_id=connector_id, external_record_id=external_id, raise_on_error=True
            )
            if record is not None and record.id:
                kept.append(record.id)
    except Exception:
        logger.warning("The items this run could not read were not looked up; nothing unseen is unlinked", exc_info=True)
        ledger.note_incomplete()
        return
    processor.note_kept_records(connector_id, kept)


def _unknown_unseen(ledger: FullSyncLedger, keep: object) -> None:
    # Which unseen records ``keep`` would keep is not known, so the run is incomplete: nothing is unlinked after it.
    if keep is not None:
        ledger.note_incomplete()


async def reconcile_unseen_records(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    ledger: FullSyncLedger,
    logger: Logger,
    keep: Callable[[list], Awaitable[set[str]]] | None = None,
) -> int:
    """Delete non-placeholder records whose external id this full sync did not see.

    ``keep`` answers which of the unseen records (by id) may exist although no
    listing can show them; those stay. When it fails, or the stored records
    cannot be listed in full to ask it, the run is marked incomplete and
    nothing is deleted.

    Returns the number of records the delete call reported. Returns 0, and
    deletes nothing, when the run was incremental, incomplete, saw nothing, or
    would delete more than half of the connector's records.
    """
    if ledger.incremental or ledger.incomplete or ledger.item_failures or ledger.skipped_drives:
        logger.warning(
            "Skipping stale-record cleanup; the Drive sync did not finish a complete listing"
        )
        return 0
    if ledger.full_listings == 0 or not ledger.seen:
        logger.warning(
            "Skipping stale-record cleanup; this Drive sync did not list any files"
        )
        return 0

    existing: list = []
    after_key: str | None = None
    while True:
        # Cleanup is best effort: a store outage here must not fail the sync
        # steps that run after it.
        try:
            page = await processor.get_records_by_status(
                connector_id,
                None,
                limit=RECONCILE_PAGE_SIZE,
                is_placeholder=False,
                after_key=after_key,
            )
        except Exception:
            logger.warning(
                "Skipping stale-record cleanup; the stored records could not be listed",
                exc_info=True,
            )
            _unknown_unseen(ledger, keep)
            return 0
        if not isinstance(page, list):
            logger.warning("Skipping stale-record cleanup; the record listing was not readable")
            _unknown_unseen(ledger, keep)
            return 0
        if not page:
            break
        existing.extend(page)
        if len(page) < RECONCILE_PAGE_SIZE:
            break
        after_key = getattr(page[-1], "id", None)
        if not after_key:
            break

    stale = [
        record
        for record in existing
        if getattr(record, "external_record_id", None)
        and record.external_record_id not in ledger.seen
    ]
    if not existing or not stale:
        return 0
    if keep is not None:
        try:
            kept = await keep(stale)
        except Exception:
            ledger.note_incomplete()
            logger.warning(
                "Skipping stale-record cleanup; whether the unseen records may still exist could not be read",
                exc_info=True,
            )
            return 0
        if kept:
            logger.info("Keeping %s unseen Drive record(s) that no listing can show", len(kept))
            stale = [record for record in stale if getattr(record, "id", None) not in kept]
        if not stale:
            return 0
    if len(stale) > len(existing) * _MAX_DELETE_FRACTION:
        logger.warning(
            "Skipping stale-record cleanup: %s of %s Drive records were not in the listing",
            len(stale),
            len(existing),
        )
        return 0

    deleted = failed_batches = 0
    ids = [record.id for record in stale if getattr(record, "id", None)]
    for start in range(0, len(ids), _DELETE_BATCH):
        # No cascade: an unseen folder can hold children this run saw and wrote,
        # and its unseen children are already in ``stale``.
        try:
            result = await processor.on_records_deleted_cascade(
                ids[start : start + _DELETE_BATCH], connector_id, cascade_children=False
            )
        except Exception:
            failed_batches += 1
            logger.warning("Could not remove a batch of stale Drive records", exc_info=True)
            continue
        deleted += int((result or {}).get("successfully_deleted") or 0)
    logger.info("Removed %s Drive record(s) that were not in the completed full sync", deleted)
    if failed_batches:
        logger.warning(
            "%s batch(es) of stale Drive records could not be removed; the next full sync retries them",
            failed_batches,
        )
    return deleted


async def delete_shared_drive_records(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    external_group_id: str,
    logger: Logger,
) -> bool:
    """Delete a shared drive's record group and the records filed under it.

    Returns False, leaving the group in place, when any record could not be
    listed or removed, so the caller can retry the drive on a later run.
    """
    group = await processor.get_record_group_by_external_id(connector_id, external_group_id)
    if group is not None and getattr(group, "id", None):
        after_key: str | None = None
        ids: list[str] = []
        while True:
            page = await processor.get_records_by_status(
                connector_id,
                None,
                limit=RECONCILE_PAGE_SIZE,
                record_group_id=group.id,
                after_key=after_key,
            )
            if not isinstance(page, list):
                logger.warning(
                    "Keeping shared drive %s; its record listing was not readable", external_group_id
                )
                return False
            if not page:
                break
            ids.extend(record.id for record in page if getattr(record, "id", None))
            if len(page) < RECONCILE_PAGE_SIZE:
                break
            after_key = getattr(page[-1], "id", None)
            if not after_key:
                logger.warning(
                    "Keeping shared drive %s; its record listing could not be paged", external_group_id
                )
                return False
        failed = False
        for start in range(0, len(ids), _DELETE_BATCH):
            try:
                result = await processor.on_records_deleted_cascade(
                    ids[start : start + _DELETE_BATCH], connector_id
                )
            except Exception:
                logger.warning(
                    "Could not remove a batch of records from shared drive %s",
                    external_group_id,
                    exc_info=True,
                )
                failed = True
                continue
            if (
                not isinstance(result, dict)
                or result.get("success") is False
                or result.get("failed_count")
                or result.get("failed_records")
            ):
                logger.warning(
                    "Could not remove every record in a batch from shared drive %s", external_group_id
                )
                failed = True
        if failed:
            return False
    await processor.on_record_group_deleted(external_group_id, connector_id)
    logger.info("Removed shared drive %s and its records", external_group_id)
    return True
