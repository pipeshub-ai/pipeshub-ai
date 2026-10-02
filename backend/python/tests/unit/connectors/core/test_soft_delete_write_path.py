"""The record delete paths with ENABLE_SOFT_DELETE off and on.

Off, every path is today's hard delete, unchanged. On, the same paths mark the
records (one batch id per action) and publish ``softDeleteRecords`` for their
vectors, never ``deleteRecord``, which is what removes blob and Mongo content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    Connectors,
    DeleteSource,
    EventTypes,
    OriginTypes,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.services.vector_cleanup_events import (
    MAX_VIRTUAL_RECORD_IDS_PER_EVENT,
)
from app.models.entities import FileRecord, Record, RecordType

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

MODULE = "app.connectors.core.base.data_processor.data_source_entities_processor"


def _processor() -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    proc.messaging_producer.send_messages = AsyncMock(side_effect=lambda topic, messages: [True] * len(messages))
    return proc


def _with_store(proc: DataSourceEntitiesProcessor, store: MagicMock) -> MagicMock:
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    proc.data_store_provider.transaction.return_value = ctx
    return store


def _soft_result(marked: list[tuple[str, str | None]], batch_id: str = "b-1") -> dict:
    return {
        "success": True,
        "soft_deleted_records": [{"record_id": rid, "name": rid, "virtual_record_id": v} for rid, v in marked],
        "failed_records": [],
        "total_requested": 1,
        "successfully_deleted": 1,
        "failed_count": 0,
        "virtual_record_ids": [v for _, v in marked if v],
        "org_id": "org-1",
        "batch_id": batch_id,
    }


def _event_types(proc: DataSourceEntitiesProcessor) -> list[str]:
    return [c.args[1]["eventType"] for c in proc.messaging_producer.send_message.await_args_list]


def flag(on: bool) -> AbstractContextManager[AsyncMock]:
    return patch(f"{MODULE}.is_soft_delete_enabled", AsyncMock(return_value=on))


def _stored(record_id: str = "r1", **overrides) -> Record:
    fields = {
        "id": record_id, "org_id": "org-1", "record_name": "a.pdf", "record_type": RecordType.FILE,
        "external_record_id": "ext-1", "version": 1, "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.GOOGLE_DRIVE, "connector_id": "c1",
    }
    fields.update(overrides)
    return Record(**fields)


# ---------------------------------------------------------------------------
# Flag off: today's hard delete
# ---------------------------------------------------------------------------


class TestFlagOff:
    async def test_a_connector_delete_removes_the_record(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        # The stored document, as GraphTransactionStore.get_record_by_key returns it.
        store.get_record_by_key = AsyncMock(return_value={"_key": "r1", "connectorId": "c1", "virtualRecordId": "v1"})
        with flag(False):
            await proc.on_record_deleted("r1")
        store.delete_parent_child_edge_to_record.assert_awaited_once_with("r1")
        store.delete_record_by_key.assert_awaited_once_with("r1")
        store.soft_delete_records.assert_not_called()
        assert EventTypes.SOFT_DELETE_RECORDS.value not in _event_types(proc)

    async def test_a_cascade_removes_the_subtree(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.delete_records_recursive = AsyncMock(return_value={"success": True, "successfully_deleted": 1})
        with flag(False):
            await proc.on_records_deleted_cascade(["f1"], "kb1", delete_source=DeleteSource.USER)
        store.delete_records_recursive.assert_awaited_once()
        store.soft_delete_records.assert_not_called()


# ---------------------------------------------------------------------------
# Flag on: marked, vectors only
# ---------------------------------------------------------------------------


class TestFlagOn:
    async def test_a_failed_read_raises_rather_than_dropping_the_delete(self) -> None:
        """Like the real stores, the read answers None on a failure unless asked to raise."""
        proc = _processor()
        store = _with_store(proc, AsyncMock())

        async def read(key: str, *, raise_on_error: bool = False) -> None:
            if raise_on_error:
                raise RuntimeError("graph busy")

        store.get_record_by_key = AsyncMock(side_effect=read)
        with flag(True), pytest.raises(RuntimeError, match="graph busy"):
            await proc.on_record_deleted("r1")
        store.soft_delete_records.assert_not_called()

    async def test_a_record_that_is_already_gone_is_left_alone(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.get_record_by_key = AsyncMock(return_value=None)
        with flag(True):
            await proc.on_record_deleted("r1")
        store.soft_delete_records.assert_not_called()

    async def test_a_connector_delete_marks_only_the_record(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.get_record_by_key = AsyncMock(return_value={"_key": "r1", "connectorId": "c1"})
        store.soft_delete_records = AsyncMock(return_value=_soft_result([("r1", "v1")]))
        with flag(True):
            await proc.on_record_deleted("r1")

        kwargs = store.soft_delete_records.await_args.kwargs
        assert store.soft_delete_records.await_args.args == (["r1"], "c1")
        assert kwargs["delete_source"] == DeleteSource.CONNECTOR.value
        assert kwargs["follow"] == ()
        assert kwargs["batch_id"]
        store.delete_record_by_key.assert_not_called()
        store.delete_parent_child_edge_to_record.assert_not_called()
        assert _event_types(proc) == [EventTypes.SOFT_DELETE_RECORDS.value]
        payload = proc.messaging_producer.send_message.await_args.args[1]["payload"]
        assert payload["virtualRecordIds"] == ["v1"]
        assert payload["batchId"] == kwargs["batch_id"]

    @pytest.mark.parametrize(("cascade", "follow"), [
        (True, ("PARENT_CHILD", "ATTACHMENT")),
        (False, ("ATTACHMENT",)),
    ])
    async def test_a_user_folder_delete_is_one_batch(self, cascade, follow) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.soft_delete_records = AsyncMock(return_value=_soft_result([("f1", None), ("a", "va"), ("b", "vb")]))
        with flag(True):
            result = await proc.on_records_deleted_cascade(
                ["f1"], "kb1", cascade, delete_source=DeleteSource.USER, deleted_by_user_id="uk1"
            )

        store.soft_delete_records.assert_awaited_once()
        kwargs = store.soft_delete_records.await_args.kwargs
        assert (kwargs["delete_source"], kwargs["deleted_by_user_id"], kwargs["follow"]) == ("USER", "uk1", follow)
        store.delete_records_recursive.assert_not_called()
        assert result["softDeleted"] is True
        assert [r["record_id"] for r in result["deleted_records"]] == ["f1", "a", "b"]
        (event,) = [c.args[1] for c in proc.messaging_producer.send_message.await_args_list]
        assert event["eventType"] == EventTypes.SOFT_DELETE_RECORDS.value
        assert event["payload"]["virtualRecordIds"] == ["va", "vb"]
        assert event["payload"]["deleteSource"] == "USER"

    async def test_each_action_gets_its_own_batch(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.soft_delete_records = AsyncMock(return_value=_soft_result([("r", "v")]))
        with flag(True):
            await proc.on_records_deleted_cascade(["a"], "c1")
            await proc.on_records_deleted_cascade(["b"], "c1")
        batches = {c.kwargs["batch_id"] for c in store.soft_delete_records.await_args_list}
        assert len(batches) == 2

    async def test_many_vectors_are_sent_in_chunks(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        count = MAX_VIRTUAL_RECORD_IDS_PER_EVENT + 1
        store.soft_delete_records = AsyncMock(return_value=_soft_result([(f"r{i}", f"v{i}") for i in range(count)]))
        with flag(True):
            await proc.on_records_deleted_cascade(["root"], "c1")
        sizes = [len(c.args[1]["payload"]["virtualRecordIds"]) for c in proc.messaging_producer.send_message.await_args_list]
        assert sizes == [MAX_VIRTUAL_RECORD_IDS_PER_EVENT, 1]

    async def test_an_unpublished_cleanup_is_reported_not_raised(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.soft_delete_records = AsyncMock(return_value=_soft_result([("r1", "v1")]))
        proc.messaging_producer.send_message = AsyncMock(side_effect=RuntimeError("broker down"))
        with flag(True), patch(f"{MODULE}.retry_async", AsyncMock(side_effect=RuntimeError("broker down"))):
            result = await proc.on_records_deleted_cascade(["r1"], "c1")
        assert result["vectorCleanupPending"] is True
        assert result["vectorCleanupFailedVirtualRecordIds"] == ["v1"]

    async def test_an_unpublished_cleanup_names_the_records_like_the_hard_path(self) -> None:
        """KB folder delete reads vectorCleanupFailedRecordIds; the soft path must set it too."""
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.soft_delete_records = AsyncMock(return_value=_soft_result([("r1", "v1"), ("r2", "v2"), ("r3", None)]))
        with flag(True), patch.object(proc, "_publish_soft_delete_events", AsyncMock(return_value=["v2"])):
            result = await proc.on_records_deleted_cascade(["r1"], "c1")
        assert result["vectorCleanupFailedRecordIds"] == ["r2"]

    async def test_a_failed_mark_raises(self) -> None:
        """Nothing was marked, so nothing may be reported as deleted."""
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.soft_delete_records = AsyncMock(side_effect=RuntimeError("graph down"))
        with flag(True), pytest.raises(RuntimeError, match="graph down"):
            await proc.on_records_deleted_cascade(["r1"], "c1")
        proc.messaging_producer.send_message.assert_not_called()

    async def test_the_counter_counts_marked_records_by_source(self) -> None:
        proc = _processor()
        _with_store(proc, AsyncMock()).soft_delete_records = AsyncMock(
            return_value=_soft_result([("a", "va"), ("b", None)])
        )
        with flag(True), patch(f"{MODULE}.record_soft_deleted") as counted:
            await proc.on_records_deleted_cascade(["a"], "kb1", delete_source=DeleteSource.USER)
        counted.assert_called_once_with("USER", 2)


# ---------------------------------------------------------------------------
# Sync leaves trashed records alone
# ---------------------------------------------------------------------------


class TestSyncSkipsTheTrash:
    @pytest.mark.parametrize("source", [DeleteSource.USER, DeleteSource.CONNECTOR])
    async def test_an_upsert_of_a_trashed_record_is_skipped(self, source) -> None:
        """A user's delete holds until the purge, though the source still has the item."""
        proc = _processor()
        store = AsyncMock()
        # A plain Record, as get_record_by_external_id returns on both providers.
        store.get_record_by_external_id = AsyncMock(
            return_value=_stored("stored-1", is_deleted=True, delete_source=source, deleted_at=1)
        )
        incoming = FileRecord(
            org_id="org-1", record_name="a.pdf", record_type=RecordType.FILE, external_record_id="ext-1",
            version=2, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE,
            connector_id="c1", is_file=True,
        )
        assert await proc._process_record(incoming, [], store) == (None, [])
        store.batch_upsert_records.assert_not_called()
        store.batch_create_edges.assert_not_called()

    async def test_a_content_update_of_a_trashed_record_publishes_nothing(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.get_record_by_external_id = AsyncMock(
            return_value=_stored("stored-1", is_deleted=True, delete_source=DeleteSource.USER)
        )
        incoming = FileRecord(
            org_id="org-1", record_name="a.pdf", record_type=RecordType.FILE, external_record_id="ext-1",
            version=2, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE,
            connector_id="c1", is_file=True,
        )
        await proc.on_record_content_update(incoming)
        proc.messaging_producer.send_message.assert_not_called()

    async def test_a_live_record_is_still_updated(self) -> None:
        proc = _processor()
        store = AsyncMock()
        store.get_record_by_external_id = AsyncMock(return_value=_stored("stored-1", external_revision_id="old"))
        store.get_record_group_by_external_id = AsyncMock(return_value=None)
        incoming = FileRecord(
            org_id="org-1", record_name="a.pdf", record_type=RecordType.FILE, external_record_id="ext-1",
            external_revision_id="new", version=2, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GOOGLE_DRIVE, connector_id="c1", is_file=True,
        )
        assert (await proc._process_record(incoming, [], store))[0] is not None
