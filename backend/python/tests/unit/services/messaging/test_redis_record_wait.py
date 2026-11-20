"""An event whose record is already being worked on waits for that record.

Seen at a customer: during one long reindex the indexer logged "Recovering own
pending message" about 34,000 times in two hours for the same 8 entries. Each
was another event for the record being reindexed. Its delivery found the
record claimed, returned leaving the entry pending but untracked, and
read-time recovery offered it again a second later, for as long as the
reindex ran. These tests drive the real per-message wrapper and recovery
against an in-memory Redis.
"""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
    messaging_env,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.telemetry.modules import scheduling_metrics
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

GROUP = "records_consumer_group"
LANE = "record-events.0"
OTHER_LANE = "record-events.1"
CLAIM_MS = 60_000
BUFFER = 8
LOGGER_NAME = "test_redis_record_wait"
RECOVERING = "Recovering own pending message"

_MODULE = "app.services.messaging.redis_streams.indexing_consumer"
_CONSUMER = "_IndexingRedisStreamsConsumer"


def _fields(record_id: str, connector_id: str = "conn-a") -> dict[str, str]:
    payload = {
        "recordId": record_id,
        "orgId": "org-1",
        "connectorId": connector_id,
        "extension": "txt",
    }
    return {"value": json.dumps({"eventType": "newRecord", "payload": payload})}


