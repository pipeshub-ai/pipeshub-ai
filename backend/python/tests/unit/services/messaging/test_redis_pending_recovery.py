"""Pending entries come back to the Redis Streams indexing consumer while it
is busy, and every lane gets its turn when they do.

A first sync of several connectors can leave thousands of entries pending.
Before this, three things together could stop whole connectors indexing for
as long as any stream kept producing:

- the dwell sweep released buffered entries merely for being old;
- released entries, and those a restart left behind, came back only once
  every stream went idle -- never, during a long sync -- and not at all while
  the buffer was full, because a full buffer skips the read;
- recovery took the lanes one at a time, so the first big lane filled the
  buffer and every other connector waited behind it.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig

LANES = ["record-events.0", "record-events.1", "record-events.2", "record-events.3"]


def _fields(connector_id: str, record_id: str) -> dict[str, str]:
    payload = {
        "recordId": record_id,
        "orgId": "org-a",
        "connectorId": connector_id,
        "virtualRecordId": f"vr-{record_id}",
        "extension": "txt",
        "mimeType": "text/plain",
    }
    return {
        "value": json.dumps(
            {"eventType": "newRecord", "payload": payload, "requestId": record_id}
        )
    }


def _consumer(**fair: object) -> IndexingRedisStreamsConsumer:
    defaults: dict[str, object] = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "max_buffered_messages": 8,
        "max_per_entity_messages": 8,
    }
    defaults.update(fair)
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("test_redis_pending_recovery"),
        RedisStreamsConfig(
            host="localhost",
            port=6379,
            block_ms=100,
            batch_size=10,
            client_id="c",
            group_id="g",
            topics=list(LANES),
        ),
        fair_scheduler_config=FairSchedulerConfig(**defaults),
    )
    consumer.redis = AsyncMock()
    consumer.redis.xclaim = AsyncMock(return_value=[])
    consumer.redis.xinfo_consumers = AsyncMock(return_value=[])
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    consumer._should_dead_letter = AsyncMock(return_value=False)
    consumer.running = True
    return consumer


def _buffered_connectors(consumer: IndexingRedisStreamsConsumer) -> set[str]:
    return {
        json.loads(fields["value"])["payload"]["connectorId"]
        for _key, (_s, _m, fields, _p, _t) in consumer._scheduler.drain_all()
    }


class _Dispatched:
    """Stands in for _start_processing_task and records what was started."""

    def __init__(self) -> None:
        self.connectors: list[str] = []

    async def __call__(
        self, stream, message_id, fields, parsed=None, slot_key=None
    ) -> None:
        self.connectors.append(json.loads(fields["value"])["payload"]["connectorId"])


class TestAgedEntriesStillDispatch:
    async def test_entries_past_the_dwell_budget_are_dispatched_while_another_lane_stays_busy(
        self,
    ) -> None:
        """Connector A's entries wait longer than the dwell budget while
        connector B's lane never stops producing. They are dispatched, not
        released into a pending list nothing reads again."""
        consumer = _consumer(max_dwell_seconds=0.0, max_buffered_messages=4)
        for i in range(2):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                LANES[1], f"1-{i}", _fields("a", f"a-{i}")
            )

        sequence = iter(range(10_000))

        async def busy_lane(*_args: object, **_kwargs: object) -> list:
            return [
                (
                    LANES[0],
                    [
                        (f"2-{n}", _fields("b", f"b-{n}"))
                        for n in (next(sequence), next(sequence))
                    ],
                )
            ]

        consumer._xreadgroup_grouped = AsyncMock(side_effect=busy_lane)
        consumer.redis.xpending = AsyncMock(return_value={"pending": 0, "consumers": []})
        dispatched = _Dispatched()
        consumer._start_processing_task = dispatched
        for _ in range(6):
            await consumer._IndexingRedisStreamsConsumer__read_phase()
            await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert dispatched.connectors.count("a") == 2


class TestStrandedEntriesComeBackWhileReading:
    async def test_a_full_buffer_gives_way_to_a_lane_it_holds_nothing_from(
        self,
    ) -> None:
        """The buffer is full of connector B, B's lane keeps producing, and
        connector A's entries sit in this consumer's pending list unheld (a
        restart or an earlier release left them there). A comes back."""
        consumer = _consumer(max_buffered_messages=4)
        for i in range(4):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                LANES[0], f"2-{i}", _fields("b", f"b-{i}")
            )
        stranded = {"1-0": _fields("a", "a-0"), "1-1": _fields("a", "a-1")}

        async def summary(stream: str, _group: str) -> dict:
            own = 4 if stream == LANES[0] else (2 if stream == LANES[1] else 0)
            return {
                "pending": own,
                "consumers": [{"name": consumer.consumer_name, "pending": own}] if own else [],
            }

        async def pending_range(stream: str, *_args: object, **_kwargs: object) -> list:
            if stream == LANES[1]:
                return [{"message_id": m} for m in stranded]
            return []

        async def xrange(stream: str, min: str, max: str, count: int) -> list:  # noqa: A002
            return [(min, stranded[min])] if min in stranded else []

        consumer.redis.xpending = AsyncMock(side_effect=summary)
        consumer.redis.xpending_range = AsyncMock(side_effect=pending_range)
        consumer.redis.xrange = AsyncMock(side_effect=xrange)
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        assert "a" in _buffered_connectors(consumer)


class TestRecoveryTakesTheLanesInTurn:
    async def test_a_restart_behind_one_big_lane_recovers_every_lane(self) -> None:
        """Entries a previous process left pending: thousands in lane 0,
        a few in each other lane. The startup drain fills the buffer with
        every lane's entries, not lane 0's alone."""
        consumer = _consumer(max_buffered_messages=8, max_per_entity_messages=8)
        served: dict[str, int] = dict.fromkeys(LANES, 0)
        small = dict.fromkeys(LANES[1:], 3)

        async def xautoclaim(
            stream: str, _group: str, _name: str, min_idle_time: int, start_id: str, count: int
        ) -> tuple:
            if stream == LANES[0]:
                base = served[stream]
                served[stream] += count
                entries = [
                    (f"0-{base + i}", _fields("big", f"big-{base + i}"))
                    for i in range(count)
                ]
                return (f"0-{base + count}", entries, [])
            left = small[stream] - served[stream]
            take = min(count, left)
            start = served[stream]
            served[stream] += take
            connector = f"small-{stream[-1]}"
            entries = [
                (f"{stream[-1]}-{start + i}", _fields(connector, f"{connector}-{start + i}"))
                for i in range(take)
            ]
            return ("0-0" if served[stream] >= small[stream] else f"x-{served[stream]}", entries, [])

        consumer.redis.xautoclaim = AsyncMock(side_effect=xautoclaim)
        consumer.redis.xpending_range = AsyncMock(return_value=[])

        await consumer._drain_pending()

        assert _buffered_connectors(consumer) == {"big", "small-1", "small-2", "small-3"}


class TestLivePeersKeepTheirEntries:
    async def test_recovery_does_not_take_what_a_live_peer_holds(self) -> None:
        """A peer replica's held entries are pending under its name. Its
        ownership refresh keeps them under the idle-claim window, so
        XAUTOCLAIM returns nothing, and this consumer gives up none of its
        own entries trying."""
        consumer = _consumer(max_buffered_messages=2)
        for i in range(2):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                LANES[0], f"2-{i}", _fields("b", f"b-{i}")
            )
        consumer.redis.xpending = AsyncMock(
            return_value={"pending": 5, "consumers": [{"name": "peer", "pending": 5}]}
        )
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))

        await consumer._IndexingRedisStreamsConsumer__recover_while_reading()

        assert consumer._scheduler.pending_count == 2
        for call in consumer.redis.xautoclaim.await_args_list:
            assert call.kwargs["min_idle_time"] == consumer.config.claim_min_idle_ms

    async def test_recovery_runs_at_most_once_a_second(self) -> None:
        consumer = _consumer()
        consumer.redis.xpending = AsyncMock(return_value={"pending": 0, "consumers": []})
        await consumer._IndexingRedisStreamsConsumer__recover_while_reading()
        await consumer._IndexingRedisStreamsConsumer__recover_while_reading()
        assert consumer.redis.xpending.await_count == len(LANES)



class TestInFlightEntriesStayOwned:
    async def test_ownership_refresh_covers_records_being_indexed(self) -> None:
        """A record can take longer than the claim window to index. Its
        entry is refreshed like a buffered one, so a peer's recovery cannot
        claim it mid-flight."""
        consumer = _consumer()
        consumer._mark_in_flight("1-7", LANES[1])

        await consumer._IndexingRedisStreamsConsumer__refresh_held_ownership()

        refreshed = {
            message_id
            for call in consumer.redis.xclaim.await_args_list
            for message_id in call.kwargs["message_ids"]
        }
        assert "1-7" in refreshed


class TestUnparseableEntriesDoNotPinALane:
    @staticmethod
    def _stranded(consumer, by_lane: dict[str, dict[str, dict[str, str]]]) -> None:
        async def summary(stream: str, _group: str) -> dict:
            own = len(by_lane.get(stream, {}))
            return {
                "pending": own,
                "consumers": [{"name": consumer.consumer_name, "pending": own}] if own else [],
            }

        async def pending_range(stream: str, *_args: object, **_kwargs: object) -> list:
            return [{"message_id": m} for m in by_lane.get(stream, {})]

        async def xrange(stream: str, min: str, max: str, count: int) -> list:  # noqa: A002
            entry = by_lane.get(stream, {}).get(min)
            return [(min, entry)] if entry is not None else []

        async def ack(stream: str, message_id: str) -> None:
            by_lane.get(stream, {}).pop(message_id, None)

        consumer.redis.xpending = AsyncMock(side_effect=summary)
        consumer.redis.xpending_range = AsyncMock(side_effect=pending_range)
        consumer.redis.xrange = AsyncMock(side_effect=xrange)
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        consumer._ack_message = AsyncMock(side_effect=ack)

    async def test_a_bad_entry_is_acknowledged_and_the_one_behind_it_recovered(
        self,
    ) -> None:
        consumer = _consumer(max_buffered_messages=4)
        by_lane = {
            LANES[0]: {"0-0": _fields("c0", "c0-0")},
            LANES[1]: {"1-0": {"value": "{not json"}, "1-1": _fields("a", "a-1")},
            LANES[2]: {"2-0": _fields("c2", "c2-0")},
            LANES[3]: {"3-0": _fields("c3", "c3-0")},
        }
        self._stranded(consumer, by_lane)

        for _ in range(3):
            consumer._last_recovery = 0.0
            await consumer._IndexingRedisStreamsConsumer__recover_while_reading()

        consumer._ack_message.assert_any_await(LANES[1], "1-0")
        assert consumer._IndexingRedisStreamsConsumer__already_held("1-1")

    async def test_room_made_for_an_entry_that_did_not_need_it_is_given_back(
        self,
    ) -> None:
        """The buffer is full of connector B; the starved lane's entry turns
        out to be unparseable and is acknowledged. B keeps its place."""
        consumer = _consumer(max_buffered_messages=2)
        for i in range(2):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                LANES[0], f"2-{i}", _fields("b", f"b-{i}")
            )
        self._stranded(consumer, {LANES[1]: {"1-0": {"value": "{not json"}}})

        await consumer._IndexingRedisStreamsConsumer__recover_while_reading()

        consumer._ack_message.assert_any_await(LANES[1], "1-0")
        assert consumer._scheduler.pending_count == 2
        assert all(
            consumer._IndexingRedisStreamsConsumer__already_held(m) for m in ("2-0", "2-1")
        )
