#!/usr/bin/env python3
"""Distil one run's artifacts into `summary.json`.

Parses the human `report.txt` the run just produced rather than recomputing
anything, so the JSON and the printed report can never disagree. `aggregate.py`
reads these across runs.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

NUM = r"([0-9]+(?:\.[0-9]+)?)"


def _search(text: str, pattern: str, cast=float):
    m = re.search(pattern, text)
    return cast(m.group(1)) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    for flag in ("outdir", "label", "users", "secs", "workers", "arm", "trial", "wall", "queries"):
        ap.add_argument(f"--{flag}", default="")
    args = ap.parse_args()

    out = Path(args.outdir)
    report = (out / "report.txt").read_text(encoding="utf-8", errors="replace")

    requests = errors = 0
    if (out / "requests.csv").exists():
        with (out / "requests.csv").open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                requests += 1
                if row.get("http_code") != "200" or (row.get("curl_exit") or "0") != "0":
                    errors += 1

    mem = []
    if (out / "memory.csv").exists():
        with (out / "memory.csv").open(newline="", encoding="utf-8") as fh:
            mem = [float(r[1]) for r in csv.reader(fh) if len(r) >= 2]

    summary = {
        "label": args.label,
        "arm": args.arm or None,
        "workers": args.workers or None,
        "users": int(args.users or 0),
        "trial": args.trial or None,
        "load_seconds": int(args.secs or 0),
        "wall_seconds": int(args.wall or 0),
        "queries": int(args.queries or 0),
        "turns": _search(report, r"completed turns\s*:\s*([0-9]+)", int),
        "throughput_req_min": _search(report, r"throughput\s*:\s*" + NUM),
        "steady_state_req_min": _search(report, r"steady-state.*?\(" + NUM + r" req/min\)"),
        "p50_ms": _search(report, r"turn total.*?p50=\s*([0-9]+)", int),
        "p95_ms": _search(report, r"turn total.*?p95=\s*([0-9]+)", int),
        "requests": requests,
        "errors": errors,
        "error_rate": round(errors / requests, 4) if requests else None,
        "invalid": "** INVALID" in report,
        "py_samples": _search(report, r"([0-9]+) samples of executing Python", int),
        "mem_start_mb": mem[0] if mem else None,
        "mem_peak_mb": max(mem) if mem else None,
        "mem_growth_mb": round(max(mem) - mem[0], 1) if mem else None,
    }
    turns, samples = summary["turns"], summary["py_samples"]
    summary["py_samples_per_turn"] = round(samples / turns, 1) if turns and samples else None

    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
