"""Records whose extraction left text unread are extracted again from their
stored blocks, in the background, without blocking indexing."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import named_entity_completion as completion
from app.modules.named_entities.domain.models import (
    ExtractionStats,
    NamedEntityExtraction,
)
from app.modules.transformers.sink_orchestrator import SinkOrchestrator

LOG = logging.getLogger("test")


def _graph(rows: list[dict[str, Any]], record: dict[str, Any] | None = None) -> MagicMock:
    graph = MagicMock()
    graph.get_due_named_entity_persist_retries = AsyncMock(return_value=rows)
    graph.clear_named_entity_persist_retry = AsyncMock(return_value=True)
    graph.schedule_named_entity_persist_retry = AsyncMock(return_value=1)
    graph.get_document = AsyncMock(return_value=record)
    return graph


def _row(attempts: int = 0) -> dict[str, Any]:
    return {"recordId": "r1", "orgId": "o1", "attempts": attempts, "dueAt": 100}


async def _drain(graph: MagicMock, sink: Any) -> int:  # noqa: ANN401
    return await completion.complete_named_entities(graph_provider=graph, sink=sink, logger=LOG)


async def test_nothing_runs_while_indexing_uses_the_llm_slots() -> None:
    graph = _graph([_row()])
    with patch.object(completion, "llm_mostly_free", return_value=False):
        assert await _drain(graph, MagicMock()) == 0
    graph.get_due_named_entity_persist_retries.assert_not_awaited()


async def test_only_reextraction_jobs_are_read() -> None:
    graph = _graph([])
    with patch.object(completion, "llm_mostly_free", return_value=True):
        await _drain(graph, MagicMock())
    graph.get_due_named_entity_persist_retries.assert_awaited_once_with(completion.PAGE_SIZE, reextract=True)


@pytest.mark.parametrize(("outcome", "cleared", "counted", "written"), [
    ("written", False, False, 1),  # the write dropped the job in its own transaction
    ("superseded", False, False, 0),
    ("failed", False, False, 0),  # the sink queued the write again
    ("extract_failed", False, True, 0),
    ("gone", True, False, 0),
    ("kept", True, False, 0),
    ("disabled", True, False, 0),
])
async def test_each_outcome_ends_or_keeps_the_job(outcome: str, cleared: bool, counted: bool, written: int) -> None:
    graph = _graph([_row()])
    with patch.object(completion, "llm_mostly_free", return_value=True), \
            patch.object(completion, "_complete_one", AsyncMock(return_value=outcome)):
        assert await _drain(graph, MagicMock()) == written
    assert graph.clear_named_entity_persist_retry.await_count == (1 if cleared else 0)
    assert graph.schedule_named_entity_persist_retry.await_count == (1 if counted else 0)


async def test_a_job_that_keeps_failing_is_dropped() -> None:
    graph = _graph([_row(attempts=completion.MAX_REEXTRACT_ATTEMPTS + 1)])
    one = AsyncMock()
    with patch.object(completion, "llm_mostly_free", return_value=True), patch.object(completion, "_complete_one", one):
        await _drain(graph, MagicMock())
    one.assert_not_awaited()
    graph.clear_named_entity_persist_retry.assert_awaited_once_with("r1", 100)


def _sink(blob: dict[str, Any] | None, extraction: NamedEntityExtraction) -> MagicMock:
    sink = MagicMock()
    sink.blob_storage.get_record_from_storage = AsyncMock(return_value=blob)
    sink.extract_named_entities = AsyncMock(return_value=extraction)
    sink.persist_named_entities = AsyncMock(return_value="written")
    return sink


_RECORD = {"_key": "r1", "id": "r1", "orgId": "o1", "virtualRecordId": "v1"}


def _context(record: Any, settings: dict[str, Any]) -> SimpleNamespace:  # noqa: ANN401
    return SimpleNamespace(record=record, settings=settings)
_BLOB = {"block_containers": {"blocks": [{"index": 0, "type": "text", "format": "txt", "data": "Acme hired Bob."}]}}


async def test_the_stored_blocks_are_extracted_again_and_written_under_the_job() -> None:
    extraction = NamedEntityExtraction(status="COMPLETED", strategy="single_call", termination_reason="single_call")
    sink = _sink(_BLOB, extraction)
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", _context):
        outcome = await completion._complete_one(_graph([], _RECORD), sink, None, "r1", "o1", 100, 2)

    assert outcome == "written"
    ctx = sink.extract_named_entities.await_args.args[0]
    assert sink.extract_named_entities.await_args.kwargs == {"reextract": True}
    assert ctx.record.block_containers.blocks[0].data == "Acme hired Bob."
    # The write lands only while this job (due at 100) is still the pending one.
    sink.persist_named_entities.assert_awaited_once_with(ctx, extraction, retry_due=100, retry_attempts=2)


async def test_a_failed_extraction_writes_nothing() -> None:
    sink = _sink(_BLOB, NamedEntityExtraction(status="FAILED", termination_reason="llm_error", strategy="deterministic"))
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", _context):
        assert await completion._complete_one(_graph([], _RECORD), sink, None, "r1", "o1", 100, 0) == "extract_failed"
    sink.persist_named_entities.assert_not_awaited()


@pytest.mark.parametrize(("record", "blob"), [(None, _BLOB), ({"isDeleted": True, "virtualRecordId": "v"}, _BLOB), (_RECORD, {"x": 1})])
async def test_a_record_or_blocks_that_are_gone_end_the_job(record: dict[str, Any] | None, blob: dict[str, Any]) -> None:
    sink = _sink(blob, NamedEntityExtraction())
    assert await completion._complete_one(_graph([], record), sink, None, "r1", "o1", 100, 0) == "gone"
    sink.extract_named_entities.assert_not_awaited()


def _partial(windows_total: int, windows_run: int) -> NamedEntityExtraction:
    return NamedEntityExtraction(
        status="PARTIAL", strategy="single_call", termination_reason="single_call",
        stats=ExtractionStats(windows_total=windows_total, windows_run=windows_run),
    )


@pytest.mark.parametrize(("outcome", "extraction", "queued"), [
    ("written", _partial(7, 5), True),
    ("written", _partial(2, 2), False),
    ("failed", _partial(7, 5), False),
    ("kept", _partial(7, 5), False),
])
async def test_the_sink_queues_a_reextraction_after_writing_unread_text(
    outcome: str, extraction: NamedEntityExtraction, queued: bool,
) -> None:
    sink = SinkOrchestrator.__new__(SinkOrchestrator)
    sink.logger = LOG
    sink.config_service = MagicMock()
    sink.graph_provider = MagicMock()
    sink.graph_provider.schedule_named_entity_reextract = AsyncMock()
    sink.graph_provider.schedule_named_entity_persist_retry = AsyncMock(return_value=1)
    sink.graphdb = MagicMock()
    sink.entity_vector_store = None
    ctx = SimpleNamespace(record=SimpleNamespace(id="r1", org_id="o1"))
    with patch("app.services.featureflag.platform_settings.is_named_entity_extraction_enabled", AsyncMock(return_value=True)), \
            patch("app.modules.named_entities.stage.persist_named_entities", AsyncMock(return_value=outcome)):
        assert await sink.persist_named_entities(ctx, extraction) == outcome
    assert sink.graph_provider.schedule_named_entity_reextract.await_count == (1 if queued else 0)


def test_the_deadline_covers_every_window_using_all_of_its_calls() -> None:
    from app.modules.named_entities.stage import REEXTRACT_MAX_WINDOWS
    from app.modules.named_entities.strategies import single_call
    from app.utils.streaming import MAX_REFLECTION_RETRIES_DEFAULT

    # Strict with its reflection retries, then each lower mode once.
    calls = 1 + MAX_REFLECTION_RETRIES_DEFAULT + len(single_call._MODES) - 1
    assert single_call.WINDOW_TIMEOUT_SECONDS >= calls * single_call._CALL_TIMEOUT
    assert completion.EXTRACT_TIMEOUT_SECONDS >= REEXTRACT_MAX_WINDOWS * single_call.WINDOW_TIMEOUT_SECONDS


async def test_a_pass_stops_before_the_next_record_once_the_lease_is_lost() -> None:
    graph = _graph([_row(), {**_row(), "recordId": "r2"}])
    leader = iter([True, False])
    one = AsyncMock(return_value="written")
    with patch.object(completion, "llm_mostly_free", return_value=True), patch.object(completion, "_complete_one", one):
        written = await completion.complete_named_entities(
            graph_provider=graph, sink=MagicMock(), logger=LOG, still_leader=lambda: next(leader),
        )
    assert written == 1
    assert [c.args[3] for c in one.await_args_list] == ["r1"]


async def test_the_lease_is_refreshed_while_a_pass_runs_and_a_lost_one_is_reported() -> None:
    lock = SimpleNamespace(refresh=AsyncMock(side_effect=[True, True, False]))
    lost = asyncio.Event()
    await asyncio.wait_for(completion._keep_lease(lock, lost, 0.001), 5)
    assert lost.is_set()
    assert lock.refresh.await_count == 3


async def test_a_leader_pass_tells_the_records_when_the_lease_is_lost() -> None:
    seen: list[bool] = []

    async def drain(**kwargs: Any) -> int:  # noqa: ANN401
        await asyncio.sleep(0.01)
        seen.append(kwargs["still_leader"]())
        return 0

    async def lose(lock: Any, lost: asyncio.Event, every: float) -> None:  # noqa: ANN401
        lost.set()

    container = SimpleNamespace(event_processor=lambda: SimpleNamespace(sink_orchestrator=MagicMock()))
    with patch.object(completion, "complete_named_entities", drain), patch.object(completion, "_keep_lease", lose):
        await completion._pass_as_leader(SimpleNamespace(ttl_seconds=60), container, MagicMock(), LOG)
    assert seen == [False]


def test_the_pass_runs_on_the_loop_records_are_indexed_on() -> None:
    """Indexing LLM slots are per event loop: a pass on another loop always sees
    them free and adds its calls on top of indexing's."""
    import threading

    from app.utils.concurrency import (
        MAX_CONCURRENT_INDEXING_LLM_CALLS,
        indexing_llm_slot,
    )
    from app.utils.worker_scaling import scaled

    worker = asyncio.new_event_loop()
    thread = threading.Thread(target=worker.run_forever, daemon=True)
    thread.start()
    held = asyncio.Event()
    release = asyncio.Event()

    async def hold_slots() -> None:
        async with contextlib.AsyncExitStack() as stack:
            for _ in range(scaled(MAX_CONCURRENT_INDEXING_LLM_CALLS)):
                await stack.enter_async_context(indexing_llm_slot())
            held.set()
            await release.wait()

    try:
        holder = asyncio.run_coroutine_threadsafe(hold_slots(), worker)
        asyncio.run_coroutine_threadsafe(held.wait(), worker).result(5)
        consumers = [("record", SimpleNamespace(worker_loop=worker), None)]
        loop = completion.record_consumer_loop(consumers)
        assert loop is worker
        graph = _graph([_row()])
        written = asyncio.run_coroutine_threadsafe(_drain(graph, MagicMock()), loop).result(5)
        assert written == 0
        graph.get_due_named_entity_persist_retries.assert_not_awaited()
        worker.call_soon_threadsafe(release.set)
        holder.result(5)
    finally:
        worker.call_soon_threadsafe(worker.stop)
        thread.join(5)
        worker.close()


def test_no_running_consumer_loop_means_the_pass_runs_where_it_is_started() -> None:
    assert completion.record_consumer_loop([]) is None
    assert completion.record_consumer_loop([("record", SimpleNamespace(worker_loop=None), None)]) is None
