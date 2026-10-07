"""Upgrading an install whose lanes were filled by hashing.

Events are published the old way, the feature is switched on, producers carry
on, and the indexing service's upkeep runs its one-time fix-up. Real streams
and consumer groups (fakeredis), the real indexing consumer's backlog read,
the real lane map scripts.
"""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("fakeredis.aioredis")
pytest.importorskip("lupa")

from app.modules.indexing import lane_upkeep
from app.modules.indexing.lane_upkeep import run_lane_upkeep
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.lanes.assignment import (
    AssignedRedisLaneRouter,
    LaneAssignments,
    LaneEntry,
    read_lane_map,
)
from app.services.messaging.lanes.hash_router import RedisLaneRouter, stable_lane
from app.services.messaging.lanes.interface import LaneAssignmentMode, LaneConfig
from app.services.messaging.lanes.producer import LaneAwareProducer
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider
from tests.unit.modules.indexing.test_lane_upkeep import _Graph
from tests.unit.services.messaging.test_lane_assignment import _colliding
from tests.unit.services.messaging.test_lane_aware_producer import _RecordingProducer

TOPIC = "record-events"
GROUP = "records_consumer_group"
LANES = 8
GITLAB, SLACK = _colliding(2)
SHARED = stable_lane(GITLAB, LANES)
JIRA = next(
    c
    for c in (f"jira-{i}" for i in range(200))
    if stable_lane(c, LANES) not in (SHARED, stable_lane("__default__", LANES))
)


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", str(LANES))
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_CACHE_SECONDS", "0")
    monkeypatch.setattr(lane_upkeep, "_FENCE_MARGIN_MS", 0)


async def _publish(provider: FakeRedisConnectionProvider, router: object, events: list[tuple[str, str]]) -> None:
    inner = RedisStreamsProducer(logging.getLogger("t"), RedisStreamsConfig(client_id="p"), provider=provider)
    producer = LaneAwareProducer(
        logging.getLogger("t"), inner, router, LaneConfig(lane_count=LANES, assignment=LaneAssignmentMode.ASSIGNED)  # type: ignore[arg-type]
    )
    await producer.initialize()
    try:
        for connector, record in events:
            await producer.send_event(
                TOPIC, "newRecord", {"recordId": record, "orgId": "org-1", "connectorId": connector}
            )
    finally:
        await producer.cleanup()


async def test_an_upgrade_separates_the_colliding_connectors_without_early_re_sends() -> None:
    provider = FakeRedisConnectionProvider()
    client = provider.get_client()
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("t"),
        RedisStreamsConfig(topics=RedisLaneRouter(LANES).lane_topics(TOPIC), group_id=GROUP, client_id="i-1"),
        provider=provider,
    )
    consumer.redis = provider.create_client()
    for stream in consumer.config.topics:
        await client.xgroup_create(stream, GROUP, id="0", mkstream=True)

    # Before the upgrade: hashing put GitLab's backlog and Slack's events on one lane.
    await _publish(
        provider,
        RedisLaneRouter(LANES),
        [(GITLAB, f"g{i}") for i in range(30)] + [(SLACK, f"s{i}") for i in range(3)] + [(JIRA, "j0")],
    )
    assert await client.xlen(f"{TOPIC}.{SHARED}") == 33

    # Switched on: producers keep publishing, GitLab first.
    assignments = LaneAssignments(logging.getLogger("t"), provider, topic=TOPIC, fallback_lane_count=LANES)
    assigned = AssignedRedisLaneRouter(assignments, logging.getLogger("t"))
    await _publish(provider, assigned, [(GITLAB, "g30"), (JIRA, "j1")])

    graph = _Graph()
    graph.add(GITLAB, queued=31)
    graph.add(SLACK, queued=3)
    graph.add(JIRA, queued=2)
    rescue = _RecordingProducer()
    await run_lane_upkeep(
        assignments=assignments,
        graph_provider=graph,  # type: ignore[arg-type]
        producer=rescue,  # type: ignore[arg-type]
        backlog=await consumer.lane_backlog(TOPIC),
        logger=logging.getLogger("t"),
    )

    entries = await read_lane_map(client, TOPIC)
    assert entries[JIRA] == LaneEntry(stable_lane(JIRA, LANES), "team"), "never collided, never moved"
    assert entries[GITLAB].lane == SHARED, "the connector with the backlog keeps the lane"
    slack = entries[SLACK]
    assert slack.lane != SHARED
    assert slack.prev_lane == SHARED
    assert len(rescue.events) == 3
    assert {topic for topic, *_ in rescue.events} == {f"{TOPIC}.{slack.lane}"}

    # Slack's new events land on its new lane.
    await _publish(provider, assigned, [(SLACK, "s3")])
    assert await client.xlen(f"{TOPIC}.{slack.lane}") == 1

    # The sweep must not re-send Slack's records while its old events still
    # wait on the shared lane...
    backlog = await consumer.lane_backlog(TOPIC)
    assert backlog.oldest_waiting_for({"connectorId": SLACK}) is not None

    # ...and the move settles once the shared lane has finished everything up
    # to the fence: the first pass fences, the drain finishes the old lane,
    # the next pass clears the old lane from Slack's entry.
    await run_lane_upkeep(
        assignments=assignments,
        graph_provider=graph,  # type: ignore[arg-type]
        producer=rescue,  # type: ignore[arg-type]
        backlog=await consumer.lane_backlog(TOPIC),
        logger=logging.getLogger("t"),
    )
    assert (await read_lane_map(client, TOPIC))[SLACK].fence_ms is not None
    shared = f"{TOPIC}.{SHARED}"
    delivered = await client.xreadgroup(GROUP, "c", {shared: ">"}, count=1000)
    await client.xack(shared, GROUP, *[entry_id for _s, entries_ in delivered for entry_id, _f in entries_])

    await run_lane_upkeep(
        assignments=assignments,
        graph_provider=graph,  # type: ignore[arg-type]
        producer=rescue,  # type: ignore[arg-type]
        backlog=await consumer.lane_backlog(TOPIC),
        logger=logging.getLogger("t"),
    )

    settled = (await read_lane_map(client, TOPIC))[SLACK]
    assert (settled.lane, settled.prev_lane, settled.fence_ms) == (slack.lane, None, None)
    assert len(rescue.events) == 3, "the fix-up ran once"
