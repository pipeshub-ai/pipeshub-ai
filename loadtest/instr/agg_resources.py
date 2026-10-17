#!/usr/bin/env python3
"""Summarise resources.csv into the run report.

    agg_resources.py resources.csv [cores] [--json out.json]

CPU is reported as percent of one core, so 400% means four cores' worth. The
whole point of sampling continuously is that a single spot reading is not
trustworthy here -- two readings of the same run disagreed 20x -- so this prints
the distribution, not just a mean. Memory is shown as average and peak plus
where it started and ended, so growth over the run is visible without the graph.

Reads the CSV written by resource_sampler.sh; prints nothing if it is absent so
runs without the sampler still report normally. `--json` also writes the same
per-row figures for `make_summary.py`, so trials can be compared across a matrix.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

WORKERS_TOTAL = "QUERY WORKERS TOTAL"


def pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(int(q * (len(s) - 1) + 0.5), len(s) - 1)
    return s[i]


def load(path: str) -> tuple[dict, dict]:
    cpu: dict[tuple[str, str], list[float]] = defaultdict(list)
    mem: dict[tuple[str, str], list[float]] = defaultdict(list)
    with open(path, encoding="utf-8", errors="replace") as fh:
        next(fh, None)
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) != 5:
                continue
            _, scope, name, c, m = parts
            try:
                c_val, m_val = float(c), float(m)
            except ValueError:
                continue
            # Negative CPU is a counter reset from a container restart, in CSVs
            # written before the sampler learned to skip it.
            if c_val < 0:
                continue
            cpu[(scope, name)].append(c_val)
            mem[(scope, name)].append(m_val)
    return cpu, mem


def add_worker_total(cpu: dict, mem: dict) -> None:
    keys = [k for k in cpu if k[0] == "process" and k[1].startswith("query_worker")]
    if not keys:
        return
    n = min(len(cpu[k]) for k in keys)
    cpu[("process", WORKERS_TOTAL)] = [sum(cpu[k][i] for k in keys) for i in range(n)]
    mem[("process", WORKERS_TOTAL)] = [sum(mem[k][i] for k in keys) for i in range(n)]


def stats(cpu: list[float], mem: list[float]) -> dict:
    return {
        "samples": len(cpu),
        "cpu_avg": round(sum(cpu) / len(cpu), 1),
        "cpu_p95": round(pct(cpu, 0.95), 1),
        "cpu_max": round(max(cpu), 1),
        "mem_avg_mb": round(sum(mem) / len(mem), 1) if mem else 0.0,
        "mem_peak_mb": round(max(mem), 1) if mem else 0.0,
        "mem_start_mb": round(mem[0], 1) if mem else 0.0,
        "mem_end_mb": round(mem[-1], 1) if mem else 0.0,
    }


def main(argv: list[str]) -> int:
    json_out = None
    if "--json" in argv:
        i = argv.index("--json")
        json_out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    path = argv[1] if len(argv) > 1 else "resources.csv"
    cores = int(argv[2]) if len(argv) > 2 else 0

    try:
        cpu, mem = load(path)
    except FileNotFoundError:
        return 0
    if not cpu:
        return 0
    add_worker_total(cpu, mem)

    table: dict[str, dict[str, dict]] = {"process": {}, "container": {}}
    for (scope, name), vals in cpu.items():
        if scope in table:
            table[scope][name] = stats(vals, mem[(scope, name)])

    hdr = "  %-28s %7s %7s %7s %9s %9s %9s %9s" % (
        "name", "cpu avg", "p95", "max", "mem avg", "peak", "start", "end")
    for scope, title in (("process", "PER-PROCESS (inside the app container)"),
                         ("container", "PER-CONTAINER")):
        rows = sorted(table[scope].items(), key=lambda kv: -kv[1]["cpu_avg"])
        if not rows:
            continue
        print("  -- %s --   cpu in %% of one core, memory in MB" % title)
        print(hdr)
        for name, s in rows:
            print("  %-28s %7.1f %7.1f %7.1f %9.0f %9.0f %9.0f %9.0f" % (
                name, s["cpu_avg"], s["cpu_p95"], s["cpu_max"],
                s["mem_avg_mb"], s["mem_peak_mb"], s["mem_start_mb"], s["mem_end_mb"]))
        print()

    host = cpu.get(("host", "loadavg"), [])
    if host:
        line = "  host load: mean %.2f  p50 %.2f  p95 %.2f  max %.2f" % (
            sum(host) / len(host), pct(host, 0.5), pct(host, 0.95), max(host))
        if cores:
            line += "   (%d cores)" % cores
        print(line)

    # total CPU and memory actually consumed across containers, vs what the box has
    ckeys = [k for k in cpu if k[0] == "container"]
    if ckeys:
        n = min(len(cpu[k]) for k in ckeys)
        csum = [sum(cpu[k][i] for k in ckeys) for i in range(n)]
        msum = [sum(mem[k][i] for k in ckeys) for i in range(n)]
        total = stats(csum, msum)
        table["container"]["ALL CONTAINERS"] = total
        avail = " of %d00%% available" % cores if cores else ""
        print("  all containers: cpu avg %.0f%%%s (max %.0f%%), memory avg %.0f MB, peak %.0f MB" % (
            total["cpu_avg"], avail, total["cpu_max"], total["mem_avg_mb"], total["mem_peak_mb"]))

    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(table, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
