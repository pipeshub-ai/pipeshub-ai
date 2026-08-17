"""Tests for the cross-platform probe chain (plan section 5).

cgroup/proc paths are module-level ``Path`` constants read fresh on every
call, so pointing them at ``tmp_path`` fixtures via monkeypatch exercises the
real parsing logic without touching the actual host's cgroup.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import probe as probe_mod
from app.services.resource_governor.policy import MEM_HARD, MEM_SOFT
from app.services.resource_governor.probe import BaselineMemoryTracker, SystemResourceProbe

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_probe_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets an empty fake cgroup root by default; individual
    tests populate the files they need.

    This module specifically exercises the Linux cgroup/proc probe paths,
    so ``sys.platform`` is pinned to "linux" for the duration of each test
    regardless of the host running the suite (macOS/Windows dev machines
    included) — otherwise these paths are silently skipped in favour of the
    macOS/Windows branches.
    """
    monkeypatch.setattr(probe_mod.sys, "platform", "linux")
    monkeypatch.setattr(probe_mod, "_CGROUP_ROOT", tmp_path)
    monkeypatch.setattr(probe_mod, "_PROC_SELF_CGROUP", tmp_path / "no_such_cgroup_file")
    monkeypatch.setattr(probe_mod, "_PROC_MEMINFO", tmp_path / "no_such_meminfo_file")
    monkeypatch.setattr(probe_mod, "_PROC_STAT", tmp_path / "no_such_stat_file")
    # A stray override on the host/CI running this suite must never leak
    # into tests that assume auto-calibration (the default) is in effect.
    monkeypatch.delenv(probe_mod._BASELINE_MEMORY_ENV_VAR, raising=False)
    return tmp_path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestCgroupV2Memory:
    def test_reads_limit_and_working_set(self, tmp_path: Path) -> None:
        _write(tmp_path / "memory.max", "8589934592")  # 8 GiB
        _write(tmp_path / "memory.current", "1073741824")  # 1 GiB
        _write(tmp_path / "memory.stat", "inactive_file 104857600\nother_field 1\n")

        limit, working_set, source = probe_mod._resolve_memory()

        assert source == "cgroup_v2"
        assert limit == 8589934592
        assert working_set == 1073741824 - 104857600

    def test_max_sentinel_falls_through(self, tmp_path: Path) -> None:
        _write(tmp_path / "memory.max", "max")
        _write(tmp_path / "memory.current", "1073741824")

        limit, working_set, source = probe_mod._resolve_memory()

        # No cgroup limit anywhere -> falls all the way through to whatever
        # host-level source is available in this environment (not cgroup_v2).
        assert source != "cgroup_v2"


class TestCgroupV1Memory:
    def test_reads_limit_and_working_set(self, tmp_path: Path) -> None:
        _write(tmp_path / "memory" / "memory.limit_in_bytes", "4294967296")  # 4 GiB
        _write(tmp_path / "memory" / "memory.usage_in_bytes", "2147483648")  # 2 GiB
        _write(tmp_path / "memory" / "memory.stat", "total_inactive_file 52428800\n")

        limit, working_set, source = probe_mod._resolve_memory()

        assert source == "cgroup_v1"
        assert limit == 4294967296
        assert working_set == 2147483648 - 52428800

    def test_no_limit_sentinel_treated_as_unlimited(self, tmp_path: Path) -> None:
        _write(tmp_path / "memory" / "memory.limit_in_bytes", str(9223372036854771712))
        _write(tmp_path / "memory" / "memory.usage_in_bytes", "2147483648")

        limit, working_set, source = probe_mod._resolve_memory()

        assert source != "cgroup_v1"


class TestProcMeminfoFallback:
    def test_used_when_no_cgroup_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\n")
        monkeypatch.setattr(probe_mod, "_PROC_MEMINFO", meminfo)

        limit, working_set, source = probe_mod._resolve_memory()

        assert source == "proc_meminfo"
        assert limit == 16384000 * 1024
        assert working_set == (16384000 - 8192000) * 1024


class TestAllReadsFail:
    def test_returns_none_fields_not_an_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(probe_mod, "psutil", None)
        monkeypatch.setattr(probe_mod, "_sysconf_memory_total", lambda: None)

        limit, working_set, source = probe_mod._resolve_memory()

        # On Linux with nothing else available this legitimately resolves to
        # unknown; the important property is it never raises.
        assert source in {"unknown", "proc_meminfo", "psutil", "sysconf"}
        if source == "unknown":
            assert limit is None
            assert working_set is None


class TestCgroupV2CpuQuota:
    def test_parses_quota_over_period(self, tmp_path: Path) -> None:
        _write(tmp_path / "cpu.max", "400000 100000")
        assert probe_mod._resolve_cpu_quota() == 4.0

    def test_max_quota_falls_through_to_affinity(self, tmp_path: Path) -> None:
        _write(tmp_path / "cpu.max", "max 100000")
        quota = probe_mod._resolve_cpu_quota()
        assert quota > 0


