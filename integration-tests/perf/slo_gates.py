"""SLO.md's rows as gates (QA-22): one row per SLO, a measured gate or a written reason with its owner.

A gate reads one statistic of one profile's result (``harness_profiles.py``) and fails when it is over its
threshold. Thresholds sit between the value measured on the reference host (with headroom, so host noise does not
fail a week) and the SLO's own target (never looser). A result measured while the host's load average was above its
core count is not a result: the verdict is ``rerun``. Regression bounds (``Bound``) gate numbers SLO.md has no row
for (hand-offs R1 measured); they follow the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["pass", "fail", "waived", "rerun", "missing"]


@dataclass(frozen=True)
class Gate:
    slo: str
    profile: str
    metric: str
    stat: str
    target: float
    threshold: float
    unit: str = "ms"
    note: str = ""
    # A finding (``QA22-Fn``, ``PERFB-Fn``) that explains a known miss: the verdict says ``waived``, not ``pass``,
    # and the finding's own fix removes the waiver.
    waived_by: str = ""
    # When a waiver ends: the milestone by which the finding is fixed or the row fails.
    waiver_expires: str = ""
    # The value must be at least the threshold (a throughput floor), not at most.
    at_least: bool = False


@dataclass(frozen=True)
class Bound(Gate):
    """A gate on a number SLO.md has no row for; ``slo`` names the gap it closes."""


@dataclass(frozen=True)
class Reason:
    slo: str
    owner: str
    why: str


Row = Gate | Reason

SLO_ROWS = tuple(f"#{n}" for n in range(1, 21))

# Measured on the reference host (QA-22): each threshold has headroom over that
# measurement and is never looser than its SLO target.
SLO_GATES: tuple[Row, ...] = (
    Gate("#1", "code-mode-5k", "commit_ms", "p99", 100.0, 100.0, note="single-node replica set, one session"),
    Gate("#1", "commits-n16", "commit_ms", "p99", 100.0, 100.0,
         note="16 sessions out of step at the capacity model's N = 16 rate (busiest recording, 18.3 commits/s)"),
    # The same SLO with the sessions in step: an open loop at the same rate in which all 16 start every turn in
    # the same instant. Unwaived (SLO.md, decision of 2026-10-12).
    Gate("#1", "commits-n16-instep", "commit_ms", "p99", 100.0, 100.0,
         note="16 sessions of one org in step at the capacity model's N = 16 rate"),
    Gate("#2", "short-turn", "ttft_added_ms", "p50", 75.0, 75.0,
         note="bench_harness.py --profile short-turn on the eval stack (existing gate)"),
    Gate("#3", "modes", "created_ms", "p95", 300.0, 150.0, note="every mode pooled; per mode in the result"),
    Gate("#4", "modes", "admission_ms", "p95", 150.0, 120.0, note="Node's admission service time"),
    Gate("#4", "commits-n16-instep", "admission_ms", "p95", 150.0, 150.0,
         note="16 admissions of one org in the same instant, every turn period"),
    Gate("#5", "modes", "delta_ms", "p95", 100.0, 75.0,
         note="worker emission to SSE client, one clock; Node's LiveBus poll (50 ms) bounds it"),
    Reason("#6", "QA-17", "Recovery after lease expiry is a fault-catalog p100 (crash points, 24 h soak), not a "
           "latency profile; QA-17 owns the catalog bound"),
    Gate("#7", "cancel", "stop_ms", "p95", 2000.0, 500.0, note="Redis up; Redis down is the outage suite's"),
    Gate("#8", "reconnect", "reconnect_ms", "p95", 3000.0, 500.0, note="short and 5,000-call journals"),
    Gate("#9", "code-mode-5k", "commit_excess", "max", 0.0, 0.0, unit="commits",
         note="per model turn: commits - (2 + 2 x tools); also in commits-n16"),
    Gate("#9", "commits-n16", "commit_excess", "max", 0.0, 0.0, unit="commits"),
    Gate("#10", "code-mode-5k", "inline_ratio", "max", 1.5, 1.5, unit="x projection",
         note="journal bytes per run against capacity.py's projection of the same run (project_loop_reads)"),
    Gate("#10", "code-mode-5k", "largest_event_kb", "max", 256.0, 256.0, unit="KB"),
    Reason("#11", "QA-34", "Cache-read share is a production metric (HarnessCacheReadShareLow); a fake model has no "
           "provider cache"),
    Gate("#12", "mcp-5-servers", "added_ttft_ms", "p50", 300.0, 300.0,
         note="first token with 5 MCP servers minus with none; SLO.md's P0 legacy baseline was never recorded"),
    Reason("#13", "QA-28", "Approval-prompt rate is a ratio over the parity cases, not a latency"),
    Reason("#14", "QA-28", "Injection success is held at 0 by the parity cases on every PR, not a latency"),
    Reason("#15", "S1 (existing)", "Footprint is gated by footprint.py in published-hub-smoke.yml"),
    Reason("#16", "QA-34", "Idle RSS is footprint.sh's, in the image; on the host the worker measured 330 MiB RSS "
           "idle (WP-88, the rig's worker without the warm-up); the image's warmed worker is 625 MiB (R26-FU), within "
           "the restated 650 MB"),
    Gate("#17", "sandbox", "warm_ms", "p95", 1000.0, 500.0, note="tier 1 (runc); tier 2 needs gVisor (QA-25)"),
    Gate("#17", "sandbox", "cold_ms", "p95", 5000.0, 2000.0, note="tier 1 (runc)"),
    Reason("#18", "P5 code-mode RPC", "No code-mode gateway RPC exists to aggregate through; "
           "test_code_mode_rpc.py is not built"),
    # PF-14 (C7.11): perf-harness.yml's soak job, 24 h at 60% of the capacity model's N = 16 (harness_profiles).
    Gate("#19", "soak", "orphan_runs", "max", 0.0, 0.0, unit="runs", note="runs not ended once the load stopped"),
    Gate("#19", "soak", "ownerless_leases", "max", 0.0, 0.0, unit="runs",
         note="worker-owned runs whose lease nobody holds, two lease lengths after the load"),
    Gate("#19", "soak", "stuck_outbox", "max", 0.0, 0.0, unit="rows", note="pending, publishing or failed"),
    *(Gate("#19", "soak", f"{kind}_growth[{process}]", "max", 10.0, 10.0, unit="%",
           note="last tenth's median over the second tenth's (the first is start-up)")
      for kind, process in (("rss", "node"), ("rss", "worker"), ("fd", "node"))),
    # The worker is non-dumpable: no other process may list its open files. Its fd growth would show as RSS and as
    # Node's or MongoDB's connections; it is not read here.
    Reason("#20", "PERF lane", "Interactive session ready under saturation is gated on virtual time by "
           "backend/python/tests/unit/harness/test_g45_preemption.py (GA-24, loop and engine runs over one provider "
           "limit); the real-stack measurement is bench_harness.py --profile saturate, not run yet"),
    Bound("JRN-G6", "code-mode-5k", "commit_ms", "p50", 45.0, 45.0,
          note="measured 32 (PERF-B): a session's commits must not grow with its history again (QA22-F2)"),
    Bound("JRN-G6", "code-mode-5k", "commit_ms[complete]", "p50", 80.0, 80.0,
          note="measured 53 (PERF-B): the ending commit must not re-read its run again (QA22-F4)"),
    Bound("JRN-G6", "commits-n16", "commit_ms", "p50", 25.0, 25.0, note="measured 15-16 (PERF-B)"),
    # Overload (commits-n16-burst: a closed loop with no think time saturates one Node process by construction).
    # Its latency is queueing at saturation, not SLO #1 or #4; these are what overload must not do (SLO.md).
    Bound("ADM-G10", "commits-n16-burst", "aborts_per_commit", "max", 1.0, 1.0, unit="aborted per committed",
          note="MongoDB's own count over the run: measured 0.1 (PERF-C), 13.5-14.7 before it. An org's "
               "transactions must not abort each other at a document they share again (PERFB-F1)"),
    Bound("ADM-G10", "commits-n16-instep", "aborts_per_commit", "max", 1.0, 1.0, unit="aborted per committed",
          note="the same, in step at the paced rate"),
    Bound("ADM-G10", "commits-n16-burst", "capacity_multiple", "max", 2.0, 2.0, unit="x the N = 16 rate",
          at_least=True,
          note="throughput does not collapse: at least twice the capacity model's N = 16 rate (18.3 commits/s); "
               "measured 3.4-3.6 x, 1.2-1.3 x with PERFB-F1's storm"),
    Bound("ADM-G10", "commits-n16-burst", "admission_ms", "p95", 1000.0, 1000.0,
          note="proposed (no overload row in SLO.md yet; awaiting confirmation): a user is admitted within about "
               "1 s under overload; measured 296-880, 8,700-10,900 before PERF-C"),
    Bound("ADM-G10", "commits-n16-burst", "runs_lost", "max", 0.0, 0.0, unit="runs"),
    Bound("ADM-G10", "commits-n16-burst", "runs_duplicated", "max", 0.0, 0.0, unit="runs"),
    Bound("ADM-G10", "commits-n16-instep", "runs_lost", "max", 0.0, 0.0, unit="runs"),
    Bound("ADM-G10", "commits-n16-instep", "runs_duplicated", "max", 0.0, 0.0, unit="runs"),
    # What one request costs the one Node process every request shares (PERFC-F1, commit_cpu.py): requests of the
    # same instant end one after another, so this is what SLO #1 and SLO #4 rest on for sessions in step. Store
    # commands do not depend on the host: the ratchet is the measured count and under one command of slack for a
    # request (a commit's read-ahead and a stream's read can fall either side of each other), 5% for a turn. CPU is
    # in calibration units (the same thread's CPU for a fixed workload over the same turns): the measured value
    # plus 25%, which is what repeated runs and a host's load leave in the unit (PERF-D).
    Bound("PERFC-F1", "commit-cpu", "commands[commit:step]", "max", 10.0, 10.0, unit="store commands",
          note="measured 9.0 (PERF-E3: payload marks in the batch), 9.2 (PERF-E2: its events and receipt in one "
               "bulkWrite), 10.2-10.3 (PERF-D), 12.2 before it"),
    Bound("PERFC-F1", "commit-cpu", "commands[commit:completed]", "max", 15.0, 15.0, unit="store commands",
          note="measured 14 (PERF-E3: the session's update in the batch, both journal appends allocated once), 16 "
               "(PERF-E2), 26 (PERF-E), 27 (PERF-D), 30 before it"),
    Bound("PERFC-F1", "commit-cpu", "commands[admission]", "max", 19.0, 19.0, unit="store commands",
          note="measured 18.1-18.3 (PERF-E3: a new conversation's session and first turn in the batch, never read), "
               "21.1 (PERF-E2), 31.1-31.3 (PERF-E), 34.2-34.3 before it"),
    Bound("PERFC-F1", "commit-cpu", "commands[turn]", "max", 141.0, 141.0, unit="store commands",
          note="measured 132.8-135.4 for the corpus's read, write, answer turn (PERF-E3: the audit link once for "
               "the org's commits of a second), 159.4 (PERF-E2), 186.6-188.3 (PERF-E), 192-194 (PERF-D), 228 "
               "before PERF-D"),
    Bound("PERFC-F1", "commit-cpu", "cpu_units[commit:step]", "max", 1.23, 1.23, unit="calibration units",
          note="measured 0.96-0.99 (PERF-D), 1.29 before it"),
    Bound("PERFC-F1", "commit-cpu", "cpu_units[commit:completed]", "max", 3.15, 3.15, unit="calibration units",
          note="measured 2.45-2.56 (PERF-D), 3.09 before it"),
    Bound("PERFC-F1", "commit-cpu", "cpu_units[admission]", "max", 3.07, 3.07, unit="calibration units",
          note="measured 2.43-2.49 (PERF-D), 3.15 before it"),
    Bound("WRK-G2", "modes", "handoff_ms", "p95", 150.0, 150.0, note="admission to the worker's first model call"),
    Bound("WRK-G2", "approval", "held_resume_ms", "p95", 300.0, 300.0, note="Allow to the write's next model call"),
    Bound("WRK-G2", "approval", "suspended_resume_ms", "p95", 400.0, 400.0),
    Bound("CHD-G2", "deep-wave", "fanout_ms", "p95", 500.0, 500.0, note="spawn to every child's first model call"),
    Bound("CHD-G2", "deep-wave", "join_ms", "p95", 300.0, 300.0, note="last child's answer to the parent's next call"),
    # design-plan §8 rows SLO.md has none for (WP-17): the gateways' own budgets, at the rate PF-05 states.
    Bound("PF-05", "tool-gateway", "overhead_ms", "p50", 35.0, 35.0,
          note="auth, grant, policy and audit of a null tool's call; the audit on Node's MongoDB journal"),
    Bound("PF-05", "tool-gateway", "overhead_ms", "p95", 100.0, 100.0),
    Bound("PF-05", "tool-gateway", "calls_per_s", "mean", 40.0, 38.0, unit="calls/s", at_least=True,
          note="one server's sustainable rate (95% of it reached; it queues from ~55/s and saturates at ~90/s)"),
    # PF-15 (C7.12): 0 -> 200 session creates in 60 s on one Node and worker (WP-88).
    Bound("PF-15", "burst-200", "admission_ms", "p95", 150.0, 150.0,
          note="SLO #4's target holds through the burst: 200 admissions in a minute is 3.3/s on average"),
    Bound("PF-15", "burst-200", "server_errors", "max", 0.0, 0.0, unit="answers", note="no 5xx"),
    Bound("PF-15", "burst-200", "runs_lost", "max", 0.0, 0.0, unit="runs"),
    Bound("PF-15", "burst-200", "runs_duplicated", "max", 0.0, 0.0, unit="runs"),
    *(Bound("PF-07", "model-gateway", f"added_ms[{wire}]", "p95", 50.0, 50.0,
            note=f"time to first token added on the {wire} wire, the budget reserved on Node's ledger")
      for wire in ("chat", "messages", "responses")),
)

# Rows other WPs gate; listed so the table says who, not silently absent.
PENDING: tuple[Reason, ...] = (
    Reason("NFR-1/2/4/5", "CPD-5", "Pinner benchmarks; the admission pipeline is CPD-5's and running"),
    Reason("NFR-8/9", "CPD-11", "Admin write, preview and catalog list latency"),
    Reason("NFR-3", "CPD-10", "Legacy-path A/B once the legacy path reads the signed config"),
    Reason("NFR-7", "CPD-6", "The in-process live check"),
)


@dataclass(frozen=True)
class Verdict:
    row: Gate
    status: Status
    value: float | None
    detail: str = ""


def gates() -> list[Gate]:
    return [row for row in SLO_GATES if isinstance(row, Gate)]


def profiles_gated() -> set[str]:
    """Profiles the weekly and release rig job runs for a gate (``short-turn`` runs on the eval stack, ``soak``
    in its own 24 h job)."""
    return {g.profile for g in gates()} - {"short-turn", "soak"}


def _value(result: dict[str, Any], gate: Gate) -> float | None:
    stats = (result.get("metrics") or {}).get(gate.metric)
    if not isinstance(stats, dict):
        return None
    value = stats.get(gate.stat)
    return float(value) if isinstance(value, int | float) else None


def evaluate(
    results: dict[str, dict[str, Any]], cores: int, rows: tuple[Gate, ...] | None = None
) -> list[Verdict]:
    """A verdict per gate whose profile ran; a profile measured above ``cores`` load is ``rerun``."""
    verdicts: list[Verdict] = []
    for gate in gates() if rows is None else rows:
        result = results.get(gate.profile)
        if result is None:
            continue
        load = float(result.get("load1_max") or 0.0)
        value = _value(result, gate)
        if load > cores:
            verdicts.append(Verdict(gate, "rerun", value, f"load {load:.1f} > {cores} cores"))
        elif value is None:
            verdicts.append(Verdict(gate, "missing", None, f"no {gate.metric}.{gate.stat} in the result"))
        elif (value >= gate.threshold) if gate.at_least else (value <= gate.threshold):
            verdicts.append(Verdict(gate, "pass", value))
        elif gate.waived_by:
            expiry = f", expires {gate.waiver_expires}" if gate.waiver_expires else ""
            verdicts.append(Verdict(gate, "waived", value, gate.waived_by + expiry))
        else:
            verdicts.append(Verdict(gate, "fail", value))
    return verdicts


def passed(verdicts: list[Verdict]) -> bool:
    """A ``rerun`` is not evidence either way; a ``waived`` miss is a known finding, reported every run."""
    return all(v.status in ("pass", "waived") for v in verdicts)


def render(verdicts: list[Verdict]) -> str:
    lines = ["| SLO | Profile | Metric | Target | Gate | Measured | Status |", "|---|---|---|---|---|---|---|"]
    for v in verdicts:
        g = v.row
        measured = "—" if v.value is None else f"{v.value:g}"
        floor = "≥ " if g.at_least else ""
        lines.append(f"| {g.slo} | {g.profile} | {g.metric} {g.stat} | {floor}{g.target:g} {g.unit} | "
                     f"{floor}{g.threshold:g} {g.unit} | {measured} | {v.status}{' (' + v.detail + ')' if v.detail else ''} |")
    return "\n".join(lines) + "\n"


__all__ = ["PENDING", "SLO_GATES", "SLO_ROWS", "Bound", "Gate", "Reason", "Verdict", "evaluate", "gates", "passed",
           "profiles_gated", "render"]
