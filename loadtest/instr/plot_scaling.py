#!/usr/bin/env python3
"""Draw how a qprof matrix scales with concurrent users, as one SVG.

Usage: plot_scaling.py <results/LABEL> <out.svg> [title] [cores]

Reads every trial's summary.json and plots, per worker count, the median over
trials against the user count:
  * steady-state throughput (req/min)
  * turn latency p50 and p95 (s)
  * CPU of the query service, and of the whole host (cores)
  * peak memory of the query service (MB)
Where throughput flattens while latency keeps climbing, the system is saturated;
the CPU panel then says whether the query process sat at its one-core ceiling
or the host ran out of cores. Stdlib only, like the other plots.
"""
from __future__ import annotations

import html
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

W, PH, PAD_L, PAD_R, GAP, COLS = 1100, 230, 64, 30, 70, 2
COLOURS = ["#2a6fdb", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#0b7285"]


def med(vals):
    vals = [v for v in vals if v is not None]
    return st.median(vals) if vals else None


def query_res(r: dict, key: str):
    proc = (r.get("resources") or {}).get("process") or {}
    q = proc.get("query (all processes)") or proc.get("QUERY WORKERS TOTAL")
    return q.get(key) if q else None


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: plot_scaling.py <results/LABEL> <out.svg> [title] [cores]", file=sys.stderr)
        return 2
    root, out = Path(sys.argv[1]), sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else root.name
    cores = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else None

    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for p in sorted(root.glob("*/summary.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("invalid") or not r.get("users"):
            continue
        groups[(str(r.get("workers") or "?"), int(r["users"]))].append(r)
    if not groups:
        return 0

    metrics = {
        "Steady-state throughput (req/min)": lambda rs: {"req/min": med(r.get("steady_state_req_min") for r in rs)},
        "Turn latency (s)": lambda rs: {"p50": med((r.get("p50_ms") or 0) / 1000 or None for r in rs),
                                         "p95": med((r.get("p95_ms") or 0) / 1000 or None for r in rs)},
        "CPU (cores, run average)": lambda rs: {
            "query service": med((query_res(r, "cpu_avg") or 0) / 100 or None for r in rs),
            "query p95": med((query_res(r, "cpu_p95") or 0) / 100 or None for r in rs),
            "host busy": med((((r.get("resources") or {}).get("host") or {}).get("cpu_avg") or 0) / 100 or None for r in rs)},
        "Query service memory peak (MB)": lambda rs: {"peak": med(query_res(r, "mem_peak_mb") for r in rs)},
    }
    users = sorted({u for _, u in groups})
    workers = sorted({w for w, _ in groups}, key=lambda w: int(w) if w.isdigit() else 99)
    pw = (W - PAD_L - PAD_R - (COLS - 1) * GAP) / COLS

    parts = []
    for i, (name, fn) in enumerate(metrics.items()):
        col, row = i % COLS, i // COLS
        x0, y0 = PAD_L + col * (pw + GAP), 60 + row * (PH + 80)
        lines: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for w in workers:
            for u in users:
                rs = groups.get((w, u))
                if not rs:
                    continue
                for k, v in fn(rs).items():
                    if v is not None:
                        lines[f"{k} · {w}w" if len(workers) > 1 else k].append((u, v))
        top = max([v for pts in lines.values() for _, v in pts] + [1e-9]) * 1.1
        if name.startswith("CPU") and cores:
            top = max(top, 1.05)

        def px(u: int) -> float:
            return x0 + (users.index(u) / max(len(users) - 1, 1)) * pw

        def py(v: float) -> float:
            return y0 + PH - v / top * PH

        parts.append(f'<text x="{x0}" y="{y0 - 12}" font-size="13" font-weight="bold">{html.escape(name)}</text>')
        for k in range(5):
            v = top * k / 4
            parts.append(f'<line x1="{x0}" y1="{py(v):.1f}" x2="{x0 + pw}" y2="{py(v):.1f}" stroke="#e8e8e8"/>')
            parts.append(f'<text x="{x0 - 6}" y="{py(v) + 4:.1f}" font-size="10" text-anchor="end" fill="#666">{v:.1f}</text>')
        for u in users:
            parts.append(f'<text x="{px(u):.1f}" y="{y0 + PH + 15}" font-size="10" text-anchor="middle" fill="#666">{u}</text>')
        parts.append(f'<text x="{x0 + pw / 2}" y="{y0 + PH + 30}" font-size="10" text-anchor="middle" fill="#666">concurrent users</text>')
        if name.startswith("CPU"):
            parts.append(f'<line x1="{x0}" y1="{py(1):.1f}" x2="{x0 + pw}" y2="{py(1):.1f}" stroke="#c92a2a" stroke-dasharray="4 4"/>')
            parts.append(f'<text x="{x0 + pw - 2}" y="{py(1) - 3:.1f}" font-size="10" text-anchor="end" fill="#c92a2a">1 core</text>')
        for n, (k, pts) in enumerate(lines.items()):
            c = COLOURS[n % len(COLOURS)]
            parts.append(f'<polyline points="{" ".join(f"{px(u):.1f},{py(v):.1f}" for u, v in pts)}" fill="none" stroke="{c}" stroke-width="2"/>')
            for u, v in pts:
                parts.append(f'<circle cx="{px(u):.1f}" cy="{py(v):.1f}" r="3" fill="{c}"/>')
                parts.append(f'<text x="{px(u):.1f}" y="{py(v) - 6:.1f}" font-size="9.5" text-anchor="middle" fill="{c}">{v:.1f}</text>')
            parts.append(f'<rect x="{x0 + 8}" y="{y0 + 6 + n * 14}" width="9" height="9" fill="{c}"/>')
            parts.append(f'<text x="{x0 + 21}" y="{y0 + 14 + n * 14}" font-size="10">{html.escape(k)}</text>')

    rows = (len(metrics) + COLS - 1) // COLS
    h = 60 + rows * (PH + 80)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" font-family="sans-serif">\n'
           f'<rect width="{W}" height="{h}" fill="white"/>\n'
           f'<text x="{PAD_L}" y="24" font-size="15">{html.escape(title)} — median over trials per point</text>\n'
           + "\n".join(parts) + "\n</svg>\n")
    Path(out).write_text(svg, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
