"""Candidate grids for a table region and the score that ranks them.

A candidate is rows (y bands) by cells (x intervals per row) with the text each cell holds. ``features``
measures what is wrong with one (text crossing a cut, a cell holding two columns, a row holding two records,
empty cells, disagreement with the drawn rules); ``quality`` turns the features into one number, the score Q:
the share of the region's characters the grid keeps, times ``exp(-sum(weight * feature))``. Zero for a list, a
contents page, tagged prose, a lossy grid and anything under 2 x 2.

The weights and thresholds are fixed constants; nothing here reads the environment.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.parsers.pdf import opencv_layout_analyzer as ola
from app.modules.parsers.pdf.table_reconcile import (
    SNAP,
    Rule,
    column_rule_at,
    is_list,
    is_margin_prose,
    is_toc,
    row_rules,
    segments,
)
from app.modules.parsers.pdf.table_rows import band_lines

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pdfplumber.table import Table

Word = dict[str, Any]
Line = dict[str, Any]

WEIGHTS = {"xcross": 3.0, "usplit": 2.0, "rmerge": 2.0, "empty": 1.5, "unstable": 1.0, "row_rule_dev": 0.75, "col_rule_dev": 0.75}
COVERAGE_MIN = 0.9  # a grid keeps at least this share of the region's characters
Q_MIN = 0.1  # below this a text-tier candidate is no table
XCROSS_MAX = 0.25  # tier 1's floor
GAP_UNSPLIT = 1.5  # line heights: a gap this wide inside a cell is two cells
GAP_CROSS = 0.6  # line heights: a gap narrower than this across a cut is text crossing it
LEADING_WRAP = 1.8  # line heights: a wrapped label continues closer than this, a sub-label row sits lower


@dataclass
class Cand:
    """``rows``: (top, bottom) per row. ``cells``: per row the (x0, x1, text) of the cells that exist in it (a
    cell covered by a span is absent). ``ncols``: base column count. ``grid``: dense, None for a covered position."""

    name: str
    tier: int
    rows: list[tuple[float, float]]
    cells: list[list[tuple[float, float, str]]]
    ncols: int
    grid: list[list[str | None]]
    bbox: tuple[float, float, float, float]
    f: dict[str, Any] | None = None
    q: float | None = None
    footnotes: list[str] = field(default_factory=list)


def cx(w: Word) -> float:
    return (float(w["x0"]) + float(w["x1"])) / 2


def cy(w: Word) -> float:
    return (float(w["top"]) + float(w["bottom"])) / 2


def col_of(x: float, cuts: Sequence[float]) -> int:
    return sum(1 for c in cuts if x > c)


def col_bounds(cuts: Sequence[float], lo: float, hi: float) -> list[tuple[float, float]]:
    e = [lo, *cuts, hi]
    return [(e[k], e[k + 1]) for k in range(len(e) - 1)]


def filled_set(line: Line, cuts: Sequence[float]) -> set[int]:
    """Columns a line fills, one per segment (a segment sits in the column of its first word)."""
    return {col_of(s0 + 0.5, cuts) for s0, _, _ in segments(line)}


def candidate_from_table(name: str, tier: int, t: Table) -> Cand:
    """A candidate from a pdfplumber table; the text is whole words by centre, as the legacy path reads it."""
    rows = list(t.rows)
    tops = [min(c[1] for c in r.cells if c is not None) for r in rows]
    bands = [
        (tops[i], tops[i + 1] if i + 1 < len(rows) else max(c[3] for c in rows[i].cells if c is not None))
        for i in range(len(rows))
    ]
    xs = sorted({round(c[0], 1) for r in rows for c in r.cells if c is not None} | {round(c[2], 1) for r in rows for c in r.cells if c is not None})
    grid = ola._table_grid_by_words(t)
    cells = [[(c[0], c[2], str(v or "")) for c, v in zip(r.cells, g) if c is not None] for r, g in zip(rows, grid)]
    return Cand(name, tier, bands, cells, max(1, len(xs) - 1), grid, tuple(float(v) for v in t.bbox))  # type: ignore[arg-type]


def header_segments(line: Line, cuts: Sequence[float], body_lefts: dict[int, float]) -> list[tuple[float, list[Word]]]:
    """Header segments: a segment is also cut where a cut lies in the gap between two of its words and the word
    after the gap starts at its column's body text edge (narrow labels over monospace columns)."""
    out: list[tuple[float, list[Word]]] = []
    for _, _, ws in segments(line):
        cur = [ws[0]]
        for a, b in zip(ws, ws[1:]):
            cut = any(float(a["x1"]) < c < float(b["x0"]) for c in cuts)
            k = col_of(cx(b), cuts)
            if cut and body_lefts.get(k) is not None and abs(float(b["x0"]) - body_lefts[k]) <= 1.5:
                out.append((float(cur[0]["x0"]), cur))
                cur = []
            cur.append(b)
        out.append((float(cur[0]["x0"]), cur))
    return out


