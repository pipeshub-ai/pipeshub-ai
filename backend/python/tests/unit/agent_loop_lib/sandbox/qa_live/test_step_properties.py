"""Property tests for the live limit's pure step.

Over any record and any reading: the limit stays within ``[MIN, C]``, never
grows on a hard memory sample or memory pressure, never grows above the
memory target, and halves at most to ``MIN`` on the hard rule. Under a steady
reading it converges: down to the target without waiters, up to the target
with them.
"""

from __future__ import annotations

from dataclasses import replace

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.sandbox.host_reading import HostSample
from app.agent_loop_lib.sandbox.live_limit import (
    LiveProfile,
    host_profile,
    initial_record,
    sidecar_profile,
    step,
)
from app.agent_loop_lib.sandbox.live_record import RULES, LiveRecord
from app.agent_loop_lib.sandbox.live_settings import LiveSettings

GiB = 2**30
MiB = 2**20
PROFILES = (host_profile(LiveSettings()), sidecar_profile(LiveSettings()))

_frac = st.none() | st.floats(0.0, 1.5, allow_nan=False)
_totals = st.integers(1 * GiB, 256 * GiB)


@st.composite
def _case(draw: st.DrawFn) -> tuple[LiveRecord, HostSample | None, dict]:
    ceiling = draw(st.integers(1, 200))
    floor = draw(st.integers(1, ceiling))
    total = draw(_totals)
    if draw(st.booleans()):
        sample: HostSample | None = HostSample(
            mem_total=total,
            # MemAvailable above MemTotal and negative values are garbage a
            # reader could pass through; the step must still stay in range.
            mem_available=draw(st.integers(-GiB, total + 4 * GiB)),
            cpu_util=draw(_frac), cpu_psi_some_avg10=draw(_frac), mem_psi_full_avg10=draw(_frac),
            cpu_throttled_ratio=draw(_frac), mem_oom_events=draw(st.none() | st.integers(0, 3)),
            source="host",
        )
    else:
        sample = draw(st.sampled_from([None, HostSample(source="host"), HostSample(mem_total=total, source="host")]))
    now = 10_000.0
    rec = LiveRecord(
        limit=draw(st.integers(1, 250)), ceiling=ceiling, min=floor,
        target=draw(st.none() | st.integers(0, 250)), rule=draw(st.sampled_from(RULES)),
        stamp=now - 15, last_step_at=now - 15, cooldown_until=draw(st.sampled_from([0.0, now - 1, now + 20])),
        cpu_high_streak=draw(st.integers(0, 5)), in_slow_start=draw(st.booleans()),
        slow_start_step=draw(st.integers(1, 64)), last_wait_limit_at=draw(st.sampled_from([0.0, now - 20, now - 5])),
        sample_at=draw(st.sampled_from([0.0, now - 30, now - 15])),
    )
    kwargs = {
        "profile": draw(st.sampled_from(PROFILES)), "m": float(draw(st.integers(64 * MiB, 8 * GiB))),
        "ceiling": ceiling, "floor": floor, "held": draw(st.integers(0, 250)), "now": now,
    }
    return rec, sample, kwargs


def _hard(sample: HostSample, profile: LiveProfile, m: float) -> bool:
    assert sample.mem_total is not None and sample.mem_available is not None
    hard_floor, _ = profile.memory_lines(sample.mem_total, m)
    return (
        sample.mem_available < hard_floor + (m if profile.hard_adds_run else 0)
        or (sample.mem_psi_full_avg10 or 0.0) > profile.mem_psi_hard
        or (sample.mem_oom_events or 0) > 0
    )


