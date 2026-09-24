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
from app.services.resource_governor import DownstreamFeedback
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


def _redis_fields(index: int, connector_id: str = "c1", extension: str = "txt"):
    envelope = {
        "eventType": "newRecord",
        "payload": {
            "recordId": f"r-{index}",
            "orgId": "org-1",
            "connectorId": connector_id,
            "extension": extension,
            "mimeType": "application/pdf" if extension == "pdf" else "text/plain",
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
    # synchronously, which is what bounds a dispatch pass. Model that with
    # a real token in the message's tier, never admitted: every started
    # record stays a waiter, as if its index gate were full.
    consumer._IndexingKafkaConsumer__start_processing_task = _never_admitted(
        consumer, lambda a, k: a[1]
    )
    return consumer


def _never_admitted(consumer, parsed_of):
    """An AsyncMock stand-in for start-processing that leaves a gate-waiter
    token behind, in the tier ``dispatch_tier`` gives the parsed message."""

    def start(*a, **k):
        parsed = parsed_of(a, k)
        concurrency.GateWaiterToken(consumer, concurrency.dispatch_tier(consumer, parsed))

    return AsyncMock(side_effect=start)


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
    consumer._start_processing_task = _never_admitted(
        consumer, lambda a, k: a[3] if len(a) > 3 else k.get("parsed_message")
    )
    return consumer


async def _hold_just_under_the_limits(governor) -> None:
    """Take every light permit and all but one heavy permit, so the gates
    are one admission short of saturated at their *current* limits."""
    heavy = governor.gate(Pool.INDEX_HEAVY)
    for _ in range(heavy.limit - 1):
        assert await heavy.acquire()
    light = governor.gate(Pool.INDEX_LIGHT)
    for _ in range(light.limit):
        assert await light.acquire()


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

    async def test_kafka_stops_dispatching_after_a_downstream_shrink(self, logger) -> None:
        """The governor narrows the index gates when a downstream service
        reports distress; permits already out then fill the narrower gates,
        and the dispatch loop sees them as saturated -- on either broker,
        because both read the same gates."""
        feedback = DownstreamFeedback()
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor(feedback=feedback)
        tp = TopicPartition("record-events", 0)
        for offset in range(5):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _kafka_message(offset, connector_id=f"c{offset}")
            )
        await _hold_just_under_the_limits(consumer.governor)
        assert not concurrency.index_gates_saturated(consumer)

        feedback.report_pool_exhausted("neo4j")
        await consumer.governor._sample_once()

        assert concurrency.index_gates_saturated(consumer)
        await consumer._IndexingKafkaConsumer__dispatch_phase()
        consumer._IndexingKafkaConsumer__start_processing_task.assert_not_awaited()

    async def test_redis_stops_dispatching_after_a_downstream_shrink(self, logger) -> None:
        feedback = DownstreamFeedback()
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor(feedback=feedback)
        for index in range(5):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"{index}-0", _redis_fields(index, f"c{index}")
            )
        await _hold_just_under_the_limits(consumer.governor)
        assert not concurrency.index_gates_saturated(consumer)

        feedback.report_pool_exhausted("neo4j")
        await consumer.governor._sample_once()

        assert concurrency.index_gates_saturated(consumer)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        consumer._start_processing_task.assert_not_awaited()

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


def _fill(consumer, tier: ParseTier, count: int) -> list:
    """`count` spawned tasks of `tier` still queued for their index gate."""
    return [concurrency.GateWaiterToken(consumer, tier) for _ in range(count)]


