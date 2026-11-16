"""Cost-class indexing benchmark: record event to indexed, per corpus kind, on the configuration that ships.

Local CPU embedding is real; only the chat and vision model is fake (``fake_ai/server.py``). Uploads
follow an open-loop schedule, so a slow stack cannot slow the arrivals down, and the gated latency
counts from the *scheduled* arrival: ``lastIndexTimestamp - scheduled_arrival_ms``. The client-side
timing and ``createdAtTimestamp`` are diagnostic columns only.

Scenarios (``--scenario``; ``f`` = ``--tier-cpus / 16``):

* ``smoke``      one file of every kind of the ``cost-smoke`` corpus, spaced out (CI nightly)
* ``b01``        ``round(10 f)`` huge PDFs at the start, then small-class probes at a Poisson rate
* ``b02``        one huge PDF alone
* ``b03``        two large PDFs in flight plus small probes (4 CPU at 8 or 16 GB)
* ``b05``        small-only steady state
* ``b06``        small-only saturation (everything arrives at once): throughput at a tier
* ``b06-knee``   the same, with one indexing process (checked); ``run_tiers_e2e.sh`` repeats it over the
                 tiers and calls ``--knee-from`` on the results to get ``CELL_CPUS`` and its confidence
* ``calibrate``  one unit at a time against a single worker, measured from the *process* that does the
                 work: Docling's and the embedding server's own CPU, Docling's ``s_d``. Parse, inspect and
                 raster run inside pools and cannot be isolated from outside: reported "not measurable"

Every sample must pass the acceptance filters (COMPLETED, one record per upload, a ``virtualRecordId``
no other record of the run shares, timestamps that make sense) or it is *rejected* with a reason.
More than 1 % rejected, or any sibling container above 70 % of its cpuset, makes the run **invalid**:
the numbers are kept but are not a result. A FAILED, missing or timed-out probe makes it **failed**.
Exit code: 0 pass, 1 failed, 3 invalid.

The first half of this file is pure (schedules, filters, statistics, calibration arithmetic) and is what
``unit/test_perf_costclass.py`` checks. The second half drives a live stack.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PERF_DIR = Path(__file__).resolve().parent
_IT_DIR = _PERF_DIR.parent
_REPO = _IT_DIR.parent
for _p in (_PERF_DIR, _IT_DIR / "helper", _IT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import salt
from stack import (
    FINAL_STATUSES,
    SUCCESS_STATUS,
    create_kb,
    describe_org_models,
    ensure_client_credentials,
    host_memory_gb,
    load_env,
    percentile,
    run_command,
)

SCHEMA_VERSION = 2
KINDS = ("small-text", "small-pdf", "office", "scanned", "large-pdf", "huge-pdf")
SMALL_KINDS = ("small-text", "small-pdf", "office")
REJECT_RATIO_LIMIT = 0.01
SIBLING_CPU_LIMIT = 0.70
SKEW_TOLERANCE_MS = 2000
EXIT_PASS, EXIT_FAILED, EXIT_INVALID = 0, 1, 3

NOT_MEASURABLE_HERE = (
    "48 and 96 CPU app tiers",
    "NUMA effects (one node)",
    "memory bandwidth at 96+ cores",
    "256 GB+ working sets",
    "cgroup v1 live",
    "Neo4j and Qdrant beyond 8 cores",
    "real provider rate limits at scale",
    "throttled-ratio semantics on wide limited cgroups",
    "HPA and KEDA autoscaling",
)

# What each family is expected to use per unit of work (cores), from the capacity design's budget table.
EXPECTED_CORES = {"docling": 2.0, "parse": 1.0, "inspect": 1.0, "raster": 1.0, "embed": 2.0}
# Families that run in a process of their own: measured from that process's CPU (``/proc/<pid>/stat``), one
# unit at a time at a single worker, with the nearest single-kind workload driving it.
FAMILY_KIND = {"docling": "small-pdf", "embed": "small-text"}
FAMILY_PROCESS = {"docling": "docling_main", "embed": "embedding_main"}
# The rest run inside pools of the indexing process: nothing outside can tell their CPU from the process's.
NOT_MEASURABLE_FAMILIES = {
    "parse": "runs in a pool inside the indexing process; needs per-unit CPU from the pool",
    "inspect": "runs in a pool inside the indexing process; needs per-unit CPU from the pool",
    "raster": "runs in a pool inside the indexing process; needs per-unit CPU from the pool",
}
WEIGHT_TOLERANCE = 0.30
BUSY_MIN_CORES = 0.10  # an interval counts as the process being busy at or above this many cores
PROC_SAMPLE_S = 0.25
CALIBRATION_PID_REFRESH_S = 2.0  # a docker top per two seconds of one unit: light next to a 0.25 s /proc sample
# Scopes the run cannot work without: a stack that refuses one stops the run at credentials.
REQUIRED_SCOPES = frozenset({"kb:read", "kb:write", "kb:upload", "kb:delete", "config:read", "config:write"})
SMOKE_P95_BUDGET_S = 600.0
SMOKE_WALL_BUDGET_S = 1800.0
# What the bench stack is, for the result's environment block (it starts one stack and does not vary it).
STACK_GRAPH_DB, STACK_BROKER = "neo4j", "redis"


# ====================================================================================
# Pure: schedules and plans
# ====================================================================================


def poisson_arrivals(rate_per_s: float, duration_s: float, seed: int, start_s: float = 0.0) -> list[float]:
    """Arrival offsets of a Poisson process, from a seeded generator. They never depend on completions."""
    if rate_per_s <= 0 or duration_s <= 0:
        return []
    rng = random.Random(seed)
    out: list[float] = []
    now = 0.0
    while True:
        now += rng.expovariate(rate_per_s)
        if now >= duration_s:
            return out
        out.append(round(start_s + now, 3))


@dataclass
class Arrival:
    seq: int
    kind: str
    at_s: float  # offset from the run's start, on the schedule
    role: str = "probe"  # probe | load | control
    template: str = ""  # corpus file name
    control_of: int | None = None


@dataclass
class Plan:
    scenario: str
    arrivals: list[Arrival]
    profile: str
    notes: dict[str, Any] = field(default_factory=dict)
    needs_corpus_kinds: tuple[str, ...] = KINDS


def lambda_for_rho(
    rho_target: float, mean_pages: float, s_d: float, docling_workers: int, rate_cap: float = 0.2
) -> float:
    """The small-PDF arrival rate that keeps Docling's utilisation at *rho_target*, capped at *rate_cap*.

    ``rho_d = lambda * E[pages] * s_d / W_d`` (the B-01 precondition: Docling is not the bottleneck).
    """
    if s_d <= 0 or mean_pages <= 0 or docling_workers <= 0:
        return rate_cap
    return min(rate_cap, rho_target * docling_workers / (mean_pages * s_d))


def rho_docling(rate: float, mean_pages: float, s_d: float, docling_workers: int) -> float:
    return rate * mean_pages * s_d / docling_workers if docling_workers else math.inf


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def build_plan(
    scenario: str,
    *,
    tier_cpus: int = 16,
    seed: int = 1,
    duration_s: float = 900.0,
    rate_per_s: float = 0.2,
    burst_count: int = 60,
    available: dict[str, list[str]] | None = None,
    inject_duplicate: bool = False,
    calibrate_units: int = 3,
) -> Plan:
    """The upload schedule of a scenario. *available* maps a kind to its corpus templates (file names)."""
    avail = available or {k: [f"{k}-{i:02d}" for i in range(4)] for k in KINDS}
    f = tier_cpus / 16
    arrivals: list[Arrival] = []
    rng = random.Random(seed)

    def add(kind: str, at: float, role: str, n: int) -> None:
        names = avail.get(kind) or []
        if not names:
            raise ValueError(f"the corpus has no {kind!r} file for scenario {scenario!r}")
        arrivals.append(Arrival(len(arrivals), kind, round(at, 3), role, names[n % len(names)]))

    def small_kind() -> str:
        usable = [k for k in SMALL_KINDS if avail.get(k)]
        return rng.choice(usable)

    notes: dict[str, Any] = {"f": f}
    if scenario == "smoke":
        for n, kind in enumerate(k for k in KINDS if avail.get(k)):
            add(kind, n * 2.0, "probe", 0)
        notes["control"] = inject_duplicate
    elif scenario == "b01":
        huge = max(1, _round_half_up(10 * f))
        notes["huge_count"] = huge
        for n in range(huge):
            add("huge-pdf", n * (60.0 / huge), "load", n)
        # the probes start once the huge documents are in flight (the first minute has passed)
        for n, at in enumerate(poisson_arrivals(rate_per_s, duration_s, seed, start_s=60.0)):
            add(small_kind(), at, "probe", n)
        notes["rate_per_s"] = rate_per_s
        notes["duration_s"] = duration_s
    elif scenario == "b02":
        add("huge-pdf", 0.0, "probe", 0)
    elif scenario == "b03":
        for n in range(2):
            add("large-pdf", n * 5.0, "load", n)
        for n, at in enumerate(poisson_arrivals(rate_per_s, duration_s, seed, start_s=30.0)):
            add(small_kind(), at, "probe", n)
        notes["rate_per_s"] = rate_per_s
        notes["duration_s"] = duration_s
    elif scenario == "b05":
        for n, at in enumerate(poisson_arrivals(rate_per_s, duration_s, seed)):
            add(small_kind(), at, "probe", n)
        notes["rate_per_s"] = rate_per_s
        notes["duration_s"] = duration_s
    elif scenario in ("b06", "b06-knee"):
        for n in range(burst_count):
            add("small-pdf", 0.0, "probe", n)
        notes["saturation"] = True
        notes["burst_count"] = burst_count
    elif scenario == "calibrate":
        at = 0.0  # the schedule is not time-based here
        for fam, kind in FAMILY_KIND.items():
            for n in range(calibrate_units):
                add(kind, at, f"unit:{fam}", n)  # sequential: the runner waits for each unit
        notes["sequential"] = True
    else:
        raise ValueError(f"unknown scenario {scenario!r}")
    if inject_duplicate and arrivals:
        first = arrivals[0]
        arrivals.append(Arrival(len(arrivals), first.kind, first.at_s + 1.0, "control", first.template, control_of=first.seq))
    arrivals.sort(key=lambda a: (a.at_s, a.seq))
    profile = {"b01": "cost-huge", "b02": "cost-huge", "b03": "cost-standard", "smoke": "cost-smoke"}.get(
        scenario, "cost-standard"
    )
    return Plan(scenario, arrivals, profile, notes)


# ====================================================================================
# Pure: samples, latency, acceptance
# ====================================================================================


@dataclass
class Sample:
    seq: int
    kind: str
    role: str
    name: str
    md5: str
    scheduled_arrival_ms: float
    send_ms: float | None = None
    record_id: str | None = None
    record_count: int = 1  # records the system created for this upload
    status: str | None = None
    last_index_ms: float | None = None
    created_ms: float | None = None
    virtual_record_id: str | None = None
    gateway_calls: int | None = None
    cache_hits: int | None = None
    embed_wait_s: float | None = None
    control: bool = False
    control_of: int | None = None  # the seq of the upload this control re-sends
    upload_error: str = ""


def gated_latency_s(sample: Sample) -> float | None:
    """``lastIndexTimestamp - scheduled_arrival_ms`` in seconds. A stalled upload keeps its stall."""
    if sample.last_index_ms is None:
        return None
    return (sample.last_index_ms - sample.scheduled_arrival_ms) / 1000.0


def diagnostic_latencies(sample: Sample) -> dict[str, float | None]:
    """Columns that are shown and never gated."""
    return {
        "from_created_s": None if sample.last_index_ms is None or sample.created_ms is None
        else (sample.last_index_ms - sample.created_ms) / 1000.0,
        "from_send_s": None if sample.last_index_ms is None or sample.send_ms is None
        else (sample.last_index_ms - sample.send_ms) / 1000.0,
        "upload_delay_s": None if sample.send_ms is None else (sample.send_ms - sample.scheduled_arrival_ms) / 1000.0,
    }


def filter_samples(
    samples: list[Sample], now_ms: float | None = None
) -> tuple[list[Sample], dict[str, list[Sample]]]:
    """Accepted samples, and the rejected ones by reason.

    Rejected: not COMPLETED; no or future/early timestamps; more than one record for the upload;
    the same bytes uploaded twice (md5); a ``virtualRecordId`` shared with another record of the run
    (the md5-duplicate copy path stamps ``lastIndexTimestamp`` too). A control sample (a deliberate
    re-upload) is judged by the same md5 rule as every other sample, on its real bytes: it is rejected as
    ``duplicate_md5`` only when it carries its original's bytes, else as ``control_not_a_duplicate``. It is
    never accepted and never makes another sample share a vrid. Whether the *product* treated it as a
    duplicate is a separate question, answered by ``judge_control``.
    Probes that hit their *own* record's cache are kept: that is a real, slower record.
    """
    accepted: list[Sample] = []
    rejected: dict[str, list[Sample]] = {}
    seen_md5: dict[str, int] = {}
    vrid_owners: dict[str, int] = {}
    for s in samples:
        if not s.control and s.virtual_record_id:
            vrid_owners[s.virtual_record_id] = vrid_owners.get(s.virtual_record_id, 0) + 1
    for s in sorted(samples, key=lambda x: x.seq):
        reason = None
        if s.md5 in seen_md5 and seen_md5[s.md5] != s.seq:
            reason = "duplicate_md5"
        elif s.control:
            reason = "control_not_a_duplicate"
        elif s.status != SUCCESS_STATUS:
            reason = "not_completed"
        elif s.record_count != 1:
            reason = "multiple_records"
        elif s.last_index_ms is None:
            reason = "missing_timestamp"
        elif not s.virtual_record_id:
            reason = "missing_virtual_record_id"
        elif vrid_owners.get(s.virtual_record_id, 0) > 1:
            reason = "shared_vrid"
        elif (
            s.last_index_ms < s.scheduled_arrival_ms - SKEW_TOLERANCE_MS
            or (s.created_ms is not None and s.last_index_ms < s.created_ms - SKEW_TOLERANCE_MS)
            or (now_ms is not None and s.last_index_ms > now_ms + SKEW_TOLERANCE_MS)
        ):
            reason = "skewed_timestamp"
        seen_md5.setdefault(s.md5, s.seq)
        if reason:
            rejected.setdefault(reason, []).append(s)
        else:
            accepted.append(s)
    return accepted, rejected


def judge_control(samples: list[Sample]) -> dict[str, Any] | None:
    """Did the product treat the deliberate re-upload as a duplicate? None when no control was injected.

    This build deduplicates by md5: a record whose bytes match a finished (or running) record takes that
    record's ``virtualRecordId`` and skips indexing. So the control passes only when it carries its
    original's bytes **and** ends up with the original's vrid. The same bytes under a different vrid, and
    indexed, is the failure this control exists to catch: the product did the work twice. The judgement
    reads the samples' md5 and vrid, never the ``control`` label.
    """
    controls = [s for s in samples if s.control]
    if not controls:
        return None
    by_seq = {s.seq: s for s in samples}
    rows = []
    for c in controls:
        o = by_seq.get(c.control_of) if c.control_of is not None else None
        md5_match = o is not None and o.md5 == c.md5
        same_vrid = bool(o and o.virtual_record_id and c.virtual_record_id == o.virtual_record_id)
        if c.upload_error:
            outcome = "upload_failed"
        elif not md5_match:
            outcome = "bytes_differ_from_original"
        elif not c.virtual_record_id or not o or not o.virtual_record_id:
            outcome = "no_virtual_record_id"
        elif same_vrid and c.status == SUCCESS_STATUS:
            outcome = "deduplicated"
        elif c.status == SUCCESS_STATUS:
            outcome = "indexed_separately"
        else:
            outcome = "not_deduplicated"
        rows.append({
            "seq": c.seq, "control_of": c.control_of, "md5_match": md5_match, "same_vrid": same_vrid,
            "original_vrid": o.virtual_record_id if o else None, "control_vrid": c.virtual_record_id,
            "control_status": c.status, "outcome": outcome,
        })
    return {"ok": all(r["outcome"] == "deduplicated" for r in rows), "controls": rows}


def rejected_ratio(accepted: list[Sample], rejected: dict[str, list[Sample]]) -> float:
    """Rejected over accepted, deliberate controls excluded. All rejected and none accepted is infinite."""
    n = sum(len([s for s in v if not s.control]) for v in rejected.values())
    if n == 0:
        return 0.0
    return n / len(accepted) if accepted else math.inf


# ====================================================================================
# Pure: statistics
# ====================================================================================


def summarise(values: list[float]) -> dict[str, float | int | None]:
    def r(v: float | None) -> float | None:
        return None if v is None else round(v, 3)

    return {
        "n": len(values),
        "p50": r(percentile(values, 50)), "p95": r(percentile(values, 95)), "p99": r(percentile(values, 99)),
        "max": r(max(values) if values else None), "mean": r(statistics.fmean(values) if values else None),
    }


def per_kind_summary(accepted: list[Sample]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for kind in sorted({s.kind for s in accepted}):
        rows = [s for s in accepted if s.kind == kind]
        out[kind] = {
            **summarise([x for x in (gated_latency_s(s) for s in rows) if x is not None]),
            "diagnostic_from_created_p50": (
                None if not rows else summarise(
                    [x for x in (diagnostic_latencies(s)["from_created_s"] for s in rows) if x is not None]
                )["p50"]
            ),
            "gateway_calls": _sum_or_none([s.gateway_calls for s in rows]),
            "cache_hits": _sum_or_none([s.cache_hits for s in rows]),
            "probes_with_cache_hits": sum(1 for s in rows if s.cache_hits),
        }
    return out


def _sum_or_none(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def coefficient_of_variation(values: list[float]) -> float | None:
    """Sample standard deviation over the mean; None for fewer than two values or a zero mean."""
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return None if mean == 0 else statistics.stdev(values) / abs(mean)


def repeats_stable(p50s: list[float], limit: float = 0.10) -> bool:
    cv = coefficient_of_variation(p50s)
    return cv is not None and cv <= limit


# ====================================================================================
# Pure: cgroup readings, sibling CPU, connection limits
# ====================================================================================


def parse_cpu_stat(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def parse_cpuset(text: str) -> int:
    """The number of CPUs in a cpuset string such as ``0-3,8,10-11``. Empty means unrestricted (0)."""
    total = 0
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            low, high = part.split("-", 1)
            total += int(high) - int(low) + 1
        else:
            total += 1
    return total


def cpu_fraction(usage_usec: list[tuple[float, int]], cpuset_size: float) -> float | None:
    """Average share of a cpuset a container used, from ``(time_s, cpu.stat usage_usec)`` readings.

    Divides by the container's own cpuset, never by the host's CPUs.
    """
    if cpuset_size <= 0 or len(usage_usec) < 2:
        return None
    (t0, u0), (t1, u1) = usage_usec[0], usage_usec[-1]
    if t1 <= t0:
        return None
    return max(0.0, (u1 - u0) / 1e6 / (t1 - t0) / cpuset_size)


def sibling_report(
    series: dict[str, list[tuple[float, int]]], cpusets: dict[str, float], limit: float = SIBLING_CPU_LIMIT
) -> dict[str, Any]:
    """Per sibling: average share of its cpuset; ``saturated`` lists those above *limit*."""
    shares = {name: cpu_fraction(series.get(name, []), size) for name, size in cpusets.items()}
    return {
        "average_share": {k: None if v is None else round(v, 3) for k, v in shares.items()},
        "saturated": sorted(k for k, v in shares.items() if v is not None and v > limit),
        "unmeasured": sorted(k for k, v in shares.items() if v is None),
    }


def check_limits(
    peaks: dict[str, float | None], *, redis_maxclients: int | None, neo4j_budget: int | None,
    pg_max_connections: int | None, oom_kills: int,
) -> list[str]:
    """Connection counts against their limits and no OOM kill. Returns violations."""
    bad: list[str] = []
    redis = peaks.get("redis_clients")
    if redis is not None and redis_maxclients and redis >= 0.5 * redis_maxclients:
        bad.append(f"redis clients {redis:.0f} >= 50% of maxclients {redis_maxclients}")
    neo = peaks.get("neo4j_connections")
    if neo is not None and neo_budget_exceeded(neo, neo4j_budget):
        bad.append(f"neo4j connections {neo:.0f} > budget {neo4j_budget}")
    pg = peaks.get("pg_backends")
    if pg is not None and pg_max_connections and pg > pg_max_connections:
        bad.append(f"postgres backends {pg:.0f} > max_connections {pg_max_connections}")
    if oom_kills:
        bad.append(f"oom_kill {oom_kills}")
    return bad


def neo_budget_exceeded(count: float, budget: int | None) -> bool:
    return budget is not None and count > budget


# ====================================================================================
# Pure: the run's verdict
# ====================================================================================


def run_verdict(
    *,
    accepted: int,
    rejected_n: int,
    failed: int,
    missing: int,
    timed_out: int,
    upload_failures: int,
    saturated_siblings: list[str],
    limit_violations: list[str],
    control_expected: bool,
    control_rejected: bool,
    restarts: int = 0,
    unmeasured_siblings: list[str] | None = None,
    control_detail: str = "",
    chaos_expected: bool = False,
    chaos_fired: bool = False,
    log_errors: list[str] | None = None,
    log_unreadable: bool = False,
    budget_failures: list[str] | None = None,
) -> dict[str, Any]:
    """``failed`` beats ``invalid`` beats ``pass``; ``invalid`` is never a pass and never a plain failure."""
    failed_reasons: list[str] = []
    invalid_reasons: list[str] = []
    for label, n in (("FAILED probes", failed), ("missing records", missing), ("timed-out probes", timed_out),
                     ("failed uploads", upload_failures)):
        if n:
            failed_reasons.append(f"{n} {label}")
    failed_reasons += limit_violations
    failed_reasons += budget_failures or []
    if log_unreadable:
        failed_reasons.append("the product log could not be read (or was empty), so its ERROR lines were not checked")
    if log_errors:
        failed_reasons.append(f"{len(log_errors)} ERROR line(s) in the product log, first: {log_errors[0][:160]}")
    if control_expected and not control_rejected:
        failed_reasons.append(
            "the duplicate-upload control was not deduplicated by the product, so the md5 dedup is not working"
            + (f" ({control_detail})" if control_detail else ""))
    if chaos_expected and not chaos_fired:
        invalid_reasons.append("--chaos was requested but the run ended before the kill fired")
    if chaos_expected and chaos_fired and restarts == 0:
        failed_reasons.append("the app was killed and did not restart (set a restart policy for the chaos run)")
    ratio = (rejected_n / accepted) if accepted else (math.inf if rejected_n else 0.0)
    if ratio > REJECT_RATIO_LIMIT:
        invalid_reasons.append(f"rejected/accepted {ratio:.1%} > {REJECT_RATIO_LIMIT:.0%}")
    if saturated_siblings:
        invalid_reasons.append("sibling above 70% of its cpuset: " + ", ".join(saturated_siblings))
    if unmeasured_siblings:
        invalid_reasons.append("sibling CPU not measurable (no cpuset or CPU limit): " + ", ".join(unmeasured_siblings))
    if accepted == 0 and not failed_reasons:
        invalid_reasons.append("no accepted sample")
    status = "failed" if failed_reasons else "invalid" if invalid_reasons else "pass"
    return {
        "status": status,
        "reasons": failed_reasons + invalid_reasons,
        "invalid_reasons": invalid_reasons,
        "disturbed": restarts > 0,
        "chaos": {"expected": chaos_expected, "fired": chaos_fired},
        "unmeasured_siblings": unmeasured_siblings or [],
        "exit_code": {"pass": EXIT_PASS, "failed": EXIT_FAILED, "invalid": EXIT_INVALID}[status],
    }


# ====================================================================================
# Pure: calibration arithmetic
# ====================================================================================


def process_cpu_ticks(stat_text: str) -> int | None:
    """``utime + stime`` in clock ticks from ``/proc/<pid>/stat``. The command name can hold spaces and
    parentheses, so the fields are counted from the last ``)``."""
    close = stat_text.rfind(")")
    fields = stat_text[close + 1:].split() if close >= 0 else []
    if len(fields) < 13:
        return None
    try:
        return int(fields[11]) + int(fields[12])
    except ValueError:
        return None


def family_pids(top_output: str, marker: str) -> list[int]:
    """Pids of the processes whose command line holds *marker*, and of all their descendants.

    *top_output* is ``docker top <container> -eo pid,ppid,args`` (host pids). A pool's child processes carry
    another command line but are the family's work, so they are followed by parent pid.
    """
    rows: list[tuple[int, int, str]] = []
    for line in top_output.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
    found = {pid for pid, _, args in rows if marker in args}
    grew = True
    while grew:
        grew = False
        for pid, ppid, _ in rows:
            if ppid in found and pid not in found:
                found.add(pid)
                grew = True
    return sorted(found)


def busy_and_cpu(series: list[tuple[float, float]], min_cores: float = BUSY_MIN_CORES) -> tuple[float, float]:
    """``(cpu_seconds, busy_seconds)`` of one process from ``(time_s, cumulative_cpu_s)`` readings.

    Both are summed over the same intervals, those in which the process used at least *min_cores*: the time
    it was doing work for the unit, not the unit's end-to-end wait. The CPU of idle intervals is left out,
    so a process that idles at a few percent for a long record does not inflate its cores.
    """
    cpu = busy = 0.0
    for (t0, c0), (t1, c1) in itertools.pairwise(series):
        dt, dc = t1 - t0, c1 - c0
        if dt <= 0 or dc < 0:
            continue
        if dc / dt >= min_cores:
            cpu += dc
            busy += dt
    return cpu, busy


PEAK_WINDOW_S = 10.0
DOCLING_BOUND_BUSY = 0.9  # Docling's process busy this share of the run's span: it is the cap, not the indexing process


def peak_cores(series: list[tuple[float, float]], window_s: float = PEAK_WINDOW_S) -> float | None:
    """Highest average cores over any stretch of at least *window_s* seconds, from ``(time_s, cumulative_cpu_s)``."""
    best: float | None = None
    j = 0
    for i, (t0, c0) in enumerate(series):
        j = max(j, i + 1)
        while j < len(series) and series[j][0] - t0 < window_s:
            j += 1
        if j >= len(series):
            break
        dt, dc = series[j][0] - t0, series[j][1] - c0
        if dt > 0 and dc >= 0:
            best = max(best or 0.0, dc / dt)
    return best


def attribute_knee_point(docling: dict[str, Any] | None, indexing: dict[str, Any] | None) -> dict[str, Any]:
    """Which component capped one knee run: Docling's process or the indexing process.

    Inputs are ``ProcessCpuSampler.finish()`` reports over the whole burst. ``bound`` is ``docling`` when
    Docling's process was busy at least ``DOCLING_BOUND_BUSY`` of the span (a tier that adds CPUs cannot add
    throughput past one Docling process), ``not_docling`` when it had slack, and ``unknown`` when either
    process was not seen or read at least twice: an unknown point is never used for the knee.
    """
    def usable(d: dict[str, Any] | None) -> bool:
        return bool(d and d.get("pids") and d.get("readings", 0) >= 2 and d.get("span_s", 0) > 0)

    out: dict[str, Any] = {"docling": docling, "indexing": indexing}
    if not (usable(docling) and usable(indexing)):
        return {**out, "bound": "unknown", "docling_busy_fraction": None, "indexing_cores": None}
    assert docling and indexing
    frac = docling["busy_s"] / docling["span_s"]
    return {**out, "docling_busy_fraction": round(frac, 3),
            "indexing_cores": round(indexing["cpu_s"] / indexing["busy_s"], 3) if indexing["busy_s"] > 0 else 0.0,
            "bound": "docling" if frac >= DOCLING_BOUND_BUSY else "not_docling"}


def cores_per_unit(cpu_s: float, busy_s: float) -> float | None:
    """Cores a family's own process used while it was busy; None when it never was (not measured)."""
    if busy_s <= 0:
        return None
    return cpu_s / busy_s


