"""``app.capacity.cgroup`` agrees with the governor's probe on odd cgroup shapes.

Shapes the implementer's fixture lacks: fractional 1.5 and v1 0.5 quotas, a cpu.max with no period,
memory.max "max" with a CPU limit, a host-mounted cgroupfs resolved through /proc/self/cgroup (v2 and
v1), and a cpuset narrower than the quota (the plan's budget vs the governor's ``_resolve_cpu_quota``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.capacity import cgroup
from app.capacity.policy import plan_capacity
from app.capacity.sources import Cgroup
from app.services.resource_governor import probe

if TYPE_CHECKING:
    from pathlib import Path

GIB = 2**30


def _write(root: Path, rel: str, text: str) -> None:
    p = root / "cgroup" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


SHAPES = {
    "v2_1.5": ({"cpu.max": "150000 100000", "memory.max": str(3 * GIB)}, None),
    "v2_0.5_mem_max": ({"cpu.max": "50000 100000", "memory.max": "max"}, None),
    "v2_no_period": ({"cpu.max": "200000", "memory.max": str(GIB)}, None),
    "v2_max_only": ({"cpu.max": "max", "memory.max": str(GIB)}, None),
    "v2_odd_period": ({"cpu.max": "250000 333333", "memory.max": str(GIB)}, None),
    "v1_0.5": ({"cpu/cpu.cfs_quota_us": "50000", "cpu/cpu.cfs_period_us": "100000",
                "memory/memory.limit_in_bytes": str(2 * GIB)}, None),
    "v1_no_limit": ({"cpu/cpu.cfs_quota_us": "-1", "cpu/cpu.cfs_period_us": "100000",
                     "memory/memory.limit_in_bytes": "9223372036854771712"}, None),
    "v2_host_mounted": ({"kubepods/pod1/c1/cpu.max": "150000 100000", "kubepods/pod1/c1/memory.max": str(5 * GIB)},
                        "0::/kubepods/pod1/c1\n"),
    "v1_host_mounted": ({"cpu,cpuacct/docker/abc/cpu.cfs_quota_us": "250000",
                         "cpu,cpuacct/docker/abc/cpu.cfs_period_us": "100000",
                         "memory/docker/abc/memory.limit_in_bytes": str(7 * GIB)},
                        "12:memory:/docker/abc\n4:cpu,cpuacct:/docker/abc\n"),
    "v1_host_mounted_cpu_dir": ({"cpu/docker/abc/cpu.cfs_quota_us": "250000",
                                 "cpu/docker/abc/cpu.cfs_period_us": "100000",
                                 "memory/docker/abc/memory.limit_in_bytes": str(7 * GIB)},
                                "12:memory:/docker/abc\n4:cpu,cpuacct:/docker/abc\n"),
}


def _point(monkeypatch: pytest.MonkeyPatch, root: Path, self_cgroup: str | None, affinity: int) -> None:
    (root / "boot_id").write_text("boot\n")
    (root / "meminfo").write_text("MemTotal:       8388608 kB\n")
    sc = root / "self_cgroup"
    if self_cgroup is not None:
        sc.write_text(self_cgroup)
    for mod, prefix in ((cgroup, ""), (probe, "_")):
        monkeypatch.setattr(mod, f"{prefix}CGROUP_ROOT", root / "cgroup")
        monkeypatch.setattr(mod, f"{prefix}PROC_SELF_CGROUP", sc)
        monkeypatch.setattr(mod, f"{prefix}BOOT_ID", root / "boot_id")
        monkeypatch.setattr(mod, f"{prefix}PROC_MEMINFO", root / "meminfo")
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(affinity)))
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    _clear()


def _clear() -> None:
    for fn in (probe._cgroup_v2_memory, probe.memory_domain_id, probe._resolve_effective_cpus):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.mark.parametrize("affinity", [64, 1])
@pytest.mark.parametrize("name", SHAPES)
def test_readers_agree(name: str, affinity: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files, self_cgroup = SHAPES[name]
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    _point(monkeypatch, tmp_path, self_cgroup, affinity)
    try:
        gov_quota = probe._cgroup_v2_cpu_quota()
        if gov_quota is None:
            gov_quota = probe._cgroup_v1_cpu_quota()
        assert cgroup.cpu_quota()[0] == gov_quota
        gov_mem = probe._cgroup_v2_memory()[0] or probe._cgroup_v1_memory()[0]
        assert cgroup.memory_limit()[0] == gov_mem
        assert cgroup.memory_domain_id() == probe.memory_domain_id()
        facts = Cgroup({}, probe_gpu=False).read()
        plan = plan_capacity(facts, {})
        # The plan's CPU budget is the governor's CPU quota (fractions kept, cpuset wins).
        assert plan.budget.cpus == pytest.approx(probe._resolve_cpu_quota()), (name, plan.budget, facts)
    finally:
        _clear()
