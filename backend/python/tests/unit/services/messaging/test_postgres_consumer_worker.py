"""The PostgreSQL indexing consumer runs the record handler on the shared worker loop, like the other brokers' indexing consumers."""

import asyncio
import contextlib
import threading
from logging import getLogger
from types import SimpleNamespace

from app.services.messaging.config import (
    ConsumerType,
    IndexingEvent,
    MessageBrokerType,
    RedisStreamsConfig,
)
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.worker_loop import WorkerLoop

LOG = getLogger("pg-consumer-worker")
CFG = RedisStreamsConfig(topics=["t"], group_id="g")


def _parsed() -> SimpleNamespace:
    return SimpleNamespace(payload={}, org_ctx=None, requestId="r")


async def test_the_handler_runs_on_the_worker_loop_and_the_caller_stays_on_its_own() -> None:
    worker = WorkerLoop(LOG, thread_name="test-indexing-worker")
    try:
        seen: list[str] = []

        async def handler(_msg):  # noqa: ANN202
            seen.append(threading.current_thread().name)
            yield SimpleNamespace(event=IndexingEvent.INDEXING_COMPLETE)

        consumer = PgMessagingConsumer(LOG, CFG, indexing=True, worker=worker)
        consumer.message_handler = handler
        await consumer._drive_indexing(_parsed(), "message:1")

        assert seen == ["test-indexing-worker"]
        assert threading.current_thread().name != "test-indexing-worker"
    finally:
        worker.stop()


async def test_cancelling_the_wait_cancels_the_handler_on_the_worker_loop() -> None:
    worker = WorkerLoop(LOG, thread_name="test-indexing-worker")
    try:
        started = threading.Event()
        cancelled = threading.Event()

        async def handler(_msg):  # noqa: ANN202
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            yield SimpleNamespace(event=IndexingEvent.INDEXING_COMPLETE)

        consumer = PgMessagingConsumer(LOG, CFG, indexing=True, worker=worker)
        consumer.message_handler = handler
        task = asyncio.create_task(consumer._drive_indexing(_parsed(), "message:1"))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(cancelled.wait, 5)
    finally:
        worker.stop()


def test_the_factory_hands_the_worker_to_indexing_consumers_only() -> None:
    worker = WorkerLoop(LOG)
    idx = MessagingFactory.create_consumer(
        LOG, CFG, broker_type=MessageBrokerType.POSTGRES, consumer_type=ConsumerType.INDEXING, worker=worker
    )
    simple = MessagingFactory.create_consumer(LOG, CFG, broker_type=MessageBrokerType.POSTGRES, worker=worker)
    assert idx._worker is worker  # type: ignore[attr-defined]
    assert simple._worker is None  # type: ignore[attr-defined]
