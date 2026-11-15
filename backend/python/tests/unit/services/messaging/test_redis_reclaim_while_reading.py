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
import math
import random
from logging import getLogger
from unittest.mock import AsyncMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytest.importorskip("fakeredis.aioredis")

from fakeredis.model import _stream as fake_stream

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
RETRY_BATCH = 10  # _RECOVERY_BATCH


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


async def _fifo_consumer(provider: FakeRedisConnectionProvider) -> IndexingRedisStreamsConsumer:
    """The same consumer without fair scheduling: records start as they are taken."""
    consumer = IndexingRedisStreamsConsumer(
        getLogger("test_reclaim_while_reading_fifo"),
        RedisStreamsConfig(
            topics=[LANE, OTHER_LANE], group_id=GROUP, client_id="live-fifo", claim_min_idle_ms=CLAIM_MS,
            stopped_consumer_after_ms=STOPPED_MS,
        ),
        provider=provider,
    )
    consumer.redis = provider.create_client()
    consumer.running = True
    consumer._start_processing_task = AsyncMock()
    return consumer


def _stopped_for(consumer: IndexingRedisStreamsConsumer) -> float:
    return consumer.stopped_consumer_after_ms() / 1000 + 0.1


async def _left_by_a_stopped_consumer(consumer, count: int, name: str = "replaced") -> None:
    for i in range(count):
        await consumer.redis.xadd(LANE, _fields(f"old-{i}"))
    await consumer.redis.xreadgroup(GROUP, name, {LANE: ">"}, count=count)
    await asyncio.sleep(_stopped_for(consumer))


async def _a_live_peer(consumer: IndexingRedisStreamsConsumer) -> None:
    """Another consumer in the group, holding one entry on the lane the tests do not count. Holding nothing, it would
    stop counting as a peer once silent past the claim time (20 ms), which a slow runner reaches during setup."""
    await consumer.redis.xadd(OTHER_LANE, _fields("peer's", "conn-peer"))
    await consumer.redis.xreadgroup(GROUP, "live-peer", {OTHER_LANE: ">"}, count=1)


async def _look(consumer: IndexingRedisStreamsConsumer) -> None:
    consumer._last_reclaim = 0.0
    await consumer._IndexingRedisStreamsConsumer__reclaim_while_reading()


async def _read_pass(consumer: IndexingRedisStreamsConsumer) -> None:
    consumer._last_ownership_refresh = 0.0
    await consumer._IndexingRedisStreamsConsumer__read_phase()


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
        self, consumer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = {"ms": 1_000_000}
        monkeypatch.setattr(fake_stream, "current_time", lambda: clock["ms"])
        await consumer.redis.xadd(LANE, _fields("long-parse"))
        read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"})
        message_id = read[0][1][0][0]
        consumer._mark_in_flight(message_id, LANE)
        clock["ms"] += CLAIM_MS * 3
        consumer._last_ownership_refresh = 0.0

        await consumer._IndexingRedisStreamsConsumer__refresh_held_ownership()

        entry = (await _owned(consumer, consumer.consumer_name))[0]
        assert entry["time_since_delivered"] == 0
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
        """Before Redis 7.0, XCLAIM replies to a trimmed entry with an empty
        slot (no id, no body) and leaves it pending under the claimer. Kept, it
        would stay at the lane's head under a live owner; and the entries with
        a body in the same page must still be handed on."""
        await _left_by_a_stopped_consumer(consumer, 2)
        listed = await _owned(consumer, "replaced")
        trimmed, kept = listed[0]["message_id"], listed[1]["message_id"]
        real_xclaim = consumer.redis.xclaim

        async def pre_7_xclaim(*args, **kwargs):  # noqa: ANN202
            # What redis-py makes of Redis 6.2's empty slot for a trimmed entry.
            reply = await real_xclaim(*args, **kwargs)
            return [(None, None) if m == trimmed else (m, f) for m, f in reply]

        consumer.redis.xclaim = pre_7_xclaim

        await _look(consumer)

        mine = [e["message_id"] for e in await _owned(consumer, consumer.consumer_name)]
        assert mine == [kept]
        assert consumer._scheduler.pending_count == 1


