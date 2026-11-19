"""Regenerates ``tables.pdf`` and ``tables.boxes.json`` (run once; the outputs are committed).

Each page holds one or two small generated tables (ruled and unruled, currency and bracket figures, a spanning
header, a long table) at different places and sizes, so a request that crops before resizing differs from one that
resizes the page first. Boxes are in points, origin top left, as the layout pass reports them.

    python make_fixtures.py
"""
from __future__ import annotations

import json
import os

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = letter


def table(c: canvas.Canvas, x: float, top: float, rows: list[list[str]], col_w: list[float], row_h: float = 15.0, *, ruled: bool, size: int = 9, right_from: int = 1) -> list[float]:
    """Draw *rows*; text columns from ``right_from`` are right aligned. Returns the box (x0, top, x1, bottom), padded."""
    c.setFont("Helvetica", size)
    width = sum(col_w)
    height = row_h * len(rows)
    for r, row in enumerate(rows):
        y = H - (top + (r + 1) * row_h) + 4
        x_cell = x
        for k, text in enumerate(row):
            if k >= right_from:
                c.drawRightString(x_cell + col_w[k] - 4, y, text)
            else:
                c.drawString(x_cell + 3, y, text)
            x_cell += col_w[k]
    if ruled:
        for r in range(len(rows) + 1):
            yy = H - (top + r * row_h)
            c.line(x, yy, x + width, yy)
        xx = x
        for w in [0, *col_w]:
            xx += w
            c.line(xx, H - top, xx, H - (top + height))
    return [x - 4, top - 4, x + width + 4, top + height + 4]


def main() -> None:
    boxes: dict[str, list[list[float]]] = {}
    c = canvas.Canvas(os.path.join(HERE, "tables.pdf"), pagesize=letter, invariant=1)
    # page 1: ruled financial table
    rows = [["Item", "2023", "2022", "Change", "Note"]] + [
        [f"Revenue line {i}", f"${1000 + 37 * i:,}", f"${900 + 31 * i:,}", f"({10 + i})" if i % 2 else f"{5 + i}.5%", "a" if i % 3 == 0 else "-"]
        for i in range(1, 9)
    ]
    boxes["1"] = [table(c, 72, 120, rows, [150, 70, 70, 70, 50], ruled=True)]
    c.showPage()
    # page 2: unruled table, low on the page
    rows = [["Region", "Q1", "Q2", "Q3", "Q4"]] + [[f"Area {chr(65 + i)}", *(f"{(i + 3) * (q + 2) * 11:,}" for q in range(4))] for i in range(12)]
    small_top = [["Rank", "Share", "Delta"]] + [[str(i), f"{i * 7}.{i}%", f"+{i * 3}"] for i in range(1, 5)]
    boxes["2"] = [table(c, 90, 400, rows, [120, 60, 60, 60, 60], ruled=False), table(c, 90, 120, small_top, [70, 70, 70], ruled=True)]
    c.showPage()
    # page 3: two tables, a narrow one and a wide one
    a = [["Key", "Value"]] + [[f"param_{i}", f"{i * 3.25:.2f}"] for i in range(5)]
    b = [["Name", "A", "B", "C", "D", "E"]] + [[f"row {i}", *(str(i * k + 7) for k in range(1, 6))] for i in range(6)]
    boxes["3"] = [table(c, 72, 100, a, [100, 80], ruled=True), table(c, 72, 300, b, [90, 55, 55, 55, 55, 55], ruled=False, size=8)]
    c.showPage()
    # page 4: spanning header over two groups, ruled
    c.setFont("Helvetica-Bold", 9)
    x, top = 100, 150
    c.drawString(x + 3, H - top - 11, "Metric")
    c.drawCentredString(x + 120 + 70, H - top - 11, "Group one")
    c.drawCentredString(x + 120 + 210, H - top - 11, "Group two")
    rows = [["", "x", "y", "z", "u", "v", "w"]] + [[f"m{i}", *(f"{i + k}" for k in range(6))] for i in range(1, 7)]
    body = table(c, x, top + 15, rows, [120, 46, 46, 46, 46, 46, 46], ruled=True)
    c.line(x, H - top, x + 396, H - top)
    c.line(x, H - top - 15, x + 396, H - top - 15)
    boxes["4"] = [[body[0], top - 4, body[2], body[3]]]
    c.showPage()
    # page 5: a small two-row table and a long three-column list
    small = [["Total", "1,234", "56.7"], ["Average", "12", "3.4"]]
    long_rows = [["Code", "Description", "Count"]] + [[f"C{i:03d}", f"Item number {i} of the list", str(i * 17 % 91)] for i in range(24)]
    boxes["5"] = [table(c, 72, 90, small, [100, 60, 60], ruled=True), table(c, 72, 200, long_rows, [60, 220, 60], row_h=13.0, ruled=False, size=8, right_from=2)]
    c.showPage()
    c.save()
    with open(os.path.join(HERE, "tables.boxes.json"), "w") as f:
        json.dump(boxes, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
