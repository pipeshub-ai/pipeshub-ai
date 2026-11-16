"""Live counts of records waiting at each admission stage, and the consumer-side explain built on them.

Each test names a broken variant of the behaviour in its docstring or a ``_broken`` helper, and
shows the check rejecting it.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import re
import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.messaging import consumer_concurrency as cc
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.consumer_concurrency import (
    PHASE_DOWNLOAD,
    PHASE_GATE,
    PHASE_LEASE,
    PHASE_PARSE,
    AdmissionBacklog,
    GateWaiters,
    GateWaiterToken,
    LocalPermits,
    StageAdmission,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.resource_governor.models import ParseTier
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

H, L = ParseTier.HEAVY, ParseTier.LIGHT
PHASES = (PHASE_GATE, PHASE_LEASE, PHASE_DOWNLOAD, PHASE_PARSE)


def _random_trace(seed: int, steps: int = 400) -> tuple[AdmissionBacklog, dict[int, tuple[ParseTier, str]]]:
    rng = random.Random(seed)
    backlog = AdmissionBacklog()
    live: dict[int, tuple[ParseTier, str]] = {}
    nid = 0
    for _ in range(steps):
        if live and rng.random() < 0.45:
            key = rng.choice(list(live))
            tier, phase = live.pop(key)
            backlog.remove(tier, phase)
        else:
            tier = rng.choice((H, L))
            phase = rng.choice([p for p in PHASES if not (tier is L and p == PHASE_DOWNLOAD)])
            nid += 1
            live[nid] = (tier, phase)
            backlog.add(tier, phase)
    return backlog, live


class TestBacklogMatchesAnOracle:
    """"""

    @pytest.mark.parametrize("seed", [11, 4242, 9001, None])
    def test_totals_equal_a_recount_of_the_live_waits(self, seed: int | None) -> None:
        seed = random.randrange(10**6) if seed is None else seed  # None: a fresh seed each run
        backlog, live = _random_trace(seed)
        totals = backlog.totals()
        for tier in (H, L):
            for phase in PHASES:
                assert totals[tier][phase] == sum(1 for t, p in live.values() if t is tier and p == phase)

    def test_counting_at_spawn_instead_of_per_phase_would_disagree(self) -> None:
        """Negative control: a count that includes the time between phases is not the oracle's."""
        backlog, live = _random_trace(7)
        spawned_total = len(live)
        assert sum(sum(v.values()) for v in backlog.totals().values()) == spawned_total
        backlog.add(H, PHASE_GATE)  # a record counted while backing off would add one
        assert sum(sum(v.values()) for v in backlog.totals().values()) != spawned_total

    def test_lease_waiting_is_per_pool(self) -> None:
        backlog = AdmissionBacklog()
        backlog.add(H, PHASE_LEASE, "indexing")
        backlog.add(H, PHASE_LEASE, "indexing")
        backlog.add(L, PHASE_LEASE, "parsing:light")
        assert backlog.lease_waiting() == {"indexing": 2, "parsing:light": 1}
        backlog.remove(H, PHASE_LEASE, "indexing")
        backlog.remove(H, PHASE_LEASE, "indexing")
        assert backlog.lease_waiting() == {"parsing:light": 1}

    def test_concurrent_writers_and_a_reader_stay_consistent(self) -> None:
        backlog = AdmissionBacklog()
        stop = threading.Event()
        failures: list[str] = []

        def writer() -> None:
            for _ in range(20_000):
                backlog.add(H, PHASE_GATE)
                backlog.remove(H, PHASE_GATE)

        def reader() -> None:
            while not stop.is_set():
                if backlog.totals()[H][PHASE_GATE] < 0:
                    failures.append("negative total")

        threads = [threading.Thread(target=writer) for _ in range(3)]
        watcher = threading.Thread(target=reader)
        watcher.start()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        watcher.join()
        assert failures == []
        assert backlog.totals()[H][PHASE_GATE] == 0


