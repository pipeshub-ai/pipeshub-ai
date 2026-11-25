"""The record consumers when the optional hook supplies parts: a slot planner, a buffer room rule, a read budget
for extra topics, an observer, extra topics and claim steps.

Every test here drives the real consumer code (read, dispatch and fetch-back phases, the queue claim loop, the
factory) on the in-memory brokers of ``tests/support/record_consumer_world.py``, with several orgs, and with
parts written for the test (a pass-through planner, a per-org room rule, a recording observer, fixed claim steps).
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import (
    EnqueueResult,
    FairSchedulerConfig,
    IndexingFairness,
)
from app.services.messaging.scheduling.key_extractors import CompositeKeyExtractor
from app.services.messaging.scheduling.slot_share import SlotShare
from tests.support import record_consumer_world as world

if TYPE_CHECKING:
    from collections.abc import Iterator
from tests.support.mutated_method import mutate

pytestmark = pytest.mark.timeout(240)
GOLDENS = Path(__file__).parent / "goldens" / "record_consumer_seam_off"
KEY_FIELDS = ("orgId", "connectorId")


@pytest.fixture(autouse=True)
def _world_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key, value in world.GOLDEN_ENV.items():
        monkeypatch.setenv(key, value)
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def golden(kind: str) -> dict:
    return json.loads((GOLDENS / f"{kind}.json").read_text())["run"]


# -- parts written for the tests ------------------------------------------------------------------------------------
class SpyPlanner:
    """Wraps SlotShare and records every call a consumer makes to its planner."""

    log: list[tuple[str, Any]] = []

    def __init__(self, entity_depth: int, weights: Any) -> None:  # noqa: ANN401
        self.inner = SlotShare(entity_depth, weights)

    def started(self, token: Any, key: Any, pool: str) -> None:  # noqa: ANN401
        SpyPlanner.log.append(("started", token))
        self.inner.started(token, key, pool)

    def admitted(self, token: Any) -> None:  # noqa: ANN401
        SpyPlanner.log.append(("admitted", token))
        self.inner.admitted(token)

    def finished(self, token: Any) -> None:  # noqa: ANN401
        SpyPlanner.log.append(("finished", token))
        self.inner.finished(token)

    def plan(self, backlogged: Any, pool_of: Any, limit_of: Any) -> Any:  # noqa: ANN401
        return self.inner.plan(backlogged, pool_of, limit_of)


class OrgRoom:
    """Each org's keys draw on one shared bucket of ``limit`` places, whatever the org's connectors."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.calls = 0

    def __call__(self, key: tuple[str, ...], scheduler: DRRScheduler) -> tuple[str, int]:
        self.calls += 1
        return key[0], self.limit - scheduler.pending_count_for(key[:1])


class Observer:
    def __init__(self) -> None:
        self.entries: list[Any] = []
        self.exits: list[Any] = []

    def entered(self, *args: Any) -> None:  # noqa: ANN401
        self.entries.append(args)

    def left(self, *args: Any) -> None:  # noqa: ANN401
        self.exits.append(args)


def bundle(**parts: Any) -> IndexingFairness:  # noqa: ANN401
    return IndexingFairness(key_fields=KEY_FIELDS, key_extractor=CompositeKeyExtractor(fields=KEY_FIELDS), **parts)


# -- O-6, O-13: the buffer room rule in the scheduler ---------------------------------------------------------------
def test_a_room_of_zero_or_less_gives_entity_full_and_positive_accepts_and_none_is_no_limit() -> None:
    config = FairSchedulerConfig(enabled=True, tier_level=False)
    for room, expected in ((0, EnqueueResult.ENTITY_FULL), (-3, EnqueueResult.ENTITY_FULL), (1, EnqueueResult.ACCEPTED)):
        scheduler: DRRScheduler = DRRScheduler(config, room=lambda _key, room=room: ("b", room))
        assert scheduler.enqueue(("o", "c"), "x") == expected
        assert scheduler.pending_count == (1 if expected == EnqueueResult.ACCEPTED else 0)
    assert DRRScheduler(config, room=lambda _key: None).enqueue(("o", "c"), "x") == EnqueueResult.ACCEPTED
    assert DRRScheduler(config).enqueue(("o", "c"), "x") == EnqueueResult.ACCEPTED


def test_the_rule_sees_the_live_counts_of_the_scheduler_it_is_given_during_enqueue() -> None:
    seen: list[tuple[int, int, int]] = []
    holder: dict[str, DRRScheduler] = {}

    def rule(key: tuple[str, ...]) -> tuple[str, int]:
        scheduler = holder["s"]
        seen.append((scheduler.pending_count_for(key), scheduler.pending_count_for(key[:1]), scheduler.active_count_at(0)))
        return key[0], 100

    scheduler = DRRScheduler(FairSchedulerConfig(enabled=True, tier_level=False), room=rule)
    holder["s"] = scheduler
    for i, key in enumerate([("a", "1"), ("a", "1"), ("a", "2"), ("b", "1")]):
        scheduler.enqueue(key, i)
    # (this connector's buffered, this org's buffered, orgs with work) as each enqueue began
    assert seen == [(0, 0, 0), (1, 1, 1), (0, 2, 1), (0, 0, 1)]


