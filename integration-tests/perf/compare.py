"""Compare a benchmark result with a committed baseline.

Handles the benchmarks in ``perf/``: ``indexing`` (bench_indexing.py), ``costclass`` (bench_costclass.py),
``query`` (bench_query.py), ``scale`` (bench_scale.py), ``load``
(bench_load.py) and ``harness`` (bench_harness.py). The result's own
``benchmark`` field picks the checks, and two results of different benchmarks
are never compared.

Reports only: it exits 0 whatever it finds unless ``--fail-on-regression`` is
passed, so a noisy week cannot block anyone while the thresholds are still
being learned. Even then, only the rows marked as gating fail it: for the load
test, the p95 latencies and the search hit rates. See README.md for why each threshold is where it is.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class Check:
    name: str
    read: Callable[[dict[str, Any]], float | None]
    # Positive: a rise is bad. Negative: a fall is bad.
    direction: int
    threshold: float
    unit: str
    # False: shown, and flagged, but never what fails --fail-on-regression.
    gates: bool = True
    # A move smaller than this, in the measure's own unit, is never flagged
    # however large it is as a share: on a 50 ms search, 30% is jitter.
    min_change: float = 0.0
    # True: the metric may be absent on one side (a corpus kind that one run did not have). Every other
    # check treats a missing or null metric as an error in the result, not as "nothing to compare".
    optional: bool = False


INDEXING_CHECKS: tuple[Check, ...] = (
    Check("Throughput (records/min)", lambda m: m["records_per_minute"], -1, 0.20, ""),
    Check("Time to indexed p95", lambda m: m["time_to_indexed_seconds"]["p95"], +1, 0.30, " s"),
    Check("Time to indexed p50", lambda m: m["time_to_indexed_seconds"]["p50"], +1, 0.30, " s"),
    Check("Wall time", lambda m: m["wall_seconds"], +1, 0.25, " s"),
    Check("Peak indexing memory", lambda m: m.get("peak_indexing_rss_mb"), +1, 0.25, " MB"),
)


def _op(metrics: dict[str, Any], operation: str, *keys: str) -> float | None:
    """A query metric, or None when that operation was never measured."""
    value: Any = (metrics.get("operations") or {}).get(operation)
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, (int, float)) else None


QUERY_CHECKS: tuple[Check, ...] = (
    Check("Search p95", lambda m: _op(m, "search", "latency_seconds", "p95"), +1, 0.30, " s"),
    Check("Search p50", lambda m: _op(m, "search", "latency_seconds", "p50"), +1, 0.30, " s"),
    Check("Filtered search p95", lambda m: _op(m, "search_filtered", "latency_seconds", "p95"), +1, 0.30, " s"),
    Check("Chat turn p95", lambda m: _op(m, "chat", "latency_seconds", "p95"), +1, 0.30, " s"),
    Check("Chat turn p50", lambda m: _op(m, "chat", "latency_seconds", "p50"), +1, 0.30, " s"),
    Check("Chat first answer frame p95", lambda m: _op(m, "chat", "first_answer_seconds", "p95"), +1, 0.30, " s"),
    Check("Throughput (operations/min)", lambda m: m.get("operations_per_minute"), -1, 0.20, ""),
)

# Judged over the load test's steady window. The p95s are what fail the run:
# the median hides a tail that only shows under load, and p99 over a
# fifteen-minute window is a handful of requests, too few to hold steady.
LOAD_CHECKS: tuple[Check, ...] = (
    Check("Search p95", lambda m: _op(m, "search", "latency_seconds", "p95"), +1, 0.30, " s",
          min_change=0.25),
    Check("Filtered search p95", lambda m: _op(m, "search_filtered", "latency_seconds", "p95"), +1, 0.30, " s",
          min_change=0.25),
    Check("Streaming chat turn p95", lambda m: _op(m, "chat", "latency_seconds", "p95"), +1, 0.30, " s",
          min_change=1.0),
    Check("Streaming chat first answer frame p95", lambda m: _op(m, "chat", "first_answer_seconds", "p95"),
          +1, 0.30, " s", min_change=0.5),
    Check("Non-streaming chat turn p95", lambda m: _op(m, "chat_sync", "latency_seconds", "p95"), +1, 0.30, " s",
          min_change=1.0),
    Check("Search p50", lambda m: _op(m, "search", "latency_seconds", "p50"), +1, 0.30, " s", gates=False),
    Check("Successful operations/min", lambda m: m.get("succeeded_per_minute"), -1, 0.20, "", gates=False),
)


def _route(metrics: dict[str, Any], route: str, measure: str, pct: str) -> float | None:
    value = ((metrics.get(route) or {}).get(measure) or {}).get(pct)
    return value if isinstance(value, (int, float)) else None


# The non-model milliseconds of each route; SLO #2's own gate is the ratio
# inside one run (bench_harness.py), these catch a route drifting week to week.
HARNESS_CHECKS: tuple[Check, ...] = tuple(
    Check(f"{route} {name}", lambda m, r=route, k=key, p=pct: _route(m, r, k, p), +1, 0.30, " ms")
    for route in ("harness", "legacy")
    for name, key, pct in (
        ("TTFT p50", "ttft_nonmodel", "p50_ms"),
        ("TTFT p95", "ttft_nonmodel", "p95_ms"),
        ("turn done p50", "done_nonmodel", "p50_ms"),
    )
)

# Fields that must match for the numbers to be comparable at all.
_SHARED_COMPARABLE = (
    ("label", lambda r: r["environment"]["label"]),
    ("graph DB", lambda r: r["environment"]["graph_db"]),
    ("message broker", lambda r: r["environment"]["message_broker"]),
    ("AI models", lambda r: r["environment"]["ai_models"]),
    ("docs", lambda r: r["corpus"]["docs"]),
    ("seed", lambda r: r["corpus"]["seed"]),
    ("file kinds", lambda r: r["corpus"].get("kinds")),
)

INDEXING_COMPARABLE = _SHARED_COMPARABLE
QUERY_COMPARABLE = _SHARED_COMPARABLE + (
    ("simulated users", lambda r: r["profile"]["users"]),
    ("load duration", lambda r: r["profile"]["duration_seconds"]),
    ("think time", lambda r: r["profile"]["think_time_seconds"]),
    ("question set", lambda r: r["profile"]["question_set"]),
    ("operation mix", lambda r: r["profile"]["mix"]),
)

LOAD_COMPARABLE = _SHARED_COMPARABLE + (
    ("stages", lambda r: r["profile"]["stages"]),
    ("think time", lambda r: r["profile"]["think_time_seconds"]),
    ("question set", lambda r: r["profile"]["question_set"]),
    ("operation mix", lambda r: r["profile"]["mix"]),
)

HARNESS_COMPARABLE = (
    ("label", lambda r: r["environment"]["label"]),
    ("profile", lambda r: r["profile"]),
    ("message broker", lambda r: (r["environment"].get("deployment") or {}).get("messageBrokerType")),
    ("model latency", lambda r: r["environment"]["model_latency_ms"]),
    ("chunk interval", lambda r: r["environment"].get("chunk_interval_ms")),
)

# The cost-class bench (bench_costclass.py): numbers are per corpus kind, and only comparable when the
# plan (scenario, tier, corpus, schedule seed) and the embedding mode are the same.
COSTCLASS_COMPARABLE = (
    ("label", lambda r: r["environment"]["label"]),
    ("AI models", lambda r: r["environment"]["ai_models"]),
    ("embedding mode", lambda r: r["environment"]["embedding_mode"]),
    ("scenario", lambda r: r["plan"]["scenario"]),
    ("tier", lambda r: r["plan"]["tier"]),
    ("corpus", lambda r: r["plan"]["corpus_id"]),
    ("schedule seed", lambda r: r["plan"]["seed"]),
    ("arrivals", lambda r: r["plan"].get("arrivals")),
    ("arrival rate", lambda r: (r["plan"].get("notes") or {}).get("rate_per_s")),
    ("load duration", lambda r: (r["plan"].get("notes") or {}).get("duration_s")),
    ("burst size", lambda r: (r["plan"].get("notes") or {}).get("burst_count")),
)
COSTCLASS_KIND_BUDGET = 0.20


def _kind_check(kind: str, stat: str) -> Check:
    return Check(
        f"{kind} {stat}", lambda m, k=kind, s=stat: m["by_kind"][k][s], +1, COSTCLASS_KIND_BUDGET, " s", min_change=1.0,
        optional=True,
    )


def costclass_checks(baseline: dict[str, Any], current: dict[str, Any]) -> tuple[Check, ...]:
    kinds = sorted(set(baseline["metrics"].get("by_kind", {})) | set(current["metrics"].get("by_kind", {})))
    checks = [Check("Throughput (records/min)", lambda m: m.get("records_per_minute"), -1, COSTCLASS_KIND_BUDGET, "")]
    for kind in kinds:
        checks += [_kind_check(kind, "p95"), _kind_check(kind, "p50")]
    return tuple(checks)


def _invalid_mismatches(result: dict[str, Any], side: str) -> list[str]:
    status = (result.get("verdict") or {}).get("status")
    if status in ("invalid", "failed"):
        return [f"{side}: the run was {status} ({'; '.join((result['verdict'].get('reasons') or [])[:3])})"]
    return []


BENCHMARKS: dict[str, tuple[tuple[Check, ...], tuple[Any, ...]]] = {
    "indexing": (INDEXING_CHECKS, INDEXING_COMPARABLE),
    "query": (QUERY_CHECKS, QUERY_COMPARABLE),
    "load": (LOAD_CHECKS, LOAD_COMPARABLE),
    # A scale run measures the same things as an indexing run, on a much larger
    # corpus, so it is judged by the same checks. Corpus size is one of the
    # fields that must match, so a 2,000-file run is never put beside a
    # 100,000-file one. Stress runs are deliberately absent: their result is a
    # list of pass-or-fail verdicts, not numbers to compare.
    "scale": (INDEXING_CHECKS, INDEXING_COMPARABLE),
    "harness": (HARNESS_CHECKS, HARNESS_COMPARABLE),
    "costclass": ((), COSTCLASS_COMPARABLE),
}


@dataclass(frozen=True)
class Row:
    name: str
    baseline: float | None
    current: float | None
    change: float | None
    regressed: bool
    note: str
    unit: str
    gates: bool = True


def _read(reader: Callable[[dict[str, Any]], Any], result: dict[str, Any]) -> Any:
    """A comparable field, or a marker when this result does not carry it."""
    try:
        return reader(result)
    except (KeyError, TypeError, IndexError):
        return "<not recorded>"


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> tuple[list[Row], list[str]]:
    benchmark = current.get("benchmark", "indexing")
    baseline_benchmark = baseline.get("benchmark", "indexing")
    if baseline_benchmark != benchmark:
        # Different benchmarks measure different things and do not even share a
        # metrics shape, so there is nothing to put side by side.
        return [], [f"benchmark: baseline {baseline_benchmark!r}, this run {benchmark!r}"]
    selected = BENCHMARKS.get(benchmark)
    if selected is None:
        # Guessing here would read a query result with indexing's checks and
        # raise a KeyError instead of saying what is wrong.
        return [], [f"benchmark: {benchmark!r} is not one this can compare"]
    checks, comparable_fields = selected
    if benchmark == "costclass":
        checks = costclass_checks(baseline, current)
    mismatches = [
        f"{label}: baseline {_read(read, baseline)!r}, this run {_read(read, current)!r}"
        for label, read in comparable_fields
        if _read(read, baseline) != _read(read, current)
    ]
    if benchmark in ("query", "load"):
        mismatches += _seeding_mismatches(baseline, "baseline") + _seeding_mismatches(current, "this run")
    if benchmark == "load":
        mismatches += _aborted_mismatches(baseline, "baseline") + _aborted_mismatches(current, "this run")
    if benchmark in ("indexing", "scale"):
        mismatches += _partial_run_mismatches(baseline, "baseline") + _partial_run_mismatches(current, "this run")
    if benchmark == "costclass":
        mismatches += _invalid_mismatches(baseline, "baseline") + _invalid_mismatches(current, "this run")
    rows = [_check_row(check, baseline["metrics"], current["metrics"]) for check in checks]
    if benchmark in ("query", "load"):
        # Under the load test's --fail-on-regression the citation rows stay
        # reports: how often an answer cites a document moves with the model
        # week to week. Whether a search over the seeded corpus finds it does not.
        rows += _rate_rows(baseline["metrics"], current["metrics"], citations_gate=benchmark != "load")
    rows.append(_failure_row(benchmark, baseline["metrics"], current["metrics"]))
    return rows, mismatches


def _value(check: Check, metrics: dict[str, Any]) -> Any:
    """A check's value. Only an optional check (a corpus kind) reads as None when one side never ran it."""
    if not check.optional:
        return check.read(metrics)
    try:
        return check.read(metrics)
    except (KeyError, TypeError):
        return None