def weights_table(
    measured: dict[str, float | None], expected: dict[str, float] = EXPECTED_CORES, tol: float = WEIGHT_TOLERANCE
) -> dict[str, dict[str, Any]]:
    """Measured against designed cores per family. Off by more than *tol* opens a plan change.

    A family with no measurement stays at its design value: ``plan_change`` is False and ``measured`` None.
    """
    table: dict[str, dict[str, Any]] = {}
    for family, want in expected.items():
        got = measured.get(family)
        off = None if got is None else (got - want) / want
        table[family] = {
            "expected": want, "measured": None if got is None else round(got, 3),
            "off": None if off is None else round(off, 3),
            "plan_change": off is not None and abs(off) > tol,
            **({"not_measurable": NOT_MEASURABLE_FAMILIES[family]} if family in NOT_MEASURABLE_FAMILIES else {}),
        }
    return table


def untracked_cpu(container_cpu_s: float, wall_s: float, tracked_unit_cpu_s: float) -> float | None:
    """``U``: cores the container used that no counted unit of work accounts for."""
    if wall_s <= 0:
        return None
    return max(0.0, (container_cpu_s - tracked_unit_cpu_s) / wall_s)


def docling_s_d(busy_s: float, pages: int) -> float | None:
    """Docling seconds per page at one worker: its busy time over the pages it converted."""
    if pages <= 0 or busy_s <= 0:
        return None
    return busy_s / pages


