"""Edge cases of the live limit: clocks, odd readings, failures and modes.

- Clocks: a record stamped ahead of this worker's clock (left over from
  before a host reboot) is started afresh at once rather than freezing the
  limit until the clock catches up; the wall clock is never read.
- Readings: missing, garbage, zero, huge, negative and wrapped values from
  every reader, and proxy answers that are slow, refused or not JSON numbers.
- Failures: a reading or record write that raises during admission admits
  (fail open) instead of failing the run; a failed wait stamp never raises.
- Over-commit: any environment value gives a factor in 1..4, capped per mode,
  and a mode that drops at run time takes the ceiling back down at once.
- The event loop never waits on the record lock or a slow reading.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    HoldReason,
    MachineSize,
    SandboxCapacity,
)
from app.agent_loop_lib.sandbox.host_reading import (
    CgroupReader,
    HostReader,
    HostSample,
    LiveMode,
    SidecarReader,
    parse_meminfo,
    parse_proc_stat_cpu,
)
from app.agent_loop_lib.sandbox.live_limit import CLOCK_SLACK_S, LiveLimit, _shared_cpu
from app.agent_loop_lib.sandbox.live_record import LiveRecord
from app.agent_loop_lib.sandbox.live_settings import (
    ENV_MEMORY_OVERCOMMIT,
    MAX_MEMORY_OVERCOMMIT,
    LiveSettings,
)
from tests.unit.agent_loop_lib.sandbox.live_fakes import (
    FakeClock,
    FakeReader,
    capacity,
    sample,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

GiB = 2**30
MiB = 2**20


def _live(
    tmp_path: Path, clock: FakeClock, reader: object, *, mode: str = "on:host",
    profile: str = "host", cap: SandboxCapacity | None = None, settings: LiveSettings | None = None,
) -> LiveLimit:
    live_mode = LiveMode(mode=mode, reader=reader, profile=profile)  # type: ignore[arg-type]
    live = LiveLimit(
        cap or capacity(), None, settings=settings or LiveSettings(), resolver=lambda: live_mode,
        directory=tmp_path / "state", clock=clock,
    )
    live.apply_mode(live_mode)
    return live


def _helm_capacity(memory: int = 16 * GiB, cpu_millis: int = 16000) -> SandboxCapacity:
    settings = CapacitySettings(host_memory_bytes=memory, host_cpu_millis=cpu_millis, memory_share=0.9)
    cap = SandboxCapacity("helm_dind", settings, slot_files=256)
    cap._apply(MachineSize(memory, cpu_millis / 1000, None, "helm_env"), None)
    return cap


# -- clocks -----------------------------------------------------------------


class TestClockJumps:
    def _shrunk_with_stamp(self, tmp_path: Path, clock: FakeClock, ahead_s: float) -> tuple[LiveLimit, FakeReader]:
        reader = FakeReader(sample(4.0))
        live = _live(tmp_path, clock, reader)
        live.sample_once()
        with live._ledger.transaction(0.1) as txn:
            assert txn is not None
            rec = txn.read()
            assert rec is not None
            # A record written before the host rebooted, by a clock that had
            # run further than this one has since.
            txn.write(replace(
                rec, limit=2, stamp=clock.now + ahead_s, last_step_at=clock.now + ahead_s,
                cooldown_until=clock.now + ahead_s + 60,
            ))
        return live, reader

    def test_a_record_stamped_ahead_of_the_clock_is_started_afresh(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live, reader = self._shrunk_with_stamp(tmp_path, clock, ahead_s=60.0)
        reader.sample = sample(12.0)
        clock.now += 15
        rec = live.sample_once()
        assert rec is not None and rec.rule == "start" and rec.limit == 6
        assert rec.stamp == clock.now and rec.cooldown_until <= clock.now

    def test_a_stamp_within_the_slack_is_not_a_reboot(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live, _ = self._shrunk_with_stamp(tmp_path, clock, ahead_s=CLOCK_SLACK_S / 2)
        assert live.sample_once() is None
        rec = live._ledger.read()
        assert rec is not None and rec.limit == 2

    def test_another_worker_stepping_during_a_slow_read_does_not_reset(self, tmp_path: Path) -> None:
        clock = FakeClock()
        other = _live(tmp_path, clock, FakeReader(sample(4.0)))
        other.sample_once()
        clock.now += 15

        class SlowReader(FakeReader):
            def read(self) -> HostSample:
                # The reading takes 5 s, and the other worker steps meanwhile.
                clock.now += 5
                other.sample_once()
                return super().read()

        slow = _live(tmp_path, clock, SlowReader(sample(4.0)))
        before = other._ledger.read()
        assert slow.sample_once() is None
        after = slow._ledger.read()
        assert before is not None and after is not None
        assert after.last_step_at == clock.now and after.rule != "start"

    def test_a_record_from_long_ago_is_started_afresh(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live, reader = self._shrunk_with_stamp(tmp_path, clock, ahead_s=-500.0)
        reader.sample = sample(12.0)
        rec = live.sample_once()
        assert rec is not None and rec.rule == "start" and rec.limit == 6

    async def test_wall_clock_jumps_change_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def run(directory: Path) -> list[LiveRecord | None]:
            clock = FakeClock()
            reader = FakeReader(sample(4.0))
            live = _live(directory, clock, reader)
            out = [live.sample_once()]
            for avail in (2.0, 1.0, 6.0):
                clock.now += 15
                reader.sample = sample(avail)
                live.waiting(HoldReason.SLOTS_FULL, 1.0)
                out.append(live.sample_once())
            return out

        steady = run(tmp_path / "a")
        jumps = iter([0.0, 4e9, -1e9, 1e12] * 1000)
        real = time.time
        monkeypatch.setattr(time, "time", lambda: next(jumps, real()))
        assert run(tmp_path / "b") == steady


# -- readings ---------------------------------------------------------------


def _proc(tmp_path: Path, meminfo: str | None, stat: str | None, *, pressure: dict[str, str] | None = None) -> Path:
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    for name, text in (("meminfo", meminfo), ("stat", stat)):
        path = proc / name
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(text)
    if pressure is not None:
        (proc / "pressure").mkdir(exist_ok=True)
        for name, text in pressure.items():
            (proc / "pressure" / name).write_text(text)
    return proc


class TestHostReadings:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("MemTotal: 100 kB\nMemAvailable: 400 kB\n", (100 * 1024, 400 * 1024)),
            ("MemTotal: 0 kB\nMemAvailable: 0 kB\n", (0, 0)),
            ("MemTotal: abc kB\nMemAvailable:\n", (None, None)),
            ("MemTotal: 1e5 kB\n", (None, None)),
            ("MemTotal 100 kB\n", (None, None)),
            ("\x00\xff garbage", (None, None)),
            ("MemTotal: 99999999999999999999 kB\nMemAvailable: -5 kB\n", (99999999999999999999 * 1024, -5 * 1024)),
        ],
    )
    def test_meminfo_never_raises(self, text: str, expected: tuple) -> None:
        assert parse_meminfo(text) == expected

    @pytest.mark.parametrize("text", ["cpu 1 2 3\n", "cpu a b c d e\n", "", "cpu0 1 2 3 4 5\n", "intr 5\n"])
    def test_proc_stat_junk_is_none(self, text: str) -> None:
        assert parse_proc_stat_cpu(text) is None

    def test_odd_meminfo_reaches_the_step_and_stays_in_range(self, tmp_path: Path) -> None:
        clock = FakeClock()
        for meminfo in (
            "MemTotal: 16777216 kB\nMemAvailable: 99999999 kB\n",  # available above total
            "MemTotal: 16777216 kB\nMemAvailable: -1 kB\n",  # negative
            "MemTotal: 0 kB\nMemAvailable: 0 kB\n",  # zero
            "MemTotal: 999999999999999 kB\nMemAvailable: 999999999999999 kB\n",  # huge
        ):
            proc = _proc(tmp_path, meminfo, "cpu 1 0 1 10 0 0 0 0\n")
            live = _live(tmp_path, clock, HostReader(proc))
            clock.now += 200
            rec = live.sample_once()
            assert rec is not None and 2 <= rec.limit <= 6, meminfo

    def test_counter_wrap_and_missing_pressure_give_no_cpu_reading(self, tmp_path: Path) -> None:
        reader = HostReader(_proc(tmp_path, "MemTotal: 100 kB\nMemAvailable: 50 kB\n", "cpu 1000 0 1000 5000 0 0 0 0\n"))
        reader.read()
        _proc(tmp_path, "MemTotal: 100 kB\nMemAvailable: 50 kB\n", "cpu 10 0 10 50 0 0 0 0\n")
        got = reader.read()
        assert got.cpu_util is None and got.cpu_psi_some_avg10 is None and got.mem_psi_full_avg10 is None
        assert got.mem_available == 50 * 1024

    def test_garbage_psi_reads_as_none(self, tmp_path: Path) -> None:
        reader = HostReader(_proc(
            tmp_path, "MemTotal: 100 kB\nMemAvailable: 50 kB\n", None,
            pressure={"cpu": "some avg10=abc avg60=1\n", "memory": "full\n"},
        ))
        got = reader.read()
        assert got.cpu_psi_some_avg10 is None and got.mem_psi_full_avg10 is None and got.cpu_counters is None

    def test_a_container_without_proc_reads_nothing(self, tmp_path: Path) -> None:
        reader = HostReader(tmp_path / "missing")
        assert reader.memory() == (None, None)
        assert reader.read() == HostSample(source="host")

    def test_shared_cpu_ignores_counters_that_went_back(self) -> None:
        own = HostSample(mem_total=1, mem_available=1, cpu_util=0.3, cpu_counters=(100, 1000), source="host")
        rec = LiveRecord(limit=3, cpu_busy=500, cpu_total=5000)
        assert _shared_cpu(own, rec) == own
        rec = LiveRecord(limit=3, cpu_busy=50, cpu_total=900)
        assert _shared_cpu(own, rec).cpu_util == pytest.approx(0.5)
        # Busy grew more than total (garbage): capped at 100 %.
        rec = LiveRecord(limit=3, cpu_busy=0, cpu_total=990)
        assert _shared_cpu(own, rec).cpu_util == 1.0


class _Snap:
    def __init__(self, limit: int | None, used: int | None, cpu: float | None = None) -> None:
        self.mem_limit_bytes, self.mem_working_set_raw_bytes = limit, used
        self.cpu_utilisation, self.cpu_pressure = cpu, None


class TestCgroupReadings:
    @pytest.mark.parametrize(
        ("limit", "used", "expected"),
        [
            (None, 5, (None, None)),  # no limit and no host fallback
            (10, None, (None, 10)),
            (10, 25, (0, 10)),  # working set above the limit
            (10, 4, (6, 10)),
            (0, 0, (0, 0)),
        ],
    )
    def test_memory_of_any_snapshot(self, limit: int | None, used: int | None, expected: tuple) -> None:
        reader = CgroupReader(lambda: type("P", (), {"snapshot": lambda self: _Snap(limit, used)})())
        assert reader.memory() == expected

    def test_a_raising_probe_reads_nothing(self) -> None:
        def boom(self: object) -> None:
            raise OSError("cgroup gone")

        reader = CgroupReader(lambda: type("P", (), {"snapshot": boom})())
        assert reader.memory() == (None, None)
        assert reader.read() == HostSample(source="cgroup")


def _sidecar_get(body: object, status: int = 200, *, delay: float = 0.0, exc: Exception | None = None) -> Callable:
    def get(path: str, timeout: float) -> tuple[int, Any]:
        if delay:
            time.sleep(delay)
        if exc is not None:
            raise exc
        return status, body

    return get


class TestSidecarReadings:
    @pytest.mark.parametrize(
        "block",
        [
            {"memLimitBytes": float("nan"), "memWorkingSetBytes": 1},
            {"memLimitBytes": float("inf"), "memWorkingSetBytes": 1},
            {"memLimitBytes": 16 * GiB, "memWorkingSetBytes": float("-inf")},
            {"memLimitBytes": 16 * GiB, "memWorkingSetBytes": 1, "oomEventsDelta": float("nan")},
            {"memLimitBytes": "16G", "memWorkingSetBytes": True},
            {"memLimitBytes": [1], "memWorkingSetBytes": {"a": 1}},
        ],
    )
    def test_non_numbers_from_the_proxy_never_raise(self, block: dict) -> None:
        reader = SidecarReader(_sidecar_get({"sidecar": block}), "sidecar", memory_bytes=16 * GiB, cpu_millis=1000)
        got = reader.read()
        assert got.source == "sidecar"
        assert got.mem_available is None or got.mem_total is not None
        reader.memory()

    def test_non_numbers_in_container_stats_never_raise(self) -> None:
        for value in (float("nan"), float("inf"), "x", None):
            body = {"containers": {"memWorkingSetBytes": value, "cpuCores": value, "partial": False}}
            reader = SidecarReader(_sidecar_get(body), "sidecar-containers", memory_bytes=16 * GiB, cpu_millis=1000)
            got = reader.read()
            assert got.mem_available is None and got.cpu_util is None

    @pytest.mark.parametrize(
        "get",
        [
            _sidecar_get({"message": "forbidden"}, 403),
            _sidecar_get(None, 200),
            _sidecar_get(["not", "a", "dict"], 200),
            _sidecar_get({"sidecar": None, "sidecarError": "stale"}, 200),
            _sidecar_get({"sidecar": "garbage"}, 200),
            _sidecar_get({}, 500),
            _sidecar_get({}, exc=TimeoutError("timed out")),
            _sidecar_get({}, exc=ValueError("bad json")),
        ],
    )
    def test_refused_stale_or_garbage_answers_are_an_empty_sample(self, get: Callable) -> None:
        reader = SidecarReader(get, "sidecar", memory_bytes=16 * GiB, cpu_millis=1000)
        assert reader.read() == HostSample(source="sidecar")
        assert reader.memory() == (None, None)

    def test_partial_container_stats_are_an_empty_sample(self) -> None:
        body = {"containers": {"memWorkingSetBytes": 5, "cpuCores": 1.0, "partial": True}}
        reader = SidecarReader(_sidecar_get(body), "sidecar-containers", memory_bytes=16 * GiB, cpu_millis=1000)
        assert reader.read() == HostSample(source="sidecar-containers")

    def test_a_working_set_above_the_limit_is_zero_free(self) -> None:
        body = {"sidecar": {"memLimitBytes": 4 * GiB, "memWorkingSetBytes": 9 * GiB}}
        reader = SidecarReader(_sidecar_get(body), "sidecar", memory_bytes=4 * GiB, cpu_millis=1000)
        assert reader.memory() == (0, 4 * GiB)

    def test_a_garbage_sidecar_reading_holds_the_limit(self, tmp_path: Path) -> None:
        clock = FakeClock()
        body: dict[str, Any] = {"sidecar": {"memLimitBytes": 16 * GiB, "memWorkingSetBytes": 2 * GiB}}
        reader = SidecarReader(lambda path, timeout: (200, body), "sidecar", memory_bytes=16 * GiB, cpu_millis=16000)
        live = _live(tmp_path, clock, reader, mode="on:sidecar", profile="sidecar", cap=_helm_capacity())
        first = live.sample_once()
        assert first is not None
        body["sidecar"] = {"memLimitBytes": float("nan"), "memWorkingSetBytes": 2 * GiB}
        clock.now += 15
        held = live.sample_once()
        assert held is not None and held.rule == "unknown" and held.limit == first.limit


# -- failures during admission ------------------------------------------------


class TestAdmitFailsOpen:
    async def test_a_raising_reader_admits(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        class Boom(FakeReader):
            def memory(self) -> tuple[int | None, int | None]:
                raise RuntimeError("reader bug")

        live = _live(tmp_path, FakeClock(), Boom(sample(4.0)))
        assert await live.admit_and_charge(held=0) is None
        assert await live.admit_and_charge(held=0) is None
        assert caplog.text.count("per-run memory check failed") == 1

    async def test_a_failed_record_write_admits(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        live = _live(tmp_path, FakeClock(), FakeReader(sample(4.0)))

        def no_space(*args: object) -> int:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "pwrite", no_space)
        assert await live.admit_and_charge(held=0) is None

    async def test_a_floor_reading_from_garbage_json_admits(self, tmp_path: Path) -> None:
        body = {"sidecar": {"memLimitBytes": float("nan"), "memWorkingSetBytes": 1}}
        reader = SidecarReader(lambda path, timeout: (200, body), "sidecar", memory_bytes=16 * GiB, cpu_millis=16000)
        live = _live(tmp_path, FakeClock(), reader, mode="on:sidecar", profile="sidecar", cap=_helm_capacity())
        assert await live.admit_and_charge(held=0) is None

    def test_a_failed_wait_stamp_does_not_raise(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(4.0)))
        live.sample_once()

        def eio(*args: object) -> int:
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(os, "pwrite", eio)
        live.waiting(HoldReason.LIVE_MEMORY, 5.0)
        monkeypatch.undo()
        # Not stamped, so the next poll tries again.
        live.waiting(HoldReason.LIVE_MEMORY, 6.0)
        rec = live._ledger.read()
        assert rec is not None and rec.last_wait_limit_at == clock.now


# -- over-commit --------------------------------------------------------------


_MODES = [
    ("compose", "on:host", MAX_MEMORY_OVERCOMMIT),
    ("helm_dind", "on:sidecar", 2.0),
    ("helm_dind", "on:sidecar-containers", 1.0),
    ("local", "on:cgroup", 1.0),
    ("compose", "off:unverified", 1.0),
]


class TestOvercommit:
    @settings(max_examples=300, deadline=None)
    @given(st.one_of(
        st.text(max_size=12),
        st.floats(allow_nan=True, allow_infinity=True).map(repr),
        st.integers(-(10**6), 10**6).map(str),
        st.sampled_from(["nan", "inf", "-inf", "1e309", " 2.5 ", "0x10", "1_5", "", "  "]),
    ))
    def test_any_value_gives_a_factor_from_one_to_four(self, raw: str) -> None:
        k = LiveSettings.from_env({ENV_MEMORY_OVERCOMMIT: raw}).memory_overcommit
        assert 1.0 <= k <= MAX_MEMORY_OVERCOMMIT

    @pytest.mark.parametrize(("cap_mode", "mode", "cap"), _MODES)
    @pytest.mark.parametrize("wanted", [1.0, 1.5, 2.0, 3.0, 4.0])
    def test_each_mode_caps_the_factor(self, tmp_path: Path, cap_mode: str, mode: str, cap: float, wanted: float) -> None:
        cpu_capacity = _helm_capacity() if cap_mode == "helm_dind" else capacity()
        cpu_capacity.mode = cap_mode  # type: ignore[assignment]
        reader = None if mode.startswith("off") else FakeReader(sample(4.0))
        live = _live(
            tmp_path, FakeClock(), reader, mode=mode, profile=mode.split(":")[1] if reader else "host",
            cap=cpu_capacity, settings=LiveSettings(memory_overcommit=wanted),
        )
        assert live._overcommit == min(wanted, cap)

    def test_a_mode_downgrade_takes_the_ceiling_down_at_once(self, tmp_path: Path) -> None:
        clock = FakeClock()
        cap = _helm_capacity(4 * GiB, 64000)
        side = FakeReader(HostSample(mem_total=4 * GiB, mem_available=3900 * MiB, source="sidecar"), source="sidecar")
        live = _live(
            tmp_path, clock, side, mode="on:sidecar", profile="sidecar", cap=cap,
            settings=LiveSettings(memory_overcommit=2.0),
        )
        high = live.sample_once()
        assert high is not None and high.ceiling == live._ceiling(2.0) > live._ceiling(1.0)
        assert high.limit > live._ceiling(1.0)
        # The proxy loses its cgroup reading; the next resolve gives container stats.
        stats = FakeReader(HostSample(mem_total=3600 * MiB, mem_available=3500 * MiB, source="sidecar-containers"),
                           source="sidecar-containers")
        live.apply_mode(LiveMode(mode="on:sidecar-containers", reader=stats, profile="sidecar"))
        clock.now += 0.5
        view = live.view()
        assert live._overcommit == 1.0
        assert view.ceiling == live._ceiling(1.0) and view.limit <= view.ceiling
        clock.now += 15
        rec = live.sample_once()
        assert rec is not None and rec.ceiling == live._ceiling(1.0) and rec.limit <= rec.ceiling

    def test_turning_off_at_run_time_is_the_fixed_ceiling(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(2.0)))
        live.sample_once()
        assert live.view(fresh=True).limit == 2
        live.apply_mode(LiveMode.off("unverified", "lost the proxy", retry=True))
        view = live.view()
        assert view.limit == view.ceiling == 6 and view.rule == "off"
        assert live.sample_once() is None


# -- the event loop -----------------------------------------------------------


def loop_blocking_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every call made on the event loop's thread that may block it: a record-lock try with a budget, a slot count
    with a budget, or a sleep. The loop's own lag would also hold every host stall a busy gate adds (WALLCLOCK-1);
    these are the calls a regression would put on the loop, recorded wherever they are made."""
    import threading

    from app.agent_loop_lib.sandbox.live_record import LiveLedger
    from app.services.resource_governor.shared_slots import (
        LocalTaggedSlots,
        SharedSlots,
    )

    loop_thread = threading.get_ident()
    found: list[str] = []
    transaction = LiveLedger.transaction

    def tried(self: LiveLedger, lock_budget: float = 0.020) -> object:
        if threading.get_ident() == loop_thread and lock_budget > 0:
            found.append(f"LiveLedger.transaction({lock_budget})")
        return transaction(self, lock_budget)

    monkeypatch.setattr(LiveLedger, "transaction", tried)
    for cls in (SharedSlots, LocalTaggedSlots):
        for name in ("held_count", "held_by_tag"):
            real = getattr(cls, name, None)
            if real is None:
                continue

            def counted(self: object, budget: float | None = None, *, _real: Callable[..., object] = real, _name: str = f"{cls.__name__}.{name}") -> object:
                if threading.get_ident() == loop_thread and (budget is None or budget > 0):
                    found.append(f"{_name}({budget})")
                return _real(self) if budget is None else _real(self, budget)

            monkeypatch.setattr(cls, name, counted)
    sleep = time.sleep

    def slept(seconds: float) -> None:
        if threading.get_ident() == loop_thread and seconds > 0:
            found.append(f"time.sleep({seconds})")
        sleep(seconds)

    monkeypatch.setattr(time, "sleep", slept)
    return found


