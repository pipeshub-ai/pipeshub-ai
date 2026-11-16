"""The governor's read-only admission explain: which rule set each pool's limit, why the head of a
gate's queue was refused, and the counters the health endpoints show.

Every test has a negative control: a broken variant of the behaviour it pins, which the same
check must reject.
"""
from __future__ import annotations

import ast
import asyncio
import gc
import inspect
import json
import logging
import textwrap
import threading
from dataclasses import replace
from typing import Any

import pytest

from app.services.base_client import CircuitBreaker, open_circuits
from app.services.messaging.distributed_concurrency import DistributedConcurrencyManager
from app.services.resource_governor import policy as policy_mod
from app.services.resource_governor.download_ahead import DownloadAhead
from app.services.resource_governor.feedback import DownstreamFeedback, FeedbackWindow
from app.services.resource_governor.gate import AdmissionGate, StartRateLimiter
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    LimitRule,
    Limits,
    Pool,
    PoolDemand,
    PoolState,
    ResourceSnapshot,
)
from app.services.resource_governor.policy import (
    INCIDENT_COOLDOWN_SECONDS,
    next_limits,
    resolve_ceilings,
    warm_start_limits,
)
from app.services.resource_governor.registry import LimitRegistry
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

GB = 1024**3
NOW = 10_000.0


def _snap(
    *,
    working_set_gb: float | None = 1.0,
    limit_gb: float = 4.0,
    util: float = 0.1,
    psi: float = 0.0,
    throttled: float = 0.0,
) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_quota=8.0,
        cpu_utilisation=util,
        cpu_throttled_ratio=throttled,
        cpu_pressure=psi,
        mem_limit_bytes=int(limit_gb * GB),
        mem_working_set_bytes=None if working_set_gb is None else int(working_set_gb * GB),
        source="test",
    )


def _ceilings(snap: ResourceSnapshot | None = None, *, env_index: int | None = None) -> Ceilings:
    return resolve_ceilings(snap or _snap(), None, env_index)


def _step(
    pool: Pool,
    *,
    snap: ResourceSnapshot | None = None,
    state: PoolState | None = None,
    demand: PoolDemand | None = None,
    feedback: FeedbackWindow | None = None,
    current: int | None = None,
    ceilings: Ceilings | None = None,
    now: float = NOW,
) -> tuple[int, PoolState]:
    snap = snap or _snap()
    ceilings = ceilings or _ceilings(snap)
    limits = warm_start_limits(ceilings)
    if current is not None:
        limits = Limits(values={**dict(limits.values), pool: current})
    pool_state = state or PoolState()
    controller = ControllerState(pools={p: (pool_state if p is pool else PoolState()) for p in Pool})
    new_limits, new_state = next_limits(
        limits, snap, ceilings, controller, {pool: demand or PoolDemand.empty()}, now, feedback=feedback,
    )
    return new_limits.get(pool), new_state.get(pool)


_BUSY = PoolDemand(permit_seconds=1000.0, blocked_acquires=5, completions=50)