class Handler:
    """Records which records it ran, and can be held open to play a long reindex."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def __call__(self, message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
        record_id = str(message.payload["recordId"])
        self.ran.append(record_id)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))


class Harness:
    """A consumer whose dispatches run the real wrapper inline, in order."""

    def __init__(self, consumer: IndexingRedisStreamsConsumer, handler: Handler) -> None:
        self.consumer = consumer
        self.handler = handler
        self.dispatched: list[str] = []
        consumer._start_processing_task = self._run  # type: ignore[method-assign]

    async def _run(
        self,
        stream_name: str,
        message_id: str,
        fields: dict[str, str],
        parsed_message: Any = None,  # noqa: ANN401
        slot_key: Any = None,  # noqa: ANN401
    ) -> None:
        self.dispatched.append(message_id)
        self.consumer._mark_in_flight(message_id, stream_name)
        try:
            await self.consumer._process_message_wrapper(
                stream_name, message_id, fields, None, parsed_message
            )
        finally:
            self.consumer._unmark_in_flight(message_id, stream_name)

    async def read_new(self, lane: str = LANE) -> list[str]:
        """XREADGROUP '>' and hand what came to the scheduler, as the read phase does."""
        read = await self.consumer.redis.xreadgroup(
            GROUP, self.consumer.consumer_name, {lane: ">"}, count=100
        )
        ids: list[str] = []
        for _stream, entries in read or []:
            for message_id, fields in entries:
                ids.append(message_id)
                await getattr(self.consumer, f"{_CONSUMER}__enqueue_message")(lane, message_id, fields)
        return ids

    async def recover(self) -> None:
        self.consumer._last_recovery = 0.0
        await getattr(self.consumer, f"{_CONSUMER}__recover_while_reading")()

    async def dispatch(self) -> None:
        await getattr(self.consumer, f"{_CONSUMER}__dispatch_phase")()

    async def refresh(self) -> None:
        self.consumer._last_ownership_refresh = 0.0
        await getattr(self.consumer, f"{_CONSUMER}__refresh_held_ownership")()

    async def passes(self, count: int) -> None:
        for _ in range(count):
            await self.recover()
            await self.dispatch()

    async def pending_here(self, lane: str = LANE) -> list[str]:
        details = await self.consumer.redis.xpending_range(
            lane, GROUP, min="-", max="+", count=1000, consumername=self.consumer.consumer_name
        )
        return [d["message_id"].decode() if isinstance(d["message_id"], bytes) else d["message_id"] for d in details]


@pytest.fixture
def provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=False)


def _make(provider: FakeRedisConnectionProvider, *, fair: bool = True) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger(LOGGER_NAME),
        RedisStreamsConfig(
            topics=[LANE, OTHER_LANE], group_id=GROUP, client_id="live", claim_min_idle_ms=CLAIM_MS,
        ),
        retry_manager=None,
        producer=None,
        governor=make_test_governor(logger_name=LOGGER_NAME),
        fair_scheduler_config=(
            FairSchedulerConfig(
                enabled=True,
                key_fields=("orgId", "connectorId"),
                max_buffered_messages=BUFFER,
                max_per_entity_messages=BUFFER,
            )
            if fair
            else None
        ),
        provider=provider,
    )
    consumer.redis = provider.create_client()
    consumer.running = True
    consumer.main_loop = asyncio.get_running_loop()
    consumer._pending_message_is_owned = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return consumer


@pytest.fixture
async def harness(provider: FakeRedisConnectionProvider) -> Harness:
    consumer = _make(provider)
    for lane in (LANE, OTHER_LANE):
        await consumer.redis.xgroup_create(lane, GROUP, id="0", mkstream=True)
    handler = Handler()
    consumer.message_handler = handler  # type: ignore[assignment]
    return Harness(consumer, handler)


def _recovering_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(RECOVERING)]


@pytest.mark.asyncio
class TestAnEventForABusyRecordWaits:
    async def test_it_is_not_offered_again_while_the_record_is_busy(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        consumer = harness.consumer
        # The long reindex of r1 holds the record in this process.
        assert consumer._claim_record("r1")
        for _ in range(8):
            await consumer.redis.xadd(LANE, _fields("r1"))
        waiting = await harness.read_new()
        await harness.dispatch()
        assert harness.dispatched == waiting

        await harness.passes(30)

        # Each of the 8 was dispatched once, found its record busy, and waited.
        # Before: offered again on every pass, 8 more dispatches and 8 more
        # "Recovering own pending message" lines a second.
        assert harness.dispatched == waiting
        assert _recovering_lines(caplog) == []
        assert harness.handler.ran == []
        assert sorted(await harness.pending_here()) == sorted(waiting)

    async def test_the_oldest_waiter_runs_when_the_record_is_free_then_the_next(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        consumer = harness.consumer
        assert consumer._claim_record("r1")
        for _ in range(3):
            await consumer.redis.xadd(LANE, _fields("r1"))
        first, second, third = await harness.read_new()
        await harness.dispatch()
        harness.dispatched.clear()

        consumer._release_record("r1")
        await harness.passes(1)

        # Only the oldest was woken: one dispatch, no stampede of the others
        # into a record the first one is about to take. It ran, and its
        # release woke the next, which the following pass offers.
        assert harness.dispatched == [first]
        assert harness.handler.ran == ["r1"]
        assert sorted(await harness.pending_here()) == sorted([second, third])

        await harness.passes(1)
        assert harness.dispatched == [first, second]
        await harness.passes(1)
        assert harness.dispatched == [first, second, third]
        await harness.passes(3)
        assert harness.dispatched == [first, second, third]
        assert harness.handler.ran == ["r1", "r1", "r1"]
        assert await harness.pending_here() == []
        assert len(_recovering_lines(caplog)) == 3

    async def test_other_records_of_the_same_connector_are_not_held_up(self, harness: Harness) -> None:
        consumer = harness.consumer
        assert consumer._claim_record("r1")
        await consumer.redis.xadd(LANE, _fields("r1"))
        await consumer.redis.xadd(LANE, _fields("r2"))
        await consumer.redis.xadd(LANE, _fields("r3"))
        await harness.read_new()
        await harness.dispatch()
        await harness.passes(2)

        assert harness.handler.ran == ["r2", "r3"]


async def _park(harness: Harness, count: int, record_id: str = "r1", lane: str = LANE) -> list[str]:
    """``count`` events for a record this process is already running, each
    dispatched once, so each now waits."""
    consumer = harness.consumer
    if not consumer._is_record_in_flight(record_id):
        assert consumer._claim_record(record_id)
    for _ in range(count):
        await consumer.redis.xadd(lane, _fields(record_id))
    ids = await harness.read_new(lane)
    await harness.dispatch()
    assert all(consumer._record_waits.get((lane, i)) for i in ids)
    return ids


def _expire(consumer: IndexingRedisStreamsConsumer) -> None:
    for wait in consumer._record_waits.values():
        wait.due_at = 0.0


@pytest.mark.asyncio
class TestTheWaitEnds:
    async def test_the_recheck_bound_does_not_re_offer_while_this_process_still_runs_the_record(
        self, harness: Harness
    ) -> None:
        waiting = await _park(harness, 2)
        harness.dispatched.clear()
        _expire(harness.consumer)

        await harness.passes(5)

        assert harness.dispatched == []
        assert sorted(await harness.pending_here()) == sorted(waiting)

    async def test_a_release_that_never_woke_anyone_is_covered_by_the_recheck_bound(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        (waiting,) = await _park(harness, 1)
        harness.dispatched.clear()
        # Freed without the wake a release brings.
        consumer._in_flight_record_ids.discard("r1")
        await harness.passes(2)
        assert harness.dispatched == []

        _expire(consumer)
        await harness.passes(1)

        assert harness.dispatched == [waiting]
        assert harness.handler.ran == ["r1"]
        assert consumer._record_waits == {}

    async def test_a_woken_waiter_acknowledged_elsewhere_hands_the_wake_to_the_next(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        first, second = await _park(harness, 2)
        harness.dispatched.clear()
        consumer._release_record("r1")
        # Gone before recovery offered it: acknowledged by another path.
        await consumer.redis.xack(LANE, GROUP, first)

        await harness.refresh()
        await harness.passes(1)

        assert harness.dispatched == [second]
        assert harness.handler.ran == ["r1"]
        assert consumer._record_waits == {}

    async def test_waits_whose_ownership_cannot_be_confirmed_are_let_go(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
        consumer = harness.consumer
        waiting = await _park(harness, 2)
        for wait in consumer._record_waits.values():
            wait.parked_at = 0.0
        consumer._ownership_confirmed_at.clear()
        consumer.redis.eval = AsyncMock(side_effect=ConnectionError("redis down"))  # type: ignore[method-assign]

        await harness.refresh()

        assert consumer._record_waits == {}
        assert any("Stopped keeping 2 entry(ies) waiting" in r.getMessage() for r in caplog.records)
        # Still pending here: recovery offers them again, and they wait again.
        assert sorted(await harness.pending_here()) == sorted(waiting)

    async def test_refresh_keeps_waiting_entries_from_going_idle(self, harness: Harness) -> None:
        consumer = harness.consumer
        waiting = await _park(harness, 2)
        # Aged far past any runner pause: the assertion below can only pass
        # if the refresh reset the idle time.
        await consumer.redis.xclaim(
            LANE, GROUP, consumer.consumer_name, min_idle_time=0, message_ids=waiting,
            idle=3_600_000, justid=True,
        )

        await harness.refresh()

        details = await consumer.redis.xpending_range(
            LANE, GROUP, min="-", max="+", count=10, consumername=consumer.consumer_name
        )
        assert sorted(d["message_id"].decode() if isinstance(d["message_id"], bytes) else d["message_id"] for d in details) == sorted(waiting)
        assert all(d["time_since_delivered"] < 600_000 for d in details)
        # JUSTID: refreshing counts no delivery, so waiting never nears the backstop.
        assert all(d["times_delivered"] == 1 for d in details)

    async def test_stopping_forgets_every_wait(self, harness: Harness) -> None:
        consumer = harness.consumer
        await _park(harness, 2)

        consumer._stop_worker_thread()

        assert consumer._record_waits == {}


@pytest.mark.asyncio
class TestWaitingEntriesAreNotRecovered:
    async def test_a_full_buffer_gives_up_nothing_for_entries_that_only_wait(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        await _park(harness, 2)
        # Fill the buffer from the other lane, without dispatching.
        for i in range(BUFFER):
            await consumer.redis.xadd(OTHER_LANE, _fields(f"b{i}", "conn-b"))
        await harness.read_new(OTHER_LANE)
        held = set(consumer._held_entries)
        assert len(held) == BUFFER

        await harness.recover()

        assert set(consumer._held_entries) == held
        assert consumer._scheduler.pending_count == BUFFER

    async def test_the_idle_drain_skips_them(self, harness: Harness) -> None:
        consumer = harness.consumer
        await _park(harness, 2)
        harness.dispatched.clear()

        await consumer._drain_pending()
        await harness.dispatch()

        assert harness.dispatched == []

    async def test_without_fair_scheduling_too(self, provider: FakeRedisConnectionProvider) -> None:
        consumer = _make(provider, fair=False)
        for lane in (LANE, OTHER_LANE):
            await consumer.redis.xgroup_create(lane, GROUP, id="0", mkstream=True)
        consumer.message_handler = Handler()  # type: ignore[assignment]
        harness = Harness(consumer, consumer.message_handler)  # type: ignore[arg-type]
        assert consumer._claim_record("r1")
        await consumer.redis.xadd(LANE, _fields("r1"))
        await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"}, count=10)

        await harness.passes(5)

        # Recovered once, found its record busy, and waited.
        assert len(harness.dispatched) == 1
        consumer._release_record("r1")
        await harness.passes(1)
        assert len(harness.dispatched) == 2
        assert harness.handler.ran == ["r1"]


@pytest.mark.asyncio
class TestARecordLeaseHeldElsewhere:
    @staticmethod
    def _lease_held_elsewhere(consumer: IndexingRedisStreamsConsumer) -> list[str]:
        asked: list[str] = []

        async def acquire(pool: str, *_args: Any, **_kwargs: Any) -> bool:  # noqa: ANN401
            asked.append(pool)
            return not pool.startswith("record:")

        consumer.concurrency_manager = MagicMock()
        consumer._acquire_distributed_slot = acquire  # type: ignore[method-assign]
        consumer._release_distributed_slot = AsyncMock()  # type: ignore[method-assign]
        return asked

    async def test_it_waits_a_growing_delay_instead_of_coming_back_every_second(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
        consumer = harness.consumer
        asked = self._lease_held_elsewhere(consumer)
        await consumer.redis.xadd(LANE, _fields("r1"))
        (entry,) = await harness.read_new()

        base = messaging_env.record_lease_wait_seconds
        await harness.dispatch()
        await harness.passes(5)
        # Asked for the record lease once; before, once a pass (each
        # holding an index slot for the whole lease wait).
        assert asked.count("record:r1") == 1
        wait = consumer._record_waits[(LANE, entry)]
        assert wait.on_lease
        assert wait.due_at - wait.parked_at == pytest.approx(base)
        # Its own run of the record ended: the lease wait is not cut short.
        assert not consumer._is_record_in_flight("r1")

        _expire(consumer)
        await harness.passes(1)
        assert asked.count("record:r1") == 2
        wait = consumer._record_waits[(LANE, entry)]
        assert wait.due_at - wait.parked_at == pytest.approx(min(2 * base, 300.0))

        for _ in range(8):
            _expire(consumer)
            await harness.passes(1)
        wait = consumer._record_waits[(LANE, entry)]
        assert wait.due_at - wait.parked_at == pytest.approx(300.0)

        parks = [r for r in caplog.records if "waits for record r1" in r.getMessage()]
        assert [r.levelno for r in parks][:2] == [logging.INFO, logging.DEBUG]
        assert f"its lease is held by another delivery; retried in {base:.0f}s" in parks[0].getMessage()

    async def test_it_runs_once_the_lease_is_free(self, harness: Harness) -> None:
        consumer = harness.consumer
        self._lease_held_elsewhere(consumer)
        await consumer.redis.xadd(LANE, _fields("r1"))
        await harness.read_new()
        await harness.dispatch()

        async def acquire(_pool: str, *_args: Any, **_kwargs: Any) -> bool:  # noqa: ANN401
            return True

        consumer._acquire_distributed_slot = acquire  # type: ignore[method-assign]
        _expire(consumer)
        await harness.passes(1)

        assert harness.handler.ran == ["r1"]
        assert consumer._record_waits == {}
        assert await harness.pending_here() == []


@pytest.mark.asyncio
class TestBoundsAndSignals:
    async def test_past_the_limit_an_entry_is_left_as_before(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
        consumer = harness.consumer
        assert consumer._claim_record("r1")
        with patch(f"{_MODULE}._RECORD_WAIT_LIMIT", 1):
            for _ in range(2):
                await consumer.redis.xadd(LANE, _fields("r1"))
            kept, left = await harness.read_new()
            await harness.dispatch()
            harness.dispatched.clear()
            await harness.passes(1)

        assert list(consumer._record_waits) == [(LANE, kept)]
        assert harness.dispatched == [left]
        assert any("cannot wait for record r1" in r.getMessage() for r in caplog.records)

    async def test_waiting_and_waking_are_logged_once_each_by_record(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        consumer = harness.consumer
        first, second = await _park(harness, 2)
        await harness.passes(10)
        consumer._release_record("r1")

        lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
        parked = [m for m in lines if "waits for record r1" in m]
        assert len(parked) == 2
        assert first in parked[0] and "this process is already working on it" in parked[0]
        assert "(wait 1, 2 entries waiting)" in parked[1]
        assert [m for m in lines if m.startswith("Record r1 is free")] == [
            f"Record r1 is free: entry {first} on {LANE} is offered again"
        ]

    async def test_the_number_waiting_is_published(self, harness: Harness, gauge: MagicMock) -> None:
        await _park(harness, 2)
        assert _gauge_values() == [1, 2]
        harness.consumer._release_record("r1")
        await harness.passes(1)
        assert _gauge_values()[-1] == 1


class TestClaimAndReleaseAreOneStep:
    def test_no_waiter_is_left_asleep_on_a_free_record(self) -> None:
        """Threads claim and release a few records at random: whenever a
        record is free, one of its waiters is due. A release between a failed
        claim and its park would leave every waiter asleep on a free record."""
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger(LOGGER_NAME),
            RedisStreamsConfig(topics=[LANE], group_id=GROUP, client_id="race"),
        )
        records = ["r1", "r2", "r3"]
        counter = iter(range(1_000_000))
        counter_lock = threading.Lock()

        def work(seed: int) -> None:
            rng = random.Random(seed)
            for _ in range(2000):
                record_id = rng.choice(records)
                with counter_lock:
                    message_id = f"{next(counter)}-0"
                if consumer._claim_record_or_wait(record_id, LANE, message_id):
                    consumer._release_record(record_id)

        threads = [threading.Thread(target=work, args=(seed,)) for seed in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert consumer._in_flight_record_ids == set()
        _assert_index_matches(consumer)
        for record_id in records:
            waits = [w for w in consumer._record_waits.values() if w.record_key == record_id]
            if waits:
                assert any(w.due_at <= time.monotonic() for w in waits), record_id


def _assert_index_matches(consumer: IndexingRedisStreamsConsumer) -> None:
    """The per-record state is exactly what the waits say."""
    expected: dict[str, tuple[set, list, list, set]] = {}
    for key, wait in consumer._record_waits.items():
        keys, local, lease, active = expected.setdefault(wait.record_key, (set(), [], [], set()))
        keys.add(key)
        if not wait.on_lease:
            local.append(key)
        elif not wait.running:
            lease.append(key)
        if wait.woken or wait.running:
            active.add(key)
    actual = {
        record: (w.keys, [entry[2] for entry in w.local], [entry[2] for entry in w.lease], w.active)
        for record, w in consumer._record_waiters.items()
    }
    for _keys, local, lease, _active in expected.values():
        for ordered in (local, lease):
            ordered.sort(key=lambda k: (tuple(int(p) for p in k[1].split("-")), k[0]))
    assert actual == expected


@pytest.mark.asyncio
class TestAtScale:
    async def test_the_by_record_index_follows_every_way_a_wait_ends(self, harness: Harness) -> None:
        consumer = harness.consumer
        await _park(harness, 3, "r1")
        await _park(harness, 2, "r2")
        _assert_index_matches(consumer)

        consumer._release_record("r1")
        await harness.passes(1)
        _assert_index_matches(consumer)

        (gone,) = [key for key, wait in consumer._record_waits.items() if wait.record_key == "r2"][:1]
        await consumer.redis.xack(gone[0], GROUP, gone[1])
        await harness.refresh()
        _assert_index_matches(consumer)

        consumer._stop_worker_thread()
        assert consumer._record_waiters == {}

    async def test_a_refresh_in_chunks_prunes_only_what_is_gone(self, harness: Harness) -> None:
        consumer = harness.consumer
        first, second, third = await _park(harness, 3)
        await consumer.redis.xack(LANE, GROUP, second)
        calls: list[int] = []
        real = consumer.redis.eval

        async def counting(script: str, numkeys: int, *args: Any) -> Any:  # noqa: ANN401
            calls.append(len(args) - 4)  # stream, group, consumer, body check, then the ids
            return await real(script, numkeys, *args)

        consumer.redis.eval = counting  # type: ignore[method-assign]
        with patch(f"{_MODULE}._REFRESH_CLAIM_CHUNK", 2):
            await harness.refresh()

        assert calls == [2, 1]
        assert sorted(consumer._record_waits) == sorted([(LANE, first), (LANE, third)])

    async def test_letting_go_of_a_woken_waiter_wakes_the_next(self, harness: Harness) -> None:
        consumer = harness.consumer
        first, second = await _park(harness, 2)
        consumer._release_record("r1")
        # The first is woken; its stream's ownership then goes unconfirmed
        # while the second's wait is fresh.
        consumer._record_waits[(LANE, first)].parked_at = 0.0
        consumer._ownership_confirmed_at.clear()
        risk_after = getattr(consumer, f"{_CONSUMER}__ownership_risk_after")()
        consumer._record_waits[(LANE, second)].parked_at = time.monotonic()
        getattr(consumer, f"{_CONSUMER}__release_record_waits_at_risk")(risk_after, time.monotonic())

        assert list(consumer._record_waits) == [(LANE, second)]
        assert not consumer._is_waiting_for_record(second, LANE)


class TestReleaseCost:
    def test_a_release_does_not_walk_every_wait(self) -> None:
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger(LOGGER_NAME),
            RedisStreamsConfig(topics=[LANE], group_id=GROUP, client_id="scale"),
        )
        for i in range(10_000):
            record_id = f"r{i}"
            assert consumer._claim_record(record_id)
            assert not consumer._claim_record_or_wait(record_id, LANE, f"{i}-0")
        assert len(consumer._record_waits) == 10_000

        class NoWalk(dict):
            """A release that walked every wait (10^8 steps for 10,000
            releases) would iterate this; looking one up is allowed."""

            def _walk(self, *_args: Any) -> Any:  # noqa: ANN401
                raise AssertionError("a release walked every wait")

            __iter__ = items = values = keys = _walk  # type: ignore[assignment]

        consumer._record_waits = NoWalk(consumer._record_waits)
        for i in range(10_000):
            consumer._release_record(f"r{i}")

        assert all(not consumer._is_waiting_for_record(f"{i}-0", LANE) for i in range(0, 10_000, 997))



def _gauge_values() -> list[float]:
    return [c.kwargs["value"] for c in _RECORD_WAITS_SET.call_args_list]


_RECORD_WAITS_SET = MagicMock()


@pytest.fixture
def gauge() -> Any:  # noqa: ANN401
    _RECORD_WAITS_SET.reset_mock()
    from app.telemetry.modules import scheduling_metrics

    scheduling_metrics._record_waits_by_owner.clear()
    scheduling_metrics._record_waits_collected.clear()
    with patch("app.telemetry.modules.scheduling_metrics.RECORD_WAITS.set", _RECORD_WAITS_SET):
        yield _RECORD_WAITS_SET


@pytest.mark.asyncio
class TestReviewOfTheWaits:
    async def test_a_lease_wait_that_loses_its_lease_a_thousand_times_still_backs_off(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        consumer._wait_for_record_lease("r1", LANE, "1-0")
        consumer._record_waits[(LANE, "1-0")].lease_losses = 5_000

        consumer._wait_for_record_lease("r1", LANE, "1-0")

        wait = consumer._record_waits[(LANE, "1-0")]
        assert wait.due_at - wait.parked_at == pytest.approx(300.0)

    async def test_the_gauge_is_the_total_of_every_consumer_in_the_process(
        self, provider: FakeRedisConnectionProvider, gauge: MagicMock
    ) -> None:
        records = _make(provider)
        priority = _make(provider)
        assert records._claim_record("r1") and priority._claim_record("p1")

        assert not records._claim_record_or_wait("r1", LANE, "1-0")
        assert not priority._claim_record_or_wait("p1", LANE, "2-0")
        assert not priority._claim_record_or_wait("p1", LANE, "3-0")
        assert _gauge_values()[-1] == 3

        priority._stop_worker_thread()
        # The records consumer still has its wait: stopping the other one
        # must not read as none.
        assert _gauge_values()[-1] == 1
        records._stop_worker_thread()
        assert _gauge_values()[-1] == 0

    async def test_a_count_published_late_cannot_overwrite_a_newer_one(
        self, harness: Harness, gauge: MagicMock
    ) -> None:
        """A worker records a wait; the main loop ends it. Each publishes the
        count it saw. Published outside the lock that changes the waits, the
        worker's 1 could land after the main loop's 0 and stay there. The
        worker is paused before the metrics module, so the metrics lock alone
        cannot hide the race."""
        consumer = harness.consumer
        assert consumer._claim_record("r1")
        main_published = threading.Event()
        worker_publishing = threading.Event()
        real = scheduling_metrics.record_record_waits

        def paused(*args: Any) -> None:  # noqa: ANN401
            if threading.current_thread().name == "worker" and args[-1] == 1:
                worker_publishing.set()
                main_published.wait(timeout=0.5)
            real(*args)

        with patch(f"{_MODULE}.metrics.record_record_waits", paused):
            worker = threading.Thread(
                target=consumer._claim_record_or_wait, args=("r1", LANE, "1-0"), name="worker"
            )
            worker.start()
            assert worker_publishing.wait(timeout=5)
            consumer._end_record_wait(LANE, "1-0")
            main_published.set()
            worker.join()

        assert consumer._record_waits == {}
        assert _gauge_values()[-1] == 0

    async def test_waiters_whose_recheck_passed_during_a_long_run_still_go_oldest_first(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        first, second, third = await _park(harness, 3)
        harness.dispatched.clear()
        # The run outlasted every waiter's recheck bound.
        _expire(consumer)
        # Recovery's scan resumes after the oldest, so it meets the newer first.
        consumer._own_scan_resume[LANE] = f"({first}"

        consumer._release_record("r1")
        await harness.passes(1)

        assert harness.dispatched == [first]
        _assert_index_matches(consumer)
        await harness.passes(2)
        assert harness.dispatched == [first, second, third]
        _assert_index_matches(consumer)

    async def test_a_woken_waiter_awaiting_admission_is_not_also_counted_as_waiting(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        (woken,) = await _park(harness, 1)
        consumer._release_record("r1")
        # Dispatched and holding its record, still before the index slot.
        consumer._mark_in_flight(woken, LANE)
        assert consumer._claim_record_or_wait("r1", LANE, woken)
        # Another entry of the lane, pending here and untracked.
        await consumer.redis.xadd(LANE, _fields("r2"))
        (other,) = [
            i for _s, entries in await consumer.redis.xreadgroup(
                GROUP, consumer.consumer_name, {LANE: ">"}, count=10
            ) for i, _f in entries
        ]

        await harness.recover()

        assert (LANE, other) in consumer._held_entries

    async def test_a_refresh_whose_later_chunk_fails_keeps_what_the_earlier_one_confirmed(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        first, second, third = await _park(harness, 3)
        for wait in consumer._record_waits.values():
            wait.parked_at = 0.0
        consumer._ownership_confirmed_at.clear()
        script = consumer.redis.eval
        calls = 0

        async def second_chunk_fails(*args: Any) -> Any:  # noqa: ANN401
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ConnectionError("redis blip")
            return await script(*args)

        consumer.redis.eval = second_chunk_fails  # type: ignore[method-assign]
        with patch(f"{_MODULE}._REFRESH_CLAIM_CHUNK", 2):
            await harness.refresh()

        assert sorted(consumer._record_waits) == sorted([(LANE, first), (LANE, second)])

    async def test_a_refresh_never_takes_back_an_entry_a_peer_claimed(self, harness: Harness) -> None:
        consumer = harness.consumer
        first, second = await _park(harness, 2)
        await consumer.redis.xadd(LANE, _fields("r9"))
        (held,) = await harness.read_new()
        # This consumer stalled past the claim window; a peer took two.
        await consumer.redis.xclaim(LANE, GROUP, "peer", min_idle_time=0, message_ids=[first, held])

        await harness.refresh()

        owners = {
            (d["message_id"].decode() if isinstance(d["message_id"], bytes) else d["message_id"]): (
                d["consumer"].decode() if isinstance(d["consumer"], bytes) else d["consumer"]
            )
            for d in await consumer.redis.xpending_range(LANE, GROUP, min="-", max="+", count=10)
        }
        assert owners[first] == "peer"
        assert owners[held] == "peer"
        assert owners[second] == consumer.consumer_name
        assert list(consumer._record_waits) == [(LANE, second)]

    async def test_a_fresh_event_for_the_record_does_not_overtake_the_ones_waiting(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        older = await _park(harness, 2)
        harness.dispatched.clear()
        consumer._release_record("r1")
        # Read fresh and dispatched before recovery offers the woken waiter.
        await consumer.redis.xadd(LANE, _fields("r1"))
        (fresh,) = await harness.read_new()
        await harness.dispatch()
        assert harness.handler.ran == []

        await harness.passes(4)

        assert harness.handler.ran == ["r1", "r1", "r1"]
        # Dispatched once on arrival, where it waited behind the two older;
        # it ran on its second dispatch, after both of them.
        assert harness.dispatched == [fresh, *older, fresh]

    async def test_a_woken_run_interrupted_before_its_lease_is_the_one_offered_again(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        first, second = await _park(harness, 2)
        consumer._release_record("r1")
        # The woken entry's run takes the record, then stops before the lease
        # (an error, or the consumer stopping), its entry still pending.
        assert consumer._claim_record_or_wait("r1", LANE, first)
        consumer._release_record("r1", (LANE, first))

        assert not consumer._is_waiting_for_record(first, LANE)
        assert consumer._is_waiting_for_record(second, LANE)


@pytest.mark.asyncio
class TestReviewOfTheWaitsRoundTwo:
    async def test_a_lease_wait_keeps_its_back_off_across_a_local_wait(self, harness: Harness) -> None:
        consumer = harness.consumer
        base = messaging_env.record_lease_wait_seconds
        consumer._wait_for_record_lease("r1", LANE, "1-0")
        # Due again, but a sibling in this process holds the record now.
        assert consumer._claim_record("r1")
        assert not consumer._claim_record_or_wait("r1", LANE, "1-0")
        consumer._release_record("r1")
        assert consumer._claim_record_or_wait("r1", LANE, "1-0")

        consumer._wait_for_record_lease("r1", LANE, "1-0")

        wait = consumer._record_waits[(LANE, "1-0")]
        assert wait.due_at - wait.parked_at == pytest.approx(min(2 * base, 300.0))

    async def test_a_lease_wait_is_not_offered_while_this_process_holds_its_record(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        consumer._wait_for_record_lease("r1", LANE, "1-0")
        _expire(consumer)
        assert consumer._claim_record("r1")

        assert consumer._is_waiting_for_record("1-0", LANE)
        consumer._release_record("r1")
        assert not consumer._is_waiting_for_record("1-0", LANE)

    async def test_a_consumer_dropped_without_a_stop_leaves_the_gauge(
        self, provider: FakeRedisConnectionProvider, gauge: MagicMock
    ) -> None:
        kept = _make(provider)
        dropped = _make(provider)
        assert kept._claim_record("k") and dropped._claim_record("d")
        assert not kept._claim_record_or_wait("k", LANE, "1-0")
        assert not dropped._claim_record_or_wait("d", LANE, "2-0")
        assert _gauge_values()[-1] == 2

        del dropped
        gc.collect()
        # What every consume pass does (in the ownership refresh).
        scheduling_metrics.refresh_record_waits("redis")

        assert _gauge_values()[-1] == 1

    async def test_an_entry_in_flight_while_it_waits_on_a_lease_is_counted_once(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        await consumer.redis.xadd(LANE, _fields("r1"))
        await consumer.redis.xadd(LANE, _fields("r9"))
        parked, other = [
            i for _s, entries in await consumer.redis.xreadgroup(
                GROUP, consumer.consumer_name, {LANE: ">"}, count=10
            ) for i, _f in entries
        ]
        # Its run lost the lease and parked it; the run's cleanup is not done,
        # so it is still in flight.
        consumer._mark_in_flight(parked, LANE)
        consumer._wait_for_record_lease("r1", LANE, parked)

        await harness.recover()

        assert (LANE, other) in consumer._held_entries

    async def test_a_buffered_waiter_whose_record_was_taken_again_is_counted_once(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        first, _second = await _park(harness, 2)
        consumer._release_record("r1")
        # The woken waiter is buffered, then an older delivery takes the
        # record before it is dispatched: it is held and waiting at once.
        await harness.recover()
        assert (LANE, first) in consumer._held_entries
        assert consumer._claim_record("r1")
        await consumer.redis.xadd(LANE, _fields("r9"))
        (other,) = [
            i for _s, entries in await consumer.redis.xreadgroup(
                GROUP, consumer.consumer_name, {LANE: ">"}, count=10
            ) for i, _f in entries
        ]

        await harness.recover()

        assert (LANE, other) in consumer._held_entries


_COLLECTION_INSIDE_A_PUBLICATION = """
import gc, threading
from unittest.mock import patch
from app.telemetry.modules import scheduling_metrics as m