async def _exercise(work: Callable[[], Any], seconds: float) -> None:
    """Run *work* on the loop, again and again, for about *seconds*."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        result = work()
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(0)


class TestEventLoop:
    @pytest.fixture
    def held_lock(self, tmp_path: Path) -> Iterator[Callable[[], None]]:
        import fcntl

        def hold() -> int:
            fd = os.open(tmp_path / "state" / "live.lock", os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX)
            return fd

        fds: list[int] = []
        yield lambda: fds.append(hold())
        for fd in fds:
            os.close(fd)

    async def test_view_wait_and_admit_never_block_on_a_held_lock(
        self, tmp_path: Path, held_lock: Callable[[], None], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(4.0)))
        live.sample_once()
        held_lock()

        def on_loop() -> None:
            clock.now += 1.5
            live.view(fresh=True)
            live.waiting(HoldReason.SLOTS_FULL, 2.0)
            live.waiting(HoldReason.HOST_MEMORY_FLOOR, 2.0)

        blocking = loop_blocking_calls(monkeypatch)
        await _exercise(on_loop, 0.3)
        # Admission waits up to its 50 ms lock budget, in a thread, then admits.
        admitted: list[object] = []

        async def admit() -> None:
            admitted.append(await live.admit_and_charge(held=0))

        await _exercise(admit, 0.3)
        assert admitted and set(admitted) == {None}
        assert blocking == []

    async def test_a_slow_reading_never_blocks_the_loop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class Slow(FakeReader):
            def memory(self) -> tuple[int | None, int | None]:
                time.sleep(0.2)
                return super().memory()

            def read(self) -> HostSample:
                time.sleep(0.2)
                return super().read()

        clock = FakeClock()
        live = _live(tmp_path, clock, Slow(sample(4.0)))
        live._settings = LiveSettings(interval_s=5.0)

        async def admit_and_sample() -> None:
            clock.now += 10
            await asyncio.gather(live.admit_and_charge(held=0), asyncio.to_thread(live.sample_once))

        blocking = loop_blocking_calls(monkeypatch)
        await _exercise(admit_and_sample, 0.5)
        assert blocking == []

    async def test_the_sampler_task_keeps_the_loop_free(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class Slow(FakeReader):
            def read(self) -> HostSample:
                time.sleep(0.1)
                return super().read()

        live = _live(tmp_path, FakeClock(), Slow(sample(4.0)), settings=LiveSettings(interval_s=5.0))
        # One pass of the sampler: ensure_sized, resolve, sample, view, sleep.
        live._settings = replace(live._settings, interval_s=0.0)  # type: ignore[call-arg]
        blocking = loop_blocking_calls(monkeypatch)
        live.start()
        try:
            await _exercise(lambda: None, 0.4)
            assert blocking == []
        finally:
            await live.stop()