class _Recorder:
    """An observer that keeps what it is told, to show the hook sees every entry and exit with the message."""

    def __init__(self) -> None:
        self.events: list[tuple[str, ParseTier, str, object]] = []

    def entered(self, tier: ParseTier, phase: str, message: object) -> None:
        self.events.append(("in", tier, phase, message))

    def left(self, tier: ParseTier, phase: str, message: object) -> None:
        self.events.append(("out", tier, phase, message))


class TestObserverHook:
    def test_an_observer_is_told_of_every_entry_and_exit_with_the_message(self) -> None:
        recorder = _Recorder()
        backlog = AdmissionBacklog(observer=recorder)
        marker = object()
        backlog.add(H, PHASE_GATE, None, marker)
        backlog.remove(H, PHASE_GATE, None, marker)
        assert recorder.events == [("in", H, PHASE_GATE, marker), ("out", H, PHASE_GATE, marker)]
        assert backlog.totals()[H][PHASE_GATE] == 0

    def test_without_an_observer_nothing_is_called(self) -> None:
        backlog = AdmissionBacklog()
        assert backlog.observer is None
        backlog.add(H, PHASE_GATE)

    def test_the_default_factory_gives_counts_only(self) -> None:
        from app import edition_services

        backlog = edition_services.admission_backlog()
        assert isinstance(backlog, AdmissionBacklog) and backlog.observer is None

    def test_the_gate_token_and_the_phase_helper_pass_the_message_they_hold(self) -> None:
        recorder = _Recorder()
        host = SimpleNamespace(admission_backlog=AdmissionBacklog(observer=recorder), gate_waiters=GateWaiters())
        message = SimpleNamespace(payload={})
        token = GateWaiterToken(host, H, message)
        token.enter_gate()
        token.admit()
        assert [e[0] for e in recorder.events] == ["in", "out"] and all(e[3] is message for e in recorder.events)


def _redis_consumer(client_id: str = "idx") -> IndexingRedisStreamsConsumer:
    return IndexingRedisStreamsConsumer(
        logging.getLogger("t"),
        RedisStreamsConfig(host="localhost", port=6379, client_id=client_id, group_id="g", topics=["t"]),
        retry_manager=None,
        governor=make_test_governor(),
    )


def _kafka_consumer() -> IndexingKafkaConsumer:
    return IndexingKafkaConsumer(
        logging.getLogger("t"),
        KafkaConsumerConfig(
            topics=["t"], client_id="idx", group_id="g", auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["b:9092"], ssl=False, sasl=None,
        ),
        governor=make_test_governor(),
    )


def _drive(consumer: IndexingKafkaConsumer | IndexingRedisStreamsConsumer) -> None:
    """The same waits on any consumer: tokens at the gate, then phases further in."""
    host = consumer
    first = GateWaiterToken(host, H)
    second = GateWaiterToken(host, L)
    third = GateWaiterToken(host, H)
    first.enter_gate()
    second.enter_gate()
    third.admit()  # never entered the gate: a no-op for the backlog
    backlog = host.admission_backlog
    backlog.add(H, PHASE_LEASE, "indexing")
    backlog.add(H, PHASE_DOWNLOAD)
    backlog.add(L, PHASE_PARSE)