def _check_row(check: Check, base_m: dict[str, Any], cur_m: dict[str, Any]) -> Row:
    base, cur = _value(check, base_m), _value(check, cur_m)
    if base is None or cur is None or base == 0:
        return Row(check.name, base, cur, None, False, "not measured on one side", check.unit, check.gates)
    change = (cur - base) / base
    regressed = change * check.direction > check.threshold and abs(cur - base) > check.min_change
    limit = f"{'+' if check.direction > 0 else '-'}{check.threshold:.0%}"
    if check.min_change:
        limit += f" and {check.min_change:g}{check.unit}"
    return Row(check.name, base, cur, change, regressed, f"flags beyond {limit}", check.unit, check.gates)


def _rate_rows(base_m: dict[str, Any], cur_m: dict[str, Any], citations_gate: bool = True) -> list[Row]:
    """Searches that found a hit, and answers that cited a document.

    An empty result is fast and counts as a success, so a run that stopped
    finding anything looks like an improvement on every latency measure. Any
    fall here is worth a look, so this flags one the way a new failure does.
    """
    rows = []
    for operation, name in (
        ("search", "Searches that found a hit"),
        # Its own row: a knowledge-base filter that stopped matching returns
        # nothing, which is fast and counts as a success, while the unfiltered
        # searches above keep finding plenty and hide it.
        ("search_filtered", "Filtered searches that found a hit"),
        ("chat", "Answers that cited a document"),
        ("chat_sync", "Non-streaming answers that cited a document"),
    ):
        # Only the load test has non-streaming turns; a query result has no row
        # to show for them.
        if operation not in (base_m.get("operations") or {}) and operation not in (cur_m.get("operations") or {}):
            continue
        gates = citations_gate or operation.startswith("search")
        base = _op(base_m, operation, "with_sources_rate")
        cur = _op(cur_m, operation, "with_sources_rate")
        if base is None or cur is None:
            rows.append(Row(name, base, cur, None, False, "not measured on one side", "", gates))
            continue
        change = (cur - base) / base if base else None
        rows.append(Row(name, base, cur, change, cur < base, "flags any fall", "", gates))
    return rows