class TestStepInvariants:
    @settings(max_examples=1500, deadline=None)
    @given(_case())
    def test_the_limit_stays_within_min_and_the_ceiling(self, case: tuple) -> None:
        rec, sample, kwargs = case
        new = step(rec, sample, **kwargs)
        assert kwargs["floor"] <= new.limit <= kwargs["ceiling"]
        assert new.ceiling == kwargs["ceiling"] and new.min == kwargs["floor"]
        assert new.last_step_at == kwargs["now"] == new.stamp
        assert new.rule in RULES
        if new.limit >= kwargs["ceiling"]:
            assert new.shrink_cause is None

    @settings(max_examples=1500, deadline=None)
    @given(_case())
    def test_never_grows_without_a_usable_reading_or_on_memory_trouble(self, case: tuple) -> None:
        rec, sample, kwargs = case
        start = min(max(rec.limit, kwargs["floor"]), kwargs["ceiling"])
        new = step(rec, sample, **kwargs)
        if sample is None or sample.mem_available is None or sample.mem_total is None:
            assert new.limit == start and new.rule == "unknown"
            return
        if _hard(sample, kwargs["profile"], kwargs["m"]):
            assert new.limit <= max(kwargs["floor"], start // 2)
            assert new.rule == "mem_hard" or new.limit < start or new.limit == kwargs["floor"]
        if new.limit > start:
            # Growth only for a waiter, within the target, below the CPU brake.
            assert new.rule == "grow"
            assert new.target is not None and new.limit <= new.target
            assert rec.last_wait_limit_at >= rec.last_step_at > 0
            assert kwargs["now"] >= rec.cooldown_until

    @settings(max_examples=800, deadline=None)
    @given(_case())
    def test_a_shrink_restarts_slow_start_and_a_cooldown(self, case: tuple) -> None:
        rec, sample, kwargs = case
        new = step(rec, sample, **kwargs)
        if new.rule in ("mem_hard", "mem_target", "cpu"):
            assert new.slow_start_step == 1 and new.in_slow_start
            assert new.cooldown_until > kwargs["now"]

    @settings(max_examples=500, deadline=None)
    @given(_case())
    def test_initial_record_is_within_range_and_keeps_charges(self, case: tuple) -> None:
        rec, sample, kwargs = case
        rec = replace(rec, charges=((kwargs["now"] + 3, 7),))
        new = initial_record(rec, sample, pid=1, **kwargs)
        assert kwargs["floor"] <= new.limit <= kwargs["ceiling"]
        assert new.charges == rec.charges and new.rule == "start"


def _trace(
    profile: LiveProfile, sample: HostSample, *, ceiling: int, floor: int, limit: int, waiting: bool,
    steps: int = 40, held: int = 0,
) -> list[int]:
    m = 512.0 * MiB
    now = 1000.0
    rec = replace(
        initial_record(None, sample, profile=profile, m=m, ceiling=ceiling, floor=floor, held=held, now=now, pid=1),
        limit=limit,
    )
    limits = [rec.limit]
    for _ in range(steps):
        now += 15.0
        if waiting:
            rec = replace(rec, last_wait_limit_at=now - 0.5)
        rec = step(rec, sample, profile=profile, m=m, ceiling=ceiling, floor=floor, held=held, now=now)
        limits.append(rec.limit)
    return limits


class TestConvergence:
    @settings(max_examples=300, deadline=None)
    @given(
        avail_gib=st.floats(0.0, 60.0, allow_nan=False), ceiling=st.integers(2, 64),
        floor=st.integers(1, 4), start=st.integers(1, 64), waiting=st.booleans(),
    )
    def test_a_steady_reading_settles_on_one_value(
        self, avail_gib: float, ceiling: int, floor: int, start: int, waiting: bool,
    ) -> None:
        assume(floor <= ceiling)
        profile = PROFILES[0]
        sample = HostSample(mem_total=64 * GiB, mem_available=int(avail_gib * GiB), cpu_util=0.2, source="host")
        limits = _trace(profile, sample, ceiling=ceiling, floor=floor, limit=start, waiting=waiting)
        tail = limits[-10:]
        assert len(set(tail)) == 1, limits
        target = initial_record(
            None, sample, profile=profile, m=512.0 * MiB, ceiling=ceiling, floor=floor, held=0, now=0.0, pid=1,
        ).target
        assert target is not None
        hard = sample.mem_available is not None and _hard(sample, profile, 512.0 * MiB)
        if hard:
            assert tail[0] == floor
        elif waiting:
            assert tail[0] == target
        else:
            # Without waiters it only comes down to the target, never up.
            assert tail[0] == min(max(start, floor), ceiling) if start <= target else tail[0] == target

    def test_busy_cpu_walks_down_to_min_and_holds(self) -> None:
        sample = HostSample(mem_total=64 * GiB, mem_available=60 * GiB, cpu_util=0.99, source="host")
        limits = _trace(PROFILES[0], sample, ceiling=20, floor=3, limit=20, waiting=True)
        assert limits[-1] == 3 and min(limits) == 3

    def test_slow_start_doubles_to_the_target_after_a_shrink(self) -> None:
        profile = PROFILES[0]
        m, now = 512.0 * MiB, 1000.0
        low = HostSample(mem_total=64 * GiB, mem_available=8 * GiB, cpu_util=0.2, source="host")
        high = HostSample(mem_total=64 * GiB, mem_available=60 * GiB, cpu_util=0.2, source="host")
        rec = initial_record(None, high, profile=profile, m=m, ceiling=60, floor=2, held=0, now=now, pid=1)
        for _ in range(8):
            now += 15.0
            rec = step(rec, low, profile=profile, m=m, ceiling=60, floor=2, held=0, now=now)
        assert rec.limit == 2
        now += 60.0
        limits = []
        for _ in range(8):
            now += 15.0
            rec = replace(rec, last_wait_limit_at=now - 0.5)
            rec = step(rec, high, profile=profile, m=m, ceiling=60, floor=2, held=0, now=now)
            limits.append(rec.limit)
        # 2 -> 3 -> 5 -> 9 -> 17 -> 33 -> capped at the ceiling.
        assert limits[:5] == [3, 5, 9, 17, 33] and limits[-1] == 60

    def test_a_fresh_record_also_grows_by_slow_start(self) -> None:
        # Started while memory was short (target 2), then memory frees with
        # runs waiting: growth doubles as after a shrink, rather than +1 per
        # sample, which would take 15 minutes to reach a ceiling of 60.
        profile = PROFILES[0]
        m, now = 512.0 * MiB, 1000.0
        low = HostSample(mem_total=64 * GiB, mem_available=8 * GiB, cpu_util=0.2, source="host")
        high = HostSample(mem_total=64 * GiB, mem_available=60 * GiB, cpu_util=0.2, source="host")
        rec = initial_record(None, low, profile=profile, m=m, ceiling=60, floor=2, held=0, now=now, pid=1)
        assert rec.limit == 2
        limits = []
        for _ in range(8):
            now += 15.0
            rec = replace(rec, last_wait_limit_at=now - 0.5)
            rec = step(rec, high, profile=profile, m=m, ceiling=60, floor=2, held=0, now=now)
            limits.append(rec.limit)
        assert limits[:5] == [3, 5, 9, 17, 33] and limits[-1] == 60
