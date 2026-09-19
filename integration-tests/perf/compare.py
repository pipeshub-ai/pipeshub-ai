"""Compare an indexing benchmark result with a committed baseline.

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


CHECKS: tuple[Check, ...] = (
    Check("Throughput (records/min)", lambda m: m["records_per_minute"], -1, 0.20, ""),
    Check("Time to indexed p95", lambda m: m["time_to_indexed_seconds"]["p95"], +1, 0.30, " s"),
    Check("Time to indexed p50", lambda m: m["time_to_indexed_seconds"]["p50"], +1, 0.30, " s"),
    Check("Wall time", lambda m: m["wall_seconds"], +1, 0.25, " s"),
    Check("Peak indexing memory", lambda m: m.get("peak_indexing_rss_mb"), +1, 0.25, " MB"),
)

# Fields that must match for the numbers to be comparable at all.
COMPARABLE_FIELDS = (
    ("label", lambda r: r["environment"]["label"]),
    ("graph DB", lambda r: r["environment"]["graph_db"]),
    ("message broker", lambda r: r["environment"]["message_broker"]),
    ("AI models", lambda r: r["environment"]["ai_models"]),
    ("docs", lambda r: r["corpus"]["docs"]),
    ("seed", lambda r: r["corpus"]["seed"]),
    ("file kinds", lambda r: r["corpus"].get("kinds")),
)


@dataclass(frozen=True)
class Row:
    name: str
    baseline: float | None
    current: float | None
    change: float | None
    regressed: bool
    note: str
    unit: str


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> tuple[list[Row], list[str]]:
    mismatches = [
        f"{label}: baseline {read(baseline)!r}, this run {read(current)!r}"
        for label, read in COMPARABLE_FIELDS
        if read(baseline) != read(current)
    ]
    rows = [_check_row(check, baseline["metrics"], current["metrics"]) for check in CHECKS]
    rows.append(_failure_row(baseline["metrics"], current["metrics"]))
    return rows, mismatches


def _check_row(check: Check, base_m: dict[str, Any], cur_m: dict[str, Any]) -> Row:
    base, cur = check.read(base_m), check.read(cur_m)
    if base is None or cur is None or base == 0:
        return Row(check.name, base, cur, None, False, "not measured on one side", check.unit)
    change = (cur - base) / base
    regressed = change * check.direction > check.threshold
    limit = f"{'+' if check.direction > 0 else '-'}{check.threshold:.0%}"
    return Row(check.name, base, cur, change, regressed, f"flags beyond {limit}", check.unit)


def _failure_row(base_m: dict[str, Any], cur_m: dict[str, Any]) -> Row:
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
    lines += ["| Measure | Baseline | This run | Change | Verdict |", "| --- | --- | --- | --- | --- |"]
    for r in rows:
        change = "" if r.change is None else f"{r.change:+.0%}"
        verdict = "not judged" if mismatches else ("⚠️ regression" if r.regressed else "ok")
        lines.append(
            f"| {r.name} | {_fmt(r.baseline, r.unit)} | {_fmt(r.current, r.unit)} | {change} | {verdict} ({r.note}) |"
        )
    lines.append("")
    if mismatches:
        lines.append("Not judged: refresh the baseline or rerun with matching settings.")
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
