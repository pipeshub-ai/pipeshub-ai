"""Tier 2 of the order of evidence: a table whose rows are drawn but whose columns are not (or not all).

Rows are the bands between the row rules the page draws. Columns are the whitespace channels shared by the lines
of the region (a rule drawn inside a channel replaces the cut at it: a partial lattice's own edge); a cut that goes
through a word moves to the nearest free channel or the candidate is not built. A header band becomes header rows
by the header rules of ``table_quality``; a body band holding several records is cut by ``split_band_records``, after
a rule that closes a cell of one column has cut it first.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from app.modules.parsers.pdf import opencv_layout_analyzer as ola
from app.modules.parsers.pdf.table_quality import (
    Cand,
    candidate_from_cuts,
    col_bounds,
    cx,
    cy,
    split_header,
)
from app.modules.parsers.pdf.table_reconcile import SNAP, Rule, segments
from app.modules.parsers.pdf.table_rows import split_band_records

if TYPE_CHECKING:
    from collections.abc import Sequence

Word = dict[str, Any]
Line = dict[str, Any]
Box = tuple[float, float, float, float]

CUT_GAP_NARROW = 0.8  # line heights: a gap narrower than this across a cut is the cut running through the text
HINT_SNAP = 1.0  # pt: a drawn rule within a channel (give or take this) replaces the channel's cut
CUT_MERGE = 2.0  # pt: cuts closer than this are one cut
CELL_RULE_EDGE = 3.0  # pt: a rule closes a cell when it reaches the text's edges within this (SNAP)
ROW_PROJ_MIN_LINES = 3  # lines needed before one stray crossing line is tolerated in a column channel


def proj_cuts(lines: list[Line], *, channels: bool = False, strict: bool = False) -> list[Any]:
    """Whitespace channels shared by the lines: an x-run at least ``_PROJ_MIN_GAP`` wide that at most a small share of
    the lines put ink on (``strict``: none). With ``channels`` each cut is ``(centre, start, end)``."""
    ws = [w for ln in lines for w in ln["w"]]
    if not ws:
        return []
    lo = int(min(float(w["x0"]) for w in ws))
    hi = int(max(float(w["x1"]) for w in ws))
    ink = np.zeros(hi - lo + 2, dtype=np.int32)
    for ln in lines:
        hit = np.zeros_like(ink, dtype=bool)
        for w in ln["w"]:
            hit[int(float(w["x0"])) - lo: int(float(w["x1"])) - lo + 1] = True
        ink += hit
    free = ink == 0 if strict else ink <= max(1 if len(lines) >= ROW_PROJ_MIN_LINES else 0, ola._PROJ_MAX_CROSSING * len(lines))
    cuts: list[Any] = []
    start: int | None = None
    for i, f in enumerate(free):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= ola._PROJ_MIN_GAP and start > 0:
                cuts.append((lo + (start + i) / 2, lo + start, lo + i) if channels else lo + (start + i) / 2)
            start = None
    return cuts


def snap_hints(chans: list[tuple[float, float, float]], hints: Sequence[float]) -> list[float]:
    """A drawn rule (a partial lattice's internal edge) replaces the cut whose channel contains it; a rule in no
    channel is added (it separates columns the text does not)."""
    hs = sorted({round(h, 1) for h in hints})
    out = sorted([c for c, a, b in chans if not any(a - HINT_SNAP <= h <= b + HINT_SNAP for h in hs)] + hs)
    return [c for i, c in enumerate(out) if i == 0 or c - out[i - 1] > CUT_MERGE]


def repair_cuts(cuts: Sequence[float], body: list[Line], lo: float, hi: float) -> list[float] | None:
    """A cut through a word, or through a gap narrower than ``CUT_GAP_NARROW`` line heights, in any body line moves
    to the nearest whitespace channel free in every body line between its neighbours; None when there is none."""
    if not body:
        return list(cuts)
    chans = proj_cuts(body, channels=True, strict=True)
    out: list[float] = []
    for i, c in enumerate(cuts):
        bad = False
        for ln in body:
            ws = ln["w"]
            if any(float(w["x0"]) < c - 0.5 and float(w["x1"]) > c + 0.5 for w in ws):
                bad = True
                break
            left = [w for w in ws if float(w["x1"]) <= c + 0.5]
            right = [w for w in ws if float(w["x0"]) >= c - 0.5]
            if left and right and float(right[0]["x0"]) - float(left[-1]["x1"]) < CUT_GAP_NARROW * ln["h"]:
                bad = True
                break
        if not bad:
            out.append(c)
            continue
        a = out[-1] if out else lo
        b = cuts[i + 1] if i + 1 < len(cuts) else hi
        opts = [m for m, _, _ in chans if a < m < b]
        if not opts:
            return None
        out.append(min(opts, key=lambda m: abs(m - c)))
    return out


def cell_rule_split(band: list[Line], a: float, b: float, hrules: Sequence[Rule], bounds: Sequence[tuple[float, float]]) -> list[list[Line]]:
    """A rule inside a band that spans one column's text from its left to its right end, with text of that column above
    and below it, closes a cell of that column (separately ruled key cells beside one shared description): the band
    is cut there before the record rule runs."""
    cuts_y: list[float] = []
    for p, s0, s1 in hrules:
        if not (a + SNAP < p < b - SNAP):
            continue
        for x0, x1 in bounds:
            cw = [w for ln in band for w in ln["w"] if x0 <= cx(w) <= x1]
            if not cw:
                continue
            tx0 = min(float(w["x0"]) for w in cw)
            tx1 = max(float(w["x1"]) for w in cw)
            if s0 <= tx0 + CELL_RULE_EDGE and s1 >= tx1 - CELL_RULE_EDGE and any(cy(w) < p for w in cw) and any(cy(w) > p for w in cw):
                cuts_y.append(p)
                break
    if not cuts_y:
        return [band]
    out: list[list[Line]] = []
    cur: list[Line] = []
    ys = sorted(cuts_y)
    for ln in band:
        if cur and any(cur[-1]["cy"] < y < ln["cy"] for y in ys):
            out.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        out.append(cur)
    return out


def ruled_rows(
    extent: Box, ys: Sequence[float], lines: list[Line], hrules: Sequence[Rule], hints: Sequence[float] = (),
) -> tuple[Cand | None, str]:
    """Tier 2: the candidate for ``extent`` whose rows are the bands between the row rules ``ys`` (three or more), or
    None and why not."""
    bands = [(-1e9, ys[0]), *zip(ys, ys[1:]), (ys[-1], 1e9)]
    body_l = [ln for ln in lines if ln["top"] >= ys[1] - 1]
    col_lines = lines
    if len(body_l) >= 2:
        bch = proj_cuts(body_l, channels=True, strict=True)
        group = [ln for ln in lines if ln["top"] < ys[1] - 1 and any(s0 < a and s1 > b for s0, s1, _ in segments(ln) for _, a, b in bch)]
        col_lines = [ln for ln in lines if ln not in group] or lines
    cuts = snap_hints(proj_cuts(col_lines, channels=True), hints)
    repaired = repair_cuts(cuts, body_l, extent[0], extent[2]) if cuts else cuts
    if repaired is None:
        return None, "cut through words"
    cuts = repaired
    if not cuts:
        return None, "no columns"
    bounds = col_bounds(cuts, extent[0], extent[2])
    groups: list[list[Line]] = []
    nhead = 0
    kx1 = cuts[0]
    # no rule above the header means the first rule is under it: band 1 is body then
    head_band = 0 if any(ln["cy"] < ys[0] for ln in lines) else 1
    for bi, (a, b) in enumerate(bands):
        bl = [ln for ln in lines if a - 1 <= ln["cy"] <= b + 1]
        if not bl:
            continue
        if bi <= head_band and all(ln["cy"] < ys[1] for ln in bl):
            hg = split_header(bl, cuts, bounds)
            groups += hg
            nhead += len(hg)
        else:
            for sb in cell_rule_split(bl, a, b, hrules, bounds):
                groups += split_band_records(sb, bounds, ruled_cell=False) if kx1 else [sb]
    if len(groups) < 2:
        return None, "under 2 rows"
    return candidate_from_cuts("ruled_rows", 2, groups, cuts, extent, header_rows=nhead), ""
