"""Consumer-side admission explain through the real acquire paths: read pause on both brokers, stage permits, priority pools."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging import consumer_concurrency as cc
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

H, L = ParseTier.HEAVY, ParseTier.LIGHT


def _kafka(**kw: object) -> IndexingKafkaConsumer:
    return IndexingKafkaConsumer(
        logging.getLogger("qa"),
        KafkaConsumerConfig(topics=["t"], client_id="idx", group_id="g", auto_offset_reset="earliest",
                            enable_auto_commit=False, bootstrap_servers=["b:9092"], ssl=False, sasl=None),
        **kw,
    )


def _redis(**kw: object) -> IndexingRedisStreamsConsumer:
    return IndexingRedisStreamsConsumer(
        logging.getLogger("qa"),
        RedisStreamsConfig(host="localhost", port=6379, client_id="idx", group_id="g", topics=["t"]),
        retry_manager=None, **kw,
    )



@pytest.mark.asyncio
async def test_stage_consumer_local_permits_through_the_real_acquire() -> None:
    stage = cc.StageAdmission(stage="summary", limit=2, workers=3)
    consumer = _redis(stage_admission=stage)
    consumer.indexing_semaphore = asyncio.Semaphore(cc.local_index_permits(consumer))
    consumer.concurrency_manager = MagicMock()
    held = [await cc.acquire_index_slot(consumer, H) for _ in range(2)]
    waiters = [asyncio.ensure_future(cc.acquire_index_slot(consumer, H)) for _ in range(3)]
    await asyncio.sleep(0.01)
    ex = consumer.admission_explain()
    assert ex["localPermits"] == {"name": "summary", "limit": 2, "inUse": 2, "waiting": 3}
    assert ex["leases"] == {"stage:summary": {"limit": 6, "waiting": 0}}
    waiters[0].cancel()
    await asyncio.sleep(0)
    assert consumer.admission_explain()["localPermits"]["waiting"] == 2
    cc.release_admission(held[0])
    await asyncio.sleep(0.01)
    ex = consumer.admission_explain()["localPermits"]
    assert ex["inUse"] == 2 and ex["waiting"] == 1
    for a in held[1:]:
        cc.release_admission(a)
    for w in waiters[1:]:
        cc.release_admission(await w)
    ex = consumer.admission_explain()["localPermits"]
    assert ex["inUse"] == 0 and ex["waiting"] == 0
    json.dumps(consumer.admission_explain())


def test_release_admission_twice_does_not_drive_in_use_negative() -> None:
    async def run() -> int:
        consumer = _redis()
        consumer.indexing_semaphore = asyncio.Semaphore(2)
        a = await cc.acquire_index_slot(consumer, H)
        cc.release_admission(a)
        cc.release_admission(a)
        return consumer.admission_explain()["localPermits"]["inUse"]
    assert asyncio.run(run()) == 0


# ---- priority lane caps and pools -------------------------------------------------------

def test_priority_lane_lists_its_three_pools_and_caps() -> None:
    lane = cc.PriorityLane(heavy_limit=2, light_limit=3, workers=2)
    consumer = _redis(governor=make_test_governor(), priority_lane=lane)
    try:
        consumer.concurrency_manager = MagicMock()
        ex = consumer.admission_explain()
        assert set(ex["leases"]) == {"indexing:priority", "parsing:priority", "parsing:light:priority"}
        assert ex["leases"]["indexing:priority"]["limit"] == 10
        assert ex["priority"]["heavy"]["limit"] == 2 and ex["priority"]["light"]["limit"] == 3
        # three heavy records in flight (two at the cap): the cap is what holds the reader back
        for _ in range(2):
            cc.GateWaiterToken(consumer, H).admit()
        assert ex is not None
        assert consumer.admission_explain()["priority"]["heavy"]["inFlight"] == 2
    finally:
        consumer.governor.close()




def _redis_pause_consumer() -> IndexingRedisStreamsConsumer:
    consumer = _redis(governor=make_test_governor(), fair_scheduler_config=FairSchedulerConfig(
        enabled=True, key_fields=("connectorId",), default_quantum=1, max_buffered_messages=100, max_per_entity_messages=50,
        max_dwell_seconds=900.0,
    ))
    for name in ("sweep_stale_buffered",):
        setattr(consumer, f"_IndexingRedisStreamsConsumer__{name}", MagicMock())
    for name in ("drain_deferred", "recover_while_reading", "retry_unhanded_when_due"):
        setattr(consumer, f"_IndexingRedisStreamsConsumer__{name}", AsyncMock())
    return consumer


@pytest.mark.asyncio
async def test_a_read_pause_is_reported_by_both_brokers() -> None:
    """The same public flag, driven through each consumer's own pause logic."""
    kafka = _kafka(governor=make_test_governor())
    redis = _redis_pause_consumer()
    try:
        fake = MagicMock()
        fake.assignment.return_value = {object()}
        fake.paused.return_value = set()
        kafka.consumer = fake
        for consumer in (kafka, redis):
            assert consumer.admission_explain()["readPause"]["admission"] is False
            for _ in range(cc.pending_task_ceiling(consumer) + 1):
                consumer.gate_waiters.add(H)
        kafka._IndexingKafkaConsumer__apply_backpressure()
        assert fake.pause.called, "precondition: Kafka paused its partitions"
        with patch("asyncio.sleep", new=AsyncMock()):
            await redis._IndexingRedisStreamsConsumer__read_phase()
        assert redis._backpressure_active, "precondition: the Redis read loop paused"
        assert kafka.admission_explain()["readPause"]["admission"] is True
        assert redis.admission_explain()["readPause"]["admission"] is True
        for consumer in (kafka, redis):
            for _ in range(cc.pending_task_ceiling(consumer) + 1):
                consumer.gate_waiters.remove(H)
        fake.paused.return_value = {object()}
        kafka._IndexingKafkaConsumer__apply_backpressure()
        with patch("asyncio.sleep", new=AsyncMock()), patch.object(redis, "_IndexingRedisStreamsConsumer__reclaim_while_reading", AsyncMock(), create=True):
            redis._scheduler = MagicMock(pending_count=0)
            redis._IndexingRedisStreamsConsumer__buffer_has_room = MagicMock(return_value=False)
            await redis._IndexingRedisStreamsConsumer__read_phase()
        assert kafka.admission_explain()["readPause"]["admission"] is False
        assert redis.admission_explain()["readPause"]["admission"] is False
    finally:
        kafka.governor.close()
        redis.governor.close()


