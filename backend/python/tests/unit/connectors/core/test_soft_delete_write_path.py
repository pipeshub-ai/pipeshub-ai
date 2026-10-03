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
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)

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
# Delete by external id (Outlook's sync delete)
# ---------------------------------------------------------------------------


def _request_result(marked: list[tuple[str, str | None]], *, success: bool = True) -> dict:
    """``delete_record``'s soft result, as both providers shape it."""
    if not success:
        return {"success": False, "code": 403, "reason": "Only mailbox owner can delete emails"}
    return {
        "success": True, "record_id": marked[0][0], "connectorId": "c1", "orgId": "org-1", "softDeleted": True,
        "batchId": "b-ext", "softDeletedRecords": [{"record_id": r, "virtual_record_id": v} for r, v in marked],
        "virtualRecordIds": [v for _, v in marked if v], "eventData": None,
    }


class TestDeleteByExternalId:
    async def test_flag_off_hard_deletes_as_before(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        with flag(False):
            await proc.delete_record_by_external_id("c1", "msg-1", "u1")
        store.delete_record_by_external_id.assert_awaited_once_with("c1", "msg-1", "u1")
        proc.messaging_producer.send_message.assert_not_called()

    async def test_flag_on_trashes_what_the_hard_delete_removes_as_a_connector_delete(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.delete_record_by_external_id = AsyncMock(
            return_value=_request_result([("m1", "vm"), ("a1", "va")])
        )
        with flag(True), patch(f"{MODULE}.record_soft_deleted") as counted:
            await proc.delete_record_by_external_id("c1", "msg-1", "u1")

        store.delete_record_by_external_id.assert_awaited_once_with("c1", "msg-1", "u1", soft_delete=True)
        counted.assert_called_once_with("CONNECTOR", 2)
        assert _event_types(proc) == [EventTypes.SOFT_DELETE_RECORDS.value]
        payload = proc.messaging_producer.send_message.await_args.args[1]["payload"]
        assert payload["virtualRecordIds"] == ["vm", "va"]
        assert (payload["batchId"], payload["deleteSource"]) == ("b-ext", "CONNECTOR")

    async def test_flag_on_a_message_not_stored_or_already_trashed_is_left_alone(self) -> None:
        proc = _processor()
        _with_store(proc, AsyncMock()).delete_record_by_external_id = AsyncMock(return_value=None)
        with flag(True):
            await proc.delete_record_by_external_id("c1", "msg-1", "u1")
        proc.messaging_producer.send_message.assert_not_called()

    async def test_flag_on_a_refused_delete_raises_on_either_backend(self) -> None:
        """Arango raises inside the store; Neo4j reports the refusal. The sync sees a failure either way."""
        proc = _processor()
        _with_store(proc, AsyncMock()).delete_record_by_external_id = AsyncMock(
            return_value=_request_result([], success=False)
        )
        with flag(True), pytest.raises(RuntimeError, match="mailbox owner"):
            await proc.delete_record_by_external_id("c1", "msg-1", "u1")
        proc.messaging_producer.send_message.assert_not_called()


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


# ---------------------------------------------------------------------------
# A move onto an external id a trashed record still holds
# ---------------------------------------------------------------------------


class _MoveStore(AsyncMock):
    """Records by id. An external-id lookup returns the first match, as both
    graph stores do (``LIMIT 1``), so a trashed holder stored first wins."""

    # The keys this path may write; any other raises, as Arango's strict schema would refuse it.
    _FIELDS = {"externalRecordId": "external_record_id", "trashedExternalRecordId": "trashed_external_record_id"}

    def __init__(self, *records: Record) -> None:
        super().__init__()
        self.docs = {r.id: r for r in records}
        self.get_edges_from_node = AsyncMock(return_value=[])

    def _get_child_mock(self, **kwargs: object) -> AsyncMock:
        return AsyncMock(**kwargs)

    def holders(self, external_id: str) -> list[str]:
        return [r.id for r in self.docs.values() if r.external_record_id == external_id]

    async def get_record_by_external_id(
        self, connector_id: str, external_id: str, visibility: RecordVisibility = RecordVisibility.ALL
    ) -> Record | None:
        return next(
            (r for r in self.docs.values()
             if r.connector_id == connector_id and r.external_record_id == external_id
             and matches_visibility(r, visibility)),
            None,
        )

    async def batch_update_nodes(self, nodes: list[dict], collection: str) -> bool:
        for node in nodes:
            stored = self.docs.get(node["id"])
            if stored is None:
                return False
            fields = {self._FIELDS[k]: v for k, v in node.items() if k != "id"}
            self.docs[node["id"]] = stored.model_copy(update=fields)
        return True

    async def batch_upsert_records(self, records: list[Record], *, release_trashed_external_ids: bool = False) -> None:
        if release_trashed_external_ids:
            ids = {r.id for r in records}
            for record in records:
                for held in list(self.docs.values()):
                    if (
                        held.id not in ids and held.is_deleted is True
                        and held.connector_id == record.connector_id
                        and held.external_record_id == record.external_record_id
                    ):
                        self.docs[held.id] = held.model_copy(update={
                            "external_record_id": f"trashed:{held.id}",
                            "trashed_external_record_id": held.external_record_id,
                        })
        self.docs.update({r.id: r for r in records})

    async def delete_record_by_key(self, key: str) -> None:
        self.docs.pop(key, None)


def _moving_processor(store: _MoveStore) -> DataSourceEntitiesProcessor:
    proc = _processor()
    _with_store(proc, store)
    proc._handle_record_group = AsyncMock(return_value=None)
    proc._handle_parent_record = AsyncMock()
    proc._handle_record_permissions = AsyncMock()
    proc._get_storage_cleanup = MagicMock(return_value=None)
    return proc


def _published(proc: DataSourceEntitiesProcessor, event_type: str) -> list[dict]:
    return [
        c.args[1]["payload"] for c in proc.messaging_producer.send_message.await_args_list
        if c.args[1]["eventType"] == event_type
    ]


class TestMoveOntoAnIdHeldInTheTrash:
    """GitLab, GitHub, network share, Local FS and KB moves all land here."""

    @staticmethod
    def _move() -> tuple[str, Record, list]:
        return ("src/old.py", _stored("fresh-uuid", external_record_id="src/new.py", version=0), [])

    async def test_the_trash_entry_survives_and_the_moved_record_owns_the_id(self) -> None:
        trashed = _stored(
            "trashed-1", external_record_id="src/new.py", virtual_record_id="vr-trashed",
            is_deleted=True, deleted_at=1, delete_source=DeleteSource.USER, delete_batch_id="b-1",
        )
        live = _stored("live-1", external_record_id="src/old.py", virtual_record_id="vr-live")
        store = _MoveStore(trashed, live)
        proc = _moving_processor(store)

        await proc.on_records_moved([self._move()])

        kept = store.docs["trashed-1"]
        assert (kept.is_deleted, kept.delete_batch_id, kept.virtual_record_id) == (True, "b-1", "vr-trashed")
        assert kept.trashed_external_record_id == "src/new.py"
        assert kept.external_record_id == "trashed:trashed-1"
        assert store.holders("src/new.py") == ["live-1"]
        assert (await store.get_record_by_external_id("c1", "src/new.py")).id == "live-1"
        assert _published(proc, EventTypes.DELETE_RECORD.value) == []

    async def test_a_live_duplicate_is_still_retired_and_the_trashed_one_kept(self) -> None:
        trashed = _stored(
            "trashed-1", external_record_id="src/new.py", virtual_record_id="vr-trashed",
            is_deleted=True, deleted_at=1, delete_source=DeleteSource.CONNECTOR,
        )
        duplicate = _stored("dup-1", external_record_id="src/new.py", virtual_record_id="vr-dup")
        live = _stored("live-1", external_record_id="src/old.py")
        store = _MoveStore(trashed, duplicate, live)
        proc = _moving_processor(store)

        await proc.on_records_moved([self._move()])

        assert "dup-1" not in store.docs
        assert store.docs["trashed-1"].is_deleted is True
        assert store.holders("src/new.py") == ["live-1"]
        assert [p["recordId"] for p in _published(proc, EventTypes.DELETE_RECORD.value)] == ["dup-1"]

    async def test_the_release_is_part_of_the_moved_records_write(self) -> None:
        """Written on its own, a release outlived a move that failed before its write (Neo4j commits each statement)."""
        trashed = _stored("trashed-1", external_record_id="src/new.py", is_deleted=True, deleted_at=1)
        store = _MoveStore(trashed, _stored("live-1", external_record_id="src/old.py"))
        proc = _moving_processor(store)
        proc._handle_record_group = AsyncMock(side_effect=RuntimeError("graph unavailable"))

        with pytest.raises(RuntimeError, match="graph unavailable"):
            await proc.on_records_moved([self._move()])
        assert store.docs["trashed-1"].external_record_id == "src/new.py"
        assert store.docs["trashed-1"].trashed_external_record_id is None
        assert _published(proc, EventTypes.DELETE_RECORD.value) == []


class TestCascadeTakesTheCallersReadOfTheFlag:
    """A caller that already acted on the flag (the KB deletes schedule file removal on it) passes its answer."""

    async def test_soft_delete_true_trashes_without_reading_the_flag(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.soft_delete_records = AsyncMock(return_value=_soft_result([("f1", "v1")]))
        with flag(False) as read:
            result = await proc.on_records_deleted_cascade(
                ["f1"], "kb1", delete_source=DeleteSource.USER, soft_delete=True
            )
        read.assert_not_awaited()
        assert result["softDeleted"] is True
        store.delete_records_recursive.assert_not_called()
        assert EventTypes.DELETE_RECORD.value not in _event_types(proc)

    async def test_soft_delete_false_hard_deletes_without_reading_the_flag(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.delete_records_recursive = AsyncMock(return_value={"success": True, "successfully_deleted": 1})
        with flag(True) as read:
            await proc.on_records_deleted_cascade(
                ["f1"], "kb1", delete_source=DeleteSource.USER, soft_delete=False
            )
        read.assert_not_awaited()
        store.delete_records_recursive.assert_awaited_once()
        store.soft_delete_records.assert_not_called()

    @pytest.mark.parametrize("include", [True, False])
    async def test_the_soft_path_hands_include_trashed_roots_to_the_store(self, include) -> None:
        """A removal of what the source no longer has also walks from a root already in the trash."""
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.soft_delete_records = AsyncMock(return_value=_soft_result([("a", "va")]))
        await proc.on_records_deleted_cascade(["f1"], "c1", soft_delete=True, include_trashed_roots=include)
        assert store.soft_delete_records.await_args.kwargs["include_trashed_roots"] is include
