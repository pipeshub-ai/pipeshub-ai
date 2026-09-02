"""Fair scheduling must not weaken the pipeline's admission controls.

Reordering *which* record runs next is the whole feature; changing *how many*
run, or which tier's budget they draw on, is not. These pin the three
properties most at risk from the read/dispatch split, lanes, and parallel
dispatch:

  1. dispatch still stops on every backpressure signal;
  2. heavy and light records still draw on their own index budgets;
  3. reading N lanes does not multiply any limit -- the gates are per
     process, not per lane.
"""
from __future__ import annotations

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


def _fair(**overrides) -> FairSchedulerConfig:
    base = dict(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        default_quantum=1,
        max_buffered_messages=500,
        max_per_entity_messages=500,
        max_dwell_seconds=900.0,
    )
    base.update(overrides)
    return FairSchedulerConfig(**base)


def _kafka_message(offset: int, connector_id: str = "c1", extension: str = "txt"):
    payload = {
        "recordId": f"r-{offset}",
        "orgId": "org-1",
        "connectorId": connector_id,
        "extension": extension,
        "mimeType": "application/pdf" if extension == "pdf" else "text/plain",
    }
    envelope = {"eventType": "newRecord", "payload": payload}
    msg = MagicMock()
    msg.topic = "record-events"
    msg.partition = 0
    msg.offset = offset
    msg.value = json.dumps(envelope).encode()
    return msg


def _redis_fields(index: int, connector_id: str = "c1"):
    envelope = {
        "eventType": "newRecord",
        "payload": {
            "recordId": f"r-{index}",
            "orgId": "org-1",
            "connectorId": connector_id,
            "extension": "txt",
            "mimeType": "text/plain",
        },
    }
    return {"value": json.dumps(envelope)}


@pytest.fixture
def logger():
    return logging.getLogger("test_fair_scheduling_limits")


def _kafka_consumer(logger, **fair_overrides):
    consumer = IndexingKafkaConsumer(
        logger,
        KafkaConsumerConfig(
            topics=["record-events"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
        ),
        fair_scheduler_config=_fair(**fair_overrides),
    )
    consumer.consumer = MagicMock()
    consumer.consumer.commit = AsyncMock()
    consumer.running = True
    # The real __start_processing_task registers a GateWaiterToken
    # synchronously, which is what bounds a dispatch pass. Model that.
    started: list = []
    mock = AsyncMock(side_effect=lambda *a, **k: started.append(1))
    consumer._IndexingKafkaConsumer__start_processing_task = mock
    consumer._get_gate_waiter_count = lambda: len(started)
    return consumer


def _redis_consumer(logger, topics=("record-events",), **fair_overrides):
    consumer = IndexingRedisStreamsConsumer(
        logger,
        RedisStreamsConfig(
            host="h", port=6379, group_id="g", topics=list(topics), batch_size=10
        ),
        fair_scheduler_config=_fair(**fair_overrides),
    )
    consumer.redis = AsyncMock()
    consumer.running = True
    started: list = []
    mock = AsyncMock(side_effect=lambda *a, **k: started.append(1))
    consumer._start_processing_task = mock
    consumer._get_gate_waiter_count = lambda: len(started)
    return consumer


class TestDispatchStopsOnEveryBackpressureSignal:
    async def test_kafka_stops_at_the_pending_task_ceiling(self, logger):
        consumer = _kafka_consumer(logger, parallel_partitions=True)
        tp = TopicPartition("record-events", 0)
        for offset in range(50):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _kafka_message(offset, connector_id=f"c{offset}")
            )
        ceiling = concurrency.pending_task_ceiling(consumer)

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        started = consumer._IndexingKafkaConsumer__start_processing_task
        assert started.await_count == ceiling, (
            f"dispatched {started.await_count} with a ceiling of {ceiling}"
        )


    async def test_redis_stops_at_the_pending_task_ceiling(self, logger):
        consumer = _redis_consumer(logger)
        for index in range(50):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"{index}-0", _redis_fields(index, f"c{index}")
            )
        ceiling = concurrency.pending_task_ceiling(consumer)

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        started = consumer._start_processing_task
        assert started.await_count == ceiling

    async def test_kafka_stops_when_downstream_signals_429(self, logger):
        consumer = _kafka_consumer(logger)
        tp = TopicPartition("record-events", 0)
        for offset in range(5):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _kafka_message(offset, connector_id=f"c{offset}")
            )
        consumer.backpressure_coordinator = MagicMock()
        consumer.backpressure_coordinator.is_paused = MagicMock(return_value=True)

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        consumer._IndexingKafkaConsumer__start_processing_task.assert_not_awaited()

    async def test_redis_stops_when_downstream_signals_429(self, logger):
        """A dispatch pass can start many records, so a 429 arriving part-way
        through has to stop the rest of them, not just the next poll."""
        consumer = _redis_consumer(logger)
        for index in range(5):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"{index}-0", _redis_fields(index, f"c{index}")
            )
        consumer.backpressure_coordinator = MagicMock()
        consumer.backpressure_coordinator.is_paused = MagicMock(return_value=True)

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        started = consumer._start_processing_task
        started.assert_not_awaited()

    async def test_kafka_stops_when_both_index_pools_are_saturated(self, logger):
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor()
        tp = TopicPartition("record-events", 0)
        for offset in range(5):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _kafka_message(offset, connector_id=f"c{offset}")
            )
        # Fill both index pools so no permit could be granted.
        for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT):
            gate = consumer.governor.gate(pool)
            for _ in range(gate.limit):
                assert await gate.acquire()
        assert concurrency.index_gates_saturated(consumer)

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        consumer._IndexingKafkaConsumer__start_processing_task.assert_not_awaited()

    async def test_dispatch_continues_while_one_pool_still_has_room(self, logger):
        """Saturation means *both* tiers are full. A light record must still
        be admitted while only the heavy pool is exhausted."""
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor()
        tp = TopicPartition("record-events", 0)
        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp, _kafka_message(0, extension="txt")
        )
        heavy = consumer.governor.gate(Pool.INDEX_HEAVY)
        for _ in range(heavy.limit):
            assert await heavy.acquire()
        assert not concurrency.index_gates_saturated(consumer)

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        consumer._IndexingKafkaConsumer__start_processing_task.assert_awaited_once()


