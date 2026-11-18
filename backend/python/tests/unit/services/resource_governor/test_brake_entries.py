"""The governor counts brake entries where the decision is made, so a reader that looks late misses none."""
from __future__ import annotations

from dataclasses import replace

from app.services.resource_governor.models import ControllerState, LimitRule, Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

NOW = 10_000.0


def _state(rule: LimitRule) -> ControllerState:
    state = ControllerState.initial()
    return state.with_update(Pool.HEAVY_PARSE, replace(state.get(Pool.HEAVY_PARSE), rule=rule, rule_since=NOW))


def _entries(governor) -> dict:
    return governor.admission_explain()["brakeEntries"]


def test_a_pool_that_enters_the_brake_n_times_between_two_reads_counts_n() -> None:
    governor = make_test_governor()
    try:
        assert _entries(governor) == {}
        path = [LimitRule.GROW_BAND, LimitRule.MEM_SOFT, LimitRule.MEM_SOFT, LimitRule.GROW_BAND,
                LimitRule.MEM_SOFT, LimitRule.CPU, LimitRule.GROW_BAND, LimitRule.CPU]
        previous = _state(LimitRule.START)
        for rule in path:  # eight decisions, nobody reads in between
            current = _state(rule)
            governor._count_brake_entries(previous, current, NOW)
            previous = current
        assert _entries(governor) == {"heavy_parse": {"memory_soft": 2, "cpu": 2}}
    finally:
        governor.close()


def test_staying_in_a_brake_and_moving_between_rules_of_one_reason_is_not_a_new_entry() -> None:
    governor = make_test_governor()
    try:
        before = _state(LimitRule.MEM_HARD)
        governor._count_brake_entries(_state(LimitRule.START), before, NOW)
        governor._count_brake_entries(before, _state(LimitRule.MEM_INCIDENT), NOW)  # the same reason
        governor._count_brake_entries(_state(LimitRule.MEM_INCIDENT), _state(LimitRule.MEM_INCIDENT), NOW)
        assert _entries(governor) == {"heavy_parse": {"memory_hard": 1}}
    finally:
        governor.close()


def test_the_counter_is_monotonic_and_a_reader_gets_a_copy() -> None:
    governor = make_test_governor()
    try:
        governor._count_brake_entries(_state(LimitRule.START), _state(LimitRule.CPU), NOW)
        seen = _entries(governor)
        seen["heavy_parse"]["cpu"] = 99  # a caller's edit must not reach the governor
        governor._count_brake_entries(_state(LimitRule.CPU), _state(LimitRule.GROW_BAND), NOW)
        governor._count_brake_entries(_state(LimitRule.GROW_BAND), _state(LimitRule.CPU), NOW)
        assert _entries(governor) == {"heavy_parse": {"cpu": 2}}
    finally:
        governor.close()


def test_a_reported_memory_incident_is_an_entry_once() -> None:
    governor = make_test_governor()
    try:
        governor.report_memory_incident("test")
        governor.report_memory_incident("test")  # still in the same incident
        assert _entries(governor) == {"heavy_parse": {"memory_hard": 1}}
    finally:
        governor.close()


# -- a cooldown goes on holding for the pressure that started it ------------------


def _policy_step(state: ControllerState, working_set_gb: float | None, now: float) -> ControllerState:
    """One real policy decision (``next_limits``) over a 4 GB cgroup, for every pool."""
    from app.services.resource_governor.models import PoolDemand, ResourceSnapshot
    from app.services.resource_governor.policy import (
        next_limits,
        resolve_ceilings,
        warm_start_limits,
    )

    gb = 1024**3
    snap = ResourceSnapshot(cpu_quota=8.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
                            mem_limit_bytes=4 * gb, mem_working_set_bytes=None if working_set_gb is None else int(working_set_gb * gb), source="test")
    ceilings = resolve_ceilings(snap, None, None)
    demand = {pool: PoolDemand(permit_seconds=1000.0, blocked_acquires=5, completions=50) for pool in Pool}
    _limits, new_state = next_limits(warm_start_limits(ceilings), snap, ceilings, state, demand, now)
    return new_state


def test_the_policys_own_cooldown_keeps_the_brake_it_came_from_and_a_return_is_not_a_second_entry() -> None:
    governor = make_test_governor()
    try:
        state = ControllerState.initial()
        history = []
        for step, working_set in enumerate((3.9, 1.0, 3.9, 1.0)):  # squeezed, relieved (cooldown), squeezed, relieved
            now = NOW + step * 15.0
            new = _policy_step(state, working_set, now)
            governor._count_brake_entries(state, new, now)
            history.append((new.get(Pool.INDEX_HEAVY).rule, governor.admission_explain()["pools"]["index_heavy"]["brakeReason"]))
            state = new
        rules = [rule for rule, _ in history]
        assert LimitRule.COOLDOWN in rules, rules  # the policy really did put the pool in a cooldown in between
        entries = _entries(governor).get("index_heavy", {})
        braked = {reason for _rule, reason in history}
        assert braked == {"memory_hard"}, history  # one reason held across the cooldown, not ramping
        assert sum(entries.values()) == 1, (entries, history)  # squeeze, cooldown, squeeze inside it: one brake
    finally:
        governor.close()