class TestBrokerParity:
    """"""

    def _normalised(self, consumer: IndexingKafkaConsumer | IndexingRedisStreamsConsumer) -> dict[str, Any]:
        explain = consumer.admission_explain()
        explain = json.loads(json.dumps(explain))
        explain.pop("consumerId")
        explain.pop("dispatch", None)
        return explain

    def test_both_brokers_explain_one_trace_identically(self) -> None:
        redis, kafka = _redis_consumer(), _kafka_consumer()
        _drive(redis)
        _drive(kafka)
        assert self._normalised(redis) == self._normalised(kafka)
        assert self._normalised(redis)["backlog"]["tiers"]["heavy"]["atGate"] == 1

    def test_a_consumer_that_drops_a_field_breaks_parity(self) -> None:
        redis, kafka = _redis_consumer(), _kafka_consumer()
        _drive(redis)
        _drive(kafka)
        broken = self._normalised(kafka)
        del broken["suspectsWaiting"]
        assert self._normalised(redis) != broken

    def test_the_lease_owner_prefix_is_the_consumer_id_on_both_brokers(self) -> None:
        redis, kafka = _redis_consumer(), _kafka_consumer()
        assert redis.admission_explain()["consumerId"] == redis.consumer_name
        assert kafka.admission_explain()["consumerId"] == kafka._consumer_instance_id
        for consumer, ident in ((redis, redis.consumer_name), (kafka, kafka._consumer_instance_id)):
            owner = f"{ident}:{uuid.uuid4().hex[:6]}-0:{uuid.uuid4().hex}"
            assert owner.rsplit(":", 2)[0] == consumer.admission_explain()["consumerId"]

    def test_the_wrappers_build_owners_from_the_same_identifiers(self) -> None:
        redis_src = inspect.getsource(IndexingRedisStreamsConsumer)
        kafka_src = inspect.getsource(IndexingKafkaConsumer)
        assert re.search(r'lease_owner = f"\{self\.consumer_name\}:\{message_id\}:\{uuid\.uuid4\(\)\.hex\}"', redis_src)
        assert "{self._consumer_instance_id}:{message_id}:{uuid.uuid4().hex}" in kafka_src

    def test_an_id_without_the_uuid_suffix_does_not_match_an_owner(self) -> None:
        """Negative control: the configured client id alone is not the owner prefix."""
        redis = _redis_consumer("idx")
        owner = f"{redis.consumer_name}:1-0:{uuid.uuid4().hex}"
        assert owner.rsplit(":", 2)[0] != "idx"
        assert owner.rsplit(":", 2)[0] == redis.admission_explain()["consumerId"]

    def test_the_explain_is_json_and_carries_counts_only(self) -> None:
        redis = _redis_consumer()
        _drive(redis)
        text = json.dumps(redis.admission_explain())
        assert set(redis.admission_explain()["backlog"]) == {"tiers"}
        assert "message" not in text.lower()


@pytest.mark.asyncio
class TestPhasesEndWhenTheWaitEnds:
    async def test_in_phase_counts_through_grant_timeout_and_cancel(self) -> None:
        host = SimpleNamespace(admission_backlog=AdmissionBacklog())
        gate = asyncio.Event()

        async def wait(timeout: float | None = None) -> bool:
            await asyncio.wait_for(gate.wait(), timeout)
            return True

        task = asyncio.ensure_future(cc.in_phase(host, PHASE_PARSE, H, None, wait()))
        timed = asyncio.ensure_future(cc.in_phase(host, PHASE_PARSE, H, None, wait(0.05)))
        await asyncio.sleep(0.01)
        assert host.admission_backlog.totals()[H][PHASE_PARSE] == 2
        with pytest.raises(TimeoutError):
            await timed
        assert host.admission_backlog.totals()[H][PHASE_PARSE] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert host.admission_backlog.totals()[H][PHASE_PARSE] == 0

    async def test_a_host_without_a_backlog_is_a_plain_await(self) -> None:
        async def value() -> int:
            return 3

        assert await cc.in_phase(SimpleNamespace(), PHASE_GATE, H, None, value()) == 3

    def test_gate_token_counts_from_enter_until_admit_or_release(self) -> None:
        host = SimpleNamespace(admission_backlog=AdmissionBacklog(), gate_waiters=GateWaiters())
        token = GateWaiterToken(host, H)
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 0
        token.enter_gate()
        token.enter_gate()  # idempotent
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 1
        token.admit()
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 0
        token.release()
        assert host.gate_waiters.count() == 0

        released = GateWaiterToken(host, L)
        released.enter_gate()
        released.release()  # the task ended while still waiting
        assert host.admission_backlog.totals()[L][PHASE_GATE] == 0

    def test_a_priority_lane_record_leaves_the_gate_phase_but_stays_in_flight(self) -> None:
        host = SimpleNamespace(
            admission_backlog=AdmissionBacklog(), gate_waiters=GateWaiters(), priority_lane=cc.PriorityLane(2, 2)
        )
        token = GateWaiterToken(host, H)
        token.enter_gate()
        token.admit()
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 0
        assert host.gate_waiters.count(H) == 1