class TestAnEntryRecoveryCouldNotHandOn:
    async def test_it_is_offered_again_without_another_delivery(self, consumer) -> None:
        """Claimed from a stopped consumer, then the check before buffering it
        failed: the entry is pending under this consumer and held by no one.
        Beside another live consumer nothing else would take it back."""
        await _left_by_a_stopped_consumer(consumer, 2)
        real_check = consumer._should_dead_letter
        failed: set[str] = set()

        async def fails_once(topic, message_id, *args, **kwargs):  # noqa: ANN202
            if message_id not in failed:
                failed.add(message_id)
                raise ConnectionError("retry store unreachable")
            return await real_check(topic, message_id, *args, **kwargs)

        consumer._should_dead_letter = fails_once

        await _look(consumer)
        assert consumer._scheduler.pending_count == 0
        consumer._last_unhanded_retry = 0.0
        consumer._last_reclaim = float("inf")
        await _read_pass(consumer)

        assert consumer._scheduler.pending_count == 2
        mine = await _owned(consumer, consumer.consumer_name)
        assert {entry["times_delivered"] for entry in mine} == {2}


class TestRetryCapacity:
    async def test_no_more_is_claimed_once_the_retry_list_is_full(
        self, consumer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An entry claimed and then not handed on is remembered for another
        try; past the list's bound one would be forgotten while still pending
        here, so claiming stops first."""
        from app.services.messaging.redis_streams import indexing_consumer as module

        monkeypatch.setattr(module, "_UNHANDED_LIMIT", 2)
        consumer._unhanded = {(LANE, "1-0"): None, (LANE, "1-1"): None}
        consumer._last_unhanded_retry = float("inf")
        await _left_by_a_stopped_consumer(consumer, 3)

        await _look(consumer)

        assert len(await _owned(consumer, "replaced")) == 3


class TestATrimmedEntryWhoseAcknowledgementFailed:
    async def test_it_is_acknowledged_on_a_later_read_pass_with_a_full_buffer(self, consumer) -> None:
        await _left_by_a_stopped_consumer(consumer, 2)
        trimmed = (await _owned(consumer, "replaced"))[0]["message_id"]
        real_xclaim, real_xack = consumer.redis.xclaim, consumer.redis.xack
        failures = {"left": 1}

        async def pre_7_xclaim(*args, **kwargs):  # noqa: ANN202
            reply = await real_xclaim(*args, **kwargs)
            return [(None, None) if m == trimmed else (m, f) for m, f in reply]

        async def flaky_xack(*args, **kwargs):  # noqa: ANN202
            if failures["left"]:
                failures["left"] -= 1
                raise ConnectionError("lost the connection")
            return await real_xack(*args, **kwargs)

        consumer.redis.xclaim = pre_7_xclaim
        consumer.redis.xack = flaky_xack

        await _look(consumer)
        assert (LANE, trimmed) in consumer._unacked_gone
        await consumer.redis.xdel(LANE, trimmed)
        await _fill_with_new_events(consumer, BUFFER - consumer._scheduler.pending_count)
        consumer._last_unhanded_retry = 0.0
        # The real read pass, which returns early on a full buffer.
        await _read_pass(consumer)

        mine = [e["message_id"] for e in await _owned(consumer, consumer.consumer_name)]
        assert trimmed not in mine
        assert (LANE, trimmed) not in consumer._unacked_gone


async def _untracked_under(consumer) -> set[str]:
    """Ids pending under this consumer that it neither holds nor remembers."""
    remembered = {**consumer._unhanded, **consumer._unacked_gone}
    tracked = {m for _t, m in remembered} | {m for _s, m in consumer._held_entries}
    return {e["message_id"] for e in await _owned(consumer, consumer.consumer_name)} - tracked


class TestTheRetryListNeverLosesAnEntry:
    async def test_a_drain_whose_handoffs_all_fail_claims_no_more_than_the_list_holds(
        self, consumer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.messaging.redis_streams import indexing_consumer as module

        monkeypatch.setattr(module, "_UNHANDED_LIMIT", 3)

        async def always_fails(*_args, **_kwargs):  # noqa: ANN202
            raise ConnectionError("retry store unreachable")

        consumer._should_dead_letter = always_fails
        await _left_by_a_stopped_consumer(consumer, 6)

        await consumer._drain_pending()

        assert len(consumer._unhanded) == 3
        assert await _untracked_under(consumer) == set()
        assert len(await _owned(consumer, "replaced")) == 3

    async def test_failed_checks_of_bodiless_entries_across_owners_stay_within_it(
        self, consumer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.messaging.redis_streams import indexing_consumer as module

        monkeypatch.setattr(module, "_UNHANDED_LIMIT", 2)
        for owner in ("replaced-a", "replaced-b"):
            for i in range(2):
                await consumer.redis.xadd(LANE, _fields(f"{owner}-{i}"))
            await consumer.redis.xreadgroup(GROUP, owner, {LANE: ">"}, count=2)
        await asyncio.sleep(_stopped_for(consumer))
        real_xclaim, real_pending = consumer.redis.xclaim, consumer.redis.xpending_range

        async def pre_7_xclaim(*args, **kwargs):  # noqa: ANN202
            return [(None, None) for _ in await real_xclaim(*args, **kwargs)]

        async def check_fails(*args, **kwargs):  # noqa: ANN202
            if kwargs.get("min") not in (None, "-"):
                raise ConnectionError("lost the connection")
            return await real_pending(*args, **kwargs)

        consumer.redis.xclaim = pre_7_xclaim
        consumer.redis.xpending_range = check_fails

        await _look(consumer)

        consumer.redis.xpending_range = real_pending
        assert len(consumer._unhanded) + len(consumer._unacked_gone) == 2
        assert await _untracked_under(consumer) == set()


class TestRetryOrder:
    async def _remember_with_bodies(self, consumer, count: int) -> list[str]:
        """Entries with a body, pending here, held by no one, and remembered;
        beside a live peer, so read-time own-pending recovery stays off and
        only the retry can bring them back."""
        await _a_live_peer(consumer)
        # With room in the buffer, own-pending recovery offers them too, beside a peer or not, and it runs first; a
        # slow runner that puts a second between passes lets it run on each, and the order checks then pass without
        # the retry.
        consumer._IndexingRedisStreamsConsumer__recover_while_reading = AsyncMock()
        for i in range(count):
            await consumer.redis.xadd(LANE, _fields(f"waiting-{i}", f"conn-{i}"))
        read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"}, count=count)
        ids = [message_id for _s, entries in read for message_id, _f in entries]
        for message_id in ids:
            consumer._IndexingRedisStreamsConsumer__remember_unhanded(LANE, message_id)
        return ids

    async def test_a_bodiless_id_is_acknowledged_behind_entries_waiting_for_room(self, consumer) -> None:
        await self._remember_with_bodies(consumer, 10)
        await consumer.redis.xadd(LANE, _fields("trimmed"))
        read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"})
        trimmed = read[0][1][0][0]
        await consumer.redis.xdel(LANE, trimmed)
        consumer._IndexingRedisStreamsConsumer__remember_unhanded(LANE, trimmed, gone=True)
        await _fill_with_new_events(consumer, BUFFER)
        consumer._last_unhanded_retry = 0.0

        await _read_pass(consumer)

        mine = {e["message_id"] for e in await _owned(consumer, consumer.consumer_name)}
        assert trimmed not in mine
        assert len(consumer._unhanded) == 10
        assert consumer._unacked_gone == {}

    async def test_with_room_for_one_every_entry_is_offered_over_successive_passes(self, consumer) -> None:
        ids = await self._remember_with_bodies(consumer, 12)
        await _fill_with_new_events(consumer, BUFFER - 1)
        offered: list[str] = []

        for _ in range(24):
            if len(set(offered)) == len(ids):
                break
            await consumer.redis.xreadgroup(GROUP, "live-peer", {LANE: ">"}, count=1)
            consumer._last_unhanded_retry = 0.0
            await _read_pass(consumer)
            for stream, message_id in _empty_the_buffer(consumer):
                if stream == LANE:
                    offered.append(message_id)
                    await consumer.redis.xack(LANE, GROUP, message_id)
            await _fill_with_new_events(consumer, BUFFER - 1 - consumer._scheduler.pending_count)

        assert sorted(offered) == sorted(ids)  # each once, none left behind


class TestEveryRememberedIdIsExamined:
    """However the remembered ids behave -- trimmed, waiting for room, or
    failing their check -- none can keep others from being examined."""

    @staticmethod
    async def _owned_ids(consumer, count: int) -> list[str]:
        for i in range(count):
            await consumer.redis.xadd(LANE, _fields(f"r-{i}", f"conn-{i}"))
        read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"}, count=count)
        return [message_id for _s, entries in read for message_id, _f in entries]

    @staticmethod
    def _failing_checks(consumer, failing: set[str], examined: list[str]) -> None:
        real = consumer.redis.xpending_range

        async def xpending_range(*args, **kwargs):  # noqa: ANN202
            message_id = kwargs.get("min")
            if message_id not in (None, "-") and message_id == kwargs.get("max"):
                examined.append(message_id)
                if message_id in failing:
                    raise ConnectionError("lost the connection")
            return await real(*args, **kwargs)

        consumer.redis.xpending_range = xpending_range

    async def _pass(self, consumer) -> None:
        await consumer.redis.xreadgroup(GROUP, "live-peer", {LANE: ">"}, count=1)
        consumer._last_unhanded_retry = 0.0
        consumer._last_recovery = 0.0
        if consumer._scheduler is not None:
            await _read_pass(consumer)
        else:
            # What the FIFO read loop runs before it reads.
            await consumer._IndexingRedisStreamsConsumer__retry_unhanded_when_due()
            await consumer._IndexingRedisStreamsConsumer__recover_while_reading()

    @pytest.mark.parametrize("path", ["fair", "fifo"])
    @pytest.mark.parametrize("seed", range(12))
    async def test_within_ceil_n_over_batch_passes_whatever_the_mix(
        self, consumer, provider, seed: int, path: str
    ) -> None:
        if path == "fifo":
            consumer = await _fifo_consumer(provider)
        rng = random.Random(seed)
        await _a_live_peer(consumer)
        n = rng.randint(1, 35)
        ids = await self._owned_ids(consumer, n)
        kinds = {message_id: rng.choice(("trimmed", "body", "failing")) for message_id in ids}
        for message_id, kind in kinds.items():
            if kind == "trimmed":
                await consumer.redis.xdel(LANE, message_id)
            consumer._IndexingRedisStreamsConsumer__remember_unhanded(LANE, message_id)
        examined: list[str] = []
        self._failing_checks(consumer, {m for m, k in kinds.items() if k == "failing"}, examined)
        if path == "fair":
            await _fill_with_new_events(consumer, BUFFER)

        for _ in range(math.ceil(n / RETRY_BATCH)):
            await self._pass(consumer)

        assert set(examined) == set(ids)
        mine = {e["message_id"] for e in await _owned(consumer, consumer.consumer_name)}
        assert not {m for m, k in kinds.items() if k == "trimmed"} & mine

    async def test_a_trimmed_entry_behind_ten_failing_checks_is_acknowledged(self, consumer) -> None:
        await _a_live_peer(consumer)
        ids = await self._owned_ids(consumer, 11)
        failing, trimmed = set(ids[:10]), ids[10]
        await consumer.redis.xdel(LANE, trimmed)
        for message_id in ids:
            consumer._IndexingRedisStreamsConsumer__remember_unhanded(LANE, message_id)
        self._failing_checks(consumer, failing, [])
        await _fill_with_new_events(consumer, BUFFER)

        for _ in range(2):
            await self._pass(consumer)

        assert trimmed not in {e["message_id"] for e in await _owned(consumer, consumer.consumer_name)}
        assert (LANE, trimmed) not in consumer._unhanded


class TestAPeersClaimBeforeTheDeadLetterCheck:
    async def test_a_transferred_entry_past_the_delivery_backstop_is_not_acknowledged(
        self, consumer
    ) -> None:
        """Own-pending recovery found the entry; a peer claimed it before the
        dead-letter check (its claims taking the delivery count past the
        backstop). The check must not acknowledge the peer's entry."""
        await consumer.redis.xreadgroup(GROUP, "live-peer", {LANE: ">"}, count=1)
        await consumer.redis.xadd(LANE, _fields("transferred"))
        read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"})
        message_id = read[0][1][0][0]
        scan = consumer._IndexingRedisStreamsConsumer__own_unheld_pending_payloads

        async def scan_then_peer_claims(*args, **kwargs):  # noqa: ANN202
            found = await scan(*args, **kwargs)
            await consumer.redis.xclaim(
                LANE, GROUP, "live-peer", min_idle_time=0, message_ids=[message_id], retrycount=50
            )
            return found

        consumer._IndexingRedisStreamsConsumer__own_unheld_pending_payloads = scan_then_peer_claims
        consumer._last_recovery = 0.0

        await _read_pass(consumer)

        peers = await _owned(consumer, "live-peer")
        assert [e["message_id"] for e in peers if e["message_id"] == message_id] == [message_id]
        assert consumer._scheduler.pending_count == 0


class TestOwnPendingScanProgress:
    """A scan of this consumer's pending list carries on where the last one
    stopped, whatever stopped it, and goes back to the head only once it has
    reached the end. Entries at the head that never leave (bodiless ones whose
    acknowledgement keeps failing) cannot keep one behind them from being
    found."""

    @staticmethod
    async def _scan_until_found(
        stuck: int, page_size: int, pages: int, limit: int, scans_allowed: int,
        full_retry_list: bool = False,
    ) -> int | None:
        from app.services.messaging.redis_streams import indexing_consumer as module

        provider = FakeRedisConnectionProvider(is_cluster=False)
        consumer = await _fifo_consumer(provider)
        await consumer.redis.xgroup_create(LANE, GROUP, id="0", mkstream=True)
        consumer.config.batch_size = 1
        for i in range(stuck + 1):
            await consumer.redis.xadd(LANE, _fields(f"r-{i}", f"conn-{i}"))
        read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"})
        ids = [message_id for _s, entries in read for message_id, _f in entries]
        target = ids[-1]
        for message_id in ids[:-1]:
            await consumer.redis.xdel(LANE, message_id)

        async def ack_keeps_failing(*_args, **_kwargs) -> None:
            return None

        consumer._IndexingRedisStreamsConsumer__ack_claimed_without_body = ack_keeps_failing
        if full_retry_list:
            consumer._unhanded = {
                (OTHER_LANE, f"{i}-0"): None for i in range(1, module._UNHANDED_LIMIT + 1)
            }
        consumer._IndexingRedisStreamsConsumer__pending_scan_pages = lambda _topic: pages
        scan = consumer._IndexingRedisStreamsConsumer__own_unheld_pending_payloads
        try:
            for attempt in range(1, scans_allowed + 1):
                found = await scan(LANE, limit, ignore_budget=True)
                if target in {message_id for message_id, _f in found}:
                    return attempt
            return None
        finally:
            await consumer.redis.aclose()

    @settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        stuck=st.integers(min_value=0, max_value=40),
        page_size=st.integers(min_value=1, max_value=6),
        pages=st.integers(min_value=1, max_value=4),
        limit=st.integers(min_value=1, max_value=5),
    )
    def test_an_entry_behind_ones_that_never_leave_is_found_in_bounded_scans(
        self, monkeypatch, stuck: int, page_size: int, pages: int, limit: int
    ) -> None:
        from app.services.messaging.redis_streams import indexing_consumer as module

        monkeypatch.setattr(module, "_PENDING_SCAN_PAGE", page_size)
        bound = math.ceil((stuck + 1) / (page_size * pages)) + 1
        found_on = asyncio.run(self._scan_until_found(stuck, page_size, pages, limit, bound))
        assert found_on is not None, f"not found within {bound} scans"

    async def test_a_full_retry_list_and_more_than_a_scan_of_failing_acks_ahead(
        self, provider
    ) -> None:
        """The case reported: more than one scan's page budget of trimmed
        entries whose acknowledgement keeps failing, a full retry list, and a
        recoverable entry behind them."""
        from app.services.messaging.redis_streams import indexing_consumer as module

        page_size, pages = 2, 2
        stuck = 3 * page_size * pages
        original = module._PENDING_SCAN_PAGE
        module._PENDING_SCAN_PAGE = page_size
        try:
            found_on = await self._scan_until_found(
                stuck, page_size, pages, 1, 5, full_retry_list=True
            )
        finally:
            module._PENDING_SCAN_PAGE = original
        assert found_on is not None and found_on <= math.ceil((stuck + 1) / (page_size * pages)) + 1


