"""Cumulative-counter CPU utilisation (plan section 4.3): a short burst
within a sample window must be visible, unlike psutil.cpu_percent()/
getloadavg() which alias it away. Cross-checks that cgroup v2, cgroup v1 and
the os.times() fallback all agree on the same synthetic counter deltas.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import probe as probe_mod
from app.services.resource_governor.probe import SystemResourceProbe

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(probe_mod.sys, "platform", "linux")
    monkeypatch.setattr(probe_mod, "_CGROUP_ROOT", tmp_path)
    monkeypatch.setattr(probe_mod, "_PROC_SELF_CGROUP", tmp_path / "missing")
    monkeypatch.setattr(probe_mod, "_PROC_STAT", tmp_path / "missing_stat")
    return tmp_path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestCounterDeltaAgreement:
    """A 5s window with exactly 2 CPU-seconds of usage on a 1-core quota
    (40% utilisation) must read the same regardless of which counter
    source supplied the cumulative usage."""

    def test_cgroup_v2_and_v1_agree_on_the_same_delta(self, tmp_path: Path) -> None:
        cpu_max = tmp_path / "cpu.max"
        cpu_stat = tmp_path / "cpu.stat"
        _write(cpu_max, "100000 100000")  # quota = 1 core
        _write(cpu_stat, "usage_usec 1000000\n")
        times = iter([0.0, 5.0])
        v2_probe = SystemResourceProbe(clock=lambda: next(times))
        v2_probe.snapshot()
        _write(cpu_stat, "usage_usec 3000000\n")  # +2,000,000 usec = 2 CPU-seconds
        v2_result = v2_probe.snapshot()

        cpu_stat.unlink()
        cpu_usage = tmp_path / "cpuacct" / "cpuacct.usage"
        _write(cpu_usage, "1000000000")  # 1s in ns
        times_v1 = iter([0.0, 5.0])
        v1_probe = SystemResourceProbe(clock=lambda: next(times_v1))
        v1_probe.snapshot()
        _write(cpu_usage, "3000000000")  # +2,000,000,000 ns = 2 CPU-seconds
        v1_result = v1_probe.snapshot()

        assert v2_result.cpu_utilisation == pytest.approx(0.4, rel=1e-6)
        assert v1_result.cpu_utilisation == pytest.approx(0.4, rel=1e-6)
        assert v2_result.cpu_utilisation == pytest.approx(v1_result.cpu_utilisation, rel=1e-6)

    def test_short_burst_is_visible_unlike_a_point_sample(self, tmp_path: Path) -> None:
        """A burst that consumes 90% of one core for the *entire* 5s window
        must show ~0.9 utilisation from counter deltas -- the failure mode
        this replaces (psutil.cpu_percent()/getloadavg() instantaneous
        reads or a 1-minute moving average) would either miss a burst
        entirely or lag it by a minute."""
        cpu_max = tmp_path / "cpu.max"
        cpu_stat = tmp_path / "cpu.stat"
        _write(cpu_max, "100000 100000")
        _write(cpu_stat, "usage_usec 0\n")
        times = iter([0.0, 5.0])
        sut = SystemResourceProbe(clock=lambda: next(times))
        sut.snapshot()

        _write(cpu_stat, "usage_usec 4500000\n")  # 4.5 CPU-seconds over 5s wall
        result = sut.snapshot()

        assert result.cpu_utilisation == pytest.approx(0.9, rel=1e-6)


class TestThrottling:
    def test_throttled_ratio_from_counter_delta(self, tmp_path: Path) -> None:
        cpu_max = tmp_path / "cpu.max"
        cpu_stat = tmp_path / "cpu.stat"
        _write(cpu_max, "100000 100000")
        _write(cpu_stat, "usage_usec 0\nthrottled_usec 0\n")
        times = iter([0.0, 5.0])
        sut = SystemResourceProbe(clock=lambda: next(times))
        sut.snapshot()

        _write(cpu_stat, "usage_usec 1000000\nthrottled_usec 2000000\n")  # 2s throttled / 5s window = 0.4
        result = sut.snapshot()

        assert result.cpu_throttled_ratio == pytest.approx(0.4, rel=1e-6)
