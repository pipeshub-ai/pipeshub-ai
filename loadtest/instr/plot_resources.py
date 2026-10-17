#!/usr/bin/env python3
"""Render per-container memory from `resources.csv` as one SVG line chart.

Hand-rolled for the same reason as plot_memory.py: stdlib Python only.

Usage: plot_resources.py resources.csv out.svg [title]
"""
from __future__ import annotations

import html
import sys
from collections import defaultdict

W, H, PAD, LEGEND_W = 980, 340, 52, 220
COLOURS = ["#2a6fdb", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#0b7285",
           "#c2255c", "#5f3dc4", "#868e96", "#364fc7", "#a61e4d", "#087f5b"]


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: plot_resources.py <resources.csv> <out.svg> [title]", file=sys.stderr)
        return 2
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    try:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
            next(fh, None)
            for line in fh:
                parts = line.strip().split(",")
                if len(parts) == 5 and parts[1] == "container":
                    try:
                        series[parts[2]].append((int(parts[0]), float(parts[4])))
                    except ValueError:
                        continue
    except OSError:
        return 0
    series = {k: v for k, v in series.items() if len(v) >= 2}
    if not series:
        return 0

    t0 = min(v[0][0] for v in series.values())
    span = (max(v[-1][0] for v in series.values()) - t0) or 1
    top = max(m for v in series.values() for _, m in v) or 1
    plot_w = W - 2 * PAD - LEGEND_W

    def px(t: int) -> float:
        return PAD + (t - t0) / span * plot_w

    def py(m: float) -> float:
        return H - PAD - m / top * (H - 2 * PAD)

    parts = []
    for i in range(5):
        v = top * i / 4
        y = py(v)
        parts.append(f'<line x1="{PAD}" y1="{y:.1f}" x2="{PAD + plot_w}" y2="{y:.1f}" stroke="#e3e3e3"/>')
        parts.append(f'<text x="{PAD - 8}" y="{y + 4:.1f}" font-size="11" text-anchor="end" fill="#666">{v:.0f}</text>')
        x = PAD + plot_w * i / 4
        parts.append(f'<text x="{x:.1f}" y="{H - PAD + 18}" font-size="11" text-anchor="middle" fill="#666">{span * i / 4:.0f}s</text>')

    # Largest peak first, so the legend reads as a ranking.
    ranked = sorted(series.items(), key=lambda kv: -max(m for _, m in kv[1]))
    for n, (name, pts) in enumerate(ranked):
        colour = COLOURS[n % len(COLOURS)]
        line = " ".join(f"{px(t):.1f},{py(m):.1f}" for t, m in pts)
        parts.append(f'<polyline points="{line}" fill="none" stroke="{colour}" stroke-width="1.6"/>')
        ly = PAD + n * 18
        peak = max(m for _, m in pts)
        parts.append(f'<rect x="{W - LEGEND_W}" y="{ly - 9}" width="10" height="10" fill="{colour}"/>')
        parts.append(f'<text x="{W - LEGEND_W + 16}" y="{ly}" font-size="11" fill="#222">'
                     f'{html.escape(name[:22])} &#183; peak {peak:.0f} MB</text>')

    title = html.escape(sys.argv[3] if len(sys.argv) > 3 else "container memory")
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="sans-serif">
<rect width="{W}" height="{H}" fill="white"/>
<text x="{PAD}" y="24" font-size="14" fill="#222">{title}</text>
{chr(10).join(parts)}
<text x="{PAD}" y="{H - 8}" font-size="11" fill="#666">seconds from load start &#183; MB used (excluding page cache)</text>
</svg>
"""
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        fh.write(svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
