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


async def test_the_handler_logs_under_the_message_request_id_and_org() -> None:
    import json

    from app.services.messaging.postgres.queue import Delivery
    from app.utils.request_context import get_context

    seen: list[tuple[str, str | None]] = []

    async def handler(_msg) -> bool:
        ctx = get_context()
        seen.append((ctx.root_id, ctx.org_id))
        return True

    consumer = PgMessagingConsumer(LOG, CFG)
    consumer.message_handler = handler
    value = json.dumps(
        {"eventType": "e", "payload": {}, "requestId": "req-1", "orgId": "org-1"}
    )
    ok, _, _, _ = await consumer._process(
        Delivery(topic="t", message_id=1, attempts=1, value=value, key=None), final=False
    )

    assert ok
    assert seen == [("req-1", "org-1")]
    assert get_context() is None


async def _process_raising(exc: Exception) -> tuple:
    import json
    from unittest.mock import MagicMock

    from app.services.messaging.postgres.queue import Delivery

    async def handler(_msg) -> bool:
        raise exc

    logger = MagicMock()
    consumer = PgMessagingConsumer(logger, CFG)
    consumer.message_handler = handler
    value = json.dumps({"eventType": "e", "payload": {}, "requestId": "req-1", "orgId": "org-1"})
    result = await consumer._process(
        Delivery(topic="t", message_id=1, attempts=1, value=value, key=None), final=False
    )
    return result, logger


async def test_a_record_deleted_while_indexed_is_not_logged_as_an_error() -> None:
    from app.exceptions.indexing_exceptions import RecordDeletedError

    (ok, terminal, _, _), logger = await _process_raising(RecordDeletedError("rec-1"))

    assert (ok, terminal) == (False, True)
    logger.error.assert_not_called()
    logger.info.assert_called()


async def test_a_record_deleted_while_indexed_is_dead_lettered_without_a_warning() -> None:
    import json
    from unittest.mock import AsyncMock, MagicMock

    from app.exceptions.indexing_exceptions import RecordDeletedError
    from app.services.messaging.postgres.queue import Delivery

    async def handler(_msg) -> bool:
        raise RecordDeletedError("rec-1")

    logger = MagicMock()
    consumer = PgMessagingConsumer(logger, CFG)
    consumer.message_handler = handler
    consumer.queue = MagicMock(dead=AsyncMock(), ack=AsyncMock(), nack=AsyncMock())
    value = json.dumps({"eventType": "e", "payload": {}, "requestId": "req-1", "orgId": "org-1"})

    await consumer._settle(Delivery(topic="t", message_id=1, attempts=1, value=value, key=None))

    consumer.queue.dead.assert_awaited_once()
    logger.warning.assert_not_called()
    logger.error.assert_not_called()


async def test_an_unexpected_failure_is_still_logged_as_an_error(caplog) -> None:
    import json
    import logging

    from app.services.messaging.postgres.queue import Delivery
    from app.utils.logger import log_record_fields

    async def handler(_msg) -> bool:
        raise RuntimeError("boom")

    consumer = PgMessagingConsumer(LOG, CFG)
    consumer.message_handler = handler
    value = json.dumps({"eventType": "e", "payload": {}, "requestId": "req-1", "orgId": "org-1"})
    with caplog.at_level(logging.DEBUG, logger=LOG.name):
        ok, _, _, reason = await consumer._process(
            Delivery(topic="t", message_id=1, attempts=1, value=value, key=None), final=False
        )

    assert not ok
    [line] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert (line.getMessage(), log_record_fields(line)["error_type"]) == ("message.handler_failed", "RuntimeError")
    assert reason == "internal: RuntimeError"


async def _settle_raising(exc: Exception) -> PgMessagingConsumer:
    """A stage consumer whose handler hands the delivery back with ``exc``."""
    import json
    from unittest.mock import AsyncMock, MagicMock

    from app.services.messaging.postgres.queue import Delivery

    async def handler(_msg) -> bool:
        raise exc

    consumer = PgMessagingConsumer(MagicMock(), CFG)
    consumer.message_handler = handler
    consumer.stage_admission = MagicMock()
    consumer.queue = MagicMock(dead=AsyncMock(), ack=AsyncMock(), nack=AsyncMock())
    value = json.dumps({"eventType": "stageJob", "payload": {}, "requestId": "req-1", "orgId": "org-1"})
    await consumer._settle(Delivery(topic="t", message_id=1, attempts=1, value=value, key=None))
    return consumer


async def test_a_job_held_for_a_connector_delete_waits_alone() -> None:
    """One connector being deleted must not stop the stage for every other connector's jobs."""
    from app.modules.pipeline.worker import StageHeldForDelete

    consumer = await _settle_raising(StageHeldForDelete("connector c1 is being deleted", delay_s=60.0))

    assert consumer._paused_until == 0.0
    (_group, _d, delay, _reason), kwargs = consumer.queue.nack.await_args
    assert delay >= 60.0 and kwargs == {"refund": True}


async def test_a_paused_stage_still_stops_claiming() -> None:
    from app.modules.pipeline.worker import StagePaused

    consumer = await _settle_raising(StagePaused("the model provider is down", delay_s=15.0))

    assert consumer._paused_until > 0.0