def candidate_from_cuts(
    name: str, tier: int, row_groups: list[list[Line]], cuts: Sequence[float],
    bbox: tuple[float, float, float, float], header_rows: int = 0,
) -> Cand:
    """A candidate from rows of lines and global x cuts. Header rows take whole segments (a label spanning
    columns stays in the column it starts in); other rows take words by centre."""
    rows: list[tuple[float, float]] = []
    cells: list[list[tuple[float, float, str]]] = []
    grid: list[list[str | None]] = []
    edges = [bbox[0], *cuts, bbox[2]]
    body_lefts: dict[int, float] = {}
    for lines in row_groups[header_rows:]:
        for ln in lines:
            for w in ln["w"]:
                k = sum(1 for c in cuts if cx(w) > c)
                body_lefts[k] = min(body_lefts.get(k, 1e9), float(w["x0"]))
    for ri, lines in enumerate(row_groups):
        top = min(ln["top"] for ln in lines)
        bot = max(ln["bottom"] for ln in lines)
        texts: list[list[str]] = [[] for _ in range(len(cuts) + 1)]
        for ln in lines:
            if ri < header_rows:
                for s0, ws in header_segments(ln, cuts, body_lefts):
                    texts[col_of(s0 + 0.5, cuts)].extend(str(w["text"]) for w in ws)
            else:
                for w in ln["w"]:
                    texts[sum(1 for c in cuts if cx(w) > c)].append(str(w["text"]))
        rows.append((top, bot))
        cells.append([(edges[k], edges[k + 1], " ".join(texts[k])) for k in range(len(cuts) + 1)])
        grid.append([" ".join(t) for t in texts])
    return Cand(name, tier, rows, cells, len(cuts) + 1, grid, bbox)


# --- features ---------------------------------------------------------------------------------


def coverage(c: Cand, words: list[Word]) -> float:
    return ola._grid_char_coverage(c.grid, words)