class TestDispatchIsBoundedPerTier:
    """Heavy waiters park for the length of the heavy-parse queue; they must
    never take the read-ahead that light records turn over in milliseconds.
    The governor here is the 4-CPU test one: index_heavy warm-starts at 4,
    so heavy may queue 2 x 4 = 8 tasks; light is bounded by the total."""

    async def test_redis_heavy_backlog_does_not_starve_light(self, logger):
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor()
        for index in range(100):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"h{index}-0", _redis_fields(index, "confluence", "pdf")
            )
        for index in range(100):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"l{index}-0", _redis_fields(200 + index, "confluence")
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        budget = concurrency.dispatch_budget(consumer)
        assert consumer.gate_waiters.count(ParseTier.HEAVY) == budget.tiers[ParseTier.HEAVY].ceiling == 8
        assert consumer.gate_waiters.count(ParseTier.LIGHT) == 56
        assert consumer.gate_waiters.count() == budget.total_ceiling == 64
        assert consumer._scheduler.pending_count_for(("org-1", "confluence", "light")) == 44

    async def test_kafka_heavy_backlog_does_not_starve_light(self, logger):
        consumer = _kafka_consumer(logger, parallel_partitions=True)
        consumer.governor = make_test_governor()
        tp = TopicPartition("record-events", 0)
        for offset in range(100):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _kafka_message(offset, connector_id="confluence", extension="pdf")
            )
        for offset in range(100, 200):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _kafka_message(offset, connector_id="confluence", extension="txt")
            )

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert consumer.gate_waiters.count(ParseTier.HEAVY) == 8
        assert consumer.gate_waiters.count(ParseTier.LIGHT) == 56

    async def test_light_only_connector_is_not_stalled_by_another_connectors_attachments(self, logger):
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor()
        for index in range(100):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"h{index}-0", _redis_fields(index, "confluence", "pdf")
            )
        for index in range(30):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"j{index}-0", _redis_fields(200 + index, "jira")
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert consumer._scheduler.pending_count_for(("org-1", "jira")) == 0
        assert consumer.gate_waiters.count(ParseTier.LIGHT) == 30
        assert consumer.gate_waiters.count(ParseTier.HEAVY) == 8

    async def test_heavy_waiters_are_capped_even_with_nothing_else_queued(self, logger):
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor()
        for index in range(50):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"{index}-0", _redis_fields(index, "confluence", "pdf")
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert consumer._start_processing_task.await_count == 8

    async def test_heavy_ceiling_tracks_the_governors_current_limit(self, logger):
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor()
        consumer.governor._registry.set(Pool.INDEX_HEAVY, 1)  # braked to the floor
        for index in range(50):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"{index}-0", _redis_fields(index, "confluence", "pdf")
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        # 2 x 1 would be 2; the per-tier floor keeps the gate fed.
        assert consumer._start_processing_task.await_count == 8

    async def test_both_tiers_at_their_ceilings_stops_dispatch(self, logger):
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor()
        tokens = _fill(consumer, ParseTier.HEAVY, 8) + _fill(consumer, ParseTier.LIGHT, 56)
        assert concurrency.dispatch_budget(consumer).blocked
        for index in range(5):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"{index}-0", _redis_fields(index)
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        consumer._start_processing_task.assert_not_awaited()
        for token in tokens:
            token.release()

    async def test_collapsed_light_tier_uses_one_ceiling(self, logger):
        """MAX_CONCURRENT_INDEXING=1: no light budget, every record is heavy,
        and the single tier gets the whole total rather than 2 x 1."""
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor(env_index=1)
        assert consumer.governor.ceilings.index_light == 0
        for index in range(100):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"{index}-0", _redis_fields(index)
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        budget = concurrency.dispatch_budget(consumer)
        assert set(budget.tiers) == {ParseTier.HEAVY}
        assert consumer._start_processing_task.await_count == budget.total_ceiling
        assert consumer.gate_waiters.count(ParseTier.LIGHT) == 0

    async def test_without_a_governor_the_shared_ceiling_is_unchanged(self, logger):
        consumer = _redis_consumer(logger)
        for index in range(100):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"{index}-0", _redis_fields(index, extension="pdf" if index % 2 else "txt")
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert consumer._start_processing_task.await_count == concurrency.pending_task_ceiling(consumer)

    async def test_explicit_pending_cap_is_the_total(self, logger, monkeypatch):
        monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "20")
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor()
        for index in range(100):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"h{index}-0", _redis_fields(index, "confluence", "pdf")
            )
        for index in range(100):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", f"l{index}-0", _redis_fields(200 + index, "confluence")
            )

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert consumer.gate_waiters.count() == 20
        assert consumer.gate_waiters.count(ParseTier.HEAVY) == 8
        assert consumer.gate_waiters.count(ParseTier.LIGHT) == 12


