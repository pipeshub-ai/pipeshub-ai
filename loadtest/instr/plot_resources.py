#!/usr/bin/env python3
"""Render a run's resource timeline as one SVG of stacked panels.

Hand-rolled for the same reason as plot_memory.py: stdlib Python only.

Usage: plot_resources.py resources.csv out.svg [title] [requests.csv] [cores]

Panels share one time axis (seconds from the first sample), so a saturation can
be read against the load at the same moment:
  1. turns in flight (from requests.csv ts_start/ts_end), with completions
  2. host CPU busy and iowait, in cores, against the core count
  3. CPU per container, in cores
  4. CPU per service inside the app container, in cores (all processes summed)
  5. memory per container, MB (excluding page cache)
  6. memory per service, MB, plus host memory in use
"""
from __future__ import annotations

import csv
import html
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

W, PANEL_H, PAD_L, PAD_R, LEGEND_W, GAP = 1100, 210, 60, 20, 250, 46
COLOURS = ["#2a6fdb", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#0b7285",
           "#c2255c", "#5f3dc4", "#868e96", "#364fc7", "#a61e4d", "#087f5b"]
PLOT_W = W - PAD_L - PAD_R - LEGEND_W

Series = Dict[str, List[Tuple[float, float]]]


def load_resources(path: str) -> tuple[Series, Series, Series, Series, Series]:
    cont_cpu: Series = defaultdict(list)
    cont_mem: Series = defaultdict(list)
    svc: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    host: Series = defaultdict(list)
    with open(path, encoding="utf-8", errors="replace") as fh:
        next(fh, None)
        for line in fh:
            p = line.strip().split(",")
            if len(p) != 5:
                continue
            try:
                t, c, m = int(p[0]), float(p[3]), float(p[4])
            except ValueError:
                continue
            if c < 0:
                continue
            scope, name = p[1], p[2]
            if scope == "container":
                cont_cpu[name].append((t, c / 100))
                cont_mem[name].append((t, m))
            elif scope == "process":
                s = name.split(":", 1)[0].replace("query_worker", "query")
                svc[s][t][0] += c / 100
                svc[s][t][1] += m
            elif scope == "host":
                if name == "mem":
                    host["host memory used"].append((t, m))
                elif name in ("cpu", "iowait", "steal"):
                    host[name].append((t, c / 100))
    # Compose names are "<project>-<service>-<n>"; the legend has room for the service.
    prefix = os.path.commonprefix(list(cont_cpu)) if len(cont_cpu) > 1 else ""
    prefix = prefix[:prefix.rfind("-") + 1]

    def short(n: str) -> str:
        return re.sub(r"-\d+$", "", n[len(prefix):])
    cont_cpu = {short(k): v for k, v in cont_cpu.items()}
    cont_mem = {short(k): v for k, v in cont_mem.items()}
    svc_cpu = {k: sorted((t, v[0]) for t, v in s.items()) for k, s in svc.items()}
    svc_mem = {k: sorted((t, v[1]) for t, v in s.items()) for k, s in svc.items()}
    return cont_cpu, cont_mem, svc_cpu, svc_mem, host


def load_requests(path: str) -> tuple[list[tuple[float, float]], list[float]]:
    spans, ends = [], []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    a, b = float(r["ts_start"]), float(r["ts_end"])
                except (KeyError, TypeError, ValueError):
                    continue
                spans.append((a, b))
                if r.get("http_code") == "200" and (r.get("curl_exit") or "0") == "0":
                    ends.append(b)
    except OSError:
        pass  # no request log: the plots simply have no in-flight series
    return spans, ends


