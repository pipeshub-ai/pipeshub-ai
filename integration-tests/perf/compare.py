"""Compare a benchmark result with a committed baseline.

Handles the benchmarks in ``perf/``: ``indexing`` (bench_indexing.py),
``query`` (bench_query.py) and ``scale`` (bench_scale.py). The result's own
``benchmark`` field picks the checks, and two results of different benchmarks
are never compared.

Reports only: it exits 0 whatever it finds unless ``--fail-on-regression`` is
passed, so a noisy week cannot block anyone while the thresholds are still
being learned. See README.md for why each threshold is where it is.
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

BENCHMARKS: dict[str, tuple[tuple[Check, ...], tuple[Any, ...]]] = {
    "indexing": (INDEXING_CHECKS, INDEXING_COMPARABLE),
    "query": (QUERY_CHECKS, QUERY_COMPARABLE),
    # A scale run measures the same things as an indexing run, on a much larger
    # corpus, so it is judged by the same checks. Corpus size is one of the
    # fields that must match, so a 2,000-file run is never put beside a
    # 100,000-file one. Stress runs are deliberately absent: their result is a
    # list of pass-or-fail verdicts, not numbers to compare.
    "scale": (INDEXING_CHECKS, INDEXING_COMPARABLE),
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
    mismatches = [
        f"{label}: baseline {_read(read, baseline)!r}, this run {_read(read, current)!r}"
        for label, read in comparable_fields
        if _read(read, baseline) != _read(read, current)
    ]
    if benchmark == "query":
        mismatches += _seeding_mismatches(baseline, "baseline") + _seeding_mismatches(current, "this run")
    if benchmark in ("indexing", "scale"):
        mismatches += _partial_run_mismatches(baseline, "baseline") + _partial_run_mismatches(current, "this run")
    rows = [_check_row(check, baseline["metrics"], current["metrics"]) for check in checks]
    if benchmark == "query":
        rows += _rate_rows(baseline["metrics"], current["metrics"])
    rows.append(_failure_row(benchmark, baseline["metrics"], current["metrics"]))
    return rows, mismatches


def _check_row(check: Check, base_m: dict[str, Any], cur_m: dict[str, Any]) -> Row:
    base, cur = check.read(base_m), check.read(cur_m)
    if base is None or cur is None or base == 0:
        return Row(check.name, base, cur, None, False, "not measured on one side", check.unit)
    change = (cur - base) / base
    regressed = change * check.direction > check.threshold
    limit = f"{'+' if check.direction > 0 else '-'}{check.threshold:.0%}"
    return Row(check.name, base, cur, change, regressed, f"flags beyond {limit}", check.unit)


def _rate_rows(base_m: dict[str, Any], cur_m: dict[str, Any]) -> list[Row]:
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
    ):
        base = _op(base_m, operation, "with_sources_rate")
        cur = _op(cur_m, operation, "with_sources_rate")
        if base is None or cur is None:
            rows.append(Row(name, base, cur, None, False, "not measured on one side", ""))
            continue
        change = (cur - base) / base if base else None
        rows.append(Row(name, base, cur, change, cur < base, "flags any fall", ""))
    return rows


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
        verdict = "not judged" if mismatches else ("⚠️ regression" if r.regressed else "ok")
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
        regressed = any(r.regressed for r in rows) and not mismatches

    print(report)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(report)
    return 1 if regressed and args.fail_on_regression else 0


if __name__ == "__main__":
    sys.exit(main())
