"""Live limit paths the main suites miss: the sampler loop's error handling
and re-resolve, mode-change logs, a damaged record logged once, a failing
indexing callback, and the no-reading answers."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox import live_limit as live_mod
from app.agent_loop_lib.sandbox.capacity import HoldReason
from app.agent_loop_lib.sandbox.host_reading import HostSample, LiveMode
from app.agent_loop_lib.sandbox.live_limit import LiveLimit
from app.agent_loop_lib.sandbox.live_settings import LiveSettings

from ..live_fakes import FakeClock, FakeReader, capacity

if TYPE_CHECKING:
    from pathlib import Path

LOGGER = live_mod.__name__


class _NoJitter:
    def uniform(self, a: float, b: float) -> float:
        return 0.0


class _BrokenReader(FakeReader):
    def read(self) -> HostSample:
        self.reads += 1
        raise OSError("reading failed")


def _live(tmp_path: Path, *, settings: LiveSettings | None = None, resolver=None, clock=None) -> LiveLimit:
    return LiveLimit(
        capacity(), None, settings=settings or LiveSettings(),
        resolver=resolver or (lambda: LiveMode.off("disabled", "x")),
        directory=tmp_path / "state", clock=clock or FakeClock(), rng=_NoJitter(),  # type: ignore[arg-type]
    )


class TestSamplerLoop:
    async def test_errors_are_logged_and_a_retryable_mode_is_resolved_again(self, tmp_path: Path, caplog) -> None:
        reader = _BrokenReader()
        calls: list[int] = []

        def resolver() -> LiveMode:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("cannot choose")
            return LiveMode(mode="on:host", reader=reader, profile="host", note="test")

        ticks = [0.0]

        def clock() -> float:  # every look is far later: a re-resolve is always due
            ticks[0] += live_mod.RERESOLVE_S
            return ticks[0]

        live = _live(tmp_path, settings=LiveSettings(interval_s=0.0), resolver=resolver, clock=clock)
        after: list[int] = []

        def after_sample() -> None:
            after.append(1)
            raise RuntimeError("after_sample broke")

        live.after_sample = after_sample
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            live.start()
            first = live._task
            live.start()  # a second start keeps the running sampler
            assert live._task is first
            for _ in range(300):
                if reader.reads >= 2 and len(after) >= 3:
                    break
                await asyncio.sleep(0.01)
            await live.stop()
        assert first is not None and first.done()
        messages = [r.getMessage() for r in caplog.records]
        assert "sandbox live limit: choosing what to read failed" in messages
        assert any(m == "sandbox live limit: sample failed" for m in messages)
        assert any(m == "sandbox live limit: after_sample failed" for m in messages)
        # The error mode asked for a retry; the "on" mode did not.
        assert len(calls) == 2
        assert live.mode.mode == "on:host"

    async def test_stop_without_start(self, tmp_path: Path) -> None:
        live = _live(tmp_path)
        await live.stop()
        assert live._task is None


class TestModeLogs:
    def test_host_thresholds_on_a_sidecar_are_named_as_ignored_once(self, tmp_path: Path, caplog) -> None:
        live = _live(tmp_path, settings=LiveSettings(host_cpu_brake=0.5))
        mode = LiveMode(mode="on:sidecar", reader=FakeReader(source="sidecar"), profile="sidecar")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            live.apply_mode(mode)
            live.apply_mode(mode)  # no change: no second warning
        ignored = [r for r in caplog.records if "SANDBOX_HOST_MEM_* / SANDBOX_HOST_CPU_* are ignored" in r.getMessage()]
        assert len(ignored) == 1

    def test_untuned_sidecar_does_not_warn(self, tmp_path: Path, caplog) -> None:
        live = _live(tmp_path)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            live.apply_mode(LiveMode(mode="on:sidecar", reader=FakeReader(source="sidecar"), profile="sidecar"))
        assert not [r for r in caplog.records if "are ignored" in r.getMessage()]

    def test_an_on_mode_with_a_reason_is_a_warning(self, tmp_path: Path, caplog) -> None:
        live = _live(tmp_path)
        mode = LiveMode(
            mode="on:sidecar-containers", reader=FakeReader(source="sidecar-containers"), profile="sidecar",
            reason="sidecar cgroup reading unavailable (x); using container stats",
        )
        with caplog.at_level(logging.INFO, logger=LOGGER):
            live.apply_mode(mode)
        warned = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert [r.getMessage() for r in warned] == [
            "sandbox live limit: sidecar cgroup reading unavailable (x); using container stats",
        ]


class TestNoReading:
    def test_memory_now_without_a_reader(self, tmp_path: Path) -> None:
        live = _live(tmp_path)
        assert live._memory_now(0.0) == (None, None)
        assert live.hard_floor_bytes() is None
        assert live._admit_locked() is None

    def test_sample_once_while_off(self, tmp_path: Path) -> None:
        assert _live(tmp_path).sample_once() is None


class TestDamagedRecord:
    async def test_the_damage_is_logged_once_and_admission_continues(self, tmp_path: Path, caplog) -> None:
        reader = FakeReader()
        live = _live(tmp_path)
        live.apply_mode(LiveMode(mode="on:host", reader=reader, profile="host"))
        os.pwrite(live._ledger._record_fd, b"\xff" * 64, 0)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await live.admit_and_charge(held=0) is None
            os.pwrite(live._ledger._record_fd, b"\xff" * 64, 0)
            assert await live.admit_and_charge(held=0) is None
        damaged = [r for r in caplog.records if "shared record is damaged" in r.getMessage()]
        assert len(damaged) == 1


class TestMemoryWaitCallback:
    def test_a_failing_callback_does_not_fail_the_waiting_run(self, tmp_path: Path, caplog) -> None:
        live = _live(tmp_path)
        live.apply_mode(LiveMode(mode="on:host", reader=FakeReader(), profile="host"))
        calls: list[tuple[HoldReason, float]] = []

        def callback(reason: HoldReason, waited_s: float) -> None:
            calls.append((reason, waited_s))
            raise RuntimeError("indexing signal broke")

        live.on_memory_wait = callback
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            live.waiting(HoldReason.HOST_MEMORY_FLOOR, 4.0)
        assert calls == [(HoldReason.HOST_MEMORY_FLOOR, 4.0)]
        assert any("on_memory_wait failed" in r.getMessage() for r in caplog.records)

    def test_an_org_share_wait_is_not_stamped_nor_signalled(self, tmp_path: Path) -> None:
        live = _live(tmp_path)
        live.apply_mode(LiveMode(mode="on:host", reader=FakeReader(), profile="host"))
        live.on_memory_wait = lambda reason, waited: (_ for _ in ()).throw(AssertionError("called"))
        live.waiting(HoldReason.ORG_SHARE, 10.0)


class TestHeldCount:
    def test_a_busy_count_keeps_the_last_known_held(self, tmp_path: Path) -> None:
        from ..live_fakes import FakeGovernor

        governor = FakeGovernor(held=3)
        live = LiveLimit(
            capacity(), governor, settings=LiveSettings(),  # type: ignore[arg-type]
            resolver=lambda: LiveMode.off("disabled", "x"), directory=tmp_path / "state", clock=FakeClock(),
        )
        assert live._held() == 3
        governor.held = None  # another worker held the mutex for the whole budget
        assert live._held() == 3
        del governor
        import gc

        gc.collect()
        assert live._held() == 3  # the governor is gone: the last count stands