@pytest.mark.asyncio
class TestLocalPermits:
    """"""

    def _host(self, permits: int = 1) -> SimpleNamespace:
        return SimpleNamespace(
            governor=None, stage_admission=StageAdmission("embed", permits), priority_lane=None,
            indexing_semaphore=asyncio.Semaphore(permits), concurrency_manager=None,
            gate_waiters=GateWaiters(), admission_backlog=AdmissionBacklog(),
        )

    async def test_waiters_and_holders_are_counted_through_acquire_cancel_and_release(self) -> None:
        host = self._host(1)
        held = await cc.acquire_index_slot(host, H)
        waiter = asyncio.create_task(cc.acquire_index_slot(host, H))
        cancelled = asyncio.create_task(cc.acquire_index_slot(host, H))
        await asyncio.sleep(0.01)
        explain = cc.admission_explain(host)
        assert explain["localPermits"] == {"name": "embed", "limit": 1, "inUse": 1, "waiting": 2}
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert host.local_permits.waiting == 1
        cc.release_admission(held)
        second = await asyncio.wait_for(waiter, 2)
        assert (host.local_permits.in_use, host.local_permits.waiting) == (1, 0)
        cc.release_admission(second)
        assert host.local_permits.in_use == 0

    async def test_reading_the_semaphore_value_is_wrong_after_a_cancel(self) -> None:
        """Negative control: a cancelled waiter leaves ``Semaphore._value`` out of step with the holders."""
        semaphore = asyncio.Semaphore(1)
        await semaphore.acquire()
        waiter = asyncio.create_task(semaphore.acquire())
        await asyncio.sleep(0)
        waiter.cancel()
        semaphore.release()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.sleep(0)
        assert semaphore._value == 1  # holders read 0, as they should; waiting is not derivable from it

    async def test_a_governor_less_consumer_reports_permits_before_anything_waited(self) -> None:
        host = self._host(3)
        assert cc.admission_explain(host)["localPermits"] == {"name": "embed", "limit": 3, "inUse": 0, "waiting": 0}

    async def test_a_governor_consumer_has_no_local_permits(self) -> None:
        host = SimpleNamespace(
            governor=make_test_governor(), stage_admission=None, priority_lane=None,
            gate_waiters=GateWaiters(), admission_backlog=AdmissionBacklog(), concurrency_manager=None,
        )
        try:
            assert "localPermits" not in cc.admission_explain(host)
        finally:
            host.governor.close()


