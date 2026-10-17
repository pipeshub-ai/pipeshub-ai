"""What the Redis Streams consumer reports as still waiting on each lane.

Runs against an in-memory Redis that implements streams, consumer groups and
the pending-entries list, with the real producer and lane router, so the lanes
the consumer names are the ones events are actually written to.
"""
from __future__ import annotations

import asyncio
from logging import getLogger

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.lanes.assignment import LaneEntry
from app.services.messaging.lanes.backlog import LaneBacklog, redis_lanes_for_key
from app.services.messaging.lanes.hash_router import RedisLaneRouter, stable_lane
from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY, LaneConfig
from app.services.messaging.lanes.producer import LaneAwareProducer
from app.services.messaging.redis_streams.backlog import read_stream_backlog
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider
from tests.support.loop_topology import on_loop, worker_loop

TOPIC = "record-events"
GROUP = "records_consumer_group"
LANES = 4
STREAM = f"{TOPIC}.0"


@pytest.fixture
def provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=False)


@pytest.fixture
async def redis(provider: FakeRedisConnectionProvider):  # noqa: ANN201
    client = provider.get_client()
    await client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    return client


async def _add(redis, *milliseconds: int, stream: str = STREAM) -> None:
    """Entries whose ids say when they were published."""
    for ms in milliseconds:
        await redis.xadd(stream, {"value": "{}"}, id=f"{ms}-0")


async def _deliver(redis, count: int, stream: str = STREAM) -> list[str]:
    batches = await redis.xreadgroup(GROUP, "consumer-1", {stream: ">"}, count=count)
    return [
        entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        for _stream, entries in batches
        for entry_id, _fields in entries
    ]


class TestReadStreamBacklog:
    async def test_nothing_delivered_yet_reports_the_first_entry(self, redis) -> None:
        await _add(redis, 1000, 2000, 3000)

        assert await read_stream_backlog(redis, GROUP, [STREAM]) == {STREAM: 1000.0}

    async def test_delivered_but_not_acknowledged_is_still_waiting(self, redis) -> None:
        """Buffered, parked behind a paused lane, or being processed: not finished."""
        await _add(redis, 1000, 2000, 3000)
        await _deliver(redis, 3)

        assert await read_stream_backlog(redis, GROUP, [STREAM]) == {STREAM: 1000.0}

    async def test_the_oldest_unfinished_entry_wins_when_later_ones_finish_first(self, redis) -> None:
        """Fair scheduling passes over a blocked record; the lane has not moved past it."""
        await _add(redis, 1000, 2000, 3000)
        _first, second, _third = await _deliver(redis, 3)
        await redis.xack(STREAM, GROUP, second)

        assert await read_stream_backlog(redis, GROUP, [STREAM]) == {STREAM: 1000.0}

    async def test_acknowledging_the_head_moves_the_lane_on(self, redis) -> None:
        await _add(redis, 1000, 2000, 3000)
        first, _second = await _deliver(redis, 2)
        await redis.xack(STREAM, GROUP, first)

        assert await read_stream_backlog(redis, GROUP, [STREAM]) == {STREAM: 2000.0}

    async def test_past_everything_delivered_the_first_undelivered_entry_is_next(self, redis) -> None:
        await _add(redis, 1000, 2000, 3000)
        delivered = await _deliver(redis, 2)
        await redis.xack(STREAM, GROUP, *delivered)

        assert await read_stream_backlog(redis, GROUP, [STREAM]) == {STREAM: 3000.0}

    async def test_a_lane_the_group_has_caught_up_on_is_left_out(self, redis) -> None:
        await _add(redis, 1000, 2000)
        delivered = await _deliver(redis, 2)
        await redis.xack(STREAM, GROUP, *delivered)

        assert await read_stream_backlog(redis, GROUP, [STREAM]) == {}

    async def test_an_empty_lane_is_left_out(self, redis) -> None:
        assert await read_stream_backlog(redis, GROUP, [STREAM]) == {}

    async def test_each_lane_is_reported_on_its_own(self, redis) -> None:
        busy, idle = f"{TOPIC}.1", f"{TOPIC}.2"
        for stream in (busy, idle):
            await redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
        await _add(redis, 5000, 6000, stream=busy)
        await _add(redis, 7000, stream=idle)
        await redis.xack(idle, GROUP, *await _deliver(redis, 1, stream=idle))

        assert await read_stream_backlog(redis, GROUP, [STREAM, busy, idle]) == {busy: 5000.0}

    async def test_a_lane_that_cannot_be_read_is_an_error_not_a_caught_up_lane(self, redis) -> None:
        await _add(redis, 1000)

        with pytest.raises(Exception, match="(?i)group|no such key"):
            await read_stream_backlog(redis, "some-other-group", [STREAM])

    async def test_a_lane_costs_three_commands_however_long_it_is(self, redis) -> None:
        await _add(redis, *range(1000, 1400))
        await _deliver(redis, 50)
        calls: list[str] = []

        class Counting:
            def __getattr__(self, name: str):  # noqa: ANN204
                command = getattr(redis, name)

                async def counted(*args, **kwargs):  # noqa: ANN202
                    calls.append(name)
                    return await command(*args, **kwargs)

                return counted

        assert await read_stream_backlog(Counting(), GROUP, [STREAM]) == {STREAM: 1000.0}
        assert sorted(calls) == ["xinfo_groups", "xpending", "xrange"]


