"""Tier planning for ``run_tiers_e2e.sh``: pick idle cores, check a tier fits, refuse a busy host.

Pure functions plus a small CLI, so the runner's decisions are unit-testable without Docker.

``plan`` prints one line per tier (or ``SKIP <tier>: <why>``) in a stable format; ``--dry-run`` of the
runner prints exactly that and starts nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BUSY_LIMIT_PCT = 85.0
SIBLINGS_STANDARD = {"neo4j": 4, "qdrant": 4, "redis": 1, "mongo": 2}  # 11 CPUs
SIBLINGS_KNEE = {"neo4j": 6, "qdrant": 6, "redis": 2, "mongo": 2}  # 16 CPUs (B-06 knee)
FAKE_AI_CPUS = 1
AUX_CPUS = 1  # mailpit and minio, together, on a cpuset of their own
HOST_RESERVE_CPUS = 4  # left for the host, the bench client and other sessions
GB_PER_CPU = 4


@dataclass(frozen=True)
class Tier:
    name: str
    cpus: int
    mem_gb: int


def parse_tiers(text: str) -> list[Tier]:
    """``"T4:4:16 T16:16:64"`` -> tiers. A malformed entry is an error, never skipped."""
    tiers = []
    for item in text.split():
        parts = item.split(":")
        if len(parts) != 3 or not (parts[1].isdigit() and parts[2].isdigit()) or int(parts[1]) < 1:
            raise ValueError(f"bad tier {item!r}; expected NAME:CPUS:MEM_GB")
        tiers.append(Tier(parts[0], int(parts[1]), int(parts[2])))
    if not tiers:
        raise ValueError("no tiers given")
    names = [t.name for t in tiers]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate tier name in {names}: each tier needs its own, it names its result files")
    return tiers


def parse_proc_stat(text: str) -> dict[int, tuple[int, int]]:
    """``cpuN`` lines -> ``{n: (idle, total)}`` jiffies."""
    out: dict[int, tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].startswith("cpu") and parts[0][3:].isdigit():
            nums = [int(x) for x in parts[1:]]
            out[int(parts[0][3:])] = (nums[3] + (nums[4] if len(nums) > 4 else 0), sum(nums[:8]))
    return out


def idle_order(before: dict[int, tuple[int, int]], after: dict[int, tuple[int, int]]) -> list[int]:
    """CPU numbers, idlest first, from two ``/proc/stat`` snapshots (ties by number)."""
    def idle_share(n: int) -> float:
        d_total = after[n][1] - before[n][1]
        return (after[n][0] - before[n][0]) / d_total if d_total > 0 else 0.0

    return sorted((n for n in before if n in after), key=lambda n: (-idle_share(n), n))


def cpuset_string(cpus: list[int]) -> str:
    """``[0,1,2,5]`` -> ``"0-2,5"``."""
    ordered = sorted(cpus)
    ranges: list[str] = []
    start = prev = None
    for n in ordered:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = n
    if start is not None:
        ranges.append(f"{start}-{prev}" if prev != start else str(start))
    return ",".join(ranges)


def assign_cpusets(
    order: list[int], app_cpus: int, siblings: dict[str, int], fake_ai: int = FAKE_AI_CPUS, aux: int = AUX_CPUS
) -> dict[str, str]:
    """Disjoint cpusets, taken from the idlest cores: the app first, then each sibling, the fake provider and
    the auxiliary containers (mailpit, minio). Every container of the stack has one: nothing floats."""
    wanted = [("app", app_cpus), *siblings.items(), ("fake_ai", fake_ai), ("aux", aux)]
    need = sum(n for _, n in wanted)
    if need > len(order):
        raise ValueError(f"need {need} idle cores, only {len(order)} to choose from")
    sets: dict[str, str] = {}
    at = 0
    for name, n in wanted:
        sets[name] = cpuset_string(order[at:at + n])
        at += n
    return sets


def fit(tier: Tier, host_cpus: int, host_mem_gb: float, siblings: dict[str, int]) -> tuple[bool, str]:
    """Does the tier plus its siblings fit on this host, leaving ``HOST_RESERVE_CPUS`` for everything else?"""
    need = tier.cpus + sum(siblings.values()) + FAKE_AI_CPUS + AUX_CPUS
    if need > host_cpus - HOST_RESERVE_CPUS:
        return False, f"needs {need} CPUs, host has {host_cpus} with {HOST_RESERVE_CPUS} kept free"
    mem_need = tier.mem_gb + 12  # siblings and the fake provider
    if mem_need > host_mem_gb * 0.7:
        return False, f"needs about {mem_need} GB, host has {host_mem_gb:g} GB (70% usable)"
    if tier.mem_gb < tier.cpus * 2:
        return False, f"{tier.mem_gb} GB is under 2 GB per CPU"
    return True, "fits"


def busy(cpu_pct: float, mem_pct: float, limit: float = BUSY_LIMIT_PCT) -> str | None:
    """A reason to refuse to start, or None."""
    if cpu_pct >= limit:
        return f"host CPU {cpu_pct:.0f}% >= {limit:.0f}%"
    if mem_pct >= limit:
        return f"host memory {mem_pct:.0f}% >= {limit:.0f}%"
    return None


def wait_until_quiet(read: object, retries: int, sleep_s: float, sleep=time.sleep) -> str | None:
    """Poll ``read() -> (cpu_pct, mem_pct)``; None once quiet, else the last reason after *retries* tries."""
    reason = None
    for attempt in range(retries):
        reason = busy(*read())  # type: ignore[operator]
        if reason is None:
            return None
        if attempt < retries - 1:
            sleep(sleep_s)
    return reason


def host_usage() -> tuple[float, float]:
    """Host CPU % over one second and memory %, from ``/proc``."""
    a = parse_proc_stat(Path("/proc/stat").read_text())
    time.sleep(1.0)
    b = parse_proc_stat(Path("/proc/stat").read_text())
    idle = sum(b[n][0] - a[n][0] for n in a if n in b)
    total = sum(b[n][1] - a[n][1] for n in a if n in b)
    cpu = 100.0 * (1 - idle / total) if total else 0.0
    mem = {k: int(v.split()[0]) for k, v in (ln.split(":", 1) for ln in Path("/proc/meminfo").read_text().splitlines())}
    return cpu, 100.0 * (1 - mem["MemAvailable"] / mem["MemTotal"])


def plan_lines(tiers: list[Tier], scenario: str, host_cpus: int, host_mem_gb: float, order: list[int]) -> list[str]:
    """The runner's plan in a stable text form: the golden of ``--dry-run``."""
    lines = []
    for tier in tiers:
        siblings = SIBLINGS_KNEE if scenario in ("b06", "b06-knee") else SIBLINGS_STANDARD
        ok, why = fit(tier, host_cpus, host_mem_gb, siblings)
        if not ok:
            lines.append(f"SKIP {tier.name}: {why}")
            continue
        sets = assign_cpusets(order, tier.cpus, siblings)
        parts = " ".join(f"{k}={v}" for k, v in sets.items())
        lines.append(f"RUN {tier.name} scenario={scenario} cpus={tier.cpus} mem={tier.mem_gb}g {parts}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--tiers", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--host-cpus", type=int, default=os.cpu_count() or 1)
    p.add_argument("--host-mem-gb", type=float, default=0.0)
    p.add_argument("--order", default=None, help="comma-separated CPU numbers, idlest first (default: measure 10 s)")
    q = sub.add_parser("quiet")
    q.add_argument("--retries", type=int, default=5)
    q.add_argument("--sleep", type=float, default=60.0)
    args = ap.parse_args(argv)
    if args.cmd == "quiet":
        reason = wait_until_quiet(host_usage, args.retries, args.sleep)
        if reason:
            print(f"host is busy: {reason}", file=sys.stderr)
            return 2
        return 0
    mem = args.host_mem_gb or float(Path("/proc/meminfo").read_text().split()[1]) / 1024**2
    if args.order:
        order = [int(x) for x in args.order.split(",")]
    else:
        before = parse_proc_stat(Path("/proc/stat").read_text())
        time.sleep(10)
        order = idle_order(before, parse_proc_stat(Path("/proc/stat").read_text()))
    lines = plan_lines(parse_tiers(args.tiers), args.scenario, args.host_cpus, mem, order)
    print("\n".join(lines))
    print(json.dumps({"tiers": len(lines)}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