# -- O-5: a planner given by the hook ------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["kafka", "redis"])
async def test_a_pass_through_planner_gives_the_default_trace_and_hears_every_dispatch(kind: str) -> None:
    SpyPlanner.log = []
    fairness = bundle(planner=SpyPlanner)
    trace = await world.collect(kind, consumer_kwargs={"fairness": fairness})
    assert trace == golden(kind)
    by_token: dict[Any, list[str]] = defaultdict(list)
    for call, token in SpyPlanner.log:
        by_token[token].append(call)
    # started, then admitted (unless it ended before the gate), then finished; a record still running when the run
    # ended has not finished yet
    shape = re.compile(r"(started (admitted )?finished ?)*(started( admitted)?)?")
    assert all(shape.fullmatch(" ".join(calls)) for calls in by_token.values())
    started = [token for call, token in SpyPlanner.log if call == "started"]
    assert len(started) >= golden(kind)["dispatch"]["count"]  # one that is handed back before the handler also starts
    assert len([1 for call, _t in SpyPlanner.log if call == "finished"]) >= len(started) - 6


# -- O-6: a room rule, with several orgs, through the real consumers -----------------------------------------------
async def test_kafka_with_a_room_rule_no_org_holds_more_than_its_room_and_nothing_is_lost() -> None:
    messages = world.generate_messages(11, 700)
    room = OrgRoom(12)
    worst = defaultdict(int)

    def watch(run: Any, _step: int) -> None:  # noqa: ANN401
        for org in {m.org_id for m in messages}:
            worst[org] = max(worst[org], run.consumer._scheduler.pending_count_for((org,)))

    trace = await world.run_kafka(messages, consumer_kwargs={"fairness": bundle(buffer_room=room)}, on_step=watch)
    assert room.calls > 0
    assert max(worst.values()) <= 12
    assert set(trace.dispatch) == {m.record_id for m in messages}
    assert max(c[1] for c in trace.counters) > 0  # reading past a full org's room remembered positions
    ends = defaultdict(int)
    for m in messages:
        ends[world.lane_of(m.connector_id, 4)] += 1
    assert dict(trace.extra["committed"]) == dict(ends)


async def test_redis_with_a_room_rule_full_orgs_are_parked_and_reoffered_and_nothing_is_lost() -> None:
    messages = world.generate_messages(12, 700)
    worst = defaultdict(int)

    def watch(run: Any, _step: int) -> None:  # noqa: ANN401
        for org in {m.org_id for m in messages}:
            worst[org] = max(worst[org], run.consumer._scheduler.pending_count_for((org,)))

    trace = await world.run_redis(messages, consumer_kwargs={"fairness": bundle(buffer_room=OrgRoom(12))}, on_step=watch)
    assert max(worst.values()) <= 12
    assert set(trace.dispatch) == {m.record_id for m in messages}
    assert max(c[1] for c in trace.counters) > 0
    assert len({message_id for _stream, message_id in trace.settled}) == len(messages)


# -- O-11: fetch-back sized from the shared room -----------------------------------------------------------------------
class CountingFetcher:
    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        self.inner = inner
        self.fetched = 0

    async def fetch(self, wanted: dict, **kwargs: Any) -> Any:  # noqa: ANN401
        self.fetched += sum(len(v) for v in wanted.values())
        return await self.inner.fetch(wanted, **kwargs)


async def fetch_world(
    seed: int, orgs: int, count: int, limit: int, cap: int, buffered: int, hot_share: float = 0.5
) -> tuple[float, int]:
    """(messages fetched back over messages buffered from remembered positions, records that never ran) for a world
    whose orgs each have one bucket of ``limit`` places shared by the org's connectors."""
    messages = world.generate_messages(seed, count, orgs=orgs, hot_share=hot_share)
    state: dict[str, Any] = {"pops": 0}

    def watch(run: Any, step: int) -> None:  # noqa: ANN401
        if step:
            return
        consumer = run.consumer
        state["fetcher"] = CountingFetcher(consumer._IndexingKafkaConsumer__offset_fetcher())
        consumer._offset_fetcher = state["fetcher"]
        remembered = consumer._remembered
        pop = remembered.pop

        def counting_pop(entity: Any) -> Any:  # noqa: ANN401
            state["pops"] += 1
            return pop(entity)

        remembered.pop = counting_pop

    trace = await world.run_kafka(
        messages, consumer_kwargs={"fairness": bundle(buffer_room=OrgRoom(limit))}, on_step=watch, max_steps=3_000,
        fair=world.fair_config(max_per_entity_messages=cap, max_buffered_messages=buffered),
    )
    return state["fetcher"].fetched / max(1, state["pops"]), len({m.record_id for m in messages} - set(trace.dispatch))


