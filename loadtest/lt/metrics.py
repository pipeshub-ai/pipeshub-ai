"""Turning raw JSONL into the numbers a run is judged on.

The reproducibility contract lives here. A single run's throughput figure means
nothing on its own; what makes an A/B comparison honest is the spread across
repeats, so every aggregate carries its coefficient of variation and the
comparison refuses to call a winner when the spread swallows the difference.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Runs whose primary metric varies more than this across repeats are not
#: trustworthy enough to A/B against. Reported, not silently tolerated.
CV_THRESHOLD = 0.05


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile. Explicit so results do not shift with the
    interpolation policy of whatever numpy/statistics version is installed."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def cv(values: list[float]) -> float:
    """Coefficient of variation: stdev / mean. 0.0 for fewer than two samples."""
    usable = [v for v in values if v is not None]
    if len(usable) < 2:
        return 0.0
    mean = statistics.fmean(usable)
    if mean == 0:
        return 0.0
    return statistics.stdev(usable) / mean


@dataclass
class LatencyStats:
    count: int = 0
    errors: int = 0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    max: float = 0.0
    #: Longest observed gap between consecutive successful probe responses.
    #: A blocked event loop shows up here even when no single request is slow,
    #: because the prober itself stops getting served.
    max_gap_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class CallLatencyStats:
    """Outbound backend-call latency (Confluence HTTP, Neo4j Cypher)."""

    count: int = 0
    errors: int = 0
    mean: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    max: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def call_latency_stats(rows: list[dict], kind: str) -> CallLatencyStats:
    samples = [r for r in rows if r.get("kind") == kind]
    if not samples:
        return CallLatencyStats()
    ok = [r for r in samples if r.get("ok", True)]
    latencies = [float(r["ms"]) for r in ok if r.get("ms") is not None]
    return CallLatencyStats(
        count=len(samples),
        errors=len(samples) - len(ok),
        mean=round(statistics.fmean(latencies), 2) if latencies else 0.0,
        p50=round(percentile(latencies, 50), 2),
        p95=round(percentile(latencies, 95), 2),
        p99=round(percentile(latencies, 99), 2),
        max=round(max(latencies, default=0.0), 2),
    )


def latency_stats(rows: list[dict], probe: str) -> LatencyStats:
    samples = [r for r in rows if r.get("type") == "probe" and r.get("probe") == probe]
    if not samples:
        return LatencyStats()
    # Anything >= 400 is an error, not a fast response. A 401 storm after token
    # expiry, or a 404 from a mistyped probe path, would otherwise show up as a
    # dramatic latency improvement.
    ok = [r for r in samples if isinstance(r.get("status"), int) and r["status"] < 400]
    latencies = [r["latency_ms"] for r in ok]
    times = sorted(r["t"] for r in ok)
    gaps = [b - a for a, b in zip(times, times[1:])] if len(times) > 1 else [0.0]
    return LatencyStats(
        count=len(samples),
        errors=len(samples) - len(ok),
        p50=round(percentile(latencies, 50), 2),
        p95=round(percentile(latencies, 95), 2),
        p99=round(percentile(latencies, 99), 2),
        max=round(max(latencies, default=0.0), 2),
        max_gap_s=round(max(gaps, default=0.0), 2),
    )


@dataclass
class ThroughputStats:
    expected_records: int = 0
    final_records: int = 0
    complete: bool = False
    #: wall-clock from sync trigger to the last unit reaching its expected count
    duration_s: float = 0.0
    #: the headline number: expected_records / duration_s
    records_per_s: float = 0.0
    #: per-connector series for charting: [[t_offset, total], ...]
    series: dict[str, list[list[float]]] = field(default_factory=dict)
    #: non-zero means the manual-index filter did not apply and the run is void
    indexed_records: int = 0
    #: connectors whose final count differs from what the fleet agrees on
    diverging: dict[str, int] = field(default_factory=dict)
    #: the count the majority of connectors reached
    modal_records: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _divergence(latest: dict[str, int]) -> tuple[int, dict[str, int]]:
    """Connectors that disagree with the fleet about how many records exist.

    Every unit syncs an identically-sized source, so their final counts must
    match. One that is *over* has duplicated work — two syncs of the same
    connector running at once, each inserting records the other did not see.

    Compared against the modal count rather than a hard-coded expectation
    because the seeded sources drift as they are edited; the invariant that
    holds regardless is that the units agree with each other.

    This matters because records_per_s is final_records / duration, so
    duplicates inflate the headline throughput number directly. A run at N=120
    once reported 32,383 records against 20,640 expected and was read as a
    throughput win.
    """
    if len(latest) < 2:
        return (next(iter(latest.values()), 0), {})

    counts: dict[int, int] = {}
    for total in latest.values():
        counts[total] = counts.get(total, 0) + 1
    modal = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    return modal, {cid: n for cid, n in latest.items() if n != modal}


def throughput_stats(rows: list[dict], started_at: float, expected_total: int) -> ThroughputStats:
    points = [r for r in rows if r.get("type") == "throughput"]
    if not points:
        return ThroughputStats(expected_records=expected_total)

    series: dict[str, list[list[float]]] = {}
    latest: dict[str, int] = {}
    auto_off: dict[str, int] = {}
    for row in points:
        cid = row["connector_id"]
        series.setdefault(cid, []).append(
            [round(row["t"] - started_at, 2), row.get("total", 0)]
        )
        latest[cid] = max(latest.get(cid, 0), row.get("total", 0))
        auto_off[cid] = max(auto_off.get(cid, 0), row.get("auto_index_off", 0))

    final = sum(latest.values())
    indexed = max(0, final - sum(auto_off.values()))

    # Completion time is when the *slowest* unit first hit its share, not when
    # polling stopped: trailing idle polls would otherwise deflate the rate.
    per_unit_target = expected_total // max(len(series), 1)
    completion_times: list[float] = []
    for cid, pts in series.items():
        hit = next((t for t, total in pts if total >= per_unit_target), None)
        if hit is not None:
            completion_times.append(hit)
    complete = len(completion_times) == len(series) and final >= expected_total

    if complete and completion_times:
        duration = max(completion_times)
    else:
        duration = max((pts[-1][0] for pts in series.values()), default=0.0)

    rate = (final / duration) if duration > 0 else 0.0
    modal, diverging = _divergence(latest)
    return ThroughputStats(
        expected_records=expected_total,
        final_records=final,
        complete=complete,
        duration_s=round(duration, 2),
        records_per_s=round(rate, 3),
        series=series,
        indexed_records=indexed,
        diverging=diverging,
        modal_records=modal,
    )


@dataclass
class ResourceStats:
    #: service -> {cpu_mean, cpu_p95, cpu_max, rss_mean_mb, rss_max_mb, threads_max, fds_max}
    services: dict[str, dict[str, float]] = field(default_factory=dict)
    #: service -> [[t_offset, cpu_percent, rss_mb], ...]
    series: dict[str, list[list[float]]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"services": self.services, "series": self.series}


@dataclass
class ContainerStats:
    #: container -> {cpu_mean, cpu_p95, cpu_max, mem_mean_mb, mem_max_mb, mem_limit_mb}
    containers: dict[str, dict[str, float]] = field(default_factory=dict)
    #: container -> [[t_offset, cpu_percent, mem_mb], ...]
    series: dict[str, list[list[float]]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"containers": self.containers, "series": self.series}


def _parse_docker_cpu(value: str | float | int | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).strip().rstrip("%") or 0.0)


def _parse_docker_bytes(token: str) -> float:
    """Docker stats sizes: `1.988GiB`, `120.8MiB`, `402kB` → bytes."""
    token = token.strip().replace(",", "")
    if not token:
        return 0.0
    units = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    lower = token.lower()
    for suffix, mult in sorted(units.items(), key=lambda kv: -len(kv[0])):
        if lower.endswith(suffix):
            return float(lower[: -len(suffix)]) * mult
    return float(lower)


def _parse_docker_mem(usage: str | None) -> tuple[float, float]:
    """`1.988GiB / 3GiB` → (used_mb, limit_mb)."""
    if not usage:
        return 0.0, 0.0
    used_s, _, limit_s = usage.partition("/")
    used = _parse_docker_bytes(used_s) / (1024 * 1024)
    limit = _parse_docker_bytes(limit_s) / (1024 * 1024) if limit_s.strip() else 0.0
    return used, limit


def container_stats(rows: list[dict], started_at: float) -> ContainerStats:
    """Summarise `docker stats` JSONL (ContainerStatsCollector output)."""
    by_name: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("type") != "container":
            continue
        name = row.get("Name") or row.get("Container") or "unknown"
        by_name.setdefault(name, []).append(row)

    stats = ContainerStats()
    for name, samples in by_name.items():
        samples = sorted(samples, key=lambda r: r.get("t", 0))
        cpus: list[float] = []
        mems: list[float] = []
        limit_mb = 0.0
        series: list[list[float]] = []
        for s in samples:
            cpu = _parse_docker_cpu(s.get("CPUPerc"))
            mem_mb, lim = _parse_docker_mem(s.get("MemUsage"))
            if lim:
                limit_mb = lim
            cpus.append(cpu)
            mems.append(mem_mb)
            series.append(
                [round(float(s["t"]) - started_at, 1), round(cpu, 2), round(mem_mb, 1)]
            )
        stats.containers[name] = {
            "cpu_mean": round(statistics.fmean(cpus), 2) if cpus else 0.0,
            "cpu_p95": round(percentile(cpus, 95), 2),
            "cpu_max": round(max(cpus, default=0.0), 2),
            "mem_mean_mb": round(statistics.fmean(mems), 1) if mems else 0.0,
            "mem_max_mb": round(max(mems, default=0.0), 1),
            "mem_limit_mb": round(limit_mb, 1),
        }
        stats.series[name] = series
    return stats


def resource_stats(rows: list[dict], started_at: float) -> ResourceStats:
    by_service: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("type") != "sample":
            continue
        by_service.setdefault(row["service"], []).append(row)

    stats = ResourceStats()
    for service, samples in by_service.items():
        # One service can span several pids (uvicorn workers, dev-mode
        # restarts). Sum concurrent pids per timestamp so the number reads as
        # "what this service costs", not "what one of its processes costs".
        per_t: dict[float, dict[str, float]] = {}
        for s in samples:
            bucket = per_t.setdefault(
                round(s["t"], 0), {"cpu": 0.0, "rss": 0.0, "threads": 0.0, "fds": 0.0}
            )
            bucket["cpu"] += s.get("cpu_percent", 0.0)
            bucket["rss"] += s.get("rss_bytes", 0) / (1024 * 1024)
            bucket["threads"] += s.get("threads", 0)
            bucket["fds"] += max(s.get("fds", 0), 0)

        times = sorted(per_t)
        cpus = [per_t[t]["cpu"] for t in times]
        rss = [per_t[t]["rss"] for t in times]
        stats.services[service] = {
            "cpu_mean": round(statistics.fmean(cpus), 2) if cpus else 0.0,
            "cpu_p95": round(percentile(cpus, 95), 2),
            "cpu_max": round(max(cpus, default=0.0), 2),
            "rss_mean_mb": round(statistics.fmean(rss), 1) if rss else 0.0,
            "rss_max_mb": round(max(rss, default=0.0), 1),
            "threads_max": max((per_t[t]["threads"] for t in times), default=0),
            "fds_max": max((per_t[t]["fds"] for t in times), default=0),
        }
        stats.series[service] = [
            [round(t - started_at, 1), round(per_t[t]["cpu"], 1), round(per_t[t]["rss"], 1)]
            for t in times
        ]
    return stats


def connector_timeline(
    rows: list[dict],
    started_at: float,
    trigger_times: dict[str, float],
) -> list[dict[str, Any]]:
    """Per-connector start, end and record count.

    Two notions of "end" are reported because they answer different questions:

      last_record_s   when records stopped arriving
      completed_s     when the connector's stored status left SYNCING, i.e. when
                      the sync task actually finished

    They differ whenever a connector keeps working after its final record —
    permissions, sync points, user resolution — and the gap between them is
    exactly the tail that a record-count-only measurement would miss.
    """
    per: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("type") != "throughput":
            continue
        cid = row["connector_id"]
        entry = per.setdefault(
            cid,
            {
                "connector_id": cid,
                "triggered_s": round(trigger_times.get(cid, started_at) - started_at, 2),
                "first_record_s": None,
                "last_record_s": None,
                "completed_s": None,
                "final_records": 0,
                "statuses": [],
            },
        )
        offset = round(row["t"] - started_at, 2)
        total = row.get("total", 0)
        if total > 0 and entry["first_record_s"] is None:
            entry["first_record_s"] = offset
        if total > entry["final_records"]:
            entry["final_records"] = total
            entry["last_record_s"] = offset

        status = (row.get("connector_status") or "").upper()
        if status:
            if not entry["statuses"] or entry["statuses"][-1] != status:
                entry["statuses"].append(status)
            # First observation of a non-syncing status after work began.
            if (
                entry["completed_s"] is None
                and entry["first_record_s"] is not None
                and status not in ("SYNCING", "FULL_SYNCING")
            ):
                entry["completed_s"] = offset

    out = []
    for entry in per.values():
        end = entry["completed_s"] if entry["completed_s"] is not None else entry["last_record_s"]
        entry["duration_s"] = (
            round(end - entry["triggered_s"], 2) if end is not None else None
        )
        entry["records_per_s"] = (
            round(entry["final_records"] / entry["duration_s"], 3)
            if entry["duration_s"]
            else None
        )
        entry["statuses"] = "->".join(entry["statuses"][:6])
        out.append(entry)
    return sorted(out, key=lambda e: e["connector_id"])


def aggregate(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    """Median across measured iterations, plus the spread that qualifies it."""
    rates = [it["throughput"]["records_per_s"] for it in iterations]
    durations = [it["throughput"]["duration_s"] for it in iterations]
    p99s = [it["latency"]["through"]["p99"] for it in iterations]
    maxgaps = [it["latency"]["through"]["max_gap_s"] for it in iterations]

    def _backend_summary(kind: str) -> dict[str, float]:
        means, p50s, p99s_b, counts = [], [], [], []
        for it in iterations:
            block = (it.get("latency") or {}).get(kind) or {}
            if not block:
                continue
            means.append(block.get("mean", 0.0))
            p50s.append(block.get("p50", 0.0))
            p99s_b.append(block.get("p99", 0.0))
            counts.append(block.get("count", 0))
        if not means:
            return {"mean_median": 0.0, "p50_median": 0.0, "p99_median": 0.0, "count_median": 0}
        return {
            "mean_median": round(statistics.median(means), 2),
            "p50_median": round(statistics.median(p50s), 2),
            "p99_median": round(statistics.median(p99s_b), 2),
            "count_median": int(statistics.median(counts)),
        }

    rate_cv = cv(rates)
    return {
        "iterations": len(iterations),
        "records_per_s": {
            "median": round(statistics.median(rates), 3) if rates else 0.0,
            "min": round(min(rates), 3) if rates else 0.0,
            "max": round(max(rates), 3) if rates else 0.0,
            "cv": round(rate_cv, 4),
        },
        "duration_s": {
            "median": round(statistics.median(durations), 2) if durations else 0.0,
            "cv": round(cv(durations), 4),
        },
        "api_p99_ms": {
            "median": round(statistics.median(p99s), 2) if p99s else 0.0,
            "max": round(max(p99s), 2) if p99s else 0.0,
        },
        "api_max_gap_s": {
            "median": round(statistics.median(maxgaps), 2) if maxgaps else 0.0,
            "max": round(max(maxgaps), 2) if maxgaps else 0.0,
        },
        "confluence_ms": _backend_summary("confluence"),
        "neo4j_ms": _backend_summary("neo4j"),
        # A single iteration has no spread to measure, so cv is 0 by definition.
        # Reporting that as "reproducible" would be the most misleading number
        # the harness could produce; it takes at least two runs to know.
        "reproducible": len(iterations) >= 2 and rate_cv <= CV_THRESHOLD,
        "cv_known": len(iterations) >= 2,
        "cv_threshold": CV_THRESHOLD,
        "all_complete": all(it["throughput"]["complete"] for it in iterations),
        "indexing_leaked": any(it["throughput"]["indexed_records"] > 0 for it in iterations),
        # Units sync identically-sized sources, so disagreement means duplicate
        # (or lost) work. records_per_s is final/duration, so a duplicating run
        # reports *higher* throughput — the failure mode this exists to catch.
        "records_diverged": any(
            it["throughput"].get("diverging") for it in iterations
        ),
        "divergence": {
            str(i): it["throughput"].get("diverging") or {}
            for i, it in enumerate(iterations)
            if it["throughput"].get("diverging")
        },
    }
