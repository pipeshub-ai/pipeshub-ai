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
