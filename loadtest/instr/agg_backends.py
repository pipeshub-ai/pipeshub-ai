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


def main() -> int:
    windows: dict[str, list[tuple[int, float, float, float, float]]] = collections.defaultdict(list)
    for line in sys.stdin:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
