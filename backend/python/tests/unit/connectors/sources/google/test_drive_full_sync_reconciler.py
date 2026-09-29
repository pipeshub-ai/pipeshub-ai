"""Stale-record cleanup runs only after a complete full listing."""

import logging
from types import SimpleNamespace

from app.connectors.sources.google.drive.utils.drive_full_sync_reconciler import (
    FullSyncLedger,
    reconcile_unseen_records,
)

LOGGER = logging.getLogger("drive-reconcile")


class _Processor:
    def __init__(self, records: list[SimpleNamespace]) -> None:
        self.records = records
        self.deleted: list[list[str]] = []

    async def get_records_by_status(self, *_args: object, **_kwargs: object) -> list:
        return list(self.records)

    async def on_records_deleted_cascade(self, ids: list[str], _connector_id: str) -> dict:
        self.deleted.append(list(ids))
        self.records = [record for record in self.records if record.id not in ids]
        return {"successfully_deleted": len(ids)}


def _record(external_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=f"id-{external_id}", external_record_id=external_id)


def _ready(seen: set[str]) -> FullSyncLedger:
    ledger = FullSyncLedger()
    ledger.note_full_listing()
    ledger.seen = set(seen)
    return ledger


async def test_unseen_records_are_deleted_after_a_complete_full_sync() -> None:
    processor = _Processor([_record("keep"), _record("gone")])

    deleted = await reconcile_unseen_records(processor, "drive-1", _ready({"keep"}), LOGGER)

    assert deleted == 1
    assert processor.deleted == [["id-gone"]]


async def test_cleanup_is_skipped_when_more_than_half_would_be_deleted() -> None:
    processor = _Processor([_record("keep"), _record("gone-1"), _record("gone-2")])

    deleted = await reconcile_unseen_records(processor, "drive-1", _ready({"keep"}), LOGGER)

    assert deleted == 0
    assert processor.deleted == []


async def test_cleanup_is_skipped_when_the_listing_was_incomplete_or_incremental() -> None:
    processor = _Processor([_record("keep"), _record("gone")])
    incomplete = _ready({"keep"})
    incomplete.note_incomplete()
    incremental = _ready({"keep"})
    incremental.note_incremental()

    assert await reconcile_unseen_records(processor, "drive-1", incomplete, LOGGER) == 0
    assert await reconcile_unseen_records(processor, "drive-1", incremental, LOGGER) == 0
    assert processor.deleted == []


async def test_an_unreadable_record_page_deletes_nothing() -> None:
    class Broken(_Processor):
        async def get_records_by_status(self, *_args: object, **_kwargs: object) -> object:
            return object()

    processor = Broken([])
    deleted = await reconcile_unseen_records(processor, "drive-1", _ready({"keep"}), LOGGER)

    assert deleted == 0