class TestExplainContent:
    def test_every_lease_pool_the_consumer_uses_is_listed_with_its_limit_and_waiters(self) -> None:
        consumer = _redis_consumer()
        consumer.concurrency_manager = object()
        consumer.admission_backlog.add(H, PHASE_LEASE, "indexing")
        explain = consumer.admission_explain()
        assert set(explain["leases"]) == {"indexing", "parsing", "parsing:light"} | (
            {"indexing:light"} if cc.messaging_env.split_index_lease_pools else set()
        )
        assert explain["leases"]["indexing"]["waiting"] == 1
        assert explain["leases"]["indexing"]["limit"] == cc.index_ceiling(consumer, H)

    def test_the_priority_lane_reports_its_caps_and_pools(self) -> None:
        host = SimpleNamespace(
            governor=make_test_governor(), stage_admission=None, priority_lane=cc.PriorityLane(2, 3, workers=2),
            gate_waiters=GateWaiters(), admission_backlog=AdmissionBacklog(), concurrency_manager=object(),
        )
        try:
            host.gate_waiters.add(H)
            host.admission_backlog.add(H, PHASE_GATE)
            explain = cc.admission_explain(host)
            assert explain["priority"]["heavy"] == {"inFlight": 1, "limit": 2, "gateWaiting": 1}
            assert explain["priority"]["light"]["limit"] == 3
            assert {"indexing:priority", "parsing:priority", "parsing:light:priority"} <= set(explain["leases"])
        finally:
            host.governor.close()

    def test_stage_pool_is_its_own_lease(self) -> None:
        host = SimpleNamespace(
            governor=None, stage_admission=StageAdmission("embed", 4, workers=2), priority_lane=None,
            gate_waiters=GateWaiters(), admission_backlog=AdmissionBacklog(), concurrency_manager=object(),
        )
        assert cc.admission_explain(host)["leases"] == {"stage:embed": {"limit": 8, "waiting": 0}}

    def test_buffered_is_spawned_minus_waiting_at_the_gate(self) -> None:
        host = SimpleNamespace(
            governor=None, stage_admission=None, priority_lane=None,
            gate_waiters=GateWaiters(), admission_backlog=AdmissionBacklog(), concurrency_manager=None,
        )
        for _ in range(5):
            host.gate_waiters.add(H)
        host.admission_backlog.add(H, PHASE_GATE)
        host.admission_backlog.add(H, PHASE_GATE)
        assert cc.admission_explain(host)["backlog"]["tiers"]["heavy"]["buffered"] == 3

    def test_suspects_and_read_pause_are_reported(self) -> None:
        consumer = _redis_consumer()
        consumer._suspects_waiting = 2
        consumer._backpressure_active = True
        explain = consumer.admission_explain()
        assert explain["suspectsWaiting"] == 2
        assert explain["readPause"]["admission"] is True
        assert LocalPermits  # imported for the permit tests above


class _Raising:
    def entered(self, *_args: object) -> None:
        raise RuntimeError("observer bug")

    def left(self, *_args: object) -> None:
        raise RuntimeError("observer bug")


class TestAnObserverThatRaises:
    def test_it_loses_no_count_and_does_not_fail_the_caller(self) -> None:
        backlog = AdmissionBacklog(observer=_Raising())
        backlog.add(H, PHASE_GATE)
        backlog.add(H, PHASE_LEASE, "indexing")
        assert backlog.totals()[H][PHASE_GATE] == 1 and backlog.lease_waiting() == {"indexing": 1}
        backlog.remove(H, PHASE_GATE)
        backlog.remove(H, PHASE_LEASE, "indexing")
        assert backlog.totals()[H][PHASE_GATE] == 0 and backlog.lease_waiting() == {}

    def test_the_gate_token_still_leaves_no_waiter_behind(self) -> None:
        host = SimpleNamespace(admission_backlog=AdmissionBacklog(observer=_Raising()), gate_waiters=GateWaiters())
        token = GateWaiterToken(host, H)
        token.enter_gate()
        token.admit()
        token.release()
        assert host.gate_waiters.count(H) == 0 and host.admission_backlog.totals()[H][PHASE_GATE] == 0

    @pytest.mark.asyncio
    async def test_in_phase_still_awaits_what_it_was_given(self) -> None:
        host = SimpleNamespace(admission_backlog=AdmissionBacklog(observer=_Raising()))

        async def work() -> int:
            return 7

        assert await cc.in_phase(host, PHASE_PARSE, H, None, work()) == 7
        assert host.admission_backlog.totals()[H][PHASE_PARSE] == 0

    def test_the_failure_is_reported_once(self, caplog: pytest.LogCaptureFixture) -> None:
        backlog = AdmissionBacklog(observer=_Raising())
        for _ in range(5):
            backlog.add(H, PHASE_GATE)
        assert sum("observer failed" in r.getMessage() for r in caplog.records) == 1

    def test_the_control_an_unguarded_call_would_raise_into_the_record_path(self) -> None:
        with pytest.raises(RuntimeError):
            _Raising().entered(H, PHASE_GATE, None)


