"""The probe takes the tightest CPU and memory limit on its cgroup and every visible ancestor (v1 and v2).

A cap on a parent slice binds every process below it. The probe reads synthetic cgroup trees under a temporary
root, and ``app.capacity.cgroup`` (the capacity planner's reader) must answer the same on each tree. Where an
ancestor's limit is the binding one, the working set and CPU counter are read at that level too: pairing a parent's
cap with the member's own use would read the brake too far from the cap.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.capacity import cgroup
from app.services.resource_governor import probe

if TYPE_CHECKING:
    from pathlib import Path

GIB = 2**30
UNLIMITED_V1 = str(2**63 - 4096)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _point(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, membership: str) -> Path:
    root = tmp_path / "cgroup"
    root.mkdir(exist_ok=True)
    (tmp_path / "self_cgroup").write_text(membership)
    for module, root_name, self_name in ((cgroup, "CGROUP_ROOT", "PROC_SELF_CGROUP"), (probe, "_CGROUP_ROOT", "_PROC_SELF_CGROUP")):
        monkeypatch.setattr(module, root_name, root)
        monkeypatch.setattr(module, self_name, tmp_path / "self_cgroup")
    return root


def _v2_tree(root: Path, *, member_cpu: str, parent_cpu: str, member_mem: str, parent_mem: str) -> None:
    _write(root / "batch" / "service" / "cpu.max", member_cpu)
    _write(root / "batch" / "service" / "memory.max", member_mem)
    _write(root / "batch" / "service" / "memory.current", str(1 * GIB))
    _write(root / "batch" / "service" / "memory.stat", "file 0\n")
    _write(root / "batch" / "service" / "cpu.stat", "usage_usec 1000\nthrottled_usec 10\n")
    _write(root / "batch" / "cpu.max", parent_cpu)
    _write(root / "batch" / "memory.max", parent_mem)
    _write(root / "batch" / "memory.current", str(3 * GIB))
    _write(root / "batch" / "memory.stat", f"file {GIB // 2}\n")
    _write(root / "batch" / "cpu.stat", "usage_usec 9000\nthrottled_usec 90\n")


def test_v2_parent_cap_binds_an_unlimited_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "0::/batch/service\n")
    _v2_tree(root, member_cpu="max 100000", parent_cpu="200000 100000", member_mem="max", parent_mem=str(4 * GIB))
    assert probe._cgroup_v2_cpu_quota() == 2.0
    limit, working_set = probe._cgroup_v2_memory()
    assert limit == 4 * GIB
    assert working_set == 3 * GIB - GIB // 2  # the parent's own use less its cache, not the member's 1 GiB
    assert probe._cgroup_v2_cpu_stat_field("usage_usec") == 1000  # the member's own counter stays the member's
    anc = probe._ancestor_cpu()  # the counters the 2-CPU cap is shared by
    assert anc is not None and (anc.quota, anc.usage_usec, anc.throttled_usec) == (2.0, 9000, 90)
    assert (cgroup.cpu_quota(), cgroup.memory_limit()) == ((2.0, "v2"), (4 * GIB, "v2"))


def test_v2_tightest_level_wins_whichever_holds_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "0::/batch/service\n")
    _v2_tree(root, member_cpu="100000 100000", parent_cpu="800000 100000", member_mem=str(2 * GIB), parent_mem=str(8 * GIB))
    assert probe._cgroup_v2_cpu_quota() == 1.0
    limit, working_set = probe._cgroup_v2_memory()
    assert (limit, working_set) == (2 * GIB, GIB)  # the member's own tighter limit and use
    assert probe._ancestor_cpu() is None  # the member's own level binds
    assert (cgroup.cpu_quota()[0], cgroup.memory_limit()[0]) == (1.0, 2 * GIB)


def test_v2_equal_limits_keep_the_members_own_readings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "0::/batch/service\n")
    _v2_tree(root, member_cpu="200000 100000", parent_cpu="200000 100000", member_mem=str(4 * GIB), parent_mem=str(4 * GIB))
    assert probe._cgroup_v2_memory() == (4 * GIB, GIB)
    assert probe._ancestor_cpu() is None


def test_v2_a_grandparent_cap_is_found_through_unlimited_levels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "0::/a/b/c\n")
    for name in ("a/b/c", "a/b"):
        _write(root / name / "cpu.max", "max 100000")
        _write(root / name / "memory.max", "max")
    _write(root / "a" / "cpu.max", "300000 100000")
    _write(root / "a" / "memory.max", str(6 * GIB))
    assert probe._cgroup_v2_cpu_quota() == 3.0
    assert probe._cgroup_v2_memory()[0] == 6 * GIB
    assert (cgroup.cpu_quota()[0], cgroup.memory_limit()[0]) == (3.0, 6 * GIB)


def test_v2_an_ancestor_without_usage_files_falls_back_to_the_members_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "0::/batch/service\n")
    _v2_tree(root, member_cpu="max 100000", parent_cpu="200000 100000", member_mem="max", parent_mem=str(4 * GIB))
    (root / "batch" / "memory.current").unlink()
    (root / "batch" / "cpu.stat").unlink()
    assert probe._cgroup_v2_memory() == (4 * GIB, GIB)
    assert probe._ancestor_cpu() is None  # no readable ancestor counter: the member's own stays the only one
    assert probe._cgroup_v2_cpu_stat_field("usage_usec") == 1000


def test_v2_an_unreadable_ancestor_counter_leaves_the_members_counter_in_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "0::/batch/service\n")
    _v2_tree(root, member_cpu="max 100000", parent_cpu="200000 100000", member_mem="max", parent_mem=str(4 * GIB))
    (root / "batch" / "cpu.stat").write_text("garbage\n")
    assert probe._ancestor_cpu() is None
    assert probe._cgroup_v2_cpu_stat_field("usage_usec") == 1000 and probe._cgroup_v2_cpu_throttled_usec() == 10


def test_v2_no_limit_on_any_level_is_still_no_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "0::/x/y\n")
    for name in ("x/y", "x"):
        _write(root / name / "cpu.max", "max 100000")
        _write(root / name / "memory.max", "max")
    assert probe._cgroup_v2_cpu_quota() is None and probe._cgroup_v2_memory() == (None, None)
    assert (cgroup.cpu_quota()[0], cgroup.memory_limit()[0]) == (None, None)


def test_v2_only_the_members_own_cgroup_mounted_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "0::/\n")
    _write(root / "cpu.max", "300000 100000")
    _write(root / "memory.max", str(3 * GIB))
    _write(root / "memory.current", str(GIB))
    _write(root / "memory.stat", "file 0\n")
    assert probe._cgroup_v2_cpu_quota() == 3.0
    assert probe._cgroup_v2_memory() == (3 * GIB, GIB)


def _v1_tree(root: Path, *, member_cpu: str, parent_cpu: str, member_mem: str, parent_mem: str) -> None:
    mem, cpu = root / "memory", root / "cpu"
    _write(mem / "jobs" / "a" / "memory.limit_in_bytes", member_mem)
    _write(mem / "jobs" / "a" / "memory.usage_in_bytes", str(1 * GIB))
    _write(mem / "jobs" / "a" / "memory.stat", "total_cache 0\n")
    _write(mem / "jobs" / "memory.limit_in_bytes", parent_mem)
    _write(mem / "jobs" / "memory.usage_in_bytes", str(3 * GIB))
    _write(mem / "jobs" / "memory.stat", f"total_cache {GIB // 2}\n")
    _write(mem / "memory.limit_in_bytes", UNLIMITED_V1)
    for name, quota, usage in (("a", member_cpu, 1_000_000), ("", parent_cpu, 9_000_000)):
        _write(cpu / "jobs" / name / "cpu.cfs_quota_us", quota)
        _write(cpu / "jobs" / name / "cpu.cfs_period_us", "100000")
        _write(cpu / "jobs" / name / "cpuacct.usage", str(usage))
        _write(root / "cpuacct" / "jobs" / name / "cpuacct.usage", str(usage))  # the same directory on a co-mounted host


def test_v1_parent_cap_binds_an_unlimited_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "4:memory:/jobs/a\n3:cpu,cpuacct:/jobs/a\n")
    _v1_tree(root, member_cpu="-1", parent_cpu="300000", member_mem=UNLIMITED_V1, parent_mem=str(6 * GIB))
    assert probe._cgroup_v1_cpu_quota() == 3.0
    assert probe._cgroup_v1_memory() == (6 * GIB, 3 * GIB - GIB // 2)
    anc = probe._ancestor_cpu()
    assert anc is not None and (anc.quota, anc.usage_usec) == (3.0, 9000)  # nanoseconds read as microseconds
    assert (cgroup.cpu_quota(), cgroup.memory_limit()) == ((3.0, "v1"), (6 * GIB, "v1"))


def test_v1_the_members_tighter_limit_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "4:memory:/jobs/a\n3:cpu,cpuacct:/jobs/a\n")
    _v1_tree(root, member_cpu="100000", parent_cpu="300000", member_mem=str(2 * GIB), parent_mem=str(6 * GIB))
    assert probe._cgroup_v1_cpu_quota() == 1.0
    assert probe._cgroup_v1_memory() == (2 * GIB, GIB)
    assert probe._ancestor_cpu() is None
    assert probe._cgroup_v1_cpu_usage_usec() == 1000


def test_v1_no_limit_on_any_level_is_still_no_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _point(monkeypatch, tmp_path, "4:memory:/jobs/a\n3:cpu,cpuacct:/jobs/a\n")
    _v1_tree(root, member_cpu="-1", parent_cpu="-1", member_mem=UNLIMITED_V1, parent_mem=UNLIMITED_V1)
    assert probe._cgroup_v1_cpu_quota() is None and probe._cgroup_v1_memory() == (None, None)


def _sampler(monkeypatch: pytest.MonkeyPatch, now: list[float]) -> probe.SystemResourceProbe:
    monkeypatch.setattr(probe.sys, "platform", "linux")
    monkeypatch.setattr(probe, "_affinity_cpu_count", lambda: 4)
    return probe.SystemResourceProbe(clock=lambda: now[0])


def test_a_busy_sibling_under_the_parents_cap_is_seen_and_the_members_own_saturation_still_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent cap 8 CPUs, member affinity 4. The parent's counter divides by 8, the member's by 4: four CPUs of sibling
    load read 0.5 (not 1.0 from the parent's counter over the member's 4), and the member busy on all its 4 CPUs reads 1.0."""
    root = _point(monkeypatch, tmp_path, "0::/batch/service\n")
    _v2_tree(root, member_cpu="max 100000", parent_cpu="800000 100000", member_mem="max", parent_mem=str(4 * GIB))
    now = [0.0]
    sampler = _sampler(monkeypatch, now)
    write = lambda member, parent: (  # noqa: E731
        _write(root / "batch" / "service" / "cpu.stat", f"usage_usec {member}\nthrottled_usec 0\n"),
        _write(root / "batch" / "cpu.stat", f"usage_usec {parent}\nthrottled_usec 0\n"),
    )
    write(0, 0)
    assert sampler.snapshot().cpu_utilisation is None
    now[0] = 10.0
    write(0, 40_000_000)  # siblings burned 4 CPUs for 10 s; the member idle
    assert sampler.snapshot().cpu_utilisation == pytest.approx(0.5)
    now[0] = 20.0
    write(40_000_000, 80_000_000)  # now the member burns its 4 CPUs
    assert sampler.snapshot().cpu_utilisation == pytest.approx(1.0)