# (seed, orgs, records, org room, per-connector cap, buffer). The first five have an org room below cap // 2: the
# chunk threshold used to be out of their reach, so their remembered positions were never fetched back.
FETCH_WORLDS = [
    (31, 5, 700, 16, 60, 300), (32, 3, 700, 27, 60, 120), (33, 2, 700, 26, 60, 500), (34, 6, 700, 21, 60, 300),
    (35, 5, 700, 15, 60, 200),
    (36, 4, 700, 47, 20, 120), (37, 6, 700, 30, 20, 200), (38, 3, 700, 34, 30, 200), (39, 2, 700, 57, 60, 300),
]


@pytest.mark.parametrize("params", FETCH_WORLDS, ids=lambda p: f"room{p[3]}-cap{p[4]}")
async def test_fetch_back_drains_every_world_and_fetches_at_most_twice_what_it_buffers(params: tuple) -> None:
    ratio, never_ran = await fetch_world(*params)
    assert never_ran == 0, f"{never_ran} records never ran: their positions stayed remembered"
    assert ratio <= 2.0, ratio


async def planned_total(connectors: int = 3, left: int = 12) -> int:
    """Positions one fetch-back plan asks for when ``connectors`` connectors of one org (one shared bucket with
    ``left`` places) each have 30 remembered."""
    async with world.kafka_world(
        [], fair=world.fair_config(max_per_entity_messages=40, max_buffered_messages=200), produce=False,
        consumer_kwargs={"fairness": bundle(buffer_room=lambda key, s: ("org", left - s.pending_count_for(key[:1])))},
    ) as run:
        from aiokafka.structs import TopicPartition

        tp = TopicPartition("record-events", 0)
        for c in range(connectors):
            for offset in range(30):
                run.consumer._remembered.remember(("org-0", f"conn-{c}"), tp, c * 100 + offset)
        asked: list[int] = []

        class Fetcher:
            async def fetch(self, wanted: dict, **_k: Any) -> tuple[dict, set]:  # noqa: ANN401
                asked.append(sum(len(v) for v in wanted.values()))
                return {}, set()

        run.consumer._offset_fetcher = Fetcher()
        await run.consumer._IndexingKafkaConsumer__fetch_back_remembered()
        await asyncio.sleep(0.02)
    return sum(asked)


async def test_one_plan_asks_a_shared_bucket_for_no_more_than_its_room_in_total() -> None:
    assert await planned_total() == 12