class TestLanesAnEventCouldBeOn:
    STREAMS = (TOPIC, *(f"{TOPIC}.{lane}" for lane in range(LANES)))

    def test_its_own_lane_the_base_stream_and_the_shared_default_lane(self) -> None:
        own = f"{TOPIC}.{stable_lane('gitlab-1', LANES)}"
        default = f"{TOPIC}.{stable_lane('__default__', LANES)}"

        assert redis_lanes_for_key(TOPIC, "gitlab-1", self.STREAMS, LANES) == {TOPIC, own, default}

    def test_an_event_without_the_key_is_on_the_default_lane(self) -> None:
        default = f"{TOPIC}.{stable_lane('__default__', LANES)}"

        assert redis_lanes_for_key(TOPIC, None, self.STREAMS, LANES) == {TOPIC, default}

    def test_lanes_left_over_from_a_larger_lane_count_could_hold_anything(self) -> None:
        """The consumer still drains them, and any key may have been routed there."""
        streams = (*self.STREAMS, f"{TOPIC}.6", f"{TOPIC}.7")

        lanes = redis_lanes_for_key(TOPIC, "gitlab-1", streams, LANES)

        assert {f"{TOPIC}.6", f"{TOPIC}.7"} <= lanes

    def test_with_laning_off_every_stream_could_hold_it(self) -> None:
        streams = (TOPIC, f"{TOPIC}.2")

        assert redis_lanes_for_key(TOPIC, "gitlab-1", streams, 1) == set(streams)


class TestLaneBacklog:
    def test_the_oldest_across_the_lanes_an_event_could_be_on(self) -> None:
        backlog = LaneBacklog(TOPIC, {"a": 300.0, "b": 100.0, "c": 50.0}, lambda _payload: {"a", "b", "idle"})

        assert backlog.oldest_waiting_for({}) == 100.0

    def test_none_when_those_lanes_are_caught_up(self) -> None:
        backlog = LaneBacklog(TOPIC, {"c": 50.0}, lambda _payload: {"a", "b"})

        assert backlog.oldest_waiting_for({}) is None

    def test_any_lane_counts_when_the_lane_cannot_be_recomputed(self) -> None:
        assert LaneBacklog(TOPIC, {"a": 300.0, "c": 50.0}).oldest_waiting_for({}) == 50.0
        assert LaneBacklog(TOPIC, {}).oldest_waiting_for({}) is None


