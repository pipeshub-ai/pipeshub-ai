"""A NUMA-pinned cell sizes from the container's CPUs, not its node's.

The pin narrows ``sched_getaffinity`` to one node. The governor splits the container's CPUs over its
cells and divides container-wide usage counters by the CPU count, so it must keep reading the count from
before the pin; otherwise each cell is sized from node-local CPUs divided by all cells, and sibling load
on other nodes inflates its utilisation.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.capacity import cgroup, numa
from app.services.resource_governor import probe

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(numa.CONTAINER_CPUS_ENV, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    monkeypatch.setattr(probe, "_cgroup_v2_cpu_quota", lambda: None)
    monkeypatch.setattr(probe, "_cgroup_v1_cpu_quota", lambda: None)
    probe._resolve_effective_cpus.cache_clear()
    yield
    probe._resolve_effective_cpus.cache_clear()


def _affinity(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(n)), raising=False)


def test_pin_records_the_count_from_before_it(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"cpus": set(range(32))}
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(state["cpus"]), raising=False)
    monkeypatch.setattr(os, "sched_setaffinity", lambda _pid, cpus: state.update(cpus=set(cpus)), raising=False)
    env: dict[str, str] = {}
    assert numa.pin(1, (tuple(range(16)), tuple(range(16, 32))), env)
    assert len(state["cpus"]) == 16 and env[numa.CONTAINER_CPUS_ENV] == "32"


def test_a_refused_pin_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_pid: int, _cpus: set[int]) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(32)), raising=False)
    monkeypatch.setattr(os, "sched_setaffinity", refuse, raising=False)
    env: dict[str, str] = {}
    assert not numa.pin(0, ((0,), (1,)), env)
    assert env == {}


def test_governor_cpu_quota_and_count_ignore_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    _affinity(monkeypatch, 16)
    monkeypatch.setenv(numa.CONTAINER_CPUS_ENV, "32")
    assert probe._resolve_cpu_quota() == 32.0
    assert probe.effective_cpu_count() == 32
    assert cgroup.cpu_affinity() == 32


def test_utilisation_is_container_wide_after_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """14 CPUs of load in a 32-CPU container is 43.75%, not 87.5% of the node's 16."""
    _affinity(monkeypatch, 16)
    monkeypatch.setenv(numa.CONTAINER_CPUS_ENV, "32")
    delta_usec, elapsed = 14 * 10 * 1_000_000, 10.0
    assert delta_usec / (elapsed * 1_000_000 * probe._resolve_cpu_quota()) == pytest.approx(0.4375)


def test_a_cpuset_without_a_pin_still_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    _affinity(monkeypatch, 4)
    assert probe._resolve_cpu_quota() == 4.0
    assert cgroup.cpu_affinity() == 4


@pytest.mark.parametrize("raw", ["", "abc", "-3", "0", "8", "4", "33", "1e9", "٣٢"], ids=lambda r: r or "empty")
def test_an_unusable_value_is_ignored(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not above the real affinity, or above the host's CPUs, or not a plain integer: use the real count."""
    _affinity(monkeypatch, 8)
    monkeypatch.setenv(numa.CONTAINER_CPUS_ENV, raw)
    assert cgroup.cpu_affinity() == 8
    assert probe._resolve_cpu_quota() == 8.0


def _snapshots(monkeypatch: pytest.MonkeyPatch, source: str, busy_cpus: float) -> float | None:
    """Utilisation read by two snapshots 10 s apart for a counter that grew by ``busy_cpus`` x 10 s."""
    usage = iter([0, int(busy_cpus * 10 * 1_000_000)])
    clock = iter([0.0, 10.0])
    monkeypatch.setattr(probe, "_resolve_cpu_usage_usec", lambda: (next(usage), source))
    monkeypatch.setattr(probe, "_resolve_memory", lambda: (None, None, "none"))
    monkeypatch.setattr(probe, "_cgroup_v2_cpu_throttled_usec", lambda: None)
    monkeypatch.setattr(probe, "_cpu_pressure", lambda: None)
    sut = probe.SystemResourceProbe(clock=lambda: next(clock))
    sut.snapshot()
    return sut.snapshot().cpu_utilisation


def test_a_pinned_process_reads_its_own_cpu_time_against_its_own_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """``os.times`` counts this process only: a cell pinned to 16 of the container's 32 CPUs that uses all
    16 is fully busy (1.0, so the brake can engage), not half busy."""
    _affinity(monkeypatch, 16)
    monkeypatch.setenv(numa.CONTAINER_CPUS_ENV, "32")
    assert probe._resolve_cpu_quota() == 32.0  # the container-wide figure is unchanged for sizing
    assert _snapshots(monkeypatch, "os_times", 16) == pytest.approx(1.0)


def test_a_cgroup_counter_stays_container_wide_after_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    _affinity(monkeypatch, 16)
    monkeypatch.setenv(numa.CONTAINER_CPUS_ENV, "32")
    assert _snapshots(monkeypatch, "cgroup_v2", 14) == pytest.approx(14 / 32)


def test_os_times_without_a_pin_keeps_the_quota_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    _affinity(monkeypatch, 32)
    monkeypatch.setattr(probe, "_cgroup_v2_cpu_quota", lambda: 8.0)
    assert _snapshots(monkeypatch, "os_times", 4) == pytest.approx(0.5)
