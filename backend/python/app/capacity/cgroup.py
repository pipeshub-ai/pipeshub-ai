"""Readers of the container's CPU and memory limits (cgroup v2, v1, /proc, sysfs).

Every read is best effort: a missing file or a malformed value gives ``None`` and never raises.
Standard library only. The resource governor's ``probe`` module reads the same files for its
live samples; ``tests/unit/capacity/test_facts_sources.py`` checks that both answer the same on
the same trees.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
PROC_MEMINFO = Path("/proc/meminfo")
NUMA_ROOT = Path("/sys/devices/system/node")
# Some kernels report this for an unlimited v1 memory limit (2^63 - 4096).
MEM_V1_NO_LIMIT_SENTINEL = 1 << 62


def read_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def read_int(path: Path | None) -> int | None:
    text = read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def resolve_path(v1_controller: str, v1_filename: str, v2_filename: str | None = None) -> Path | None:
    """A cgroup file under either version, including a host-mounted cgroupfs."""
    v2_name = v2_filename or v1_filename
    direct_v2 = CGROUP_ROOT / v2_name
    if direct_v2.exists():
        return direct_v2
    direct_v1 = CGROUP_ROOT / v1_controller / v1_filename
    if direct_v1.exists():
        return direct_v1
    cgroup_self = read_text(PROC_SELF_CGROUP)
    if not cgroup_self:
        return None
    for line in cgroup_self.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        _hier_id, controllers, rel_path = parts
        rel = rel_path.lstrip("/")
        if controllers == "":  # cgroup v2 line: "0::/path"
            candidate = CGROUP_ROOT / rel / v2_name
            if candidate.exists():
                return candidate
        elif v1_controller in controllers.split(","):
            candidate = CGROUP_ROOT / v1_controller / rel / v1_filename
            if candidate.exists():
                return candidate
    return None


def cpu_quota_v2() -> float | None:
    text = read_text(resolve_path("cpu", "cpu.max"))
    if not text:
        return None
    parts = text.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota_us, period_us = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if period_us <= 0 or quota_us <= 0:
        return None
    return quota_us / period_us


def cpu_quota_v1() -> float | None:
    quota = read_int(resolve_path("cpu", "cpu.cfs_quota_us"))
    period = read_int(resolve_path("cpu", "cpu.cfs_period_us"))
    if quota is None or period is None or quota <= 0 or period <= 0:
        return None
    return quota / period


def cpu_quota() -> tuple[float | None, str]:
    """``(quota in CPUs, "v2" | "v1" | "none")``; ``None`` is no CPU limit."""
    quota = cpu_quota_v2()
    if quota is not None:
        return quota, "v2"
    quota = cpu_quota_v1()
    if quota is not None:
        return quota, "v1"
    return None, "none"


def cpu_affinity() -> int | None:
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None


def memory_limit_v2() -> int | None:
    raw = read_text(resolve_path("memory", "memory.max"))
    if raw is None or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def memory_limit_v1() -> int | None:
    limit = read_int(resolve_path("memory", "memory.limit_in_bytes"))
    if limit is None or limit >= MEM_V1_NO_LIMIT_SENTINEL:
        return None
    return limit


def memory_limit() -> tuple[int | None, str]:
    """``(limit in bytes, "v2" | "v1" | "none")``; ``None`` is no memory limit."""
    limit = memory_limit_v2()
    if limit is not None:
        return limit, "v2"
    limit = memory_limit_v1()
    if limit is not None:
        return limit, "v1"
    return None, "none"


def swap_limit(mem_limit: int | None) -> int | None:
    """The swap the cgroup may use: v2 ``memory.swap.max``, or v1 ``memsw - limit``."""
    v2 = CGROUP_ROOT / "memory.swap.max"
    raw = read_text(v2) if v2.exists() else None
    if raw is not None:
        if raw == "max":
            return None
        try:
            return int(raw)
        except ValueError:
            return None
    memsw = read_int(resolve_path("memory", "memory.memsw.limit_in_bytes", "memory.memsw.limit_in_bytes"))
    if memsw is None or memsw >= MEM_V1_NO_LIMIT_SENTINEL or mem_limit is None:
        return None
    return max(0, memsw - mem_limit)


def host_memory() -> int | None:
    """``MemTotal`` of ``/proc/meminfo`` in bytes."""
    text = read_text(PROC_MEMINFO)
    if text:
        match = re.search(r"^MemTotal:\s+(\d+)\s*kB", text, re.MULTILINE)
        if match:
            return int(match.group(1)) * 1024
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def memory_domain_id() -> str | None:
    """An opaque id shared by every process under this memory cgroup and by no other.

    The same value as ``resource_governor.probe.memory_domain_id``: the cgroup directory's device
    and inode with the kernel boot id. ``None`` where either cannot be read.
    """
    boot_id = read_text(BOOT_ID)
    limit_file = resolve_path("memory", "memory.limit_in_bytes", "memory.max")
    if not boot_id or limit_file is None:
        return None
    try:
        directory = limit_file.parent.stat()
    except OSError:
        return None
    raw = f"{boot_id.strip()}:{directory.st_dev}:{directory.st_ino}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _parse_cpulist(text: str) -> set[int]:
    cpus: set[int] = set()
    for raw in text.split(","):
        part = raw.strip()
        if not part:
            continue
        low, _, high = part.partition("-")
        try:
            cpus.update(range(int(low), int(high or low) + 1))
        except ValueError:
            return set()
    return cpus


def numa_nodes() -> tuple[tuple[int, tuple[int, ...]], ...]:
    """``(node, cpus)`` pairs, each node's CPUs limited to this process's affinity."""
    try:
        allowed = set(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        allowed = None
    nodes: list[tuple[int, tuple[int, ...]]] = []
    try:
        entries = sorted(NUMA_ROOT.glob("node[0-9]*"))
    except OSError:
        return ()
    for entry in entries:
        text = read_text(entry / "cpulist")
        if not text:
            continue
        cpus = _parse_cpulist(text)
        if allowed is not None:
            cpus &= allowed
        if cpus:
            nodes.append((int(entry.name[4:]), tuple(sorted(cpus))))
    return tuple(nodes)