async def test_connectors_sharing_a_bucket_take_turns_at_being_first_in_the_plan() -> None:
    async with world.kafka_world(
        [], fair=world.fair_config(max_per_entity_messages=500, max_buffered_messages=2000), produce=False,
        consumer_kwargs={"fairness": bundle(buffer_room=lambda key, s: ("org", 12 - s.pending_count_for(key[:1])))},
    ) as run:
        from aiokafka.structs import TopicPartition

        tp = TopicPartition("record-events", 0)
        for c in range(2):
            for offset in range(30):
                run.consumer._remembered.remember(("org-0", f"conn-{c}"), tp, c * 100 + offset)
        asked: list[list[int]] = []

        class Fetcher:
            async def fetch(self, wanted: dict, **_k: Any) -> tuple[dict, set]:  # noqa: ANN401
                asked.append(sorted(o for offsets in wanted.values() for o in offsets))
                return {}, set()

        run.consumer._offset_fetcher = Fetcher()
        for _ in range(2):
            await run.consumer._IndexingKafkaConsumer__fetch_back_remembered()  # starts a fetch
            await asyncio.sleep(0.02)
            await run.consumer._IndexingKafkaConsumer__fetch_back_remembered()  # takes its result
    firsts = {offsets[0] // 100 for offsets in asked}
    assert len(asked) == 2 and all(len(offsets) == 12 for offsets in asked)
    assert firsts == {0, 1}, "one connector took the bucket's whole room in every plan"


async def test_a_plan_without_the_running_bucket_total_asks_for_more_than_twice_the_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: every connector planning the bucket's whole room, as the earlier rule did."""
    from app.services.messaging.kafka.consumer.indexing_consumer import (
        IndexingKafkaConsumer,
    )

    name = "_IndexingKafkaConsumer__fetch_back_remembered"
    monkeypatch.setattr(IndexingKafkaConsumer, name, getattr(IndexingKafkaConsumer, name))
    mutate(IndexingKafkaConsumer, name, [("left = shared[1] - planned.get(shared[0], 0)", "left = shared[1]")])
    assert await planned_total() > 2 * 12


async def test_fetch_back_takes_what_a_small_bucket_has_left_and_nothing_when_it_has_none() -> None:
    async with world.kafka_world(
        [], fair=world.fair_config(max_per_entity_messages=20, max_buffered_messages=200),
        consumer_kwargs={"fairness": bundle(buffer_room=lambda key, _s: ("b", ROOM["left"]))}, produce=False,
    ) as run:
        consumer = run.consumer
        from aiokafka.structs import TopicPartition

        tp = TopicPartition("record-events", 0)
        for offset in range(30):
            consumer._remembered.remember(("org-0", "conn-a"), tp, offset)
        asked: list[int] = []

        class Fetcher:
            async def fetch(self, wanted: dict, **_k: Any) -> tuple[dict, set]:  # noqa: ANN401
                asked.append(sum(len(v) for v in wanted.values()))
                return {}, set()

        consumer._offset_fetcher = Fetcher()
        for left in (0, -2, 3, 12):
            ROOM["left"] = left
            await consumer._IndexingKafkaConsumer__fetch_back_remembered()  # starts a fetch, or not
            await asyncio.sleep(0.02)
            await consumer._IndexingKafkaConsumer__fetch_back_remembered()  # takes its result
        # room of 3, below the chunk threshold of min(cap // 2, remembered) = 10: still fetched, up to the room
        assert asked == [3, 12]


ROOM = {"left": 0}


# -- O-14: the read budget for extra topics and the shared cursor ----------------------------------------------------
BUDGET = 100  # two streams' worth of a batch each (the buffer of 2000 shared by 44 streams gives 45 apiece)
BASE_STREAMS = [f"record-events.{i}" for i in range(4)]
EXTRA_STREAMS = [f"extra-events.{i}" for i in range(40)]


async def test_a_small_budget_reads_a_few_extra_streams_a_pass_base_streams_always_and_loses_nothing() -> None:
    messages = world.generate_messages(31, 600)
    streams_of = lambda m: (EXTRA_STREAMS if m.record_id[-1] in "01234" else BASE_STREAMS)[  # noqa: E731
        world.lane_of(m.connector_id, 40 if m.record_id[-1] in "01234" else 4) % (40 if m.record_id[-1] in "01234" else 4)
    ]
    reads: list[tuple[int, int, int]] = []
    fairness = bundle(extra_topics=("extra-events",), extra_read_budget=lambda _s: BUDGET)
    async with world.redis_world(
        messages, topics=[*BASE_STREAMS, *EXTRA_STREAMS], stream_of=streams_of, batch_size=500,
        fair=world.fair_config(max_buffered_messages=2000, max_per_entity_messages=500),
        consumer_kwargs={"fairness": fairness},
    ) as run:
        real = run.consumer._xreadgroup_grouped

        async def spy(streams: dict, count: int, block_ms: Any) -> Any:  # noqa: ANN401
            base = sum(1 for s in streams if s in BASE_STREAMS)
            reads.append((base, len(streams) - base, count))
            return await real(streams, count, block_ms)

        run.consumer._xreadgroup_grouped = spy
        for _ in range(900):
            await world.redis_step(run)
            if len(run.consumer.acks) >= len(messages):
                break
        assert len(run.consumer.acks) == len(messages)
        assert {m.record_id for m in messages} <= set(run.handler.seen)
    assert reads
    assert all(base == len(BASE_STREAMS) for base, _extra, _count in reads)
    assert all(extra * count <= BUDGET for _base, extra, count in reads), max(e * c for _b, e, c in reads)
    assert any(extra for _base, extra, _count in reads)


async def test_with_no_budget_every_extra_stream_is_read_in_one_pass() -> None:
    fairness = bundle(extra_topics=("extra-events",))
    async with world.redis_world(
        [], topics=[*BASE_STREAMS, *EXTRA_STREAMS], produce=False, consumer_kwargs={"fairness": fairness},
        fair=world.fair_config(max_buffered_messages=2000, max_per_entity_messages=500),
    ) as run:
        seen: list[int] = []
        real = run.consumer._xreadgroup_grouped

        async def spy(streams: dict, count: int, block_ms: Any) -> Any:  # noqa: ANN401
            seen.append(len(streams))
            return await real(streams, count, block_ms)

        run.consumer._xreadgroup_grouped = spy
        await world.redis_step(run)
    assert seen == [len(BASE_STREAMS) + len(EXTRA_STREAMS)]


async def one_budget_pass(budget: int, room: int = 2000, offset: int = 0, batch_size: int = 500) -> list[str]:
    """The streams one read phase reads, with 4 base streams, 40 extra ones and the given budget."""
    from app.services.messaging.redis_streams.indexing_consumer import (
        IndexingRedisStreamsConsumer,
    )

    fairness = bundle(extra_topics=("extra-events",), extra_read_budget=lambda _s: budget)
    config = RedisStreamsConfig(
        host="h", port=1, client_id="c", group_id="g", topics=[*BASE_STREAMS, *EXTRA_STREAMS], batch_size=batch_size, block_ms=1
    )
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("edges"), config, fair_scheduler_config=world.fair_config(), fairness=fairness
    )
    consumer.redis = AsyncMock()
    consumer.main_loop = asyncio.get_running_loop()
    consumer.running = True
    consumer._read_offset = offset
    read: list[str] = []

    async def grouped(streams: dict, count: int, block_ms: Any) -> list:  # noqa: ANN401
        read.extend(streams)
        return []

    consumer._xreadgroup_grouped = grouped
    consumer._IndexingRedisStreamsConsumer__buffer_room = lambda: room
    consumer._IndexingRedisStreamsConsumer__drain_deferred = AsyncMock()
    await consumer._IndexingRedisStreamsConsumer__read_phase()
    return read