def _aborted_mismatches(result: dict[str, Any], side: str) -> list[str]:
    """A load run that stopped early measured a shorter, broken run; its own check already failed it."""
    aborted = (result.get("metrics") or {}).get("aborted")
    return [f"{side}: the load stopped early ({aborted})"] if aborted else []


def _seeding_mismatches(result: dict[str, Any], side: str) -> list[str]:
    """Reasons this run's corpus was not what it claims, so nothing is judged.

    Questions asked over a half-seeded knowledge base come back empty, which is
    faster and counts as a success everywhere: an unfinished seed would read as
    the best run yet.
    """
    metrics = result.get("metrics") or {}
    reasons = []
    stopped = metrics.get("seeding_stopped_early")
    if stopped:
        reasons.append(f"{side}: seeding did not finish ({stopped})")
    indexed = metrics.get("docs_indexed")
    intended = (result.get("corpus") or {}).get("docs")
    if isinstance(indexed, int) and isinstance(intended, int) and indexed < intended:
        reasons.append(f"{side}: only {indexed} of {intended} documents were indexed")
    return reasons


def _partial_run_mismatches(result: dict[str, Any], side: str) -> list[str]:
    """Reasons this run did not cover its corpus, so nothing is judged.

    A run that stopped part way indexed a prefix and reports on that: fewer
    files, so higher throughput and no failures among the ones that never
    arrived. It would read as the best run yet. The same rule the query
    benchmark applies to a half-seeded knowledge base.
    """
    metrics = result.get("metrics") or {}
    reasons = []
    stopped = metrics.get("stopped_early")
    if stopped:
        reasons.append(f"{side}: the run did not finish ({stopped})")
    uploaded = metrics.get("records_uploaded")
    intended = (result.get("corpus") or {}).get("docs")
    if isinstance(uploaded, int) and isinstance(intended, int) and uploaded < intended:
        reasons.append(f"{side}: only {uploaded} of {intended} documents were uploaded")
    return reasons


