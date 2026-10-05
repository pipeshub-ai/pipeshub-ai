"""A duplicate ends with its twin's final enrichment state, never IN_PROGRESS.

The twin turns ``indexingStatus`` COMPLETED before its enrichment (the
extraction LLM call) runs, so a duplicate arriving in that window used to copy
``extractionStatus=IN_PROGRESS``. The twin's completion only promotes QUEUED
duplicates, so nothing ever finalised that copy.

Driven through the real ``EventProcessor._check_duplicate_by_md5`` over an
in-memory records store. The twin's completion is the provider's promotion of
QUEUED duplicates, using the same field mapping the providers use.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.events.events import EventProcessor
from app.exceptions.indexing_exceptions import IndexingError
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.services.graph_db.interface.graph_db_provider import (
    promoted_duplicate_extraction_status,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

COMPLETED = ProgressStatus.COMPLETED.value
IN_PROGRESS = ProgressStatus.IN_PROGRESS.value
QUEUED = ProgressStatus.QUEUED.value
FAILED = ProgressStatus.FAILED.value
NOT_STARTED = ProgressStatus.NOT_STARTED.value

CONTENT = b"the same quarterly report, uploaded twice"
ORG = "org-1"


class RecordsStore:
    """The records collection, with the queries dedup and promotion run against it."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.copied_relationships: list[tuple[str, str]] = []
        self.before_write: dict[tuple[str, str], Any] = {}
        self.failing_reads: set[str] = set()

    def add(self, key: str, **fields: Any) -> None:  # noqa: ANN401
        self.records[key] = {"_key": key, "orgId": ORG, "recordType": "FILE", **fields}

    async def update_node(self, key: str, collection: str, fields: dict[str, Any]) -> bool:
        assert collection == CollectionNames.RECORDS.value
        hook = self.before_write.pop((key, fields.get("indexingStatus")), None)
        if hook is not None:
            hook()
        self.records[key].update(fields)
        return True

    async def get_document(
        self, key: str, collection: str, *, raise_on_error: bool = False, **_: object
    ) -> dict[str, Any] | None:
        if key in self.failing_reads:
            # Both providers log a failed read and answer None unless asked to raise.
            if raise_on_error:
                raise ConnectionError("graph unavailable")
            return None
        record = self.records.get(key)
        return dict(record) if record is not None else None

    async def find_duplicate_records(
        self, record_key: str, md5_checksum: str, org_id: str, **_: object
    ) -> list[dict[str, Any]]:
        return [
            dict(r) for k, r in self.records.items()
            if k != record_key and r.get("md5Checksum") == md5_checksum and r.get("orgId") == org_id
        ]

    async def batch_upsert_nodes(self, rows: list[dict[str, Any]], collection: str) -> bool:
        assert collection == CollectionNames.RECORDS.value
        for row in rows:
            self.records[row["id"]].update({k: v for k, v in row.items() if k != "id"})
        return True

    async def copy_document_relationships(self, source: str, target: str) -> bool:
        self.copied_relationships.append((source, target))
        return True

    def promote_queued_duplicates(self, record_id: str, new_status: str, reason: str | None = None) -> int:
        primary = self.records[record_id]
        extraction = promoted_duplicate_extraction_status(new_status, primary)
        if extraction is None:
            return 0
        queued = [
            r for k, r in self.records.items()
            if k != record_id and r.get("indexingStatus") == QUEUED
            and r.get("md5Checksum") == primary.get("md5Checksum") and r.get("orgId") == primary["orgId"]
        ]
        for record in queued:
            record.update({
                "indexingStatus": new_status,
                "virtualRecordId": primary.get("virtualRecordId"),
                "extractionStatus": extraction,
                **({"reason": reason} if reason else {}),
            })
        return len(queued)


@pytest.fixture
def store() -> RecordsStore:
    return RecordsStore()


@pytest.fixture
def processor(store: RecordsStore) -> EventProcessor:
    pipeline_host = MagicMock()
    pipeline_host.indexing_pipeline = AsyncMock()
    pipeline_host.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="stored/path")
    return EventProcessor(MagicMock(), pipeline_host, store, MagicMock())


def md5_of(processor: EventProcessor) -> str:
    return processor._hash_for_dedup(CONTENT, "FILE", None)


def add_twin(store: RecordsStore, processor: EventProcessor, extraction: str, **fields: Any) -> None:  # noqa: ANN401
    store.add("twin", **{
        "md5Checksum": md5_of(processor),
        "indexingStatus": COMPLETED,
        "virtualRecordId": "vr-twin",
        "extractionStatus": extraction,
        "processingStartedAt": get_epoch_timestamp_in_ms(),
        **fields,
    })


async def dedup(store: RecordsStore, processor: EventProcessor, key: str = "dup") -> Any:  # noqa: ANN401
    if key not in store.records:
        store.add(key, indexingStatus=QUEUED, extractionStatus=NOT_STARTED)
    return await processor._check_duplicate_by_md5(CONTENT, dict(store.records[key]))