def in_flight(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ev = sorted([(a, 1) for a, _ in spans] + [(b, -1) for _, b in spans])
    n, pts = 0, []
    for t, d in ev:
        pts.append((t, n))
        n += d
        pts.append((t, n))
    return pts


class Chart:
    def __init__(self, t0: float, span: float):
        self.t0, self.span, self.parts, self.y = t0, span or 1, [], 40

    def x(self, t: float) -> float:
        return PAD_L + (t - self.t0) / self.span * PLOT_W

    def panel(self, title: str, series: Series, unit: str, cap: float | None = None,
              marks: list[float] | None = None) -> None:
        series = {k: v for k, v in series.items() if len(v) >= 2}
        top = max([v for s in series.values() for _, v in s] + [cap or 0, 1e-9])
        top *= 1.05
        y0, h = self.y, PANEL_H
        p = self.parts

        def py(v: float) -> float:
            return y0 + h - v / top * h

        p.append(f'<text x="{PAD_L}" y="{y0 - 10}" font-size="13" font-weight="bold" fill="#222">{html.escape(title)}</text>')
        for i in range(5):
            v = top * i / 4
            yy = py(v)
            p.append(f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{PAD_L + PLOT_W}" y2="{yy:.1f}" stroke="#e8e8e8"/>')
            p.append(f'<text x="{PAD_L - 6}" y="{yy + 4:.1f}" font-size="10" text-anchor="end" fill="#666">{v:.1f}</text>' if top < 10
                     else f'<text x="{PAD_L - 6}" y="{yy + 4:.1f}" font-size="10" text-anchor="end" fill="#666">{v:.0f}</text>')
            xx = PAD_L + PLOT_W * i / 4
            p.append(f'<text x="{xx:.1f}" y="{y0 + h + 14}" font-size="10" text-anchor="middle" fill="#666">{self.span * i / 4:.0f}s</text>')
        p.append(f'<text x="{PAD_L - 44}" y="{y0 + h / 2}" font-size="10" fill="#666" transform="rotate(-90 {PAD_L - 44} {y0 + h / 2})" text-anchor="middle">{html.escape(unit)}</text>')
        if cap:
            p.append(f'<line x1="{PAD_L}" y1="{py(cap):.1f}" x2="{PAD_L + PLOT_W}" y2="{py(cap):.1f}" stroke="#c92a2a" stroke-dasharray="5 4"/>')
            p.append(f'<text x="{PAD_L + PLOT_W - 4}" y="{py(cap) - 4:.1f}" font-size="10" text-anchor="end" fill="#c92a2a">capacity {cap:g}</text>')
        ranked = sorted(series.items(), key=lambda kv: -max(v for _, v in kv[1]))
        for n, (name, pts) in enumerate(ranked[:12]):
            colour = COLOURS[n % len(COLOURS)]
            line = " ".join(f"{self.x(t):.1f},{py(v):.1f}" for t, v in pts)
            p.append(f'<polyline points="{line}" fill="none" stroke="{colour}" stroke-width="1.5"/>')
            avg = sum(v for _, v in pts) / len(pts)
            peak = max(v for _, v in pts)
            ly = y0 + 10 + n * 16
            fmt = (lambda v: f"{v:.2f}") if top < 10 else (lambda v: f"{v:.0f}")
            p.append(f'<rect x="{W - LEGEND_W}" y="{ly - 9}" width="10" height="10" fill="{colour}"/>')
            p.append(f'<text x="{W - LEGEND_W + 15}" y="{ly}" font-size="10.5" fill="#222">'
                     f'{html.escape(name[:20])} avg {fmt(avg)} · max {fmt(peak)}</text>')
        for t in marks or []:
            xx = self.x(t)
            p.append(f'<line x1="{xx:.1f}" y1="{y0 + h - 6}" x2="{xx:.1f}" y2="{y0 + h}" stroke="#2b8a3e" stroke-width="1"/>')
        self.y += h + GAP + 18


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: plot_resources.py <resources.csv> <out.svg> [title] [requests.csv] [cores]", file=sys.stderr)
        return 2
    title = sys.argv[3] if len(sys.argv) > 3 else "resources"
    req = sys.argv[4] if len(sys.argv) > 4 else ""
    cores = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5].isdigit() else None
    try:
        cont_cpu, cont_mem, svc_cpu, svc_mem, host = load_resources(sys.argv[1])
    except OSError:
        return 0
    spans, ends = load_requests(req) if req else ([], [])
    times = [t for s in (cont_cpu, svc_cpu, host) for v in s.values() for t, _ in v] + [a for a, _ in spans]
    if not times:
        return 0
    t0 = min(times)
    span = max(times + [b for _, b in spans]) - t0
    ch = Chart(t0, span)
    if spans:
        ch.panel("Turns in flight (green ticks: a turn completed)", {"in flight": in_flight(spans)}, "turns", marks=ends)
    hc = {k: v for k, v in host.items() if k in ("cpu", "iowait", "steal") and any(x for _, x in v)}
    if hc:
        ch.panel("Host CPU", {("busy" if k == "cpu" else k): v for k, v in hc.items()}, "cores", cap=cores)
    ch.panel("CPU per container", cont_cpu, "cores", cap=cores)
    ch.panel("CPU per service in the app container (all its processes)", svc_cpu, "cores")
    ch.panel("Memory per container (excluding page cache)", cont_mem, "MB")
    mem = dict(svc_mem)
    if host.get("host memory used"):
        mem["host memory used"] = host["host memory used"]
    ch.panel("Memory per service in the app container, and host memory in use", mem, "MB")

    height = ch.y + 10
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}" font-family="sans-serif">\n'
           f'<rect width="{W}" height="{height}" fill="white"/>\n'
           f'<text x="{PAD_L}" y="20" font-size="15" fill="#111">{html.escape(title)}</text>\n'
           + "\n".join(ch.parts) + "\n</svg>\n")
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        fh.write(svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