def knee(points: dict[int, float | None], base_tier: int = 8, efficiency: float = 0.85) -> dict[str, Any]:
    """``CELL_CPUS``: the largest tier whose throughput is at least ``efficiency`` times linear from *base_tier*,
    walking up the tiers and stopping at the first one that falls short (a later recovery is noise, not scaling).

    *points* maps tier CPUs to throughput of a **valid** run (invalid ones are left out or None). The answer
    is a multiple of 4. ``confidence``:

    * ``high``: every tier has a point, the curve never rises after the knee, and a tier above the knee
      fell short, so the knee is bracketed;
    * ``medium``: the highest tier still passed, so the knee is at least that (a lower bound);
    * ``low``: a tier is missing, or the curve fell short and then recovered, or the knee is the base tier.
    """
    base = points.get(base_tier)
    if not base:
        return {"cell_cpus": None, "confidence": None, "reason": f"no valid point at T{base_tier}", "curve": {}}
    curve: dict[int, float] = {}
    best, stopped_at = base_tier, None
    missing = sorted(t for t, v in points.items() if v is None and t > base_tier)
    for tier in sorted(points):
        value = points[tier]
        if value is None or tier < base_tier:
            continue
        ratio = value / (base * tier / base_tier)
        curve[tier] = round(ratio, 3)
        if stopped_at is None:
            if ratio >= efficiency:
                best = max(best, tier)
            elif tier > base_tier:
                stopped_at = tier
    recovered = stopped_at is not None and any(r >= efficiency for t, r in curve.items() if t > stopped_at)
    if missing or recovered or best == base_tier:
        confidence = "low"
    elif stopped_at is None:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "cell_cpus": (best // 4) * 4, "confidence": confidence, "curve": curve, "efficiency": efficiency,
        "base_tier": base_tier, "first_short_tier": stopped_at, "missing_tiers": missing,
        "recovered_after_short": recovered,
    }


def _tier_of(result: dict[str, Any]) -> int | None:
    return (result.get("environment", {}).get("tier") or {}).get("cpus")


def _knee_exclusion(r: dict[str, Any]) -> str | None:
    """Why a result cannot be a knee point, or None."""
    if r.get("plan", {}).get("scenario") != "b06-knee":
        return f"scenario {r.get('plan', {}).get('scenario')!r}, not b06-knee"
    if r.get("environment", {}).get("indexing_processes") != 1:
        return f"indexing processes {r.get('environment', {}).get('indexing_processes')!r}, not 1"
    if r.get("environment", {}).get("capacity_profile") != "legacy":
        return f"capacity profile {r.get('environment', {}).get('capacity_profile')!r}, not legacy"
    if (r.get("knee_attribution") or {}).get("bound", "unknown") == "unknown":
        return "no process CPU recorded for Docling and the indexing process: the cap cannot be attributed"
    if r["verdict"]["status"] != "pass":
        return f"run {r['verdict']['status']}"
    if r["metrics"].get("records_per_minute") is None:
        return "no throughput"
    return None


def knee_from_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """``CELL_CPUS`` from the result files of a ``b06-knee`` run over the tiers.

    The files may be in any order and a tier may have several repeats. Per tier, the point is the **median**
    throughput of its valid repeats that Docling did not cap. A repeat that Docling capped measures Docling,
    not the indexing process: it is left out of the median and listed under ``docling_capped_repeats``. A tier
    whose valid repeats were all capped stays a (Docling-bound) point, so the whole knee is reported as
    Docling-bound. Only results that passed, ran scenario ``b06-knee`` with exactly one indexing process, and
    share one corpus make a point; anything else is listed as excluded and costs confidence. Without a T8
    point there is no knee, and nothing is reported.
    """
    excluded: list[dict[str, Any]] = []
    corpora = {r["plan"]["corpus_id"] for r in results}
    valid: dict[int, list[dict[str, Any]]] = {}
    seen: set[int] = set()
    for r in results:
        tier = _tier_of(r)
        why = _knee_exclusion(r)
        if tier is None:
            excluded.append({"tier": None, "why": why or "no tier"})
            continue
        seen.add(tier)
        if why:
            excluded.append({"tier": tier, "why": why})
        else:
            valid.setdefault(tier, []).append(r)
    points: dict[int, float | None] = {t: None for t in seen}
    repeats: dict[int, dict[str, Any]] = {}
    attribution: dict[int, dict[str, Any]] = {}
    capped_repeats: list[dict[str, Any]] = []
    capped_tiers: list[int] = []
    for tier, runs in valid.items():
        capped = [r for r in runs if r["knee_attribution"]["bound"] == "docling"]
        usable = [r for r in runs if r not in capped] or capped
        if usable is not capped:
            for r in capped:
                capped_repeats.append({"tier": tier, "records_per_minute": r["metrics"]["records_per_minute"]})
        else:
            capped_tiers.append(tier)
        rpms = sorted(r["metrics"]["records_per_minute"] for r in usable)
        points[tier] = statistics.median(rpms)
        repeats[tier] = {"runs": len(runs), "used": len(usable), "docling_capped": len(capped),
                         "values": rpms, "median": points[tier]}
        # the attribution of the repeat nearest the median (the lower one on a tie), whatever the file order
        nearest = min(usable, key=lambda r: (abs(r["metrics"]["records_per_minute"] - points[tier]),
                                             r["metrics"]["records_per_minute"]))
        attribution[tier] = nearest["knee_attribution"]
    out = knee(points)
    out["excluded"] = sorted(excluded, key=lambda e: (-1 if e["tier"] is None else e["tier"], e["why"]))
    out["repeats"] = {t: repeats[t] for t in sorted(repeats)}
    out["docling_capped_repeats"] = sorted(capped_repeats, key=lambda c: (c["tier"], c["records_per_minute"]))
    out["attribution"] = {t: {k: a.get(k) for k in ("bound", "docling_busy_fraction", "indexing_cores")}
                          for t, a in sorted(attribution.items())}
    capped_tiers.sort()
    if out.get("cell_cpus") is not None and capped_tiers:
        # Docling's process was the cap at these tiers, so the curve measures it, not the indexing process.
        out.update(cell_cpus=None, confidence=None, bound="docling", docling_bound_tiers=capped_tiers,
                   reason=f"Docling's process was busy at least {DOCLING_BOUND_BUSY:.0%} of the run at "
                          f"T{', T'.join(map(str, capped_tiers))}: the knee is Docling-bound, not the indexing process's")
    if len(corpora) > 1:
        out["confidence"] = "low" if out.get("confidence") else None
        out["note"] = "the results do not share one corpus"
    # Only a high-confidence knee is recorded as a decision (judged after every confidence change); anything else is a note.
    out["record_as_decision"] = out.get("cell_cpus") is not None and out.get("confidence") == "high"
    return out


def sensitivity(a: float | None, b: float | None, minimum: float = 0.30) -> dict[str, Any]:
    """Do two tiers' throughput differ by at least *minimum* (of the larger)?"""
    if not a or not b:
        return {"ok": False, "difference": None}
    diff = abs(b - a) / max(a, b)
    return {"ok": diff >= minimum, "difference": round(diff, 3)}


