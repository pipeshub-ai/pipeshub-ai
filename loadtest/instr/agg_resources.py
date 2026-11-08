#!/usr/bin/env python3
"""Summarise resources.csv into the run report.

    agg_resources.py resources.csv [cores] [--json out.json]

CPU is reported as percent of one core, so 400% means four cores' worth. The
whole point of sampling continuously is that a single spot reading is not
trustworthy here -- two readings of the same run disagreed 20x -- so this prints
the distribution, not just a mean. Memory is shown as average and peak plus
where it started and ended, so growth over the run is visible without the graph.

Process rows are `<service>:<pid>`; a service is reported as the sum of its
processes at each sample, because the indexing pool and uvicorn workers are
child processes that come and go. Query processes are also listed one by one,
since one pegged worker next to idle ones is the signal that matters there.

The SATURATION block is the bottleneck read-out: the share of samples in which
a single query process sat above 90% of one core (the GIL ceiling for one
worker), and in which the host as a whole was above 90% of all its cores.

Reads the CSV written by resource_sampler.sh; prints nothing if it is absent so
runs without the sampler still report normally. `--json` also writes the same
per-row figures for `make_summary.py`, so trials can be compared across a matrix.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

QUERY_TOTAL = "query (all processes)"


def pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(int(q * (len(s) - 1) + 0.5), len(s) - 1)
    return s[i]


def load(path: str) -> dict:
    """{(scope, name): {ts: (cpu, mem)}}; process rows keyed by full `svc:pid`."""
    rows: dict[tuple[str, str], dict[int, tuple[float, float]]] = defaultdict(dict)
    with open(path, encoding="utf-8", errors="replace") as fh:
        next(fh, None)
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) != 5:
                continue
            ts, scope, name, c, m = parts
            try:
                t, c_val, m_val = int(ts), float(c), float(m)
            except ValueError:
                continue
            # Negative CPU is a counter reset from a container restart, in CSVs
            # written before the sampler learned to skip it.
            if c_val < 0:
                continue
            rows[(scope, name)][t] = (c_val, m_val)
    return rows


def by_service(rows: dict) -> dict[str, dict[int, tuple[float, float]]]:
    out: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for (scope, name), series in rows.items():
        if scope != "process":
            continue
        svc = name.split(":", 1)[0]
        # Older CSVs named the query process query_worker:<pid>.
        if svc == "query_worker":
            svc = "query"
        for t, (c, m) in series.items():
            out[svc][t][0] += c
            out[svc][t][1] += m
    return {k: {t: (v[0], v[1]) for t, v in s.items()} for k, s in out.items()}


def stats(series: dict[int, tuple[float, float]]) -> dict:
    ts = sorted(series)
    cpu = [series[t][0] for t in ts]
    mem = [series[t][1] for t in ts]
    return {
        "samples": len(cpu),
        "cpu_avg": round(sum(cpu) / len(cpu), 1),
        "cpu_p50": round(pct(cpu, 0.50), 1),
        "cpu_p95": round(pct(cpu, 0.95), 1),
        "cpu_max": round(max(cpu), 1),
        "mem_avg_mb": round(sum(mem) / len(mem), 1),
        "mem_peak_mb": round(max(mem), 1),
        "mem_start_mb": round(mem[0], 1),
        "mem_end_mb": round(mem[-1], 1),
    }


HDR = "  %-34s %7s %7s %7s %7s %9s %9s %9s %9s" % (
    "name", "cpu avg", "p50", "p95", "max", "mem avg", "peak", "start", "end")


def print_rows(title: str, rows: dict[str, dict]) -> None:
    if not rows:
        return
    print("  -- %s --   cpu in %% of one core, memory in MB" % title)
    print(HDR)
    for name, s in sorted(rows.items(), key=lambda kv: -kv[1]["cpu_avg"]):
        print("  %-34s %7.1f %7.1f %7.1f %7.1f %9.0f %9.0f %9.0f %9.0f" % (
            name[:34], s["cpu_avg"], s["cpu_p50"], s["cpu_p95"], s["cpu_max"],
            s["mem_avg_mb"], s["mem_peak_mb"], s["mem_start_mb"], s["mem_end_mb"]))
    print()


def main(argv: list[str]) -> int:
    json_out = None
    if "--json" in argv:
        i = argv.index("--json")
        json_out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    path = argv[1] if len(argv) > 1 else "resources.csv"
    cores = int(argv[2]) if len(argv) > 2 else 0

    try:
        rows = load(path)
    except FileNotFoundError:
        return 0
    if not rows:
        return 0

    services = by_service(rows)
    table: dict[str, dict] = {"process": {}, "container": {}, "host": {}, "saturation": {}}
    for svc, series in services.items():
        table["process"][svc] = stats(series)
    if "query" in table["process"]:
        table["process"][QUERY_TOTAL] = table["process"].pop("query")
    query_procs = {name: stats(s) for (scope, name), s in rows.items()
                   if scope == "process" and name.split(":")[0] in ("query", "query_worker")}
    for (scope, name), series in rows.items():
        if scope == "container":
            table["container"][name] = stats(series)

    print_rows("PER-SERVICE (inside the app container, all processes of each service)", table["process"])
    if len(query_procs) > 1:
        print_rows("QUERY PROCESSES (one row per process)", query_procs)
    print_rows("PER-CONTAINER", table["container"])

    host_cpu = rows.get(("host", "cpu"), {})
    if host_cpu:
        def h(name: str) -> list[float]:
            s = rows.get(("host", name), {})
            return [s[t][0 if name != "mem" else 1] for t in sorted(s)]
        cpu, io, steal, mem, load_ = h("cpu"), h("iowait"), h("steal"), h("mem"), h("loadavg")
        cap = (cores or 1) * 100
        print("  -- HOST --   cpu in %% of one core (%s available)" % (f"{cap}%" if cores else "?"))
        print("  cpu busy  avg %6.0f%%  p50 %6.0f%%  p95 %6.0f%%  max %6.0f%%" % (
            sum(cpu) / len(cpu), pct(cpu, .5), pct(cpu, .95), max(cpu)))
        if io:
            print("  iowait    avg %6.0f%%  p95 %6.0f%%     steal avg %.0f%%  max %.0f%%" % (
                sum(io) / len(io), pct(io, .95), sum(steal) / max(len(steal), 1), max(steal or [0])))
        if mem:
            print("  memory    avg %6.0f MB  peak %6.0f MB used (excluding page cache)" % (
                sum(mem) / len(mem), max(mem)))
        if load_:
            print("  load      avg %6.2f    p95 %6.2f    max %6.2f" % (
                sum(load_) / len(load_), pct(load_, .95), max(load_)))
        table["host"] = {
            "cpu_avg": round(sum(cpu) / len(cpu), 1), "cpu_p95": round(pct(cpu, .95), 1),
            "cpu_max": round(max(cpu), 1), "iowait_avg": round(sum(io) / len(io), 1) if io else None,
            "mem_peak_mb": round(max(mem), 1) if mem else None,
        }
        print()
    else:
        load_ = [v[0] for _, v in sorted(rows.get(("host", "loadavg"), {}).items())]
        if load_:
            print("  host load: mean %.2f  p95 %.2f  max %.2f" % (sum(load_) / len(load_), pct(load_, .95), max(load_)))

    # Bottleneck read-out.
    sat = table["saturation"]
    if query_procs:
        busiest = max((s for (sc, n), s in rows.items() if sc == "process" and n.split(":")[0] in ("query", "query_worker")),
                      key=lambda s: sum(v[0] for v in s.values()) / max(len(s), 1))
        vals = [v[0] for v in busiest.values()]
        sat["query_proc_over_90pct_core"] = round(100 * sum(v > 90 for v in vals) / len(vals), 1)
    if host_cpu and cores:
        vals = [v[0] for v in host_cpu.values()]
        sat["host_over_90pct_all_cores"] = round(100 * sum(v > 0.9 * cores * 100 for v in vals) / len(vals), 1)
    if sat:
        print("  -- SATURATION (share of samples) --")
        if "query_proc_over_90pct_core" in sat:
            print("  busiest query process above 90%% of one core : %5.1f%%   (one event loop = one core ceiling)"
                  % sat["query_proc_over_90pct_core"])
        if "host_over_90pct_all_cores" in sat:
            print("  host above 90%% of all %d cores               : %5.1f%%" % (cores, sat["host_over_90pct_all_cores"]))
        print()

    ckeys = list(table["container"])
    if ckeys:
        conts = [rows[("container", k)] for k in ckeys]
        common = sorted(set.intersection(*(set(s) for s in conts)))
        if common:
            total = stats({t: (sum(s[t][0] for s in conts), sum(s[t][1] for s in conts)) for t in common})
            table["container"]["ALL CONTAINERS"] = total
            avail = " of %d00%% available" % cores if cores else ""
            print("  all containers: cpu avg %.0f%%%s (p95 %.0f%%, max %.0f%%), memory avg %.0f MB, peak %.0f MB" % (
                total["cpu_avg"], avail, total["cpu_p95"], total["cpu_max"], total["mem_avg_mb"], total["mem_peak_mb"]))

    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(table, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
