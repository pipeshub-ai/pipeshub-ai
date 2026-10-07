"""The community report, scaled down, against a real Redis (standalone or cluster).

A GitLab connector's large backlog is published first, then a few Slack
events, from two connectors whose ids hash to the same lane. The real indexing
consumer then runs with a GitLab handler slow enough that its backlog cannot
drain inside the test. With hashing, Slack's events sit behind GitLab's on one
stream; with assigned lanes they are on a stream of their own.

Only seams that existed before the lane map are used (the producer factory,
the environment, the consumer), so the same scenario runs against the code
that only hashed.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING

from app.services.messaging.config import (
    IndexingEvent,
    MessageBrokerType,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.lanes.hash_router import stable_lane
from app.services.messaging.messaging_factory import MessagingFactory, lane_topics_for
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier

if TYPE_CHECKING:
    import pytest

LANES = 8
GITLAB_EVENTS = 20_000
SLACK_EVENTS = 50
# Long enough that GitLab's backlog cannot drain while the test watches.
GITLAB_HANDLER_SECONDS = 0.2
WAIT_FOR_SLACK_SECONDS = 20.0


def colliding_connectors() -> tuple[str, str]:
    """A GitLab and a Slack connector id that hash to the same lane."""
    by_lane: dict[int, str] = {}
    for i in count():
        name = f"connector-{i}"
        lane = stable_lane(name, LANES)
        if lane in by_lane:
            return by_lane[lane], name
        by_lane[lane] = name
    raise AssertionError("unreachable")


@dataclass
class Outcome:
    gitlab_stream_lengths: dict[str, int]
    slack_stream_lengths: dict[str, int]
    slack_dispatched: int = 0
    seconds_to_last_slack: float | None = None
    gitlab_dispatched_when_slack_done: int | None = None
    gitlab_dispatched_by_end: int = 0
    gitlab_order: list[int] = field(default_factory=list)

    @property
    def shared_a_stream(self) -> bool:
        return bool(set(self.gitlab_stream_lengths) & set(self.slack_stream_lengths))

    def describe(self) -> str:
        return (
            f"GitLab streams {self.gitlab_stream_lengths}, Slack streams "
            f"{self.slack_stream_lengths}; Slack dispatched {self.slack_dispatched}/"
            f"{SLACK_EVENTS} (last after {self.seconds_to_last_slack}s), GitLab "
            f"dispatched {self.gitlab_dispatched_when_slack_done} by then and "
            f"{self.gitlab_dispatched_by_end} by the end"
        )


def configure(monkeypatch: pytest.MonkeyPatch, topic: str, assignment: str) -> None:
    monkeypatch.setenv("FAIR_SCHEDULING_LANED_TOPICS", topic)
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", str(LANES))
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_ASSIGNMENT", assignment)


def _event(connector: str, index: int) -> tuple[str, dict[str, object]]:
    record = f"{connector}-{index}"
    return record, {
        "eventType": "newRecord",
        "payload": {
            "recordId": record,
            "orgId": "org-1",
            "connectorId": connector,
            "index": index,
            "extension": "txt",
            "mimeType": "text/plain",
        },
    }


async def _stream_lengths(redis: object, topic: str, connector: str) -> dict[str, int]:
    """Which streams hold this connector's events, and how many."""
    lengths: dict[str, int] = {}
    for stream in lane_topics_for(topic, MessageBrokerType.REDIS):
        entries = await redis.xrange(stream, count=GITLAB_EVENTS + SLACK_EVENTS)  # type: ignore[attr-defined]
        mine = sum(f'"connectorId": "{connector}"' in fields["value"] for _id, fields in entries)
        if mine:
            lengths[stream] = mine
    return lengths


async def run(topic: str, redis_config: RedisStreamsConfig, group: str) -> Outcome:
    gitlab, slack = colliding_connectors()
    return await _consume(topic, redis_config, group, gitlab, slack, publish=True)