class TestAScanThatRaisesPartWay:
    @staticmethod
    async def _owned_with_bodies(consumer, count: int) -> list[str]:
        await consumer.redis.xgroup_create(LANE, GROUP, id="0", mkstream=True)
        for i in range(count):
            await consumer.redis.xadd(LANE, _fields(f"r-{i}", f"conn-{i}"))
        read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"})
        return [message_id for _s, entries in read for message_id, _f in entries]

    @staticmethod
    def _xrange_raises_for(consumer, failing: str, times: int) -> None:
        real = consumer.redis.xrange
        left = {"n": times}

        async def xrange(topic, *args, **kwargs):  # noqa: ANN202
            if kwargs.get("min") == failing and left["n"] > 0:
                left["n"] -= 1
                raise ConnectionError("lost the connection")
            return await real(topic, *args, **kwargs)

        consumer.redis.xrange = xrange

    async def test_entries_read_before_the_error_are_returned_and_the_failing_one_comes_next(
        self, provider
    ) -> None:
        consumer = await _fifo_consumer(provider)
        ids = await self._owned_with_bodies(consumer, 5)
        self._xrange_raises_for(consumer, ids[2], times=1)
        scan = consumer._IndexingRedisStreamsConsumer__own_unheld_pending_payloads

        first = await scan(LANE, 10, ignore_budget=True)
        second = await scan(LANE, 10, ignore_budget=True)

        assert [m for m, _f in first] == ids[:2]
        assert [m for m, _f in second][:3] == ids[2:]

    async def test_an_entry_that_keeps_raising_first_does_not_hide_the_ones_behind(
        self, provider
    ) -> None:
        consumer = await _fifo_consumer(provider)
        ids = await self._owned_with_bodies(consumer, 4)
        self._xrange_raises_for(consumer, ids[0], times=1000)
        scan = consumer._IndexingRedisStreamsConsumer__own_unheld_pending_payloads

        with pytest.raises(ConnectionError):
            await scan(LANE, 10, ignore_budget=True)
        second = await scan(LANE, 10, ignore_budget=True)

        assert [m for m, _f in second] == ids[1:]
