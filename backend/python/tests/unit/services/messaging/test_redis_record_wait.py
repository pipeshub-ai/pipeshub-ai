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
import json
import logging
import random
import threading
import time
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
        consumer.redis.xclaim = AsyncMock(side_effect=ConnectionError("redis down"))  # type: ignore[method-assign]

        await harness.refresh()

        assert consumer._record_waits == {}
        assert any("Stopped keeping 2 entry(ies) waiting" in r.getMessage() for r in caplog.records)
        # Still pending here: recovery offers them again, and they wait again.
        assert sorted(await harness.pending_here()) == sorted(waiting)

    async def test_refresh_keeps_waiting_entries_from_going_idle(self, harness: Harness) -> None:
        consumer = harness.consumer
        waiting = await _park(harness, 2)
        await asyncio.sleep(0.05)

        await harness.refresh()

        details = await consumer.redis.xpending_range(
            LANE, GROUP, min="-", max="+", count=10, consumername=consumer.consumer_name
        )
        assert sorted(d["message_id"].decode() if isinstance(d["message_id"], bytes) else d["message_id"] for d in details) == sorted(waiting)
        assert all(d["time_since_delivered"] < 50 for d in details)
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

    async def test_the_number_waiting_is_published(self, harness: Harness) -> None:
        with patch(f"{_MODULE}.metrics.record_record_waits") as gauge:
            await _park(harness, 2)
            assert [c.args for c in gauge.call_args_list] == [("redis", 1), ("redis", 2)]
            harness.consumer._release_record("r1")
            await harness.passes(1)
            assert gauge.call_args_list[-1].args == ("redis", 1)


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
        for record_id in records:
            waits = [w for w in consumer._record_waits.values() if w.record_key == record_id]
            if waits:
                assert any(w.due_at <= time.monotonic() for w in waits), record_id
