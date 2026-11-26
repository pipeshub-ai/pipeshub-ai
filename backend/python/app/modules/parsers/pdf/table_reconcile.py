"""Order of evidence for the tables of one page: what a layout-model table box holds.

The legacy path re-extracts the cells of a model box with whichever text strategy keeps
the most columns. This module reads the page's drawn rules first.

``PDF_TABLE_EXTRACTION`` chooses the path:

* ``legacy`` (default): this module is not used.
* ``shadow``: the decisions are computed and logged; the regions are the legacy regions.
* ``evidence``: the decisions replace the legacy regions of the boxes they cover.

What a decision can be, for a model box ``M`` (the first that applies):

1. **Group box.** ``M`` holds two or more ruled tables: one table per ruled table.
2. **Ruled table (tier 1).** A ruled table the page draws with ``M``'s extent keeps its drawn
   rows and columns. A ruled row whose key cell holds several records is split by
   ``table_rows.split_band_records`` (the one row rule). Trailing NOTE rows are not part of
   the grid. The region is the ruled table's own extent, so a caption above it is text.
3. **Not a table.** No ruled table, and the words are a list, a table of contents or
   identifier-tagged prose.
4. Otherwise no decision (``None``): the legacy region stays.

A ruled table is used only when it passes the floor: two rows, two columns, its grid keeps
0.9 of the characters inside it, no more than a quarter of its row/column pairs have text
crossing a cut that is not drawn, and it is not a list. A ruled table that covers only part
of a table-like block (words beside it sit on its own text lines) is a sub-area: no decision.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.parsers.pdf import opencv_layout_analyzer as ola
from app.modules.parsers.pdf.table_rows import band_lines

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pdfplumber.page import Page
    from pdfplumber.table import Table

    from app.modules.parsers.pdf.table_quality import Cand

logger = logging.getLogger(__name__)

ENV_MODE = "PDF_TABLE_EXTRACTION"
MODES = ("legacy", "shadow", "evidence")
DEFAULT_MODE = "legacy"

SNAP = 3.0  # rules closer than this are one rule (pdfplumber's snap distance), pt
COVERAGE_MIN = 0.9  # share of the characters in a ruled table its grid must keep
XCROSS_MAX = 0.25  # share of row/column pairs whose text may cross an undrawn cut
RULE_MIN_LEN = 5.0  # shorter rules are not row or column rules, pt
FULL_ROW_RULE = 0.9  # a rule that ends a record of a shaded table runs the table's width (a line-level rule in one column does not)
FRAGMENT_MIN_ROWS = 3  # row edges that are no row rule before a table counts as shaded line by line
FRAGMENT_ONE_COLUMN = 0.4  # share of those rows that hold text in one column only: lines of one wrapped cell
FRAGMENT_SHARE = 0.4  # ... and their share of the finder's rows
COLUMN_RULE_COVER = 0.6  # a column rule runs this share of the table's height
ROW_RULE_COVER = 0.6  # a row rule's segments cover this share of the extent's width
SEGMENT_GAP = 1.5  # a gap wider than this many line heights splits a line into segments
LIST_SHARE = 0.6  # share of lines that open with a bullet or an enumerator for a list
LIST_MIN_LINES = 2
ENUM_MIN_WORDS = 6  # an enumerated line (1. / a)) needs this many more words to count as a list line
TOC_SHARE = 0.5  # share of lines ending with a dot leader and a page number for a contents page
TOC_MIN_LINES = 3
TAG_MIN_WORDS = 8  # words of text a tagged block needs
TAG_SHARE = 0.6
SUBAREA_ROWS = 0.75  # share of the ruled table's rows with outside text on their own line
SUBAREA_OFF = 0.25  # largest share of outside lines that sit between the rows
SUBAREA_ALIGN = 4.0  # outside segment starts align within this, pt
GROUP_INSIDE = 0.9  # a ruled table is inside a group box when this much of it is
BAND_ABOVE = 1.5  # a column-aligned line above/below the first/last rule joins the extent within this many line heights + 4 pt

BULLET = re.compile(r"^[•●▪◦‣∙·–—\-\*■□⁃]$")
ENUM = re.compile(r"^\(?([0-9]{1,2}|[a-z])[.)]$")
NOTES = re.compile(r"^\s*(NOTES?\b\s*:?|\d+\.\s|\*|[†‡§])", re.I)
DOTS = re.compile(r"\.{4,}|(\. ){3,}")
PAGENO = re.compile(r"^[A-Z]{0,3}-?\d{1,4}$")
TAG = re.compile(r"^[A-Z][A-Z0-9_]{1,11}$")

Rule = tuple[float, float, float]  # a horizontal rule (y, x0, x1) or a vertical rule (x, y0, y1)

_warned: set[str] = set()


def table_extraction_mode() -> str:
    """The configured path. An unknown value is ``legacy`` with one warning per value."""
    raw = os.environ.get(ENV_MODE)
    if raw is None or not raw.strip():
        return DEFAULT_MODE
    mode = raw.strip().lower()
    if mode in MODES:
        return mode
    if raw not in _warned:
        _warned.add(raw)
        logger.warning("%s=%r is not one of %s; using %s", ENV_MODE, raw, "/".join(MODES), DEFAULT_MODE)
    return DEFAULT_MODE


@dataclass
class Decision:
    """What one model box holds. ``tables`` are new regions; ``not_table`` drops the legacy
    table regions the box covers; neither: no decision, the legacy regions stay."""

    box: tuple[float, float, float, float]
    kind: str  # "group", "ruled", "not_table", "none"
    tables: list[ola.LayoutRegion] = field(default_factory=list)
    not_table: str | None = None
    reason: str = ""
    footnotes: list[str] = field(default_factory=list)


# --- geometry -------------------------------------------------------------------------------


def _cx(w: dict[str, Any]) -> float:
    return (float(w["x0"]) + float(w["x1"])) / 2.0


def _in_box(w: dict[str, Any], b: tuple[float, float, float, float]) -> bool:
    return ola._word_center_in_bbox(w, b)


def _line_x0(ln: dict[str, Any]) -> float:
    return float(ln["w"][0]["x0"])


def segments(ln: dict[str, Any]) -> list[tuple[float, float, list[dict[str, Any]]]]:
    """A line cut where the gap between two words is wider than ``SEGMENT_GAP`` line heights."""
    ws = ln["w"]
    segs: list[list[dict[str, Any]]] = [[ws[0]]]
    for a, b in zip(ws, ws[1:]):
        if float(b["x0"]) - float(a["x1"]) > SEGMENT_GAP * ln["h"]:
            segs.append([])
        segs[-1].append(b)
    return [(float(s[0]["x0"]), max(float(w["x1"]) for w in s), s) for s in segs]


def _merge(segs: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for p, a, b in sorted(segs):
        hit = [o for o in out if abs(o[0] - p) <= SNAP and a <= o[2] + SNAP and b >= o[1] - SNAP]
        if not hit:
            out.append([p, a, b])
            continue
        # a segment that bridges two spans joins them all
        keep = hit[0]
        keep[1] = min(a, *(o[1] for o in hit))
        keep[2] = max(b, *(o[2] for o in hit))
        out = [o for o in out if not any(o is h for h in hit[1:])]
    return out


def page_rules(page: Page) -> tuple[list[Rule], list[Rule]]:
    """Horizontal rules ``[y, x0, x1]`` and vertical rules ``[x, y0, y1]`` the page draws, merged."""
    hs: list[list[float]] = []
    vs: list[list[float]] = []
    for e in page.edges:
        x0, x1, t, b = float(e["x0"]), float(e["x1"]), float(e["top"]), float(e["bottom"])
        if e.get("orientation") == "h" and x1 - x0 >= RULE_MIN_LEN:
            hs.append([(t + b) / 2, x0, x1])
        elif e.get("orientation") == "v" and b - t >= RULE_MIN_LEN:
            vs.append([(x0 + x1) / 2, t, b])
    return [(p, a, b) for p, a, b in _merge(hs)], [(p, a, b) for p, a, b in _merge(vs)]


def row_rules(
    box: tuple[float, float, float, float], hrules: Sequence[Rule],
    words: list[dict[str, Any]] | None = None, cover: float = ROW_RULE_COVER,
) -> list[float]:
    """Heights of the row rules in ``box``: at one height the union of rule segments covers
    ``ROW_RULE_COVER`` of the box's width. A gap in the union is allowed only over a column
    that holds text (a word centre lies in the gap); without words, gaps are allowed."""
    x0, y0, x1, y1 = box
    width = max(1.0, x1 - x0)
    by_y: list[list[Any]] = []
    for p, a, b in sorted(hrules):
        if not (y0 - SNAP <= p <= y1 + SNAP) or min(b, x1) <= max(a, x0):
            continue
        if by_y and abs(by_y[-1][0] - p) <= SNAP:
            by_y[-1][1].append((max(a, x0), min(b, x1)))
        else:
            by_y.append([p, [(max(a, x0), min(b, x1))]])
    out: list[float] = []
    for p, segs in by_y:
        segs.sort()
        union: list[list[float]] = []
        for a, b in segs:
            if union and a <= union[-1][1] + SNAP:
                union[-1][1] = max(union[-1][1], b)
            else:
                union.append([a, b])
        if sum(b - a for a, b in union) < cover * width:
            continue
        if words is not None and len(union) > 1:
            gaps = [(union[i][1], union[i + 1][0]) for i in range(len(union) - 1)]
            if not all(any(ga <= _cx(wd) <= gb for wd in words) for ga, gb in gaps):
                continue
        if not out or p - out[-1] > SNAP:
            out.append(p)
    return out


def column_rule_at(x: float, y0: float, y1: float, vrules: Sequence[Rule]) -> bool:
    """A vertical rule at ``x`` covers the height from ``y0`` to ``y1``."""
    return any(abs(p - x) <= SNAP and a <= y0 + 1 and b >= y1 - 1 for p, a, b in vrules)


# --- what a block of words is ---------------------------------------------------------------


def is_list(lines: list[dict[str, Any]]) -> bool:
    """Most lines open with a bullet, or with an enumerator followed by a sentence."""
    if not lines:
        return False
    n = 0
    for ln in lines:
        first = str(ln["w"][0]["text"])
        if BULLET.match(first) or (ENUM.match(first) and len(ln["w"]) - 1 >= ENUM_MIN_WORDS):
            n += 1
    return n >= LIST_SHARE * len(lines) and n >= LIST_MIN_LINES


def is_toc(lines: list[dict[str, Any]]) -> bool:
    """Most lines end with a dot leader and a page number."""
    n = 0
    for ln in lines:
        text = " ".join(str(w["text"]) for w in ln["w"])
        last = str(ln["w"][-1]["text"]).split(".")[-1]
        if DOTS.search(text) and PAGENO.match(last.strip()):
            n += 1
    return len(lines) >= TOC_MIN_LINES and n >= TOC_SHARE * len(lines)


def is_margin_prose(lines: list[dict[str, Any]]) -> bool:
    """Identifier tags (upper case, up to two words, up to 12 characters) in a left column, each
    beside a paragraph of at least ``TAG_MIN_WORDS`` words. A definition list (terms that are words,
    not codes) is a table; so is anything whose first line is a header of words."""
    if len(lines) < 3:
        return False
    left = min(_line_x0(ln) for ln in lines)
    blocks: list[list[dict[str, Any]]] = []
    for ln in lines:
        if abs(_line_x0(ln) - left) <= SNAP and len(segments(ln)) >= 2:
            blocks.append([ln])
        elif blocks:
            blocks[-1].append(ln)
    if len(blocks) < 2:
        return False
    first = segments(lines[0])
    if len(first) >= 2 and not TAG.match("".join(str(w["text"]) for w in first[0][2])) and all(len(s[2]) <= 4 for s in first):
        return False
    ok = 0
    for blk in blocks:
        segs = segments(blk[0])
        tag = segs[0][2]
        joined = "".join(str(w["text"]) for w in tag)
        n_words = sum(len(s[2]) for s in segs[1:]) + sum(len(x["w"]) for x in blk[1:])
        if len(tag) <= 2 and TAG.match(joined) and n_words >= TAG_MIN_WORDS:
            ok += 1
    return ok >= TAG_SHARE * len(blocks)


def extent_by_rules(
    box: tuple[float, float, float, float], words: list[dict[str, Any]], hrules: Sequence[Rule],
) -> tuple[tuple[float, float, float, float], list[float]]:
    """When ``box`` holds three or more row rules, the extent is the rule range plus the contiguous
    column-aligned lines above the first and below the last rule (prose around a ruled table is
    outside it). Returns the extent and the row rules."""
    ys = row_rules(box, hrules, [w for w in words if _in_box(w, box)])
    if len(ys) < 3:
        return box, ys
    xs = [(a, b) for p, a, b in hrules if any(abs(p - y) <= SNAP for y in ys)]
    x0 = min(a for a, _ in xs)
    x1 = max(b for _, b in xs)
    lines = band_lines([w for w in words if _in_box(w, box)])
    top, bot = ys[0], ys[-1]
    for ln in [ln for ln in lines if ln["bottom"] <= top + 1][::-1]:
        if top - ln["bottom"] <= BAND_ABOVE * ln["h"] + 4 and len(segments(ln)) >= 2:
            top = ln["top"] - 1
        else:
            break
    for ln in [ln for ln in lines if ln["top"] >= bot - 1]:
        if ln["top"] - bot <= BAND_ABOVE * ln["h"] + 4 and len(segments(ln)) >= 2:
            bot = ln["bottom"] + 1
        else:
            break
    return (max(box[0], x0 - 1), max(box[1], top - 1), min(box[2], x1 + 1), min(box[3], bot + 1)), ys


# --- ruled tables ---------------------------------------------------------------------------


def _bbox(t: Table) -> tuple[float, float, float, float]:
    return tuple(float(v) for v in t.bbox)  # type: ignore[return-value]


def same_table(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return ola._iou(a, b) >= 0.5 or ola._overlap_ratio(a, b) >= 0.8 or ola._overlap_ratio(b, a) >= 0.8


def _row_boxes(t: Table) -> list[tuple[float, float]]:
    """Top and bottom of each row of a ruled table."""
    rows = list(t.rows)
    tops = [min(c[1] for c in r.cells if c is not None) for r in rows]
    return [
        (tops[i], tops[i + 1] if i + 1 < len(rows) else max(c[3] for c in rows[i].cells if c is not None))
        for i in range(len(rows))
    ]


def undrawn_cut_crossings(
    t: Table, words: list[dict[str, Any]], vrules: Sequence[Rule],
) -> float:
    """Share of (line, cut) pairs in the body rows where the text runs into the cut without a drawn
    rule: a word crosses it, or the gap across it is under 0.6 line heights."""
    lines = band_lines(words)
    pairs = cross = 0
    for (top, bot), row in list(zip(_row_boxes(t), t.rows))[1:]:
        cells = [c for c in row.cells if c is not None]
        cuts = [c[2] for c in cells[:-1]]
        for ln in (ln for ln in lines if top - 1 <= ln["cy"] <= bot + 1):
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
                if gap < 0.6 * ln["h"] and not column_rule_at(x, ln["top"], ln["bottom"], vrules):
                    cross += 1
    return cross / pairs if pairs else 0.0


def is_sub_area(
    t: Table, box: tuple[float, float, float, float], words: list[dict[str, Any]],
) -> bool:
    """The ruled table covers part of a table-like block: the words beside it (inside ``box``, at its
    height) sit on its own text lines on most of its rows, they are not paragraphs with lines between
    the rows, and their segment starts align."""
    lb = _bbox(t)
    inside = [w for w in words if _in_box(w, lb)]
    outside = [
        w for w in words
        if box[0] <= _cx(w) <= box[2] and lb[1] - 1 <= (float(w["top"]) + float(w["bottom"])) / 2 <= lb[3] + 1
        and not _in_box(w, lb)
    ]
    if not outside:
        return False
    row_cys = []
    for top, bot in _row_boxes(t):
        row_cys.append([ln["cy"] for ln in band_lines([w for w in inside if top <= (float(w["top"]) + float(w["bottom"])) / 2 <= bot])])
    on: set[int] = set()
    off = 0
    starts: list[tuple[int, ...]] = []
    olines = band_lines(outside)
    for ln in olines:
        hit = [i for i, cys in enumerate(row_cys) if any(abs(ln["cy"] - y) <= 2.0 for y in cys)]
        if hit:
            on.update(hit)
            starts.append(tuple(round(s0) for s0, _, _ in segments(ln)))
        else:
            off += 1
    n_rows = len(row_cys)
    if len(on) < SUBAREA_ROWS * n_rows or off > SUBAREA_OFF * len(olines):
        return False
    firsts = [st[0] for st in starts if st]
    if not firsts:
        return False
    common = max(sum(1 for x in firsts if abs(x - y) <= SUBAREA_ALIGN) for y in firsts)
    return common >= SUBAREA_ROWS * len(firsts)


def _trailing_rows_cut(
    t: Table, grid: list[list[Any]], row_of: list[int], box: tuple[float, float, float, float],
    others: Sequence[tuple[float, float, float, float]] = (),
) -> tuple[list[list[Any]], float | None, list[str]]:
    """Cut the trailing rows that are not the table: a row that is one full-width cell opening NOTE(S) or a
    footnote mark (the text goes to the caller as a footnote), and a row that starts below the model
    box (a callout or paragraph the ruled finder joined; the model says the table ends above it) and
    that no other model box covers (a table the model boxed in two parts is one ruled table).
    Returns the grid, the top of the first cut row (None: nothing cut) and the footnotes."""
    notes: list[str] = []
    rows = list(t.rows)
    keep = len(rows)
    width = _bbox(t)[2] - _bbox(t)[0]
    while keep > 2:
        cells = [c for c in rows[keep - 1].cells if c is not None]
        mine = [g for g, r in zip(grid, row_of) if r == keep - 1]
        texts = [str(v) for g in mine for v in g if v is not None and str(v).strip()]
        row_top = min(c[1] for c in cells)
        below = row_top >= box[3] - 1 and not any(o[1] - 1 <= row_top + 1 <= o[3] and o is not box for o in others)
        if below:
            pass
        elif len(cells) == 1 and len(texts) == 1 and (cells[0][2] - cells[0][0]) >= 0.9 * width and NOTES.match(texts[0]):
            notes.insert(0, texts[0])
        else:
            break
        keep -= 1
    if keep == len(rows):
        return grid, None, []
    top_cut = min(c[1] for c in rows[keep].cells if c is not None)
    return [g for g, r in zip(grid, row_of) if r < keep], top_cut, notes


def drop_title_line(
    t: Table, grid: list[list[Any]], row_of: list[int], own: list[dict[str, Any]],
) -> tuple[list[list[Any]], list[dict[str, Any]]]:
    """The header row without a title line: when the top row of the ruled table holds a line that is one segment
    running across two or more of its cells, above a line of labels, that line is the table's title (a caption set
    inside the top band), not a label; the row is read again without it."""
    if not row_of or row_of[0] != 0 or (len(row_of) > 1 and row_of[1] == 0):
        return grid, []
    top, bot = _row_boxes(t)[0]
    cells = [c for c in t.rows[0].cells if c is not None]
    words = [w for w in own if top - 1 <= (float(w["top"]) + float(w["bottom"])) / 2 <= bot + 1]
    lines = band_lines(words)
    if len(lines) < 2:
        return grid, []
    first, second = lines[0], lines[1]
    cuts = [c[2] for c in cells[:-1]]
    segs = segments(first)
    if len(segs) != 1 or len(segments(second)) < 2 or not any(segs[0][0] < c - 1 and segs[0][1] > c + 1 for c in cuts):
        return grid, []
    keep = [w for w in words if not any(w is x for x in first["w"])]
    head: list[Any] = []
    for cell in t.rows[0].cells:
        if cell is None:
            head.append(None)
            continue
        mine = [w for w in keep if cell[0] <= _cx(w) < cell[2]]
        head.append("\n".join(" ".join(str(w["text"]) for w in ln["w"]) for ln in band_lines(mine)))
    return [head, *grid[1:]], list(first["w"])


def drawn_rows_and_columns(
    t: Table, own: list[dict[str, Any]], hrules: Sequence[Rule], vrules: Sequence[Rule],
) -> Table | None:
    """The ruled table as its page really draws it, or None when the finder's table is already that.

    A table shaded line by line (a filled box per text line in one column) gives the finder a row edge at
    every line, so one record becomes many rows. The rows are the heights at which a rule crosses the table
    (``row_rules`` at ``FULL_ROW_RULE``); the columns are the vertical rules that run most of its height.
    When at least ``FRAGMENT_MIN_ROWS`` of the finder's row edges, and ``FRAGMENT_SHARE`` of them, are no
    row rule, the table is read again with exactly those rows and columns."""
    lb = _bbox(t)
    ys = row_rules(lb, hrules, own, FULL_ROW_RULE)
    tops = sorted({round(min(c[1] for c in r.cells if c is not None), 1) for r in t.rows})
    stray = [top for top in tops if not any(abs(y - top) <= SNAP for y in ys)]
    if len(ys) < 2 or len(stray) < FRAGMENT_MIN_ROWS or len(stray) < FRAGMENT_SHARE * len(tops):
        return None
    # a row that is a line of one wrapped cell has text in one column only; a row of a table has text in several
    one_column = 0
    for (top, bot), row in zip(_row_boxes(t), t.rows):
        if not any(abs(y - top) <= SNAP for y in ys):
            cols = {j for j, c in enumerate(row.cells) if c is not None and any(c[0] <= _cx(w) < c[2] and top - 1 <= (float(w["top"]) + float(w["bottom"])) / 2 <= bot + 1 for w in own)}
            one_column += len(cols) <= 1
    if one_column < FRAGMENT_ONE_COLUMN * len(stray):
        return None
    height = lb[3] - lb[1]
    xs = sorted({round(x, 1) for x, a, b in vrules if lb[0] - SNAP <= x <= lb[2] + SNAP and min(b, lb[3]) - max(a, lb[1]) >= COLUMN_RULE_COVER * height})
    cols = [x for i, x in enumerate(xs) if i == 0 or x - xs[i - 1] > SNAP]
    if len(cols) < 3:
        return None
    rows = [y for i, y in enumerate(ys) if i == 0 or y - ys[i - 1] > SNAP]
    if abs(rows[0] - lb[1]) > SNAP:
        rows.insert(0, lb[1])
    if abs(rows[-1] - lb[3]) > SNAP:
        rows.append(lb[3])
    settings = {
        "vertical_strategy": "explicit", "horizontal_strategy": "explicit",
        "explicit_vertical_lines": cols, "explicit_horizontal_lines": rows,
    }
    try:
        found = t.page.find_tables(settings)
    except Exception:
        return None
    return max(found, key=lambda f: ola._iou(_bbox(f), lb), default=None)


def ruled_table(
    t: Table, words: list[dict[str, Any]], vrules: Sequence[Rule], box: tuple[float, float, float, float],
    others: Sequence[tuple[float, float, float, float]] = (), hrules: Sequence[Rule] = (),
) -> tuple[ola.LayoutRegion | None, list[str], str]:
    """Tier 1: the ruled table ``t`` as a region, or ``None`` and why not."""
    lb = _bbox(t)
    own = [w for w in words if _in_box(w, lb)]
    redrawn = drawn_rows_and_columns(t, own, hrules, vrules) if hrules else None
    if redrawn is not None:
        region, notes, why = ruled_table(redrawn, words, vrules, box, others)
        if region is not None:
            return region, notes, why
    row_of: list[int] = []
    try:
        grid = ola._table_grid_by_words(t, split_rows=True, row_of=row_of)
    except Exception:  # a table the finder cannot read is no evidence
        return None, [], "grid failed"
    title: list[dict[str, Any]] = []
    if len(row_of) == len(grid):
        grid, title = drop_title_line(t, grid, row_of, own)
        own = [w for w in own if not any(w is x for x in title)]
    clean = ola._trim_empty_rows_cols(grid)
    if not clean or len(clean) < 2 or max(len(r) for r in clean) < 2:
        return None, [], "under 2x2"
    if ola._grid_char_coverage(clean, own) < COVERAGE_MIN:
        return None, [], "lossy"
    if undrawn_cut_crossings(t, own, vrules) > XCROSS_MAX:
        return None, [], "text crosses undrawn cuts"
    if is_list(band_lines(own)):
        return None, [], "list"
    if len(row_of) == len(grid):
        grid, cut, notes = _trailing_rows_cut(t, grid, row_of, box, others)
    else:
        cut, notes = None, []
    clean = ola._trim_empty_rows_cols(grid)
    if not clean or len(clean) < 2 or max(len(r) for r in clean) < 2:
        return None, [], "under 2x2 without trailing rows"
    below_title = max((float(w["bottom"]) for w in title), default=None)
    bbox = (lb[0], lb[1] if below_title is None else below_title + 0.5, lb[2], cut if cut is not None else lb[3])
    from app.modules.parsers.pdf import table_quality

    scored = table_quality.candidate_from_table("lattice", 1, t)
    scored.f = table_quality.features(scored, own, hrules, vrules, scored.bbox)
    return (
        ola.LayoutRegion(
            type=ola.LayoutRegionType.TABLE, bbox=bbox, text=ola._grid_to_text(clean), table_grid=clean,
            from_evidence=True, table_q=table_quality.quality(scored.f),
        ),
        notes,
        "",
    )


def decide_box(
    page: Page, page_w: float, page_h: float,
    box: tuple[float, float, float, float], words: list[dict[str, Any]],
    lattices: list[Table], hrules: Sequence[Rule], vrules: Sequence[Rule],
    others: Sequence[tuple[float, float, float, float]] = (), claimed: set[int] | None = None,
    legacy: Sequence[ola.LayoutRegion] = (),
) -> Decision:
    """What the model box ``box`` holds (module docstring)."""
    claimed = claimed if claimed is not None else set()
    # a ruled table inside another one (a shaded cell the finder boxes) is part of that table, not a second table
    lattices = [t for t in lattices if not any(o is not t and ola._overlap_ratio(_bbox(t), _bbox(o)) >= GROUP_INSIDE and ola._overlap_ratio(_bbox(o), _bbox(t)) < GROUP_INSIDE for o in lattices)]
    inside = [t for t in lattices if ola._overlap_ratio(_bbox(t), box) >= GROUP_INSIDE]
    if len(inside) >= 2:
        parts = [ruled_table(t, words, vrules, _bbox(t), (), hrules) for t in inside]
        if all(r is not None for r, _, _ in parts):
            claimed.update(id(t) for t in inside)
            return Decision(
                box, "group", [r for r, _, _ in parts if r is not None],
                footnotes=[n for _, ns, _ in parts for n in ns],
            )
        return Decision(box, "none", reason="a table of the group is not ruled evidence: " + "; ".join(why for r, _, why in parts if r is None))
    matching = sorted((t for t in lattices if same_table(_bbox(t), box)), key=lambda t: -ola._iou(_bbox(t), box))
    why = ""
    hints: list[float] = []
    if matching and all(id(t) in claimed for t in matching):
        return Decision(box, "none", reason="ruled table used by another box")
    for t in matching:
        if id(t) in claimed:
            continue
        if is_sub_area(t, box, words):
            hints = [c[2] for r in t.rows for c in [c for c in r.cells if c is not None][:-1]]
            box = (min(box[0], _bbox(t)[0]), min(box[1], _bbox(t)[1]), max(box[2], _bbox(t)[2]), max(box[3], _bbox(t)[3]))
            why = "partial lattice"
            break
        region, notes, why = ruled_table(t, words, vrules, box, others, hrules)
        if region is not None:
            claimed.add(id(t))
            return Decision(box, "ruled", [region], footnotes=notes)
    extent, ys = extent_by_rules(box, words, hrules)
    lines = band_lines([w for w in words if _in_box(w, extent)])
    if lines:
        if is_list(lines):
            return Decision(box, "not_table", not_table="list")
        if is_toc(lines):
            return Decision(box, "not_table", not_table="toc")
        if len(ys) < 3 and is_margin_prose(lines):  # ruled rows are table evidence; tagged prose has none
            return Decision(box, "not_table", not_table="margin_prose")
    from app.modules.parsers.pdf import table_quality, table_tiers

    if not lines:
        return Decision(box, "none", reason=why)
    text_words = [w for w in words if _in_box(w, extent)]

    def score(c: table_quality.Cand) -> table_quality.Cand:
        c.f = table_quality.features(c, text_words, hrules, vrules, extent)
        c.q = table_quality.quality(c.f)
        return c

    def region_of(c: table_quality.Cand, kind: str) -> Decision:
        grid = ola._trim_empty_rows_cols(c.grid)
        region = ola.LayoutRegion(type=ola.LayoutRegionType.TABLE, bbox=extent, text=ola._grid_to_text(grid), table_grid=grid, from_evidence=True, table_q=c.q)
        return Decision(box, kind, [region], reason=f"tier {c.tier} {c.name} q={c.q:.2f}")

    ruled: table_quality.Cand | None = None
    if len(ys) >= 3:
        cand, bad = table_tiers.ruled_rows(extent, ys, lines, hrules, hints)
        if cand is None:
            why = f"tier 2: {bad}"
        else:
            ruled = score(cand)
            if (ruled.q or 0.0) >= table_quality.Q_MIN and not legacy_scores_higher(page, legacy, box, ruled, text_words, hrules, vrules, page_w, page_h):
                return region_of(ruled, "ruled_rows")
    if not any(ola._boxes_overlap(r.bbox, box) for r in legacy):
        return Decision(box, "none", reason=why)  # the text tiers replace a table; they never make a new one
    best = ruled
    for c in table_tiers.text_alignment(page, extent, lines, page_w, page_h):
        score(c)
        # ties go to more rows, then more columns; never to list order
        if best is None or (c.q or 0.0) > (best.q or 0.0) + 1e-9 or (
            abs((c.q or 0.0) - (best.q or 0.0)) <= 1e-9 and (len(c.rows), c.ncols) > (len(best.rows), best.ncols)
        ):
            best = c
    if best is None or (best.q or 0.0) < table_quality.Q_MIN:
        return Decision(box, "none", reason=why)
    old = max((r for r in legacy if ola._boxes_overlap(r.bbox, box)), key=lambda r: ola._iou(r.bbox, box))
    kept = legacy_candidate(page, old, page_w, page_h)
    if kept is None:
        return Decision(box, "none", reason="legacy grid cannot be re-read: kept")
    legacy_words = [w for w in words if _in_box(w, kept.bbox)] or text_words
    kept.f = table_quality.features(kept, legacy_words, hrules, vrules, kept.bbox)
    kept.q = table_quality.quality(kept.f)
    harm = table_quality.defects(kept, text_words, hrules, vrules, extent)
    if not table_quality.replaces_legacy(kept.q or 0.0, best.q or 0.0, strong_defect=bool(harm["strong"])):
        return Decision(box, "none", reason=f"legacy kept: q={kept.q:.2f} new {best.q:.2f} defects {harm['strong']}")
    return region_of(best, "text_tier")


def legacy_scores_higher(
    page: Page, legacy: Sequence[ola.LayoutRegion], box: tuple[float, float, float, float], new: Cand,
    words: list[dict[str, Any]], hrules: Sequence[Rule], vrules: Sequence[Rule], page_w: float, page_h: float,
) -> bool:
    """True when the legacy table of ``box`` can be re-read and its score Q is higher than the new candidate's: rows that
    are one text line each look like wrapped lines to the record rule, and the legacy grid then keeps them apart."""
    from app.modules.parsers.pdf import table_quality

    olds = [r for r in legacy if ola._boxes_overlap(r.bbox, box)]
    if not olds:
        return False
    old = max(olds, key=lambda r: ola._iou(r.bbox, box))
    kept = legacy_candidate(page, old, page_w, page_h)
    if kept is None:
        return False
    legacy_words = [w for w in words if _in_box(w, kept.bbox)] or words
    kept.f = table_quality.features(kept, legacy_words, hrules, vrules, kept.bbox)
    kept.q = table_quality.quality(kept.f)
    if (kept.q or 0.0) > (new.q or 0.0) + 1e-9:
        return True
    # a new grid that shows a strong defect (continuation lines as rows, cut through words) does not replace a
    # legacy grid that shows none
    return bool(
        table_quality.defects(new, words, hrules, vrules, new.bbox)["strong"]
        and not table_quality.defects(kept, legacy_words, hrules, vrules, kept.bbox)["strong"]
    )


_LEGACY_STRATEGIES: tuple[dict[str, Any] | None, ...] = (
    None,
    {"vertical_strategy": "text", "horizontal_strategy": "lines", "intersection_tolerance": 5},
    {"vertical_strategy": "text", "horizontal_strategy": "text", "intersection_tolerance": 5},
)


def legacy_candidate(page: Page, region: ola.LayoutRegion, page_w: float, page_h: float) -> Cand | None:
    """The candidate behind a legacy table region: the pdfplumber table, by the strategies the legacy path tries, whose
    grid is the region's. None when none reproduces it (the legacy grid came from somewhere else): the caller keeps it."""
    from app.modules.parsers.pdf import table_quality

    def norm(g: list[list[Any]] | None) -> list[list[str]]:
        return [[" ".join(str(c or "").split()) for c in row] for row in (g or [])]

    target = norm(region.table_grid)
    sub = page.within_bbox(ola._clamp_bbox(region.bbox, page_w, page_h))
    for settings in _LEGACY_STRATEGIES:
        try:
            found = sub.find_tables(settings) if settings else sub.find_tables()
        except Exception:
            continue
        for t in found:
            try:
                if norm(ola._trim_empty_rows_cols(ola._table_grid_by_words(t))) == target:
                    return table_quality.candidate_from_table("legacy", 0, t)
            except Exception:
                continue
    return None