def features(
    c: Cand, words: list[Word], hrules: Sequence[Rule], vrules: Sequence[Rule], box: tuple[float, float, float, float],
) -> dict[str, Any]:
    """What is wrong with ``c`` (module docstring). ``words`` are the words of the region ``box``."""
    f: dict[str, Any] = {}
    nr, nc = len(c.rows), c.ncols
    f["rows"], f["cols"] = nr, nc
    f["cov"] = coverage(c, words)
    lines = band_lines(words)
    f["list"] = is_list(lines)
    f["toc"] = is_toc(lines)
    f["margin_prose"] = is_margin_prose(lines)
    filled = [sum(1 for (_, _, t) in row if t.strip()) for row in c.cells]
    present = [len(row) for row in c.cells]
    covered = [nc - p for p in present]
    modal = statistics.mode([a + b for a, b in zip(filled[1:], covered[1:])]) if nr > 1 else 0
    header = 1 + (1 if nr > 2 and filled[0] < modal else 0)
    row_lines = [[ln for ln in lines if top - 1 <= ln["cy"] <= bot + 1] for top, bot in c.rows]
    pairs = cross = 0
    gutters: list[float] = []
    for ri in range(header, nr):
        cuts = [x1 for (_, x1, _) in c.cells[ri][:-1]]
        for ln in row_lines[ri]:
            ws = ln["w"]
            for x in cuts:
                left = [w for w in ws if float(w["x1"]) <= x + 0.5]
                right = [w for w in ws if float(w["x0"]) >= x - 0.5]
                if any(float(w["x0"]) < x - 0.5 and float(w["x1"]) > x + 0.5 for w in ws):
                    pairs += 1
                    cross += 1
                    continue
                if not left or not right:
                    continue
                pairs += 1
                gap = float(right[0]["x0"]) - float(left[-1]["x1"])
                gutters.append(gap)
                if gap < GAP_CROSS * ln["h"] and not column_rule_at(x, ln["top"], ln["bottom"], vrules):
                    cross += 1
    f["xcross"] = cross / pairs if pairs else 0.0
    hmed = statistics.median([ln["h"] for ln in lines]) if lines else 8.0
    gap_split = max(GAP_UNSPLIT * hmed, 0.75 * statistics.median(gutters)) if gutters else GAP_UNSPLIT * hmed
    gaps_by_row: list[list[tuple[float, float]]] = []
    for ri in range(header, nr):
        gs: list[tuple[float, float]] = []
        for (x0, x1, _) in c.cells[ri]:
            for ln in row_lines[ri]:
                ws = [w for w in ln["w"] if x0 - 0.5 <= cx(w) <= x1 + 0.5]
                gs.extend((float(a["x1"]), float(b["x0"])) for a, b in zip(ws, ws[1:]) if float(b["x0"]) - float(a["x1"]) >= gap_split)
        gaps_by_row.append(gs)
    hdr_split = 0
    for ri in range(min(header, nr)):
        for (x0, x1, _) in c.cells[ri]:
            for ln in row_lines[ri]:
                ws = [w for w in ln["w"] if x0 - 0.5 <= cx(w) <= x1 + 0.5]
                if any(float(b["x0"]) - float(a["x1"]) >= gap_split for a, b in zip(ws, ws[1:])):
                    hdr_split = 1
    us = hdr_split
    for i, gs in enumerate(gaps_by_row):
        need = min(2, len(gaps_by_row) - 1)
        if need >= 1 and any(
            sum(1 for j, hs in enumerate(gaps_by_row) if j != i and any(min(b, d) > max(a, e) for e, d in hs)) >= need
            for a, b in gs
        ):
            us += 1
    f["usplit"] = min(1.0, us / max(1, nr - header))
    hy = [p for p, x0_, x1_ in hrules if min(x1_, box[2]) - max(x0_, box[0]) >= 0.6 * (box[2] - box[0])]
    rm = 0
    for ri in range(nr):
        top, bot = c.rows[ri]
        if any(abs(p - top) <= SNAP for p in hy) and any(abs(p - bot) <= SNAP for p in hy):
            continue
        cuts = [x1 for (_, x1, _) in c.cells[ri][:-1]]
        ls = row_lines[ri]
        if any(header_breaks(a, b, cuts) for a, b in zip(ls, ls[1:])):
            rm += 1
    f["rmerge"] = rm / max(1, nr)
    sparse: set[int] = set()
    if nr > header + 1 and all(len(r) == nc for r in c.cells):
        for k in range(nc):
            named = any(c.cells[ri][k][2].strip() for ri in range(header))
            fill_k = sum(1 for ri in range(header, nr) if c.cells[ri][k][2].strip())
            if named and fill_k < 0.5 * (nr - header):
                sparse.add(k)
    tot = fl = 0
    for ri in range(header, nr):
        for k, (_, _, t) in enumerate(c.cells[ri]):
            if k in sparse and len(c.cells[ri]) == nc:
                continue
            tot += 1
            fl += bool(t.strip())
    f["empty"] = 1 - fl / max(1, tot)
    f["sparse_cols"] = len(sparse)
    occ = [
        sum(1 for k, (_, _, t) in enumerate(c.cells[ri]) if t.strip() and not (k in sparse and len(c.cells[ri]) == nc)) + covered[ri]
        for ri in range(header, nr)
    ]
    f["unstable"] = (sum(1 for k in occ if k != statistics.mode(occ)) / len(occ)) if occ else 0.0
    ys = row_rules(box, hrules, words)
    f["row_rule_dev"] = abs(math.log2(nr / (len(ys) - 1))) if len(ys) >= 4 and nr else 0.0
    devs: list[float] = []
    for (top, bot), row in zip(c.rows, c.cells):
        if not row:
            continue
        rx0, rx1 = min(x0 for x0, _, _ in row), max(x1 for _, x1, _ in row)
        vx = sorted({round(p) for p, a, b in vrules if rx0 - SNAP <= p <= rx1 + SNAP and min(b, bot) - max(a, top) >= 0.6 * (bot - top)})
        vd: list[float] = []
        for x in vx:
            if not vd or x - vd[-1] > SNAP:
                vd.append(x)
        if len(vd) >= 3 and abs(vd[0] - rx0) <= SNAP + 2 and abs(vd[-1] - rx1) <= SNAP + 2:
            devs.append(abs(math.log2(len(row) / (len(vd) - 1))))
    f["col_rule_dev"] = statistics.mean(devs) if devs else 0.0
    return f