class Owner:
    def __init__(self):
        self.cycle = self  # freed only by a collection

gc.disable()
dropped = Owner()
m.record_record_waits("test", dropped, 1)
del dropped
real_set = m.RECORD_WAITS.set

def collecting_set(*args, **kwargs):
    gc.collect()  # the dropped owner's finalizer runs here, mid-publication
    return real_set(*args, **kwargs)

kept = Owner()
with patch.object(m.RECORD_WAITS, "set", collecting_set):
    m.record_record_waits("test", kept, 2)
    m.record_record_waits("test", kept, 3)
m.refresh_record_waits("test")
assert {k: r.count for k, r in m._record_waits_by_owner["test"].items()} == {id(kept): 3}
print("ok")
"""


class TestTheGaugeUnderCollection:
    def test_a_collection_inside_a_publication_does_not_deadlock(self) -> None:
        """In its own process: a deadlock there must fail this test, not hang
        the rest of the suite behind a lock its thread still holds."""
        result = subprocess.run(
            [sys.executable, "-c", _COLLECTION_INSIDE_A_PUBLICATION],
            cwd=str(Path(__file__).resolve().parents[4]),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0 and result.stdout.strip().endswith("ok"), result.stderr[-2000:]

    def test_a_finalizer_takes_no_lock_and_publishes_nothing(self) -> None:
        """It runs on whatever thread a collection happens on, possibly one
        inside the gauge's own lock: it only records the collection."""

        class Owner:
            def __init__(self) -> None:
                self.cycle = self

        acquired: list[str] = []

        class SpyLock:
            def __init__(self, real: Any) -> None:  # noqa: ANN401
                self.real = real

            def __enter__(self) -> None:
                acquired.append(threading.current_thread().name)
                self.real.acquire()

            def __exit__(self, *_exc: object) -> None:
                self.real.release()

            def acquire(self, *args: Any, **kwargs: Any) -> bool:  # noqa: ANN401
                acquired.append(threading.current_thread().name)
                return self.real.acquire(*args, **kwargs)

            def release(self) -> None:
                self.real.release()

        gc.disable()
        try:
            owner = Owner()
            scheduling_metrics.record_record_waits("spy", owner, 1)
            with patch.object(scheduling_metrics, "_record_waits_lock", SpyLock(scheduling_metrics._record_waits_lock)), \
                    patch.object(scheduling_metrics.RECORD_WAITS, "set") as gauge_set:
                del owner
                gc.collect()
                assert acquired == []
                gauge_set.assert_not_called()
                scheduling_metrics.refresh_record_waits("spy")
                gauge_set.assert_called_with("spy", value=0)
        finally:
            gc.enable()

    def test_a_total_computed_before_a_collection_is_corrected(self) -> None:
        class Owner:
            def __init__(self) -> None:
                self.cycle = self

        published: list[float] = []
        real_set = scheduling_metrics.RECORD_WAITS.set
        gc.disable()
        try:
            dropped = Owner()
            scheduling_metrics.record_record_waits("stale", dropped, 1)
            del dropped
            kept = Owner()

            def collecting_set(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
                gc.collect()  # after the total (with the dropped owner) is computed
                published.append(kwargs["value"])
                real_set(*args, **kwargs)

            with patch.object(scheduling_metrics.RECORD_WAITS, "set", collecting_set):
                scheduling_metrics.record_record_waits("stale", kept, 2)
                scheduling_metrics.refresh_record_waits("stale")
            assert published[-1] == 2
        finally:
            gc.enable()


@pytest.mark.asyncio
class TestReviewOfTheWaitsRoundThree:
    async def test_a_wait_a_peer_took_meanwhile_does_not_hide_the_lanes_unheld_entry(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        await consumer.redis.xadd(LANE, _fields("r1"))
        await consumer.redis.xadd(LANE, _fields("r9"))
        waiting, other = [
            i for _s, entries in await consumer.redis.xreadgroup(
                GROUP, consumer.consumer_name, {LANE: ">"}, count=10
            ) for i, _f in entries
        ]
        consumer._wait_for_record_lease("r1", LANE, waiting)
        # A peer took the waiting entry; this consumer has not refreshed yet.
        await consumer.redis.xclaim(LANE, GROUP, "peer", min_idle_time=0, message_ids=[waiting])

        await harness.recover()

        assert (LANE, other) in consumer._held_entries

    async def test_a_donor_is_restored_when_the_waiter_it_made_room_for_waits_again(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        await consumer.redis.xadd(LANE, _fields("r1"))
        (waiter,) = [
            i for _s, entries in await consumer.redis.xreadgroup(
                GROUP, consumer.consumer_name, {LANE: ">"}, count=10
            ) for i, _f in entries
        ]
        consumer._wait_for_record_lease("r1", LANE, waiter)
        _expire(consumer)
        for i in range(BUFFER):
            await consumer.redis.xadd(OTHER_LANE, _fields(f"b{i}", "conn-b"))
        await harness.read_new(OTHER_LANE)
        held = set(consumer._held_entries)
        real_xrange = consumer.redis.xrange

        async def sibling_takes_the_record(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            # Between the scan that found the due waiter and its offer.
            consumer._claim_record("r1")
            return await real_xrange(*args, **kwargs)

        consumer.redis.xrange = sibling_takes_the_record  # type: ignore[method-assign]
        await harness.recover()

        assert set(consumer._held_entries) == held
        assert consumer._scheduler.pending_count + len(consumer._deferred_entries) == BUFFER


class TestTheGaugeAcrossIdReuse:
    def test_a_new_owner_reusing_a_collected_owners_id_keeps_its_count(self) -> None:
        """CPython reuses the id of a freed object. A collected owner's
        notification still queued must not take away the count of a new
        owner that got the same id before the queue was drained."""

        class Owner:
            pass

        scheduling_metrics._record_waits_by_owner.pop("reuse", None)
        old = Owner()
        old_id = id(old)
        scheduling_metrics.record_record_waits("reuse", old, 1)
        del old
        gc.collect()  # its finalizer queues the collection; nothing drains it yet
        candidates = []
        for _ in range(10_000):
            candidate = Owner()
            if id(candidate) == old_id:
                break
            candidates.append(candidate)
        else:
            pytest.skip("the interpreter did not reuse the freed id")
        published: list[float] = []
        with patch.object(
            scheduling_metrics.RECORD_WAITS, "set", side_effect=lambda *_a, value: published.append(value)
        ):
            scheduling_metrics.record_record_waits("reuse", candidate, 5)
            scheduling_metrics.refresh_record_waits("reuse")
        assert published[-1] == 5


@pytest.mark.asyncio
class TestReviewOfTheWaitsRoundFive:
    async def test_a_waiter_woken_while_its_lane_is_summarised_is_offered_that_pass(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        (waiter,) = await _park(harness, 1)
        harness.dispatched.clear()
        # The lane's ids were checked against the pending list moments ago.
        consumer._tracked_checked_at[LANE] = time.monotonic()
        real_xpending = consumer.redis.xpending

        async def released_meanwhile(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            consumer._release_record("r1")  # the run holding r1 ends during XPENDING
            return await real_xpending(*args, **kwargs)

        consumer.redis.xpending = released_meanwhile  # type: ignore[method-assign]
        await harness.passes(1)

        assert harness.dispatched == [waiter]

    async def test_a_newer_event_does_not_overtake_an_older_one_waiting_on_a_lease(
        self, harness: Harness
    ) -> None:
        consumer = harness.consumer
        consumer._wait_for_record_lease("r1", LANE, "1-0")
        # The lease is free now; a newer event for r1 arrives first.
        assert not consumer._claim_record_or_wait("r1", LANE, "2-0")
        assert not consumer._is_record_in_flight("r1")

        _expire(consumer)
        # The older one, its delay up, takes the record; the newer waits on it.
        assert consumer._claim_record_or_wait("r1", LANE, "1-0")
        assert consumer._is_waiting_for_record("2-0", LANE)
        consumer._end_record_wait(LANE, "1-0")
        consumer._release_record("r1", (LANE, "1-0"))
        assert not consumer._is_waiting_for_record("2-0", LANE)
        _assert_index_matches(consumer)


class TestTheGaugeAcrossRestarts:
    def test_a_consumer_stopped_and_restarted_leaves_no_finalizer_behind(self) -> None:
        class Owner:
            pass

        owner = Owner()
        owner_id = id(owner)
        for _ in range(5):
            scheduling_metrics.record_record_waits("restart", owner, 1)
            scheduling_metrics.forget_record_waits("restart", owner)
        scheduling_metrics.record_record_waits("restart", owner, 1)  # running, then dropped
        scheduling_metrics._record_waits_collected.clear()
        del owner
        gc.collect()

        notices = [n for n in scheduling_metrics._record_waits_collected if n[:2] == ("restart", owner_id)]
        # One for the registration it was dropped with; none for the stopped ones.
        assert len(notices) == 1


@pytest.mark.asyncio
class TestReviewOfTheWaitsRoundSix:
    async def test_a_newer_event_behind_an_older_lease_wait_stays_parked_until_it_resolves(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        consumer = harness.consumer
        await consumer.redis.xadd(LANE, _fields("r1"))
        await consumer.redis.xadd(LANE, _fields("r1"))
        older, newer = [
            i for _s, entries in await consumer.redis.xreadgroup(
                GROUP, consumer.consumer_name, {LANE: ">"}, count=10
            ) for i, _f in entries
        ]
        # The older one lost its lease and waits out its delay.
        consumer._wait_for_record_lease("r1", LANE, older)

        await harness.passes(10)
        _assert_index_matches(consumer)

        # The newer one was offered once, found the older one waiting, and
        # waited: not offered again every pass for the older one's delay.
        assert harness.dispatched == [newer]
        assert len(_recovering_lines(caplog)) == 1
        assert consumer._is_waiting_for_record(newer, LANE)

        # The older one's delay is up and its lease is free: it runs, and its
        # release wakes the newer one.
        consumer._record_waits[(LANE, older)].due_at = 0.0
        await harness.passes(3)
        assert harness.dispatched == [newer, older, newer]
        assert harness.handler.ran == ["r1", "r1"]
        assert consumer._record_waits == {}
        _assert_index_matches(consumer)