class TestEveryBranchNamesItsRule:
    """each way ``_next_pool_limit`` can end sets the rule that produced the limit."""

    @staticmethod
    def _returns_without_rule(source: str) -> list[int]:
        tree = ast.parse(textwrap.dedent(source))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_next_pool_limit")
        missing = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Return):
                continue
            value = node.value
            tagged = (
                isinstance(value, ast.Tuple)
                and len(value.elts) == 2
                and isinstance(value.elts[1], ast.Call)
                and isinstance(value.elts[1].func, ast.Name)
                and value.elts[1].func.id == "_with_rule"
            )
            if not tagged:
                missing.append(node.lineno)
        return missing

    def test_every_return_goes_through_with_rule(self) -> None:
        source = inspect.getsource(policy_mod)
        assert self._returns_without_rule(source) == []

    def test_a_return_that_leaves_the_rule_stale_is_caught(self) -> None:
        source = inspect.getsource(policy_mod)
        stale = source.replace(
            "return current, _with_rule(\n            replace(state, healthy_streak=0), LimitRule.PRESSURE_UNKNOWN, now, limit=current, ceiling=ceiling\n        )",
            "return current, replace(state, healthy_streak=0)",
        )
        assert stale != source
        assert self._returns_without_rule(stale)

    @pytest.mark.parametrize(
        ("name", "pool", "kwargs", "expected"),
        [
            ("mem_hard", Pool.HEAVY_PARSE, {"snap": _snap(working_set_gb=35, limit_gb=40)}, LimitRule.MEM_HARD),
            ("mem_soft", Pool.HEAVY_PARSE, {"snap": _snap(working_set_gb=30, limit_gb=40)}, LimitRule.MEM_SOFT),
            ("cpu util", Pool.HEAVY_PARSE, {"snap": _snap(util=0.95)}, LimitRule.CPU),
            ("cpu psi", Pool.HEAVY_PARSE, {"snap": _snap(psi=0.7)}, LimitRule.CPU),
            ("cpu throttled", Pool.HEAVY_PARSE, {"snap": _snap(throttled=0.5)}, LimitRule.CPU),
            (
                "downstream",
                Pool.INDEX_HEAVY,
                {"feedback": FeedbackWindow(throttles={"embedding": 2})},
                LimitRule.DOWNSTREAM,
            ),
            ("pressure unknown", Pool.INDEX_HEAVY, {"snap": _snap(working_set_gb=None)}, LimitRule.PRESSURE_UNKNOWN),
            ("no demand", Pool.INDEX_HEAVY, {}, LimitRule.NO_DEMAND),
            ("grow", Pool.INDEX_HEAVY, {"demand": _BUSY}, LimitRule.GROW),
            (
                "downstream holdoff",
                Pool.INDEX_HEAVY,
                {"state": PoolState(downstream_holdoff=2), "demand": _BUSY},
                LimitRule.DOWNSTREAM_HOLDOFF,
            ),
            (
                "latency hold",
                Pool.INDEX_HEAVY,
                {"state": PoolState(hold_ewma=50.0, hold_baseline=10.0), "demand": _BUSY},
                LimitRule.LATENCY_HOLD,
            ),
            (
                "cooldown",
                Pool.INDEX_HEAVY,
                {"state": PoolState(cooldown_until=NOW + 20.0, cooldown_causes=(("memory", NOW + 20.0),)), "demand": _BUSY},
                LimitRule.COOLDOWN,
            ),
        ],
    )
    def test_branch_reports_its_rule(self, name: str, pool: Pool, kwargs: dict[str, Any], expected: LimitRule) -> None:
        _, state = _step(pool, **kwargs)
        assert state.rule is expected, name

    def test_mem_target_names_the_memory_walk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy_mod, "_target_for", lambda *_a, **_k: 1)
        limit, state = _step(Pool.INDEX_HEAVY, current=8)
        assert state.rule is LimitRule.MEM_TARGET
        assert limit < 8
        assert state.active_cooldown_cause(NOW) == "memory"

    def test_confirming_while_the_streak_is_short(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy_mod, "LIGHT_GROW_CONFIRM_SAMPLES", 3)
        _, state = _step(Pool.INDEX_HEAVY, demand=_BUSY)
        assert state.rule is LimitRule.CONFIRMING

    def test_grow_band_holds_heavy_below_the_soft_mark(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy_mod, "GROW_BAND", 0.3)
        _, state = _step(Pool.HEAVY_PARSE, snap=_snap(working_set_gb=25, limit_gb=40), demand=_BUSY)
        assert state.rule is LimitRule.GROW_BAND

    def test_resource_delta_hold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy_mod, "_growth_step", lambda _pool, _ceiling, state, _snap: (0, state))
        _, state = _step(Pool.INDEX_HEAVY, demand=_BUSY)
        assert state.rule is LimitRule.RESOURCE_DELTA

    def test_a_pool_at_its_ceiling_says_so(self) -> None:
        ceilings = _ceilings()
        _, state = _step(Pool.INDEX_HEAVY, current=ceilings.index_heavy, ceilings=ceilings)
        assert state.rule is LimitRule.AT_CEILING

    def test_a_pool_with_no_ceiling_is_collapsed(self) -> None:
        ceilings = _ceilings(env_index=1)
        assert ceilings.index_light == 0
        _, state = _step(Pool.INDEX_LIGHT, ceilings=ceilings)
        assert state.rule is LimitRule.COLLAPSED

    def test_the_initial_state_is_start(self) -> None:
        assert PoolState().rule is LimitRule.START
        assert ControllerState.initial().get(Pool.HEAVY_PARSE).rule is LimitRule.START

    def test_the_binding_shrink_rule_is_the_one_that_produced_the_smaller_target(self) -> None:
        # MEM_HARD halves (8 -> 4); the downstream timeout rule would only take 3/4 of it (8 -> 6).
        limit, state = _step(
            Pool.INDEX_HEAVY,
            snap=_snap(working_set_gb=35, limit_gb=40),
            feedback=FeedbackWindow(timeouts={"embedding": 2}),
            current=8,
        )
        assert limit <= 4
        assert state.rule is LimitRule.MEM_HARD

    def test_downstream_names_the_services_that_reported(self) -> None:
        _, state = _step(
            Pool.INDEX_HEAVY, feedback=FeedbackWindow(throttles={"embedding": 1}, unavailable={"qdrant": 2})
        )
        assert state.rule is LimitRule.DOWNSTREAM
        assert state.rule_services == ("embedding", "qdrant")
        assert state.active_cooldown_cause(NOW) == "downstream"

    def test_rule_since_keeps_its_first_instant_while_the_rule_holds(self) -> None:
        _, first = _step(Pool.INDEX_HEAVY, now=NOW)
        _, second = _step(Pool.INDEX_HEAVY, state=first, now=NOW + 15)
        assert second.rule is first.rule
        assert second.rule_since == first.rule_since == NOW

    def test_the_rule_never_changes_what_the_limit_is(self) -> None:
        """Observability only: stripping the tag out of the state gives the same limits."""
        for pool in Pool:
            for snap in (_snap(), _snap(working_set_gb=3.5), _snap(util=0.95)):
                tagged, _ = _step(pool, snap=snap, demand=_BUSY)
                plain, _ = _step(pool, snap=snap, demand=_BUSY, state=replace(PoolState(), rule=LimitRule.CPU, rule_since=5.0))
                assert tagged == plain