def quality(f: dict[str, Any]) -> float:
    if f.get("toc") or f.get("margin_prose") or f["list"] or f["cov"] < COVERAGE_MIN or f["rows"] < 2 or f["cols"] < 2:
        return 0.0
    return f["cov"] * math.exp(-sum(w * f[k] for k, w in WEIGHTS.items() if k in f))


# --- header rows ------------------------------------------------------------------------------


def group_label(prev: Line, ln: Line, cuts: Sequence[float]) -> bool:
    """``prev`` is a label over several columns: a segment crossing a cut that is not left-aligned with any segment
    of the line below (a wrapped cell is left-aligned with its continuation)."""
    starts = [s0 for s0, _, _ in segments(ln)]
    return len(filled_set(ln, cuts)) >= 2 and any(
        any(s0 < c - 1 and s1 > c + 1 for c in cuts) and not any(abs(s0 - t) <= 2.0 for t in starts)
        for s0, s1, _ in segments(prev)
    )


def seg_by_col(line: Line, cuts: Sequence[float]) -> dict[int, tuple[float, float, float, float]]:
    """Per column: start, end, width of the first word and line height of the line's text in it."""
    by: dict[int, list[tuple[float, float, list[Word]]]] = {}
    for s0, s1, ws in segments(line):
        by.setdefault(col_of(s0 + 0.5, cuts), []).append((s0, s1, ws))
    res: dict[int, tuple[float, float, float, float]] = {}
    for k, v in by.items():
        v.sort(key=lambda t: t[0])
        fw = v[0][2][0]
        res[k] = (min(a for a, _, _ in v), max(b for _, b, _ in v), float(fw["x1"]) - float(fw["x0"]), line["h"])
    return res


def wraps_into(up: tuple[float, float, float, float], lo: tuple[float, float, float, float], k: int, bounds: Sequence[tuple[float, float]]) -> bool:
    """The text above wrapped into the line below in column ``k``: the lower line's first word would not have fitted
    after it in the column, or the text above already ran past the column's right bound."""
    right = bounds[k][1]
    return up[1] > right + 1 or up[1] + 0.3 * up[3] + lo[2] > right - 1


def header_breaks(prev: Line, ln: Line, cuts: Sequence[float], bounds: Sequence[tuple[float, float]] | None = None) -> bool:
    """A new header row starts at ``ln`` unless it continues ``prev``'s wrapped text: filled sets incomparable, a group
    label in ``prev``, a sub-label row sitting lower than a wrap, or a short label above text in ``ln``."""
    a, b = filled_set(prev, cuts), filled_set(ln, cuts)
    if not (a <= b or b <= a) or group_label(prev, ln, cuts):
        return True
    if ln["top"] - prev["top"] > LEADING_WRAP * prev["h"]:
        return True
    if bounds is None:
        return False
    up, lo = seg_by_col(prev, cuts), seg_by_col(ln, cuts)
    return any(k in up and not wraps_into(up[k], lo[k], k, bounds) for k in lo)


def split_header(lines: list[Line], cuts: Sequence[float], bounds: Sequence[tuple[float, float]] | None = None) -> list[list[Line]]:
    rows = [[lines[0]]]
    for prev, ln in zip(lines, lines[1:]):
        if header_breaks(prev, ln, cuts, bounds):
            rows.append([ln])
        else:
            rows[-1].append(ln)
    return rows


