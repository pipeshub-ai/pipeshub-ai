"""The Redis Streams indexing consumer takes over what a stopped consumer left
pending while it reads, not only once every lane has gone quiet.

Runs against an in-memory Redis with streams, consumer groups and the pending
list. Before, entries a replaced process held were claimed only by the
startup drain (one buffer's worth) and the idle drain, which a steady trickle
of new events never lets run: they stayed pending for good.
"""
from __future__ import annotations

import asyncio
import json
from logging import getLogger
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

GROUP = "records_consumer_group"
LANE = "record-events.0"
OTHER_LANE = "record-events.1"
CLAIM_MS = 20
STOPPED_MS = 200
BUFFER = 8
HEADROOM = BUFFER // 4


def _fields(record_id: str, connector_id: str = "conn-a") -> dict[str, str]:
    payload = {"recordId": record_id, "orgId": "org-1", "connectorId": connector_id}
    return {"value": json.dumps({"eventType": "newRecord", "payload": payload})}


@pytest.fixture
def provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=False)


@pytest.fixture
async def consumer(provider: FakeRedisConnectionProvider) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        getLogger("test_reclaim_while_reading"),
        RedisStreamsConfig(
            topics=[LANE, OTHER_LANE], group_id=GROUP, client_id="live", claim_min_idle_ms=CLAIM_MS,
            stopped_consumer_after_ms=STOPPED_MS,
        ),
        fair_scheduler_config=FairSchedulerConfig(
            enabled=True,
            key_fields=("orgId", "connectorId"),
            max_buffered_messages=BUFFER,
            max_per_entity_messages=BUFFER,
        ),
        provider=provider,
    )
    consumer.redis = provider.create_client()
    consumer.running = True
    for lane in (LANE, OTHER_LANE):
        await consumer.redis.xgroup_create(lane, GROUP, id="0", mkstream=True)
    return consumer


def _stopped_for(consumer: IndexingRedisStreamsConsumer) -> float:
    return consumer.stopped_consumer_after_ms() / 1000 + 0.1


async def _left_by_a_stopped_consumer(consumer, count: int, name: str = "replaced") -> None:
    for i in range(count):
        await consumer.redis.xadd(LANE, _fields(f"old-{i}"))
    await consumer.redis.xreadgroup(GROUP, name, {LANE: ">"}, count=count)
    await asyncio.sleep(_stopped_for(consumer))


async def _look(consumer: IndexingRedisStreamsConsumer) -> None:
    consumer._last_reclaim = 0.0
    await consumer._IndexingRedisStreamsConsumer__reclaim_while_reading()


async def _owned(consumer, name: str, lane: str = LANE) -> list[dict]:
    return await consumer.redis.xpending_range(
        lane, GROUP, min="-", max="+", count=1000, consumername=name
    )


async def _fill_with_new_events(consumer, count: int) -> None:
    for i in range(count):
        await consumer.redis.xadd(OTHER_LANE, _fields(f"new-{i}", "conn-b"))
    read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {OTHER_LANE: ">"}, count=count)
    for _stream, entries in read:
        for message_id, fields in entries:
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(OTHER_LANE, message_id, fields)


def _empty_the_buffer(consumer: IndexingRedisStreamsConsumer) -> list[tuple[str, str]]:
    taken = [(stream, message_id) for _key, (stream, message_id, *_rest) in consumer._scheduler.drain_all()]
    consumer._held_entries.clear()
    return taken