async def _consume(
    topic: str,
    redis_config: RedisStreamsConfig,
    group: str,
    gitlab: str,
    slack: str,
    *,
    publish: bool,
) -> Outcome:
    logger = logging.getLogger("it-report")
    if publish:
        producer = MessagingFactory.create_producer(logger, redis_config, MessageBrokerType.REDIS)
        await producer.initialize()
        try:
            for start in range(0, GITLAB_EVENTS, 1_000):
                await producer.send_messages(
                    topic, [_event(gitlab, i) for i in range(start, start + 1_000)]
                )
            await producer.send_messages(topic, [_event(slack, i) for i in range(SLACK_EVENTS)])
        finally:
            await producer.cleanup()

    consumer = IndexingRedisStreamsConsumer(
        logger,
        redis_config.model_copy(
            update={
                "topics": lane_topics_for(topic, MessageBrokerType.REDIS),
                "group_id": group,
                "client_id": f"{group}-1",
                "batch_size": 100,
                "block_ms": 200,
            }
        ),
        fair_scheduler_config=FairSchedulerConfig(
            enabled=True,
            key_fields=("orgId", "connectorId"),
            default_quantum=1,
            max_buffered_messages=2_000,
            max_per_entity_messages=500,
            max_dwell_seconds=900.0,
        ),
    )
    client = _client(redis_config)
    try:
        outcome = Outcome(
            gitlab_stream_lengths=await _stream_lengths(client, topic, gitlab),
            slack_stream_lengths=await _stream_lengths(client, topic, slack),
        )
    finally:
        await client.aclose()

    started = time.monotonic()
    slack_done = asyncio.Event()

    async def handle(message):  # noqa: ANN202
        connector = message.payload["connectorId"]
        if connector == gitlab:
            outcome.gitlab_dispatched_by_end += 1
        else:
            outcome.slack_dispatched += 1
            if outcome.slack_dispatched == SLACK_EVENTS:
                outcome.seconds_to_last_slack = round(time.monotonic() - started, 2)
                outcome.gitlab_dispatched_when_slack_done = outcome.gitlab_dispatched_by_end
                slack_done.set()
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        if connector == gitlab:
            await asyncio.sleep(GITLAB_HANDLER_SECONDS)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    # GitLab's order as the consumer hands its events out, which is what a
    # lane keeps; handlers start concurrently, so their own order jitters.
    start_processing = consumer._start_processing_task

    async def record_order(stream_name, message_id, fields, parsed_message=None):  # noqa: ANN202
        if parsed_message is not None and parsed_message.payload.get("connectorId") == gitlab:
            outcome.gitlab_order.append(int(parsed_message.payload["index"]))
        return await start_processing(stream_name, message_id, fields, parsed_message)

    consumer._start_processing_task = record_order  # type: ignore[method-assign]
    await consumer.start(handle)
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(slack_done.wait(), timeout=WAIT_FOR_SLACK_SECONDS)
    finally:
        await consumer.stop()
    return outcome


def _client(redis_config: RedisStreamsConfig):  # noqa: ANN202
    from app.services.redis.config import ClientOptions, RedisConnectionConfig
    from app.services.redis.connection_provider_factory import get_redis_provider

    return get_redis_provider(RedisConnectionConfig.from_redis_config(redis_config)).create_client(
        ClientOptions(decode_responses=True)
    )


async def remove(redis_config: RedisStreamsConfig, topic: str) -> None:
    """Drop the scenario's streams and lane map."""
    client = _client(redis_config)
    try:
        for stream in lane_topics_for(topic, MessageBrokerType.REDIS):
            await client.delete(stream)
        await client.delete(f"{{{topic}}}:lane-map")
        await client.delete(f"{{{topic}}}:lane-meta")
    finally:
        await client.aclose()


def assert_fixed(outcome: Outcome) -> None:
    """What assigned lanes promise for the report."""
    assert not outcome.shared_a_stream, outcome.describe()
    assert len(outcome.gitlab_stream_lengths) == 1, outcome.describe()
    assert outcome.slack_dispatched == SLACK_EVENTS, outcome.describe()
    assert outcome.gitlab_dispatched_when_slack_done is not None
    assert GITLAB_EVENTS - outcome.gitlab_dispatched_when_slack_done > 18_000, outcome.describe()
    assert outcome.gitlab_order == sorted(outcome.gitlab_order), f"GitLab's own order was kept: {outcome.gitlab_order}"


def assert_reproduced(outcome: Outcome) -> None:
    """What hashing does to the report: Slack waits behind GitLab."""
    assert outcome.shared_a_stream, outcome.describe()
    assert outcome.slack_dispatched == 0, outcome.describe()
    assert GITLAB_EVENTS - outcome.gitlab_dispatched_by_end > 18_000, outcome.describe()


