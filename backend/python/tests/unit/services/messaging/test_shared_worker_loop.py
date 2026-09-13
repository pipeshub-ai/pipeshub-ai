"""Indexing consumers sharing one worker loop, on both brokers."""

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.services.distributed.interface import IDistributedLeaseManager
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.consumer_concurrency import StageAdmission
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.worker_loop import WorkerLoop

_LOGGER = logging.getLogger("shared-worker-loop-test")
_STAGE = StageAdmission(stage="classify", limit=2)


Consumer = IndexingRedisStreamsConsumer | IndexingKafkaConsumer


def _redis(
    worker: WorkerLoop | None, stage: StageAdmission | None = None, manager: IDistributedLeaseManager | None = None
) -> IndexingRedisStreamsConsumer:
    config = RedisStreamsConfig(host="h", port=6379, group_id="g", topics=["t"], batch_size=10)
    return IndexingRedisStreamsConsumer(_LOGGER, config, concurrency_manager=manager, stage_admission=stage, worker=worker)


def _kafka(
    worker: WorkerLoop | None, stage: StageAdmission | None = None, manager: IDistributedLeaseManager | None = None
) -> IndexingKafkaConsumer:
    config = KafkaConsumerConfig(
        topics=["t"], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["b:9092"],
    )
    return IndexingKafkaConsumer(_LOGGER, config, concurrency_manager=manager, stage_admission=stage, worker=worker)


def _start(consumer: Consumer) -> None:
    if isinstance(consumer, IndexingKafkaConsumer):
        consumer._IndexingKafkaConsumer__start_worker_thread()
    else:
        consumer._start_worker_thread()
    assert consumer.worker_loop_ready.wait(10)


def _stop(consumer: Consumer) -> None:
    if isinstance(consumer, IndexingKafkaConsumer):
        consumer._IndexingKafkaConsumer__stop_worker_thread()
    else:
        consumer._stop_worker_thread()


Build = Callable[..., Consumer]
BROKERS = pytest.mark.parametrize("build", [_redis, _kafka], ids=["redis", "kafka"])


@BROKERS
def test_consumers_given_one_worker_run_on_its_loop(build: Build) -> None:
    worker = WorkerLoop(_LOGGER)
    record, stage = build(worker), build(worker, _STAGE)
    try:
        _start(record)
        _start(stage)
        assert record.worker_loop is stage.worker_loop is worker.loop
        # One loop, separate admission: the stage keeps its own permits.
        assert stage.indexing_semaphore is not record.indexing_semaphore
    finally:
        _stop(stage)
        _stop(record)
        worker.stop()


@BROKERS
def test_stopping_one_consumer_leaves_the_loop_serving_the_other(build: Build) -> None:
    worker = WorkerLoop(_LOGGER)
    manager = cast(IDistributedLeaseManager, AsyncMock())
    first, second = build(worker, manager=manager), build(worker, _STAGE, manager=manager)
    try:
        _start(first)
        _start(second)
        assert first.lease_renewer is not None
        _stop(first)
        assert first.worker_loop is None and first.lease_renewer is None
        assert second.lease_renewer is not None

        async def ping() -> str:
            return "ok"

        assert asyncio.run_coroutine_threadsafe(ping(), second.worker_loop).result(timeout=5) == "ok"
    finally:
        _stop(second)
        worker.stop()


@BROKERS
def test_without_a_worker_a_consumer_runs_a_loop_of_its_own(build: Build) -> None:
    consumer = build(None)
    try:
        _start(consumer)
        assert consumer.worker_loop is not None and consumer.worker_loop.is_running()
    finally:
        _stop(consumer)


@BROKERS
def test_stopping_waits_for_the_lease_renewer_to_stop(build: Build, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared resources close, and a restart starts a new renewer, only after the old one stopped."""
    worker = WorkerLoop(_LOGGER)
    consumer = build(worker, manager=cast(IDistributedLeaseManager, AsyncMock()))
    stopped = threading.Event()
    try:
        _start(consumer)
        renewer = consumer.lease_renewer
        assert renewer is not None

        async def slow_stop() -> None:
            await asyncio.sleep(0.2)
            stopped.set()

        monkeypatch.setattr(renewer, "stop", slow_stop)
        _stop(consumer)
        assert stopped.is_set()
        assert consumer.lease_renewer is None
    finally:
        worker.stop()


def test_run_awaits_a_coroutine_on_the_loop() -> None:
    worker = WorkerLoop(_LOGGER)
    loop = worker.start()
    try:

        async def which_loop() -> asyncio.AbstractEventLoop:
            return asyncio.get_running_loop()

        assert worker.run(which_loop(), timeout=5) is loop
    finally:
        worker.stop()


def test_run_without_a_running_loop_raises_and_closes_the_coroutine() -> None:
    async def never() -> None:
        return None

    coro = never()
    with pytest.raises(RuntimeError):
        WorkerLoop(_LOGGER).run(coro)
    assert coro.cr_frame is None  # closed, so it is never reported as "never awaited"