class TestReclaimWhileReading:
    async def test_reclaiming_leaves_room_for_new_events(self, consumer) -> None:
        """A large stopped backlog must not fill the buffer: a full buffer stops
        reading, and every other connector's new events wait behind it."""
        await _left_by_a_stopped_consumer(consumer, 30)

        await _look(consumer)
        await _look(consumer)

        assert len(await _owned(consumer, consumer.consumer_name)) == BUFFER - HEADROOM
        assert consumer._scheduler.pending_count == BUFFER - HEADROOM
        left = await _owned(consumer, "replaced")
        assert len(left) == 30 - (BUFFER - HEADROOM)
        # Not claimed, not counted: XCLAIM counts a delivery on what it returns.
        assert {entry["times_delivered"] for entry in left} == {1}

    async def test_a_full_buffer_claims_nothing(self, consumer) -> None:
        await _fill_with_new_events(consumer, BUFFER)
        await _left_by_a_stopped_consumer(consumer, 10)

        await _look(consumer)

        assert len(await _owned(consumer, "replaced")) == 10
        assert len(await _owned(consumer, consumer.consumer_name)) == 0

    async def test_the_headroom_is_kept_beside_new_events_already_held(self, consumer) -> None:
        await _fill_with_new_events(consumer, 3)
        await _left_by_a_stopped_consumer(consumer, 10)

        await _look(consumer)

        assert len(await _owned(consumer, consumer.consumer_name, LANE)) == BUFFER - HEADROOM - 3
        assert consumer._scheduler.pending_count == BUFFER - HEADROOM

    async def test_everything_comes_back_as_room_frees_and_the_stopped_consumer_is_removed(
        self, consumer
    ) -> None:
        await _left_by_a_stopped_consumer(consumer, 23)
        reclaimed: set[str] = set()

        for _ in range(30):
            await _look(consumer)
            for stream, message_id in _empty_the_buffer(consumer):
                await consumer.redis.xack(stream, GROUP, message_id)
                reclaimed.add(message_id)
            if len(reclaimed) == 23:
                break
        await _look(consumer)

        assert len(reclaimed) == 23
        names = {c["name"] for c in await consumer.redis.xinfo_consumers(LANE, GROUP)}
        assert "replaced" not in names

    async def test_a_live_consumers_idle_entries_are_never_claimed(self, consumer) -> None:
        """An entry a live replica is still processing goes idle past the claim
        window (a long parse). Claiming it would count a delivery each window
        and run it twice; its owner still touches the group, so it is left alone."""
        for i in range(3):
            await consumer.redis.xadd(LANE, _fields(f"busy-{i}"))
        await consumer.redis.xreadgroup(GROUP, "peer", {LANE: ">"}, count=3)
        for _ in range(4):
            await asyncio.sleep(consumer.stopped_consumer_after_ms() / 1000 / 3)
            await consumer.redis.xreadgroup(GROUP, "peer", {LANE: ">"}, count=1)

        await _look(consumer)

        owned = await _owned(consumer, "peer")
        assert len(owned) == 3
        assert {entry["times_delivered"] for entry in owned} == {1}
        assert consumer._scheduler.pending_count == 0

    async def test_this_consumers_entries_in_processing_stay_fresh_without_a_delivery(
        self, consumer
    ) -> None:
        await consumer.redis.xadd(LANE, _fields("long-parse"))
        read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"})
        message_id = read[0][1][0][0]
        consumer._mark_in_flight(message_id, LANE)
        await asyncio.sleep(CLAIM_MS * 3 / 1000)
        consumer._last_ownership_refresh = 0.0

        await consumer._IndexingRedisStreamsConsumer__refresh_held_ownership()

        entry = (await _owned(consumer, consumer.consumer_name))[0]
        assert entry["time_since_delivered"] < CLAIM_MS
        assert entry["times_delivered"] == 1
        claimed = await consumer.redis.xautoclaim(
            LANE, GROUP, "other", min_idle_time=CLAIM_MS, start_id="0-0", count=10
        )
        assert claimed[1] == []

    async def test_a_stopped_consumer_that_still_holds_entries_is_not_removed(self, consumer) -> None:
        await _fill_with_new_events(consumer, BUFFER)
        await _left_by_a_stopped_consumer(consumer, 2)

        await _look(consumer)

        names = {c["name"] for c in await consumer.redis.xinfo_consumers(LANE, GROUP)}
        assert "replaced" in names

    async def test_removal_is_one_check_and_delete_so_a_consumer_that_read_meanwhile_stays(
        self, consumer
    ) -> None:
        await consumer.redis.xadd(LANE, _fields("x"))
        await consumer.redis.xreadgroup(GROUP, "came-back", {LANE: ">"})
        await asyncio.sleep(_stopped_for(consumer))
        consumer._IndexingRedisStreamsConsumer__stopped_owners = AsyncMock(
            return_value=[("came-back", 0)]
        )

        await _look(consumer)

        assert len(await _owned(consumer, "came-back")) == 1

    async def test_a_look_that_finds_nothing_waits_before_the_next(self, consumer) -> None:
        await _look(consumer)
        assert not consumer._reclaim_found
        await _left_by_a_stopped_consumer(consumer, 2)

        await consumer._IndexingRedisStreamsConsumer__reclaim_while_reading()

        assert len(await _owned(consumer, "replaced")) == 2



