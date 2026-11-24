"""A connector whose dispatched records have not reached the index gate yet
still counts as waiting, so another connector cannot flood the gate ahead of
them.

Replays a measured run. Connector A's records each had an earlier unfinished
attempt, so they take a one-at-a-time lock before the gate and one is held
there for as long as its predecessor runs; they were dispatched first and
were no longer buffered in the scheduler. Connector B then arrived with 30
heavy records. Counting only buffered records, A looked idle: B had no cap,
dispatched up to the read-ahead ceiling and filled the FIFO gate queue ahead
of A's next record.

The dispatch loop, the dispatch bookkeeping, the real wrapper (suspect lock,
gate, admission mark) and the real governor gate all run; only the handler
and the worker-loop hop are replaced.
"""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import Future
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.resource_governor import ResourceGovernor

LOGGER = logging.getLogger("test-slot-share-pre-gate")
GATE = 4  # heavy index slots
CAP = 2  # ceil(GATE / 2): B's share while A is waiting
HEAVY_RECORDS = 30
# Well above the cap; the read-ahead ceiling stops a connector short of all 30.
UNCAPPED_AT_LEAST = 4 * CAP

_FAIR = FairSchedulerConfig(
    enabled=True,
    key_fields=("orgId", "connectorId"),
    default_quantum=1,
    max_buffered_messages=2000,
    max_per_entity_messages=500,
    max_dwell_seconds=900.0,
    parallel_partitions=True,
)


@pytest.fixture(autouse=True)
def _environment(monkeypatch) -> None:
    monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "100")
    # Heavy parse starts are paced 2 s apart by default; this is about the gate.
    monkeypatch.setattr(
        "app.services.resource_governor.controller.start_rate_limiter_params",
        lambda _ceiling: (0.001, 100),
    )

    async def unfinished(_host, work_key: str) -> int:
        # Every record of connector A has an unfinished earlier attempt.
        return 1 if work_key.startswith("a-") else 0

    monkeypatch.setattr(concurrency, "unfinished_attempts", unfinished)


class _Run:
    """The records' handlers: each is admitted, then holds its slot until the
    test lets it go."""

    def __init__(self) -> None:
        self.admitted: list[str] = []
        self.release: dict[str, asyncio.Event] = {}
        self.tasks: list[asyncio.Task] = []

    async def handler(self, message) -> AsyncIterator[PipelineEvent]:
        record_id = message.payload["recordId"]
        self.admitted.append(record_id)
        await self.release.setdefault(record_id, asyncio.Event()).wait()
        yield PipelineEvent(
            event=IndexingEvent.INDEXING_COMPLETE,
            data=PipelineEventData(record_id=record_id),
        )

    def let_go(self, record_id: str) -> None:
        self.release.setdefault(record_id, asyncio.Event()).set()

    def spawn(self, coro, _loop) -> Future:
        """Stands in for the hop to the worker loop: run the real wrapper on
        this loop and complete a future when it ends."""
        future: Future = Future()
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)

        def done(t: asyncio.Task) -> None:
            if t.cancelled():
                future.cancel()
                future.set_running_or_notify_cancel()
            elif t.exception() is not None:
                future.set_exception(t.exception())
            else:
                future.set_result(t.result())

        task.add_done_callback(done)
        return future

    async def cancel_all(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def _governor() -> ResourceGovernor:
    governor = make_test_governor(logger_name="test-slot-share-pre-gate", env_parse=8)
    governor._registry.set(Pool.HEAVY_PARSE, 8)
    governor._registry.set(Pool.INDEX_HEAVY, GATE)
    return governor


def _event(record_id: str) -> dict:
    return {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id,
            "orgId": "org-1",
            "connectorId": record_id.split("-")[0],
            "extension": "pdf",
            "mimeType": "application/pdf",
        },
        "requestId": record_id,
    }


class _Redis:
    kind = "redis"

    def __init__(self, run: _Run) -> None:
        self.consumer = IndexingRedisStreamsConsumer(
            LOGGER,
            RedisStreamsConfig(host="h", port=6379, client_id="c", group_id="g", topics=["t"]),
            retry_manager=None,
            producer=None,
            governor=_governor(),
            fair_scheduler_config=_FAIR,
        )
        c = self.consumer
        c.running = True
        c.worker_loop = MagicMock()
        c.message_handler = run.handler
        c._pending_message_is_owned = AsyncMock(return_value=True)
        c._ack_message = AsyncMock()
        c._requeue_message = AsyncMock()
        self._n = 0

    async def enqueue(self, record_id: str) -> None:
        self._n += 1
        await self.consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "t", f"{self._n}-0", {"value": json.dumps(_event(record_id))}
        )

    async def dispatch(self) -> None:
        await self.consumer._IndexingRedisStreamsConsumer__dispatch_phase()