class TestTwinStillEnriching:
    async def test_the_duplicate_waits_and_takes_the_twins_final_enrichment(self, store, processor) -> None:
        add_twin(store, processor, IN_PROGRESS)

        decision = await dedup(store, processor)

        assert decision.skip_indexing is True
        assert store.records["dup"]["indexingStatus"] == QUEUED
        assert store.records["dup"]["extractionStatus"] != IN_PROGRESS

        store.records["twin"]["extractionStatus"] = COMPLETED
        assert store.promote_queued_duplicates("twin", COMPLETED) == 1

        assert store.records["dup"]["indexingStatus"] == COMPLETED
        assert store.records["dup"]["extractionStatus"] == COMPLETED
        assert store.records["dup"]["virtualRecordId"] == "vr-twin"

    async def test_a_twin_whose_enrichment_fails_leaves_the_duplicate_failed_not_running(
        self, store, processor
    ) -> None:
        add_twin(store, processor, IN_PROGRESS)
        await dedup(store, processor)

        store.records["twin"]["extractionStatus"] = FAILED
        store.promote_queued_duplicates("twin", COMPLETED)

        assert store.records["dup"]["indexingStatus"] == COMPLETED
        assert store.records["dup"]["extractionStatus"] == FAILED

    async def test_a_twin_that_fails_outright_finalises_the_duplicate(self, store, processor) -> None:
        add_twin(store, processor, IN_PROGRESS)
        await dedup(store, processor)

        store.records["twin"].update(indexingStatus=FAILED, extractionStatus=FAILED)
        store.promote_queued_duplicates("twin", FAILED, reason="The original copy failed")

        assert store.records["dup"]["indexingStatus"] == FAILED
        assert store.records["dup"]["extractionStatus"] == FAILED

    async def test_an_abandoned_enrichment_is_not_waited_on(self, store, processor) -> None:
        add_twin(store, processor, IN_PROGRESS)
        store.records["twin"]["processingStartedAt"] = 0

        decision = await dedup(store, processor)

        assert decision.skip_indexing is False, "indexed on its own rather than parked behind a dead twin"
        assert store.records["dup"]["extractionStatus"] != IN_PROGRESS


class TestTwinAlreadyFinished:
    @pytest.mark.parametrize("final", [COMPLETED, FAILED, NOT_STARTED])
    async def test_the_duplicate_copies_the_final_state_at_once(self, store, processor, final) -> None:
        add_twin(store, processor, final)

        decision = await dedup(store, processor)

        assert decision.skip_indexing is True
        assert store.records["dup"]["indexingStatus"] == COMPLETED
        assert store.records["dup"]["extractionStatus"] == final
        assert store.copied_relationships == [("twin", "dup")]

    async def test_a_redelivered_duplicate_ends_in_the_same_state(self, store, processor) -> None:
        add_twin(store, processor, COMPLETED)
        await dedup(store, processor)
        first = dict(store.records["dup"])

        await dedup(store, processor)

        assert {k: v for k, v in store.records["dup"].items() if not k.startswith("last")} == {
            k: v for k, v in first.items() if not k.startswith("last")
        }


class TestTheTwinCannotBeReadAfterQueueing:
    async def test_a_failed_read_fails_the_attempt_instead_of_acknowledging_it(self, store, processor) -> None:
        add_twin(store, processor, NOT_STARTED, indexingStatus=IN_PROGRESS)
        store.before_write[("dup", QUEUED)] = lambda: store.failing_reads.add("twin")

        with pytest.raises(IndexingError):
            await dedup(store, processor)

        store.failing_reads.clear()
        store.records["twin"].update(indexingStatus=COMPLETED, extractionStatus=COMPLETED)
        decision = await dedup(store, processor)
        assert decision.skip_indexing is True
        assert store.records["dup"]["indexingStatus"] == COMPLETED, "the redelivery reuses the finished twin"

    async def test_a_twin_deleted_meanwhile_leaves_the_duplicate_to_index_itself(self, store, processor) -> None:
        add_twin(store, processor, NOT_STARTED, indexingStatus=IN_PROGRESS)
        store.before_write[("dup", QUEUED)] = lambda: store.records.pop("twin")

        decision = await dedup(store, processor)

        assert decision.skip_indexing is False


