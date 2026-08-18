#!/usr/bin/env python3
"""Summarise resources.csv into the run report.

CPU is reported as percent of one core, so 400% means four cores' worth. The
whole point of sampling continuously is that a single spot reading is not
trustworthy here -- two readings of the same run disagreed 20x -- so this prints
the distribution (p50/p95/max), not just a mean.

Reads the CSV written by resource_sampler.sh; prints nothing if it is absent so
runs without the sampler still report normally.
"""
from __future__ import annotations

import sys
from collections import defaultdict


def pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(int(q * (len(s) - 1) + 0.5), len(s) - 1)
    return s[i]


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "resources.csv"
    cores = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    cpu: dict[tuple[str, str], list[float]] = defaultdict(list)
    rss: dict[tuple[str, str], list[float]] = defaultdict(list)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            next(fh, None)
            for line in fh:
                parts = line.strip().split(",")
                if len(parts) != 5:
                    continue
                _, scope, name, c, m = parts
                try:
                    cpu[(scope, name)].append(float(c))
                    rss[(scope, name)].append(float(m))
                except ValueError:
                    continue
    except FileNotFoundError:
        return 0
    if not cpu:
        return 0

    # workers roll up into one line as well as appearing individually
    worker_series: list[list[float]] = [
        v for (scope, name), v in cpu.items() if scope == "process" and name.startswith("query_worker")
    ]
    if worker_series:
        n = min(len(v) for v in worker_series)
        cpu[("process", "QUERY WORKERS TOTAL")] = [
            sum(v[i] for v in worker_series) for i in range(n)
        ]
        rss[("process", "QUERY WORKERS TOTAL")] = [
            sum(rss[k][i] for k in rss if k[0] == "process" and k[1].startswith("query_worker"))
            for i in range(n)
        ]

    def rows(scope: str) -> list[tuple]:
        out = []
        for (s, name), vals in cpu.items():
            if s != scope:
                continue
            out.append((name, len(vals), sum(vals) / len(vals), pct(vals, 0.5),
                        pct(vals, 0.95), max(vals), max(rss[(s, name)] or [0])))
        return sorted(out, key=lambda r: -r[2])

    hdr = "  %-26s %5s %8s %8s %8s %8s %10s" % (
        "name", "n", "mean%", "p50%", "p95%", "max%", "peak RSS MB")
    for scope, title in (("process", "PER-PROCESS (inside the app container)"),
                         ("container", "PER-CONTAINER")):
        rs = rows(scope)
        if not rs:
            continue
        print("  -- %s --" % title)
        print(hdr)
        for name, n, mean, p50, p95, mx, peak in rs:
            print("  %-26s %5d %8.1f %8.1f %8.1f %8.1f %10.0f" % (name, n, mean, p50, p95, mx, peak))
        print()

    host = cpu.get(("host", "loadavg"), [])
    if host:
        line = "  host load: mean %.2f  p50 %.2f  p95 %.2f  max %.2f" % (
            sum(host) / len(host), pct(host, 0.5), pct(host, 0.95), max(host))
        if cores:
            line += "   (%d cores)" % cores
        print(line)

    # total CPU actually consumed across containers, vs what the box has
    ctotal = [v for (s, _), v in cpu.items() if s == "container"]
    if ctotal and cores:
        n = min(len(v) for v in ctotal)
        summed = [sum(v[i] for v in ctotal) for i in range(n)]
        print("  all containers: mean %.0f%% of %d00%% available (p95 %.0f%%)" % (
            sum(summed) / len(summed), cores, pct(summed, 0.95)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
