"""The one definition of a drawn rule on a PDF page, shared by everything that asks whether a cell straddles one.

A rule is a stroked line, or a filled rectangle up to ``RULE_MAX_THICKNESS`` points thick (a header rule is often drawn as
a thin filled bar), or an edge of a *stroked* rectangle (an outline). The edges of a background fill, a rectangle wider than
``RULE_MAX_THICKNESS`` in both dimensions that is only filled, are not rules. The thickness is ``table_reconcile.SNAP``
(a test keeps the two equal; this module imports nothing heavy so any caller can use it).

Strokes are ``(position, start, end)`` tuples in the text layer's coordinates (points, origin top left): a horizontal one is
``(y, x0, x1)``, a vertical one ``(x, y0, y1)``.
"""
from __future__ import annotations

from typing import Any

RULE_MAX_THICKNESS = 3.0  # pt; equals table_reconcile.SNAP
MIN_HORIZONTAL_LENGTH = 8.0
MIN_VERTICAL_LENGTH = 6.0

Stroke = tuple[float, float, float]
Strokes = tuple[list[Stroke], list[Stroke]]  # (horizontal, vertical)


def drawn_rules(page: object) -> Strokes | None:
    """The drawn rules of a pdfplumber page, or ``None`` when the page cannot be read."""
    try:
        h: list[Stroke] = []
        v: list[Stroke] = []
        for ln in page.lines or []:  # type: ignore[attr-defined]
            x0, x1, t, b = ln["x0"], ln["x1"], ln["top"], ln["bottom"]
            if abs(b - t) <= RULE_MAX_THICKNESS and x1 - x0 >= MIN_HORIZONTAL_LENGTH:
                h.append(((t + b) / 2, x0, x1))
            elif abs(x1 - x0) <= RULE_MAX_THICKNESS and b - t >= MIN_VERTICAL_LENGTH:
                v.append(((x0 + x1) / 2, t, b))
        for r in page.rects or []:  # type: ignore[attr-defined]
            x0, x1, t, b = r["x0"], r["x1"], r["top"], r["bottom"]
            w, hh = x1 - x0, b - t
            if hh <= RULE_MAX_THICKNESS and w >= MIN_HORIZONTAL_LENGTH:
                h.append(((t + b) / 2, x0, x1))
            elif w <= RULE_MAX_THICKNESS and hh >= MIN_VERTICAL_LENGTH:
                v.append(((x0 + x1) / 2, t, b))
            elif _is_outline(r, w, hh):
                h += [(t, x0, x1), (b, x0, x1)]
                v += [(x0, t, b), (x1, t, b)]
        return h, v
    except Exception:
        return None


def _is_outline(rect: dict[str, Any], w: float, h: float) -> bool:
    """A stroked rectangle (its edges are drawn lines), as opposed to a background fill."""
    return bool(
        rect.get("stroke") and w >= MIN_HORIZONTAL_LENGTH and h >= MIN_VERTICAL_LENGTH
        and rect.get("linewidth", 0) > 0 and rect.get("stroking_color") is not None
    )


def strokes_touching(strokes: Strokes | None, box: tuple[float, ...]) -> dict[str, list[list[float]]] | None:
    """The strokes that touch *box*, as lists (for a spool record); ``None`` when the page's strokes are unknown."""
    if strokes is None:
        return None
    h = [[round(y, 1), round(a, 1), round(c, 1)] for y, a, c in strokes[0] if box[1] <= y <= box[3] and c >= box[0] and a <= box[2]]
    v = [[round(x, 1), round(a, 1), round(c, 1)] for x, a, c in strokes[1] if box[0] <= x <= box[2] and c >= box[1] and a <= box[3]]
    return {"h": h, "v": v}


def crossings(words_by_id: dict[int, dict[str, Any]], cells: list[dict[str, Any]], h_rules: list[Stroke], v_rules: list[Stroke]) -> tuple[int, int, int]:
    """Cells holding words on both sides of a drawn rule that spans them, as ``(horizontal, vertical, any)``: the cells that
    cross a horizontal rule, the cells that cross a vertical one, and the cells that cross either (a cell crossing both
    counts once in the last). Every word of the cell is on one side or the other, and the rule runs over the cell's words."""
    n_h = n_v = n_any = 0
    for c in cells:
        ws = [words_by_id[t] for t in c.get("tokens", []) if t in words_by_id]
        if len(ws) < 2:
            continue
        x0, x1 = min(w["x0"] for w in ws), max(w["x1"] for w in ws)
        t0, b0 = min(w["top"] for w in ws), max(w["bottom"] for w in ws)
        crossed_h = any(_crosses_h(ws, y, a, d, x0, x1, t0, b0) for y, a, d in h_rules)
        crossed_v = any(_crosses_v(ws, x, a, d, x0, x1, t0, b0) for x, a, d in v_rules)
        n_h += crossed_h
        n_v += crossed_v
        n_any += crossed_h or crossed_v
    return n_h, n_v, n_any


def _crosses_h(ws: list[dict[str, Any]], y: float, a: float, d: float, x0: float, x1: float, t0: float, b0: float) -> bool:
    if not (t0 + 1 < y < b0 - 1 and a <= x0 + 2 and d >= x1 - 2):
        return False
    # each word on one side only: a short word touching the rule must not stand in for a word the rule cuts
    up = [w for w in ws if w["bottom"] <= y + 1.0]
    dn = [w for w in ws if w["bottom"] > y + 1.0 and w["top"] >= y - 1.0]
    return bool(up and dn and len(up) + len(dn) == len(ws))


def _crosses_v(ws: list[dict[str, Any]], x: float, a: float, d: float, x0: float, x1: float, t0: float, b0: float) -> bool:
    if not (x0 + 1 < x < x1 - 1 and a <= t0 + 2 and d >= b0 - 2):
        return False
    le = [w for w in ws if w["x1"] <= x + 1.0]
    ri = [w for w in ws if w["x1"] > x + 1.0 and w["x0"] >= x - 1.0]
    return bool(le and ri and len(le) + len(ri) == len(ws))


def h_crossing_ys(ws: list[dict[str, Any]], h_rules: list[Stroke]) -> list[float]:
    """The horizontal rules (their y) that run over a cell's words with every word on one side or the other: the rules a
    cell would be cut along. The same test as the horizontal count of ``crossings``."""
    if len(ws) < 2:
        return []
    x0, x1 = min(w["x0"] for w in ws), max(w["x1"] for w in ws)
    t0, b0 = min(w["top"] for w in ws), max(w["bottom"] for w in ws)
    return [y for y, a, d in h_rules if _crosses_h(ws, y, a, d, x0, x1, t0, b0)]
