"""Stale-record cleanup runs only after a complete full listing."""

import logging
from types import SimpleNamespace

from app.connectors.sources.google.drive.utils.drive_full_sync_reconciler import (
    FullSyncLedger,
    delete_shared_drive_records,
    reconcile_unseen_records,
)

LOGGER = logging.getLogger("drive-reconcile")


class _Processor:
    def __init__(self, records: list[SimpleNamespace]) -> None:
        self.records = records
        self.deleted: list[list[str]] = []
        self.cascades: list[bool] = []

    async def get_records_by_status(self, *_args: object, **_kwargs: object) -> list:
        return list(self.records)

    async def on_records_deleted_cascade(
        self, ids: list[str], _connector_id: str, cascade_children: bool = True
    ) -> dict:
        self.cascades.append(cascade_children)
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
    assert processor.cascades == [False]


async def test_a_failed_delete_batch_does_not_stop_the_others() -> None:
    class FailsFirstBatch(_Processor):
        calls = 0

        async def on_records_deleted_cascade(
            self, ids: list[str], connector_id: str, cascade_children: bool = True
        ) -> dict:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("graph unavailable")
            return await super().on_records_deleted_cascade(ids, connector_id, cascade_children)

    kept = [_record(f"keep-{n}") for n in range(150)]
    gone = [_record(f"gone-{n}") for n in range(150)]
    processor = FailsFirstBatch(kept + gone)

    deleted = await reconcile_unseen_records(
        processor, "drive-1", _ready({record.external_record_id for record in kept}), LOGGER
    )

    assert deleted == 50
    assert processor.deleted == [[f"id-gone-{n}" for n in range(100, 150)]]


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


async def test_a_record_listing_that_raises_deletes_nothing_and_does_not_raise() -> None:
    class Unavailable(_Processor):
        async def get_records_by_status(self, *_args: object, **_kwargs: object) -> list:
            raise RuntimeError("graph unavailable")

    processor = Unavailable([_record("gone")])
    deleted = await reconcile_unseen_records(processor, "drive-1", _ready({"keep"}), LOGGER)

    assert deleted == 0
    assert processor.deleted == []


class _DriveProcessor(_Processor):
    def __init__(self, records: list[SimpleNamespace]) -> None:
        super().__init__(records)
        self.groups_deleted: list[str] = []

    async def get_record_group_by_external_id(self, *_args: object) -> SimpleNamespace:
        return SimpleNamespace(id="group-1")

    async def on_record_group_deleted(self, external_group_id: str, _connector_id: str) -> None:
        self.groups_deleted.append(external_group_id)


async def test_a_shared_drive_is_removed_once_every_record_is() -> None:
    processor = _DriveProcessor([_record("a"), _record("b")])

    removed = await delete_shared_drive_records(processor, "drive-1", "sd-1", LOGGER)

    assert removed is True
    assert processor.deleted == [["id-a", "id-b"]]
    assert processor.groups_deleted == ["sd-1"]


async def test_a_shared_drive_keeps_its_group_when_records_are_left_behind() -> None:
    class PartlyFails(_DriveProcessor):
        async def on_records_deleted_cascade(
            self, ids: list[str], connector_id: str, cascade_children: bool = True
        ) -> dict:
            return {"success": True, "successfully_deleted": 0, "failed_count": 1, "failed_records": ids}

    class Refused(_DriveProcessor):
        async def on_records_deleted_cascade(
            self, ids: list[str], connector_id: str, cascade_children: bool = True
        ) -> dict:
            return {"success": False, "code": 409}

    class Raises(_DriveProcessor):
        async def on_records_deleted_cascade(
            self, ids: list[str], connector_id: str, cascade_children: bool = True
        ) -> dict:
            raise RuntimeError("graph unavailable")

    for processor_type in (PartlyFails, Refused, Raises):
        processor = processor_type([_record("a")])

        removed = await delete_shared_drive_records(processor, "drive-1", "sd-1", LOGGER)

        assert removed is False, processor_type.__name__
        assert processor.groups_deleted == [], processor_type.__name__


async def test_a_shared_drive_with_an_unreadable_record_page_keeps_its_group() -> None:
    class Broken(_DriveProcessor):
        async def get_records_by_status(self, *_args: object, **_kwargs: object) -> object:
            return object()

    processor = Broken([_record("a")])

    removed = await delete_shared_drive_records(processor, "drive-1", "sd-1", LOGGER)

    assert removed is False
    assert processor.deleted == []
    assert processor.groups_deleted == []