@pytest.mark.parametrize("budget", [-100, -45, -1, 0, 1, 44])
async def test_a_budget_below_one_batch_or_below_zero_reads_no_extra_stream(budget: int) -> None:
    read = await one_budget_pass(budget)  # 44 streams share 2000 places: 45 per stream
    assert [s for s in read if s in EXTRA_STREAMS] == []
    assert [s for s in read if s in BASE_STREAMS] == BASE_STREAMS


async def test_the_budget_is_counted_in_entries_a_read_takes_not_in_places_the_buffer_has() -> None:
    # 44 streams share 2000 places (45 each), but a read takes at most batch_size = 10 entries from a stream
    read = await one_budget_pass(100, batch_size=10)
    assert len([s for s in read if s in EXTRA_STREAMS]) == 10


async def test_a_read_pass_with_no_stream_left_by_the_budget_neither_reads_nor_blocks() -> None:
    """A full buffer shortens the read window, which then lands on extra streams only; with no budget the pass must
    not call a read with no stream (it sleeps a whole block time)."""
    fairness = bundle(extra_topics=("extra-events",), extra_read_budget=lambda _s: 0)
    async with world.redis_world(
        [], topics=[*SWEEP_BASE, *SWEEP_EXTRA], produce=False, consumer_kwargs={"fairness": fairness},
    ) as run:
        consumer = run.consumer
        consumer.config.block_ms = 1500
        consumer._IndexingRedisStreamsConsumer__buffer_room = lambda: 2  # two places: a window of two streams
        real = consumer._xreadgroup_grouped
        asked: list[dict] = []

        async def spy(streams: dict, count: int, block_ms: Any) -> Any:  # noqa: ANN401
            asked.append(dict(streams))
            assert streams, "a read with no stream sleeps the whole block time"
            return await real(streams, count, block_ms)

        consumer._xreadgroup_grouped = spy
        started = time.monotonic()
        for _ in range(16):  # two rounds over the 8 windows, 4 of them extra streams only
            await consumer._IndexingRedisStreamsConsumer__read_phase()
        elapsed = time.monotonic() - started
    assert asked and all(set(streams) <= set(SWEEP_BASE) for streams in asked)
    # wall-clock-ok: 16 read phases; the earlier behaviour slept 1.5 s in each extra-stream window, 6 s or more
    assert elapsed < 1.4, elapsed  # the earlier behaviour sleeps 1.5 s for each of those windows


async def test_a_pass_with_no_stream_left_forgets_which_lanes_had_data_so_parked_base_lanes_are_read() -> None:
    from app.services.messaging.redis_streams.indexing_consumer import (
        IndexingRedisStreamsConsumer,
    )

    fairness = bundle(extra_topics=("extra-events",), extra_read_budget=lambda _s: 0)
    config = RedisStreamsConfig(
        host="h", port=1, client_id="c", group_id="g", topics=["record-events", "extra-events.0", "extra-events.1"],
        batch_size=500, block_ms=1,
    )
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("lanes"), config, fair_scheduler_config=world.fair_config(), fairness=fairness
    )
    consumer.redis = AsyncMock()
    consumer.main_loop = asyncio.get_running_loop()
    consumer.running = True
    read: list[list[str]] = []

    async def grouped(streams: dict, count: int, block_ms: Any) -> list:  # noqa: ANN401
        read.append(list(streams))
        return []

    consumer._xreadgroup_grouped = grouped
    consumer._IndexingRedisStreamsConsumer__buffer_room = lambda: 2000
    consumer._IndexingRedisStreamsConsumer__drain_deferred = AsyncMock()
    # the only base lane holds a parked entry, and the last read saw data on an extra lane only
    consumer._deferred_entries.append(("record-events", "0-0", {}, None, 0.0, time.monotonic()))
    consumer._lanes_with_data = {"extra-events.0"}
    for _ in range(2):
        await consumer._IndexingRedisStreamsConsumer__read_phase()
    assert read and "record-events" in read[-1]  # the first pass reads nothing; the next one probes the parked lane


async def test_a_short_window_of_extra_streams_only_with_no_budget_left_reads_nothing_and_does_not_fail() -> None:
    assert await one_budget_pass(0, room=2, offset=10) == []  # the window is two extra streams


SWEEP_BASE = [f"record-events.{i}" for i in range(8)]
SWEEP_EXTRA = [f"extra-events.{i}" for i in range(8)]


