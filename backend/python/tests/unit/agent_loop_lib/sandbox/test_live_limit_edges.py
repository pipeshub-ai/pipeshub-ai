"""Edge cases of the live limit: a long unread spell, low CPU brakes, and a
slow per-run memory check racing another worker's charge."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.host_reading import HostSample, LiveMode
from app.agent_loop_lib.sandbox.live_limit import (
    RERESOLVE_S,
    LiveLimit,
    host_profile,
    initial_record,
    step,
)
from app.agent_loop_lib.sandbox.live_settings import LiveSettings

from .live_fakes import FakeClock, FakeReader, capacity, sample

if TYPE_CHECKING:
    from pathlib import Path

GiB = 2**30
M = 512 * 2**20


def _live(tmp_path: Path, clock: FakeClock, reader: FakeReader) -> LiveLimit:
    mode = LiveMode(mode="on:host", reader=reader, profile="host")
    live = LiveLimit(
        capacity(), None, settings=LiveSettings(), resolver=lambda: mode,  # type: ignore[arg-type]
        directory=tmp_path / "state", clock=clock,
    )
    live.apply_mode(mode)
    return live


class TestUnreadBackoff:
    def test_days_of_unread_retries_stay_at_the_cap(self, tmp_path: Path) -> None:
        live = _live(tmp_path, FakeClock(), FakeReader(sample(4.0)))
        live.apply_mode(LiveMode.off("unread", "not read yet", retry=True))
        live._unread_tries = 2000
        assert live.reresolve_after_s() == RERESOLVE_S


class TestGrowAtLowBrakes:
    def test_an_idle_cpu_grows_at_the_lowest_brakes(self) -> None:
        profile = host_profile(LiveSettings(host_cpu_brake=0.10, host_cpu_psi_brake=0.05))
        now = 1000.0
        reading = HostSample(
            mem_total=16 * GiB, mem_available=12 * GiB, source="host", cpu_util=0.0, cpu_psi_some_avg10=0.0,
        )
        rec = initial_record(None, reading, profile=profile, m=M, ceiling=6, floor=1, held=0, now=now, pid=1)
        rec = replace(rec, limit=2, cooldown_until=0.0)
        now += 15.0
        rec = replace(rec, last_wait_limit_at=now - 0.1)
        stepped = step(rec, reading, profile=profile, m=M, ceiling=6, floor=1, held=0, now=now)
        assert stepped.rule == "grow" and stepped.limit > 2


class SlowReader(FakeReader):
    """A reading that takes a while, during which another worker admits a run."""

    def __init__(self, first: HostSample, clock: FakeClock, during: LiveLimit, delay: float) -> None:
        super().__init__(first)
        self.clock, self.during, self.delay = clock, during, delay

    def memory(self) -> tuple[int | None, int | None]:
        self.clock.now += self.delay
        assert self.during._admit_locked() is None
        return super().memory()


class TestSlowAdmission:
    def test_a_charge_made_during_a_slow_reading_is_kept(self, tmp_path: Path) -> None:
        clock = FakeClock()
        other = _live(tmp_path, clock, FakeReader(sample(8.0)))
        slow = _live(tmp_path, clock, FakeReader(sample(8.0)))
        slow.apply_mode(LiveMode(
            mode="on:host", reader=SlowReader(sample(8.0), clock, other, delay=2.0), profile="host",
        ))
        assert slow._admit_locked() is None
        rec = slow._ledger.read()
        assert rec is not None and len(rec.charges) == 2
        assert sum(size for _, size in rec.charges) == 2 * M