# --- do no harm -------------------------------------------------------------------------------

DNH_MARGIN = 0.15  # the new grid must beat a defective legacy grid by this much Q
DEFECT_WORD_SPLITS = 2  # lines whose words a legacy cut runs through
DEFECT_XCROSS = 0.25
DEFECT_FRAGMENT_ROWS = 0.25  # share of body rows whose key cell is empty beside filled cells
DEFECT_UNDER_SPLIT = 2  # body cells holding a gap that lines up with a header channel the grid does not cut
UNDER_SPLIT_GAP = 1.5  # line heights
WIDE_HEADER_CHANNEL = 2.0  # line heights: one stray under-split row counts when the header channel is this wide


def defects(c: Cand, words: list[Word], hrules: Sequence[Rule], vrules: Sequence[Rule], box: tuple[float, float, float, float]) -> dict[str, Any]:
    """Strong defect signals of a grid: words split across cells, sentences crossing cells, fragment rows (continuation
    lines as rows) and a column the header shows but the grid does not cut."""
    f = c.f or features(c, words, hrules, vrules, box)
    lines = band_lines(words)
    split_lines: set[int] = set()  # a line on the edge of two rows is checked in both and counts once
    for (top, bot), row in zip(c.rows, c.cells):
        cuts = [x1 for (_, x1, _) in row[:-1]]
        for i, ln in enumerate(lines):
            if top - 1 <= ln["cy"] <= bot + 1 and any(float(w["x0"]) < x - 0.5 and float(w["x1"]) > x + 0.5 for w in ln["w"] for x in cuts):
                split_lines.add(i)
    split = len(split_lines)
    frag = sum(1 for row in c.cells[1:] if row and not row[0][2].strip() and any(t.strip() for _, _, t in row[1:]))
    under = 0
    hch: list[tuple[float, float]] = []
    hl = 8.0
    if len(c.rows) >= 2:
        htop, hbot = c.rows[0]
        for ln in lines:
            if htop - 1 <= ln["cy"] <= hbot + 1:
                sg = segments(ln)
                hch += [(sg[i][1], sg[i + 1][0]) for i in range(len(sg) - 1)]
        allcuts = sorted({x1 for row in c.cells for (_, x1, _) in row[:-1]})
        hch = [(a, b) for a, b in hch if not any(a - 1 <= x <= b + 1 for x in allcuts)]
        hl = statistics.median([ln["h"] for ln in lines]) if lines else 8.0
        for (top, bot), row in list(zip(c.rows, c.cells))[1:]:
            hit = False
            for (x0, x1, _) in row:
                for ln in lines:
                    if not top - 1 <= ln["cy"] <= bot + 1:
                        continue
                    ws = [w for w in ln["w"] if x0 - 0.5 <= cx(w) <= x1 + 0.5]
                    for a_, b_ in zip(ws, ws[1:]):
                        ga, gb = float(a_["x1"]), float(b_["x0"])
                        if gb - ga >= UNDER_SPLIT_GAP * ln["h"] and any(min(gb, hb) > max(ga, ha) for ha, hb in hch):
                            hit = True
            under += hit
    wide = under >= 1 and len(c.rows) >= 2 and any(b_ - a_ >= WIDE_HEADER_CHANNEL * hl for a_, b_ in hch)
    d: dict[str, Any] = {
        "word_split": split, "xcross": f["xcross"], "fragment_rows": frag / max(1, len(c.cells) - 1), "under_split": under,
    }
    d["strong"] = (
        split >= DEFECT_WORD_SPLITS or f["xcross"] >= DEFECT_XCROSS or d["fragment_rows"] >= DEFECT_FRAGMENT_ROWS
        or under >= DEFECT_UNDER_SPLIT or wide
    )
    return d


def replaces_legacy(kept_q: float, new_q: float, *, strong_defect: bool) -> bool:
    """The legacy grid stays unless it shows a strong defect and the new grid beats it by ``DNH_MARGIN``."""
    return strong_defect and new_q >= kept_q + DNH_MARGIN