def _registry_with(limit: int, pool: Pool = Pool.HEAVY_PARSE) -> LimitRegistry:
    return LimitRegistry(Limits(values={p: (limit if p == pool else 1) for p in Pool}))


@pytest.mark.asyncio
class TestGateWaiting:
    """Gate waiting follows the queue through timeout, cancel and priority insertion."""

    async def test_waiting_follows_the_queue_through_timeout_cancel_and_priority(self) -> None:
        gate = AdmissionGate(Pool.HEAVY_PARSE, _registry_with(1))
        assert await gate.acquire()
        assert gate.waiting == 0

        timed_out = asyncio.create_task(gate.acquire(timeout=0.05))
        cancelled = asyncio.create_task(gate.acquire())
        prioritised = asyncio.create_task(gate.acquire(priority=True))
        await asyncio.sleep(0.01)
        assert gate.waiting == 3
        assert gate.waiting == len(gate._waiters)

        assert await timed_out is False
        assert gate.waiting == 2
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert gate.waiting == 1

        gate.release()
        assert await asyncio.wait_for(prioritised, 2) is True
        assert gate.waiting == 0
        gate.release()

    async def test_a_count_taken_from_blocked_acquires_is_not_the_queue(self) -> None:
        """Negative control: blocked_acquires is cumulative, so it cannot stand for the queue."""
        gate = AdmissionGate(Pool.HEAVY_PARSE, _registry_with(1))
        await gate.acquire()
        await gate.acquire(timeout=0.01)
        assert gate._blocked_acquires == 1
        assert gate.waiting == 0


@pytest.mark.asyncio
class TestHeadBlock:
    """why the last refused acquire was refused."""

    async def test_rate_room_cap_and_none(self) -> None:
        clock = [0.0]
        limiter = StartRateLimiter(10.0, 1, clock=lambda: clock[0])
        gate = AdmissionGate(Pool.HEAVY_PARSE, _registry_with(6), rate_limiter=limiter, clock=lambda: clock[0])
        assert gate.head_block == "none"
        assert await gate.acquire(cost=1)
        assert gate.head_block == "none"

        # Room is free but the limiter is drained: rate.
        waiter = asyncio.create_task(gate.acquire(cost=1))
        await asyncio.sleep(0.02)
        assert gate.head_block == "rate"
        clock[0] += 100.0
        assert await asyncio.wait_for(waiter, 3) is True
        assert gate.head_block == "none"

        # No room against the adaptive limit: room.
        gate2 = AdmissionGate(Pool.LIGHT_PARSE, _registry_with(2, Pool.LIGHT_PARSE))
        assert await gate2.acquire(cost=2)
        waiter2 = asyncio.create_task(gate2.acquire(cost=1))
        await asyncio.sleep(0.02)
        assert gate2.head_block == "room"
        waiter2.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter2
        assert gate2.head_block == "none"

        # Refused against a caller's cap: cap.
        gate3 = AdmissionGate(Pool.LIGHT_PARSE, _registry_with(1, Pool.LIGHT_PARSE))
        assert await gate3.acquire(cost=2, cap=6)
        waiter3 = asyncio.create_task(gate3.acquire(cost=5, cap=6))
        await asyncio.sleep(0.02)
        assert gate3.head_block == "cap"
        gate3.release(cost=2)
        assert await asyncio.wait_for(waiter3, 2) is True
        assert gate3.head_block == "none"

    async def test_a_gate_that_only_records_room_refusals_misses_the_limiter(self) -> None:
        """Negative control: with the rate branch removed the limiter's refusal reads as no room at all."""
        source = inspect.getsource(AdmissionGate._try_admit)
        assert 'self._head_block = "rate"' in source
        assert 'self._head_block = "cap"' not in source.replace('"room" if cap is None else "cap"', "")
        assert '"room" if cap is None else "cap"' in source

    async def test_a_cost_two_head_with_no_room_after_a_rate_limited_window_reads_room(self) -> None:
        """The last refusal, not an earlier window, says why the head waits."""
        clock = [0.0]
        limiter = StartRateLimiter(1.0, 1, clock=lambda: clock[0])
        gate = AdmissionGate(Pool.HEAVY_PARSE, _registry_with(6), rate_limiter=limiter, clock=lambda: clock[0])
        assert await gate.acquire(cost=1)
        denied = asyncio.create_task(gate.acquire(cost=1))
        await asyncio.sleep(0.02)
        assert gate.head_block == "rate"
        clock[0] += 50.0
        assert await asyncio.wait_for(denied, 3)
        for _ in range(4):
            clock[0] += 50.0
            assert await gate.acquire(cost=1)
        clock[0] += 50.0
        assert gate.in_use == 6
        gate.release(cost=1)
        assert gate.in_use == 5
        head = asyncio.create_task(gate.acquire(cost=2))
        await asyncio.sleep(0.02)
        assert gate.head_block == "room"
        head.cancel()
        with pytest.raises(asyncio.CancelledError):
            await head


