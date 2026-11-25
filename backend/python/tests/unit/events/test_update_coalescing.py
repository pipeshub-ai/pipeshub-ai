"""An update that carries the bytes a record is already indexed from does no parse and no embed.

``EventProcessor.on_event`` decides this after it has the content revision (a digest of the bytes) and before
it writes anything: the record is COMPLETED at that very revision with an indexed copy, so the work would
reproduce the same points. Instead the vector membership is synced, the stages after embed are re-dispatched
without force (so a claim keeps their pause budget), and the delivery is remembered in the replay set.
Code and SQL kinds, forced reindexes, vector-store rebuilds and named-stage re-runs are never skipped.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes, MimeTypes
from app.core import org_context
from app.events.dedup import DedupDecision
from app.events.events import EventProcessor
from app.modules.indexing import replay_set
from app.modules.indexing.connector_off_events import (
    SAME_BYTES_GIVE_UP_LABEL,
    SameBytesSkipPending,
)
from app.modules.pipeline.fingerprint import content_revision
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.consumer_concurrency import (
    RequeueWithoutAttempt,
    backstop_give_up_reason,
)
from app.services.messaging.error_classifier import is_expected_outcome
from tests.unit.attempt_start import attempt_start_via

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
        # A revision write goes through the store's own conditional statements (the count rule), not ``update_node``.
        graph.write_attempt_start = attempt_start_via(graph)
        graph.update_node_fields_if_match = AsyncMock(side_effect=self._write_if_match)
        graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
        self.graph = graph
        processor = MagicMock()
        processor.indexing_pipeline = AsyncMock()
        processor.indexing_pipeline.orgs_holding_points = AsyncMock(return_value={"org-1"})  # the record's points are in the store
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

    async def _write_if_match(self, _key: str, _collection: str, fields: dict[str, Any], match: dict[str, Any]) -> bool:
        if any(self.state.get(name) != value for name, value in match.items()):
            return False
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
        with org_context.for_org("org-1", job="test"):  # the handler runs the event bound to the record's org
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
    world = _World(node={"reason": "an earlier failure", "contentRev": content_revision(NEW), "parsingStatus": "COMPLETED"})
    world.state["md5Checksum"] = _md5(world, NEW)
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
    world.graph.update_node_fields_if_match.assert_not_awaited()
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
async def test_a_record_whose_points_are_gone_runs_in_full() -> None:
    """After a cleanup or reset of the index the record still says COMPLETED: skipping would leave it with no vectors."""
    world = _World(node={"contentRev": content_revision(NEW)})
    world.processor.indexing_pipeline.orgs_holding_points = AsyncMock(return_value=set())

    await world.run(world.event(NEW))

    assert world.parses == 1
    world.processor.indexing_pipeline.sync_vector_membership.assert_not_awaited()
    world.ingress.redrive_unchanged.assert_not_awaited()


@pytest.mark.asyncio
async def test_points_held_only_by_another_org_do_not_count() -> None:
    world = _World(node={"contentRev": content_revision(NEW)})
    world.processor.indexing_pipeline.orgs_holding_points = AsyncMock(return_value={"org-2"})

    await world.run(world.event(NEW))

    assert world.parses == 1


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
async def test_a_persistent_sync_failure_never_re_indexes_the_record() -> None:
    """Many same-bytes updates while the vector store is down: each is handed back, the record is never written and
    nothing is parsed or embedded."""
    world = _World(node={"contentRev": content_revision(NEW)})
    world.processor.indexing_pipeline.sync_vector_membership = AsyncMock(side_effect=RuntimeError("vector store down"))
    before = dict(world.state)

    for _ in range(25):
        with pytest.raises(SameBytesSkipPending):
            await world.run(world.event(NEW))

    assert world.parses == 0
    world.ingress.redrive_unchanged.assert_not_awaited()
    world.graph.update_node.assert_not_awaited()
    assert world.state == before


@pytest.mark.asyncio
async def test_a_transient_sync_failure_heals_on_the_next_delivery() -> None:
    world = _World(node={"contentRev": content_revision(NEW)})
    sync = AsyncMock(side_effect=[RuntimeError("blip"), None])
    world.processor.indexing_pipeline.sync_vector_membership = sync

    with pytest.raises(SameBytesSkipPending):
        await world.run(world.event(NEW))
    events = await world.run(world.event(NEW))

    assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert world.parses == 0 and sync.await_count == 2
    world.ingress.redrive_unchanged.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["handoff_lost", "redrive_unchanged"])
async def test_a_failed_state_read_or_redrive_is_handed_back_not_failed(failing: str) -> None:
    world = _World(node={"contentRev": content_revision(NEW), "extractionStatus": "QUEUED"})
    setattr(world.ingress, failing, AsyncMock(side_effect=ConnectionError("graph down")))

    with pytest.raises(SameBytesSkipPending):
        await world.run(world.event(NEW))

    assert world.parses == 0
    world.graph.update_node.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_a_hand_back_is_bounded_by_the_backstop_and_delayed() -> None:
    """The retries of a skip that cannot finish end in the consumer's delivery backstop (counted hand-backs) and are
    spaced by a delay, so a store that stays down is neither endless nor a busy loop."""
    world = _World(node={"contentRev": content_revision(NEW)})
    world.processor.indexing_pipeline.sync_vector_membership = AsyncMock(side_effect=RuntimeError("down"))

    with pytest.raises(SameBytesSkipPending) as raised:
        await world.run(world.event(NEW))

    assert raised.value.counts_toward_backstop is True
    assert raised.value.delay_s and raised.value.delay_s >= 1.0
    assert is_expected_outcome(raised.value), "an expected hand-back is logged once, not as an error"


@pytest.mark.asyncio
async def test_a_type_change_queued_behind_an_update_of_the_same_bytes_is_skipped_known_limit() -> None:
    """Documents a limit: the stored record and the event agree on the new type, and nothing stored says which parser
    produced the indexed copy, so the skip cannot see that a type change landed while an earlier update was queued.
    A forced reindex repairs it. This is the behaviour with ``RECORD_PARSER_IDENTITY_WRITE`` off (the default); with it on the
    skip compares the parser that built the copy (``test_same_bytes_parser_identity.py``)."""
    world = _World(node={"contentRev": content_revision(NEW), "mimeType": "text/markdown", "recordName": "notes.md"})

    events = await world.run(world.event(NEW, mime="text/markdown", ext="md"))

    assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert world.parses == 0


def test_the_give_up_reason_names_a_same_bytes_hand_back_and_only_that() -> None:
    named = backstop_give_up_reason(SameBytesSkipPending("r1: x"), 11, 10)
    plain = backstop_give_up_reason(RequeueWithoutAttempt("wait"), 11, 10)

    assert SAME_BYTES_GIVE_UP_LABEL in named and "11" in named and "10" in named
    assert SAME_BYTES_GIVE_UP_LABEL not in plain and "without being admitted" in plain


def _md5(world: _World, content: bytes) -> str:
    return world.ep._hash_for_dedup(content, world.state["recordType"], world.state["mimeType"])


@pytest.mark.asyncio
async def test_a_skip_restores_the_parsing_status_and_checksum_a_re_save_reset() -> None:
    """A Collection file replaced with the same bytes is saved again with NOT_STARTED and no checksum; the skip runs no
    parse, so it puts both back, or the record reads as never parsed and drops out of duplicate detection."""
    world = _World(node={"contentRev": content_revision(NEW), "parsingStatus": "NOT_STARTED", "md5Checksum": None})

    events = await world.run(world.event(NEW))

    assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert world.parses == 0
    assert world.state["parsingStatus"] == "COMPLETED"
    assert world.state["md5Checksum"] == _md5(world, NEW)
    assert world.state["indexingStatus"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_skip_writes_only_the_fields_that_differ() -> None:
    world = _World(node={"contentRev": content_revision(NEW), "parsingStatus": "COMPLETED", "md5Checksum": None})

    await world.run(world.event(NEW))

    world.graph.update_node_fields_if_match.assert_awaited_once()
    _key, _coll, fields, match = world.graph.update_node_fields_if_match.await_args.args
    assert fields == {"md5Checksum": _md5(world, NEW)}
    assert match == {"contentRev": content_revision(NEW), "indexingStatus": "COMPLETED"}


@pytest.mark.asyncio
async def test_a_skip_of_a_record_already_current_writes_nothing() -> None:
    world = _World(node={"contentRev": content_revision(NEW), "parsingStatus": "COMPLETED"})
    world.state["md5Checksum"] = _md5(world, NEW)

    await world.run(world.event(NEW))

    world.graph.update_node_fields_if_match.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("moved", [{"contentRev": "another"}, {"indexingStatus": "IN_PROGRESS"}], ids=["revision", "status"])
async def test_a_record_that_moved_on_keeps_its_own_values(moved: dict[str, Any]) -> None:
    """A run of other bytes that started after the skip read the record owns its parsing status and checksum."""
    world = _World(node={"contentRev": content_revision(NEW), "parsingStatus": "NOT_STARTED", "md5Checksum": None})
    read = dict(world.state)
    world.state.update(moved)

    await world.ep._restore_indexed_fields(read, "rec-1", NEW, content_revision(NEW))

    assert world.state["parsingStatus"] == "NOT_STARTED"
    assert world.state["md5Checksum"] is None


@pytest.mark.asyncio
async def test_a_failed_restore_still_settles_the_update() -> None:
    world = _World(node={"contentRev": content_revision(NEW), "parsingStatus": "NOT_STARTED"})
    world.graph.update_node_fields_if_match = AsyncMock(side_effect=RuntimeError("graph down"))
    world.ep.logger = MagicMock()

    events = await world.run(world.event(NEW))

    assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert world.parses == 0
    world.ep.logger.warning.assert_any_call(
        "Record %s: could not restore the parsing status and checksum after a same-bytes skip: %s", "rec-1", ANY)


@pytest.mark.asyncio
async def test_a_skip_of_an_html_record_stores_the_checksum_of_the_normalised_content() -> None:
    """The same hash as a full run's `_check_duplicate_by_md5`, so duplicate detection keeps matching the record."""
    html = b"<p local-id='1'>Same text</p>"
    world = _World(node={"recordType": "CONFLUENCE_PAGE", "mimeType": MimeTypes.HTML.value,
                         "contentRev": content_revision(html), "parsingStatus": "COMPLETED"})

    await world.ep._restore_indexed_fields(dict(world.state), "rec-1", html, content_revision(html))

    assert world.state["md5Checksum"] == _md5(world, html) != hashlib.md5(html).hexdigest()