class TestTierRoutingIsUnchanged:
    """The index permit is taken by tier, resolved from the record event's own
    extension/mimeType before the handler runs. Fair scheduling reorders which
    record goes next; it must not change which budget that record draws on."""

    def test_heavy_and_light_extensions_route_to_different_pools(self, logger):
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor()
        from app.services.resource_governor import classify

        assert concurrency.index_pool(
            concurrency.effective_index_tier(consumer, classify("pdf", "application/pdf"))
        ) is Pool.INDEX_HEAVY
        assert concurrency.index_pool(
            concurrency.effective_index_tier(consumer, classify("txt", "text/plain"))
        ) is Pool.INDEX_LIGHT

    def test_unclassifiable_records_draw_on_the_heavy_budget(self, logger):
        """classify resolves the unknown to HEAVY so an unrecognised record
        takes the smaller budget rather than the one sized for fast records."""
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor()
        from app.services.resource_governor import classify

        assert concurrency.index_pool(
            concurrency.effective_index_tier(consumer, classify("", ""))
        ) is Pool.INDEX_HEAVY

    def test_parse_pools_stay_distinct_from_index_pools(self, logger):
        consumer = _kafka_consumer(logger)
        governor = make_test_governor()
        consumer.governor = governor
        pools = {
            concurrency.index_pool(ParseTier.HEAVY),
            concurrency.index_pool(ParseTier.LIGHT),
            Pool.HEAVY_PARSE,
            Pool.LIGHT_PARSE,
        }
        assert len(pools) == 4
        # Parse ceilings are sized independently of the index ceilings.
        assert concurrency.parse_ceiling(consumer, ParseTier.HEAVY) == governor.ceilings.heavy
        assert concurrency.parse_ceiling(consumer, ParseTier.LIGHT) == governor.ceilings.light


class TestLanesDoNotMultiplyLimits:
    """Lanes are a routing device. One consumer process reads all of them and
    shares one set of gates, so N lanes must not mean N times the budget."""

    def test_one_gate_object_per_pool_regardless_of_lane_count(self, logger):
        governor = make_test_governor()
        many_lanes = [f"record-events.{i}" for i in range(8)]
        consumer = _redis_consumer(logger, topics=["record-events", *many_lanes])
        consumer.governor = governor

        gates = {pool: governor.gate(pool) for pool in Pool}
        again = {pool: governor.gate(pool) for pool in Pool}
        assert all(gates[pool] is again[pool] for pool in Pool)
        assert len(consumer.config.topics) == 9

    async def test_total_dispatch_is_capped_by_the_ceiling_not_by_lane_count(
        self, logger
    ):
        """Records spread over eight lane streams, all buffered together --
        the ceiling still applies once, across all of them."""
        many_lanes = [f"record-events.{i}" for i in range(8)]
        consumer = _redis_consumer(logger, topics=many_lanes)
        ceiling = concurrency.pending_task_ceiling(consumer)
        for index in range(ceiling * 4):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                many_lanes[index % 8], f"{index}-0", _redis_fields(index, f"c{index}")
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        started = consumer._start_processing_task
        assert started.await_count == ceiling, (
            f"{started.await_count} dispatched across 8 lanes with ceiling {ceiling}"
        )

    async def test_buffer_budget_is_shared_across_lanes_not_per_lane(self, logger):
        """max_buffered_messages bounds this consumer's memory, so it has to
        be a total, not an allowance each lane gets."""
        many_lanes = [f"record-events.{i}" for i in range(4)]
        consumer = _redis_consumer(
            logger, topics=many_lanes, max_buffered_messages=10
        )
        for index in range(40):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                many_lanes[index % 4], f"{index}-0", _redis_fields(index, f"c{index}")
            )

        held = consumer._scheduler.pending_count + len(consumer._deferred_entries)
        assert held <= 10, f"buffered {held} against a total budget of 10"

    async def test_kafka_parked_messages_count_against_the_same_budget(
        self, logger
    ):
        consumer = _kafka_consumer(
            logger, max_buffered_messages=10, max_per_entity_messages=2
        )
        tp = TopicPartition("record-events", 0)
        for offset in range(40):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _kafka_message(offset, connector_id="busy")
            )

        held = (
            consumer._scheduler.pending_count + len(consumer._deferred_messages)
        )
        assert held <= 10, f"held {held} against a total budget of 10"