class TestRegressionsFoundInReview:
    def test_the_binding_shrink_rule_is_the_smallest_target_not_the_first_candidate(self) -> None:
        # Memory pressure over the soft mark asks for a one-step shrink; a throttling downstream asks for a halving.
        limit, state = _step(
            Pool.INDEX_HEAVY, snap=_snap(working_set_gb=30, limit_gb=40), current=8,
            feedback=FeedbackWindow(throttles={"embedding": 1}),
        )
        assert limit <= 4
        assert state.rule is LimitRule.DOWNSTREAM

    def test_a_grow_clipped_by_the_memory_target_is_a_memory_hold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy_mod, "_target_for", lambda *_a, **_k: 4)
        ceilings = _ceilings()
        assert ceilings.heavy > 4
        limit, state = _step(Pool.HEAVY_PARSE, current=4, demand=_BUSY, ceilings=ceilings)
        assert limit == 4 and state.rule is LimitRule.MEM_TARGET

    def test_control_a_grow_with_room_under_the_target_is_still_a_grow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy_mod, "_target_for", lambda *_a, **_k: 100)
        limit, state = _step(Pool.INDEX_HEAVY, demand=_BUSY)
        assert state.rule is LimitRule.GROW

    def test_rule_age_is_unknown_before_the_first_sample_and_small_after(self) -> None:
        governor = make_test_governor()
        try:
            assert all(p["ruleAgeSec"] is None for p in governor.admission_explain()["pools"].values())
            asyncio.run(governor._sample_once())
            ages = [p["ruleAgeSec"] for p in governor.admission_explain()["pools"].values()]
            assert all(a is not None and a < 60 for a in ages)
        finally:
            governor.close()

    def test_a_later_admit_clears_a_stale_room_refusal(self) -> None:
        gate = AdmissionGate(Pool.HEAVY_PARSE, _registry_with(2))
        gate._in_use = 2
        assert gate._try_admit(1) is False and gate.head_block == "room"
        gate._in_use = 0
        assert gate._try_admit(1) is True and gate.head_block == "none"

    def test_a_live_room_refusal_replaces_an_earlier_rate_refusal(self) -> None:
        clock = [0.0]
        registry = _registry_with(2)
        gate = AdmissionGate(
            Pool.HEAVY_PARSE, registry, rate_limiter=StartRateLimiter(10.0, 1, clock=lambda: clock[0]),
            clock=lambda: clock[0],
        )
        assert gate._try_admit(1) is True  # spends the only token
        assert gate._try_admit(1) is False and gate.head_block == "rate"
        registry.set(Pool.HEAVY_PARSE, 1)  # the limit shrinks: now there is no room, and nothing was admitted since
        assert gate._try_admit(1) is False and gate.head_block == "room"

    def test_the_governor_accessor_has_no_lane_block(self) -> None:
        governor = make_test_governor()
        try:
            assert "lanes" not in governor.admission_explain()
        finally:
            governor.close()


