"""Records whose extraction left text unread are extracted again from their
stored blocks, in the background, without blocking indexing."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import named_entity_completion as completion
from app.modules.named_entities.domain.models import (
    ExtractionStats,
    NamedEntityExtraction,
)
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

LOG = logging.getLogger("test")


def _graph(rows: list[dict[str, Any]], record: dict[str, Any] | None = None) -> MagicMock:
    graph = MagicMock()
    graph.get_due_named_entity_persist_retries = AsyncMock(return_value=rows)
    graph.clear_named_entity_persist_retry = AsyncMock(return_value=True)
    graph.schedule_named_entity_persist_retry = AsyncMock(return_value=1)
    graph.get_document = AsyncMock(return_value=record)
    graph.stage_state_compare_and_set = AsyncMock(return_value=True)
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


_RECORD = {"_key": "r1", "id": "r1", "orgId": "o1", "virtualRecordId": "v1", "contentRev": "rev-a"}


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


async def test_a_written_completion_replaces_the_kept_extraction() -> None:
    """A later reprojection (a persist retry, a re-embed) must write the completed entities,
    not the extraction that left text unread."""
    extraction = NamedEntityExtraction(status="COMPLETED", strategy="single_call", termination_reason="single_call")
    graph = _graph([], _RECORD)
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", _context):
        assert await completion._complete_one(graph, _sink(_BLOB, extraction), None, "r1", "o1", 100, 0) == "written"

    key, expected, new, fields = graph.stage_state_compare_and_set.await_args.args
    assert (key, expected, new) == ("v1:rev-a:entities", "COMPLETED", "COMPLETED")
    assert NamedEntityExtraction.model_validate_json(fields["output"]).termination_reason == "single_call"


@pytest.mark.parametrize("outcome", ["kept", "failed", "disabled"])
async def test_nothing_is_kept_unless_the_completion_was_written(outcome: str) -> None:
    extraction = NamedEntityExtraction(status="COMPLETED", strategy="single_call", termination_reason="single_call")
    graph, sink = _graph([], _RECORD), _sink(_BLOB, extraction)
    sink.persist_named_entities = AsyncMock(return_value=outcome)
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", _context):
        assert await completion._complete_one(graph, sink, None, "r1", "o1", 100, 0) == outcome
    graph.stage_state_compare_and_set.assert_not_awaited()


async def test_a_failed_keep_does_not_undo_the_written_completion() -> None:
    extraction = NamedEntityExtraction(status="COMPLETED", strategy="single_call", termination_reason="single_call")
    graph = _graph([], _RECORD)
    graph.stage_state_compare_and_set = AsyncMock(side_effect=ConnectionError("graph down"))
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", _context):
        assert await completion._complete_one(graph, _sink(_BLOB, extraction), None, "r1", "o1", 100, 0) == "written"


@pytest.mark.parametrize("keep", [AsyncMock(return_value=False), AsyncMock(side_effect=ConnectionError("graph down"))])
async def test_a_completion_that_is_not_kept_is_not_queued_again(keep: AsyncMock) -> None:
    """A record indexed before the stage has no state to keep it in: queuing it here would
    extract it again on every pass. The re-projection that writes the older extraction
    queues it instead."""
    extraction = NamedEntityExtraction(status="COMPLETED", strategy="single_call", termination_reason="single_call")
    graph = _graph([], _RECORD)
    graph.stage_state_compare_and_set = keep
    graph.schedule_named_entity_reextract = AsyncMock()
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", _context):
        assert await completion._complete_one(graph, _sink(_BLOB, extraction), None, "r1", "o1", 100, 0) == "written"
    graph.schedule_named_entity_reextract.assert_not_called()


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


class _Queue:
    """Re-extraction jobs shared by replicas, recording who drains them and
    whether two passes ever overlap."""

    def __init__(self, jobs: int, *, fail_first: bool = False) -> None:
        self.left = jobs
        self.fail_next = fail_first
        self.active = 0
        self.overlapped = False
        self.drained_by: list[str] = []
        self.stopped_on_loss = 0
        self.on_pass: Callable[[], Awaitable[None]] | None = None

    async def drain(self, **kwargs: Any) -> int:  # noqa: ANN401
        self.active += 1
        self.overlapped |= self.active > 1
        try:
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("graph unavailable")
            written = 0
            for _ in range(completion.PAGE_SIZE):
                await asyncio.sleep(0)
                if self.on_pass is not None:
                    await self.on_pass()
                if not kwargs["still_leader"]():
                    self.stopped_on_loss += 1
                    break
                if not self.left:
                    break
                self.left -= 1
                written += 1
                self.drained_by.append(asyncio.current_task().get_name())
            return written
        finally:
            self.active -= 1


async def _run_replicas(
    queue: _Queue, provider: FakeRedisConnectionProvider | None = None, *, replicas: int = 2, ticks: int = 12,
) -> None:
    """Each replica runs the real loop and lease over one Redis; the loop's
    waits are reduced to a yield, and each replica stops after ``ticks``."""
    provider = provider or FakeRedisConnectionProvider()
    real_sleep = asyncio.sleep
    ticked: dict[str, int] = {}

    async def sleep(seconds: float) -> None:
        if seconds == completion.DEFAULT_INTERVAL_SECONDS:
            name = asyncio.current_task().get_name()
            ticked[name] = ticked.get(name, 0) + 1
            if ticked[name] >= ticks:
                raise asyncio.CancelledError
        await real_sleep(0)

    container = SimpleNamespace(
        logger=lambda: LOG,
        event_processor=lambda: SimpleNamespace(sink_orchestrator=MagicMock()),
    )
    # The loop reads its interval from the environment; ticks are counted at that one.
    interval = {"NAMED_ENTITY_COMPLETION_INTERVAL_SECONDS": str(completion.DEFAULT_INTERVAL_SECONDS)}
    with patch.dict(os.environ, interval), \
            patch("app.services.redis.connection_provider_factory.get_redis_provider", return_value=provider), \
            patch.object(completion.MessagingUtils, "_get_redis_config", AsyncMock(return_value=MagicMock())), \
            patch.object(completion, "complete_named_entities", queue.drain), \
            patch.object(completion.asyncio, "sleep", sleep):
        tasks = [
            asyncio.create_task(completion.run_named_entity_completion_loop(container, MagicMock()), name=f"r{i}")
            for i in range(replicas)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(r, asyncio.CancelledError) for r in results)


async def test_replicas_drain_every_job_one_at_a_time() -> None:
    queue = _Queue(jobs=20)
    await _run_replicas(queue, replicas=3)
    assert queue.left == 0
    assert not queue.overlapped
    assert len(set(queue.drained_by)) == 1
    assert _holder_changes(queue.drained_by) == 0


def _holder_changes(drained_by: list[str]) -> int:
    return sum(a != b for a, b in zip(drained_by, drained_by[1:]))


async def test_after_the_lease_expires_one_replica_takes_it_and_drains() -> None:
    provider = FakeRedisConnectionProvider()
    queue = _Queue(jobs=20)
    expired = False

    async def expire_mid_pass() -> None:
        nonlocal expired
        if not expired and len(queue.drained_by) == 2:
            expired = True
            await provider.get_client().delete(completion.LEADER_KEY)

    queue.on_pass = expire_mid_pass
    await _run_replicas(queue, provider, ticks=20)
    assert expired
    assert queue.stopped_on_loss >= 1
    assert queue.left == 0
    # The replica that lost it stops at its next record and never drains again.
    assert _holder_changes(queue.drained_by) <= 1


async def test_a_replica_that_lost_the_lease_takes_it_again_once_it_is_free() -> None:
    provider = FakeRedisConnectionProvider()
    queue = _Queue(jobs=12)
    other_held = False

    async def taken_then_freed() -> None:
        nonlocal other_held
        client = provider.get_client()
        if not other_held and len(queue.drained_by) == 2:
            other_held = True
            await client.set(completion.LEADER_KEY, "another-replica")
        elif other_held and await client.get(completion.LEADER_KEY) == "another-replica":
            await client.delete(completion.LEADER_KEY)

    queue.on_pass = taken_then_freed
    await _run_replicas(queue, provider, replicas=1, ticks=20)
    assert other_held
    assert queue.stopped_on_loss == 1
    assert queue.left == 0
    assert await provider.get_client().get(completion.LEADER_KEY) is None


async def test_the_loop_keeps_draining_after_a_pass_raises() -> None:
    queue = _Queue(jobs=8, fail_first=True)
    await _run_replicas(queue)
    assert queue.left == 0
    assert not queue.overlapped