MIN_BASELINE_REPEATS = 3
BASELINE_MAX_CV = 0.10


def _baseline_group_key(r: dict[str, Any]) -> tuple[Any, ...]:
    env, tier = r.get("environment", {}), r.get("environment", {}).get("tier") or {}
    plan = r.get("plan", {})
    notes = plan.get("notes") or {}
    # the offered load is part of the identity: repeats at different rates are not repeats
    return (plan.get("scenario"), tier.get("cpus"), tier.get("mem_gb"), env.get("embedding_mode"),
            plan.get("corpus_id"), plan.get("arrivals"), notes.get("rate_per_s"), notes.get("duration_s"),
            notes.get("burst_count"))


def baseline_from_results(results: list[dict[str, Any]], *, max_cv: float = BASELINE_MAX_CV,
                          min_repeats: int = MIN_BASELINE_REPEATS, expected_repeats: int | None = None) -> dict[str, Any]:
    """Whether result files are a valid baseline: at least *min_repeats* passing repeats of one scenario and tier,
    and the p50 of every kind (and, for a saturation burst, the throughput) steady across them, CV at most *max_cv*.

    The files may be in any order. Anything else marks the baseline **invalid** with the reasons; only a valid
    one is committed. When the files hold two tiers of a saturation burst, the throughput of the lowest and
    the highest tier must also differ by at least the sensitivity minimum .
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in results:
        groups.setdefault(_baseline_group_key(r), []).append(r)
    reasons: list[str] = []
    out_groups: list[dict[str, Any]] = []
    by_tier: list[tuple[int, float | None]] = []
    for key in sorted(groups, key=lambda k: tuple("" if v is None else str(v) for v in k)):
        runs = groups[key]
        name = f"{key[0]} at {key[1]} CPU / {key[2]} GB ({key[3]} embedding)"
        load = {k: v for k, v in zip(("arrivals", "rate", "duration", "burst"), key[5:], strict=True) if v is not None}
        if load:
            name += " " + ", ".join(f"{k} {v}" for k, v in load.items())
        problems: list[str] = []
        not_pass = sorted(r["verdict"]["status"] for r in runs if r["verdict"]["status"] != "pass")
        if not_pass:
            problems.append(f"{len(not_pass)} repeat(s) did not pass: {', '.join(not_pass)}")
        if len(runs) < min_repeats:
            problems.append(f"{len(runs)} repeat(s), at least {min_repeats} are needed")
        if expected_repeats and len(runs) < expected_repeats:
            problems.append(f"{expected_repeats - len(runs)} of the {expected_repeats} planned repeat(s) left no result")
        passing = [r for r in runs if r["verdict"]["status"] == "pass"]
        series: dict[str, list[float]] = {}
        for r in passing:
            for kind, row in (r["metrics"].get("by_kind") or {}).items():
                if row.get("p50") is not None:
                    series.setdefault(f"{kind} p50", []).append(float(row["p50"]))
            if str(key[0]).startswith("b06") and r["metrics"].get("records_per_minute") is not None:
                series.setdefault("throughput", []).append(float(r["metrics"]["records_per_minute"]))
        if not series:
            problems.append("no p50 or throughput to compare")
        cvs: dict[str, float | None] = {}
        for metric, values in sorted(series.items()):
            cvs[metric] = None if coefficient_of_variation(values) is None else round(coefficient_of_variation(values), 4)
            if len(values) < len(passing):
                problems.append(f"{metric} is missing in {len(passing) - len(values)} repeat(s)")
            elif not repeats_stable(values, max_cv):
                problems.append(f"{metric} varies too much across repeats: CV {cvs[metric]} > {max_cv}")
        out_groups.append({
            "group": name, "repeats": len(runs), "valid": not problems, "problems": problems, "cv": cvs,
            "median": {m: round(statistics.median(v), 3) for m, v in sorted(series.items())},
        })
        reasons += [f"{name}: {p}" for p in problems]
        if str(key[0]).startswith("b06") and key[1] is not None:
            by_tier.append((key[1], out_groups[-1]["median"].get("throughput")))
    sens: dict[str, Any] | None = None
    by_tier.sort(key=lambda row: row[0])
    if len({t for t, _ in by_tier}) >= 2:
        sens = sensitivity(by_tier[0][1], by_tier[-1][1])
        sens["tiers"] = [by_tier[0][0], by_tier[-1][0]]
        if not sens["ok"]:
            reasons.append(f"throughput at T{by_tier[0][0]} and T{by_tier[-1][0]} differs by "
                           f"{sens['difference']}, less than the 30 % the benchmark must be able to see")
    if not results:
        reasons.append("no result files")
    return {"baseline_valid": not reasons, "reasons": reasons, "groups": out_groups, "sensitivity": sens,
            "max_cv": max_cv, "min_repeats": min_repeats}


# ====================================================================================
# Pure: corpus access and chaos parsing
# ====================================================================================


def load_cost_corpus_module() -> Any:
    """The seeded generators in ``backend/python/tests/perf/parse_bench/corpus.py``, loaded by path.

    By path because this directory has its own ``corpus`` module (the indexing benchmark's).
    """
    path = _REPO / "backend" / "python" / "tests" / "perf" / "parse_bench" / "corpus.py"
    spec = importlib.util.spec_from_file_location("parse_bench_corpus", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["parse_bench_corpus"] = mod
    spec.loader.exec_module(mod)
    return mod


def indexing_workers_from_env(value: str | None) -> int:
    """The indexing worker processes the app will run, read as the product reads ``INDEXING_UVICORN_WORKERS``
    (unset or empty: 1). An unreadable value is -1, which is never one process."""
    if value is None or not value.strip():
        return 1
    try:
        return max(1, int(value.strip()))
    except ValueError:
        return -1


# ERROR lines of the product that are known and harmless, each with the reason. Anything else fails a run
# that asks for ``--fail-on-log-errors``.
LOG_ERROR_ALLOW: tuple[tuple[str, str], ...] = ()
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# The level as the product logs it: Python ("... - ERROR - ...", "[ERROR]", "ERROR:" at the line start, uvicorn's
# "ERROR:    msg"), the Node text format (colour codes around the level, stripped first) and JSON lines.
_ERROR_LINE = re.compile(
    r'(?:(?:^|[ \[|])(?:ERROR|CRITICAL)(?:$|[ \]|:])'
    r'|(?i:"level(?:name)?"\s*:\s*"(?:error|critical)"))',
)


def scan_error_lines(text: str, allow: tuple[tuple[str, str], ...] = LOG_ERROR_ALLOW) -> list[str]:
    """ERROR-level lines of a product log that no allow-list entry (regex, reason) explains."""
    patterns = [re.compile(rx) for rx, _reason in allow]
    clean = (_ANSI.sub("", ln) for ln in text.splitlines())
    return [ln.strip()[:300] for ln in clean
            if _ERROR_LINE.search(ln) and not any(p.search(ln) for p in patterns)]


def budget_violations(p95_s: float | None, wall_s: float, p95_limit: float | None, wall_limit: float | None) -> list[str]:
    """The smoke's budgets : p95 over every accepted probe and the run's wall time."""
    bad: list[str] = []
    if p95_limit is not None and (p95_s is None or p95_s > p95_limit):
        bad.append("no accepted latency to hold to the p95 budget" if p95_s is None
                   else f"p95 {p95_s:.0f}s > budget {p95_limit:.0f}s")
    if wall_limit is not None and wall_s > wall_limit:
        bad.append(f"wall {wall_s:.0f}s > budget {wall_limit:.0f}s")
    return bad


def parse_chaos(text: str | None) -> tuple[str, float] | None:
    """``kill-app-at=300`` -> ``("kill-app", 300.0)``."""
    if not text:
        return None
    m = re.fullmatch(r"(kill-app)-at=(\d+(?:\.\d+)?)", text.strip())
    if not m:
        raise ValueError(f"unknown --chaos {text!r}; use kill-app-at=<seconds>")
    return m.group(1), float(m.group(2))


def counters_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Per-record gateway call and cache-hit counts from the parse stage state, when this build writes them."""
    state = record.get("stageState") or record.get("stage_state") or {}
    parse = state.get("parse") if isinstance(state, dict) else None
    parse = parse if isinstance(parse, dict) else {}
    pick = lambda *keys: next((parse[k] for k in keys if isinstance(parse.get(k), (int, float))), None)
    return {
        "gateway_calls": pick("gatewayCalls", "gateway_calls"),
        "cache_hits": pick("cacheHits", "cache_hits"),
        "embed_wait_s": pick("embedWaitSeconds", "embed_wait_seconds"),
    }


def record_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """The timestamps and ids of ``GET /record/{id}``, whether the record is nested or flat."""
    rec = payload.get("record") if isinstance(payload.get("record"), dict) else payload
    return {
        "status": rec.get("indexingStatus"),
        "last_index_ms": rec.get("lastIndexTimestamp"),
        "created_ms": rec.get("createdAtTimestamp"),
        "virtual_record_id": rec.get("virtualRecordId"),
        **counters_from_record(rec),
    }


# ====================================================================================
# Live: containers, cgroups, samplers
# ====================================================================================


def docker(*cmd: str, timeout: int = 30, merge_stderr: bool = False) -> str | None:
    """Output of ``docker <cmd>``, or None on any failure. ``docker logs`` writes a container's stderr to
    its own stderr, so a log read passes ``merge_stderr``."""
    try:
        out = subprocess.run(["docker", *cmd], capture_output=True, text=True, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout + out.stderr if merge_stderr else out.stdout


def container_cgroup_dir(container: str) -> Path | None:
    cid = (docker("inspect", "-f", "{{.Id}}", container) or "").strip()
    for tpl in ("/sys/fs/cgroup/system.slice/docker-{}.scope", "/sys/fs/cgroup/docker/{}"):
        path = Path(tpl.format(cid))
        if cid and path.is_dir():
            return path
    return None


def read_cgroup(container: str, name: str, cache: dict[str, Path | None]) -> str | None:
    if container not in cache:
        cache[container] = container_cgroup_dir(container)
    base = cache[container]
    try:
        if base is not None:
            return (base / name).read_text()
    except OSError:
        cache[container] = None
    return docker("exec", container, "cat", f"/sys/fs/cgroup/{name}")


def cpu_limit(cpuset: str, nano_cpus: int) -> float:
    """The CPUs a container may use: its cpuset, its ``--cpus`` limit, or the smaller of the two.

    0 when it has neither: a container that floats over the host has no limit to measure a share against,
    and dividing by the host's CPUs would make a saturated one look idle. The caller treats 0 as
    unmeasured, which invalidates the run.
    """
    sets = parse_cpuset(cpuset)
    nano = nano_cpus / 1e9 if nano_cpus > 0 else 0.0
    limits = [x for x in (float(sets), nano) if x > 0]
    return min(limits) if limits else 0.0


def container_cpuset(container: str) -> float:
    out = docker("inspect", "-f", "{{.HostConfig.CpusetCpus}}|{{.HostConfig.NanoCpus}}", container) or "|0"
    cpus, _, nano = out.strip().partition("|")
    return cpu_limit(cpus, int(nano) if nano.lstrip("-").isdigit() else 0)


class Sampler(threading.Thread):
    """Every ``interval`` seconds: merged ``/health``, connection counts, app cgroup counters, sibling CPU."""

    def __init__(self, base_url: str, app: str | None, siblings: dict[str, int], interval: float,
                 redis: str | None, neo4j: str | None, postgres: str | None) -> None:
        super().__init__(daemon=True)
        self.base_url, self.app, self.siblings, self.interval = base_url, app, siblings, interval
        self.redis, self.neo4j, self.postgres = redis, neo4j, postgres
        self.cache: dict[str, Path | None] = {}
        self.app_cpu: list[tuple[float, int]] = []
        self.sibling_cpu: dict[str, list[tuple[float, int]]] = {k: [] for k in siblings}
        self.health: list[dict[str, Any]] = []
        self.peaks: dict[str, float | None] = {"redis_clients": None, "neo4j_connections": None, "pg_backends": None,
                                               "memory_peak_bytes": None}
        self.oom_kills = 0
        self.throttled_usec = 0
        self.redis_maxclients: int | None = None
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            self.sample()
            self._halt.wait(self.interval)
        self.sample()

    def stop(self) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout=60)

    def _peak(self, key: str, value: float | None) -> None:
        if value is not None:
            self.peaks[key] = max(self.peaks[key] or 0.0, value)

    def sample(self) -> None:
        now = time.time()
        if self.app:
            stat = parse_cpu_stat(read_cgroup(self.app, "cpu.stat", self.cache) or "")
            if "usage_usec" in stat:
                self.app_cpu.append((now, stat["usage_usec"]))
                self.throttled_usec = stat.get("throttled_usec", self.throttled_usec)
            peak = (read_cgroup(self.app, "memory.peak", self.cache) or "").strip()
            self._peak("memory_peak_bytes", float(peak) if peak.isdigit() else None)
            events = parse_cpu_stat(read_cgroup(self.app, "memory.events", self.cache) or "")
            self.oom_kills = max(self.oom_kills, events.get("oom_kill", 0))
        for name in self.siblings:
            stat = parse_cpu_stat(read_cgroup(name, "cpu.stat", self.cache) or "")
            if "usage_usec" in stat:
                self.sibling_cpu[name].append((now, stat["usage_usec"]))
        self._connections()
        self._health(now)

    def _health(self, now: float) -> None:
        import requests

        try:
            body = requests.get(f"{self.base_url}/api/v1/health/services", timeout=10).json()
            self.health.append({"t": round(now, 1), "status": body.get("status"),
                                "services": {k: (v if isinstance(v, str) else (v or {}).get("status"))
                                             for k, v in (body.get("services") or {}).items()}})
        except Exception as exc:  # noqa: BLE001 - a missed sample is data
            self.health.append({"t": round(now, 1), "error": str(exc)[:80]})

    def _connections(self) -> None:
        if self.redis:
            info = docker("exec", self.redis, "redis-cli", "INFO", "clients") or ""
            m = re.search(r"connected_clients:(\d+)", info)
            self._peak("redis_clients", float(m.group(1)) if m else None)
            if self.redis_maxclients is None:
                cfg = (docker("exec", self.redis, "redis-cli", "CONFIG", "GET", "maxclients") or "").split()
                self.redis_maxclients = int(cfg[-1]) if cfg and cfg[-1].isdigit() else None
        if self.neo4j:
            out = docker("exec", self.neo4j, "cypher-shell", "-u", "neo4j", "-p",
                         os.getenv("NEO4J_PASSWORD", "your_password"),
                         "CALL dbms.listConnections() YIELD connectionId RETURN count(*) AS n") or ""
            m = re.search(r"^\s*(\d+)\s*$", out, re.MULTILINE)
            self._peak("neo4j_connections", float(m.group(1)) if m else None)
        if self.postgres:
            out = docker("exec", self.postgres, "psql", "-U", os.getenv("POSTGRES_USER", "postgres"), "-tAc",
                         "select count(*) from pg_stat_activity") or ""
            self._peak("pg_backends", float(out.strip()) if out.strip().isdigit() else None)


# ====================================================================================
# Live: the run
# ====================================================================================


def _now_ms() -> float:
    return time.time() * 1000.0


def parse_invalid_scopes(message: str) -> list[str]:
    """Scopes a stack refused, from the OAuth-app error (``"invalidScopes":["a","b"]``)."""
    m = re.search(r'"invalidScopes"\s*:\s*\[([^\]]*)\]', message)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def ensure_credentials(base_url: str, attempts: int = 3) -> list[str]:
    """Client credentials for the stack. A stack built from an older checkout may not know every scope the
    suite asks for: those are dropped (and returned, to be reported) instead of failing the run."""
    import local_auth

    dropped: list[str] = []
    for _ in range(attempts):
        try:
            ensure_client_credentials(base_url)
            return dropped
        except RuntimeError as exc:
            bad = parse_invalid_scopes(str(exc))
            if not bad:
                raise
            needed = sorted(set(bad) & REQUIRED_SCOPES)
            if needed:  # the run cannot work without these: say so now, not as failed uploads an hour later
                raise RuntimeError(f"the stack refused scopes this benchmark needs: {', '.join(needed)}") from exc
            dropped += bad
            local_auth.TEST_CLIENT_SCOPES = tuple(x for x in local_auth.TEST_CLIENT_SCOPES if x not in bad)
    ensure_client_credentials(base_url)
    return dropped


def register_fake_ai(client: Any, endpoint: str, fake_embeddings: bool) -> list[Any]:
    """Add the fake provider as the org's multimodal chat model (and embedding model for the control)."""
    from helper import ai_models_setup as ams

    seeded = []
    llm = ams._ProviderCandidate("openAICompatible", "fake-chat", {"endpoint": endpoint, "apiKey": "fake", "model": "fake-chat"})
    seeded.append(ams._post_provider_model(
        client, candidate=llm, model_type="llm", is_reasoning=False, is_multimodal=True, is_default=True))
    if fake_embeddings:
        emb = ams._ProviderCandidate(
            "openAICompatible", "fake-embedding", {"endpoint": endpoint, "apiKey": "fake", "model": "fake-embedding"})
        seeded.append(ams._post_provider_model(
            client, candidate=emb, model_type="embedding", is_reasoning=False, is_multimodal=False, is_default=True))
    return seeded


def corpus_templates(manifest: Any, corpus_dir: Path) -> tuple[dict[str, list[str]], dict[str, Any]]:
    by_kind: dict[str, list[str]] = {}
    entries: dict[str, Any] = {}
    for e in manifest.entries:
        by_kind.setdefault(e.kind, []).append(e.path)
        entries[e.path] = e
    return by_kind, entries


class Run:
    """State of one live run, shared by the scheduler and the pollers."""

    def __init__(self) -> None:
        self.samples: dict[int, Sample] = {}
        self.names: dict[str, int] = {}
        self.lock = threading.Lock()
        self.status_by_record: dict[str, str] = {}
        self.names_seen: dict[str, list[str]] = {}
        self.poll_errors = 0
        self.detailed: set[int] = set()
        self._final: dict[int, threading.Event] = {}

    def final_event(self, seq: int) -> threading.Event:
        """Set once the upload *seq* has reached a final status (and its details were read)."""
        with self.lock:
            return self._final.setdefault(seq, threading.Event())


def poll_once(kb_client: Any, kb_id: str, run: Run) -> None:
    page = 1
    seen_names: dict[str, list[str]] = {}
    status: dict[str, str] = {}
    while True:
        try:
            body = kb_client.list_records(kb_id, page=page, limit=200)
        except Exception:  # noqa: BLE001
            run.poll_errors += 1
            return
        for rec in body.get("items") or []:
            if rec.get("id"):
                status[rec["id"]] = rec.get("indexingStatus") or "UNKNOWN"
                seen_names.setdefault(rec.get("name") or rec.get("recordName") or "", []).append(rec["id"])
        if page >= ((body.get("pagination") or {}).get("totalPages") or 1):
            break
        page += 1
    with run.lock:
        run.status_by_record.update(status)
        run.names_seen = seen_names


def execute(args: argparse.Namespace) -> dict[str, Any]:
    """Run one benchmark. The generated corpus is removed when the run ends, whatever its outcome."""
    run_id = uuid.uuid4().hex[:8]
    tmp = Path(os.getenv("TMPDIR") or "/tmp") / f"costclass-{run_id}"
    try:
        return _execute(args, run_id, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _execute(args: argparse.Namespace, run_id: str, tmp: Path) -> dict[str, Any]:
    from pipeshub_client import PipeshubClient

    from helper.clients.kb_client import KBClient

    cc = load_cost_corpus_module()
    base_url = (args.base_url or os.getenv("PIPESHUB_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise SystemExit("Set PIPESHUB_BASE_URL or pass --base-url")
    chaos_spec = parse_chaos(args.chaos)
    if chaos_spec and not args.app_container:
        raise SystemExit("--chaos needs --app-container")
    os.environ["PIPESHUB_BASE_URL"] = base_url
    args.dropped_scopes = ensure_credentials(base_url)
    stack = check_stack(args)

    profile = args.profile or build_plan(args.scenario).profile
    manifest = cc.generate(tmp, seed=args.seed, profile=profile, scale=args.corpus_scale, max_mb=args.max_mb)
    templates, entries = corpus_templates(manifest, tmp)
    plan = build_plan(
        args.scenario, tier_cpus=args.tier_cpus, seed=args.seed, duration_s=args.duration, rate_per_s=args.rate,
        burst_count=args.burst, available=templates, inject_duplicate=args.inject_duplicate,
        calibrate_units=args.calibrate_units,
    )

    client = PipeshubClient(base_url=base_url, timeout_seconds=args.request_timeout)
    kb_client = KBClient(client)
    seeded = register_fake_ai(client, args.fake_ai_endpoint, args.fake_embeddings) if args.fake_ai_endpoint else []
    org_models = describe_org_models(client)
    siblings = {n: container_cpuset(n) for n in (args.siblings.split(",") if args.siblings else [])}
    sampler = Sampler(base_url, args.app_container, siblings, args.sample_interval, args.redis_container,
                      args.neo4j_container, args.postgres_container)
    restarts_before = _restart_count(args.app_container)
    chaos = ChaosKill(args.app_container, chaos_spec[1]) if chaos_spec else None
    # A knee run records which process capped it: Docling's, or the indexing process .
    knee_watch = knee_watchers(args)
    knee_attribution: dict[str, Any] | None = None
    run = Run()
    kb_id: str | None = None
    started_at = datetime.now(UTC)
    wall_end = 0.0
    sequential_log: list[dict[str, Any]] = []
    try:
        kb_id = create_kb(kb_client, f"perf-costclass-{run_id}")
        sampler.start()
        for w in knee_watch.values():
            w.start()
        t0 = time.time()
        if chaos:
            chaos.start()
        if plan.notes.get("sequential"):
            sequential_log = _run_sequential(plan, kb_client, kb_id, run, tmp, entries, run_id, t0, args)
        else:
            _run_open_loop(plan, kb_client, kb_id, run, tmp, entries, run_id, t0, args)
        wall_end = time.time()
    finally:
        if chaos:
            chaos.cancel()
        if knee_watch:
            reports = {name: w.finish() for name, w in knee_watch.items()}
            knee_attribution = attribute_knee_point(reports["docling"], reports["indexing"])
        sampler.stop()
        if kb_id and not args.keep_kb:
            try:
                kb_client.delete_kb(kb_id)
            except Exception as exc:  # noqa: BLE001
                print(f"warning: could not delete KB {kb_id}: {exc}", file=sys.stderr)
        from helper import ai_models_setup as ams

        for model in seeded:
            try:
                (ams.teardown_test_embedding_model if model.model_type == "embedding" else ams.teardown_test_llm_model)(
                    client, model)
            except Exception as exc:  # noqa: BLE001
                print(f"warning: could not remove fake model: {exc}", file=sys.stderr)
    restarts = _restart_count(args.app_container) - restarts_before
    restarts += chaos_restart_count(chaos)
    log_errors = fetch_log_errors(args.app_container, started_at.isoformat(timespec="seconds")) \
        if args.fail_on_log_errors else []
    if args.inject_duplicate and args.app_container:  # the product's dedup decisions, as evidence for the control
        text = docker("logs", "--since", started_at.isoformat(timespec="seconds"), args.app_container,
                      timeout=180, merge_stderr=True) or ""
        stack["dedup_log"] = [ln.strip()[:300] for ln in text.splitlines()
                              if re.search(r"(?i)duplicate|md5", ln)][:100]
    return build_result(
        args, plan, manifest, run, sampler, siblings, org_models, started_at, t0, wall_end, restarts,
        sequential_log, stack=stack, chaos_fired=bool(chaos and chaos.fired), log_errors=log_errors,
        knee_attribution=knee_attribution,
    )


def check_stack(args: argparse.Namespace) -> dict[str, Any]:
    """What the stack under test really is, read from it: the indexing process count and the fake provider's
    configuration. A variant that was asked for and is not there stops the run instead of being a label."""
    stack: dict[str, Any] = {"indexing_processes": None, "fake_ai": None, "family_processes": None,
                             "capacity_profile": None, "docling_workers": None, "omp_threads": None}
    if args.app_container:
        top = docker("top", args.app_container, "-eo", "pid,ppid,args") or ""
        stack["family_processes"] = {f: len(family_pids(top, m)) for f, m in FAMILY_PROCESS.items()}
        stack["indexing_processes"] = workers_from_container(args.app_container, "INDEXING_UVICORN_WORKERS")
        profile = container_env(args.app_container, "PIPESHUB_CAPACITY_PROFILE")
        # Unset is the product's default, ``legacy``; the knee is one process of that, not of a sized ``auto`` plan.
        stack["capacity_profile"] = "unreadable" if profile is None else (profile.lower() or "legacy")
        stack["docling_workers"] = workers_from_container(args.app_container, "DOCLING_UVICORN_WORKERS")
        stack["omp_threads"] = container_env(args.app_container, "OMP_NUM_THREADS") or None
        if args.scenario in ("b06-knee", "calibrate") and stack["indexing_processes"] != 1:
            raise SystemExit(
                f"{args.scenario} needs one indexing process; the app container runs "
                f"{stack['indexing_processes']} (INDEXING_UVICORN_WORKERS). No knee can be reported from this stack.")
        if args.scenario == "calibrate":
            docling = stack["docling_workers"]
            if docling != 1:
                raise SystemExit(f"calibrate needs a single Docling worker; DOCLING_UVICORN_WORKERS gives {docling}.")
    asked = args.fake_ai_base_ms is not None or args.fake_embeddings
    if asked and not args.fake_ai_container:
        raise SystemExit("--fake-ai-base-ms and --fake-embeddings need --fake-ai-container, to check the fake took them")
    if args.fake_ai_container:
        observed = fake_ai_stats(args.fake_ai_container)
        cfg = (observed or {}).get("config")
        if cfg is None:
            raise SystemExit(f"could not read the fake provider's configuration from {args.fake_ai_container}")
        if args.fake_ai_base_ms is not None and float(cfg["base_ms"]) != float(args.fake_ai_base_ms):
            raise SystemExit(f"--fake-ai-base-ms {args.fake_ai_base_ms} was not applied: the fake runs at {cfg['base_ms']} ms")
        if args.fake_embeddings and not cfg["fake_embeddings"]:
            raise SystemExit("--fake-embeddings was not applied: the fake does not serve embeddings")
        stack["fake_ai"] = cfg
    return stack


def _restart_count(container: str | None) -> int:
    if not container:
        return 0
    out = (docker("inspect", "-f", "{{.RestartCount}}", container) or "0").strip()
    return int(out) if out.isdigit() else 0


def container_env(container: str | None, name: str) -> str | None:
    """One environment variable of a container, read from ``docker inspect`` (``Config.Env``).

    ``""`` when the variable is not set; **None when docker could not tell** (a wrong container name, a
    timeout, unparseable output). The two must differ: an unset worker count means one process, an unreadable
    one means the stack is unknown.
    """
    if not container:
        return None
    out = docker("inspect", "-f", "{{json .Config.Env}}", container)
    try:
        env = json.loads(out or "")
    except ValueError:
        return None
    if not isinstance(env, list):
        return None
    for item in env:
        key, _, value = str(item).partition("=")
        if key == name:
            return value.strip()
    return ""


def workers_from_container(container: str | None, name: str) -> int:
    """A worker-count variable of a container as a number; -1 (never one process) when it cannot be read."""
    value = container_env(container, name)
    return -1 if value is None else indexing_workers_from_env(value)


def fake_ai_stats(container: str | None) -> dict[str, Any] | None:
    """The fake provider's own report of its configuration, read from inside its container."""
    if not container:
        return None
    out = docker("exec", container, "python", "-c",
                 "import urllib.request as u; print(u.urlopen('http://localhost:8080/stats').read().decode())")
    try:
        return json.loads(out or "")
    except ValueError:
        return None


def fetch_log_errors(container: str | None, since_iso: str) -> list[str] | None:
    """The product's ERROR lines since *since_iso*. None when the log cannot be read or is empty: a check that
    saw nothing must not read as a clean one (the app logs at startup and per upload, so empty is wrong)."""
    if not container:
        return None
    text = docker("logs", "--since", since_iso, container, timeout=180, merge_stderr=True)
    if text is None or not text.strip():
        return None
    return scan_error_lines(text)


# Kills every process of the indexing service inside the app container and prints the container pids it hit.
# Docker never applies ``restart: on-failure`` to ``docker kill`` (a manual stop), and PID 1 ignores a signal from
# inside its own namespace, so the indexing process is the one to kill: the container's process monitor
# (``deployment/process_monitor.sh``) starts it again within its check interval. The pattern is written with a
# bracket so this script's own command line does not match it.
_KILL_INDEXING_SH = (
    "for d in /proc/[0-9]*; do p=${d#/proc/}; [ \"$p\" = \"$$\" ] && continue; "
    "tr '\\000' ' ' < $d/cmdline 2>/dev/null | grep -q 'app[.]indexing_mai[n]' && { echo $p; kill -9 $p; }; "
    "done; true")
INDEXING_MARKER = "indexing_main"
CHAOS_RECOVERY_WAIT_S = 300.0


def kill_indexing_process(container: str) -> list[int] | None:
    """SIGKILL the indexing service in *container*. The pids it hit, or None when docker could not run it."""
    out = docker("exec", container, "sh", "-c", _KILL_INDEXING_SH)
    if out is None:
        return None
    return [int(x) for x in out.split() if x.isdigit()]


class ChaosKill:
    """Kills the app's indexing process once, at a set time. A daemon timer, cancelled when the run ends first.

    ``fired`` means the kill hit a process. ``recovered()`` is true once an indexing process that was not
    there before the kill is running: the app restarted it.
    """

    def __init__(self, container: str, at_s: float, kill: Any = None, top: Any = None) -> None:
        self.container = container
        self.fired = False
        self.before: set[int] = set()
        self._kill = kill or kill_indexing_process
        self._top = top or (lambda: docker("top", container, "-eo", "pid,ppid,args") or "")
        self.timer = threading.Timer(at_s, self._fire)
        self.timer.daemon = True

    def _fire(self) -> None:
        self.before = set(family_pids(self._top(), INDEXING_MARKER))
        self.fired = bool(self._kill(self.container))

    def start(self) -> None:
        self.timer.start()

    def cancel(self) -> None:
        self.timer.cancel()

    def recovered(self, wait_s: float = 0.0, poll_s: float = 2.0, sleep: Any = time.sleep) -> bool:
        deadline = time.monotonic() + wait_s
        while True:
            now = set(family_pids(self._top(), INDEXING_MARKER))
            if self.fired and now and not (now & self.before):
                return True
            if time.monotonic() >= deadline:
                return False
            sleep(poll_s)


def chaos_restart_count(chaos: Any, sleep: Any = time.sleep) -> int:
    """1 when the kill fired and a new indexing process came up within the recovery wait, else 0."""
    return 1 if chaos and chaos.fired and chaos.recovered(wait_s=CHAOS_RECOVERY_WAIT_S, sleep=sleep) else 0


class ControlBytes:
    """The bytes of every upload, kept so a control can re-send its original's exactly.

    A control waits for its original's bytes instead of racing it: whichever worker starts first, the
    control carries the original's md5 (or fails visibly when the original never started).
    """

    def __init__(self) -> None:
        self._items: dict[int, tuple[bytes, str]] = {}
        self._events: dict[int, threading.Event] = {}
        self._lock = threading.Lock()

    def _event(self, seq: int) -> threading.Event:
        with self._lock:
            return self._events.setdefault(seq, threading.Event())

    def put(self, seq: int, data: bytes, name: str) -> None:
        self._items[seq] = (data, name)
        self._event(seq).set()

    def fail(self, seq: int) -> None:
        """The original has no bytes and never will: whoever waits for them is released with None."""
        self._event(seq).set()

    def get(self, seq: int, timeout: float) -> tuple[bytes, str] | None:
        return self._items.get(seq) if self._event(seq).wait(timeout) else None


def _upload_one(a: Arrival, kb_client: Any, kb_id: str, run: Run, tmp: Path, entries: dict[str, Any],
                run_id: str, t0: float, control_bytes: ControlBytes, control_wait_s: float = 600.0) -> Sample:
    entry = entries[a.template]
    scheduled_ms = (t0 + a.at_s) * 1000.0
    if a.role == "control":
        original = control_bytes.get(a.control_of, control_wait_s) if a.control_of is not None else None
        if original is None:
            sample = Sample(seq=a.seq, kind=a.kind, role=a.role, name=f"dup-{a.template}", md5=f"no-original-{a.seq}",
                            scheduled_arrival_ms=scheduled_ms, control=True, control_of=a.control_of,
                            upload_error="the original upload never started, so there are no bytes to re-send")
            with run.lock:
                run.samples[a.seq] = sample
            return sample
        data, name = original[0], f"dup-{original[1]}"
        # The product deduplicates against a twin whose md5 it has already stored, which a record does a
        # moment into its own indexing. Sent beside its original, the copy races it and both are indexed:
        # that says nothing about the dedup. So the copy goes after the original is final.
        waited = run.final_event(a.control_of).wait(control_wait_s)
        with run.lock:
            orig = run.samples.get(a.control_of)
        why = None
        if not waited:
            why = "the original did not reach a final status in time to be a duplicate target"
        elif orig is not None and orig.upload_error:  # a failed upload never gets a final status: do not wait for one
            why = f"the original's upload failed, so there is nothing to duplicate: {orig.upload_error[:120]}"
        if why:
            sample = Sample(seq=a.seq, kind=a.kind, role=a.role, name=name, md5=hashlib.md5(data).hexdigest(),
                            scheduled_arrival_ms=scheduled_ms, control=True, control_of=a.control_of, upload_error=why)
            with run.lock:
                run.samples[a.seq] = sample
            run.final_event(a.seq).set()
            return sample
    else:
        raw = (tmp / entry.path).read_bytes()
        data = salt.salt_bytes(raw, entry.ext, salt.salt_token(run_id, a.seq))
        name = f"{a.seq:05d}-{entry.path}"
        control_bytes.put(a.seq, data, name)
    sample = Sample(
        seq=a.seq, kind=a.kind, role=a.role, name=name, md5=hashlib.md5(data).hexdigest(),
        scheduled_arrival_ms=scheduled_ms, control=a.role == "control", control_of=a.control_of,
    )
    with run.lock:
        run.samples[a.seq] = sample
    sample.send_ms = _now_ms()
    try:
        result = kb_client.upload_file(kb_id, name, data, mimetype=entry.mime)
        records = result.get("records") or []
        sample.record_count = len(records)
        if records:
            sample.record_id = records[0].get("recordId") or records[0].get("id")
        if not records or result.get("failed"):
            sample.upload_error = json.dumps(result.get("failed"))[:200] or "no record returned"
    except Exception as exc:  # noqa: BLE001
        sample.upload_error = str(exc)[:200]
    if sample.upload_error:
        run.final_event(a.seq).set()  # it will never get a final status; whoever waits on it must not wait for one
    return sample


def account_unstarted(plan: Plan, run: Run, t0: float) -> list[Sample]:
    """A sample for every planned arrival that never started (still queued in the client pool at the
    deadline), so the run counts it as a failed upload instead of losing it. The result then fails."""
    added: list[Sample] = []
    with run.lock:
        for a in plan.arrivals:
            if a.seq not in run.samples:
                sample = Sample(
                    seq=a.seq, kind=a.kind, role=a.role, name=f"unstarted-{a.seq}", md5=f"unstarted-{a.seq}",
                    scheduled_arrival_ms=(t0 + a.at_s) * 1000.0, control=a.role == "control",
                    control_of=a.control_of, upload_error="not started before the deadline")
                run.samples[a.seq] = sample
                added.append(sample)
    return added


def _fetch_details(kb_client: Any, sample: Sample, listed_status: str | None = None) -> bool:
    """Read the record. The sample's status is the *record's* confirmed status (the listing's only when the read
    omits one); a failed read changes nothing, so a listing's COMPLETED never stands in for a read that failed."""
    try:
        fields = record_fields(kb_client.get_record(sample.record_id))
    except Exception:  # noqa: BLE001
        return False
    sample.status = fields["status"] or listed_status or sample.status
    sample.last_index_ms = fields["last_index_ms"]
    sample.created_ms = fields["created_ms"]
    sample.virtual_record_id = fields["virtual_record_id"]
    sample.gateway_calls, sample.cache_hits, sample.embed_wait_s = (
        fields["gateway_calls"], fields["cache_hits"], fields["embed_wait_s"])
    return sample.status in FINAL_STATUSES


def _upload_guarded(a: Arrival, kb_client: Any, kb_id: str, run: Run, tmp: Path, entries: dict[str, Any],
                    run_id: str, t0: float, control_bytes: ControlBytes, control_wait_s: float = 600.0) -> Sample:
    """``_upload_one``, but an exception in the harness (a template it cannot read, a salt it refuses) is
    recorded as that upload's error, with its cause, instead of leaving the probe looking never started."""
    try:
        return _upload_one(a, kb_client, kb_id, run, tmp, entries, run_id, t0, control_bytes, control_wait_s)
    except Exception as exc:  # noqa: BLE001
        why = f"the harness failed this upload before it was sent: {type(exc).__name__}: {str(exc)[:160]}"
        with run.lock:
            sample = run.samples.get(a.seq)
            if sample is None:
                sample = Sample(seq=a.seq, kind=a.kind, role=a.role, name=f"harness-error-{a.seq}",
                                md5=f"harness-error-{a.seq}", scheduled_arrival_ms=(t0 + a.at_s) * 1000.0,
                                control=a.role == "control", control_of=a.control_of)
                run.samples[a.seq] = sample
            sample.upload_error = sample.upload_error or why
        run.final_event(a.seq).set()
        control_bytes.fail(a.seq)  # a control of this upload must not wait for bytes that never come
        return sample


def _run_open_loop(plan: Plan, kb_client: Any, kb_id: str, run: Run, tmp: Path, entries: dict[str, Any],
                   run_id: str, t0: float, args: argparse.Namespace) -> None:
    control_bytes = ControlBytes()
    pool = ThreadPoolExecutor(max_workers=args.upload_workers)
    futures = []
    try:
        for a in plan.arrivals:  # open loop: the next arrival never waits for an earlier file to finish
            delay = t0 + a.at_s - time.time()
            if delay > 0:
                time.sleep(delay)
            futures.append(pool.submit(_upload_guarded, a, kb_client, kb_id, run, tmp, entries, run_id, t0, control_bytes,
                                       max(1.0, args.timeout)))
        _wait_all(kb_client, kb_id, run, futures, t0 + args.timeout, args.poll_interval)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        account_unstarted(plan, run, t0)


def _wait_all(kb_client: Any, kb_id: str, run: Run, futures: list[Any], deadline: float, poll_s: float) -> None:
    detailed = run.detailed
    while time.time() < deadline:
        uploads_done = all(f.done() for f in futures)
        poll_once(kb_client, kb_id, run)
        with run.lock:
            samples = list(run.samples.values())
        pending = 0
        for s in samples:
            if s.upload_error or s.seq in detailed:
                continue
            status = run.status_by_record.get(s.record_id or "")
            # a failed or non-final detail read leaves the sample open: the next poll reads it again
            if status in FINAL_STATUSES and _fetch_details(kb_client, s, status):
                s.record_count = max(s.record_count, len(run.names_seen.get(s.name, [])) or s.record_count)
                detailed.add(s.seq)
                run.final_event(s.seq).set()
                continue
            if s.role != "load":  # background load is never waited for: the probes decide when the run is over
                pending += 1
        if uploads_done and pending == 0:
            return
        time.sleep(poll_s)


class ProcessCpuSampler(threading.Thread):
    """Cumulative CPU seconds of a family's own process tree, read from ``/proc/<pid>/stat`` every
    ``PROC_SAMPLE_S`` while a unit runs. Pids are looked up once per unit (``docker top``, host pids)."""

    def __init__(self, container: str, marker: str, read_stat: Any = None, top: Any = None,
                 refresh_s: float | None = None) -> None:
        super().__init__(daemon=True)
        self.container, self.marker = container, marker
        self.refresh_s = refresh_s  # a run-long watch looks the pids up again: pool children come and go
        self._read_stat = read_stat or (lambda pid: Path(f"/proc/{pid}/stat").read_text())
        self._top = top or (lambda: docker("top", container, "-eo", "pid,ppid,args"))
        self.pids: list[int] = []
        self._last: dict[int, int] = {}  # the last ticks read per pid
        self._acc_ticks = 0.0  # CPU ticks used since each pid was first seen: never shrinks when a pid exits
        self._seen = False
        self.series: list[tuple[float, float]] = []
        self.tick = float(os.sysconf("SC_CLK_TCK")) if hasattr(os, "sysconf") else 100.0
        self._halt = threading.Event()

    def _cpu_s(self) -> float | None:
        for pid in self.pids:
            try:
                ticks = process_cpu_ticks(self._read_stat(pid))
            except OSError:
                continue  # a pool child that has exited: what it used so far stays in the total
            if ticks is None:
                continue
            if pid in self._last:
                self._acc_ticks += max(0, ticks - self._last[pid])
            # else: a pid first seen now is a baseline, so CPU it used before it was found is not a spike
            self._last[pid] = ticks
            self._seen = True
        return self._acc_ticks / self.tick if self._seen else None

    def run(self) -> None:
        self.pids = family_pids(self._top() or "", self.marker)
        looked = time.time()
        while not self._halt.is_set():
            if self.refresh_s and time.time() - looked >= self.refresh_s:
                self.pids = sorted(set(self.pids) | set(family_pids(self._top() or "", self.marker)))
                looked = time.time()
            self._read_once()
            self._halt.wait(PROC_SAMPLE_S)
        self._read_once()

    def _read_once(self) -> None:
        cpu = self._cpu_s()
        if cpu is not None:
            self.series.append((time.time(), cpu))

    def finish(self) -> dict[str, Any]:
        self._halt.set()
        if self.is_alive():
            self.join(timeout=30)
        cpu_s, busy_s = busy_and_cpu(self.series)
        span = self.series[-1][0] - self.series[0][0] if len(self.series) >= 2 else 0.0
        peak = peak_cores(self.series)
        return {"pids": len(self.pids), "readings": len(self.series), "cpu_s": round(cpu_s, 3), "busy_s": round(busy_s, 3),
                "span_s": round(span, 3), "peak_cores": None if peak is None else round(peak, 3)}


def knee_watchers(args: argparse.Namespace) -> dict[str, ProcessCpuSampler]:
    """The run-long watches of a knee run: Docling's process and the indexing process (none for other scenarios)."""
    if args.scenario != "b06-knee" or not args.app_container:
        return {}
    return {"docling": ProcessCpuSampler(args.app_container, FAMILY_PROCESS["docling"], refresh_s=15.0),
            "indexing": ProcessCpuSampler(args.app_container, INDEXING_MARKER, refresh_s=15.0)}


def _run_sequential(plan: Plan, kb_client: Any, kb_id: str, run: Run, tmp: Path, entries: dict[str, Any],
                    run_id: str, t0: float, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Calibration: one unit at a time at a single worker, with the family's own process watched meanwhile."""
    log: list[dict[str, Any]] = []
    control_bytes = ControlBytes()
    for a in plan.arrivals:
        a.at_s = time.time() - t0
        family = a.role.split(":", 1)[1]
        # Docling's pool workers are spawned on a unit's first submit: look the pids up again while it runs.
        watch = ProcessCpuSampler(args.app_container or "", FAMILY_PROCESS[family], refresh_s=CALIBRATION_PID_REFRESH_S)
        watch.start()
        start = time.time()
        _upload_one(a, kb_client, kb_id, run, tmp, entries, run_id, t0, control_bytes)
        fut = type("Done", (), {"done": staticmethod(lambda: True)})()
        _wait_all(kb_client, kb_id, run, [fut], start + args.timeout, args.poll_interval)
        wall = time.time() - start
        log.append({"family": family, "kind": a.kind, "seq": a.seq, "wall_s": round(wall, 3),
                    "proc": watch.finish(), "pages": entries[a.template].pages})
        if a.seq not in run.detailed:
            # The unit is still open at its deadline: another upload would overlap it, and its CPU series is
            # already closed. Calibration stops and fails (the open unit times out, the rest count as unstarted).
            account_unstarted(plan, run, t0)
            break
    return log


def calibration_report(log: list[dict[str, Any]]) -> dict[str, Any]:
    """Cores per unit and ``s_d`` from each family's own process CPU . Whole-container CPU over a
    record's wall time is never used: it mixes every stage of the pipeline."""
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in log:
        by_family.setdefault(row["family"], []).append(row)
    measured: dict[str, float | None] = {}
    detail: dict[str, Any] = {}
    for family, rows in by_family.items():
        seen = [r for r in rows if (r.get("proc") or {}).get("pids") and r["proc"].get("readings", 0) >= 2]
        cpu = sum(r["proc"]["cpu_s"] for r in seen)
        busy = sum(r["proc"]["busy_s"] for r in seen)
        measured[family] = cores_per_unit(cpu, busy) if seen else None
        detail[family] = {
            "units": len(rows), "units_with_process": len(seen), "cpu_s": round(cpu, 3), "busy_s": round(busy, 3),
            "why_not_measured": None if measured[family] is not None else (
                "its process was not found in the container" if not seen else "the process never used CPU for a unit"),
        }
    docling = [r for r in by_family.get("docling", []) if (r.get("proc") or {}).get("busy_s")]
    return {
        "method": "utime+stime of the family's own process tree from /proc, sampled every "
                  f"{PROC_SAMPLE_S}s while one unit runs at a single worker; busy time = intervals at or above "
                  f"{BUSY_MIN_CORES} cores",
        "cores_per_unit": {k: None if v is None else round(v, 3) for k, v in measured.items()},
        "detail": detail,
        "weights": weights_table(measured),
        "not_measurable": dict(NOT_MEASURABLE_FAMILIES),
        "untracked_cpu_U": "not measurable: needs per-unit CPU from the pools (parse, inspect, raster)",
        "s_d": docling_s_d(sum(r["proc"]["busy_s"] for r in docling), sum(r["pages"] for r in docling)) if docling else None,
        "s_d_note": "Docling busy seconds per page at one worker (OMP threads at the stack's setting; the "
                    "OMP 1 versus 2 control is not run here)",
        "family_kind": FAMILY_KIND,
    }


def build_result(
    args: argparse.Namespace, plan: Plan, manifest: Any, run: Run, sampler: Sampler, siblings: dict[str, int],
    org_models: dict[str, str], started_at: datetime, t0: float, wall_end: float, restarts: int,
    sequential_log: list[dict[str, Any]], *, stack: dict[str, Any] | None = None, chaos_fired: bool = False,
    log_errors: list[str] | None = None,
    knee_attribution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    samples = list(run.samples.values())
    upload_failures = [s for s in samples if s.upload_error]
    # Background load still indexing when the run ends is not a probe: it is neither a timeout nor a rejection.
    inflight_load = [s for s in samples if s.role == "load" and not s.upload_error and s.status not in FINAL_STATUSES]
    inflight_ids = {id(s) for s in inflight_load}
    not_final = [s for s in samples if not s.upload_error and not s.control and s.status not in FINAL_STATUSES
                 and id(s) not in inflight_ids]
    accepted, rejected = filter_samples([s for s in samples if id(s) not in inflight_ids], now_ms=_now_ms())
    failed = [s for s in samples if s.status in FINAL_STATUSES and s.status != SUCCESS_STATUS and not s.control]
    probes = [s for s in accepted if s.role in ("probe", "load") or s.role.startswith("unit")]
    timed_out = [s for s in not_final if s.record_id]
    missing = [s for s in not_final if not s.record_id and not s.upload_error]
    sib = sibling_report(sampler.sibling_cpu, siblings)
    violations = check_limits(
        sampler.peaks, redis_maxclients=sampler.redis_maxclients, neo4j_budget=args.neo4j_connection_budget,
        pg_max_connections=args.pg_max_connections, oom_kills=sampler.oom_kills)
    control = judge_control(samples)
    wall = max(0.0, wall_end - t0)
    p95_all = summarise([x for x in (gated_latency_s(s) for s in accepted) if x is not None])["p95"]
    budget_failures = budget_violations(
        p95_all, wall, getattr(args, "max_p95_s", None), getattr(args, "max_wall_s", None))
    verdict = run_verdict(
        accepted=len(accepted), rejected_n=sum(len([s for s in v if not s.control]) for v in rejected.values()),
        failed=len(failed), missing=len(missing), timed_out=len(timed_out), upload_failures=len(upload_failures),
        saturated_siblings=sib["saturated"], limit_violations=violations, control_expected=args.inject_duplicate,
        control_rejected=bool(control and control["ok"]), restarts=restarts, unmeasured_siblings=sib["unmeasured"],
        control_detail=", ".join(f"{r['outcome']}" for r in (control or {}).get("controls", [])),
        chaos_expected=bool(getattr(args, "chaos", None)), chaos_fired=chaos_fired, log_errors=log_errors,
        log_unreadable=bool(getattr(args, "fail_on_log_errors", False)) and log_errors is None,
        budget_failures=budget_failures)
    stack = stack or {}
    last_ms = max((s.last_index_ms for s in accepted if s.last_index_ms), default=None)
    span = ((last_ms / 1000.0 - t0) if last_ms else wall) or 1.0
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "benchmark": "costclass",
        "label": args.label,
        "started_at": started_at.isoformat(timespec="seconds"),
        "environment": {
            "label": args.label, "graph_db": STACK_GRAPH_DB, "message_broker": STACK_BROKER,
            "store": STACK_GRAPH_DB, "source": "upload",
            "indexing_processes": stack.get("indexing_processes"),
            "capacity_profile": stack.get("capacity_profile"),
            "docling_workers": stack.get("docling_workers"), "omp_threads": stack.get("omp_threads"),
            "fake_ai": stack.get("fake_ai"),
            "family_processes_found": stack.get("family_processes"),
            "ai_models": org_models,
            "embedding_mode": "fake" if args.fake_embeddings else "real",
            "host_cpus": os.cpu_count(), "host_memory_gb": host_memory_gb(), "host_platform": platform.platform(),
            "git_sha": os.getenv("GITHUB_SHA") or run_command(["git", "rev-parse", "HEAD"]) or "",
            "dropped_oauth_scopes": getattr(args, "dropped_scopes", []),
            "tier": {"cpus": args.tier_cpus, "mem_gb": args.tier_mem_gb},
        },
        "knee_attribution": knee_attribution,
        "plan": {"scenario": args.scenario, "tier": f"{args.tier_cpus}cpu/{args.tier_mem_gb}gb", "seed": args.seed,
                 "profile": plan.profile, "corpus_id": manifest.corpus_id, "arrivals": len(plan.arrivals),
                 "notes": plan.notes},
        "metrics": {
            "wall_seconds": round(wall, 2),
            "records_per_minute": round(len(accepted) / (span / 60), 2) if accepted else None,
            "accepted": len(accepted),
            "rejected": {k: len(v) for k, v in sorted(rejected.items())},
            "rejected_ratio": None if not accepted and not rejected else round(
                min(rejected_ratio(accepted, rejected), 9.99), 4),
            "by_kind": per_kind_summary(accepted),
            "failures": {
                "total": len(failed) + len(timed_out) + len(missing) + len(upload_failures),
                "failed_status": len(failed), "timed_out": len(timed_out), "missing": len(missing),
                "upload": len(upload_failures),
                "reasons": [{"name": s.name, "status": s.status, "error": s.upload_error} for s in
                            (failed + timed_out + missing + upload_failures)][:50],
            },
            "log_errors": {"checked": bool(getattr(args, "fail_on_log_errors", False)), "count": len(log_errors or []),
                           "unreadable": bool(getattr(args, "fail_on_log_errors", False)) and log_errors is None,
                           "first": (log_errors or [])[:200]},
            "budgets": {"max_p95_s": getattr(args, "max_p95_s", None), "max_wall_s": getattr(args, "max_wall_s", None),
                        "p95_s": p95_all, "wall_s": round(wall, 2)},
            "negative_control": {"injected": args.inject_duplicate, "dedup_log": (stack or {}).get("dedup_log", []), "deduplicated": bool(control and control["ok"]),
                                 **(control or {"controls": []})},
            "gateway_diagnostics": {
                "available": any(s.gateway_calls is not None for s in samples),
                "calls": _sum_or_none([s.gateway_calls for s in probes]),
                "cache_hits": _sum_or_none([s.cache_hits for s in probes]),
            },
            "embed_wait_p95_small_s": summarise(
                [s.embed_wait_s for s in accepted if s.kind in SMALL_KINDS and s.embed_wait_s is not None])["p95"],
            "status_poll_errors": run.poll_errors,
        },
        "samples": [_sample_row(s, s in accepted) for s in sorted(samples, key=lambda x: x.seq)][:2000],
        "resources": {
            "siblings": {**sib, "cpusets": siblings},
            "connection_peaks": sampler.peaks, "redis_maxclients": sampler.redis_maxclients,
            "oom_kills": sampler.oom_kills, "app_throttled_usec": sampler.throttled_usec,
            "app_cpu_cores_avg": cpu_fraction(sampler.app_cpu, 1) and round(cpu_fraction(sampler.app_cpu, 1) or 0, 3),
            "health_samples": len(sampler.health),
            "health_not_healthy": sum(1 for h in sampler.health if h.get("status") not in ("healthy", "ok")),
        },
        "verdict": verdict,
        "not_measurable_here": list(NOT_MEASURABLE_HERE),
    }
    if sequential_log:
        result["calibration"] = calibration_report(sequential_log)
    return result


def _sample_row(s: Sample, accepted: bool) -> dict[str, Any]:
    lat = gated_latency_s(s)
    return {
        "seq": s.seq, "kind": s.kind, "role": s.role, "name": s.name, "status": s.status, "accepted": accepted,
        "md5": s.md5, "virtual_record_id": s.virtual_record_id, "record_id": s.record_id,
        "record_count": s.record_count, "control_of": s.control_of,
        "gated_latency_s": None if lat is None else round(lat, 3),
        **{k: (None if v is None else round(v, 3)) for k, v in diagnostic_latencies(s).items()},
        "gateway_calls": s.gateway_calls, "cache_hits": s.cache_hits,
    }


def render_summary(result: dict[str, Any]) -> str:
    m, v, p = result["metrics"], result["verdict"], result["plan"]
    lines = [
        f"### Cost-class benchmark: {p['scenario']} on {p['tier']} ({result['label']})",
        "",
        f"**{v['status'].upper()}**" + (": " + "; ".join(v["reasons"]) if v["reasons"] else ""),
        "",
        (f"{m['accepted']} accepted, rejected {m['rejected'] or 'none'}, throughput {m['records_per_minute']} "
         f"records/min, embedding {result['environment']['embedding_mode']}."),
        "",
        "| Kind | n | p50 s | p95 s | p99 s |", "| --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {k} | {x['n']} | {x['p50']} | {x['p95']} | {x['p99']} |" for k, x in m["by_kind"].items()]
    if v["disturbed"]:
        lines += ["", "**Disturbed:** the app restarted during the run."]
    if "calibration" in result:
        c = result["calibration"]
        lines += ["", "Calibration (per family process): " + json.dumps(c["cores_per_unit"]) + f", s_d {c['s_d']}. "
                  "Not measurable: " + ", ".join(c["not_measurable"]) + "."]
    lines += ["", "Not measurable here: " + "; ".join(result["not_measurable_here"]) + "."]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", choices=["smoke", "b01", "b02", "b03", "b05", "b06", "b06-knee", "calibrate"])
    ap.add_argument("--label")
    ap.add_argument("--knee-from", nargs="+", type=Path, default=None, metavar="RESULT",
                    help="no run: compute CELL_CPUS and its confidence from the b06-knee result files of the tiers")
    ap.add_argument("--expected-repeats", type=int, default=None,
                    help="with --baseline-from: the repeats the runner planned; fewer result files make the baseline invalid")
    ap.add_argument("--baseline-from", nargs="+", type=Path, default=None, metavar="RESULT",
                    help="no run: check that the repeats of a scenario are a valid baseline (3 or more passing runs, "
                         "p50 CV at most 10 percent); exit 3 and no baseline when not")
    ap.add_argument("--profile", default=None, help="corpus profile (default: the scenario's)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--corpus-scale", type=float, default=1.0)
    ap.add_argument("--max-mb", type=int, default=1024)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--tier-cpus", type=int, default=4)
    ap.add_argument("--tier-mem-gb", type=int, default=16)
    ap.add_argument("--duration", type=float, default=900.0, help="seconds of small-class probes")
    ap.add_argument("--rate", type=float, default=0.2, help="probe arrivals per second")
    ap.add_argument("--burst", type=int, default=60, help="files in a saturation burst")
    ap.add_argument("--calibrate-units", type=int, default=3)
    ap.add_argument("--fake-embeddings", action="store_true",
                    help="register the fake as the embedding model too (the control); needs --fake-ai-container")
    ap.add_argument("--fake-ai-endpoint", default=None, help="e.g. http://fake-ai:8080/v1 (as the stack sees it)")
    ap.add_argument("--fake-ai-container", default=None, help="the fake provider's container: its configuration is read and checked")
    ap.add_argument("--fake-ai-base-ms", type=float, default=None,
                    help="checked against the running fake (start it with BENCH_FAKE_AI_ARGS='--base-ms N')")
    ap.add_argument("--fail-on-log-errors", action="store_true", help="any ERROR line of the app's log fails the run")
    ap.add_argument("--max-p95-s", type=float, default=None, help="fail the run when the p95 over every accepted probe is above this")
    ap.add_argument("--max-wall-s", type=float, default=None, help="fail the run when its wall time is above this")
    ap.add_argument("--inject-duplicate", action="store_true", help="re-upload one file's bytes: it must be rejected")
    ap.add_argument("--chaos", default=None, help="kill-app-at=<seconds>")
    ap.add_argument("--app-container", default=None)
    ap.add_argument("--siblings", default="", help="comma-separated sibling container names")
    ap.add_argument("--redis-container", default=None)
    ap.add_argument("--neo4j-container", default=None)
    ap.add_argument("--postgres-container", default=None)
    ap.add_argument("--neo4j-connection-budget", type=int, default=None)
    ap.add_argument("--pg-max-connections", type=int, default=None)
    ap.add_argument("--upload-workers", type=int, default=16)
    ap.add_argument("--poll-interval", type=float, default=2.0)
    ap.add_argument("--sample-interval", type=float, default=15.0)
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--request-timeout", type=int, default=120)
    ap.add_argument("--keep-kb", action="store_true")
    ap.add_argument("--output", type=Path, default=_IT_DIR / "reports" / "perf" / "costclass.json")
    ap.add_argument("--summary", type=Path, default=None)
    args = ap.parse_args(argv)
    if not args.knee_from and not args.baseline_from and not (args.scenario and args.label):
        ap.error("--scenario and --label are required (or --knee-from, or --baseline-from)")
    return args


def knee_main(args: argparse.Namespace) -> int:
    results = [json.loads(p.read_text(encoding="utf-8")) for p in args.knee_from]
    out = knee_from_results(results)
    text = json.dumps(out, indent=2) + "\n"
    if args.output != _IT_DIR / "reports" / "perf" / "costclass.json":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    if out["cell_cpus"] is None:
        print(f"no CELL_CPUS: {out['reason']}")
        return EXIT_FAILED
    print(f"CELL_CPUS={out['cell_cpus']} confidence={out['confidence']} "
          f"{'record as a decision' if out['record_as_decision'] else 'note only: not a decision (it needs high confidence)'}")
    return EXIT_PASS


def baseline_main(args: argparse.Namespace) -> int:
    out = baseline_from_results([json.loads(p.read_text(encoding="utf-8")) for p in args.baseline_from],
                                expected_repeats=args.expected_repeats)
    text = json.dumps(out, indent=2) + "\n"
    if args.output != _IT_DIR / "reports" / "perf" / "costclass.json":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    if not out["baseline_valid"]:
        print("baseline INVALID, do not commit it: " + "; ".join(out["reasons"]))
        return EXIT_INVALID
    print("baseline valid")
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.knee_from:
        return knee_main(args)
    if args.baseline_from:
        return baseline_main(args)
    load_env()
    result = execute(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = render_summary(result)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Result written to {args.output}")
    return int(result["verdict"]["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
