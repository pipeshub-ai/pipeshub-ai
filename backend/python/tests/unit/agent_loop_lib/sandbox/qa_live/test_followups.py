"""The per-run check at full run size, a damaged record, the sidecar reading
shared between admissions, and a floor that leaves no room for a run.

- Over-commit only raises the ceiling: the per-run floor check and its burst
  charge count each run at its full memory limit.
- A damaged record seen by an admission never lifts the limit to the
  ceiling: the worker keeps its last good record, or writes one at the floor
  that the next sample starts afresh. A missing record, or one an older
  version left, is still simply uninitialised.
- The sidecar's per-run reading is reused for a second, refreshed by one
  request that concurrent admissions share, and an admission waits for it at
  most 50 ms before using a recent reading or admitting.
- A hard floor that leaves no room for one run is logged once as an ERROR
  naming the variable.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import threading
import time
import zlib
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.sandbox import host_reading
from app.agent_loop_lib.sandbox.capacity import HoldReason
from app.agent_loop_lib.sandbox.host_reading import HostSample, LiveMode, SidecarReader
from app.agent_loop_lib.sandbox.live_limit import LiveLimit
from app.agent_loop_lib.sandbox.live_record import RECORD_SIZE, LiveLedger
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from tests.unit.agent_loop_lib.sandbox.live_fakes import (
    FakeClock,
    FakeReader,
    capacity,
    sample,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

GiB = 2**30
MiB = 2**20
M = 512 * MiB


def _live(tmp_path: Path, clock: FakeClock, reader: object, *, settings: LiveSettings | None = None,
          cap: object = None, mode: str = "on:host", profile: str = "host") -> LiveLimit:
    live_mode = LiveMode(mode=mode, reader=reader, profile=profile)  # type: ignore[arg-type]
    live = LiveLimit(
        cap or capacity(), None, settings=settings or LiveSettings(), resolver=lambda: live_mode,
        directory=tmp_path / "state", clock=clock,
    )
    live.apply_mode(live_mode)
    return live


class TestFullRunSize:
    async def test_overcommit_checks_and_charges_the_full_run(self, tmp_path: Path) -> None:
        # Memory-bound: 16 GiB and 16 CPUs, so 4x raises the ceiling.
        cap = capacity(16, 16)
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(1.0 + 2.5 * M / GiB)), cap=cap,
                     settings=LiveSettings(memory_overcommit=4.0))
        assert live._overcommit == 4.0 and live._ceiling(4.0) > live._ceiling(1.0)
        assert await live.admit_and_charge(held=0) is None
        assert await live.admit_and_charge(held=1) is None
        # At 1/4 of a run each, eight more would have fitted.
        assert await live.admit_and_charge(held=2) is HoldReason.HOST_MEMORY_FLOOR
        rec = live._ledger.read()
        assert rec is not None and sum(size for _, size in rec.charges) == 2 * M

    def test_the_reported_floor_is_the_same_at_any_overcommit(self, tmp_path: Path) -> None:
        floors = []
        for k in (1.0, 4.0):
            live = _live(tmp_path / str(k), FakeClock(), FakeReader(sample(8.0)), cap=capacity(16, 16),
                         settings=LiveSettings(memory_overcommit=k))
            live.sample_once()
            floors.append(live.hard_floor_bytes())
        assert floors[0] == floors[1] == GiB


def _damage(tmp_path: Path) -> None:
    path = tmp_path / "state" / "live"
    raw = bytearray(path.read_bytes())
    raw[200] ^= 0xFF
    path.write_bytes(bytes(raw))


class TestDamagedRecord:
    async def test_an_admission_keeps_this_workers_last_good_limit(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(2.0)))
        assert live.sample_once().limit == 2  # type: ignore[union-attr]
        _damage(tmp_path)
        clock.now += 2
        assert await live.admit_and_charge(held=0) is None
        other = _live(tmp_path, clock, FakeReader(sample(2.0)))
        clock.now += 2
        assert other.view(fresh=True).limit == 2
        rec = LiveLedger(tmp_path / "state").read()
        assert rec is not None and rec.limit == 2 and len(rec.charges) == 1

    async def test_without_a_good_record_it_holds_at_the_floor_until_the_next_sample(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        clock = FakeClock()
        writer = _live(tmp_path, clock, FakeReader(sample(12.0)))
        writer.sample_once()
        _damage(tmp_path)
        reader = FakeReader(sample(12.0))
        fresh = _live(tmp_path, clock, reader)  # never read a good record
        with caplog.at_level(logging.WARNING):
            assert await fresh.admit_and_charge(held=0) is None
            assert await fresh.admit_and_charge(held=1) is None
        assert caplog.text.count("shared record is damaged") == 1
        clock.now += 2
        view = fresh.view(fresh=True)
        assert view.limit == view.floor == 2 < view.ceiling
        # The next sample decides: a fresh record at the memory target, charges kept.
        rec = fresh.sample_once()
        assert rec is not None and rec.rule == "start" and rec.limit == 6 and len(rec.charges) == 2

    async def test_a_missing_record_is_still_uninitialised(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(4.0)))
        assert await live.admit_and_charge(held=0) is None
        clock.now += 2
        assert live.view(fresh=True).limit == 6

    async def test_an_older_version_is_not_damage(self, tmp_path: Path) -> None:
        (tmp_path / "state").mkdir(mode=0o700)
        raw = bytearray(128)
        struct.pack_into("<8sII", raw, 0, b"PXSBXLV\0", 1, 0)
        struct.pack_into("<8sII", raw, 0, b"PXSBXLV\0", 1, zlib.crc32(bytes(raw)))
        (tmp_path / "state" / "live").write_bytes(bytes(raw))
        (tmp_path / "state" / "live").chmod(0o600)
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(4.0)))
        assert await live.admit_and_charge(held=0) is None
        clock.now += 2
        assert live.view(fresh=True).limit == 6
        assert len((tmp_path / "state" / "live").read_bytes()) == RECORD_SIZE


class _Proxy:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0
        self.used = 2 * GiB
        self.lock = threading.Lock()

    def __call__(self, path: str, timeout: float) -> tuple[int, Any]:
        with self.lock:
            self.calls += 1
        time.sleep(self.delay)
        return 200, {"sidecar": {"memLimitBytes": 16 * GiB, "memWorkingSetBytes": self.used}}


class _Now:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _reader(proxy: _Proxy, now: _Now) -> SidecarReader:
    reader = SidecarReader(proxy, "sidecar", memory_bytes=16 * GiB, cpu_millis=16000)
    reader._clock = now
    return reader


class TestSidecarMemory:
    def test_reused_for_a_second(self) -> None:
        proxy, now = _Proxy(), _Now()
        reader = _reader(proxy, now)
        assert reader.memory() == (14 * GiB, 16 * GiB)
        now.now += 0.9
        reader.memory()
        assert proxy.calls == 1
        now.now += 0.2
        proxy.used = 3 * GiB
        assert reader.memory() == (13 * GiB, 16 * GiB) and proxy.calls == 2

    def test_concurrent_admissions_share_one_request(self) -> None:
        proxy, now = _Proxy(delay=0.03), _Now()
        reader = _reader(proxy, now)
        results: list[tuple[int | None, int | None]] = []
        threads = [threading.Thread(target=lambda: results.append(reader.memory())) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert proxy.calls == 1 and results == [(14 * GiB, 16 * GiB)] * 16

    def test_a_slow_proxy_admits_within_the_wait_then_the_answer_is_used(self) -> None:
        proxy, now = _Proxy(delay=0.3), _Now()
        reader = _reader(proxy, now)
        started = time.perf_counter()
        assert reader.memory() == (None, None)
        assert time.perf_counter() - started < 0.2
        assert reader.memory() == (None, None) and proxy.calls == 1  # joined, not a second request
        time.sleep(0.4)
        assert reader.memory() == (14 * GiB, 16 * GiB) and proxy.calls == 1

    def test_a_recent_reading_is_used_while_a_slow_refresh_runs(self) -> None:
        proxy, now = _Proxy(), _Now()
        reader = _reader(proxy, now)
        reader.memory()
        proxy.delay = 0.3
        now.now += 3.0
        started = time.perf_counter()
        assert reader.memory() == (14 * GiB, 16 * GiB)
        assert time.perf_counter() - started < 0.2
        now.now += 3.0  # 6 s old, no answer yet: admit
        assert reader.memory() == (None, None)
        time.sleep(0.4)

    def test_a_failing_proxy_is_none_and_retried(self) -> None:
        calls = []

        def down(path: str, timeout: float) -> tuple[int, Any]:
            calls.append(path)
            raise TimeoutError

        now = _Now()
        reader = SidecarReader(down, "sidecar", memory_bytes=16 * GiB, cpu_millis=16000)
        reader._clock = now
        assert reader.memory() == (None, None)
        time.sleep(0.05)
        assert reader.memory() == (None, None)
        assert len(calls) == 2

    async def test_admission_with_a_slow_proxy_admits_in_time_and_checks_later(self, tmp_path: Path) -> None:
        from tests.unit.agent_loop_lib.sandbox.qa_live.test_live_edges import (
            _helm_capacity,
        )

        proxy = _Proxy(delay=0.3)
        reader = SidecarReader(proxy, "sidecar", memory_bytes=16 * GiB, cpu_millis=16000)
        clock = FakeClock()
        live = _live(tmp_path, clock, reader, cap=_helm_capacity(), mode="on:sidecar", profile="sidecar")
        started = time.perf_counter()
        results = await asyncio.gather(*(live.admit_and_charge(held=0) for _ in range(8)))
        assert results == [None] * 8 and time.perf_counter() - started < 0.25
        assert proxy.calls == 1
        await asyncio.sleep(0.4)
        # The shared answer is in: the next admission is checked and charged.
        assert await live.admit_and_charge(held=0) is None
        rec = live._ledger.read()
        assert rec is not None and len(rec.charges) == 1 and proxy.calls == 1


class TestFloorAboveMemory:
    def test_an_impossible_floor_is_one_error_naming_the_variable(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(12.0)), settings=LiveSettings(host_mem_floor_mb=16384))
        with caplog.at_level(logging.ERROR):
            live.sample_once()
            clock.now += 15
            live.sample_once()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "SANDBOX_HOST_MEM_FLOOR_MB=16384" in errors[0].getMessage()
        assert "16.0GiB" in errors[0].getMessage()

    def test_a_derived_floor_on_a_tiny_host_is_named_as_such(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        reader = FakeReader(HostSample(mem_total=GiB, mem_available=GiB // 2, source="host"))
        live = _live(tmp_path, FakeClock(), reader)
        with caplog.at_level(logging.ERROR):
            live.sample_once()
        assert "the hard free-memory floor" in caplog.text

    def test_a_sensible_floor_logs_nothing(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        live = _live(tmp_path, FakeClock(), FakeReader(sample(12.0)), settings=LiveSettings(host_mem_floor_mb=4096))
        with caplog.at_level(logging.ERROR):
            live.sample_once()
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_module_wait_is_within_the_admission_budget() -> None:
    from app.agent_loop_lib.sandbox.live_limit import ADMIT_LOCK_BUDGET

    assert host_reading._SIDECAR_MEMORY_WAIT_S <= ADMIT_LOCK_BUDGET
    assert host_reading._SIDECAR_MEMORY_FRESH_S <= 1.0
