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
from collections import defaultdict
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


async def fetched_per_buffered(seed: int, limit: int) -> float:
    """Messages fetched back over messages buffered from remembered positions, for a world of two orgs whose
    connectors share each org's room."""
    messages = world.generate_messages(seed, 900, orgs=2, hot_share=0.5)
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

    await world.run_kafka(
        messages, consumer_kwargs={"fairness": bundle(buffer_room=OrgRoom(limit))}, on_step=watch,
        fair=world.fair_config(max_per_entity_messages=40, max_buffered_messages=200),
    )
    return state["fetcher"].fetched / max(1, state["pops"])


FETCH_WORLDS = [(21, 25), (22, 40), (23, 60), (24, 30), (25, 50), (26, 45)]


async def test_fetch_back_fetches_at_most_twice_what_it_buffers_in_random_worlds() -> None:
    ratios = [await fetched_per_buffered(seed, limit) for seed, limit in FETCH_WORLDS]
    assert max(ratios) <= 2.0, ratios


async def test_fetch_back_without_the_running_bucket_total_fetches_more_than_twice_what_it_buffers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: every entity planning the bucket's whole room, as the earlier rule did."""
    from app.services.messaging.kafka.consumer.indexing_consumer import (
        IndexingKafkaConsumer,
    )

    name = "_IndexingKafkaConsumer__fetch_back_remembered"
    monkeypatch.setattr(IndexingKafkaConsumer, name, getattr(IndexingKafkaConsumer, name))
    mutate(IndexingKafkaConsumer, name, [("shared[1] - planned.get(shared[0], 0)", "shared[1]")])
    ratios = [await fetched_per_buffered(seed, limit) for seed, limit in FETCH_WORLDS]
    assert max(ratios) > 2.0, ratios


async def test_fetch_back_waits_while_the_room_left_is_below_the_chunk_threshold() -> None:
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
        for left in (3, 9):
            ROOM["left"] = left
            await consumer._IndexingKafkaConsumer__fetch_back_remembered()
        assert asked == []  # the chunk threshold is min(cap // 2, remembered) = 10
        ROOM["left"] = 12
        await consumer._IndexingKafkaConsumer__fetch_back_remembered()
        await asyncio.sleep(0.05)
        assert asked == [12]


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
async def test_claim_steps_claim_one_topic_at_a_time_in_the_order_and_amounts_given() -> None:
    messages = world.generate_messages(41, 300)
    snapshots: list[dict[str, int]] = []

    def steps(free: int, in_flight: Any) -> list[tuple[str, int]]:  # noqa: ANN401
        snapshots.append(dict(in_flight))
        return [("aux-events", 2), ("record-events", free), ("ignored-because-full", 5)]

    trace, _consumer, _runtime = await world.run_pg(messages, consumer_kwargs={"fairness": bundle(claim_steps=steps)})
    claims = [s[2] for s in trace.extra["statements"] if s[0] == "claim"]
    assert {m.record_id for m in messages} == set(trace.dispatch)
    assert claims and all(len(topics) == 1 for _group, topics, *_rest in claims)
    assert {topics[0] for _group, topics, *_rest in claims} <= {"aux-events", "record-events"}
    assert all(limit <= 2 for _g, topics, limit, *_rest in claims if topics == ["aux-events"])
    assert all(limit >= 1 for *_x, limit, _v, _c in claims)
    # the first claim of a pass is the first step's topic
    assert claims[0][1] == ["aux-events"]
    assert all(set(s) <= {"aux-events", "record-events"} and max(s.values(), default=0) <= 4 for s in snapshots)
    assert snapshots


async def test_claim_steps_stop_once_the_free_places_are_used() -> None:
    messages = world.generate_messages(42, 60)
    asked: list[int] = []

    def steps(free: int, _in_flight: Any) -> list[tuple[str, int]]:  # noqa: ANN401
        asked.append(free)
        return [("aux-events", 100), ("record-events", 100)]

    trace, _c, _r = await world.run_pg(
        messages, consumer_kwargs={"fairness": bundle(claim_steps=steps)}, batch_size=3, concurrency=3
    )
    claims = [s[2] for s in trace.extra["statements"] if s[0] == "claim"]
    # with 3 places and 100 rows offered, the first topic's claim takes the whole pass; the second is not asked
    assert claims[0][1] == ["aux-events"] and claims[0][2] <= 3
    assert asked and max(asked) <= 3


async def test_a_bundle_without_claim_steps_claims_as_before() -> None:
    trace, _c, _r = await world.run_pg(
        world.generate_messages(world.GOLDEN_SEED, world.GOLDEN_SIZES["pg"]), consumer_kwargs={"fairness": bundle()}
    )
    assert world.summarise(json.loads(json.dumps(trace.to_json()))) == golden("pg")


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
