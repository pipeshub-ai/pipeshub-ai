"""The title event a connector publishes so a record is findable by name before it is indexed.

``record-title-events`` carries ``{orgId, recordId, recordName, connectorId, recordGroupId, eventType,
timestamp}`` for a record that was created, renamed or moved (or deleted), and never for one that is
unchanged, whatever its indexing status. A failed publish never fails the sync.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    PushOutcome,
)
from app.services.graph_db.push_claim import PushExpect, PushSet
from app.services.messaging.record_titles import RECORD_TITLE_MAX_CHARS
from tests.unit.connectors.core.test_data_source_entities_processor import (
    _make_ctx,
    _make_processor,
    _make_record,
    _make_tx_store,
    _setup_proc_for_moved,
)

TITLE_TOPIC = "record-title-events"
MODULE = "app.connectors.core.base.data_processor.data_source_entities_processor"

pytestmark = pytest.mark.asyncio


def _proc(tx_store=None):
    """The shared test processor, with the real title publisher put back."""
    proc = _make_processor()
    if "_publish_record_titles" in vars(proc):
        del proc._publish_record_titles
    tx_store = tx_store or _make_tx_store()
    proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
    return proc, tx_store


def _titles(proc) -> list[tuple[str, dict]]:
    """``(key, payload)`` of every title event sent."""
    return [
        (key, message["payload"])
        for call in proc.messaging_producer.send_messages.await_args_list
        if call.args[0] == TITLE_TOPIC
        for key, message in call.args[1]
    ]


def _title_sends(proc) -> int:
    return sum(1 for c in proc.messaging_producer.send_messages.await_args_list if c.args[0] == TITLE_TOPIC)


def _content_events(proc) -> list[str]:
    return [
        message["eventType"]
        for call in proc.messaging_producer.send_messages.await_args_list
        if call.args[0] != TITLE_TOPIC
        for _key, message in call.args[1]
    ]


class TestNewRecords:
    async def test_the_payload_has_exactly_the_contracted_fields(self) -> None:
        proc, _ = _proc()
        record = _make_record(record_name="Q3 plan")
        await proc.on_new_records([(record, [])])

        ((key, payload),) = _titles(proc)
        assert key == record.id
        assert set(payload) == {
            "orgId", "recordId", "recordName", "connectorId", "recordGroupId", "eventType", "timestamp",
        }
        assert payload["orgId"] == "org-1"
        assert (payload["recordId"], payload["recordName"], payload["connectorId"]) == (record.id, "Q3 plan", "conn-1")
        assert payload["eventType"] == "upsert"
        assert isinstance(payload["timestamp"], int)
        message = proc.messaging_producer.send_messages.await_args_list[0].args[1][0][1]
        assert message["eventType"] == "recordTitle"

    async def test_the_org_is_the_processors_not_the_records(self) -> None:
        proc, _ = _proc()
        await proc.on_new_records([(_make_record(org_id="org-other"), [])])
        assert _titles(proc)[0][1]["orgId"] == "org-1"

    async def test_the_event_carries_no_content_fields(self) -> None:
        proc, _ = _proc()
        await proc.on_new_records([(_make_record(), [])])
        text = repr(_titles(proc))
        for leaked in ("signedUrl", "mimeType", "virtualRecordId", "extension", "weburl"):
            assert leaked not in text

    async def test_a_new_record_gets_one_content_event_and_one_title_event(self) -> None:
        proc, _ = _proc()
        await proc.on_new_records([(_make_record(), [])])
        assert _content_events(proc) == ["newRecord"]
        assert len(_titles(proc)) == 1

    async def test_a_record_with_automatic_indexing_off_is_findable_by_name(self) -> None:
        proc, _ = _proc()
        record = _make_record()
        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        await proc.on_new_records([(record, [])])
        assert _content_events(proc) == []
        assert [p["recordId"] for _, p in _titles(proc)] == [record.id]

    async def test_an_internal_record_gets_no_title(self) -> None:
        proc, _ = _proc()
        record = _make_record()
        record.is_internal = True
        await proc.on_new_records([(record, [])])
        assert _titles(proc) == []

    async def test_a_placeholder_gets_no_title(self) -> None:
        proc, _ = _proc()
        record = _make_record()
        record.is_placeholder = True
        await proc.on_new_records([(record, [])])
        assert _titles(proc) == []

    async def test_a_collection_folder_gets_no_title_but_its_files_do(self) -> None:
        proc, _ = _proc()
        folder = _make_record(origin=OriginTypes.UPLOAD.value, record_name="Folder", external_record_id="f1")
        folder.is_file = False
        folder.indexing_status = ProgressStatus.COMPLETED.value
        upload = _make_record(origin=OriginTypes.UPLOAD.value, record_name="notes.txt", external_record_id="f2")
        await proc.on_new_records([(folder, []), (upload, [])])
        assert [p["recordName"] for _, p in _titles(proc)] == ["notes.txt"]

    async def test_a_connector_folder_gets_no_title(self) -> None:
        proc, _ = _proc()
        folder = _make_record(record_name="Folder", external_record_id="f1")
        folder.mime_type = MimeTypes.FOLDER.value
        await proc.on_new_records([(folder, [])])
        assert _titles(proc) == []

    @pytest.mark.parametrize("name", ["Thumbs.db", "~$Report.docx"])
    async def test_a_file_that_is_not_stored_gets_no_title(self, name: str) -> None:
        proc, _ = _proc()
        await proc.on_new_records([(_make_record(record_name=name), [])])
        assert _titles(proc) == []

    @pytest.mark.parametrize("name", ["", "   ", "\n\t"])
    async def test_a_blank_name_gets_no_title_and_does_not_fail(self, name: str) -> None:
        proc, _ = _proc()
        await proc.on_new_records([(_make_record(record_name=name), [])])
        assert _titles(proc) == []

    async def test_a_very_long_name_is_cut(self) -> None:
        proc, _ = _proc()
        await proc.on_new_records([(_make_record(record_name="é" * 5000), [])])
        assert len(_titles(proc)[0][1]["recordName"]) == RECORD_TITLE_MAX_CHARS

    async def test_a_mixed_batch_sends_once_with_the_right_records_in_order(self) -> None:
        proc, _ = _proc()
        normal = _make_record(record_name="normal", external_record_id="e1")
        off = _make_record(record_name="off", external_record_id="e2")
        off.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        internal = _make_record(record_name="internal", external_record_id="e3")
        internal.is_internal = True
        blank = _make_record(record_name=" ", external_record_id="e4")
        folder = _make_record(origin=OriginTypes.UPLOAD.value, record_name="folder", external_record_id="e5")
        folder.is_file = False
        await proc.on_new_records([(r, []) for r in (normal, off, internal, blank, folder)])

        assert _title_sends(proc) == 1
        assert [p["recordName"] for _, p in _titles(proc)] == ["normal", "off"]

    async def test_the_same_record_twice_in_a_batch_is_one_event(self) -> None:
        proc, _ = _proc()
        record = _make_record()
        await proc.on_new_records([(record, []), (record, [])])
        assert [p["recordId"] for _, p in _titles(proc)] == [record.id]


class TestUnchangedRecords:
    @pytest.mark.parametrize("status", [
        ProgressStatus.COMPLETED.value, ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value,
        ProgressStatus.AUTO_INDEX_OFF.value, ProgressStatus.FAILED.value,
    ])
    async def test_a_resync_of_an_unchanged_record_sends_no_title(self, status: str) -> None:
        tx_store = _make_tx_store()
        existing = _make_record(indexing_status=status, external_revision_id="r1")
        tx_store.get_record_by_external_id.return_value = existing
        proc, _ = _proc(tx_store)
        await proc.on_new_records([(_make_record(external_revision_id="r1", indexing_status=status), [])])
        assert _titles(proc) == []

    async def test_unchanged_but_unindexed_still_gets_its_content_event_and_no_title(self) -> None:
        tx_store = _make_tx_store()
        existing = _make_record(indexing_status=ProgressStatus.NOT_STARTED.value, external_revision_id="r1")
        tx_store.get_record_by_external_id.return_value = existing
        proc, _ = _proc(tx_store)
        await proc.on_new_records([(_make_record(external_revision_id="r1"), [])])
        assert _content_events(proc) == ["newRecord"]
        assert _titles(proc) == []

    async def test_a_thousand_unchanged_records_send_no_title(self) -> None:
        tx_store = _make_tx_store()
        existing = _make_record(indexing_status=ProgressStatus.COMPLETED.value, external_revision_id="r1")
        tx_store.get_record_by_external_id.return_value = existing
        proc, _ = _proc(tx_store)
        await proc.on_new_records([
            (_make_record(external_revision_id="r1", indexing_status=ProgressStatus.COMPLETED.value), [])
            for _ in range(1000)
        ])
        assert _title_sends(proc) == 0

    async def test_a_rename_sends_the_new_name_and_no_content_event(self) -> None:
        tx_store = _make_tx_store()
        existing = _make_record(
            record_name="old name", indexing_status=ProgressStatus.COMPLETED.value, external_revision_id="r1",
        )
        tx_store.get_record_by_external_id.return_value = existing
        proc, _ = _proc(tx_store)
        await proc.on_new_records([(_make_record(record_name="new name", external_revision_id="r1"), [])])
        assert [p["recordName"] for _, p in _titles(proc)] == ["new name"]
        assert _content_events(proc) == []

    async def test_a_move_to_another_group_sends_the_new_group(self) -> None:
        tx_store = _make_tx_store()
        existing = _make_record(
            indexing_status=ProgressStatus.COMPLETED.value, external_revision_id="r1", record_group_id="g1",
        )
        tx_store.get_record_by_external_id.return_value = existing
        proc, _ = _proc(tx_store)

        async def to_group_two(record, _tx):
            record.record_group_id = "g2"
            return "g2"

        proc._handle_record_group = AsyncMock(side_effect=to_group_two)
        proc._link_record_to_group = AsyncMock(return_value=False)
        await proc.on_new_records([(_make_record(external_revision_id="r1"), [])])
        ((_, payload),) = _titles(proc)
        assert payload["recordGroupId"] == "g2"

    async def test_a_change_of_parent_folder_alone_sends_nothing(self) -> None:
        tx_store = _make_tx_store()
        existing = _make_record(
            indexing_status=ProgressStatus.COMPLETED.value, external_revision_id="r1", parent_external_record_id="p1",
        )
        tx_store.get_record_by_external_id.return_value = existing
        proc, _ = _proc(tx_store)
        await proc.on_new_records([(_make_record(external_revision_id="r1", parent_external_record_id="p2"), [])])
        assert _titles(proc) == []

    async def test_a_stub_that_becomes_a_real_record_gets_its_title(self) -> None:
        tx_store = _make_tx_store()
        stub = _make_record(external_revision_id="r1")
        stub.is_placeholder = True
        tx_store.get_record_by_external_id.return_value = stub
        proc, _ = _proc(tx_store)
        await proc.on_new_records([(_make_record(external_revision_id="r1"), [])])
        assert len(_titles(proc)) == 1


class TestOtherWritePaths:
    async def test_a_metadata_update_that_renames_sends_the_title(self) -> None:
        tx_store = _make_tx_store()
        existing = _make_record(record_name="old", indexing_status=ProgressStatus.COMPLETED.value)
        tx_store.get_record_by_external_id.return_value = existing
        proc, _ = _proc(tx_store)
        await proc.on_record_metadata_update(_make_record(record_name="renamed"))
        assert [p["recordName"] for _, p in _titles(proc)] == ["renamed"]

    async def test_a_metadata_update_that_changes_nothing_sends_nothing(self) -> None:
        tx_store = _make_tx_store()
        tx_store.get_record_by_external_id.return_value = _make_record(indexing_status=ProgressStatus.COMPLETED.value)
        proc, _ = _proc(tx_store)
        await proc.on_record_metadata_update(_make_record())
        assert _titles(proc) == []

    async def test_a_content_update_with_automatic_indexing_off_still_sends_the_title(self) -> None:
        proc, _ = _proc()
        record = _make_record()
        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        await proc.on_record_content_update(record)
        assert [p["recordId"] for _, p in _titles(proc)] == [record.id]
        proc.messaging_producer.send_message.assert_not_awaited()

    async def test_a_pushed_record_renamed_without_a_content_change_sends_the_title(self) -> None:
        from contextlib import asynccontextmanager

        stored = _make_record(
            record_name="old", origin=OriginTypes.UPLOAD.value, external_record_id="doc-1",
            indexing_status=ProgressStatus.COMPLETED.value, external_revision_id="r1",
        )
        store = AsyncMock()
        store.explicit = True
        store.claim_push_record = AsyncMock(return_value=MagicMock())
        store.get_record_by_key = AsyncMock(return_value=stored.to_arango_base_record())
        store.get_record_by_external_id = AsyncMock(return_value=stored)
        store.get_record_group_by_external_id = AsyncMock(return_value=None)

        @asynccontextmanager
        async def transaction(**_):
            yield store

        proc = _make_processor()
        del proc._publish_record_titles
        proc.data_store_provider.transaction = transaction
        proc.data_store_provider.graph_provider.get_document = AsyncMock(return_value={"id": "kb", "status": None})
        renamed = _make_record(
            record_name="new", origin=OriginTypes.UPLOAD.value, external_record_id="doc-1", external_revision_id="r1",
        )
        renamed.id = stored.id
        with patch(f"{MODULE}.PushClaim") as claim:
            store.claim_push_record.return_value = claim.CLAIMED
            outcome = await proc.on_pushed_record(
                renamed, expected=PushExpect("doc-1", "r1", 1), new=PushSet("doc-1", 1), content_changed=False,
            )
        assert outcome == PushOutcome(event=None, published=False)
        assert [p["recordName"] for _, p in _titles(proc)] == ["new"]


def _move_proc(old, *, new_name="new.py", status=None, internal=False, group=None):
    tx_store = _make_tx_store()
    proc = _setup_proc_for_moved(tx_store, old_record=old)
    del proc._publish_record_titles
    new = _make_record(record_name=new_name, external_revision_id="same")
    new.indexing_status = status or ProgressStatus.NOT_STARTED.value
    new.is_internal = internal
    if group is not None:
        async def set_group(record, _tx):
            record.record_group_id = group
            return group

        proc._handle_record_group = AsyncMock(side_effect=set_group)
    return proc, new, tx_store


def _old(name="old.py", *, revision="same", group=None, **over):
    return _make_record(record_name=name, external_revision_id=revision, record_group_id=group, **over)


class TestMoves:
    async def test_a_rename_sends_the_title_and_the_membership_refresh(self) -> None:
        old = _old()
        old.virtual_record_id = "vr-1"
        proc, new, _ = _move_proc(old)
        await proc.on_records_moved([("old-ext", new, [])])
        assert [p["recordName"] for _, p in _titles(proc)] == ["new.py"]
        assert _content_events(proc) == ["syncVectorMembership"]

    async def test_a_rename_with_changed_content_sends_the_title_and_the_update(self) -> None:
        proc, new, _ = _move_proc(_old(revision="before"))
        new.external_revision_id = "after"
        await proc.on_records_moved([("old-ext", new, [])])
        assert len(_titles(proc)) == 1
        assert "updateRecord" in _content_events(proc)

    async def test_with_automatic_indexing_off_the_title_is_still_sent(self) -> None:
        proc, new, _ = _move_proc(_old(revision="before"), status=ProgressStatus.AUTO_INDEX_OFF.value)
        new.external_revision_id = "after"
        await proc.on_records_moved([("old-ext", new, [])])
        assert len(_titles(proc)) == 1
        assert _content_events(proc) == []

    async def test_an_internal_record_gets_no_title(self) -> None:
        proc, new, _ = _move_proc(_old(), internal=True)
        await proc.on_records_moved([("old-ext", new, [])])
        assert _titles(proc) == []

    async def test_a_move_to_another_group_sends_the_new_group_even_with_the_same_name(self) -> None:
        proc, new, _ = _move_proc(_old("same.py", group="g1"), new_name="same.py", group="g2")
        await proc.on_records_moved([("old-ext", new, [])])
        ((_, payload),) = _titles(proc)
        assert payload["recordGroupId"] == "g2"

    async def test_a_move_that_keeps_name_and_group_sends_nothing(self) -> None:
        proc, new, _ = _move_proc(_old("same.py"), new_name="same.py")
        await proc.on_records_moved([("old-ext", new, [])])
        assert _titles(proc) == []

    async def test_an_unknown_old_record_is_a_plain_add(self) -> None:
        proc, new, _ = _move_proc(None)
        await proc.on_records_moved([("old-ext", new, [])])
        assert len(_titles(proc)) == 1

    async def test_a_record_in_the_trash_is_left_alone(self) -> None:
        old = _old()
        old.is_deleted = True
        proc, new, _ = _move_proc(old)
        await proc.on_records_moved([("old-ext", new, [])])
        assert _titles(proc) == []

    async def test_a_record_of_another_org_is_left_alone(self) -> None:
        proc, new, _ = _move_proc(_old(org_id="org-2"))
        await proc.on_records_moved([("old-ext", new, [])])
        assert _titles(proc) == []


class TestFailuresNeverFailTheSync:
    async def test_a_broker_that_refuses_is_logged_and_content_events_still_go(self, caplog) -> None:
        proc, _ = _proc()

        async def send(topic, messages):
            return [topic != TITLE_TOPIC] * len(messages)

        proc.messaging_producer.send_messages = AsyncMock(side_effect=send)
        with caplog.at_level(logging.WARNING):
            await proc.on_new_records([(_make_record(), [])])
        assert _content_events(proc) == ["newRecord"]
        proc.logger.warning.assert_called()
        assert "not accepted" in proc.logger.warning.call_args.args[0]

    async def test_a_producer_that_raises_does_not_fail_the_sync(self) -> None:
        proc, _ = _proc()
        calls = []

        async def send(topic, messages):
            calls.append(topic)
            if topic == TITLE_TOPIC:
                raise ConnectionError("broker down")
            return [True] * len(messages)

        proc.messaging_producer.send_messages = AsyncMock(side_effect=send)
        record = _make_record()
        await proc.on_new_records([(record, [])])
        assert calls == [TITLE_TOPIC, "record-events"], "the content event was still published"

    async def test_the_log_does_not_carry_the_name(self) -> None:
        proc, _ = _proc()
        async def send(topic, messages):
            if topic == TITLE_TOPIC:
                raise ConnectionError("down")
            return [True] * len(messages)

        proc.messaging_producer.send_messages = AsyncMock(side_effect=send)
        await proc.on_new_records([(_make_record(record_name="Secret merger plan"), [])])
        logged = repr(proc.logger.warning.call_args_list)
        assert "Secret merger plan" not in logged

    async def test_an_unusual_stored_row_does_not_raise(self) -> None:
        proc, _ = _proc()
        # A fake row with no name or group attributes at all.
        assert proc._title_event_due(_make_record(), MagicMock(spec=["is_placeholder"], is_placeholder=False))


class TestOnlyAfterCommit:
    async def test_the_title_is_sent_after_the_transaction_ends(self) -> None:
        tx_store = _make_tx_store()
        proc, _ = _proc(tx_store)
        order: list[str] = []
        ctx = _make_ctx(tx_store)

        async def leave(*_):
            order.append("commit")
            return False

        ctx.__aexit__ = AsyncMock(side_effect=leave)
        proc.data_store_provider.transaction.return_value = ctx

        async def send(topic, messages):
            order.append(topic)
            return [True] * len(messages)

        proc.messaging_producer.send_messages = AsyncMock(side_effect=send)
        await proc.on_new_records([(_make_record(), [])])
        assert order.index("commit") < order.index(TITLE_TOPIC)

    async def test_a_slice_that_rolls_back_sends_no_title(self) -> None:
        tx_store = _make_tx_store()
        tx_store.get_record_by_external_id.side_effect = RuntimeError("db fail")
        proc, _ = _proc(tx_store)
        with pytest.raises(RuntimeError):
            await proc.on_new_records([(_make_record(), [])])
        assert _title_sends(proc) == 0


class TestDeletes:
    async def test_a_deleted_record_that_was_never_indexed_gets_a_delete_event(self) -> None:
        store = AsyncMock()
        store.get_record_by_key = AsyncMock(return_value={"_key": "rec-1", "orgId": "org-1", "connectorId": "c1"})
        store.delete_single_record = AsyncMock(return_value={"success": True, "eventData": None})
        proc, _ = _proc(store)
        with patch(f"{MODULE}.is_soft_delete_enabled", AsyncMock(return_value=False)):
            await proc.on_record_deleted("rec-1")
        ((key, payload),) = _titles(proc)
        assert key == "rec-1"
        assert (payload["eventType"], payload["recordId"], payload["orgId"], payload["connectorId"]) == (
            "delete", "rec-1", "org-1", "c1",
        )
        assert payload["recordName"] is None

    async def test_a_cascade_delete_sends_one_event_per_deleted_record(self) -> None:
        store = AsyncMock()
        store.delete_records_recursive = AsyncMock(return_value={
            "success": True, "successfully_deleted": 2, "eventData": None,
            "deleted_records": [{"record_id": "a", "name": "A"}, {"record_id": "b", "name": "B"}],
        })
        store.get_edges_to_node = AsyncMock(return_value=[])
        proc, _ = _proc(store)
        with patch(f"{MODULE}.notify_kb_records_changed", AsyncMock()):
            await proc.on_records_deleted_cascade(["a"], "c1", soft_delete=False)
        assert [(p["eventType"], p["recordId"]) for _, p in _titles(proc)] == [("delete", "a"), ("delete", "b")]
        assert all("name" not in p for _, p in _titles(proc))

    async def test_a_failed_cascade_sends_nothing(self) -> None:
        store = AsyncMock()
        store.delete_records_recursive = AsyncMock(return_value={"success": False, "reason": "no"})
        proc, _ = _proc(store)
        await proc.on_records_deleted_cascade(["a"], "c1", soft_delete=False)
        assert _titles(proc) == []

    async def test_a_record_sent_to_the_trash_keeps_its_point(self) -> None:
        store = AsyncMock()
        store.soft_delete_records = AsyncMock(return_value={
            "soft_deleted_records": [{"record_id": "a"}], "org_id": "org-1", "virtual_record_ids": [],
        })
        proc, _ = _proc(store)
        with patch(f"{MODULE}.notify_kb_records_changed", AsyncMock()):
            await proc.on_records_deleted_cascade(["a"], "c1", soft_delete=True)
        assert _titles(proc) == []