def _failure_row(benchmark: str, base_m: dict[str, Any], cur_m: dict[str, Any]) -> Row:
    if benchmark == "load":
        # Under load a stray failure is expected now and then, and the run's own
        # --max-error-rate check is what fails on errors. This only says the
        # rate moved by more than a percentage point.
        base, cur = base_m.get("error_rate"), cur_m.get("error_rate")
        if base is None or cur is None:
            return Row("Error rate", base, cur, None, False, "not measured on one side", "", False)
        return Row("Error rate", base, cur, None, cur - base > 0.01, "flags a rise of over 1 point", "", False)
    if benchmark == "harness":
        base = sum(route.get("samples", 0) - route.get("ok", 0) for route in base_m.values())
        cur = sum(route.get("samples", 0) - route.get("ok", 0) for route in cur_m.values())
        return Row("Failed turns", base, cur, None, cur > base, "flags any increase", "")
    if benchmark == "query":
        base = sum(op.get("errors", 0) for op in (base_m.get("operations") or {}).values())
        cur = sum(op.get("errors", 0) for op in (cur_m.get("operations") or {}).values())
        return Row("Failed searches and chat turns", base, cur, None, cur > base, "flags any increase", "")
    base = base_m["failures"]["total"]
    cur = cur_m["failures"]["total"]
    return Row("Failed or unfinished records", base, cur, None, cur > base, "flags any increase", "")


