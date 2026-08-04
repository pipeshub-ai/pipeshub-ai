#!/usr/bin/env python3
"""Render `memory.csv` as an SVG line chart.

Hand-rolled rather than matplotlib: the harness otherwise needs nothing beyond
a stdlib Python, and a load-test box is the last place to start installing a
plotting stack.

Usage: plot_memory.py memory.csv out.svg [title]
"""
from __future__ import annotations

import csv
import sys

W, H, PAD = 900, 260, 46


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: plot_memory.py <memory.csv> <out.svg> [title]", file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1], newline="", encoding="utf-8") as fh:
            pts = [(int(r[0]), float(r[1])) for r in csv.reader(fh) if len(r) >= 2]
    except (OSError, ValueError):
        return 0
    if len(pts) < 2:
        return 0

    t0 = pts[0][0]
    xs = [t - t0 for t, _ in pts]
    ys = [v for _, v in pts]
    lo, hi = min(ys), max(ys)
    # Separate scaling bound from the reported peak: widening `hi` for a flat
    # run kept the line off the top edge, but also made the caption claim a peak
    # 1 MB above reality and growth of +1 MB where there was none.
    top = hi if hi - lo >= 1 else lo + 1
    span = xs[-1] or 1

    def px(x: float) -> float:
        return PAD + (x / span) * (W - 2 * PAD)

    def py(y: float) -> float:
        return H - PAD - ((y - lo) / (top - lo)) * (H - 2 * PAD)

    line = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(xs, ys))
    area = f"{px(0):.1f},{H - PAD} {line} {px(xs[-1]):.1f},{H - PAD}"
    title = sys.argv[3] if len(sys.argv) > 3 else "query service memory"

    # Five gridlines, labelled in MB; x labelled in seconds from load start.
    grid = []
    for i in range(5):
        v = lo + (top - lo) * i / 4
        y = py(v)
        grid.append(f'<line x1="{PAD}" y1="{y:.1f}" x2="{W - PAD}" y2="{y:.1f}" '
                    f'stroke="#e3e3e3" stroke-width="1"/>')
        grid.append(f'<text x="{PAD - 8}" y="{y + 4:.1f}" font-size="11" '
                    f'text-anchor="end" fill="#666">{v:.0f}</text>')
    for i in range(5):
        x = px(span * i / 4)
        grid.append(f'<text x="{x:.1f}" y="{H - PAD + 18:.0f}" font-size="11" '
                    f'text-anchor="middle" fill="#666">{span * i / 4:.0f}s</text>')

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="white"/>
<text x="{PAD}" y="24" font-size="14" font-family="sans-serif" fill="#222">{title}</text>
<text x="{W - PAD}" y="24" font-size="12" font-family="sans-serif" text-anchor="end" fill="#666">
start {ys[0]:.0f} MB &#183; peak {hi:.0f} MB &#183; end {ys[-1]:.0f} MB &#183; growth +{hi - ys[0]:.0f} MB</text>
{chr(10).join(grid)}
<polygon points="{area}" fill="#4a90d9" fill-opacity="0.12"/>
<polyline points="{line}" fill="none" stroke="#4a90d9" stroke-width="2"/>
<text x="{PAD}" y="{H - 8}" font-size="11" font-family="sans-serif" fill="#666">seconds from load start &#183; MB RSS</text>
</svg>
"""
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        fh.write(svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
