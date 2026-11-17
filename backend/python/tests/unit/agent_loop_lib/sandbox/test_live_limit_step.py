"""Tests for the live limit's step function and over-commit arithmetic."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.host_reading import HostSample, LiveMode
from app.agent_loop_lib.sandbox.live_limit import (
    LiveLimit,
    host_profile,
    initial_record,
    step,
)
from app.agent_loop_lib.sandbox.live_settings import LiveSettings

from .live_fakes import FakeReader
from .live_fakes import capacity as _capacity

if TYPE_CHECKING:
    from pathlib import Path

    from app.agent_loop_lib.sandbox.capacity import SandboxCapacity
    from app.agent_loop_lib.sandbox.live_record import LiveRecord

GiB = 2**30
MiB = 2**20
M = 512 * MiB
PROFILE = host_profile(LiveSettings())


def _sample(avail_gib: float, total_gib: float = 16, **kwargs: object) -> HostSample:
    return HostSample(mem_total=int(total_gib * GiB), mem_available=int(avail_gib * GiB), source="host", **kwargs)  # type: ignore[arg-type]


class Trace:
    """Steps a record every 15 s, optionally with a run waiting before each step."""

    def __init__(self, first: HostSample, *, ceiling: int, floor: int = 2, held: int = 0) -> None:
        self.now = 1000.0
        self.ceiling, self.floor, self.held = ceiling, floor, held
        self.rec = initial_record(
            None, first, profile=PROFILE, m=M, ceiling=ceiling, floor=floor, held=held, now=self.now, pid=1,
        )

    def __call__(self, sample: HostSample | None, *, waiting: bool = False) -> LiveRecord:
        self.now += 15.0
        if waiting:
            self.rec = replace(self.rec, last_wait_limit_at=self.now - 0.1)
        self.rec = step(
            self.rec, sample, profile=PROFILE, m=M, ceiling=self.ceiling, floor=self.floor,
            held=self.held, now=self.now,
        )
        return self.rec


class TestMemoryLines:
    def test_defaults_on_16_and_192_gib(self) -> None:
        assert PROFILE.memory_lines(16 * GiB, M) == (1 * GiB, int(2.4 * GiB))
        assert PROFILE.memory_lines(192 * GiB, M) == (8 * GiB, 24 * GiB)

    def test_explicit_values(self) -> None:
        profile = host_profile(LiveSettings(host_mem_floor_mb=2048, host_mem_soft_mb=1024))
        hard, soft = profile.memory_lines(16 * GiB, M)
        assert hard == 2 * GiB
        assert soft == 2 * GiB + M


class TestGoldenTraces:
    def test_example_a_16_gib(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        assert trace.rec.limit == 6 and trace.rec.rule == "start"
        limits = [trace(_sample(4.0)).limit, trace(_sample(4.0)).limit, trace(_sample(2.0)).limit]
        assert limits == [4, 3, 2]
        assert trace.rec.rule == "mem_target" and trace.rec.shrink_cause == "memory"
        hard = trace(_sample(1.3))
        assert (hard.limit, hard.rule) == (2, "mem_hard")
        rules = [trace(_sample(6.0), waiting=True).rule for _ in range(3)]
        assert rules == ["cooldown"] * 3
        assert [trace(_sample(6.0), waiting=True).limit for _ in range(3)] == [3, 5, 6]
        assert trace.rec.shrink_cause is None
        assert trace(_sample(6.0), waiting=True).rule == "at_ceiling"

    def test_example_b_192_gib(self) -> None:
        trace = Trace(_sample(160, 192), ceiling=72)
        assert trace.rec.limit == 72
        limits = [trace(_sample(10, 192)).limit for _ in range(7)]
        assert limits == [37, 19, 10, 6, 4, 3, 2]


class TestRules:
    def test_never_below_the_floor(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        trace.rec = replace(trace.rec, limit=3)
        assert trace(_sample(0.5)).limit == 2
        assert trace(_sample(0.5)).limit == 2

    def test_tightest_rule_wins(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        rec = trace(_sample(1.2, mem_psi_full_avg10=0.2, cpu_util=0.99))
        assert (rec.limit, rec.rule) == (3, "mem_hard")

    def test_memory_psi_is_hard(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        assert trace(_sample(7.0, mem_psi_full_avg10=0.06)).rule == "mem_hard"

    def test_cpu_brake_needs_two_samples_and_unknown_resets(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        busy = _sample(7.0, cpu_util=0.95)
        assert trace(busy).rule == "no_demand"
        assert trace(None).rule == "unknown"
        assert trace(busy).rule == "no_demand"
        rec = trace(busy)
        assert (rec.limit, rec.rule, rec.shrink_cause) == (5, "cpu", "cpu")

    def test_psi_brake(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        trace(_sample(7.0, cpu_psi_some_avg10=0.8))
        assert trace(_sample(7.0, cpu_psi_some_avg10=0.8)).rule == "cpu"

    def test_growth_needs_waiters_and_the_cpu_band(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        trace.rec = replace(trace.rec, limit=3, cooldown_until=0)
        assert trace(_sample(7.0)).rule == "no_demand"
        assert trace(_sample(7.0, cpu_util=0.85), waiting=True).rule == "cpu_hold"
        assert trace(_sample(7.0, cpu_util=0.5, cpu_psi_some_avg10=0.6), waiting=True).rule == "cpu_hold"
        assert trace(_sample(7.0, cpu_util=0.5), waiting=True).rule == "grow"

    def test_slow_start_is_capped_at_the_target(self) -> None:
        trace = Trace(_sample(7.0), ceiling=72)
        trace.rec = replace(trace.rec, limit=2, cooldown_until=0, in_slow_start=True, slow_start_step=1)
        # target = (5.4 - 2.4) / 0.5 = 6 on 16 GiB
        limits = [trace(_sample(5.4), waiting=True).limit for _ in range(4)]
        assert limits == [3, 5, 6, 6]
        assert trace.rec.rule == "at_target"

    def test_a_shrink_resets_slow_start(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        trace.rec = replace(trace.rec, limit=6, cooldown_until=0, in_slow_start=False, slow_start_step=4)
        trace(_sample(4.0))
        rec = trace(_sample(4.0))
        assert (rec.rule, rec.in_slow_start, rec.slow_start_step) == ("mem_target", True, 1)


class TestReversalAfterGrowth:
    """A shrink right after a grow needs the target below the limit twice."""

    def _grown(self) -> Trace:
        trace = Trace(_sample(7.0), ceiling=6)
        trace.rec = replace(trace.rec, limit=3, cooldown_until=0, in_slow_start=True, slow_start_step=1)
        assert trace(_sample(5.4), waiting=True).rule == "grow"
        assert trace.rec.limit == 4 and trace.rec.target == 6
        return trace

    def test_one_low_sample_holds_and_a_second_shrinks(self) -> None:
        trace = self._grown()
        # target (3.9 - 2.4) / 0.5 = 3: the runs the grow let in took memory
        assert (trace(_sample(3.9)).limit, trace.rec.rule) == (4, "no_demand")
        assert (trace(_sample(3.9)).limit, trace.rec.rule) == (3, "mem_target")

    def test_it_shrinks_only_to_the_higher_of_the_two_targets(self) -> None:
        trace = self._grown()
        trace(_sample(3.4))  # target 2
        rec = trace(_sample(3.9))  # target 3
        assert (rec.limit, rec.rule) == (3, "mem_target")

    def test_a_hard_floor_still_halves_at_once(self) -> None:
        trace = self._grown()
        rec = trace(_sample(1.3))
        assert (rec.limit, rec.rule) == (2, "mem_hard")

    def test_a_lost_reading_does_not_carry_an_old_target(self) -> None:
        trace = self._grown()
        assert trace(None).rule == "unknown"
        assert (trace(_sample(3.9)).limit, trace.rec.rule) == (3, "mem_target")

    def test_without_a_grow_the_target_applies_at_once(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        assert trace(_sample(3.9)).limit == 4

    def test_unknown_memory_holds(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        trace.rec = replace(trace.rec, limit=4)
        rec = trace(HostSample(mem_total=None, mem_available=None, cpu_util=0.1))
        assert (rec.limit, rec.rule) == (4, "unknown")

    def test_a_live_memory_wait_regrows_after_recovery(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        trace(_sample(4.0))
        assert trace.rec.limit == 4
        trace.now += 30
        assert trace(_sample(7.0), waiting=True).limit == 5

    def test_a_floor_wait_alone_does_not_grow(self) -> None:
        trace = Trace(_sample(7.0), ceiling=6)
        trace(_sample(4.0))
        trace.now += 30
        trace.rec = replace(trace.rec, last_wait_mem_at=trace.now + 14)
        assert trace(_sample(7.0)).rule == "no_demand"

    def test_initial_record_without_a_reading_is_the_ceiling(self) -> None:
        rec = initial_record(None, None, profile=PROFILE, m=M, ceiling=6, floor=2, held=0, now=1.0, pid=1)
        assert (rec.limit, rec.rule) == (6, "start")


def _live(capacity: SandboxCapacity, tmp_path: Path, k: float, mode: str = "on:host") -> LiveLimit:
    live = LiveLimit(
        capacity, None, settings=LiveSettings(memory_overcommit=k), directory=tmp_path / "state",
        resolver=lambda: LiveMode(mode="off:x"),
    )
    profile = "host" if mode == "on:host" else "cgroup" if mode == "on:cgroup" else None
    live.apply_mode(LiveMode(mode=mode, reader=FakeReader() if profile else None, profile=profile))  # type: ignore[arg-type]
    return live


class TestOvercommit:
    def test_off_by_default(self, tmp_path: Path) -> None:
        capacity = _capacity(16, 16)
        live = _live(capacity, tmp_path, 1.0)
        assert live._overcommit == 1.0 and live._ceiling(1.0) == capacity.ceiling() == 8

    def test_raises_a_memory_bound_ceiling(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        capacity = _capacity(16, 16)
        with caplog.at_level(logging.INFO):
            live = _live(capacity, tmp_path, 2.0)
        assert live._overcommit == 2.0 and live._ceiling(2.0) == 16
        assert live._run_bytes(2.0) == M / 2
        assert "ceiling 8->16" in caplog.text

    def test_cpu_term_binds(self, tmp_path: Path) -> None:
        capacity = _capacity(64, 16)
        assert capacity.ceiling() == 24
        assert _live(capacity, tmp_path, 2.0)._ceiling(2.0) == 24

    def test_clamped_to_the_slot_files(self, tmp_path: Path) -> None:
        capacity = _capacity(256, 1000)
        assert capacity.ceiling() == 128
        assert _live(capacity, tmp_path, 4.0)._ceiling(4.0) == 256

    def test_explicit_total_is_unchanged(self, tmp_path: Path) -> None:
        capacity = _capacity(16, 16, max_total=5)
        assert _live(capacity, tmp_path, 3.0)._ceiling(3.0) == 5

    @pytest.mark.parametrize("mode", ["on:cgroup", "off:unverified", "off:disabled"])
    def test_forced_to_one_without_a_host_reading(self, tmp_path: Path, mode: str, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            live = _live(_capacity(16, 16), tmp_path, 2.0, mode)
        assert live._overcommit == 1.0
        assert "forced to 1.0" in caplog.text