async def test_a_consumer_that_reports_no_backlog_says_so_rather_than_answering_empty(
    provider: FakeRedisConnectionProvider,
) -> None:
    """An empty answer would read as "caught up" and license a re-send."""
    simple = RedisStreamsConsumer(
        getLogger("test"),
        RedisStreamsConfig(topics=["entity-events"], group_id=GROUP, client_id="connectors-1"),
        provider=provider,
    )

    with pytest.raises(NotImplementedError):
        await simple.lane_backlog("entity-events")


def _consumer(provider: FakeRedisConnectionProvider, topics: list[str]) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        getLogger("test"),
        RedisStreamsConfig(topics=topics, group_id=GROUP, client_id="indexing-1"),
        provider=provider,
    )
    consumer.redis = provider.create_client()
    return consumer


async def _publish(provider: FakeRedisConnectionProvider, connector_id: str, record_id: str) -> None:
    """Through the real lane-aware producer, as every publisher does."""
    inner = RedisStreamsProducer(getLogger("test"), RedisStreamsConfig(client_id="p"), provider=provider)
    producer = LaneAwareProducer(
        getLogger("test"), inner, RedisLaneRouter(LANES), LaneConfig(lane_count=LANES)
    )
    await producer.initialize()
    try:
        await producer.send_event(
            TOPIC, "newRecord", {"recordId": record_id, "connectorId": connector_id}, key=record_id
        )
    finally:
        await producer.cleanup()


def _connectors_on_lanes_of_their_own() -> tuple[str, str]:
    """Two connectors that share a lane with neither each other nor the default lane."""
    taken = {stable_lane("__default__", LANES)}
    chosen: list[str] = []
    for name in (f"connector-{i}" for i in range(100)):
        if stable_lane(name, LANES) not in taken:
            taken.add(stable_lane(name, LANES))
            chosen.append(name)
        if len(chosen) == 2:
            break
    return chosen[0], chosen[1]


BUSY, IDLE = _connectors_on_lanes_of_their_own()