class TestTheTwinChangesBeforeTheReRead:
    async def test_a_twin_whose_content_changed_is_not_copied(self, store, processor) -> None:
        add_twin(store, processor, NOT_STARTED, indexingStatus=IN_PROGRESS)
        store.before_write[("dup", QUEUED)] = lambda: store.records["twin"].update(
            md5Checksum="md5-of-an-edited-file", indexingStatus=COMPLETED, extractionStatus=COMPLETED,
            virtualRecordId="vr-edited",
        )

        decision = await dedup(store, processor)

        assert decision.skip_indexing is False, "indexed itself rather than borrowing another file's identity"
        assert store.records["dup"].get("virtualRecordId") != "vr-edited"
        assert store.copied_relationships == []

    async def test_a_twin_moved_to_the_trash_is_not_copied(self, store, processor) -> None:
        add_twin(store, processor, NOT_STARTED, indexingStatus=IN_PROGRESS)
        store.before_write[("dup", QUEUED)] = lambda: store.records["twin"].update(
            indexingStatus=COMPLETED, extractionStatus=COMPLETED, virtualRecordId="vr-twin", isDeleted=True,
        )

        decision = await dedup(store, processor)

        assert decision.skip_indexing is False, "its vectors are being removed with it"
        assert store.copied_relationships == []

    async def test_a_twin_that_failed_first_leaves_the_duplicate_to_index_itself(self, store, processor) -> None:
        add_twin(store, processor, NOT_STARTED, indexingStatus=IN_PROGRESS)

        def twin_fails_and_propagates() -> None:
            store.records["twin"].update(indexingStatus=FAILED, extractionStatus=FAILED)
            assert store.promote_queued_duplicates("twin", FAILED) == 0, "the duplicate is not QUEUED yet"

        store.before_write[("dup", QUEUED)] = twin_fails_and_propagates
        store.add("dup", indexingStatus=NOT_STARTED, extractionStatus=NOT_STARTED)

        decision = await dedup(store, processor)

        assert decision.skip_indexing is False

    async def test_a_twin_waiting_on_its_retry_still_holds_the_duplicate(self, store, processor) -> None:
        add_twin(store, processor, NOT_STARTED, indexingStatus=IN_PROGRESS)
        store.before_write[("dup", QUEUED)] = lambda: store.records["twin"].update(indexingStatus=QUEUED)

        decision = await dedup(store, processor)

        assert decision.skip_indexing is True
        assert store.records["dup"]["indexingStatus"] == QUEUED


class TestTwinFinishesWhileTheDuplicateIsQueued:
    async def test_a_twin_finishing_between_the_check_and_the_queued_write_is_not_missed(
        self, store, processor
    ) -> None:
        add_twin(store, processor, NOT_STARTED, indexingStatus=IN_PROGRESS)

        def twin_finishes_and_promotes() -> None:
            store.records["twin"].update(indexingStatus=COMPLETED, extractionStatus=COMPLETED)
            assert store.promote_queued_duplicates("twin", COMPLETED) == 0, "the duplicate is not QUEUED yet"

        store.before_write[("dup", QUEUED)] = twin_finishes_and_promotes
        store.add("dup", indexingStatus=NOT_STARTED, extractionStatus=NOT_STARTED)

        decision = await dedup(store, processor)

        assert decision.skip_indexing is True
        assert store.records["dup"]["indexingStatus"] == COMPLETED
        assert store.records["dup"]["extractionStatus"] == COMPLETED
        assert store.copied_relationships == [("twin", "dup")]


def sink_over(store: RecordsStore) -> SinkOrchestrator:
    return SinkOrchestrator(
        graphdb=AsyncMock(), blob_storage=AsyncMock(), vector_store=AsyncMock(),
        graph_provider=store, logger=MagicMock(), config_service=MagicMock(),
    )


def twin_being_indexed(store: RecordsStore, processor: EventProcessor) -> MagicMock:
    store.add("twin", **{
        "md5Checksum": md5_of(processor),
        "indexingStatus": IN_PROGRESS,
        "extractionStatus": NOT_STARTED,
        "processingStartedAt": get_epoch_timestamp_in_ms(),
    })
    ctx = MagicMock()
    ctx.record.id = "twin"
    ctx.record.virtual_record_id = "vr-twin"
    return ctx


class TestTheIntervalBetweenIndexingAndEnrichment:
    async def test_a_duplicate_arriving_then_gets_the_twins_later_enrichment(self, store, processor) -> None:
        ctx = twin_being_indexed(store, processor)
        ctx.settings = {"enrichment_follows": True}
        await sink_over(store)._update_indexing_status(ctx)

        await dedup(store, processor)

        store.records["twin"].update(extractionStatus=COMPLETED, processingStartedAt=None)
        store.promote_queued_duplicates("twin", COMPLETED)
        assert store.records["dup"]["indexingStatus"] == COMPLETED
        assert store.records["dup"]["extractionStatus"] == COMPLETED

    async def test_deferred_enrichment_is_copied_as_not_started_straight_away(self, store, processor) -> None:
        ctx = twin_being_indexed(store, processor)
        ctx.settings = {}
        await sink_over(store)._update_indexing_status(ctx)

        decision = await dedup(store, processor)

        assert decision.skip_indexing is True
        assert store.records["dup"]["indexingStatus"] == COMPLETED
        assert store.records["dup"]["extractionStatus"] == NOT_STARTED
