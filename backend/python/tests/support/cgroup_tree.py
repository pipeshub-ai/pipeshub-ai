"""Fixture cgroup, ``/proc`` and sysfs trees for the capacity facts readers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.capacity import cgroup

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def build_tree(
    root: Path,
    *,
    version: str = "v2",
    cpu_max: str | None = None,
    cpu_v1: tuple[str, str] | None = None,
    memory_max: str | None = None,
    swap_max: str | None = None,
    memsw: str | None = None,
    meminfo_kb: int | None = 8 * 1024 * 1024,
    boot_id: str = "boot-1",
    numa: dict[int, str] | None = None,
) -> None:
    """Write the files one container would see. ``version`` is ``v1`` or ``v2``."""
    cg = root / "cgroup"
    cg.mkdir(parents=True, exist_ok=True)
    if version == "v2":
        if cpu_max is not None:
            (cg / "cpu.max").write_text(cpu_max)
        if memory_max is not None:
            (cg / "memory.max").write_text(memory_max)
        if swap_max is not None:
            (cg / "memory.swap.max").write_text(swap_max)
    else:
        if cpu_v1 is not None:
            (cg / "cpu").mkdir(exist_ok=True)
            (cg / "cpu" / "cpu.cfs_quota_us").write_text(cpu_v1[0])
            (cg / "cpu" / "cpu.cfs_period_us").write_text(cpu_v1[1])
        if memory_max is not None:
            (cg / "memory").mkdir(exist_ok=True)
            (cg / "memory" / "memory.limit_in_bytes").write_text(memory_max)
        if memsw is not None:
            (cg / "memory").mkdir(exist_ok=True)
            (cg / "memory" / "memory.memsw.limit_in_bytes").write_text(memsw)
    if meminfo_kb is not None:
        (root / "meminfo").write_text(f"MemTotal:       {meminfo_kb} kB\nMemAvailable:   1 kB\n")
    (root / "boot_id").write_text(boot_id + "\n")
    for node, cpulist in (numa or {}).items():
        d = root / "node" / f"node{node}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "cpulist").write_text(cpulist + "\n")


def point_readers_at(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(cgroup, "CGROUP_ROOT", root / "cgroup")
    monkeypatch.setattr(cgroup, "PROC_SELF_CGROUP", root / "no_self_cgroup")
    monkeypatch.setattr(cgroup, "PROC_MEMINFO", root / "meminfo")
    monkeypatch.setattr(cgroup, "BOOT_ID", root / "boot_id")
    monkeypatch.setattr(cgroup, "NUMA_ROOT", root / "node")
