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


def _policy_step(state: ControllerState, working_set_gb: float, now: float) -> ControllerState:
    """One real policy decision (``next_limits``) over a 4 GB cgroup, for every pool."""
    from app.services.resource_governor.models import PoolDemand, ResourceSnapshot
    from app.services.resource_governor.policy import (
        next_limits,
        resolve_ceilings,
        warm_start_limits,
    )

    gb = 1024**3
    snap = ResourceSnapshot(cpu_quota=8.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
                            mem_limit_bytes=4 * gb, mem_working_set_bytes=int(working_set_gb * gb), source="test")
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