class TestBackpressureSignalsArePerTier:
    async def test_kafka_pauses_partitions_only_when_every_tier_is_blocked(self, logger):
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor()
        tp = TopicPartition("record-events", 0)
        consumer.consumer.assignment.return_value = {tp}
        consumer.consumer.paused.return_value = set()

        tokens = _fill(consumer, ParseTier.HEAVY, 8)
        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.pause.assert_not_called()

        tokens += _fill(consumer, ParseTier.LIGHT, 56)
        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.pause.assert_called_once_with(tp)
        for token in tokens:
            token.release()

    async def test_kafka_backpressure_log_names_each_tier(self, logger, caplog):
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor()
        consumer.consumer.assignment.return_value = {TopicPartition("record-events", 0)}
        consumer.consumer.paused.return_value = set()
        tokens = _fill(consumer, ParseTier.HEAVY, 8) + _fill(consumer, ParseTier.LIGHT, 56)

        with caplog.at_level(logging.WARNING, logger=logger.name):
            consumer._IndexingKafkaConsumer__apply_backpressure()

        assert "heavy 8/8, light 56/64, total 64/64" in caplog.text
        for token in tokens:
            token.release()

    async def test_kafka_finished_partition_resumes_while_a_tier_has_room(self, logger):
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor()
        tokens = _fill(consumer, ParseTier.HEAVY, 8)
        message = _kafka_message(0)
        tp = TopicPartition(message.topic, message.partition)
        consumer._in_flight_partitions.add(tp)

        consumer._IndexingKafkaConsumer__finish_partition(message, retry_current=False)

        consumer.consumer.resume.assert_called_once_with(tp)
        for token in tokens:
            token.release()

    async def test_kafka_lane_is_blocked_by_its_entity_not_by_a_tier_leaf(self, logger):
        consumer = _kafka_consumer(logger, max_per_entity_messages=1)
        tp = TopicPartition("record-events", 0)
        await consumer._IndexingKafkaConsumer__enqueue_message(tp, _kafka_message(0, extension="pdf"))
        outcome, blocked_key = await consumer._IndexingKafkaConsumer__enqueue_message(
            tp, _kafka_message(1, extension="txt")
        )

        assert outcome != "buffered"
        assert blocked_key == ("org-1", "c1")

    async def test_redis_read_phase_backpressure_only_when_every_tier_is_blocked(self, logger):
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor()
        consumer.redis.xreadgroup = AsyncMock(return_value=[])
        tokens = _fill(consumer, ParseTier.HEAVY, 8)

        await consumer._IndexingRedisStreamsConsumer__read_phase()
        assert consumer.redis.xreadgroup.await_count == 1
        assert consumer._backpressure_active is False

        tokens += _fill(consumer, ParseTier.LIGHT, 56)
        await consumer._IndexingRedisStreamsConsumer__read_phase()
        assert consumer.redis.xreadgroup.await_count == 1
        assert consumer._backpressure_active is True
        for token in tokens:
            token.release()

    async def test_redis_pel_recovery_claims_from_the_total_remaining(self, logger):
        """Recovered entries go through the scheduler, so the claim is sized
        by the total, not stopped because one tier is at its ceiling."""
        consumer = _redis_consumer(logger)
        consumer.governor = make_test_governor()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        consumer.redis.xpending_range = AsyncMock(return_value=[])
        tokens = _fill(consumer, ParseTier.HEAVY, 8)

        await consumer._drain_pending()

        kwargs = consumer.redis.xautoclaim.await_args.kwargs
        assert kwargs["count"] == 10  # min(10, 56 remaining, buffer room)
        for token in tokens:
            token.release()


class TestWaitersSurviveNothingPastShutdown:
    async def test_kafka_stop_resets_the_counters(self, logger):
        consumer = _kafka_consumer(logger)
        _fill(consumer, ParseTier.HEAVY, 3)
        consumer._IndexingKafkaConsumer__stop_worker_thread()
        assert consumer.gate_waiters.count() == 0

    async def test_redis_stop_resets_the_counters(self, logger):
        consumer = _redis_consumer(logger)
        _fill(consumer, ParseTier.LIGHT, 3)
        consumer._stop_worker_thread()
        assert consumer.gate_waiters.count() == 0

    async def test_tokens_released_on_cancelled_futures_never_go_negative(self, logger):
        consumer = _redis_consumer(logger)
        tokens = _fill(consumer, ParseTier.HEAVY, 2)
        for token in tokens:
            token.release()
            token.release()
        assert consumer.gate_waiters.snapshot() == {ParseTier.HEAVY: 0, ParseTier.LIGHT: 0}


class TestRevocationCoversBothTiers:
    async def test_kafka_revoked_partition_drops_its_heavy_and_light_leaves(self, logger):
        consumer = _kafka_consumer(logger)
        consumer.governor = make_test_governor()
        tp0 = TopicPartition("record-events", 0)
        tp1 = TopicPartition("record-events", 1)
        for offset, (tp, extension) in enumerate(
            [(tp0, "pdf"), (tp0, "txt"), (tp1, "pdf"), (tp1, "txt")]
        ):
            message = _kafka_message(offset, connector_id="c1", extension=extension)
            message.partition = tp.partition
            await consumer._IndexingKafkaConsumer__enqueue_message(tp, message)
        assert consumer._scheduler.pending_count == 4

        await consumer._on_partitions_revoked([tp0])

        assert consumer._scheduler.pending_count == 2
        assert consumer._scheduler.pending_count_for(("org-1", "c1", "heavy")) == 1
        assert consumer._scheduler.pending_count_for(("org-1", "c1", "light")) == 1
        # Spawned tasks are not touched by revocation: their tokens are
        # released by the futures' done callbacks, never by the rebalance.
        assert consumer.gate_waiters.count() == 0
