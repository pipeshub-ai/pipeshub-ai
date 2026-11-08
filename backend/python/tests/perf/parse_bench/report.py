"""Statistics, cgroup counters and the markdown report of the parse benchmark.

Pure functions, no PipesHub imports, so they are unit-tested without the
parsers installed.
"""

from __future__ import annotations

import math
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

MIB = 1024 * 1024
GATE_LAG_P99_MS = 100.0
GATE_MEM_FRACTION = 0.85


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100); 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    half = len(ordered) // 2
    return ordered[half] if len(ordered) % 2 else (ordered[half - 1] + ordered[half]) / 2


def latency_by_type(samples: Iterable[tuple[str, float]]) -> dict[str, dict[str, float]]:
    """{kind: {n, p50, p95, p99}} in seconds from (kind, seconds) pairs."""
    grouped: dict[str, list[float]] = {}
    for kind, seconds in samples:
        grouped.setdefault(kind, []).append(seconds)
    return {
        kind: {
            "n": len(vals),
            "p50": round(percentile(vals, 50), 3),
            "p95": round(percentile(vals, 95), 3),
            "p99": round(percentile(vals, 99), 3),
        }
        for kind, vals in sorted(grouped.items())
    }


def lag_summary(lags_seconds: Sequence[float]) -> dict[str, float]:
    """Event-loop lag figures in milliseconds."""
    return {
        "samples": len(lags_seconds),
        "p50_ms": round(percentile(lags_seconds, 50) * 1000, 1),
        "p99_ms": round(percentile(lags_seconds, 99) * 1000, 1),
        "max_ms": round(max(lags_seconds, default=0.0) * 1000, 1),
    }


_FRAME = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<fn>\S+)')


def stall_frames(stack_text: str, depth: int = 2) -> str:
    """The innermost *depth* frames of a formatted stack, as ``file:line fn <- file:line fn``."""
    frames = [f"{os.path.basename(m["file"])}:{m['line']} {m['fn']}" for m in _FRAME.finditer(stack_text)]
    return " <- ".join(reversed(frames[-depth:])) if frames else "<no frames>"


def top_stalls(stacks: Sequence[str], n: int = 3) -> list[dict[str, Any]]:
    """The *n* most frequent places the loop was found blocked, most frequent first."""
    counts: dict[str, int] = {}
    for stack in stacks:
        key = stall_frames(stack)
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"count": c, "where": where} for where, c in ranked[:n]]


# -- cgroup -------------------------------------------------------------------