class TestSmallBuffers:
    @pytest.mark.parametrize("buffer", [1, 2, 3, 4, 2000])
    async def test_reclaim_always_makes_progress(self, consumer, buffer: int) -> None:
        """A quarter kept free must never be the whole buffer: with a buffer of
        one, nothing would ever be taken from a stopped consumer."""
        consumer.fair_scheduler_config = FairSchedulerConfig(
            enabled=True, key_fields=("orgId", "connectorId"),
            max_buffered_messages=buffer, max_per_entity_messages=buffer,
        )
        await _left_by_a_stopped_consumer(consumer, 5)

        await _look(consumer)

        taken = len(await _owned(consumer, consumer.consumer_name))
        assert taken >= 1
        assert taken <= buffer
        assert consumer._scheduler.pending_count <= buffer


class TestOwnRecoveryBesideAnEmptyCrashedConsumer:
    async def test_a_crashed_consumer_holding_nothing_does_not_stop_own_recovery(self, consumer) -> None:
        """Removal of an empty consumer now waits until it has been silent for
        many claim windows; until then it must not switch off the read-time
        recovery of this consumer's own pending entries, which it did not
        before (it was removed after one window)."""
        await consumer.redis.xadd(LANE, _fields("crashed-read-nothing"))
        await consumer.redis.xreadgroup(GROUP, "crashed", {LANE: ">"})
        await consumer.redis.xack(LANE, GROUP, (await _owned(consumer, "crashed"))[0]["message_id"])
        await consumer.redis.xadd(LANE, _fields("mine-unheld"))
        await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"})
        await asyncio.sleep(CLAIM_MS * 2 / 1000)
        consumer._last_recovery = 0.0

        await consumer._IndexingRedisStreamsConsumer__recover_while_reading()

        assert consumer._scheduler.pending_count == 1
        names = {c["name"] for c in await consumer.redis.xinfo_consumers(LANE, GROUP)}
        assert "crashed" in names


class TestTrimmedEntriesOfAStoppedConsumer:
    async def test_a_claim_returned_without_a_body_is_acknowledged_not_kept(self, consumer) -> None:
        """Before Redis 7.0, XCLAIM returns a trimmed entry with no body and
        leaves it pending under the claiming consumer. Kept, it would stay at
        the lane's head under a live owner and hold the sweep back."""
        await _left_by_a_stopped_consumer(consumer, 2)
        listed = await _owned(consumer, "replaced")
        trimmed, kept = listed[0]["message_id"], listed[1]["message_id"]
        real_xclaim = consumer.redis.xclaim

        async def pre_7_xclaim(*args, **kwargs):  # noqa: ANN202
            reply = await real_xclaim(*args, **kwargs)
            return [(m, None if m == trimmed else f) for m, f in reply]

        consumer.redis.xclaim = pre_7_xclaim

        await _look(consumer)

        mine = [e["message_id"] for e in await _owned(consumer, consumer.consumer_name)]
        assert mine == [kept]
        assert consumer._scheduler.pending_count == 1