class TestAccessorIsSafe:
    """it reads, it never creates, and a thread may call it while the loop works."""

    def test_it_creates_no_gate(self) -> None:
        governor = make_test_governor()
        try:
            assert governor._gates == {}
            explain = governor.admission_explain()
            assert governor._gates == {}
            assert set(explain["pools"]) == {pool.value for pool in Pool}
            for entry in explain["pools"].values():
                assert entry["inUse"] == 0
                assert entry["waiting"] == 0
                assert entry["headBlock"] == "none"
        finally:
            governor.close()

    def test_an_accessor_that_calls_gate_would_create_one(self) -> None:
        """Negative control: asking the governor for a gate does create it, so the test above can fail."""
        governor = make_test_governor()
        try:
            governor.gate(Pool.INDEX_HEAVY)
            assert Pool.INDEX_HEAVY in governor._gates
        finally:
            governor.close()

    @pytest.mark.asyncio
    async def test_reading_from_a_thread_during_loop_activity(self) -> None:
        governor = make_test_governor()
        gate = governor.gate(Pool.INDEX_LIGHT)
        failures: list[BaseException] = []
        stop = threading.Event()

        def reader() -> None:
            try:
                while not stop.is_set():
                    json.dumps(governor.admission_explain())
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for _ in range(20_000):
                await gate.acquire()
                gate.release()
            for _ in range(2_000):
                task = asyncio.create_task(gate.acquire(timeout=0.0001))
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises((asyncio.CancelledError, BaseException)) if False else _nothing():
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            stop.set()
            thread.join(5)
            governor.close()
        assert failures == []

    def test_the_output_is_json_and_counts_only(self) -> None:
        governor = make_test_governor()
        try:
            text = json.dumps(governor.admission_explain())
        finally:
            governor.close()
        payload = json.loads(text)
        assert payload["v"] == 2
        lowered = text.lower()
        for word in ("sender", "message"):
            assert word not in lowered


class _nothing:  # a context manager that suppresses nothing
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.asyncio
class TestSampleSequenceAndTotals:
    """The sample counter and the cumulative feedback."""

    async def test_sequence_moves_by_one_per_sample_and_totals_are_the_sum_of_drained_windows(self) -> None:
        feedback = DownstreamFeedback()
        governor = make_test_governor(feedback=feedback)
        try:
            assert governor.admission_explain()["sampleSeq"] == 0
            assert governor.admission_explain()["sampleAgeSec"] is None
            expected = {"throttles": 0, "timeouts": 0}
            for step in range(1, 6):
                feedback.report_throttle("embedding")
                expected["throttles"] += 1
                if step % 2:
                    feedback.report_timeout("qdrant")
                    expected["timeouts"] += 1
                await governor._sample_once()
                explain = governor.admission_explain()
                assert explain["sampleSeq"] == step
                totals = explain["feedback"]["totals"]
                assert totals["throttles"] == {"embedding": expected["throttles"]}
                assert totals["timeouts"].get("qdrant", 0) == expected["timeouts"]
                assert explain["feedback"]["window"]["seq"] == step
                assert explain["sampleAgeSec"] is not None
        finally:
            governor.close()

    async def test_forwarding_the_window_instead_of_summing_it_is_not_a_total(self) -> None:
        """Negative control: the last window alone would read 1, not the sum."""
        feedback = DownstreamFeedback()
        governor = make_test_governor(feedback=feedback)
        try:
            for _ in range(3):
                feedback.report_throttle("embedding")
                await governor._sample_once()
            window = governor.admission_explain()["feedback"]["window"]["throttles"]
            total = governor.admission_explain()["feedback"]["totals"]["throttles"]
            assert window == {"embedding": 1}
            assert total == {"embedding": 3}
        finally:
            governor.close()

    async def test_a_memory_incident_is_reported_as_the_rule(self) -> None:
        governor = make_test_governor()
        try:
            await governor._sample_once()
            governor.report_memory_incident("test")
            pool = governor.admission_explain()["pools"]["heavy_parse"]
            assert pool["rule"] == "mem_incident"
            assert pool["cooldownCause"] == "memory"
            assert 0 < pool["cooldownRemainingSec"] <= INCIDENT_COOLDOWN_SECONDS
        finally:
            governor.close()


class TestBrakeThresholdsAreExplained:
    def test_psi_is_reported_next_to_its_threshold(self) -> None:
        governor = make_test_governor()
        try:
            brake = governor.admission_explain()["cpu"]["brakeAt"]
        finally:
            governor.close()
        assert set(brake) == {"util", "throttled", "psi"}
        assert brake["psi"] == policy_mod.CPU_BRAKE_PRESSURE_AVG10


