"""A network share takes back the parked records of files on disk, and never reads one moved to slower storage."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import Filter, FilterCollection, FilterType
from app.connectors.sources.network_share.entry import DirectoryEntry
from app.connectors.sources.network_share.operations import requeue_parked_records
from app.connectors.sources.network_share.record_mapper import (
    FILE_ATTRIBUTE_OFFLINE,
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
)
from app.models.entities import FileRecord, RecordType

OFF = ProgressStatus.AUTO_INDEX_OFF.value


def _record(name: str) -> FileRecord:
    return FileRecord(
        id=f"id-{name}", org_id="org1", record_name=name, record_type=RecordType.FILE,
        external_record_id=f"docs/{name}", external_record_group_id="docs", version=0, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.SMB, connector_id="c1", mime_type="application/octet-stream", is_file=True,
        extension=name.rsplit(".", 1)[-1], path=name, indexing_status=OFF,
    )


def _entry(name: str, attributes: int = 0) -> DirectoryEntry:
    return DirectoryEntry(name=name, is_directory=False, is_symlink=False, size=10, created_time=None,
                          last_write_time=None, file_id=1, attributes=attributes)


class _Store:
    """Compare-and-set on an in-memory status table."""

    def __init__(self, records: list[FileRecord]) -> None:
        self.status = {r.id: r.indexing_status for r in records}

    async def compare_and_set_indexing_status(self, ids: list[str], expected: str, new: str) -> list[str]:
        won = [i for i in ids if self.status.get(i) == expected]
        for i in won:
            self.status[i] = new
        return won


def _processor(store: _Store, *, published: bool = True) -> MagicMock:
    processor = MagicMock()
    processor.data_store_provider = store

    async def reindex(records: list[FileRecord]) -> list[str]:
        if not published:
            return []
        for r in records:
            store.status[r.id] = ProgressStatus.QUEUED.value
        return [r.id for r in records]

    processor.reindex_existing_records = AsyncMock(side_effect=reindex)
    return processor


def _share(entries: dict[str, DirectoryEntry | None]) -> MagicMock:
    source = MagicMock()
    source.stat = AsyncMock(side_effect=lambda share, path: entries.get(path))
    return source


async def _requeue(records: list[FileRecord], entries: dict, *, published: bool = True,
                   filters: FilterCollection | None = None) -> tuple[list[str], _Store, MagicMock, MagicMock]:
    store = _Store(records)
    processor = _processor(store, published=published)
    source = _share(entries)
    sent = await requeue_parked_records(
        data_source=source, processor=processor, records=records,
        indexing_filters=filters or FilterCollection(), logger=logging.getLogger("share-requeue"),
    )
    return sent, store, processor, source


async def test_a_file_on_disk_is_taken_back_and_one_moved_away_is_never_read() -> None:
    online, offline, recall, gone = _record("a.docm"), _record("b.docm"), _record("c.docm"), _record("d.docm")
    entries = {"a.docm": _entry("a.docm"), "b.docm": _entry("b.docm", FILE_ATTRIBUTE_OFFLINE),
               "c.docm": _entry("c.docm", FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS), "d.docm": None}

    sent, store, processor, source = await _requeue([online, offline, recall, gone], entries)

    assert sent == [online.id]
    assert store.status == {online.id: ProgressStatus.QUEUED.value, offline.id: OFF, recall.id: OFF, gone.id: OFF}
    published = processor.reindex_existing_records.await_args.args[0]
    assert [r.id for r in published] == [online.id]
    assert published[0].indexing_status == ProgressStatus.NOT_STARTED.value
    # Only the attributes were read: nothing streams a file's content.
    assert {c.args for c in source.stat.await_args_list} == {("docs", n) for n in ("a.docm", "b.docm", "c.docm", "d.docm")}


async def test_an_event_lost_after_the_move_leaves_the_file_to_the_stranded_record_sweep() -> None:
    record = _record("a.docm")

    sent, store, _, _ = await _requeue([record], {"a.docm": _entry("a.docm")}, published=False)

    # NOT_STARTED is a status the sweep re-sends; the file was on disk when it moved there.
    assert sent == [] and store.status[record.id] == ProgressStatus.NOT_STARTED.value


async def test_a_record_that_left_auto_index_off_meanwhile_is_not_sent() -> None:
    record = _record("a.docm")
    store = _Store([record])
    store.status[record.id] = ProgressStatus.IN_PROGRESS.value
    processor = _processor(store)

    sent = await requeue_parked_records(
        data_source=_share({"a.docm": _entry("a.docm")}), processor=processor, records=[record],
        indexing_filters=FilterCollection(), logger=logging.getLogger("share-requeue"),
    )

    assert sent == [] and store.status[record.id] == ProgressStatus.IN_PROGRESS.value
    processor.reindex_existing_records.assert_not_awaited()


async def test_files_not_indexed_automatically_are_left_for_manual_indexing() -> None:
    record = _record("a.docm")
    manual = FilterCollection(filters=[Filter(key="files", type=FilterType.BOOLEAN, operator="is", value=False)])

    sent, store, processor, source = await _requeue([record], {"a.docm": _entry("a.docm")}, filters=manual)

    assert sent == [] and store.status[record.id] == OFF
    source.stat.assert_not_awaited()
    processor.reindex_existing_records.assert_not_awaited()


async def test_a_share_that_cannot_be_read_leaves_the_file_parked() -> None:
    record = _record("a.docm")
    store = _Store([record])
    source = MagicMock()
    source.stat = AsyncMock(side_effect=ConnectionError("share unreachable"))

    sent = await requeue_parked_records(
        data_source=source, processor=_processor(store), records=[record],
        indexing_filters=FilterCollection(), logger=logging.getLogger("share-requeue"),
    )

    assert sent == [] and store.status[record.id] == OFF
