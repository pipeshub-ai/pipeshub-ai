"""An update that carries the bytes a record is already indexed from does no parse and no embed.

``EventProcessor.on_event`` decides this after it has the content revision (a digest of the bytes) and before
it writes anything: the record is COMPLETED at that very revision with an indexed copy, so the work would
reproduce the same points. Instead the vector membership is synced, the stages after embed are re-dispatched
without force (so a claim keeps their pause budget), and the delivery is remembered in the replay set.
Code and SQL kinds, forced reindexes, vector-store rebuilds and named-stage re-runs are never skipped.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes, MimeTypes
from app.events.dedup import DedupDecision
from app.events.events import EventProcessor
from app.modules.indexing import replay_set
from app.modules.pipeline.fingerprint import content_revision
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

OLD = b"a,b\n1,2\n"
NEW = b"a,b\n1,3\n"
VRID = "vr-1"
UPDATE = EventTypes.UPDATE_RECORD.value


class _World:
    """One record in a fake graph, a parser that counts its runs and re-indexes the record like the sink does."""

    def __init__(self, *, node: dict[str, Any] | None = None, redrive_result: list[str] | None = None) -> None:
        self.state: dict[str, Any] = {
            "_key": "rec-1", "recordType": "FILE", "orgId": "org-1", "origin": "UPLOAD",
            "mimeType": MimeTypes.CSV.value, "indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED",
            "hasIndexedCopy": True, "virtualRecordId": VRID, "contentRev": content_revision(OLD),
            "lastIndexTimestamp": 1_700_000_000_000,
        }
        self.state.update(node or {})
        self.parses = 0
        graph = AsyncMock()
        graph.get_document = AsyncMock(side_effect=lambda *_a, **_k: dict(self.state))
        graph.update_node = AsyncMock(side_effect=self._write)
        graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
        self.graph = graph
        processor = MagicMock()
        processor.indexing_pipeline = AsyncMock()
        processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="stored/path")
        processor.process_delimited_document = MagicMock(side_effect=self._parse)
        processor.process_code_document = MagicMock(side_effect=self._parse)
        self.processor = processor
        self.ingress = MagicMock()
        self.ingress.handoff_lost = AsyncMock(return_value=False)
        self.ingress.redrive_unchanged = AsyncMock(return_value=["j1"] if redrive_result is None else redrive_result)
        self.ingress.redrive = AsyncMock()
        self.ep = EventProcessor(
            logging.getLogger("test"), processor, graph, MagicMock(), stage_ingress=self.ingress
        )
        self.ep._check_duplicate_by_md5 = AsyncMock(return_value=DedupDecision())
        self.ep._spawn_abandoned_vrid_cleanup = MagicMock()
        self.ep._rewrite_or_delete_vrid_vectors = AsyncMock()

    async def _write(self, _key: str, _collection: str, fields: dict[str, Any]) -> bool:
        self.state.update(fields)
        return True

    async def _parse(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[PipelineEvent]:  # noqa: ANN401
        self.parses += 1
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
        # What the sink's COMPLETED write leaves behind.
        self.state.update({"indexingStatus": "COMPLETED", "hasIndexedCopy": True, "lastIndexTimestamp": 1_700_000_001_000})
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

    def event(
        self, buffer: bytes, *, event_type: str = UPDATE, mime: str = MimeTypes.CSV.value, ext: str = "csv", **extra: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        payload = {
            "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": VRID, "version": 1, "connectorName": "",
            "extension": ext, "mimeType": mime, "recordName": "t.csv", "buffer": buffer, **extra,
        }
        return {"eventType": event_type, "payload": payload}

    async def run(self, event: dict[str, Any]) -> list[IndexingEvent]:
        return [item.event async for item in self.ep.on_event(event)]


@pytest.mark.asyncio
@pytest.mark.parametrize("k", [2, 5, 20])
async def test_k_queued_updates_parse_once(k: int) -> None:
    """The first update of new bytes parses and indexes them; the k-1 queued behind it with the same bytes do not."""
    world = _World()
    for _ in range(k):
        events = await world.run(world.event(NEW))
        assert events[-1] is IndexingEvent.INDEXING_COMPLETE

    assert world.parses == 1
    assert world.state["contentRev"] == content_revision(NEW)
    assert world.processor.indexing_pipeline.sync_vector_membership.await_count == k - 1
    assert world.ingress.redrive_unchanged.await_count == k - 1


@pytest.mark.asyncio
async def test_same_bytes_update_side_effects() -> None:
    world = _World(node={"reason": "an earlier failure", "contentRev": content_revision(NEW)})
    noted: list[tuple[str, int]] = []

    async def remember(record_id: str, completed_at_ms: int) -> None:
        noted.append((record_id, completed_at_ms))

    replay_set.begin_delivery(replay_set.DeliveryRef(event_id="e1", delivery_id=None, flags="", remember=remember))
    try:
        events = await world.run(world.event(NEW))
    finally:
        replay_set.end_delivery()

    assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert world.parses == 0
    world.processor.indexing_pipeline.sync_vector_membership.assert_awaited_once_with(VRID)
    # Unforced: only the redrive that keeps a stage's pause budget. A forced one is not used.
    world.ingress.redrive_unchanged.assert_awaited_once_with(VRID, content_revision(NEW), world.state)
    world.ingress.redrive.assert_not_awaited()
    # Nothing of the record is written: statuses and `reason` stay as they were.
    world.graph.update_node.assert_not_awaited()
    world.graph.compare_and_set_record_fields.assert_not_awaited()
    assert world.state["reason"] == "an earlier failure"
    world.ep._rewrite_or_delete_vrid_vectors.assert_not_awaited()
    world.ep._spawn_abandoned_vrid_cleanup.assert_not_called()
    assert noted == [("rec-1", 1_700_000_000_000)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra", "node"),
    [
        ({"forceReindex": True}, {}),
        ({"vectorDbOnly": True}, {}),
        ({"stages": ["classify"]}, {}),
        ({}, {"indexingStatus": "FAILED"}),
        ({}, {"indexingStatus": "IN_PROGRESS"}),
        ({}, {"hasIndexedCopy": False}),
        ({}, {"hasIndexedCopy": None}),
        ({}, {"extractionStatus": "IN_PROGRESS"}),
        ({}, {"contentRev": "another"}),
        ({}, {"contentRev": None}),
        ({}, {"virtualRecordId": None}),
    ],
    ids=lambda v: str(v) if v else "-",
)
async def test_updates_that_need_work_still_parse(extra: dict[str, Any], node: dict[str, Any]) -> None:
    world = _World(node={"contentRev": content_revision(NEW), **node})
    await world.run(world.event(NEW, **extra))

    assert world.parses == 1
    world.ingress.redrive_unchanged.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_new_record_event_for_a_completed_record_is_not_this_skip() -> None:
    world = _World(node={"contentRev": content_revision(NEW)})
    await world.run(world.event(NEW, event_type=EventTypes.NEW_RECORD.value))

    world.ingress.redrive_unchanged.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lost_hand_off_runs_in_full() -> None:
    world = _World(node={"contentRev": content_revision(NEW)})
    world.ingress.handoff_lost = AsyncMock(return_value=True)

    await world.run(world.event(NEW))

    assert world.parses == 1
    world.processor.indexing_pipeline.sync_vector_membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_revision_the_runtime_never_saw_runs_in_full() -> None:
    world = _World(node={"contentRev": content_revision(NEW)})
    world.ingress.redrive_unchanged = AsyncMock(return_value=None)

    await world.run(world.event(NEW))

    assert world.parses == 1


@pytest.mark.asyncio
async def test_without_a_stage_runtime_the_update_runs_in_full() -> None:
    world = _World(node={"contentRev": content_revision(NEW)})
    world.ep.stage_ingress = None

    await world.run(world.event(NEW))

    assert world.parses == 1


@pytest.mark.asyncio
async def test_a_failed_membership_sync_runs_the_update_in_full_instead_of_failing_the_record() -> None:
    world = _World(node={"contentRev": content_revision(NEW)})
    world.processor.indexing_pipeline.sync_vector_membership = AsyncMock(side_effect=RuntimeError("vector store down"))

    events = await world.run(world.event(NEW))

    assert events[-1] is IndexingEvent.INDEXING_COMPLETE
    assert world.parses == 1
    world.ingress.redrive_unchanged.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_stage_publish_runs_the_update_in_full_instead_of_failing_the_record() -> None:
    from app.modules.pipeline.coordinator import StagePublishError

    world = _World(node={"contentRev": content_revision(NEW), "extractionStatus": "QUEUED"})
    world.ingress.redrive_unchanged = AsyncMock(side_effect=StagePublishError("job"))

    events = await world.run(world.event(NEW))

    assert events[-1] is IndexingEvent.INDEXING_COMPLETE
    assert world.parses == 1
    assert world.state["indexingStatus"] == "COMPLETED"


@pytest.mark.asyncio
async def test_code_files_are_not_skipped() -> None:
    """Their projection and stored blocks are not the whole content, so the bytes alone prove nothing."""
    world = _World(node={"contentRev": content_revision(NEW), "recordType": "CODE_FILE", "mimeType": "text/x-python"})
    await world.run(world.event(NEW, mime="text/x-python", ext="py"))

    world.ingress.redrive_unchanged.assert_not_awaited()
    world.processor.indexing_pipeline.sync_vector_membership.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_repository_file_read_as_code_is_not_skipped_whatever_its_mime() -> None:
    """A CODE_FILE record of a type no code extension catches (a repository's values.yaml) is still code."""
    world = _World(node={"contentRev": content_revision(NEW), "recordType": "CODE_FILE", "mimeType": "application/x-yaml"})
    await world.run(world.event(NEW, mime="application/x-yaml", ext="yml"))

    world.ingress.redrive_unchanged.assert_not_awaited()
    world.processor.indexing_pipeline.sync_vector_membership.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", [MimeTypes.SQL_TABLE.value, MimeTypes.SQL_VIEW.value])
async def test_sql_tables_and_views_are_not_skipped(mime: str) -> None:
    world = _World(node={"contentRev": content_revision(NEW), "mimeType": mime})
    world.processor.process_sql_structured_data = MagicMock(side_effect=world._parse)
    events = await world.run(world.event(NEW, mime=mime, ext=""))

    assert world.parses == 1 and events[-1] is IndexingEvent.INDEXING_COMPLETE
    world.ingress.redrive_unchanged.assert_not_awaited()
    world.processor.indexing_pipeline.sync_vector_membership.assert_not_awaited()


def test_the_same_bytes_rule_is_a_pure_function_of_the_record_and_the_event() -> None:
    rev = content_revision(NEW)
    record = {
        "indexingStatus": "COMPLETED", "hasIndexedCopy": True, "extractionStatus": "COMPLETED",
        "virtualRecordId": VRID, "contentRev": rev,
    }

    def decide(event_type: str = UPDATE, data: dict[str, Any] | None = None, rec: dict[str, Any] | None = None, **kw: Any) -> bool:  # noqa: ANN401
        flags = {"store_only": False, "is_code": False, "is_sql": False, **kw}
        return EventProcessor._is_same_bytes_update(event_type, data or {}, rec or record, rev, **flags)

    assert decide()
    assert not decide(is_code=True)
    assert not decide(is_sql=True)
    assert not decide(store_only=True)
    assert not decide(event_type=EventTypes.REINDEX_RECORD.value)
    assert not decide(data={"forceReindex": True})
    assert not decide(rec={**record, "contentRev": "x"})
