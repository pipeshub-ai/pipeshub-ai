"""Brake entries are counted once per real transition, on the final state, in both ``_sample_once`` paths.

The capacity-container path is driven through the real ``_sample_once`` with a fake container whose ``step``
returns the state it is told to (``ResourceGovernor._container`` is what PIPESHUB_CAPACITY_PROFILE=auto installs).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from app.services.resource_governor import ResourceGovernor
from app.services.resource_governor.feedback import DownstreamFeedback
from app.services.resource_governor.models import LimitRule, Pool, ResourceSnapshot

LIMIT = 8 * 1024 ** 3


class _Probe:
    def __init__(self) -> None:
        self.level = 0.1

    def snapshot(self) -> ResourceSnapshot:
        return ResourceSnapshot(cpu_quota=4.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
                                mem_limit_bytes=LIMIT, mem_working_set_bytes=int(LIMIT * self.level), source="test")


class _Container:
    """Returns the governor's own decision, or rewrites the heavy-parse rule, or runs an incident mid-step."""

    def __init__(self) -> None:
        self.mode = "pass"
        self.governor: ResourceGovernor | None = None
        self.calls = 0

    def step(self, *, own: tuple[Any, Any], now: float, **_rest: object) -> tuple[Any, Any]:
        self.calls += 1
        limits, state = own
        heavy = state.get(Pool.HEAVY_PARSE)
        if self.mode == "reverse":  # the container step lifts the brake the own decision made
            state = state.with_update(Pool.HEAVY_PARSE, replace(heavy, rule=LimitRule.GROW_BAND, cooldown_until=0.0, cooldown_causes=()))
            for p in Pool:
                state = state.with_update(p, replace(state.get(p), rule=LimitRule.GROW_BAND, cooldown_until=0.0, cooldown_causes=()))
        elif self.mode == "cause":  # the container step itself brakes a pool the own decision left calm
            state = state.with_update(Pool.HEAVY_PARSE, replace(heavy, rule=LimitRule.MEM_SOFT, rule_since=now))
        elif self.mode == "incident":  # an incident is reported while the step runs (outside the state lock)
            assert self.governor is not None
            self.governor.report_memory_incident("test")
        return limits, state

    def stats(self) -> dict:
        return {}


def _governor(container: _Container | None) -> tuple[ResourceGovernor, _Probe]:
    probe = _Probe()
    g = ResourceGovernor(logger=logging.getLogger("test.container"), env_index=24, worker_count=1, probe=probe,
                         feedback=DownstreamFeedback())
    if container is not None:
        container.governor = g
        g._container = container
    return g, probe


def _entries(g: ResourceGovernor) -> dict:
    return g.admission_explain()["brakeEntries"]


def _sample(g: ResourceGovernor) -> None:
    asyncio.run(g._sample_once())


def test_without_a_container_one_pressure_sample_is_one_entry_and_holding_adds_none() -> None:
    g, probe = _governor(None)
    try:
        _sample(g)
        assert _entries(g) == {}
        probe.level = 0.97
        _sample(g)
        first = _entries(g)
        assert first and all(r.startswith("memory") for row in first.values() for r in row), first
        _sample(g)  # heavy_parse may move from memory_target (not counted) to memory_hard: a real new entry
        settled = _entries(g)
        _sample(g)  # still under pressure: the same brakes, no new entry
        assert _entries(g) == settled
    finally:
        g.close()


def test_with_a_container_passing_the_decision_through_counts_once() -> None:
    c = _Container()
    g, probe = _governor(c)
    try:
        _sample(g)
        assert c.calls == 1 and _entries(g) == {}
        probe.level = 0.97
        _sample(g)
        first = _entries(g)
        assert first and sum(n for row in first.values() for n in row.values()) == len(first), first  # one per pool
        _sample(g)
        settled = _entries(g)
        _sample(g)
        assert _entries(g) == settled
    finally:
        g.close()


def test_a_brake_the_container_step_reverses_is_not_counted() -> None:
    c = _Container()
    g, probe = _governor(c)
    try:
        _sample(g)
        c.mode = "reverse"
        probe.level = 0.97  # the own decision brakes; the container step lifts it
        _sample(g)
        assert _entries(g) == {}, _entries(g)
        assert all(row["brakeReason"] in ("none", "ramping", None) for row in g.admission_explain()["pools"].values())
    finally:
        g.close()


def test_a_brake_the_container_step_itself_causes_is_counted_once() -> None:
    c = _Container()
    g, _probe = _governor(c)
    try:
        _sample(g)
        c.mode = "cause"  # calm own decision; the step brakes heavy_parse
        _sample(g)
        assert _entries(g) == {"heavy_parse": {"memory_soft": 1}}, _entries(g)
        _sample(g)  # held by the step again: no new entry
        assert _entries(g) == {"heavy_parse": {"memory_soft": 1}}, _entries(g)
    finally:
        g.close()


def test_an_incident_during_the_container_step_is_one_entry() -> None:
    c = _Container()
    g, _probe = _governor(c)
    try:
        _sample(g)
        c.mode = "incident"
        _sample(g)
        assert _entries(g) == {"heavy_parse": {"memory_hard": 1}}, _entries(g)
    finally:
        g.close()


def test_every_real_transition_through_the_container_is_one_entry_and_a_repeat_is_none() -> None:
    """brake, hold, release, brake again: two entries, however many samples, with the container in the path."""
    c = _Container()
    g, _probe = _governor(c)
    try:
        _sample(g)
        seen = []
        for mode in ("cause", "cause", "reverse", "pass", "cause", "cause"):
            c.mode = mode
            _sample(g)
            seen.append(_entries(g).get("heavy_parse", {}).get("memory_soft", 0))
        assert seen == [1, 1, 1, 1, 2, 2], seen
    finally:
        g.close()