class TestCgroupV1CpuQuota:
    def test_parses_quota_over_period(self, tmp_path: Path) -> None:
        _write(tmp_path / "cpu" / "cpu.cfs_quota_us", "200000")
        _write(tmp_path / "cpu" / "cpu.cfs_period_us", "100000")
        assert probe_mod._resolve_cpu_quota() == 2.0


class TestCpuUsageUsec:
    def test_cgroup_v2_usage_usec(self, tmp_path: Path) -> None:
        _write(tmp_path / "cpu.stat", "usage_usec 5000000\nuser_usec 4000000\nsystem_usec 1000000\n")
        usage, source = probe_mod._resolve_cpu_usage_usec()
        assert usage == 5000000
        assert source == "cgroup_v2"

    def test_cgroup_v1_usage_from_nanoseconds(self, tmp_path: Path) -> None:
        _write(tmp_path / "cpuacct" / "cpuacct.usage", "5000000000")  # 5s in ns
        usage, source = probe_mod._resolve_cpu_usage_usec()
        assert usage == 5_000_000
        assert source == "cgroup_v1"

    def test_falls_back_to_os_times_when_nothing_else_present(self) -> None:
        usage, source = probe_mod._resolve_cpu_usage_usec()
        assert usage is not None
        assert source in {"os_times", "proc_stat", "psutil"}

    def test_container_scoped_quota_does_not_pair_with_host_wide_proc_stat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When usage falls back to host-wide /proc/stat (cgroup v1 cpuacct
        missing), utilisation is divided by the host CPU count, never by this
        container's quota — pairing the two would report an idle container as
        pegged on a large host."""
        _write(tmp_path / "cpu" / "cpu.cfs_quota_us", "200000")
        _write(tmp_path / "cpu" / "cpu.cfs_period_us", "100000")
        proc_stat = tmp_path / "proc_stat"
        _write(proc_stat, "cpu  100 0 0 0 0 0 0 0 0 0\n")
        monkeypatch.setattr(probe_mod, "_PROC_STAT", proc_stat)
        monkeypatch.setattr(probe_mod, "_clock_ticks_per_second", lambda: 100.0)
        monkeypatch.setattr(probe_mod, "_host_cpu_count", lambda: 8)

        assert probe_mod._resolve_cpu_quota() == 2.0
        usage, source = probe_mod._resolve_cpu_usage_usec()
        assert source == "proc_stat"
        assert usage == 1_000_000

        times = iter([0.0, 1.0])
        sut = SystemResourceProbe(clock=lambda: next(times))
        first = sut.snapshot()
        assert first.cpu_utilisation is None
        assert first.cpu_quota == 2.0

        # +100 jiffies in 1s at USER_HZ=100 is 1s of CPU time.
        # Host-wide: 1.0 / (1s * 8 cpus) = 0.125
        # If wrongly divided by the container quota of 2: 0.5
        _write(proc_stat, "cpu  200 0 0 0 0 0 0 0 0 0\n")
        second = sut.snapshot()
        assert second.cpu_utilisation == pytest.approx(0.125, rel=1e-6)


class TestNestedCgroupfs:
    def test_resolves_via_proc_self_cgroup_v2(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cgroup_self = tmp_path / "self_cgroup"
        cgroup_self.write_text("0::/docker/abc123\n")
        monkeypatch.setattr(probe_mod, "_PROC_SELF_CGROUP", cgroup_self)
        _write(tmp_path / "docker" / "abc123" / "memory.max", "1073741824")
        _write(tmp_path / "docker" / "abc123" / "memory.current", "536870912")

        limit, working_set, source = probe_mod._resolve_memory()

        assert source == "cgroup_v2"
        assert limit == 1073741824

    def test_resolves_via_proc_self_cgroup_v1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cgroup_self = tmp_path / "self_cgroup"
        cgroup_self.write_text("5:memory:/docker/abc123\n")
        monkeypatch.setattr(probe_mod, "_PROC_SELF_CGROUP", cgroup_self)
        _write(tmp_path / "memory" / "docker" / "abc123" / "memory.limit_in_bytes", "1073741824")
        _write(tmp_path / "memory" / "docker" / "abc123" / "memory.usage_in_bytes", "536870912")

        limit, working_set, source = probe_mod._resolve_memory()

        assert source == "cgroup_v1"
        assert limit == 1073741824


class TestSystemResourceProbeUtilisation:
    def test_utilisation_is_none_on_first_sample(self, tmp_path: Path) -> None:
        _write(tmp_path / "cpu.max", "400000 100000")
        _write(tmp_path / "cpu.stat", "usage_usec 1000000\n")
        clock = iter([0.0])
        sut = SystemResourceProbe(clock=lambda: next(clock))
        snap = sut.snapshot()
        assert snap.cpu_utilisation is None

    def test_utilisation_from_counter_delta_over_two_samples(self, tmp_path: Path) -> None:
        cpu_max = tmp_path / "cpu.max"
        cpu_stat = tmp_path / "cpu.stat"
        _write(cpu_max, "400000 100000")  # quota = 4 cores
        _write(cpu_stat, "usage_usec 1000000\n")

        times = iter([0.0, 5.0])
        sut = SystemResourceProbe(clock=lambda: next(times))

        first = sut.snapshot()
        assert first.cpu_utilisation is None

        # 5s elapsed, +10,000,000 usec of usage on a 4-core quota:
        # utilisation = 10s-of-cpu-time / (5s * 4 cores) = 0.5
        _write(cpu_stat, "usage_usec 11000000\n")
        second = sut.snapshot()
        assert second.cpu_utilisation == pytest.approx(0.5, rel=1e-6)

    def test_snapshot_never_raises_even_if_probe_internals_explode(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _boom() -> float:
            raise RuntimeError("clock is broken")

        sut = SystemResourceProbe(clock=_boom)
        snap = sut.snapshot()
        assert snap.source == "error"
        assert snap.mem_limit_bytes is None
        assert snap.cpu_utilisation is None


class TestBaselineMemoryTracker:
    """Plan: "Fix 1 — Baseline Memory Reservation". A sibling service's idle
    memory (e.g. Docling's model weights) sitting in the same cgroup must
    not permanently inflate mem_pressure past MEM_SOFT."""

    def test_explicit_override_applies_from_the_first_sample(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GOVERNOR_BASELINE_MEMORY_MB", "100")
        tracker = BaselineMemoryTracker()

        adjusted, baseline = tracker.adjust(500 * 1024 * 1024)

        assert baseline == 100 * 1024 * 1024
        assert adjusted == 400 * 1024 * 1024

    def test_explicit_override_of_zero_is_a_valid_no_baseline_choice(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GOVERNOR_BASELINE_MEMORY_MB", "0")
        tracker = BaselineMemoryTracker()

        adjusted, baseline = tracker.adjust(500 * 1024 * 1024)

        assert baseline == 0
        assert adjusted == 500 * 1024 * 1024

    def test_malformed_override_falls_back_to_auto_calibration(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GOVERNOR_BASELINE_MEMORY_MB", "not-a-number")
        tracker = BaselineMemoryTracker(calibration_samples=2)

        # First sample of a 2-sample warm-up: auto mode still returns no
        # adjustment on the sample that only seeds the low-water mark.
        adjusted, baseline = tracker.adjust(500 * 1024 * 1024)

        assert baseline is None
        assert adjusted == 500 * 1024 * 1024

    def test_auto_mode_does_not_adjust_during_the_warmup_window(self) -> None:
        # calibration_samples=3: only the first 2 readings are pure warm-up
        # (no adjustment yet) — the 3rd is where calibration completes.
        tracker = BaselineMemoryTracker(calibration_samples=3)

        for reading in (300, 250):
            adjusted, baseline = tracker.adjust(reading)
            assert baseline is None
            assert adjusted == reading

    def test_auto_mode_applies_low_water_mark_once_warmup_completes(self) -> None:
        tracker = BaselineMemoryTracker(calibration_samples=3)
        for reading in (300, 250, 400):
            tracker.adjust(reading)

        adjusted, baseline = tracker.adjust(600)

        assert baseline == 250
        assert adjusted == 350

    def test_low_water_mark_keeps_ratcheting_down_after_warmup(self) -> None:
        tracker = BaselineMemoryTracker(calibration_samples=2)
        tracker.adjust(300)
        tracker.adjust(300)  # warmup complete, baseline=300

        _, baseline_before = tracker.adjust(500)
        assert baseline_before == 300

        # A later, lower idle reading (e.g. Docling's model unloaded/GC'd)
        # should lower the baseline instead of getting stuck at the
        # calibration-window value forever.
        tracker.adjust(200)
        _, baseline_after = tracker.adjust(500)
        assert baseline_after == 200

    def test_low_water_mark_never_increases_from_a_higher_later_reading(self) -> None:
        tracker = BaselineMemoryTracker(calibration_samples=2)
        tracker.adjust(200)
        tracker.adjust(200)  # warmup complete, baseline=200

        tracker.adjust(900)  # a real workload spike, not a new baseline
        _, baseline_after_spike = tracker.adjust(500)

        assert baseline_after_spike == 200

    def test_adjust_floors_at_zero_when_baseline_exceeds_current_reading(self) -> None:
        tracker = BaselineMemoryTracker(calibration_samples=1)
        tracker.adjust(500)  # warmup, baseline=500

        adjusted, baseline = tracker.adjust(300)  # reading dropped below baseline

        assert baseline == 300  # low-water mark also ratcheted down to 300
        assert adjusted == 0

    def test_adjust_returns_none_for_unknown_working_set(self) -> None:
        tracker = BaselineMemoryTracker()

        adjusted, baseline = tracker.adjust(None)

        assert adjusted is None
        assert baseline is None

    def test_negative_override_is_treated_as_malformed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GOVERNOR_BASELINE_MEMORY_MB", "-5")
        tracker = BaselineMemoryTracker(calibration_samples=2)

        adjusted, baseline = tracker.adjust(500)

        # Falls back to auto mode (still calibrating) rather than a
        # nonsensical negative baseline.
        assert baseline is None
        assert adjusted == 500


class TestSystemResourceProbeBaselineIntegration:
    def test_snapshot_exposes_raw_and_adjusted_working_set(self, tmp_path: Path) -> None:
        _write(tmp_path / "memory.max", "8589934592")  # 8 GiB
        _write(tmp_path / "memory.current", "6000000000")

        tracker = BaselineMemoryTracker()
        tracker.adjust(6000000000)
        tracker.adjust(6000000000)
        # Third call below happens inside snapshot() itself.

        sut = SystemResourceProbe(baseline_tracker=tracker)
        snap = sut.snapshot()

        assert snap.mem_working_set_raw_bytes == 6000000000
        # The low-water mark is the entire 6 GB working set, but the probe
        # caps the baseline at half the 8 GiB limit so the pressure ratio
        # keeps a usable denominator instead of attributing every byte in
        # the cgroup to co-located services.
        assert snap.mem_baseline_bytes == 4 * 1024 ** 3
        assert snap.mem_working_set_bytes == 6000000000 - 4 * 1024 ** 3

    def test_snapshot_uses_raw_working_set_while_baseline_still_calibrating(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path / "memory.max", "8589934592")
        _write(tmp_path / "memory.current", "6000000000")

        sut = SystemResourceProbe(baseline_tracker=BaselineMemoryTracker(calibration_samples=3))
        snap = sut.snapshot()

        assert snap.mem_baseline_bytes is None
        assert snap.mem_working_set_bytes == snap.mem_working_set_raw_bytes == 6000000000

    def test_snapshot_applies_explicit_baseline_override_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GOVERNOR_BASELINE_MEMORY_MB", "3000")  # 3000 MiB
        _write(tmp_path / "memory.max", "12884901888")  # 12 GiB
        _write(tmp_path / "memory.current", "9700000000")  # ~9.03 GiB raw -> ~75% raw pressure

        sut = SystemResourceProbe()
        snap = sut.snapshot()

        raw_pressure = snap.mem_working_set_raw_bytes / snap.mem_limit_bytes
        assert raw_pressure > MEM_SOFT
        # Crediting the co-located 3000 MiB pulls the growth reading back
        # under MEM_SOFT, which is the whole point of the baseline (the
        # shrink brake reads mem_pressure_raw and still trips here — see
        # test_policy.TestBrakeUsesRawPressure)...
        assert snap.mem_pressure < MEM_SOFT
        # ...but only just: the container really is ~78% full, so the reading
        # must stay well clear of idle rather than collapsing toward zero.
        assert snap.mem_pressure > 0.65

    def test_pressure_still_reaches_one_on_a_full_cgroup_despite_a_baseline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The regression that let the all-in-one container OOM.

        Subtracting the baseline from the working set but not the limit caps
        mem_pressure at ``1 - baseline / limit``. With a 5 GiB baseline in a
        12 GiB container that ceiling is 0.58 — below both MEM_SOFT and
        MEM_HARD — so neither brake could fire however full the cgroup got.
        """
        monkeypatch.setenv("GOVERNOR_BASELINE_MEMORY_MB", "5120")  # 5 GiB
        limit = 12 * 1024 ** 3
        _write(tmp_path / "memory.max", str(limit))
        _write(tmp_path / "memory.current", str(limit))  # cgroup completely full

        snap = SystemResourceProbe().snapshot()

        assert snap.mem_pressure == pytest.approx(1.0)
        assert snap.mem_pressure > MEM_HARD

    def test_baseline_is_capped_at_half_the_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOVERNOR_BASELINE_MEMORY_MB", str(11 * 1024))  # 11 GiB of a 12 GiB limit
        limit = 12 * 1024 ** 3
        _write(tmp_path / "memory.max", str(limit))
        _write(tmp_path / "memory.current", str(limit))

        snap = SystemResourceProbe().snapshot()

        assert snap.mem_baseline_bytes == limit // 2
        assert snap.mem_usable_bytes == limit // 2
        assert snap.mem_pressure == pytest.approx(1.0)
