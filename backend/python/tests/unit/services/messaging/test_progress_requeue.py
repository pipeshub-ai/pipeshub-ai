"""A record cut after embedding part of it is handed back without counting an attempt.

Each consumer is driven through its real per-message wrapper, with a handler that does what the
vector store does (plans, beats, then stops finishing batches) under a processing timeout of a
fraction of a second. What is observed is what the consumer does with the message: re-queue it with no
attempt counted, or count the failure as it always has.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.indexing.record_progress import current_progress
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    MessagingEnvConfig,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery
from app.services.messaging.worker_loop import WorkerLoop
from tests.unit.services.messaging.test_delivery_identity import (
    _body,
    _kafka,
    _record,
    _redis,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

LOGGER = logging.getLogger("test-progress-requeue")


@pytest.fixture(autouse=True)
def _short_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MessagingEnvConfig, "record_processing_timeout", property(lambda self: 1.5))
    monkeypatch.setattr(MessagingEnvConfig, "embed_stall_floor_seconds", property(lambda self: 0.25))
    monkeypatch.setattr(MessagingEnvConfig, "embed_stall_ceiling_seconds", property(lambda self: 0.25))


class Handler:
    """Embeds ``beats`` batches, then stops finishing them (or raises, or finishes)."""

    def __init__(self, *, beats: int = 2, then: str = "hang", applies: bool = True) -> None:
        self.beats, self.then, self.applies = beats, then, applies
        self.progress: Any = None
        self.writes: list[tuple[float, int]] = []

    async def __call__(self, message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
        self.progress = p = current_progress()
        if p is not None:
            async def commit(seconds: float, uncounted: int) -> bool:
                self.writes.append((seconds, uncounted))
                return self.applies

            p.bind(record_id="r1", delivery_id="d", prior_seconds=0.0, prior_uncounted=0, commit=commit)
            p.plan(10, 0)
            for _ in range(self.beats):
                await asyncio.sleep(0.03)
                p.beat(2)
        if self.then == "inner-timeout":
            raise TimeoutError
        if self.then == "hang":
            await asyncio.sleep(60)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r"))


def _retry_manager() -> AsyncMock:
    rm = AsyncMock()
    rm.increment_and_check.return_value = (1, False)
    rm.unfinished_attempts.return_value = 0
    rm.start_attempt.return_value = 0
    rm.get_retry_count.return_value = 0
    rm.record_delivery.return_value = 1
    return rm


async def _redis_run(handler: Handler) -> tuple[Any, AsyncMock]:
    consumer, _ = _redis()
    consumer.message_handler = handler  # type: ignore[assignment]
    rm = _retry_manager()
    consumer.retry_manager = rm
    await consumer._process_message_wrapper("t", "100-0", {"value": _body("r1")})
    return consumer, rm


async def _kafka_run(handler: Handler) -> tuple[Any, AsyncMock]:
    consumer, _ = _kafka()
    consumer.running = True
    consumer.message_handler = handler  # type: ignore[assignment]
    rm = _retry_manager()
    consumer.retry_manager = rm
    consumer._requeue_message = AsyncMock()  # type: ignore[method-assign]
    wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper  # type: ignore[attr-defined]
    await wrapper(_record(7, _body("r1")))
    return consumer, rm


RUNNERS = pytest.mark.parametrize("run", [_redis_run, _kafka_run], ids=["redis", "kafka"])


def _handed_back(consumer: Any) -> list[Any]:  # noqa: ANN401
    return [c for c in consumer._requeue_message.await_args_list if c.kwargs.get("delay_s") is not None]


@pytest.mark.asyncio
class TestAProductiveCut:
    @RUNNERS
    async def test_it_is_requeued_without_counting_an_attempt(self, run: Any) -> None:  # noqa: ANN401
        # Kills: a counted stall (the record would dead-letter after three cuts); no conversion at all.
        handler = Handler(beats=2)
        consumer, rm = await run(handler)
        (call,) = _handed_back(consumer)
        assert call.kwargs["retry_count"] == 0 and call.kwargs["delay_s"] == 5.0
        rm.increment_and_check.assert_not_awaited()
        assert handler.writes and handler.writes[0][1] == 1  # the node was told, once
        assert len(handler.writes) == 1

    @RUNNERS
    async def test_it_does_not_count_toward_the_delivery_backstop(self, run: Any) -> None:  # noqa: ANN401
        # Kills: a backstop that dead-letters a record for being cut a few times while progressing.
        _consumer, rm = await run(Handler(beats=2))
        rm.record_delivery.assert_not_awaited()

    @RUNNERS
    async def test_it_is_cut_at_the_stall_limit_not_at_the_processing_timeout(
        self, run: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN401
    ) -> None:
        # A budget far above the 0.25 s stall limit, so a loaded runner cannot blur the two.
        monkeypatch.setattr(MessagingEnvConfig, "record_processing_timeout", property(lambda self: 10.0))
        handler = Handler(beats=2)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await run(handler)
        assert loop.time() - started < 5.0

    @RUNNERS
    async def test_a_cut_the_node_refuses_is_counted(self, run: Any) -> None:  # noqa: ANN401
        # Kills: handing back a record whose status write did not apply (another owner has it).
        consumer, rm = await run(Handler(beats=2, applies=False))
        assert _handed_back(consumer) == []
        rm.increment_and_check.assert_awaited()


@pytest.mark.asyncio
class TestWhatStaysCounted:
    @RUNNERS
    async def test_a_cut_with_no_batch_is_counted_as_today(self, run: Any) -> None:  # noqa: ANN401
        # Kills: every cut uncounted.
        consumer, rm = await run(Handler(beats=0))
        assert _handed_back(consumer) == []
        rm.increment_and_check.assert_awaited_once()

    @RUNNERS
    async def test_a_timeout_raised_inside_the_handler_is_counted(self, run: Any) -> None:  # noqa: ANN401
        # Kills: any TimeoutError after a batch read as the attempt's own deadline.
        consumer, rm = await run(Handler(beats=2, then="inner-timeout"))
        assert _handed_back(consumer) == []
        rm.increment_and_check.assert_awaited_once()

    @RUNNERS
    async def test_a_record_that_finishes_is_acked_and_not_requeued(self, run: Any) -> None:  # noqa: ANN401
        consumer, rm = await run(Handler(beats=2, then="finish"))
        assert _handed_back(consumer) == []
        rm.increment_and_check.assert_not_awaited()

    async def test_a_shutdown_in_the_middle_of_the_embedding_is_never_a_requeue_redis(self) -> None:
        # Kills: a cancellation read as a cut. The entry stays in the PEL and nothing is counted.
        consumer, _ = _redis()
        handler = Handler(beats=2)
        consumer.message_handler = handler  # type: ignore[assignment]
        rm = _retry_manager()
        consumer.retry_manager = rm
        task = asyncio.create_task(consumer._process_message_wrapper("t", "100-0", {"value": _body("r1")}))
        await asyncio.sleep(0.12)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _handed_back(consumer) == [] and handler.writes == []
        rm.increment_and_check.assert_not_awaited()

    async def test_a_shutdown_in_the_middle_of_the_embedding_is_never_a_requeue_kafka(self) -> None:
        consumer, _ = _kafka()
        consumer.running = True
        handler = Handler(beats=2)
        consumer.message_handler = handler  # type: ignore[assignment]
        rm = _retry_manager()
        consumer.retry_manager = rm
        consumer._requeue_message = AsyncMock()  # type: ignore[method-assign]
        wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper  # type: ignore[attr-defined]
        task = asyncio.create_task(wrapper(_record(7, _body("r1"))))
        await asyncio.sleep(0.12)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _handed_back(consumer) == [] and handler.writes == []
        rm.increment_and_check.assert_not_awaited()


@pytest.mark.asyncio
class TestTheDecisionHelper:
    async def _cut_progress(self) -> Any:  # noqa: ANN401
        handler = Handler(beats=2)
        consumer, _ = await _redis_run(handler)
        return consumer, handler

    async def test_a_lost_lease_is_never_a_requeue(self) -> None:
        # Kills: a helper that ignores the lease handle.
        consumer, handler = await self._cut_progress()
        handler.writes.clear()
        lease = MagicMock()
        lease.lost.is_set.return_value = True
        await concurrency.requeue_after_productive_cut(consumer, handler.progress, lease, "m")  # counted: returns
        assert handler.writes == []

    async def test_a_stopping_consumer_is_never_a_requeue(self) -> None:
        consumer, handler = await self._cut_progress()
        handler.writes.clear()
        consumer.running = False
        await concurrency.requeue_after_productive_cut(consumer, handler.progress, None, "m")
        assert handler.writes == []

    async def test_an_intact_lease_on_a_running_consumer_raises_the_requeue(self) -> None:
        consumer, handler = await self._cut_progress()
        lease = MagicMock()
        lease.lost.is_set.return_value = False
        consumer.running = True
        with pytest.raises(concurrency.ProgressRequeue) as raised:
            await concurrency.requeue_after_productive_cut(consumer, handler.progress, lease, "m")
        assert raised.value.counts_toward_backstop is False and raised.value.delay_s == 5.0

    async def test_no_progress_object_means_counted(self) -> None:
        consumer, _ = _redis()
        await concurrency.requeue_after_productive_cut(consumer, None, None, "m")


@pytest.mark.asyncio
class TestPostgresKeepsTheFlatCap:
    async def test_its_handler_is_given_no_progress_to_report_to(self) -> None:
        # Kills: wiring the stall timer into a consumer whose timeout lives on another loop.
        seen: list[Any] = []

        async def handler(message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
            seen.append(current_progress())
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r"))

        worker = WorkerLoop(LOGGER, thread_name="test-progress-requeue")
        try:
            consumer = PgMessagingConsumer(LOGGER, RedisStreamsConfig(topics=["t"], group_id="g"), indexing=True, worker=worker)
            consumer.message_handler = handler  # type: ignore[assignment]
            import json

            value = json.dumps({**json.loads(_body("r1")), "requestId": "req", "orgId": "org-1"})
            await consumer._process(Delivery(topic="t", message_id=1, attempts=1, value=value, key=None), final=False)
        finally:
            worker.stop()
        assert seen == [None]