def test_a_consumer_without_the_probe_reports_no_pause() -> None:
    assert cc.admission_explain(_bare())["readPause"]["admission"] is False


def _bare() -> MagicMock:
    host = MagicMock()
    host.governor = None
    host.stage_admission = None
    host.priority_lane = None
    host.concurrency_manager = None
    host.gate_waiters = cc.GateWaiters()
    host.admission_backlog = cc.AdmissionBacklog()
    host.read_paused_for_admission = None
    host.backpressure_coordinator = None
    host.dispatch_stats = None
    host.local_permits = None
    host.consumer_name = "c"
    return host


def _paused_kafka(coordinator: object | None = None) -> tuple[IndexingKafkaConsumer, MagicMock]:
    kafka = _kafka(governor=make_test_governor(), backpressure_coordinator=coordinator)
    fake = MagicMock()
    fake.assignment.return_value = {object()}
    fake.paused.return_value = set()
    kafka.consumer = fake
    return kafka, fake


def _signalled_coordinator() -> object:
    from app.services.messaging.backpressure import BackpressureCoordinator

    coordinator = BackpressureCoordinator()
    coordinator.signal("ParsingService", 30.0)
    return coordinator


def test_a_downstream_only_pause_is_not_reported_as_an_admission_pause_by_either_broker() -> None:
    kafka, fake = _paused_kafka(_signalled_coordinator())
    redis = _redis(governor=make_test_governor(), backpressure_coordinator=_signalled_coordinator())
    try:
        kafka._IndexingKafkaConsumer__apply_backpressure()
        assert fake.pause.called, "precondition: the downstream signal paused the Kafka partitions"
        for consumer in (kafka, redis):
            pause = consumer.admission_explain()["readPause"]
            assert pause["downstream"] is True and pause["admission"] is False, pause
            assert pause["pausedServices"] == ["ParsingService"]
    finally:
        kafka.governor.close()
        redis.governor.close()


