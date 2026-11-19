"""The priority lane on Redis Streams: the consumer of ``record-events-priority`` reads past the
backlog, on caps of its own, and obeys the parser's backpressure. Also covers the lane's shared
admission helpers (``PriorityLane`` in ``consumer_concurrency``), which the Kafka consumer uses too."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    REQUIRED_TOPICS,
    ConsumerType,
    MessageBrokerType,
    RedisStreamsConfig,
    Topic,
    compute_retry_backoff_seconds,
)
from app.services.messaging.messaging_factory import MessagingFactory, lane_topics_for
from app.services.messaging.redis_streams.admin import RedisStreamsAdmin
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier, Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

PRIORITY = Topic.RECORD_EVENTS_PRIORITY.value
LANE = concurrency.PriorityLane(heavy_limit=1, light_limit=3)


def _config() -> RedisStreamsConfig:
    return RedisStreamsConfig(
        host="h", port=6379, group_id="priority-group", topics=[PRIORITY], batch_size=10, client_id="priority-client",
    )


def _consumer(*, lane: concurrency.PriorityLane | None = LANE, governor=None, **kwargs) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("test_parse_now_priority"),
        _config(),
        governor=governor,
        priority_lane=lane,
        provider=MagicMock(),
        fair_scheduler_config=FairSchedulerConfig(
            enabled=True, key_fields=("orgId", "connectorId"), default_quantum=1,
            max_buffered_messages=100, max_per_entity_messages=50, max_dwell_seconds=900.0,
        ),
        **kwargs,
    )
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    return consumer


def _fields(org_id: str, record_id: str, *, mime: str = "text/plain", extension: str = "txt") -> dict:
    payload = {
        "recordId": record_id, "orgId": org_id, "connectorId": f"conn-{org_id}", "mimeType": mime,
        "extension": extension, "parseNow": True, "requestedByUserId": "u-1",
    }
    return {"value": json.dumps({"eventType": "reindexRecord", "payload": payload, "requestId": record_id})}


class TestPriorityLane:
    def test_the_caps_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            concurrency.PriorityLane(heavy_limit=0, light_limit=1)
        with pytest.raises(ValueError, match=">= 1"):
            concurrency.PriorityLane(heavy_limit=1, light_limit=1, workers=0)

    def test_it_leases_from_pools_of_its_own_sized_for_every_worker(self) -> None:
        lane = concurrency.PriorityLane(heavy_limit=2, light_limit=4, workers=3)
        host = MagicMock(priority_lane=lane, stage_admission=None, governor=None)

        assert concurrency.admission_lease_pool(host, ParseTier.LIGHT) == "indexing:priority"
        assert concurrency.index_ceiling(host, ParseTier.LIGHT) == 18
        assert concurrency.parse_lease_pool(ParseTier.HEAVY, host) == "parsing:priority"
        assert concurrency.parse_lease_pool(ParseTier.LIGHT, host) == "parsing:light:priority"
        assert concurrency.parse_ceiling(host, ParseTier.HEAVY) == 6
        assert concurrency.parse_ceiling(host, ParseTier.LIGHT) == 12

    def test_a_host_without_a_lane_keeps_the_normal_pools(self) -> None:
        host = MagicMock(priority_lane=None, stage_admission=None, governor=None)

        assert concurrency.admission_lease_pool(host, ParseTier.HEAVY) == "indexing"
        assert concurrency.parse_lease_pool(ParseTier.HEAVY, host) == "parsing"
        assert concurrency.parse_lease_pool(ParseTier.LIGHT) == "parsing:light"

    def test_the_legacy_semaphore_is_the_lanes_total(self) -> None:
        assert concurrency.local_index_permits(_consumer()) == 4


class TestCapsOnRecordsInFlight:
    def test_the_heavy_cap_leaves_light_alone(self) -> None:
        # P3
        consumer = _consumer(governor=make_test_governor())
        token = concurrency.GateWaiterToken(consumer, ParseTier.HEAVY)
        token.admit()  # admitted: the lane still counts it, it is in flight

        budget = consumer._dispatch_budget()
        assert not budget.allows(ParseTier.HEAVY)
        assert budget.allows(ParseTier.LIGHT)

    def test_the_lane_never_runs_more_than_its_total(self) -> None:
        # P2
        consumer = _consumer(governor=make_test_governor())
        tokens = [concurrency.GateWaiterToken(consumer, ParseTier.LIGHT) for _ in range(3)]
        for token in tokens:
            token.admit()

        assert consumer._dispatch_budget().blocked is False  # heavy is still free
        concurrency.GateWaiterToken(consumer, ParseTier.HEAVY)
        assert consumer._dispatch_budget().blocked is True

        tokens[0].release()  # the task ended
        assert consumer._dispatch_budget().allows(ParseTier.LIGHT)

    def test_a_normal_consumer_stops_counting_a_record_once_it_is_admitted(self) -> None:
        consumer = _consumer(lane=None, governor=make_test_governor())
        token = concurrency.GateWaiterToken(consumer, ParseTier.HEAVY)
        token.admit()

        assert consumer.gate_waiters.count() == 0

    def test_its_slot_pools_are_its_own_caps(self) -> None:
        consumer = _consumer(governor=make_test_governor())

        assert concurrency.slot_pool_limit(consumer, Pool.INDEX_HEAVY.value) == 1
        assert concurrency.slot_pool_limit(consumer, Pool.INDEX_LIGHT.value) == 3


@pytest.mark.asyncio
class TestNotHeldBackByTheBacklog:
    async def test_full_index_pools_do_not_stop_the_lane(self) -> None:
        # P1, P2: the normal consumer stops reading when both pools are full; the lane does not.
        governor = make_test_governor()
        normal, priority = _consumer(lane=None, governor=governor), _consumer(governor=governor)
        for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT):
            gate = governor.gate(pool)
            for _ in range(gate.limit):
                await gate.acquire()

        assert concurrency.index_gates_saturated(normal) is True
        assert concurrency.index_gates_saturated(priority) is False

    async def test_a_priority_record_is_admitted_ahead_of_queued_normal_ones(self) -> None:
        governor = make_test_governor()
        normal, priority = _consumer(lane=None, governor=governor), _consumer(governor=governor)
        gate = governor.gate(Pool.INDEX_LIGHT)
        for _ in range(gate.limit):
            await gate.acquire()
        order: list[str] = []

        async def admit(name: str, host: IndexingRedisStreamsConsumer) -> None:
            await concurrency.acquire_index_slot(host, ParseTier.LIGHT)
            order.append(name)

        queued = [asyncio.create_task(admit(f"normal-{i}", normal)) for i in range(2)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        lane = asyncio.create_task(admit("priority", priority))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        gate.release()
        await asyncio.wait_for(lane, timeout=5)
        assert order == ["priority"]
        for task in queued:
            task.cancel()

    async def test_a_priority_parse_is_admitted_ahead_of_queued_normal_ones(self) -> None:
        governor = make_test_governor()
        normal, priority = _consumer(lane=None, governor=governor), _consumer(governor=governor)
        gate = governor.gate(Pool.LIGHT_PARSE)
        held = []
        for _ in range(gate.limit):
            await gate.acquire()
            held.append(1)
        order: list[str] = []

        async def parse(name: str, host: IndexingRedisStreamsConsumer) -> None:
            await concurrency.acquire_parsing_slot(host, ParseTier.LIGHT, None)
            order.append(name)

        queued = asyncio.create_task(parse("normal", normal))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        lane = asyncio.create_task(parse("priority", priority))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        gate.release()
        await asyncio.wait_for(lane, timeout=5)
        assert order == ["priority"]
        queued.cancel()


@pytest.mark.asyncio
class TestPerOrgShare:
    async def test_a_small_org_is_not_behind_a_big_one(self) -> None:
        # P4: org A has fifty records queued, org B one; A may not take the lane's light slots.
        consumer = _consumer(governor=make_test_governor())
        for i in range(50):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(PRIORITY, f"{i}-0", _fields("org-a", f"a-{i}"))
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(PRIORITY, "99-0", _fields("org-b", "b-0"))
        keys = list(consumer._scheduler.backlogged_keys())
        key_a = next(k for k in keys if k[0] == "org-a")
        key_b = next(k for k in keys if k[0] == "org-b")
        pool = consumer._slot_pool_of(key_a)

        for i in range(2):  # a share of the three light slots between two orgs: two
            assert consumer._slot_share_plan().allows(key_a)
            consumer._slot_share.started(("s", f"a-{i}"), key_a, pool)

        plan = consumer._slot_share_plan()
        assert not plan.allows(key_a)
        assert plan.allows(key_b)

    async def test_the_first_dispatch_round_serves_both_orgs(self) -> None:
        consumer = _consumer(governor=make_test_governor())
        for i in range(5):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(PRIORITY, f"{i}-0", _fields("org-a", f"a-{i}"))
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(PRIORITY, "9-0", _fields("org-b", "b-0"))

        first_two = {consumer._scheduler.dequeue()[0][0] for _ in range(2)}

        assert first_two == {"org-a", "org-b"}


@pytest.mark.asyncio
class TestBackpressureFromTheParser:
    async def _dispatch(self, *, paused: bool) -> AsyncMock:
        coordinator = MagicMock()
        coordinator.is_paused.return_value = paused
        consumer = _consumer(governor=make_test_governor(), backpressure_coordinator=coordinator)
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer._start_processing_task = AsyncMock()
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(PRIORITY, "0-0", _fields("org-a", "a-0"))
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        return consumer._start_processing_task

    async def test_the_lane_starts_nothing_while_the_parser_asks_for_a_pause(self) -> None:
        # P5
        (await self._dispatch(paused=True)).assert_not_awaited()

    async def test_the_lane_dispatches_when_there_is_no_pause(self) -> None:
        (await self._dispatch(paused=False)).assert_awaited_once()


class TestTopic:
    def test_it_is_a_required_topic_and_is_not_laned(self, monkeypatch) -> None:
        # P6
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "8")

        assert PRIORITY in REQUIRED_TOPICS
        assert lane_topics_for(PRIORITY, MessageBrokerType.REDIS) == [PRIORITY]
        assert lane_topics_for(Topic.RECORD_EVENTS.value, MessageBrokerType.REDIS)[0] == "record-events"

    @pytest.mark.asyncio
    async def test_the_redis_admin_creates_its_stream(self) -> None:
        redis = AsyncMock()
        redis.exists = AsyncMock(return_value=0)
        provider = MagicMock()
        provider.create_client.return_value = redis
        admin = RedisStreamsAdmin(logging.getLogger("t"), _config(), provider=provider)

        await admin.ensure_topics_exist([PRIORITY])

        assert [call.args[0] for call in redis.xgroup_create.await_args_list] == [PRIORITY]

    def test_a_retry_goes_back_to_the_same_topic_on_the_documented_backoff(self) -> None:
        # P12
        assert IndexingRedisStreamsConsumer._retry_topic(PRIORITY) == PRIORITY
        assert [compute_retry_backoff_seconds(n) for n in (1, 2, 3)] == [15, 60, 240]

    def test_the_lane_has_its_own_group_and_the_factory_gives_it_the_fair_scheduler(self) -> None:
        consumer = MessagingFactory.create_consumer(
            logging.getLogger("t"),
            config=_config(),
            broker_type=MessageBrokerType.REDIS,
            consumer_type=ConsumerType.INDEXING,
            fair_scheduler_config=FairSchedulerConfig(enabled=False),
            priority_lane=LANE,
        )

        assert consumer.priority_lane is LANE
        assert consumer.fair_scheduler_config.enabled is True
        assert consumer.config.group_id != "records_consumer_group"

    def test_a_consumer_is_a_stage_or_the_lane_not_both(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            _consumer(stage_admission=concurrency.StageAdmission(stage="classify", limit=2))
