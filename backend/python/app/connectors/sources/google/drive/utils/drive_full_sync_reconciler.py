"""Drop records a completed full sync did not see.

A full resync deletes the connector's permission and parent edges, then
rebuilds them only for items the new listing visits. Anything it did not visit
is left in the graph with no edges, so it disappears from the product while
still occupying a record. This sweep deletes those records, and only when the
listing actually finished: a partial run must not treat "not seen" as "deleted".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
        self.skipped_drives = 0
        self.full_listings = 0

    def see(self, external_id: str | None) -> None:
        if external_id:
            self.seen.add(str(external_id))

    def fail_item(self) -> None:
        self.item_failures += 1
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
    def can_reconcile(self) -> bool:
        return (
            self.full_listings > 0
            and not self.incremental
            and not self.incomplete
            and self.item_failures == 0
            and self.skipped_drives == 0
            and bool(self.seen)
        )


async def reconcile_unseen_records(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    ledger: FullSyncLedger,
    logger: Logger,
) -> int:
    """Delete non-placeholder records whose external id this full sync did not see.

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
        page = await processor.get_records_by_status(
            connector_id,
            None,
            limit=RECONCILE_PAGE_SIZE,
            is_placeholder=False,
            after_key=after_key,
        )
        if not isinstance(page, list):
            logger.warning("Skipping stale-record cleanup; the record listing was not readable")
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
    if len(stale) > len(existing) * _MAX_DELETE_FRACTION:
        logger.warning(
            "Skipping stale-record cleanup: %s of %s Drive records were not in the listing",
            len(stale),
            len(existing),
        )
        return 0

    deleted = 0
    ids = [record.id for record in stale if getattr(record, "id", None)]
    for start in range(0, len(ids), _DELETE_BATCH):
        result = await processor.on_records_deleted_cascade(
            ids[start : start + _DELETE_BATCH], connector_id
        )
        deleted += int((result or {}).get("successfully_deleted") or 0)
    logger.info("Removed %s Drive record(s) that were not in the completed full sync", deleted)
    return deleted


async def delete_shared_drive_records(
    processor: DataSourceEntitiesProcessor,
    connector_id: str,
    external_group_id: str,
    logger: Logger,
) -> None:
    """Delete a shared drive's record group and the records filed under it."""
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
            if not isinstance(page, list) or not page:
                break
            ids.extend(record.id for record in page if getattr(record, "id", None))
            if len(page) < RECONCILE_PAGE_SIZE:
                break
            after_key = getattr(page[-1], "id", None)
            if not after_key:
                break
        for start in range(0, len(ids), _DELETE_BATCH):
            await processor.on_records_deleted_cascade(
                ids[start : start + _DELETE_BATCH], connector_id
            )
    await processor.on_record_group_deleted(external_group_id, connector_id)
    logger.info("Removed shared drive %s and its records", external_group_id)