class _Graph:
    """The three graph reads lane upkeep makes, for the two connectors."""

    def __init__(self, queued: dict[str, int]) -> None:
        self.queued = queued

    async def get_documents_paginated(
        self, collection, skip=0, limit=50, filters=None, sort_field=None, **_kwargs
    ) -> list[dict[str, object]]:
        if collection == "apps":
            rows = [
                {"_key": c, "name": c, "type": "GITLAB", "scope": "team", "orgId": "org-1"}
                for c in sorted(self.queued)
            ]
        else:
            connector = filters["connectorId"]
            rows = [
                {
                    "_key": f"{connector}-{i}",
                    "orgId": "org-1",
                    "connectorId": connector,
                    "indexingStatus": "QUEUED",
                    "queuedAtTimestamp": i,
                    "origin": "CONNECTOR",
                    "extension": "txt",
                    "mimeType": "text/plain",
                }
                for i in range(self.queued[connector])
            ]
        return rows[skip : skip + limit]

    async def get_connector_stats(self, _org_id: str, connector_id: str) -> dict[str, object]:
        return {"data": {"stats": {"indexingStatus": {"QUEUED": self.queued[connector_id]}}}}


async def run_upgrade(
    monkeypatch: pytest.MonkeyPatch, topic: str, redis_config: RedisStreamsConfig, group: str
) -> tuple[dict[str, object], Outcome]:
    """The report's install upgraded: its lanes were filled by hashing, then
    assigned lanes are switched on and the indexing service's upkeep runs the
    one-time fix-up before the consumer starts."""
    from app.modules.indexing.lane_upkeep import run_lane_upkeep
    from app.services.messaging.lanes import assignment
    from app.services.messaging.lanes.assignment import lane_assignments_in_use

    logger = logging.getLogger("it-upgrade")
    gitlab, slack = colliding_connectors()
    configure(monkeypatch, topic, "hash")
    legacy = MessagingFactory.create_producer(logger, redis_config, MessageBrokerType.REDIS)
    await legacy.initialize()
    try:
        for start in range(0, GITLAB_EVENTS, 1_000):
            await legacy.send_messages(topic, [_event(gitlab, i) for i in range(start, start + 1_000)])
        await legacy.send_messages(topic, [_event(slack, i) for i in range(SLACK_EVENTS)])
    finally:
        await legacy.cleanup()

    configure(monkeypatch, topic, "assigned")
    monkeypatch.setattr(assignment, "_shared", {})
    producer = MessagingFactory.create_producer(logger, redis_config, MessageBrokerType.REDIS)
    await producer.initialize()
    try:
        assignments = lane_assignments_in_use(topic)
        assert assignments is not None
        await run_lane_upkeep(
            assignments=assignments,
            graph_provider=_Graph({gitlab: GITLAB_EVENTS, slack: SLACK_EVENTS}),  # type: ignore[arg-type]
            producer=producer,
            backlog=None,
            logger=logger,
        )
        entries = await assignments.read_map()
    finally:
        await producer.cleanup()

    lanes = {
        "gitlab": entries[gitlab].lane,
        "gitlab_prev": entries[gitlab].prev_lane,
        "slack": entries[slack].lane,
        "slack_prev": entries[slack].prev_lane,
        "hash_lane": stable_lane(gitlab, LANES),
    }
    return lanes, await _consume(topic, redis_config, group, gitlab, slack, publish=False)


def assert_upgrade_separated_them(lanes: dict[str, object], outcome: Outcome) -> None:
    """GitLab, with the backlog, kept the lane; Slack moved and its queued
    records followed it, so they are indexed without waiting for GitLab."""
    assert lanes["gitlab"] == lanes["hash_lane"], lanes
    assert lanes["gitlab_prev"] is None, lanes
    assert lanes["slack"] != lanes["hash_lane"], lanes
    assert lanes["slack_prev"] == lanes["hash_lane"], lanes
    assert outcome.slack_dispatched == SLACK_EVENTS, outcome.describe()
    assert outcome.gitlab_dispatched_when_slack_done is not None
    assert GITLAB_EVENTS - outcome.gitlab_dispatched_when_slack_done > 18_000, outcome.describe()
    assert outcome.gitlab_order == sorted(outcome.gitlab_order), f"GitLab's own order was kept: {outcome.gitlab_order}"