class _Interleaver:
    """A backlog observer that runs *action* on another thread at one chosen step of the counter update.

    It is the cancellation callback of a future landing in the middle of the worker's own update. The
    other thread is given a bounded time to finish: if the token serialises the two, it is still waiting
    for the worker when the time is up, and finishes when the worker does.
    """

    def __init__(self, on: str, action: Any) -> None:  # noqa: ANN401
        self.on = on
        self.action = action
        self.done_early: bool | None = None
        self.thread: threading.Thread | None = None

    def _fire(self, call: str) -> None:
        if call != self.on or self.thread is not None:
            return
        self.thread = threading.Thread(target=self.action)
        self.thread.start()
        self.thread.join(timeout=0.3)
        self.done_early = not self.thread.is_alive()

    def entered(self, tier: ParseTier, phase: str, message: Any) -> None:  # noqa: ANN401
        self._fire("entered")

    def left(self, tier: ParseTier, phase: str, message: Any) -> None:  # noqa: ANN401
        self._fire("left")


class TestGateTokenIsExactlyOnceUnderConcurrentEnds:
    """The worker's gate entry and admit, and the done callback's release (which a cancel runs on the
    cancelling thread), each change the counters exactly once, whatever the order."""

    def _host(self, observer: Any = None) -> SimpleNamespace:  # noqa: ANN401
        return SimpleNamespace(admission_backlog=AdmissionBacklog(observer=observer), gate_waiters=GateWaiters())

    def test_a_cancel_between_the_count_and_the_flag_leaves_no_phantom_at_the_gate(self) -> None:
        host = self._host()
        token = GateWaiterToken(host, H)
        host.admission_backlog.observer = _Interleaver("entered", token.release)
        token.enter_gate()
        host.admission_backlog.observer.thread.join(5)
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 0
        assert host.gate_waiters.count() == 0

    def test_a_release_between_the_gate_exit_and_the_admit_does_not_count_the_waiter_out_twice(self) -> None:
        host = self._host()
        token = GateWaiterToken(host, H)
        token.enter_gate()
        host.admission_backlog.observer = _Interleaver("left", token.release)
        token.admit()
        host.admission_backlog.observer.thread.join(5)
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 0
        assert host.gate_waiters.count() == 0  # -1 if both the admit and the release took the waiter out

    def test_a_release_before_the_gate_entry_means_the_entry_counts_nothing(self) -> None:
        host = self._host()
        token = GateWaiterToken(host, H)
        token.release()
        token.enter_gate()
        token.admit()
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 0
        assert host.gate_waiters.count() == 0

    @pytest.mark.parametrize("seed", [3, 77, None])
    def test_many_tokens_ended_from_two_threads_always_end_at_zero(self, seed: int | None) -> None:
        rng = random.Random(random.randrange(10**6) if seed is None else seed)
        host = self._host()
        tokens = [GateWaiterToken(host, rng.choice((H, L))) for _ in range(1500)]
        plans = [rng.choice(("admit", "none")) for _ in tokens]

        def worker() -> None:
            for token, plan in zip(tokens, plans):
                token.enter_gate()
                if plan == "admit":
                    token.admit()

        def canceller() -> None:
            for token in reversed(tokens):
                token.release()

        threads = [threading.Thread(target=worker), threading.Thread(target=canceller)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert all(not thread.is_alive() for thread in threads)
        for token in tokens:
            token.release()
        totals = host.admission_backlog.totals()
        assert all(totals[t][PHASE_GATE] == 0 for t in (H, L)), totals
        assert host.gate_waiters.snapshot() == {H: 0, L: 0}

    def test_the_counter_operations_stay_within_the_hot_path_budget(self) -> None:
        # Counted, not timed: one round (a construction and three calls) takes 2 us, 11 us under the CI unit job's
        # branch coverage and about 30 us with its workers on every core, so a microsecond bound measured the
        # runner. The calls and bytecode instructions a round runs are the same everywhere, coverage or not.
        import sys
        from types import FrameType

        def work_per_round(live: int) -> tuple[int, int]:
            host = self._host()
            held = [GateWaiterToken(host, H) for _ in range(live)]
            for token in held:
                token.enter_gate()
            work = {"call": 0, "opcode": 0}

            def count(frame: FrameType, event: str, _arg: object) -> object:
                if event in work:
                    work[event] += 1
                if event == "call":
                    frame.f_trace_opcodes = True  # a loop that makes no call still runs its instructions
                return count

            previous = sys.gettrace()
            sys.settrace(count)
            try:
                token = GateWaiterToken(host, H)
                token.enter_gate()
                token.admit()
                token.release()
            finally:
                sys.settrace(previous)  # coverage's tracer, under --cov
            return work["call"], work["opcode"]

        # Python 3.12 starts reporting a code object's instructions from the round after it was first traced.
        work_per_round(0)
        python_calls, instructions = work_per_round(0)
        # No more Python work with 10,000 records at the gate: the hot path does not grow with the records waiting.
        assert work_per_round(10_000) == (python_calls, instructions)
        # 13 calls and 296 instructions today; the bounds leave room for a small change and catch the
        # order-of-magnitude regression the old timing bound was for (a log line, a lookup through other objects).
        assert python_calls <= 20 and instructions <= 600, (python_calls, instructions)


class TestBufferedIsNotCountedTwiceForTheLane:
    def test_an_admitted_lane_record_is_in_flight_and_not_buffered(self) -> None:
        lane = cc.PriorityLane(2, 2)
        host = SimpleNamespace(
            admission_backlog=AdmissionBacklog(), gate_waiters=GateWaiters(), priority_lane=lane, governor=None,
            stage_admission=None, concurrency_manager=None,
        )
        admitted = GateWaiterToken(host, H)
        admitted.enter_gate()
        admitted.admit()
        at_gate = GateWaiterToken(host, H)
        at_gate.enter_gate()
        before_gate = GateWaiterToken(host, H)  # spawned, in retry back-off or waiting for a lease
        explain = cc.admission_explain(host)
        assert explain["priority"]["heavy"]["inFlight"] == 3
        assert explain["priority"]["heavy"]["gateWaiting"] == 1
        assert explain["backlog"]["tiers"]["heavy"]["buffered"] == 1
        admitted.release()
        assert cc.admission_explain(host)["priority"]["heavy"]["inFlight"] == 2
        assert cc.admission_explain(host)["backlog"]["tiers"]["heavy"]["buffered"] == 1
        at_gate.release()
        before_gate.release()
        assert host.gate_waiters.snapshot() == {H: 0, L: 0}
        assert host.gate_waiters.retained_snapshot() == {H: 0, L: 0}

    def test_without_the_lane_an_admitted_record_is_in_neither(self) -> None:
        host = SimpleNamespace(
            admission_backlog=AdmissionBacklog(), gate_waiters=GateWaiters(), priority_lane=None, governor=None,
            stage_admission=None, concurrency_manager=None,
        )
        token = GateWaiterToken(host, H)
        token.enter_gate()
        token.admit()
        assert cc.admission_explain(host)["backlog"]["tiers"]["heavy"]["buffered"] == 0


@pytest.mark.asyncio
class TestOnlyARealWaitIsCounted:
    """An acquire that is granted at once never touches the backlog; one that has to wait is counted from the moment it suspends."""

    async def test_an_immediate_grant_is_not_counted_and_not_observed(self) -> None:
        recorder = _Recorder()
        host = SimpleNamespace(admission_backlog=AdmissionBacklog(observer=recorder))

        async def at_once() -> str:
            return "granted"

        assert await cc.in_phase(host, PHASE_PARSE, H, None, at_once(), pool="p") == "granted"
        assert recorder.events == [] and host.admission_backlog.totals()[H][PHASE_PARSE] == 0
        assert host.admission_backlog.lease_waiting() == {}

    async def test_a_wait_is_counted_from_its_first_suspension_and_not_after_it_ends(self) -> None:
        recorder = _Recorder()
        host = SimpleNamespace(admission_backlog=AdmissionBacklog(observer=recorder))
        gate = asyncio.Event()

        async def waits() -> int:
            await gate.wait()
            return 7

        task = asyncio.ensure_future(cc.in_phase(host, PHASE_LEASE, H, "m", waits(), pool="indexing"))
        await asyncio.sleep(0.01)
        assert host.admission_backlog.totals()[H][PHASE_LEASE] == 1 and host.admission_backlog.lease_waiting() == {"indexing": 1}
        gate.set()
        assert await task == 7
        assert host.admission_backlog.totals()[H][PHASE_LEASE] == 0 and host.admission_backlog.lease_waiting() == {}
        assert [e[0] for e in recorder.events] == ["in", "out"] and recorder.events[0][3] == "m"

    async def test_an_exception_raised_while_waiting_and_one_raised_at_once_both_pass_through_without_a_leak(self) -> None:
        host = SimpleNamespace(admission_backlog=AdmissionBacklog())
        gate = asyncio.Event()

        async def fails_late() -> None:
            await gate.wait()
            raise ValueError("late")

        async def fails_now() -> None:
            raise KeyError("now")

        task = asyncio.ensure_future(cc.in_phase(host, PHASE_PARSE, H, None, fails_late()))
        await asyncio.sleep(0.01)
        gate.set()
        with pytest.raises(ValueError, match="late"):
            await task
        with pytest.raises(KeyError):
            await cc.in_phase(host, PHASE_PARSE, H, None, fails_now())
        assert host.admission_backlog.totals()[H][PHASE_PARSE] == 0

    async def test_a_cancelled_wait_leaves_and_the_cancellation_reaches_the_awaitable(self) -> None:
        host = SimpleNamespace(admission_backlog=AdmissionBacklog())
        seen: list[str] = []

        async def waits() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                seen.append("cancelled")
                raise

        task = asyncio.ensure_future(cc.in_phase(host, PHASE_PARSE, H, None, waits()))
        await asyncio.sleep(0.01)
        assert host.admission_backlog.totals()[H][PHASE_PARSE] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert seen == ["cancelled"] and host.admission_backlog.totals()[H][PHASE_PARSE] == 0

    async def test_a_wait_that_swallows_the_cancellation_and_finishes_still_leaves(self) -> None:
        host = SimpleNamespace(admission_backlog=AdmissionBacklog())

        async def stubborn() -> str:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "finished anyway"
            return "unreachable"

        task = asyncio.ensure_future(cc.in_phase(host, PHASE_PARSE, H, None, stubborn()))
        await asyncio.sleep(0.01)
        task.cancel()
        assert await task == "finished anyway"
        assert host.admission_backlog.totals()[H][PHASE_PARSE] == 0

    async def test_the_gate_token_enters_the_gate_only_when_the_permit_is_not_free(self) -> None:
        host = SimpleNamespace(admission_backlog=AdmissionBacklog(), gate_waiters=GateWaiters())
        free = GateWaiterToken(host, H)

        async def now() -> int:
            return 1

        assert await cc.at_gate(free, now()) == 1
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 0
        free.admit()

        busy = GateWaiterToken(host, H)
        gate = asyncio.Event()

        async def later() -> int:
            await gate.wait()
            return 2

        task = asyncio.ensure_future(cc.at_gate(busy, later()))
        await asyncio.sleep(0.01)
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 1
        gate.set()
        assert await task == 2
        busy.admit()
        assert host.admission_backlog.totals()[H][PHASE_GATE] == 0 and host.gate_waiters.count(H) == 0
        assert await cc.at_gate(None, now()) == 1

    async def test_a_host_without_a_backlog_gets_the_awaitable_itself(self) -> None:
        async def now() -> int:
            return 3

        coro = now()
        assert cc.in_phase(SimpleNamespace(), PHASE_GATE, H, None, coro) is coro
        assert await coro == 3