def render(rows: list[Row], mismatches: list[str], baseline_path: str) -> str:
    regressions = [r for r in rows if r.regressed]
    lines = ["### Compared with the baseline", "", f"Baseline: `{baseline_path}`", ""]
    if mismatches:
        lines += [
            "**These runs are not like for like, so the numbers are shown without a verdict:**",
            "",
            *[f"- {m}" for m in mismatches],
            "",
        ]
    if not rows:
        lines += ["Nothing was compared.", ""]
        return "\n".join(lines) + "\n"
    lines += ["| Measure | Baseline | This run | Change | Verdict |", "| --- | --- | --- | --- | --- |"]
    for r in rows:
        change = "" if r.change is None else f"{r.change:+.0%}"
        if mismatches:
            verdict = "not judged"
        elif r.regressed:
            verdict = "⚠️ regression" if r.gates else "⚠️ worse (reported only)"
        else:
            verdict = "ok"
        lines.append(
            f"| {r.name} | {_fmt(r.baseline, r.unit)} | {_fmt(r.current, r.unit)} | {change} | {verdict} ({r.note}) |"
        )
    lines.append("")
    if mismatches:
        lines.append(
            "Not judged: refresh the baseline, or rerun with matching settings and a corpus that "
            "finished indexing."
        )
    elif regressions:
        lines.append(f"{len(regressions)} measure(s) moved past their threshold.")
    else:
        lines.append("Nothing moved past its threshold.")
    return "\n".join(lines) + "\n"


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    return f"{value:g}{unit}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--summary", type=Path, default=None, help="append the Markdown report here")
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args()

    current = json.loads(args.current.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    if baseline is None or baseline.get("placeholder"):
        why = baseline.get("note", "") if baseline else f"There is no baseline at `{args.baseline}` yet."
        report = (
            "### Compared with the baseline\n\n"
            f"Nothing to compare with. {why}\n\n"
            "To make a run the baseline, see \"Updating a baseline\" in integration-tests/perf/README.md.\n"
        )
        regressed = False
    else:
        rows, mismatches = compare(baseline, current)
        report = render(rows, mismatches, str(args.baseline))
        regressed = any(r.regressed and r.gates for r in rows) and not mismatches

    print(report)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(report)
    return 1 if regressed and args.fail_on_regression else 0


if __name__ == "__main__":
    sys.exit(main())
