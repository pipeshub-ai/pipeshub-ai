#!/usr/bin/env python3
"""Roll every run's `summary.json` into one table and a CSV.

    ./aggregate.py [results_dir]

Reports the MEDIAN of each group's trials, not the mean: one run whose drain
tail stretched the window is an outlier of the measurement, not of the system,
and a median ignores it. The spread column is min-max, so a group whose trials
disagree cannot be quoted as a single confident number.

Runs marked INVALID (error rate over threshold) are excluded from the medians
and counted separately -- a run that mostly failed is not a measurement.
"""
from __future__ import annotations

import csv
import json
import re
import statistics as st
import sys
from pathlib import Path

FIELDS = [
    "arm", "workers", "users", "trial", "label", "turns", "throughput_req_min",
    "steady_state_req_min", "p50_ms", "p95_ms", "requests", "errors", "error_rate",
    "invalid", "py_samples_per_turn", "mem_start_mb", "mem_peak_mb", "mem_growth_mb",
    "load_seconds", "wall_seconds", "queries",
]


def _fmt(vals: list[float], places: int = 1) -> str:
    if not vals:
        return "-"
    med = st.median(vals)
    if len(vals) == 1:
        return f"{med:.{places}f}"
    return f"{med:.{places}f} [{min(vals):.{places}f}-{max(vals):.{places}f}]"


LABEL_RE = re.compile(r"^(?P<arm>[a-z]+)_w(?P<workers>\d+)_u(?P<users>\d+)_t(?P<trial>\d+)$")


def _fill_from_label(run: dict) -> dict:
    """The matrix driver sets workers once per group rather than per run, so
    `perftest.sh` has no worker count to record. The label carries it."""
    m = LABEL_RE.match(run.get("label") or "")
    if not m:
        return run
    for key, cast in (("arm", str), ("workers", str), ("users", int), ("trial", str)):
        if run.get(key) in (None, "", 0):
            run[key] = cast(m.group(key))
    return run


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "results")
    runs = []
    for path in sorted(root.glob("*/summary.json")):
        try:
            runs.append(_fill_from_label(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            print(f"  (skipped unreadable {path})", file=sys.stderr)
    if not runs:
        print(f"no summary.json under {root}/ — has anything run yet?")
        return 1

    out = root / "matrix.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in runs:
            w.writerow({k: r.get(k) for k in FIELDS})

    groups: dict[tuple, list[dict]] = {}
    for r in runs:
        groups.setdefault((r.get("arm") or "?", str(r.get("workers")), r.get("users") or 0), []).append(r)

    print(f"  {len(runs)} runs from {root}/   ->   {out}")
    print()
    print(f"  {'arm':<10}{'w':>3}{'users':>7}{'n':>4}{'req/min':>22}"
          f"{'steady-state':>22}{'p50 s':>20}{'samples/turn':>18}{'err':>8}")
    print(f"  {'':<10}{'':>3}{'':>7}{'':>4}{'median [min-max]':>22}"
          f"{'median [min-max]':>22}{'median [min-max]':>20}{'median':>18}{'max':>8}")
    for key in sorted(groups, key=lambda k: (k[0], int(k[1]) if k[1].isdigit() else 99, k[2])):
        arm, workers, users = key
        rows = groups[key]
        good = [r for r in rows if not r.get("invalid")]
        bad = len(rows) - len(good)
        thr = [r["throughput_req_min"] for r in good if r.get("throughput_req_min")]
        ss = [r["steady_state_req_min"] for r in good if r.get("steady_state_req_min")]
        p50 = [r["p50_ms"] / 1000 for r in good if r.get("p50_ms")]
        spt = [r["py_samples_per_turn"] for r in good if r.get("py_samples_per_turn")]
        err = [r["error_rate"] for r in rows if r.get("error_rate") is not None]
        flag = f"  ({bad} INVALID)" if bad else ""
        print(f"  {arm:<10}{workers:>3}{users:>7}{len(good):>4}{_fmt(thr):>22}"
              f"{_fmt(ss):>22}{_fmt(p50):>20}{_fmt(spt, 0):>18}"
              f"{(max(err) * 100 if err else 0):>7.1f}%{flag}")

    arms = {r.get("arm") for r in runs if r.get("arm")}
    if len(arms) == 2:
        print()
        print("  before/after by configuration (median throughput, req/min)")
        a, b = sorted(arms)
        combos = sorted({(str(r.get("workers")), r.get("users")) for r in runs},
                        key=lambda k: (int(k[0]) if k[0].isdigit() else 99, k[1]))
        print(f"  {'workers':>8}{'users':>7}{a:>12}{b:>12}{'change':>10}")
        for workers, users in combos:
            def med(arm: str) -> float | None:
                v = [r["throughput_req_min"] for r in runs
                     if r.get("arm") == arm and str(r.get("workers")) == workers
                     and r.get("users") == users and not r.get("invalid")
                     and r.get("throughput_req_min")]
                return st.median(v) if v else None
            x, y = med(a), med(b)
            delta = f"{(y / x - 1) * 100:+.0f}%" if x and y else "-"
            print(f"  {workers:>8}{users:>7}{(f'{x:.1f}' if x else '-'):>12}"
                  f"{(f'{y:.1f}' if y else '-'):>12}{delta:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