async def never_read_extra(room: int, budget: int | None, alternate: bool, passes: int = 120) -> set[str]:
    """Extra streams never read in ``passes`` reads at a steady buffer room, through the real read phase."""
    from app.services.messaging.redis_streams.indexing_consumer import (
        IndexingRedisStreamsConsumer,
    )

    fairness = bundle(extra_topics=("extra-events",), extra_read_budget=(lambda _s: budget) if budget is not None else None)
    config = RedisStreamsConfig(
        host="h", port=1, client_id="c", group_id="g", topics=[*SWEEP_BASE, *SWEEP_EXTRA], batch_size=500, block_ms=1
    )
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("sweep"), config, fair_scheduler_config=world.fair_config(), fairness=fairness
    )
    consumer.redis = AsyncMock()
    consumer.main_loop = asyncio.get_running_loop()
    consumer.running = True
    reads: set[str] = set()

    async def grouped(streams: dict, count: int, block_ms: Any) -> list:  # noqa: ANN401
        reads.update(streams)
        return []

    consumer._xreadgroup_grouped = grouped
    consumer._IndexingRedisStreamsConsumer__buffer_room = lambda: room
    consumer._IndexingRedisStreamsConsumer__drain_deferred = AsyncMock()
    for p in range(passes):
        consumer._deferred_entries.clear()
        if alternate and p % 2:
            consumer._deferred_entries.extend((stream, "0-0", {}, None, 0.0, time.monotonic()) for stream in SWEEP_EXTRA)
            consumer._lanes_with_data = set(SWEEP_BASE)
        else:
            consumer._lanes_with_data = set()
        await consumer._IndexingRedisStreamsConsumer__read_phase()
    return set(SWEEP_EXTRA) - reads


async def sweep_violations(budgets: tuple[int, ...] = (1, 2, 3), stop_at_first: bool = False) -> list[tuple[int, int, bool]]:
    """Steady states where the budget leaves an extra stream unread although reading with no budget leaves none."""
    found: list[tuple[int, int, bool]] = []
    for room in range(1, 21):
        for alternate in (False, True):
            control = await never_read_extra(room, None, alternate)
            for budget in budgets:
                if not control and await never_read_extra(room, budget, alternate):
                    found.append((room, budget, alternate))
                    if stop_at_first:
                        return found
    return found


async def test_steady_state_sweep_through_the_real_read_phase_leaves_no_extra_stream_starved() -> None:
    assert await sweep_violations() == []


@pytest.mark.parametrize(
    ("label", "replacements"),
    [
        (
            "a separate cursor",
            [(
                "start = self._read_offset % len(topics)\n        chosen = sorted(extra, key=lambda t: (topics.index(t) - start) % len(topics))",
                "extras = [t for t in topics if t in extra or t.startswith('extra-events')]\n"
                "        start = getattr(self, '_own', 0) % len(extras)\n"
                "        chosen = sorted(extra, key=lambda t: (extras.index(t) - start) % len(extras))",
            ), (
                "self._read_offset = topics.index(chosen[-1]) + 1\n        streams",
                "self._own = extras.index(chosen[-1]) + 1\n        streams",
            )],
        ),
        (
            "no cursor advance",
            [("if chosen:\n            self._read_offset = topics.index(chosen[-1]) + 1\n        streams", "streams")],
        ),
    ],
)
async def test_the_other_readings_of_the_cursor_starve_streams_and_are_noticed(
    monkeypatch: pytest.MonkeyPatch, label: str, replacements: list[tuple[str, str]]
) -> None:
    from app.services.messaging.redis_streams.indexing_consumer import (
        IndexingRedisStreamsConsumer,
    )

    name = "_IndexingRedisStreamsConsumer__read_phase"
    monkeypatch.setattr(IndexingRedisStreamsConsumer, name, getattr(IndexingRedisStreamsConsumer, name))
    mutate(IndexingRedisStreamsConsumer, name, replacements)
    assert await sweep_violations(stop_at_first=True), label


# -- O-8: claim steps on the PostgreSQL queue ---------------------------------------------------------------------------
class Uneven(world.Handler):
    """Records take 0 to 60 ms, so some finish while others run on."""

    async def __call__(self, message: Any) -> Any:  # noqa: ANN401
        await asyncio.sleep(0.02 * (int(str(message.payload["recordId"]).split("-")[1]) % 4))
        async for event in super().__call__(message):
            yield event


async def test_claim_steps_claim_one_topic_at_a_time_in_the_order_and_amounts_given() -> None:
    messages = world.generate_messages(41, 120)
    seen: list[tuple[dict[str, int], dict[str, int]]] = []
    holder: dict[str, Any] = {}

    def steps(free: int, in_flight: Any) -> list[tuple[str, int]]:  # noqa: ANN401
        held = Counter(topic for topic, _id in holder["consumer"]._held)
        seen.append((dict(in_flight), dict(held)))
        return [("aux-events", 2), ("record-events", free), ("empty-events", 5)]

    trace, _consumer, _runtime = await world.run_pg(
        messages, consumer_kwargs={"fairness": bundle(claim_steps=steps)}, on_claim=lambda c: holder.update(consumer=c),
        handler=Uneven(),
    )
    claims = [s[2] for s in trace.extra["statements"] if s[0] == "claim"]
    assert {m.record_id for m in messages} == set(trace.dispatch)
    assert claims and all(len(topics) == 1 for _group, topics, *_rest in claims)
    assert {topics[0] for _group, topics, *_rest in claims} <= {"aux-events", "record-events", "empty-events"}
    assert all(limit <= 2 for _g, topics, limit, *_rest in claims if topics == ["aux-events"])
    assert all(limit <= 5 for _g, topics, limit, *_rest in claims if topics == ["empty-events"])
    assert all(limit >= 1 for *_x, limit, _v, _c in claims)
    # the first claim of a pass is the first step's topic
    assert claims[0][1] == ["aux-events"]
    # the handlers in flight per topic are what the steps are told
    assert all(told == held for told, held in seen)
    assert any(told for told, _held in seen), "some pass starts while a handler is still running"
    assert all(set(told) <= {"aux-events", "record-events"} for told, _held in seen)


