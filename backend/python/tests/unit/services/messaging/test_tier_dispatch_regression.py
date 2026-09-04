"""Regression for the throughput collapse in docs/indexing-service.md §5.

A Confluence sync publishes light pages and heavy attachments through one
connector. Heavy records queue for minutes on the INDEX_HEAVY gate; with a
single tier-blind gate-waiter ceiling they filled the whole read-ahead
budget and light records were never dispatched although INDEX_LIGHT sat
idle. This drives the real dispatch phase, real gate-waiter tokens, real
governor gates and ``acquire_index_slot``; only the worker-loop hop and the
handler are stubbed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier, Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

_FAIR = FairSchedulerConfig(
    enabled=True,
    key_fields=("orgId", "connectorId"),
    default_quantum=1,
    max_buffered_messages=2000,
    max_per_entity_messages=500,
    max_dwell_seconds=900.0,
    parallel_partitions=True,
)


def _payload(i: int, *, connector: str, heavy: bool) -> dict:
    return {
        "recordId": f"r-{connector}-{'pdf' if heavy else 'page'}-{i}",
        "orgId": "org-1",
        "connectorId": connector,
        "extension": "pdf" if heavy else "",
        "mimeType": "application/pdf" if heavy else "application/blocks",
    }


class _Pipeline:
    """Stands in for the worker: admits through the real index gate, holds a
    heavy permit for the length of the test (an attachment behind Docling),
    releases a light one at once (a page or an issue)."""

    def __init__(self, consumer) -> None:
        self.consumer = consumer
        self.started = {"heavy": 0, "light": 0}
        self.finished = {"heavy": 0, "light": 0}
        self.tasks: list[asyncio.Task] = []

    def start(self, parsed) -> None:
        token = concurrency.GateWaiterToken(
            self.consumer, concurrency.dispatch_tier(self.consumer, parsed)
        )
        self.started[token.tier.value] += 1

        async def run() -> None:
            admission = await concurrency.acquire_index_slot(self.consumer, token.tier)
            token.admit()
            if token.tier is ParseTier.LIGHT:
                await asyncio.sleep(0.001)
                concurrency.release_admission(admission)
                self.finished["light"] += 1
            else:
                await asyncio.sleep(3600)

        self.tasks.append(asyncio.create_task(run()))

    def cancel(self) -> None:
        for task in self.tasks:
            task.cancel()


def _redis(logger):
    consumer = IndexingRedisStreamsConsumer(
        logger,
        RedisStreamsConfig(host="h", port=6379, group_id="g", topics=["record-events"], batch_size=10),
        governor=make_test_governor(),
        fair_scheduler_config=_FAIR,
    )
    consumer.redis = AsyncMock()
    consumer.running = True
    pipeline = _Pipeline(consumer)
    consumer._start_processing_task = AsyncMock(
        side_effect=lambda stream, mid, fields, parsed=None: pipeline.start(parsed)
    )

    async def enqueue(i: int, *, connector: str, heavy: bool) -> None:
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events",
            f"{connector}-{i}-0",
            {"value": json.dumps({"eventType": "newRecord", "payload": _payload(i, connector=connector, heavy=heavy)})},
        )

    return consumer, pipeline, enqueue, consumer._IndexingRedisStreamsConsumer__dispatch_phase


def _kafka(logger):
    consumer = IndexingKafkaConsumer(
        logger,
        KafkaConsumerConfig(
            topics=["record-events"], client_id="c", group_id="g",
            auto_offset_reset="earliest", enable_auto_commit=False, bootstrap_servers=["b:9092"],
        ),
        governor=make_test_governor(),
        fair_scheduler_config=_FAIR,
    )
    consumer.consumer = MagicMock()
    consumer.consumer.commit = AsyncMock()
    consumer.running = True
    pipeline = _Pipeline(consumer)
    consumer._IndexingKafkaConsumer__start_processing_task = AsyncMock(
        side_effect=lambda message, parsed, in_flight, record_key: pipeline.start(parsed)
    )
    tp = TopicPartition("record-events", 0)
    offsets = iter(range(10_000))

    async def enqueue(i: int, *, connector: str, heavy: bool) -> None:
        message = MagicMock()
        message.topic, message.partition, message.offset = "record-events", 0, next(offsets)
        message.value = json.dumps(
            {"eventType": "newRecord", "payload": _payload(i, connector=connector, heavy=heavy)}
        ).encode()
        await consumer._IndexingKafkaConsumer__enqueue_message(tp, message)

    return consumer, pipeline, enqueue, consumer._IndexingKafkaConsumer__dispatch_phase


@pytest.fixture
def logger():
    return logging.getLogger("test_tier_dispatch_regression")


async def _run(build, logger, *, heavy: int, light: int, light_connector: str):
    consumer, pipeline, enqueue, dispatch = build(logger)
    for pool in Pool:
        consumer.governor.gate(pool)
    for i in range(heavy):
        await enqueue(i, connector="confluence", heavy=True)
    for i in range(light):
        await enqueue(i, connector=light_connector, heavy=False)
    for _ in range(40):
        await dispatch()
        await asyncio.sleep(0.005)
    heavy_gate = consumer.governor.gate(Pool.INDEX_HEAVY)
    light_gate = consumer.governor.gate(Pool.INDEX_LIGHT)
    snapshot = {
        "budget": concurrency.dispatch_budget(consumer).describe(),
        "index_heavy": f"{heavy_gate.in_use}/{heavy_gate.limit}",
        "index_light": f"{light_gate.in_use}/{light_gate.limit}",
        "started": dict(pipeline.started),
        "finished": dict(pipeline.finished),
        "buffered": consumer._scheduler.pending_count,
    }
    pipeline.cancel()
    return consumer, snapshot


@pytest.mark.parametrize("build", [_redis, _kafka], ids=["redis", "kafka"])
async def test_attachment_burst_does_not_stall_pages_of_the_same_connector(build, logger):
    consumer, snap = await _run(build, logger, heavy=150, light=100, light_connector="confluence")
    print(snap)
    assert snap["finished"]["light"] == 100
    assert consumer.gate_waiters.count(ParseTier.HEAVY) <= 8
    assert snap["index_heavy"] == "4/4"          # heavy pool full, as it should be
    assert snap["index_light"] == "0/8"          # and light drained, not stalled
    assert snap["buffered"] == 150 - 4 - 8       # the rest of the attachments wait their turn


@pytest.mark.parametrize("build", [_redis, _kafka], ids=["redis", "kafka"])
async def test_attachment_burst_does_not_stall_another_connector(build, logger):
    consumer, snap = await _run(build, logger, heavy=150, light=100, light_connector="jira")
    assert snap["finished"]["light"] == 100
    assert consumer.gate_waiters.count(ParseTier.HEAVY) <= 8


@pytest.mark.parametrize("build", [_redis, _kafka], ids=["redis", "kafka"])
async def test_light_only_stream_still_flows(build, logger):
    _consumer, snap = await _run(build, logger, heavy=0, light=300, light_connector="jira")
    assert snap["finished"]["light"] == 300
    assert snap["buffered"] == 0