def test_a_change_of_binding_ancestor_between_samples_gives_no_ancestor_delta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``/a`` (2 CPUs) binds, then its cap is lifted and ``/a/b`` (4 CPUs) binds: the two counters are not comparable, so
    that sample reads nothing from them (not 60 CPUs of work that never ran), and the next one is normal again."""
    root = _point(monkeypatch, tmp_path, "0::/a/b/c\n")
    for name, cpu, stat in (("a/b/c", "max 100000", "usage_usec 0\n"), ("a/b", "400000 100000", "usage_usec 100000000\n"),
                            ("a", "200000 100000", "usage_usec 1000000\n")):
        _write(root / name / "cpu.max", cpu)
        _write(root / name / "cpu.stat", stat)
        _write(root / name / "memory.max", "max")
    now = [0.0]
    sampler = _sampler(monkeypatch, now)
    sampler.snapshot()
    now[0] = 15.0
    _write(root / "a" / "cpu.stat", "usage_usec 16000000\n")  # 1 CPU busy for 15 s under the 2-CPU cap
    assert sampler.snapshot().cpu_utilisation == pytest.approx(0.5)
    _write(root / "a" / "cpu.max", "max 100000")  # the cap moves down to /a/b
    now[0] = 30.0
    moved = sampler.snapshot()
    assert moved.cpu_utilisation == 0.0  # the member's own counter did not move; no ancestor figure from the other scope
    now[0] = 45.0
    _write(root / "a" / "b" / "cpu.stat", "usage_usec 160000000\n")  # 60 CPU-s in 15 s under a 4-CPU cap
    assert sampler.snapshot().cpu_utilisation == pytest.approx(1.0)