async def test_claim_steps_stop_once_the_free_places_are_used() -> None:
    messages = world.generate_messages(42, 60)
    asked: list[int] = []
    calls: list[tuple[list[str], int]] = []

    def steps(free: int, _in_flight: Any) -> list[tuple[str, int]]:  # noqa: ANN401
        asked.append(free)
        return [("aux-events", 100), ("record-events", 100)]

    def spy(consumer: Any) -> None:  # noqa: ANN401
        real = consumer.queue.claim

        async def claim(group: str, topics: list[str], who: str, limit: int, visibility: float, **kw: Any) -> Any:  # noqa: ANN401
            calls.append((list(topics), limit))
            return await real(group, topics, who, limit, visibility, **kw)

        consumer.queue.claim = claim

    await world.run_pg(
        messages, consumer_kwargs={"fairness": bundle(claim_steps=steps)}, batch_size=3, concurrency=3, on_claim=spy
    )
    # 30 messages on each topic, 3 places a pass, 100 rows offered: while the first topic has rows, its claim takes
    # the whole pass, and the second topic is not asked at all (not even for nothing)
    assert calls[:10] == [(["aux-events"], 3)] * 10
    assert all(limit > 0 for _topics, limit in calls)
    assert asked and max(asked) <= 3


async def test_a_bundle_without_claim_steps_claims_as_before() -> None:
    trace, _c, _r = await world.run_pg(
        world.generate_messages(world.GOLDEN_SEED, world.GOLDEN_SIZES["pg"]), consumer_kwargs={"fairness": bundle()}
    )
    assert world.summarise(json.loads(json.dumps(trace.to_json()))) == golden("pg")


async def test_the_replay_leaves_no_delivery_task_running(monkeypatch: pytest.MonkeyPatch) -> None:
    class Slow(world.Handler):
        async def __call__(self, message: Any) -> Any:  # noqa: ANN401
            await asyncio.sleep(0.5)
            async for event in super().__call__(message):
                yield event

    seen: dict[str, Any] = {}
    await world.run_pg(
        world.generate_messages(61, 6), handler=Slow(), max_seconds=0.3, concurrency=3, on_claim=lambda c: seen.update(c=c)
    )
    assert all(t.done() for t in seen["c"]._tasks)


# -- O-12: the observer ---------------------------------------------------------------------------------------------
async def test_the_bundles_observer_is_told_entered_and_left_once_each_per_wait() -> None:
    observer = Observer()
    messages = world.generate_messages(51, 300)
    await world.run_kafka(
        messages, consumer_kwargs={"fairness": bundle(observer=observer)},
        fair=world.fair_config(),
    )
    assert observer.entries, "records wait for admission in this world"
    assert len(observer.entries) == len(observer.exits)
    assert sorted(map(repr, observer.entries)) == sorted(map(repr, observer.exits))


async def test_the_bundles_observer_is_told_on_redis_streams_too() -> None:
    observer = Observer()
    messages = world.generate_messages(52, 300)
    await world.run_redis(messages, consumer_kwargs={"fairness": bundle(observer=observer)})
    assert observer.entries, "records wait for admission in this world"
    assert len(observer.entries) == len(observer.exits)
    assert sorted(map(repr, observer.entries)) == sorted(map(repr, observer.exits))


# -- O-9, O-10: how the factory takes the bundle ------------------------------------------------------------------------
def kafka_config() -> KafkaConsumerConfig:
    return KafkaConsumerConfig(
        topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["k:1"],
    )


def redis_config() -> RedisStreamsConfig:
    return RedisStreamsConfig(host="h", port=1, client_id="c", group_id="g", topics=["record-events"])


def make(kind: str, fairness: IndexingFairness | None, **extra: Any) -> Any:  # noqa: ANN401
    config = kafka_config() if kind == "kafka" else redis_config()
    broker = {"kafka": MessageBrokerType.KAFKA, "redis": MessageBrokerType.REDIS, "pg": MessageBrokerType.POSTGRES}[kind]
    return MessagingFactory.create_consumer(
        logging.getLogger("factory"), config, broker_type=broker, consumer_type=extra.pop("consumer_type", ConsumerType.INDEXING),
        fairness=fairness, **extra,
    )