def test_an_admission_pause_during_a_downstream_pause_is_reported_as_both_and_clears_separately() -> None:
    kafka, fake = _paused_kafka(_signalled_coordinator())
    try:
        for _ in range(cc.pending_task_ceiling(kafka) + 1):
            kafka.gate_waiters.add(H)
        kafka._IndexingKafkaConsumer__apply_backpressure()
        pause = kafka.admission_explain()["readPause"]
        assert pause["admission"] is True and pause["downstream"] is True
        for _ in range(cc.pending_task_ceiling(kafka) + 1):
            kafka.gate_waiters.remove(H)
        kafka._IndexingKafkaConsumer__apply_backpressure()
        pause = kafka.admission_explain()["readPause"]
        assert pause["admission"] is False and pause["downstream"] is True
    finally:
        kafka.governor.close()


# ---- the pools the explain lists are the pools the wrappers ask for --------------------------

def _governorless(consumer_cls: str, **kw: object) -> Any:  # noqa: ANN401
    consumer = (_redis if consumer_cls == "redis" else _kafka)(**kw)
    consumer.concurrency_manager = MagicMock()
    return consumer


@pytest.mark.parametrize("kind", ["redis", "kafka"])
def test_a_governorless_consumer_lists_the_one_parse_pool_both_tiers_take(kind: str) -> None:
    consumer = _governorless(kind)
    assert consumer.governor is None
    leases = consumer.admission_explain()["leases"]
    assert "parsing" in leases and "parsing:light" not in leases
    for tier in (H, L):
        assert cc.admission_parse_lease_pool(consumer, tier) == "parsing"


@pytest.mark.parametrize("kind", ["redis", "kafka"])
def test_a_governorless_priority_consumer_lists_the_parse_pool_it_takes(kind: str) -> None:
    lane = cc.PriorityLane(heavy_limit=2, light_limit=3, workers=1)
    consumer = _governorless(kind, priority_lane=lane)
    assert consumer.governor is None
    leases = consumer.admission_explain()["leases"]
    assert "parsing" in leases, leases
    assert cc.admission_parse_lease_pool(consumer, L) == "parsing"


@pytest.mark.parametrize("kind", ["redis", "kafka"])
def test_a_governed_consumer_lists_a_parse_pool_per_tier(kind: str) -> None:
    consumer = (_redis if kind == "redis" else _kafka)(governor=make_test_governor())
    try:
        consumer.concurrency_manager = MagicMock()
        assert {"parsing", "parsing:light"} <= set(consumer.admission_explain()["leases"])
        assert cc.admission_parse_lease_pool(consumer, L) == "parsing:light"
        assert cc.admission_parse_lease_pool(consumer, H) == "parsing"
    finally:
        consumer.governor.close()


@pytest.mark.parametrize("path", [
    "app/services/messaging/redis_streams/indexing_consumer.py",
    "app/services/messaging/kafka/consumer/indexing_consumer.py",
])
def test_the_wrappers_choose_their_parse_pool_through_the_function_the_explain_uses(path: str) -> None:
    """Drift guard: a wrapper that picks the pool by its own condition can disagree with the explain."""
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[4] / path).read_text()
    assert "concurrency.admission_parse_lease_pool(self, parse_tier)" in source
    assert "concurrency.parse_lease_pool(" not in source
