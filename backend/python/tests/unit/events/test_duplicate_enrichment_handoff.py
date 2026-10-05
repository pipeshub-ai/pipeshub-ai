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

    def add(self, key: str, **fields: Any) -> None:  # noqa: ANN401
        self.records[key] = {"_key": key, "orgId": ORG, "recordType": "FILE", **fields}

    async def update_node(self, key: str, collection: str, fields: dict[str, Any]) -> bool:
        assert collection == CollectionNames.RECORDS.value
        hook = self.before_write.pop((key, fields.get("indexingStatus")), None)
        if hook is not None:
            hook()
        self.records[key].update(fields)
        return True

    async def get_document(self, key: str, collection: str, **_: object) -> dict[str, Any] | None:
        record = self.records.get(key)
        return dict(record) if record is not None else None

    async def find_duplicate_records(
        self, record_key: str, md5_checksum: str, org_id: str, **_: object
    ) -> list[dict[str, Any]]:
        return [
            dict(r) for k, r in self.records.items()
            if k != record_key and r.get("md5Checksum") == md5_checksum and r.get("orgId") == org_id
        ]

    async def copy_document_relationships(self, source: str, target: str) -> bool:
        self.copied_relationships.append((source, target))
        return True

    def promote_queued_duplicates(self, record_id: str, new_status: str, reason: str | None = None) -> int:
        primary = self.records[record_id]
        queued = [
            r for k, r in self.records.items()
            if k != record_id and r.get("indexingStatus") == QUEUED
            and r.get("md5Checksum") == primary.get("md5Checksum") and r.get("orgId") == primary["orgId"]
        ]
        for record in queued:
            record.update({
                "indexingStatus": new_status,
                "virtualRecordId": primary.get("virtualRecordId"),
                "extractionStatus": promoted_duplicate_extraction_status(new_status, primary),
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
        "lastIndexTimestamp": get_epoch_timestamp_in_ms(),
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
        store.records["twin"]["lastIndexTimestamp"] = 0

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