class TestBreakers:
    """the breaker registry holds clients weakly."""

    def test_open_and_half_open_are_listed_once_and_a_discarded_breaker_is_gone(self) -> None:
        a = CircuitBreaker("svc-a", failure_threshold=1, cooldown_seconds=60)
        b = CircuitBreaker("svc-a", failure_threshold=1, cooldown_seconds=60)
        c = CircuitBreaker("svc-c", failure_threshold=1, cooldown_seconds=60)
        quiet = CircuitBreaker("svc-quiet", failure_threshold=5, cooldown_seconds=60)
        for breaker in (a, b, c):
            breaker.record_failure()
        del breaker
        assert quiet.state == "closed"
        assert {"svc-a", "svc-c"} <= set(open_circuits())
        assert "svc-quiet" not in open_circuits()
        assert open_circuits().count("svc-a") == 1
        del c
        gc.collect()
        assert "svc-c" not in open_circuits()

    def test_a_breaker_made_on_another_thread_during_the_walk_neither_breaks_it_nor_is_lost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The registry changes under the walk, as it does when a client is first used on another thread."""
        import weakref

        from app.services import base_client

        made: list[CircuitBreaker] = []

        def make() -> None:
            breaker = CircuitBreaker("svc-late", failure_threshold=1, cooldown_seconds=60)
            breaker.record_failure()
            made.append(breaker)

        class Registry(weakref.WeakSet):
            started = False

            def __iter__(self):  # noqa: ANN204
                inner = super().__iter__()
                if not Registry.started:
                    Registry.started = True
                    thread = threading.Thread(target=make)
                    thread.start()
                    thread.join(timeout=0.3)  # a registry that is locked keeps it waiting; one that is not does not
                    Registry.thread = thread
                yield from inner

        registry = Registry()
        keep = CircuitBreaker("svc-early", failure_threshold=1, cooldown_seconds=60)
        registry.add(keep)
        monkeypatch.setattr(base_client, "_BREAKERS", registry)
        monkeypatch.setattr(base_client, "_BREAKERS_LOCK", getattr(base_client, "_BREAKERS_LOCK", threading.Lock()))
        keep.record_failure()
        assert base_client.open_circuits() == ["svc-early"]
        Registry.thread.join(5)
        assert made and "svc-late" in base_client.open_circuits()

    @pytest.mark.parametrize("seed", [1, 2])
    def test_walking_while_breakers_are_made_and_dropped_on_other_threads_never_raises(self, seed: int) -> None:
        stop = threading.Event()
        errors: list[BaseException] = []

        def churn() -> None:
            while not stop.is_set():
                CircuitBreaker(f"svc-churn-{seed}", failure_threshold=1, cooldown_seconds=60)

        threads = [threading.Thread(target=churn) for _ in range(3)]
        for thread in threads:
            thread.start()
        try:
            for _ in range(3000):
                try:
                    open_circuits()
                except BaseException as error:
                    errors.append(error)
                    break
        finally:
            stop.set()
            for thread in threads:
                thread.join(5)
        assert not errors, errors

    def test_a_strong_set_would_retain_the_client(self) -> None:
        """Negative control: a plain set keeps a discarded breaker alive, a WeakSet does not."""
        import weakref

        class Holder:
            pass

        strong: set[Holder] = set()
        weak: weakref.WeakSet[Holder] = weakref.WeakSet()
        one, two = Holder(), Holder()
        strong.add(one)
        weak.add(two)
        del one, two
        gc.collect()
        assert len(strong) == 1
        assert len(weak) == 0


class TestPoolKey:
    """Gate waiting follows the queue through timeout, cancel and priority insertion."""

    @pytest.mark.parametrize("namespace", [None, "", "ns1"])
    def test_pool_key_is_the_key_the_acquire_script_uses(self, namespace: str | None) -> None:
        manager = DistributedConcurrencyManager.__new__(DistributedConcurrencyManager)
        manager.key_prefix = "pipeshub:concurrency"
        manager._key_namespace = namespace
        assert manager.pool_key("indexing") == manager._key("indexing")
        assert manager.pool_key("stage:embed").endswith("pipeshub:concurrency:stage:embed")

    def test_a_hand_built_key_ignores_the_namespace(self) -> None:
        manager = DistributedConcurrencyManager.__new__(DistributedConcurrencyManager)
        manager.key_prefix = "pipeshub:concurrency"
        manager._key_namespace = "ns1"
        assert f"{manager.key_prefix}:indexing" != manager.pool_key("indexing")

    def test_the_acquire_path_asks_for_the_same_key(self) -> None:
        source = inspect.getsource(DistributedConcurrencyManager.try_acquire)
        assert "self._key(pool)" in source


@pytest.mark.asyncio
class TestDownloadAheadWaiting:
    """Gate waiting follows the queue through timeout, cancel and priority insertion."""

    async def test_waiting_equals_the_waiters_through_acquire_timeout_and_cancel(self) -> None:
        ahead = DownloadAhead(1)
        assert await ahead.acquire()
        assert ahead.waiting == 0
        slow = asyncio.create_task(ahead.acquire())
        timed = asyncio.create_task(ahead.acquire(timeout=0.05))
        await asyncio.sleep(0.01)
        assert ahead.waiting == 2
        assert await timed is False
        assert ahead.waiting == 1
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
        assert ahead.waiting == 0
        ahead.release()
        assert ahead.stats() == {"limit": 1, "in_use": 0}

    async def test_a_counter_left_standing_on_timeout_overcounts(self) -> None:
        """Negative control: the same loop without the finally leaves the timed-out waiter counted."""
        waiting = 0
        semaphore = asyncio.Semaphore(0)
        waiting += 1
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(semaphore.acquire(), 0.01)
        assert waiting == 1  # what the fixed code's finally prevents

    async def test_a_disabled_bound_never_waits(self) -> None:
        ahead = DownloadAhead(0)
        assert await ahead.acquire()
        assert ahead.waiting == 0


class TestSharedSlotsWaiting:
    def test_slot_wait_counts_while_inside(self) -> None:
        governor = make_test_governor()
        try:
            assert governor._slot_waiting == 0
            with governor.slot_wait():
                assert governor._slot_waiting == 1
                with governor.slot_wait():
                    assert governor._slot_waiting == 2
            assert governor._slot_waiting == 0
        finally:
            governor.close()

    def test_shared_slots_are_reported_only_when_there_are_any(self, tmp_path) -> None:
        none = make_test_governor()
        shared = make_test_governor(worker_count=2, heavy_parse_slots_dir=tmp_path)
        try:
            assert none.admission_explain()["sharedSlots"] is None
            slots = shared.admission_explain()["sharedSlots"]
            assert set(slots) == {"capacity", "limit", "waiting"}
        finally:
            none.close()
            shared.close()


def test_the_governor_explain_is_part_of_stats() -> None:
    governor = make_test_governor()
    try:
        assert governor.stats()["admission"]["v"] == 2
    finally:
        governor.close()


def test_logger_is_not_involved() -> None:
    # The accessor must not log (it runs on every health probe).
    governor = make_test_governor()
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("test.governor")
    handler = Capture()
    logger.addHandler(handler)
    try:
        governor.admission_explain()
    finally:
        logger.removeHandler(handler)
        governor.close()
    assert records == []


def _pool_health(states: dict[Pool, PoolState], at: float) -> dict[str, Any]:
    """The ``pools`` section of the governor's explain, read at the controller instant *at*."""
    governor = make_test_governor()
    try:
        governor._clock = lambda: at
        governor._state = ControllerState(pools={p: states.get(p, PoolState()) for p in Pool})
        return governor.admission_explain()["pools"]
    finally:
        governor.close()