def parse_flat_keyed(text: str) -> dict[str, int]:
    """``key value`` lines (cpu.stat, memory.events) to ints; bad lines are skipped."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_cgroup(root: Path) -> dict[str, int | None]:
    """Counters of the cgroup v2 directory *root*; a missing file gives None."""
    stat_text = _read(root / "cpu.stat")
    stat = parse_flat_keyed(stat_text) if stat_text else {}
    events_text = _read(root / "memory.events")
    events = parse_flat_keyed(events_text) if events_text else {}

    def num(name: str) -> int | None:
        text = _read(root / name)
        return int(text) if text and text.isdigit() else None

    return {
        "memory_peak": num("memory.peak"),
        "memory_current": num("memory.current"),
        "usage_usec": stat.get("usage_usec"),
        "nr_throttled": stat.get("nr_throttled"),
        "throttled_usec": stat.get("throttled_usec"),
        "oom_kill": events.get("oom_kill"),
    }


REQUIRED_CGROUP_FILES = ("memory.peak", "cpu.stat", "memory.events")


def missing_cgroup_files(root: Path) -> list[str]:
    """Counter files the tier table needs that cannot be read under *root* (cgroup v2)."""
    missing = []
    for name in REQUIRED_CGROUP_FILES:
        try:
            (root / name).read_text()
        except OSError:
            missing.append(name)
    return missing


def cgroup_delta(before: Mapping[str, int | None], after: Mapping[str, int | None]) -> dict[str, int | None]:
    """Run-scoped counters. ``memory_peak`` is absolute (it is a high-water mark)."""
    out: dict[str, int | None] = {"memory_peak": after.get("memory_peak")}
    for key in ("usage_usec", "nr_throttled", "throttled_usec", "oom_kill"):
        a, b = after.get(key), before.get(key)
        out[key] = None if a is None or b is None else a - b
    return out


def in_own_cgroup(proc_self_cgroup: str) -> bool:
    """True when /proc/self/cgroup says the cgroup root is ours (a container namespace)."""
    lines = [ln for ln in proc_self_cgroup.splitlines() if ln.startswith("0::")]
    return bool(lines) and lines[0][3:].strip() in ("/", "")


# -- targets ------------------------------------------------------------------

GATES = [
    ("Loop stall during parsing", "event-loop lag p99 <= 100 ms"),
    ("CPU-bound parse throughput scales", "files/s at T-server >= 3x T-laptop"),
    ("No regression on small machines", "throughput >= baseline -5 %, idle RSS <= baseline +10 %"),
    ("Memory safety", "peak memory < 85 % of the limit, 0 OOM kills"),
    ("CPU stays within budget", "no throttling storms (cgroup throttled_usec)"),
    ("Output unchanged", "golden diff empty"),
]


def memory_safety(peak_bytes: float | None, limit_bytes: float | None) -> str:
    if not peak_bytes or not limit_bytes:
        return "n/a"
    ratio = peak_bytes / limit_bytes
    return f"{ratio * 100:.0f} % ({'ok' if ratio < GATE_MEM_FRACTION else 'OVER 85 %'})"


ARM_LABELS = {
    "pdfplumber": "PdfPlumber arm (not yet the shipped default)",
    "docling": "Docling arm (upper bound, local Docling)",
}


def arm_label(result: Mapping[str, Any]) -> str:
    return ARM_LABELS.get(result["config"].get("pdf_parser", "pdfplumber"), "unknown arm")


def spread(values: Sequence[float], digits: int = 1) -> str:
    """``median (min-max)`` of *values*."""
    if not values:
        return "n/a"
    return f"{round(median(values), digits):g} ({round(min(values), digits):g}-{round(max(values), digits):g})"


def cell_row(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """One row for all finished runs of a (tier, mix) cell: medians with their min-max."""
    first = results[0]
    return [
        first["config"]["label"],
        first["config"]["mix"],
        str(len(results)),
        spread([r["metrics"]["files_per_s"] for r in results], 3),
        spread([r["metrics"]["loop_lag"]["p99_ms"] for r in results], 0),
        spread([r["metrics"]["memory"]["peak_tree_rss_mib"] for r in results], 0),
        spread([r["metrics"]["cpu_seconds"] for r in results], 0),
    ]


CELL_HEADER = ["run", "mix", "runs", "files/s", "loop lag p99 ms", "peak tree RSS MiB", "CPU s"]


def run_row(result: Mapping[str, Any]) -> list[str]:
    cfg, m = result["config"], result["metrics"]
    cg = result.get("cgroup")
    return [
        cfg["label"],
        cfg["mix"],
        f"{m['files_per_s']:.3f}",
        f"{m['loop_lag']['p50_ms']:.0f} / {m['loop_lag']['p99_ms']:.0f}",
        f"{m['memory']['peak_tree_rss_mib']:.0f}",
        f"{m['cpu_seconds']:.0f}",
        f"{m['threads']['peak_total']} x {m['workers']['peak_parse_workers']}",
        "n/a" if not cg else f"{(cg['memory_peak'] or 0) / MIB:.0f} / {(cg['throttled_usec'] or 0) / 1e6:.1f}s",
        str(result["files"]["failed"]),
    ]


HEADER = [
    "run", "mix", "files/s", "loop lag p50/p99 ms", "peak tree RSS MiB", "CPU s",
    "threads x parse workers", "cgroup mem.peak MiB / throttled", "failed",
]


def markdown_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def markdown_report(results: Sequence[Mapping[str, Any]]) -> str:
    return markdown_table(HEADER, [run_row(r) for r in results])


def median_run(results: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The run whose files/s is the median of *results* (the lower one for an even count)."""
    ordered = sorted(results, key=lambda r: r["metrics"]["files_per_s"])
    return ordered[(len(ordered) - 1) // 2]


def targets_table(by_tier: Mapping[str, Mapping[str, Any]], limits: Mapping[str, float]) -> str:
    """The README's targets table, filled from one median result per tier.

    *by_tier* maps a tier label to its result (cpu-heavy mix preferred by the caller);
    *limits* maps a tier label to its memory limit in bytes.
    """
    tiers = list(by_tier)
    rows = []
    lag = ", ".join(f"{t}: {by_tier[t]['metrics']['loop_lag']['p99_ms']:.0f} ms" for t in tiers)
    fps = ", ".join(f"{t}: {by_tier[t]['metrics']['files_per_s']:.3f}" for t in tiers)
    mem = ", ".join(
        f"{t}: {memory_safety(_peak_bytes(by_tier[t]), limits.get(t))}" for t in tiers
    )
    throttle = ", ".join(f"{t}: {_throttle(by_tier[t])}" for t in tiers)
    rows.append(["Loop stall during parsing", lag])
    rows.append(["CPU-bound parse throughput (files/s)", fps])
    rows.append(["Memory safety (peak / limit)", mem])
    rows.append(["CPU throttling (cgroup throttled_usec)", throttle])
    return markdown_table(["Target", "Baseline"], rows)


def _peak_bytes(result: Mapping[str, Any]) -> float:
    cg = result.get("cgroup")
    if cg and cg.get("memory_peak"):
        return float(cg["memory_peak"])
    return result["metrics"]["memory"]["peak_tree_rss_mib"] * MIB


def _throttle(result: Mapping[str, Any]) -> str:
    cg = result.get("cgroup")
    if not cg or cg.get("throttled_usec") is None:
        return "n/a (no cgroup)"
    return f"{cg['throttled_usec'] / 1e6:.1f} s"


class PeakReader:
    """``memory.peak`` scoped to one run where the kernel allows it (5.19+).

    Writing to an open ``memory.peak`` resets the high-water mark for that file
    descriptor only, so the figure read back later is this run's peak and not
    the container's since it started. Where the reset fails, ``scoped`` is False
    and the value is the container's lifetime peak.
    """

    def __init__(self, root: Path) -> None:
        self.scoped = False
        self._file = None
        try:
            self._file = (root / "memory.peak").open("r+")
            self._file.write("0")
            self._file.flush()
            self.scoped = True
        except OSError:
            if self._file is not None:
                self._file.close()
            self._file = None

    def read(self) -> int | None:
        if self._file is None:
            return None
        try:
            self._file.seek(0)
            text = self._file.read().strip()
        except OSError:
            return None
        return int(text) if text.isdigit() else None

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