class _Kafka:
    kind = "kafka"

    def __init__(self, run: _Run) -> None:
        self.consumer = IndexingKafkaConsumer(
            LOGGER,
            KafkaConsumerConfig(
                topics=["t"], client_id="c", group_id="g", bootstrap_servers=["k:9092"],
                auto_offset_reset="earliest", enable_auto_commit=False,
            ),
            retry_manager=None,
            producer=None,
            governor=_governor(),
            fair_scheduler_config=_FAIR,
        )
        c = self.consumer
        c.running = True
        c.worker_loop = MagicMock()
        c.message_handler = run.handler
        c.consumer = MagicMock()
        c.consumer.commit = AsyncMock()
        c._IndexingKafkaConsumer__resolve_offset = AsyncMock()
        c._IndexingKafkaConsumer__commit_if_appropriate = AsyncMock()
        self._offset = 0

    async def enqueue(self, record_id: str) -> None:
        message = MagicMock()
        message.topic, message.partition, message.offset = "t", 0, self._offset
        self._offset += 1
        message.value = json.dumps(_event(record_id)).encode()
        await self.consumer._IndexingKafkaConsumer__enqueue_message(
            TopicPartition("t", 0), message
        )

    async def dispatch(self) -> None:
        await self.consumer._IndexingKafkaConsumer__dispatch_phase()


async def _settle(side, rounds: int = 12) -> None:
    for _ in range(rounds):
        await side.dispatch()
        await asyncio.sleep(0.01)


@pytest.fixture(params=[_Redis, _Kafka], ids=["redis", "kafka"])
async def world(request, monkeypatch) -> AsyncIterator[tuple[_Redis | _Kafka, _Run]]:
    run = _Run()
    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", run.spawn)
    side = request.param(run)
    for pool in Pool:
        side.consumer.governor.gate(pool)
    yield side, run
    await run.cancel_all()


def _b_in_flight(side) -> int:
    return side.consumer._slot_share.in_flight(("org-1", "b"), "index_heavy")


async def test_the_busy_connector_is_held_to_its_share_and_the_waiting_one_gets_in(world) -> None:
    side, run = world
    # A's three records, dispatched first. The first takes the suspect lock
    # and a slot; the other two wait for the lock, before the gate.
    for i in range(3):
        await side.enqueue(f"a-{i}")
    await _settle(side)
    assert run.admitted == ["a-0"]
    share = side.consumer._slot_share
    assert share.unadmitted(("org-1", "a"), "index_heavy") == 2

    for i in range(HEAVY_RECORDS):
        await side.enqueue(f"b-{i}")
    await _settle(side)

    # B is held to its share while A has records ahead of the gate...
    assert _b_in_flight(side) == CAP
    assert run.admitted == ["a-0", "b-0", "b-1"]

    # ...so when A's first record finishes, its next one gets a slot at once
    # rather than queueing behind B's read-ahead.
    run.let_go("a-0")
    await _settle(side)
    assert "a-1" in run.admitted

    # B's completions keep A moving: a-2 is admitted within one B completion.
    run.let_go("a-1")
    await _settle(side)
    if "a-2" not in run.admitted:
        run.let_go("b-0")
        await _settle(side)
    assert "a-2" in run.admitted


async def test_a_connector_alone_still_dispatches_everything(world) -> None:
    side, run = world
    for i in range(HEAVY_RECORDS):
        await side.enqueue(f"b-{i}")
    await _settle(side)
    assert _b_in_flight(side) >= UNCAPPED_AT_LEAST


async def test_a_record_stops_counting_once_it_is_admitted(world) -> None:
    side, run = world
    await side.enqueue("a-0")
    await _settle(side)
    assert run.admitted == ["a-0"]
    assert side.consumer._slot_share.unadmitted(("org-1", "a"), "index_heavy") == 0
    for i in range(HEAVY_RECORDS):
        await side.enqueue(f"b-{i}")
    await _settle(side)
    # A holds a slot but nothing of A is waiting: no cap on B.
    assert _b_in_flight(side) >= UNCAPPED_AT_LEAST