class TestIndexingConsumerLaneBacklog:
    @pytest.fixture(autouse=True)
    def _four_lanes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", str(LANES))

    @pytest.fixture
    async def consumer(self, provider: FakeRedisConnectionProvider) -> IndexingRedisStreamsConsumer:
        topics = RedisLaneRouter(LANES).lane_topics(TOPIC)
        consumer = _consumer(provider, topics)
        for topic in topics:
            await consumer.redis.xgroup_create(topic, GROUP, id="0", mkstream=True)
        return consumer

    async def test_a_published_event_is_waiting_on_the_lane_its_connector_routes_to(
        self, provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        await _publish(provider, BUSY, "rec-1")

        backlog = await consumer.lane_backlog(TOPIC)

        assert list(backlog.oldest_waiting_ms) == [f"{TOPIC}.{stable_lane(BUSY, LANES)}"]
        assert backlog.oldest_waiting_for({"connectorId": BUSY}) is not None
        assert backlog.oldest_waiting_for({"connectorId": IDLE}) is None

    async def test_work_on_the_base_stream_is_waiting_for_every_connector(
        self, provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        """Published before lanes were switched on, or by a producer that is not laned."""
        await _add(provider.get_client(), 1000, stream=TOPIC)

        backlog = await consumer.lane_backlog(TOPIC)

        assert backlog.oldest_waiting_for({"connectorId": "gitlab-1"}) == 1000.0
        assert backlog.oldest_waiting_for({"connectorId": "anything-else"}) == 1000.0

    async def test_work_on_the_default_lane_is_waiting_for_every_connector(
        self, provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        """Stale recovery publishes without the connector, so its events land there."""
        default = f"{TOPIC}.{stable_lane('__default__', LANES)}"
        await _add(provider.get_client(), 1000, stream=default)

        backlog = await consumer.lane_backlog(TOPIC)

        assert backlog.oldest_waiting_for({"connectorId": BUSY}) == 1000.0
        assert backlog.oldest_waiting_for({"connectorId": IDLE}) == 1000.0

    async def test_a_lane_adopted_from_a_larger_lane_count_is_waiting_for_every_connector(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        retired = f"{TOPIC}.{LANES + 2}"
        topics = [*RedisLaneRouter(LANES).lane_topics(TOPIC), retired]
        consumer = _consumer(provider, topics)
        for topic in topics:
            await consumer.redis.xgroup_create(topic, GROUP, id="0", mkstream=True)
        await _add(provider.get_client(), 1000, stream=retired)

        backlog = await consumer.lane_backlog(TOPIC)

        assert backlog.oldest_waiting_for({"connectorId": "gitlab-1"}) == 1000.0

    async def test_other_topics_the_consumer_reads_are_not_part_of_the_answer(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        topics = [TOPIC, f"{TOPIC}.0", "entity-events", "record-events-archive"]
        consumer = _consumer(provider, topics)
        for topic in topics:
            await consumer.redis.xgroup_create(topic, GROUP, id="0", mkstream=True)
        for stream in ("entity-events", "record-events-archive"):
            await _add(provider.get_client(), 1000, stream=stream)

        backlog = await consumer.lane_backlog(TOPIC)

        assert backlog.oldest_waiting_ms == {}

    async def test_a_consumer_that_is_not_connected_cannot_answer(
        self, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        consumer.redis = None

        with pytest.raises(RuntimeError, match="not connected"):
            await consumer.lane_backlog(TOPIC)

    async def test_asked_from_the_worker_loop_it_reads_on_the_loop_that_owns_the_client(
        self, provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        """The recovery sweep runs on the worker loop; the Redis client belongs to the main one."""
        await _publish(provider, "gitlab-1", "rec-1")
        consumer.main_loop = asyncio.get_running_loop()
        read_on: list[asyncio.AbstractEventLoop] = []
        xpending = consumer.redis.xpending

        async def recording(*args, **kwargs):  # noqa: ANN202
            read_on.append(asyncio.get_running_loop())
            return await xpending(*args, **kwargs)

        consumer.redis.xpending = recording

        with worker_loop() as worker:
            backlog = await on_loop(worker, consumer.lane_backlog(TOPIC))

        assert backlog.oldest_waiting_for({"connectorId": "gitlab-1"}) is not None
        assert read_on and all(loop is consumer.main_loop for loop in read_on)


def _lane(connector_id: str) -> str:
    return f"{TOPIC}.{stable_lane(connector_id, LANES)}"


def _another_lane(*avoid: str) -> str:
    """A lane none of these connectors, nor the default key, hashes to."""
    taken = {_lane(c) for c in (*avoid, DEFAULT_LANE_KEY)}
    return next(f"{TOPIC}.{lane}" for lane in range(LANES) if f"{TOPIC}.{lane}" not in taken)


class TestLanesAnAssignedEventCouldBeOn:
    STREAMS = (TOPIC, *(f"{TOPIC}.{lane}" for lane in range(LANES)))

    def test_its_assigned_lane_as_well_as_its_hash_lane(self) -> None:
        """The hash lane stays: old producers, a rollback and lookups that fell back use it."""
        assigned = _another_lane("gitlab-1")
        entry = LaneEntry(int(assigned.rsplit(".", 1)[1]), "team")

        lanes = redis_lanes_for_key(TOPIC, "gitlab-1", self.STREAMS, LANES, {"gitlab-1": entry})

        assert {assigned, _lane("gitlab-1"), _lane(DEFAULT_LANE_KEY), TOPIC} <= lanes

    def test_the_lane_a_move_is_still_settling_off(self) -> None:
        entry = LaneEntry(1, "team", prev_lane=2, moved_at_ms=1000)

        lanes = redis_lanes_for_key(TOPIC, "gitlab-1", self.STREAMS, LANES, {"gitlab-1": entry})

        assert {f"{TOPIC}.1", f"{TOPIC}.2"} <= lanes

    def test_the_default_keys_assigned_lane(self) -> None:
        entry = LaneEntry(3, "system")

        lanes = redis_lanes_for_key(TOPIC, "gitlab-1", self.STREAMS, LANES, {DEFAULT_LANE_KEY: entry})

        assert f"{TOPIC}.3" in lanes

    def test_another_connectors_assigned_lane_does_not_count(self) -> None:
        other = _another_lane("gitlab-1")
        entry = LaneEntry(int(other.rsplit(".", 1)[1]), "team")

        lanes = redis_lanes_for_key(TOPIC, "gitlab-1", self.STREAMS, LANES, {"slack-1": entry})

        assert other not in lanes

    def test_a_map_that_could_not_be_read_makes_every_stream_count(self) -> None:
        assert redis_lanes_for_key(TOPIC, "gitlab-1", self.STREAMS, LANES, None) == set(self.STREAMS)


class TestIndexingConsumerAndTheLaneMap:
    @pytest.fixture(autouse=True)
    def _lanes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", str(LANES))

    @pytest.fixture
    async def consumer(self, provider: FakeRedisConnectionProvider) -> IndexingRedisStreamsConsumer:
        topics = RedisLaneRouter(LANES).lane_topics(TOPIC)
        consumer = _consumer(provider, topics)
        for topic in topics:
            await consumer.redis.xgroup_create(topic, GROUP, id="0", mkstream=True)
        return consumer

    async def test_when_the_map_cannot_be_read_every_lane_holds_a_record_back(
        self, provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        """Leaving a record alone longer is the safe mistake; re-sending early is not."""
        await _add(provider.get_client(), 1000, stream=_another_lane(BUSY))

        async def unreadable(*_args, **_kwargs):  # noqa: ANN202
            raise ConnectionError("lane map unavailable")

        consumer.redis.hgetall = unreadable

        backlog = await consumer.lane_backlog(TOPIC)

        assert backlog.oldest_waiting_for({"connectorId": BUSY}) == 1000.0

    async def test_with_no_map_it_answers_exactly_as_hashing_did(
        self, provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        await _add(provider.get_client(), 1000, stream=_another_lane(BUSY))

        backlog = await consumer.lane_backlog(TOPIC)

        assert backlog.oldest_waiting_for({"connectorId": BUSY}) is None

    async def test_at_startup_it_records_how_many_lanes_it_reads(
        self, provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        await consumer._IndexingRedisStreamsConsumer__record_lane_count()

        assert await provider.get_client().hget("{record-events}:lane-meta", "laneCount") == str(LANES)

    async def test_with_laning_off_it_records_nothing(
        self,
        provider: FakeRedisConnectionProvider,
        consumer: IndexingRedisStreamsConsumer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "1")

        await consumer._IndexingRedisStreamsConsumer__record_lane_count()

        assert not await provider.get_client().exists("{record-events}:lane-meta")

    async def test_a_stream_of_a_topic_that_is_not_laned_gets_its_retry_back_as_before(
        self, consumer: IndexingRedisStreamsConsumer
    ) -> None:
        from unittest.mock import AsyncMock

        from app.services.messaging.config import StreamMessage

        consumer.producer = AsyncMock()
        consumer._run_on_main_loop = lambda coro: coro  # type: ignore[method-assign]
        message = StreamMessage(eventType="newRecord", payload={"recordId": "r1"})

        await consumer._requeue_message("entity-events.2", message, "stable-1")

        assert consumer.producer.send_event.await_args.kwargs["topic"] == "entity-events.2"

    async def test_after_switching_back_to_hashing_assigned_lanes_still_count(
        self,
        provider: FakeRedisConnectionProvider,
        consumer: IndexingRedisStreamsConsumer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A rollback must not make the sweep re-send records whose events
        still wait on the lanes they were assigned."""
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_ASSIGNMENT", "hash")
        assigned = _another_lane(BUSY)
        client = provider.get_client()
        await client.hset(
            "{record-events}:lane-map", BUSY, f"v1|{assigned.rsplit('.', 1)[1]}|team|live|||"
        )
        await _add(client, 1000, stream=assigned)

        backlog = await consumer.lane_backlog(TOPIC)

        assert backlog.oldest_waiting_for({"connectorId": BUSY}) == 1000.0