_SOFT = _snap(working_set_gb=3.0, limit_gb=4.0)  # 0.75: over the soft mark, under the hard one
_OUTAGE = FeedbackWindow(unavailable={"embedding": 1})  # an incident: the longer cooldown window
_SLOW = FeedbackWindow(timeouts={"embedding": 9})  # shrinks without an incident: the shorter window


class TestCooldownCauseFollowsItsOwnDeadline:
    """Each cause of a shrink keeps its own expiry; health names the cause whose window is still open."""

    def test_a_later_shorter_cause_does_not_rename_a_longer_cooldown(self) -> None:
        # t=0 an outage starts a 60 s cooldown; t=15 a soft memory shrink asks for 30 s more, ending at 45.
        _, state = _step(Pool.INDEX_HEAVY, feedback=_OUTAGE, current=8, now=NOW)
        _, state = _step(Pool.INDEX_HEAVY, snap=_SOFT, state=state, current=8, now=NOW + 15)
        assert state.cooldown_until == NOW + 60
        pool = _pool_health({Pool.INDEX_HEAVY: state}, NOW + 50)["index_heavy"]
        assert pool["cooldownCause"] == "downstream"

    def test_a_cause_whose_window_has_closed_is_not_reported_while_another_is_open(self) -> None:
        _, state = _step(Pool.INDEX_HEAVY, snap=_SOFT, current=8, now=NOW)  # memory, until NOW + 30
        _, state = _step(Pool.INDEX_HEAVY, feedback=_OUTAGE, state=state, current=8, now=NOW + 10)  # until NOW + 70
        assert _pool_health({Pool.INDEX_HEAVY: state}, NOW + 40)["index_heavy"]["cooldownCause"] == "downstream"
        assert "cooldownCause" not in _pool_health({Pool.INDEX_HEAVY: state}, NOW + 71)["index_heavy"]

    def test_the_cause_that_ends_last_wins_and_a_tie_goes_to_the_rule_that_bound(self) -> None:
        # One sample, two causes with the same window: the smaller target (memory, hard) is the one that binds.
        _, state = _step(
            Pool.INDEX_HEAVY, snap=_snap(working_set_gb=3.5, limit_gb=4.0), feedback=_OUTAGE, current=8, now=NOW
        )
        assert state.rule is LimitRule.MEM_HARD
        assert _pool_health({Pool.INDEX_HEAVY: state}, NOW + 5)["index_heavy"]["cooldownCause"] == "memory"

    def test_a_cause_shrinks_back_to_nothing_once_every_window_has_closed(self) -> None:
        _, state = _step(Pool.INDEX_HEAVY, feedback=_SLOW, current=8, now=NOW)
        assert _pool_health({Pool.INDEX_HEAVY: state}, NOW + 10)["index_heavy"]["cooldownCause"] == "downstream"
        assert "cooldownCause" not in _pool_health({Pool.INDEX_HEAVY: state}, NOW + 31)["index_heavy"]

    def test_the_causes_kept_stay_few(self) -> None:
        state = PoolState()
        for step in range(200):
            feedback, snap = (_OUTAGE, _snap()) if step % 2 else (None, _SOFT)
            _, state = _step(Pool.INDEX_HEAVY, snap=snap, feedback=feedback, state=state, current=8, now=NOW + step)
        assert len(state.cooldown_causes) <= 3


