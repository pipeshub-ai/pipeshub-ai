"""Bringing records back from the trash in DataSourceEntitiesProcessor.

``restore_trashed_records`` restores a batch in one transaction, giving back the
external id a record gave up, and refuses when a live record holds that id now.
A sync that sees again an item the connector deleted restores it and indexes it
again; one a user deleted stays in the trash. ``on_record_deleted`` reports a
move to the trash so a connector keeps the stored file.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    Connectors,
    DeleteSource,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
    RestoreRefused,
)
from app.models.entities import FileRecord, Record, RecordType
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.utils import TRASHED_EXTERNAL_ID_PREFIX

MODULE = "app.connectors.core.base.data_processor.data_source_entities_processor"


def _processor() -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    proc.messaging_producer.send_messages = AsyncMock(side_effect=lambda topic, messages: [True] * len(messages))
    return proc


def _with_store(proc: DataSourceEntitiesProcessor, store) -> object:
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    proc.data_store_provider.transaction.return_value = ctx
    return store


def _stored(record_id: str, **overrides) -> Record:
    fields = {
        "id": record_id, "org_id": "org-1", "record_name": f"{record_id}.pdf", "record_type": RecordType.FILE,
        "external_record_id": f"ext-{record_id}", "version": 1, "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.GOOGLE_DRIVE, "connector_id": "c1",
    }
    fields.update(overrides)
    return Record(**fields)


class _Store:
    """External-id lookups over a dict of records, and restore over the trashed ones."""

    def __init__(self, records: list[Record]) -> None:
        self.records = {r.id: r for r in records}
        self.restored: list[tuple[list[dict], str | None]] = []
        self.released: list[dict] = []
        self.parents: dict[str, str] = {}

    async def get_record_by_external_id(
        self, connector_id: str, external_id: str, visibility: RecordVisibility = RecordVisibility.ALL
    ) -> Record | None:
        for r in self.records.values():
            if r.external_record_id != external_id:
                continue
            if visibility is RecordVisibility.LIVE and r.is_deleted:
                continue
            if visibility is RecordVisibility.DELETED and not r.is_deleted:
                continue
            return r
        return None

    async def batch_update_nodes(self, updates: list[dict], collection: str) -> bool:
        for update in updates:
            self.released.append(update)
            self.records[update["id"]] = self.records[update["id"]].model_copy(
                update={"external_record_id": update["externalRecordId"]}
            )
        return True

    async def restore_records(
        self,
        restores: list[dict],
        batch_id: str | None,
        *,
        connector_id: str | None = None,
        require_live_parent: bool = False,
    ) -> list[str]:
        """All or nothing, with the external ids taken back in the same write, as the providers do."""
        ids = {r["id"] for r in restores}
        in_batch = all(
            (rec := self.records.get(r["id"])) is not None and rec.is_deleted and rec.delete_batch_id == batch_id
            for r in restores
        )
        if require_live_parent and any(
            (parent := self.records.get(self.parents.get(rid, ""))) is not None
            and parent.is_deleted and parent.id not in ids
            for rid in ids
        ):
            return []
        claims = {r["id"]: r["reclaimExternalRecordId"] for r in restores if r.get("reclaimExternalRecordId")}
        taken = any(
            rec.external_record_id == ext and rec.id != rid and (not rec.is_deleted or rec.id in ids)
            for rid, ext in claims.items()
            for rec in self.records.values()
        )
        if not in_batch or taken:
            return []
        for ext in claims.values():
            for rec in list(self.records.values()):
                if rec.external_record_id == ext and rec.is_deleted and rec.id not in ids:
                    await self.batch_update_nodes(
                        [{"id": rec.id, "externalRecordId": f"{TRASHED_EXTERNAL_ID_PREFIX}{rec.id}",
                          "trashedExternalRecordId": ext}],
                        "records",
                    )
        self.restored.append((restores, batch_id))
        return [r["id"] for r in restores]


def _trashed(record_id: str, batch: str = "b-1", **overrides) -> Record:
    return _stored(record_id, is_deleted=True, delete_batch_id=batch, delete_source=DeleteSource.USER, **overrides)


class TestRestoreTrashedRecords:
    async def test_a_trashed_parent_refuses_only_when_asked(self) -> None:
        proc = _processor()
        store = _with_store(proc, _Store([_trashed("r1"), _trashed("folder-1", batch="b-2")]))
        store.parents = {"r1": "folder-1"}
        with patch(f"{MODULE}.notify_kb_records_changed", AsyncMock()), \
                patch(f"{MODULE}.record_restored"):
            with pytest.raises(RestoreRefused) as refused:
                await proc.restore_trashed_records("c1", "b-1", [{"id": "r1"}], require_live_parent=True)
            assert refused.value.code == 409
            assert store.restored == []
            assert await proc.restore_trashed_records("c1", "b-1", [{"id": "r1"}]) == ["r1"]

    async def test_the_batch_comes_back_in_one_transaction(self) -> None:
        proc = _processor()
        store = _with_store(proc, _Store([_trashed("r1"), _trashed("r2")]))
        with patch(f"{MODULE}.notify_kb_records_changed", AsyncMock()) as notify, \
                patch(f"{MODULE}.record_restored") as counted:
            restored = await proc.restore_trashed_records(
                "c1", "b-1", [{"id": "r1", "set": {"indexingStatus": "NOT_STARTED"}}, {"id": "r2"}]
            )
        assert restored == ["r1", "r2"]
        ((restores, batch),) = store.restored
        assert batch == "b-1"
        assert restores == [{"id": "r1", "set": {"indexingStatus": "NOT_STARTED"}}, {"id": "r2", "set": {}}]
        notify.assert_awaited_once_with("c1")
        counted.assert_called_once_with(DeleteSource.USER.value, 2)

    async def test_a_released_external_id_is_given_back_when_free(self) -> None:
        proc = _processor()
        store = _with_store(proc, _Store([
            _trashed("r1", external_record_id=f"{TRASHED_EXTERNAL_ID_PREFIX}r1", trashed_external_record_id="ext-a"),
        ]))
        with patch(f"{MODULE}.notify_kb_records_changed", AsyncMock()):
            await proc.restore_trashed_records("c1", "b-1", [{"id": "r1", "trashedExternalRecordId": "ext-a"}])
        ((restores, _),) = store.restored
        assert restores == [{"id": "r1", "set": {}, "reclaimExternalRecordId": "ext-a"}]

    async def test_a_live_record_on_the_id_refuses_the_whole_restore(self) -> None:
        proc = _processor()
        store = _with_store(proc, _Store([
            _trashed("r1", external_record_id=f"{TRASHED_EXTERNAL_ID_PREFIX}r1", trashed_external_record_id="ext-a"),
            _trashed("r2"),
            _stored("live", external_record_id="ext-a", record_name="report.pdf"),
        ]))
        with pytest.raises(RestoreRefused) as refused:
            await proc.restore_trashed_records(
                "c1", "b-1",
                [{"id": "r2"}, {"id": "r1", "name": "old report.pdf", "trashedExternalRecordId": "ext-a"}],
            )
        assert refused.value.code == 409
        assert refused.value.reason == (
            "'old report.pdf' can't be restored because 'report.pdf' has taken its place. That usually means "
            "the same item was added again after this one was deleted. To restore this one, delete "
            "'report.pdf' first, then try again."
        )
        assert refused.value.details["conflicting_record_id"] == "live"
        assert store.restored == [] and store.released == []

    async def test_a_trashed_holder_of_the_id_gives_it_up(self) -> None:
        proc = _processor()
        store = _with_store(proc, _Store([
            _trashed("r1", external_record_id=f"{TRASHED_EXTERNAL_ID_PREFIX}r1", trashed_external_record_id="ext-a"),
            _trashed("other", batch="b-2", external_record_id="ext-a"),
        ]))
        with patch(f"{MODULE}.notify_kb_records_changed", AsyncMock()):
            await proc.restore_trashed_records("c1", "b-1", [{"id": "r1", "trashedExternalRecordId": "ext-a"}])
        assert store.released == [{
            "id": "other",
            "externalRecordId": f"{TRASHED_EXTERNAL_ID_PREFIX}other",
            "trashedExternalRecordId": "ext-a",
        }]
        assert store.restored[0][0] == [{"id": "r1", "set": {}, "reclaimExternalRecordId": "ext-a"}]

    async def test_a_holder_inside_the_batch_is_refused_not_released(self) -> None:
        proc = _processor()
        store = _with_store(proc, _Store([
            _trashed("r1", external_record_id=f"{TRASHED_EXTERNAL_ID_PREFIX}r1", trashed_external_record_id="ext-a"),
            _trashed("r2", external_record_id="ext-a"),
        ]))
        with pytest.raises(RestoreRefused) as refused:
            await proc.restore_trashed_records(
                "c1", "b-1", [{"id": "r1", "trashedExternalRecordId": "ext-a"}, {"id": "r2"}]
            )
        assert "one at a time" in refused.value.reason
        assert store.released == [] and store.restored == []

    async def test_two_items_reclaiming_one_id_are_refused(self) -> None:
        proc = _processor()
        _with_store(proc, _Store([]))
        with pytest.raises(RestoreRefused):
            await proc.restore_trashed_records(
                "c1", "b-1",
                [{"id": "r1", "trashedExternalRecordId": "ext-a"}, {"id": "r2", "trashedExternalRecordId": "ext-a"}],
            )

    async def test_a_record_no_longer_in_the_batch_rolls_everything_back(self) -> None:
        proc = _processor()
        _with_store(proc, _Store([_trashed("r1"), _stored("r2")]))
        with pytest.raises(RestoreRefused) as refused:
            await proc.restore_trashed_records("c1", "b-1", [{"id": "r1"}, {"id": "r2"}])
        assert refused.value.details["missing"] == ["r1", "r2"]
        assert "Refresh the page" in refused.value.reason


def _incoming(**overrides) -> FileRecord:
    fields: dict = {
        "org_id": "org-1", "record_name": "a.pdf", "record_type": RecordType.FILE, "external_record_id": "ext-1",
        "external_revision_id": "rev-1", "version": 2, "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.GOOGLE_DRIVE, "connector_id": "c1", "is_file": True,
    }
    fields.update(overrides)
    return FileRecord(**fields)


def _sync_store(existing: Record) -> AsyncMock:
    store = AsyncMock()
    store.get_record_by_external_id = AsyncMock(return_value=existing)
    store.get_record_group_by_external_id = AsyncMock(return_value=None)
    store.restore_records = AsyncMock(return_value=[existing.id])
    return store


class TestSyncBringsBackWhatTheConnectorDeleted:
    async def test_a_connector_deleted_item_seen_again_is_restored_and_indexed_again(self) -> None:
        proc = _processor()
        existing = _stored(
            "stored-1", external_record_id="ext-1", external_revision_id="rev-1", is_deleted=True,
            delete_source=DeleteSource.CONNECTOR, delete_batch_id="b-9",
            indexing_status=ProgressStatus.COMPLETED.value,
        )
        store = _sync_store(existing)

        processed, _ = await proc._process_record(_incoming(), [], store)

        store.restore_records.assert_awaited_once_with([{"id": "stored-1"}], "b-9")
        assert processed is not None and processed.id == "stored-1"
        # Unchanged content would stay COMPLETED and publish nothing; its vectors are gone.
        assert processed.indexing_status == ProgressStatus.NOT_STARTED.value
        assert processed.is_deleted is False

    async def test_a_manual_only_item_never_indexed_stays_manual(self) -> None:
        proc = _processor()
        existing = _stored(
            "stored-1", external_record_id="ext-1", is_deleted=True, delete_source=DeleteSource.CONNECTOR,
            delete_batch_id="b-9", indexing_status=ProgressStatus.AUTO_INDEX_OFF.value,
        )
        processed, _ = await proc._process_record(
            _incoming(indexing_status=ProgressStatus.AUTO_INDEX_OFF.value), [], _sync_store(existing)
        )
        assert processed.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_the_restored_item_is_published_for_indexing(self) -> None:
        proc = _processor()
        existing = _stored(
            "stored-1", external_record_id="ext-1", external_revision_id="rev-1", is_deleted=True,
            delete_source=DeleteSource.CONNECTOR, delete_batch_id="b-9",
            indexing_status=ProgressStatus.COMPLETED.value,
        )
        _with_store(proc, _sync_store(existing))
        proc._snapshot_old_paths = AsyncMock(return_value={})
        proc._flush_pending_blob_moves = AsyncMock()
        await proc.on_new_records([(_incoming(), [])])
        ((_topic, messages),) = [c.args for c in proc.messaging_producer.send_messages.await_args_list]
        assert [(key, m["eventType"]) for key, m in messages] == [("stored-1", "newRecord")]

    @pytest.mark.parametrize("source", [DeleteSource.USER, DeleteSource.SYSTEM, None])
    async def test_an_item_a_user_deleted_stays_in_the_trash(self, source) -> None:
        proc = _processor()
        existing = _stored("stored-1", external_record_id="ext-1", is_deleted=True, delete_source=source)
        store = _sync_store(existing)
        assert await proc._process_record(_incoming(), [], store) == (None, [])
        store.restore_records.assert_not_called()

    async def test_a_path_that_publishes_nothing_does_not_restore(self) -> None:
        """A metadata update preserves COMPLETED and sends no event: restored, it would have no vectors."""
        proc = _processor()
        existing = _stored(
            "stored-1", external_record_id="ext-1", is_deleted=True, delete_source=DeleteSource.CONNECTOR,
        )
        store = _sync_store(existing)
        assert await proc._process_record(_incoming(), [], store, publishes_event=False) == (None, [])
        store.restore_records.assert_not_called()

    async def test_a_restore_that_lost_a_race_fails_the_batch(self) -> None:
        proc = _processor()
        existing = _stored(
            "stored-1", external_record_id="ext-1", is_deleted=True, delete_source=DeleteSource.CONNECTOR,
        )
        store = _sync_store(existing)
        store.restore_records = AsyncMock(return_value=[])
        with pytest.raises(RuntimeError):
            await proc._process_record(_incoming(), [], store)


class TestOnRecordDeletedReportsTheTrash:
    async def test_true_when_the_record_went_to_the_trash(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.get_record_by_key = AsyncMock(return_value={"connectorId": "c1"})
        proc.on_records_soft_deleted = AsyncMock()
        with patch(f"{MODULE}.is_soft_delete_enabled", AsyncMock(return_value=True)):
            assert await proc.on_record_deleted("r1") is True

    async def test_false_when_there_is_no_such_record(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.get_record_by_key = AsyncMock(return_value=None)
        with patch(f"{MODULE}.is_soft_delete_enabled", AsyncMock(return_value=True)):
            assert await proc.on_record_deleted("r1") is False

    async def test_false_on_a_hard_delete(self) -> None:
        proc = _processor()
        store = _with_store(proc, AsyncMock())
        store.get_record_by_key = AsyncMock(return_value=None)
        with patch(f"{MODULE}.is_soft_delete_enabled", AsyncMock(return_value=False)):
            assert await proc.on_record_deleted("r1") is False