def test_a_sample_without_a_pressure_reading_does_not_end_the_brake_a_cooldown_holds() -> None:
    """3.9 GB, 1.0, unknown, 1.0, 3.9 (15 s apart): one entry, held as memory_hard through the unknown sample."""
    governor = make_test_governor()
    try:
        state = ControllerState.initial()
        seen = []
        for step, working_set in enumerate((3.9, 1.0, None, 1.0, 3.9)):
            now = NOW + step * 15.0
            new = _policy_step(state, working_set, now)
            governor._count_brake_entries(state, new, now)
            seen.append((new.get(Pool.INDEX_HEAVY).rule.value, governor.admission_explain()["pools"]["index_heavy"]["brakeReason"]))
            state = new
        assert [rule for rule, _ in seen][2] == "pressure_unknown", seen  # the policy really saw no reading
        assert [reason for _rule, reason in seen][1:4] == ["memory_hard"] * 3, seen  # not frozen, not ramping
        assert sum(_entries(governor)["index_heavy"].values()) == 1, (seen, _entries(governor))
    finally:
        governor.close()


def test_the_explain_reads_the_state_and_the_tracked_reasons_of_one_decision() -> None:
    """A decision made while admission_explain is between its reads must not be half in the answer."""
    import threading

    governor = make_test_governor()
    try:
        governor._count_brake_entries(ControllerState.initial(), _state(LimitRule.CPU), NOW)
        governor._state = _state(LimitRule.CPU)
        newer = _state(LimitRule.GROW_BAND)
        real = governor._stats_lock

        def decide() -> None:
            with governor._state_lock:
                governor._count_brake_entries(governor._state, newer, NOW)
                governor._state = newer

        class Gap:
            """Stands in for the stats lock; the first time it is taken, a decision tries to run."""

            def __init__(self) -> None:
                self.fired = False

            def __enter__(self) -> None:
                if not self.fired:
                    self.fired = True
                    worker = threading.Thread(target=decide)
                    worker.start()
                    worker.join(0.5)  # a decision that can run now is one the explain did not exclude
                real.acquire()

            def __exit__(self, *_exc: object) -> None:
                real.release()

        governor._stats_lock = Gap()
        row = governor.admission_explain()["pools"]["heavy_parse"]
        governor._stats_lock = real
        assert (row["rule"], row["brakeReason"]) in {("cpu", "cpu"), ("grow_band", "ramping")}, row
    finally:
        governor.close()


def test_a_cooldown_follows_the_cause_that_holds_it_not_the_last_brake() -> None:
    """mem_hard, then a CPU brake, then a cooldown whose longest window is the memory one: the pool is held for
    memory again, and that is an entry (the CPU brake had ended the memory hold as far as anyone could see)."""
    governor = make_test_governor()
    try:
        def cooldown(causes: tuple[tuple[str, float], ...]) -> ControllerState:
            state = ControllerState.initial()
            return state.with_update(Pool.HEAVY_PARSE, replace(state.get(Pool.HEAVY_PARSE), rule=LimitRule.COOLDOWN,
                                                                  cooldown_until=NOW + 300.0, cooldown_causes=causes))

        steps = [_state(LimitRule.MEM_HARD), _state(LimitRule.CPU), cooldown((("cpu", NOW + 60.0), ("memory", NOW + 300.0)))]
        previous = ControllerState.initial()
        for state in steps:
            governor._count_brake_entries(previous, state, NOW)
            previous = state
        assert governor.admission_explain()["pools"]["heavy_parse"]["brakeReason"] == "memory_hard"
        assert _entries(governor)["heavy_parse"] == {"memory_hard": 2, "cpu": 1}
        governor._count_brake_entries(previous, cooldown((("cpu", NOW + 400.0), ("memory", NOW + 300.0))), NOW)
        assert governor.admission_explain()["pools"]["heavy_parse"]["brakeReason"] == "cpu"  # the cpu window is the longer now
        assert _entries(governor)["heavy_parse"] == {"memory_hard": 2, "cpu": 2}
    finally:
        governor.close()


def test_the_sampling_decision_itself_counts_the_entry() -> None:
    """Through the real ``_sample_once``, not ``_count_brake_entries`` called by hand: a sample at 97 % of the
    memory limit brakes the pools, and the governor's count shows it (fails when the count leaves ``_sample_once``)."""
    import asyncio
    import logging

    from app.services.resource_governor import ResourceGovernor
    from app.services.resource_governor.feedback import DownstreamFeedback
    from app.services.resource_governor.models import ResourceSnapshot

    level = [0.1]

    class _Probe:
        def snapshot(self) -> ResourceSnapshot:
            limit = 8 * 1024**3
            return ResourceSnapshot(cpu_quota=4.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
                                    mem_limit_bytes=limit, mem_working_set_bytes=int(limit * level[0]), source="test")

    governor = ResourceGovernor(logger=logging.getLogger("test.brake"), env_index=24, worker_count=1, probe=_Probe(),
                                feedback=DownstreamFeedback())  # not the process-wide one: other tests leave symptoms in it
    try:
        asyncio.run(governor._sample_once())
        assert _entries(governor) == {}
        level[0] = 0.97
        asyncio.run(governor._sample_once())
        entries = _entries(governor)
        assert entries, "a sample at 97 % memory counted no brake entry"
        assert all(reason in ("memory_soft", "memory_hard") for row in entries.values() for reason in row), entries
    finally:
        governor.close()