def topics_of(consumer: Any, kind: str) -> list[str]:  # noqa: ANN401
    return list((consumer.kafka_config if kind == "kafka" else consumer.config).topics)


class Weights:
    def quantum_for(self, key: tuple[str, ...]) -> int:
        return 1 + len(key)


@pytest.mark.parametrize("kind", ["kafka", "redis", "pg"])
def test_the_factory_applies_the_bundle_to_the_record_consumer(kind: str) -> None:
    observer, weights, room = Observer(), Weights(), OrgRoom(5)
    fairness = bundle(
        weights=weights, planner=SpyPlanner, buffer_room=room, observer=observer, extra_topics=("extra-events",),
        extra_read_budget=lambda _s: 3, claim_steps=lambda free, _held: [("record-events", free)],
    )
    consumer = make(kind, fairness)
    assert consumer._fairness is fairness
    assert topics_of(consumer, kind) == ["record-events", "extra-events"]
    if kind == "pg":
        return
    assert consumer.key_extractor.inner is fairness.key_extractor
    assert consumer.weight_provider is weights
    assert isinstance(consumer._slot_share, SpyPlanner)
    assert consumer.admission_backlog.observer is observer
    assert consumer._scheduler.enqueue(("o", "c", "heavy"), 1) == EnqueueResult.ACCEPTED
    assert room.calls == 1


@pytest.mark.parametrize("kind", ["kafka", "redis", "pg"])
def test_explicit_arguments_win_and_the_bundle_is_then_left_out_whole(kind: str) -> None:
    fairness = bundle(extra_topics=("extra-events",), observer=Observer())
    mine = CompositeKeyExtractor(fields=KEY_FIELDS)
    consumer = make(kind, fairness, **({"key_extractor": mine, "weight_provider": Weights()} if kind != "pg" else {"stage_admission": MagicMock()}))
    assert consumer._fairness is None
    assert topics_of(consumer, kind) == ["record-events"]
    if kind != "pg":
        assert consumer.key_extractor.inner is mine
        assert consumer.admission_backlog.observer is None


@pytest.mark.parametrize("kind", ["kafka", "redis"])
def test_the_bundle_is_ignored_with_fair_scheduling_off_and_warned_once(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    logging.disable(logging.NOTSET)
    caplog.set_level(logging.WARNING)
    consumer = make(kind, bundle(extra_topics=("extra-events",)), fair_scheduler_config=FairSchedulerConfig(enabled=False))
    assert consumer._fairness is None
    assert topics_of(consumer, kind) == ["record-events"]
    assert sum("fair scheduling is off or key fields are set" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize("kind", ["kafka", "redis"])
def test_the_bundle_is_ignored_when_the_operator_chose_the_key_fields(kind: str) -> None:
    consumer = make(
        kind, bundle(extra_topics=("extra-events",)),
        fair_scheduler_config=FairSchedulerConfig(enabled=True, key_fields=("orgId",)),
    )
    assert consumer._fairness is None
    assert topics_of(consumer, kind) == ["record-events"]


@pytest.mark.parametrize("kind", ["kafka", "redis", "pg"])
@pytest.mark.parametrize("other", ["priority_lane", "stage_admission"])
def test_parse_now_and_stage_consumers_ignore_the_bundle_and_get_no_observer(kind: str, other: str) -> None:
    lane = SimpleNamespace(limit=1, light_limit=1, heavy_limit=1)
    consumer = make(kind, bundle(extra_topics=("extra-events",), observer=Observer()), **{other: MagicMock() if other == "stage_admission" else lane})
    assert consumer._fairness is None
    assert topics_of(consumer, kind) == ["record-events"]
    if kind != "pg":
        assert consumer.admission_backlog.observer is None


def test_a_simple_consumer_takes_no_bundle() -> None:
    consumer = make("redis", bundle(extra_topics=("extra-events",)), consumer_type=ConsumerType.SIMPLE)
    assert consumer.config.topics == ["record-events"]
    assert not hasattr(consumer, "_fairness")


def test_only_the_record_consumer_call_in_the_indexing_main_passes_the_bundle() -> None:
    source = (Path(world.__file__).parents[2] / "app" / "indexing_main.py").read_text()
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_consumer"
    ]
    with_bundle = [c for c in calls if any(k.arg == "fairness" for k in c.keywords)]
    assert len(calls) >= 3
    assert len(with_bundle) == 1
    config = next(k.value for k in with_bundle[0].keywords if k.arg == "config")
    assert isinstance(config, ast.Name) and config.id == "record_consumer_config"
    assert not any(k.arg in {"priority_lane", "stage_admission"} for k in with_bundle[0].keywords)


def test_the_default_hook_supplies_nothing() -> None:
    from app import edition_services

    assert edition_services.indexing_fairness(lambda _pool: 1) is None


def test_a_stream_message_round_trips_through_the_bundle_extractor() -> None:
    message = StreamMessage(eventType="newRecord", payload={"orgId": "o", "connectorId": "c"}, timestamp=1)
    assert bundle().key_extractor.extract(message) == ("o", "c")