def reconcile(
    page: Page, words: list[dict[str, Any]], boxes: list[tuple[float, float, float, float]],
    legacy: Sequence[ola.LayoutRegion] = (),
) -> list[Decision]:
    """One decision per model box (pixel boxes, page coordinates)."""
    if not boxes:
        return []
    try:
        lattices = list(page.find_tables())
        hrules, vrules = page_rules(page)
    except Exception:  # no page evidence: no decision, the legacy regions stay
        logger.warning("Ruled-table evidence for a page failed; keeping the legacy tables", exc_info=True)
        return [Decision(b, "none", reason="page evidence failed") for b in boxes]
    # The box that matches a ruled table best takes it; another box over the same table keeps the legacy region.
    def best_iou(b: tuple[float, float, float, float]) -> float:
        return max((ola._iou(_bbox(t), b) for t in lattices if same_table(_bbox(t), b)), default=0.0)

    claimed: set[int] = set()
    done: dict[int, Decision] = {}
    for i in sorted(range(len(boxes)), key=lambda i: -best_iou(boxes[i])):
        done[i] = decide_box(page, float(page.width), float(page.height), boxes[i], words, lattices, hrules, vrules, boxes, claimed, legacy)
    return [done[i] for i in range(len(boxes))]


def apply(
    legacy: list[ola.LayoutRegion], decisions: list[Decision],
) -> tuple[list[ola.LayoutRegion], list[tuple[float, float, float, float]]]:
    """The regions after the decisions, and the boxes that are not tables (their words stay text).
    A decision replaces the legacy table regions its new tables overlap (a not-table box: the ones it
    covers); legacy tables elsewhere in the box stay."""
    out = list(legacy)
    suppressed: list[tuple[float, float, float, float]] = []
    for d in decisions:
        if d.kind == "none":
            continue
        covered = [t.bbox for t in d.tables] or [d.box]
        out = [
            r for r in out
            if r.type != ola.LayoutRegionType.TABLE or not any(ola._boxes_overlap(r.bbox, c) for c in covered)
        ]
        out.extend(d.tables)
        if d.kind == "not_table":
            suppressed.append(d.box)
    return out, suppressed
