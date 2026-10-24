#!/usr/bin/env python3
"""Summarise BACKENDAGG lines (see instr/backend_timing.py) from stdin.

Latency is measured caller-side, inside the query worker, so it includes time
the request spent queued at the backend — which is what actually delays a turn.
A backend whose own p50 stays flat while this grows is not itself slow; the
queue in front of it is.
"""
from __future__ import annotations

import collections
import re
import statistics as st
import sys

LINE = re.compile(r"kind=(\w+) n=(\d+) qps=([\d.]+) p50=(\d+) p95=(\d+) max=(\d+)")
OP = re.compile(r"BACKENDOP .*?kind=(\w+) op=(\S+) n=(\d+) ms=(\d+)")


def main() -> int:
    windows: dict[str, list[tuple[int, float, float, float, float]]] = collections.defaultdict(list)
    ops: dict[tuple[str, str], list[int]] = collections.defaultdict(lambda: [0, 0])
    for line in sys.stdin:
        o = OP.search(line)
        if o:
            ops[(o.group(1), o.group(2))][0] += int(o.group(3))
            ops[(o.group(1), o.group(2))][1] += int(o.group(4))
            continue
        m = LINE.search(line)
        if m:
            windows[m.group(1)].append((
                int(m.group(2)), float(m.group(3)), float(m.group(4)),
                float(m.group(5)), float(m.group(6)),
            ))
    if not windows:
        print("  (no BACKENDAGG lines — run ./instrument.sh on)")
        return 0

    print(f"  {'backend':<12}{'calls':>9}{'qps':>8}{'p50':>9}{'p95 worst':>12}{'max':>9}")
    for kind, rows in sorted(windows.items(), key=lambda kv: -sum(r[0] for r in kv[1])):
        calls = sum(r[0] for r in rows)
        print(
            f"  {kind:<12}{calls:>9}"
            # Median of the per-window rates, not calls/elapsed: the run's first
            # and last windows are partial, and averaging over them understates
            # the rate the backend actually sustained.
            f"{st.median(r[1] for r in rows):>8.1f}"
            f"{st.median(r[2] for r in rows):>8.0f}ms"
            f"{max(r[3] for r in rows):>11.0f}ms"
            f"{max(r[4] for r in rows):>8.0f}ms"
        )
    if ops:
        turns = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
        print()
        print(f"  {'neo4j calls by provider method <- caller':<70}{'calls':>8}{'/turn':>8}{'total s':>9}")
        for (_kind, op), (n, ms) in sorted(ops.items(), key=lambda kv: -kv[1][0])[:15]:
            per = f"{n / turns:.1f}" if turns else "-"
            print(f"  {op[:70]:<70}{n:>8}{per:>8}{ms / 1000:>9.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