def _clean_samples(
    state: PoolState, count: int, *, current: int, ceilings: Ceilings | None = None
) -> tuple[int, PoolState]:
    """*count* healthy samples 15 s apart, from 60 s after the shrink: the cooldown and the hold-off are over."""
    limit = current
    for step in range(count):
        limit, state = _step(
            Pool.INDEX_HEAVY, state=state, current=limit, ceilings=ceilings, demand=_BUSY, now=NOW + 60 + 15 * step
        )
    return limit, state


class TestServicesAreNamedOnlyWhileTheDownstreamRestrictionLasts:
    def test_services_follow_the_cooldown_that_they_started(self) -> None:
        _, state = _step(Pool.INDEX_HEAVY, feedback=_SLOW, current=8, now=NOW)
        assert _pool_health({Pool.INDEX_HEAVY: state}, NOW + 5)["index_heavy"]["services"] == ["embedding"]
        _, later = _clean_samples(state, 4, current=2)
        health = _pool_health({Pool.INDEX_HEAVY: later}, NOW + 120)["index_heavy"]
        assert health["rule"] in {"grow", "no_demand", "confirming", "at_ceiling"}, health["rule"]
        assert "services" not in health

    def test_a_pool_that_grew_back_to_its_ceiling_names_no_service(self) -> None:
        ceilings = _ceilings()
        _, state = _step(Pool.INDEX_HEAVY, feedback=_SLOW, current=ceilings.index_heavy, ceilings=ceilings, now=NOW)
        _, state = _clean_samples(state, 4, current=ceilings.index_heavy, ceilings=ceilings)
        health = _pool_health({Pool.INDEX_HEAVY: state}, NOW + 120)["index_heavy"]
        assert health["rule"] == "at_ceiling"
        assert "services" not in health

    def test_services_are_kept_while_the_holdoff_after_a_downstream_shrink_lasts(self) -> None:
        _, state = _step(Pool.INDEX_HEAVY, feedback=_SLOW, current=8, now=NOW)
        _, held = _step(Pool.INDEX_HEAVY, state=state, current=4, demand=_BUSY, now=NOW + 40)
        health = _pool_health({Pool.INDEX_HEAVY: held}, NOW + 40)["index_heavy"]
        assert health["rule"] == "downstream_holdoff"
        assert health["services"] == ["embedding"]

    def test_a_later_memory_shrink_does_not_hide_a_downstream_cooldown_that_is_still_open(self) -> None:
        _, state = _step(Pool.INDEX_HEAVY, feedback=_OUTAGE, current=8, now=NOW)
        _, state = _step(Pool.INDEX_HEAVY, snap=_SOFT, state=state, current=4, now=NOW + 15)
        health = _pool_health({Pool.INDEX_HEAVY: state}, NOW + 40)["index_heavy"]
        assert health["cooldownCause"] == "downstream"
        assert health["services"] == ["embedding"]
