"""Hybrid table extraction, pure part: a defect score for any table grid, the route trigger, word-to-topology
assignment and the checks a replacement grid must pass. No I/O, no model, no pdfplumber, no environment.

A grid is described by a ``Topology`` (rows, columns, cells with boxes) and by a word-to-cell map. The score Q is
computed from that map only, never from predicted boxes, so a grid read by the rules and a grid proposed by a
structure model are scored by the same function.

Q is a defect detector. It is never used to accept a table or to compare two candidates (a high Q does not mean the
table is right). A table is routed when Q is low, when a strong defect fires, or when Q is unknown. A replacement
grid is taken only through ``can_replace`` (hard checks; never a ``not_table`` decision; never a new table).

Weights and thresholds are written down here before any calibration and are not tuned on held-out data.
"""
from __future__ import annotations

import math
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.modules.parsers.pdf.drawn_rules import Stroke as _Stroke
from app.modules.parsers.pdf.drawn_rules import crossings as _crossings

Word = dict[str, Any]
Box = tuple[float, float, float, float]  # x0, top, x1, bottom

# --- pinned constants -----------------------------------------------------------------------------------------
Q_ROUTE = 0.6  # a table with Q below this is routed
WEIGHTS = {
    "cross": 3.0,  # share of words with more than CROSS_AREA of their box outside their cell
    "impure": 1.5,  # numeric columns: share of filled cells that are not numbers
    "edge": 1.0,  # numeric columns: right-edge spread beyond one line height
    "header_gap": 1.0,  # numeric column without a header word, or a number in the label column's header
    "label_gap": 0.75,  # body rows with numbers and an empty label cell (label column is text)
    "multi_number": 3.0,  # numeric cells holding two numbers
    "section_merged": 3.0,  # label cells where a section line sits on top of an item line
    "rows_merged": 3.0,  # rows where two or more columns each hold the same two lines (two records)
    "total_mismatch": 2.0,  # total rows whose cells are not the sum of their addends
    "fused": 3.0,  # numbers with a raised small glyph fused on
    "non_upright": 3.0,  # fragments of rotated text left as words
    "displaced": 3.0,  # words whose centre lies outside the cell they are in
    "header_shift": 2.0,  # header words that sit outside the body extent of their column
    "ambiguous": 0.5,  # words that overlap two cells
}
CROSS_AREA = 0.25
DISPLACED_TOL_H = 1.0  # line heights
SMALL_GLYPH = 0.75  # a glyph at most this share of its neighbours' size ...
RAISE = 0.12  # ... whose baseline is higher by this share of the size is a raised mark
BIND_GAP_H = 8.0  # line heights: the most a symbol may sit from the token it binds to
LINE_TOL = 0.6  # line heights: two tokens of one line differ in centre less than this
GAP_UNSPLIT_H = 1.5  # line heights: a gap this wide inside a cell may be two cells
EDGE_OK_H = 1.0
STACK_GAP = 1.5  # rotated fragments: gap between stacked fragments, in fragment thicknesses
MULTI_NUMBER_STRONG = 0.3  # share of a numeric column's cells holding two numbers
TOTAL_RE = re.compile(r"^\s*(total|subtotal|sub-total|sum)\b", re.I)
NUM_CORE = re.compile(r"^\.?\d[\d,]*(\.\d+)?$")
DASHES = {"-", "–", "—", "−", "--"}
LEFT_SYMBOLS = {"$", "(", "€", "£", "¥"}  # bind to the token on their right
RIGHT_SYMBOLS = {")", "%"}  # bind to the token on their left
YEAR_RE = re.compile(r"^(19|20|21)\d\d$")


# --- model ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    r0: int
    c0: int
    r1: int
    c1: int
    box: Box


@dataclass
class Topology:
    nrows: int
    ncols: int
    cells: list[Cell]
    header_rows: int | None = None  # None: inferred


@dataclass
class Token:
    """One or more atoms read as one unit. ``ids`` are atom ids ``(word index, part)``. ``anchor`` is the box the
    token is placed by (a bound symbol follows its host token)."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    ids: tuple[tuple[int, int], ...]
    anchor: Box = (0.0, 0.0, 0.0, 0.0)
    marker: bool = False  # a raised mark split off a number: goes to the footnotes, not to a cell
    upright: bool = True
    rebuilt: bool = False
    fused: bool = False  # still carries a raised small glyph

    @property
    def box(self) -> Box:
        return (self.x0, self.top, self.x1, self.bottom)

    @property
    def h(self) -> float:
        return max(0.1, self.bottom - self.top)


@dataclass
class Assignment:
    tokens: list[Token]
    cell_of: dict[int, int]  # token index -> cell index
    footnotes: list[int] = field(default_factory=list)
    unassigned: list[int] = field(default_factory=list)
    ambiguous: list[int] = field(default_factory=list)


@dataclass
class Report:
    q: float | None  # None: unknown
    features: dict[str, float]
    defects: dict[str, float]  # name -> count or share, only those that fired
    strong: list[str]
    hard_fail: list[str]
    would_route: bool
    reasons: list[str]

    def log_fields(self) -> dict[str, Any]:
        """Low-cardinality fields for a log line: no cell text."""
        return {"q": None if self.q is None else round(self.q, 2), "would_route": self.would_route, "reasons": list(self.reasons), "hard_fail": list(self.hard_fail)}


# --- numbers --------------------------------------------------------------------------------------------------


def is_number(text: str) -> bool:
    t = text.replace("$", "").replace("€", "").replace("£", "").replace("%", "").replace(" ", "")
    t = t.strip("()-−–")
    return bool(NUM_CORE.match(t))


def is_dash(text: str) -> bool:
    return text.strip() in DASHES


def parse_number(text: str) -> float | None:
    t = text.strip()
    if is_dash(t):
        return 0.0
    if "%" in t or not is_number(t):
        return None
    neg = t.startswith(("(", "-", "−", "–")) or t.endswith(")") and "(" in t
    core = re.sub(r"[^\d.]", "", t)
    try:
        v = float(core)
    except ValueError:
        return None
    return -v if neg else v


def _is_symbol(t: Token) -> bool:
    """A currency sign or bracket on its own: it sits beside its number in one cell, with a gap, by design."""
    return t.text in LEFT_SYMBOLS | RIGHT_SYMBOLS


def _numeric_token(t: Token) -> bool:
    return not t.marker and (is_number(t.text) or is_dash(t.text))


# --- atoms and tokens (R1-7) ---------------------------------------------------------------------------------


def _size(c: dict[str, Any]) -> float:
    return float(c.get("size") or (float(c["bottom"]) - float(c["top"])))


def raised_tail(chars: list[dict[str, Any]]) -> int | None:
    """Index where a trailing run of raised, smaller glyphs starts, or None. The run follows a digit or ``)``."""
    if len(chars) < 2:
        return None
    base = max(_size(c) for c in chars)
    full = [c for c in chars if _size(c) > SMALL_GLYPH * base]
    if not full:
        return None
    base_bottom = statistics.median(float(c["bottom"]) for c in full)
    k = len(chars)
    while k > 0 and _size(chars[k - 1]) <= SMALL_GLYPH * base and float(chars[k - 1]["bottom"]) <= base_bottom - RAISE * base:
        k -= 1
    if k == len(chars) or k == 0:
        return None
    prev = str(chars[k - 1]["text"])
    return k if prev[-1:].isdigit() or prev[-1:] == ")" else None


def _union(cs: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    return (min(float(c["x0"]) for c in cs), min(float(c["top"]) for c in cs), max(float(c["x1"]) for c in cs), max(float(c["bottom"]) for c in cs))


def atoms_of(words: list[Word], *, split_raised: bool = True) -> list[Token]:
    """One token per word, except that a raised small glyph run is split off a number (the mark token has
    ``marker=True``). Without ``split_raised`` the word stays whole and is flagged ``fused``."""
    out: list[Token] = []
    for i, w in enumerate(words):
        chars = w.get("chars")
        k = raised_tail(chars) if chars else None
        upright = bool(w.get("upright", True))
        if k is not None and split_raised and chars:
            for part, cs in enumerate((chars[:k], chars[k:])):
                x0, top, x1, bot = _union(cs)
                out.append(Token("".join(str(c["text"]) for c in cs), x0, x1, top, bot, ((i, part),), (x0, top, x1, bot), marker=part == 1, upright=upright))
            continue
        box = (float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"]))
        out.append(Token(str(w["text"]), box[0], box[2], box[1], box[3], ((i, 0),), box, upright=upright, fused=k is not None))
    return out


def _join_stack(tokens: list[Token], group: list[int], rot: int) -> Token:
    order = list(reversed(group)) if rot == 90 else list(group)
    ts = [tokens[j] for j in order]
    x0, x1 = min(t.x0 for t in ts), max(t.x1 for t in ts)
    top, bot = min(t.top for t in ts), max(t.bottom for t in ts)
    return Token("".join(t.text for t in ts), x0, x1, top, bot, tuple(a for t in ts for a in t.ids), (x0, top, x1, bot), upright=False, rebuilt=True)


def rebuild_rotated(tokens: list[Token], words: list[Word]) -> list[Token]:
    """Join stacked fragments of rotated text into one token in reading order (``rot`` 90: bottom to top, -90: top
    to bottom). The result is marked ``rebuilt``; fragments that cannot be joined stay as they are."""
    rot_of = {t.ids[0][0]: int(words[t.ids[0][0]].get("rot", 90)) for t in tokens if not t.upright}
    frag = [i for i, t in enumerate(tokens) if not t.upright]
    used: set[int] = set()
    out_new: list[Token] = []
    for rot in (90, -90):
        mine = sorted((i for i in frag if rot_of[tokens[i].ids[0][0]] == rot), key=lambda i: tokens[i].top)
        groups: list[list[int]] = []
        for i in mine:
            t = tokens[i]
            if groups:
                g = tokens[groups[-1][-1]]
                thick = max(0.1, t.x1 - t.x0)
                same_col = min(g.x1, t.x1) - max(g.x0, t.x0) > 0.4 * min(g.x1 - g.x0, thick)
                if same_col and t.top - g.bottom <= STACK_GAP * thick:
                    groups[-1].append(i)
                    continue
            groups.append([i])
        for grp in groups:
            if len(grp) >= 2:
                out_new.append(_join_stack(tokens, grp, rot))
                used.update(grp)
    return [t for i, t in enumerate(tokens) if i not in used] + out_new


def bind_symbols(tokens: list[Token]) -> list[Token]:
    """``$`` and ``(`` bind to the token on their right, ``)`` and ``%`` to the token on their left, on one line, and
    only to the next token in that direction (nothing between them, at most ``BIND_GAP_H`` line heights away). Never
    by choosing the nearer of two. The bound token is placed by its host."""
    order = sorted(range(len(tokens)), key=lambda i: (round((tokens[i].top + tokens[i].bottom) / 2 / 3), tokens[i].x0))
    absorbed: set[int] = set()
    merged: dict[int, Token] = {}

    def same_line(a: Token, b: Token) -> bool:
        return abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) <= LINE_TOL * max(a.h, b.h)

    def host_ok(t: Token) -> bool:
        return not t.marker and bool(t.text) and (t.text[0].isdigit() or t.text[0] in ".-(−–" or t.text[0] in LEFT_SYMBOLS or is_number(t.text))

    for i in sorted(range(len(tokens)), key=lambda k: tokens[k].x0):
        t = tokens[i]
        if t.marker or i in absorbed or t.text not in LEFT_SYMBOLS | RIGHT_SYMBOLS:
            continue
        line = [k for k in range(len(tokens)) if k != i and same_line(t, tokens[k]) and k not in absorbed]
        if t.text in LEFT_SYMBOLS:
            cand = [k for k in line if tokens[k].x0 >= t.x1 - 0.5]
            if not cand:
                continue
            j = min(cand, key=lambda k: tokens[k].x0)
            gap = tokens[j].x0 - t.x1
            if gap > BIND_GAP_H * t.h or not host_ok(merged.get(j, tokens[j])):
                continue
            h = merged.get(j, tokens[j])
            merged[j] = Token(t.text + h.text, t.x0, h.x1, min(t.top, h.top), max(t.bottom, h.bottom), t.ids + h.ids, h.anchor, upright=h.upright, fused=h.fused)
            absorbed.add(i)
        else:
            cand = [k for k in line if tokens[k].x1 <= t.x0 + 0.5]
            if not cand:
                continue
            j = max(cand, key=lambda k: tokens[k].x1)
            gap = t.x0 - tokens[j].x1
            if gap > BIND_GAP_H * t.h or not host_ok(merged.get(j, tokens[j])):
                continue
            h = merged.get(j, tokens[j])
            merged[j] = Token(h.text + t.text, h.x0, t.x1, min(t.top, h.top), max(t.bottom, h.bottom), h.ids + t.ids, h.anchor, upright=h.upright, fused=h.fused)
            absorbed.add(i)
    del order
    return [merged.get(i, t) for i, t in enumerate(tokens) if i not in absorbed]


def prepare(words: list[Word], *, split_raised: bool = True, rebuild: bool = True, bind: bool = True) -> list[Token]:
    toks = atoms_of(words, split_raised=split_raised)
    if rebuild:
        toks = rebuild_rotated(toks, words)
    if bind:
        toks = bind_symbols(toks)
    return toks


# --- assignment -----------------------------------------------------------------------------------------------


def _area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a: Box, b: Box) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def assign(tokens: list[Token], topo: Topology) -> Assignment:
    """Each token goes to the cell its anchor overlaps most. A token overlapping two cells goes to the one with the
    larger overlap; a tie goes to the cell whose alignment edge is nearer (a number: right edge, other text: left
    edge), then to the lower cell index. A token overlapping no cell is unassigned (the grid then fails the hard
    checks). A raised mark goes to the footnotes. A cell spanning rows or columns holds its text in that one cell."""
    a = Assignment(tokens, {})
    for i, t in enumerate(tokens):
        if t.marker:
            a.footnotes.append(i)
            continue
        box = t.anchor if _area(t.anchor) > 0 else t.box
        ov = [(_inter(box, c.box), k) for k, c in enumerate(topo.cells)]
        ov = [(o, k) for o, k in ov if o > 1e-9]
        if not ov:
            a.unassigned.append(i)
            continue
        if len(ov) > 1:
            a.ambiguous.append(i)
        best = max(o for o, _ in ov)
        tied = [k for o, k in ov if o >= best - 1e-6 * max(best, 1.0)]
        if len(tied) > 1:
            numeric = _numeric_token(t)
            tied.sort(key=lambda k: (abs(topo.cells[k].box[2] - box[2]) if numeric else abs(topo.cells[k].box[0] - box[0]), k))
        a.cell_of[i] = tied[0]
    return a


# --- helpers over a grid --------------------------------------------------------------------------------------


def _lines(ts: list[Token]) -> list[list[Token]]:
    ts = sorted((t for t in ts if t.upright), key=lambda t: (t.top + t.bottom) / 2)
    out: list[list[Token]] = []
    for t in ts:
        c = (t.top + t.bottom) / 2
        if out and abs(c - statistics.mean((u.top + u.bottom) / 2 for u in out[-1])) <= LINE_TOL * t.h:
            out[-1].append(t)
        else:
            out.append([t])
    return [sorted(ln, key=lambda t: t.x0) for ln in out]


def cell_text(ts: list[Token]) -> str:
    return " ".join(t.text for ln in _lines(ts) for t in ln)


def _grid(topo: Topology, a: Assignment) -> dict[tuple[int, int], list[Token]]:
    g: dict[tuple[int, int], list[Token]] = {}
    for i, k in a.cell_of.items():
        c = topo.cells[k]
        g.setdefault((c.r0, c.c0), []).append(a.tokens[i])
    return g


def infer_header_rows(topo: Topology, g: dict[tuple[int, int], list[Token]]) -> int:
    if topo.header_rows is not None:
        return topo.header_rows
    n = 0
    for r in range(topo.nrows):
        row = [t for c in range(topo.ncols) for t in g.get((r, c), [])]
        if any(_numeric_token(t) and not YEAR_RE.match(t.text) for t in row):
            break
        n += 1
    while n > 1 and not any(g.get((n - 1, c)) for c in range(1, topo.ncols)):
        n -= 1  # a label-only row is a section or title line, not a header row
    if n == 1 and not any(g.get((0, c)) for c in range(1, topo.ncols)):
        n = 0
    return min(n, 3)


def _median_h(a: Assignment) -> float:
    hs = [t.h for t in a.tokens if t.upright and not t.marker]
    return statistics.median(hs) if hs else 8.0


# --- hard checks (R1-4) ---------------------------------------------------------------------------------------


def _monotone(topo: Topology) -> list[str]:
    out: list[str] = []
    cx = lambda c: (c.box[0] + c.box[2]) / 2  # noqa: E731
    cy = lambda c: (c.box[1] + c.box[3]) / 2  # noqa: E731
    by_row: dict[int, list[Cell]] = {}
    by_col: dict[int, list[Cell]] = {}
    for c in topo.cells:
        if c.c0 == c.c1:
            by_row.setdefault(c.r0, []).append(c)
        if c.r0 == c.r1:
            by_col.setdefault(c.c0, []).append(c)
    for cs in by_row.values():
        xs = [cx(c) for c in sorted(cs, key=lambda c: c.c0)]
        if any(b < a - 0.5 for a, b in zip(xs, xs[1:])):
            out.append("cols_not_monotone")
            break
    for cs in by_col.values():
        ys = [cy(c) for c in sorted(cs, key=lambda c: c.r0)]
        if any(b < a - 0.5 for a, b in zip(ys, ys[1:])):
            out.append("rows_not_monotone")
            break
    # a column keeps its place across rows
    med = {c: statistics.median(cx(x) for x in cs if x.c0 == x.c1) for c, cs in by_col.items() if any(x.c0 == x.c1 for x in cs)}
    ks = sorted(med)
    if any(med[b] < med[a] - 0.5 for a, b in zip(ks, ks[1:])) and "cols_not_monotone" not in out:
        out.append("cols_not_monotone")
    return out


def _unsplit_columns(topo: Topology, g: dict[tuple[int, int], list[Token]], hrows: int, h: float) -> int:
    """Cells holding two words with a gap that lines up with such a gap in two other rows of the same column."""
    gaps: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for (r, c), ts in g.items():
        if r < hrows:
            continue
        for ln in _lines(ts):
            for u, v in zip(ln, ln[1:]):
                if v.x0 - u.x1 >= GAP_UNSPLIT_H * h and _numeric_token(u) and _numeric_token(v) and not _is_symbol(u) and not _is_symbol(v):
                    gaps.setdefault((r, c), []).append((u.x1, v.x0))
    n = 0
    for (r, c), gs in gaps.items():
        others = {r2 for (r2, c2) in gaps if c2 == c and r2 != r and any(min(b, d) > max(a, e) for a, b in gs for e, d in gaps[(r2, c2)])}
        if len(others) >= 1:
            n += 1
    return n


def hard_checks(words: list[Word] | None, topo: Topology, a: Assignment) -> list[str]:
    """The R1-4 checks a replacement grid must pass. Empty list: passes."""
    fails: list[str] = []
    if topo.nrows < 2 or topo.ncols < 2:
        fails.append("under_2x2")
    if a.unassigned:
        fails.append("unassigned")
    seen: dict[tuple[int, int], int] = {}
    for i in list(a.cell_of) + a.footnotes:
        for aid in a.tokens[i].ids:
            seen[aid] = seen.get(aid, 0) + 1
    every = {aid for t in a.tokens for aid in t.ids}
    if any(v != 1 for v in seen.values()) or (set(seen) | {aid for i in a.unassigned for aid in a.tokens[i].ids}) != every:
        fails.append("bijection")
    cell_by_word: dict[int, set[int]] = {}
    for i, k in a.cell_of.items():
        for aid in a.tokens[i].ids:
            cell_by_word.setdefault(aid[0], set()).add(k)
    if any(len(v) > 1 for v in cell_by_word.values()):
        fails.append("word_split")
    fails.extend(_monotone(topo))
    g = _grid(topo, a)
    if _unsplit_columns(topo, g, infer_header_rows(topo, g), _median_h(a)):
        fails.append("unsplit_columns")
    return fails


# --- Q ---------------------------------------------------------------------------------------------------------


def _section_merged(g: dict[tuple[int, int], list[Token]], topo: Topology, hrows: int) -> int:
    """Label cells whose first line is a section line (ends with a colon, or is upper case) with an item line
    below it, while the row's numbers sit beside the last line only."""
    n = 0
    for r in range(hrows, topo.nrows):
        ts = g.get((r, 0), [])
        lines = _lines(ts)
        if len(lines) < 2:
            continue
        first = " ".join(t.text for t in lines[0])
        letters = [ch for ch in first if ch.isalpha()]
        section = first.rstrip().endswith(":") or (len(letters) >= 3 and all(ch.isupper() for ch in letters))
        if not section:
            continue
        nums = [t for c in range(1, topo.ncols) for t in g.get((r, c), []) if _numeric_token(t)]
        last_top = min(t.top for t in lines[-1])
        if nums and all(t.top >= last_top - 0.5 * t.h for t in nums):
            n += 1
    return n


def _rows_merged(g: dict[tuple[int, int], list[Token]], topo: Topology, hrows: int) -> tuple[int, int]:
    """Rows where two or more columns each hold the same two lines (two records in one row). Returns (rows whose
    merged cells are numbers, other rows): wrapped text in two columns lines up by chance, numbers do not."""
    num_rows = other_rows = 0
    for r in range(hrows, topo.nrows):
        ys: list[tuple[list[float], bool]] = []
        for c in range(topo.ncols):
            ts = g.get((r, c), [])
            ls = _lines(ts)
            if len(ls) >= 2:
                ys.append(([statistics.mean((t.top + t.bottom) / 2 for t in ln) for ln in ls], all(_numeric_token(t) for t in ts)))
        def aligned(a: list[float], b: list[float]) -> bool:
            return sum(1 for y in a if any(abs(y - z) <= 0.6 * 8 for z in b)) >= 2

        nums = [y for y, isnum in ys if isnum]
        if len(nums) >= 2 and any(aligned(nums[0], other) for other in nums[1:]):
            num_rows += 1
        elif len(ys) >= 2 and any(aligned(ys[0][0], other) for other, _ in ys[1:]):
            other_rows += 1
    return num_rows, other_rows


def _total_check(g: dict[tuple[int, int], list[Token]], topo: Topology, hrows: int, numeric_cols: list[int]) -> tuple[int, int, int]:
    """(checked cells, mismatching cells, total rows with a mismatch in two or more columns, or in the only column)."""
    checked = bad = 0
    bad_by_row: dict[int, int] = {}
    for c in numeric_cols:
        since_boundary: list[float] = []
        since_total: list[float] = []
        for r in range(hrows, topo.nrows):
            row_has = any(_numeric_token(t) for c2 in range(1, topo.ncols) for t in g.get((r, c2), []))
            if not row_has:
                since_boundary, since_total = [], []
                continue
            ts = [t for t in g.get((r, c), []) if _numeric_token(t)]
            if len(ts) != 1:
                continue
            v = parse_number(ts[0].text)
            if v is None:
                continue
            if TOTAL_RE.match(cell_text(g.get((r, 0), []))):
                for block in (since_total, since_boundary):
                    if len(block) >= 2:
                        break
                else:
                    since_total = []
                    continue
                checked += 1
                ok = any(len(b) >= 2 and abs(sum(b) - v) <= max(0.51, 1e-6 * abs(v)) for b in (since_total, since_boundary))
                bad += not ok
                bad_by_row[r] = bad_by_row.get(r, 0) + (not ok)
                since_total = []
            else:
                since_boundary.append(v)
                since_total.append(v)
    wide = sum(1 for v in bad_by_row.values() if v >= (2 if len(numeric_cols) >= 2 else 1))
    return checked, bad, wide


def evaluate(
    words: list[Word] | None, topo: Topology | None, *, assignment: Assignment | None = None,
    split_raised: bool = True, rebuild: bool = True,
) -> Report:
    """Q, defects and the route decision for one grid. ``topo`` None: Q unknown. ``assignment`` lets a caller supply
    the word-to-cell map (otherwise words are prepared and assigned by ``assign``)."""
    if topo is None or (words is None and assignment is None):
        return Report(None, {}, {}, [], [], True, ["q_unknown"])
    a = assignment if assignment is not None else assign(prepare(words or [], split_raised=split_raised, rebuild=rebuild), topo)
    hard = hard_checks(words, topo, a)
    g = _grid(topo, a)
    hrows = infer_header_rows(topo, g)
    h = _median_h(a)
    body = range(hrows, topo.nrows)
    f: dict[str, float] = {}
    d: dict[str, float] = {}
    strong: list[str] = [x for x in hard if x != "under_2x2"]
    toks = [t for i, t in enumerate(a.tokens) if i in a.cell_of]
    n_tok = max(1, len(toks))

    # crossing / displacement
    cross = disp = 0
    for i, k in a.cell_of.items():
        t, c = a.tokens[i], topo.cells[k]
        ba = t.anchor if _area(t.anchor) > 0 else t.box
        if _area(ba) > 0 and _inter(ba, c.box) < (1 - CROSS_AREA) * _area(ba):
            cross += 1
        cxm, cym = (ba[0] + ba[2]) / 2, (ba[1] + ba[3]) / 2
        tol = DISPLACED_TOL_H * max(h, 4.0)
        if t.upright and (cxm < c.box[0] - tol or cxm > c.box[2] + tol or cym < c.box[1] - tol or cym > c.box[3] + tol):
            disp += 1
    f["cross"] = cross / n_tok
    f["displaced"] = disp / n_tok
    f["ambiguous"] = len(a.ambiguous) / n_tok
    if disp:
        d["displaced"] = disp
        strong.append("displaced")
    if a.unassigned:
        d["unassigned"] = len(a.unassigned)

    # numeric columns
    ncols = sorted({c for (_, c) in g if c >= 0})
    numeric_cols: list[int] = []
    impure = fill = 0
    multi = numcells = 0
    spreads: list[float] = []
    for c in ncols:
        cells = [(r, g[(r, c)]) for r in body if g.get((r, c))]
        if not cells:
            continue
        num = [(r, ts) for r, ts in cells if all(_numeric_token(t) for t in ts)]
        if len(num) >= 0.5 * len(cells) and len(num) >= 2:
            numeric_cols.append(c)
            impure += len(cells) - len(num)
            fill += len(cells)
            m = sum(1 for _, ts in num if sum(1 for t in ts if not is_dash(t.text)) >= 2)
            multi += m
            numcells += len(num)
            if m >= max(1, MULTI_NUMBER_STRONG * len(num)):
                d["multi_number"] = d.get("multi_number", 0) + m
                if "multi_number" not in strong:
                    strong.append("multi_number")
            ends = []
            for _, ts in num:
                for t in ts:
                    if is_dash(t.text):
                        continue
                    gw = (t.x1 - t.x0) / max(1, len(t.text))
                    ends.append((t.x1 - gw if t.text.endswith((")", "%")) else t.x1, t.x0, (t.x0 + t.x1) / 2))
            if len(ends) >= 3:
                sp = min(max(e[k] for e in ends) - min(e[k] for e in ends) for k in range(3)) / max(h, 1.0)
                spreads.append(min(1.0, max(0.0, sp - EDGE_OK_H) / 3.0))
    f["impure"] = impure / fill if fill else 0.0
    f["edge"] = statistics.mean(spreads) if spreads else 0.0
    f["multi_number"] = multi / numcells if numcells else 0.0

    # header and label
    hgap = 0
    shifted = 0
    for c in numeric_cols:
        hdr = [t for r in range(hrows) for t in g.get((r, c), [])]
        if hrows and not hdr:
            hgap += 1
        elif hdr:
            xs = [t for r in body for t in g.get((r, c), []) if _numeric_token(t)]
            if xs:
                lo, hi = min(t.x0 for t in xs), max(t.x1 for t in xs)
                pad = 0.5 * max(hi - lo, 4 * h)
                for r in range(hrows):
                    cell = next((x for x in topo.cells if (x.r0, x.c0) == (r, c)), None)
                    if cell is not None and cell.c1 > cell.c0:
                        continue
                    for t in g.get((r, c), []):
                        if t.upright and not (lo - pad <= (t.x0 + t.x1) / 2 <= hi + pad):
                            shifted += 1
    label_numeric_hdr = 1 if hrows and any(_numeric_token(t) and not t.marker for t in g.get((0, 0), [])) and 0 not in numeric_cols else 0
    f["header_gap"] = min(1.0, (hgap + label_numeric_hdr) / max(1, len(numeric_cols)))
    f["header_shift"] = min(1.0, shifted / max(1, len(numeric_cols)))
    if shifted:
        d["header_shift"] = shifted
    lab_gap = 0
    if 0 not in numeric_cols and topo.ncols > 1:
        for r in body:
            if not g.get((r, 0)) and any(_numeric_token(t) for c in range(1, topo.ncols) for t in g.get((r, c), [])):
                lab_gap += 1
    f["label_gap"] = lab_gap / max(1, len(body))

    sm = _section_merged(g, topo, hrows)
    rm, rm_text = _rows_merged(g, topo, hrows)
    f["section_merged"] = sm / max(1, len(body))
    f["rows_merged"] = (rm + 0.3 * rm_text) / max(1, len(body))
    if sm:
        d["section_merged"] = sm
        strong.append("section_merged")
    if rm:
        d["rows_merged"] = rm
        strong.append("rows_merged")
    checked, bad, wide = _total_check(g, topo, hrows, numeric_cols)
    f["total_mismatch"] = bad / checked if checked else 0.0
    if wide:
        d["total_mismatch"] = wide
        strong.append("total_mismatch")
    fused = sum(1 for t in toks if t.fused)
    f["fused"] = fused / n_tok
    if fused:
        d["fused"] = fused
        strong.append("fused_superscript")
    frag = sum(1 for t in toks if not t.upright and not t.rebuilt)
    f["non_upright"] = frag / n_tok
    f["non_upright_share"] = sum(1 for t in toks if not t.upright) / n_tok  # information, not a defect
    if frag:
        d["non_upright"] = frag
        strong.append("rotated_fragments")
    for name in ("unsplit_columns",):
        if name in hard:
            d[name] = 1
    for name in hard:
        d.setdefault(name, 1)

    pen = sum(WEIGHTS[k] * min(1.0, f.get(k, 0.0)) for k in WEIGHTS)
    q = math.exp(-pen)
    strong = list(dict.fromkeys(strong))
    reasons: list[str] = []
    if q < Q_ROUTE:
        reasons.append("low_q")
    reasons.extend(f"strong:{s}" for s in strong)
    return Report(q, f, d, strong, hard, bool(reasons), reasons)


def unknown_report() -> Report:
    return Report(None, {}, {}, [], [], True, ["q_unknown"])


def route(report: Report) -> tuple[bool, list[str]]:
    """The route trigger: low Q, a strong defect, or Q unknown."""
    return report.would_route, list(report.reasons)


# --- replacement (R1-4) ---------------------------------------------------------------------------------------


def can_replace(h: Report | None, decision_kind: str, t_topo: Topology | None, t_hard: list[str]) -> tuple[bool, str]:
    """May grid T replace heuristic grid H? Only when H exists (a model never makes a new table), H is not a
    ``not_table`` decision, H is routed, T is at least 2 x 2 and T passes every hard check."""
    if h is None:
        return False, "no_heuristic_table"
    if decision_kind == "not_table":
        return False, "not_table"
    if not h.would_route:
        return False, "h_not_routed"
    if t_topo is None or t_topo.nrows < 2 or t_topo.ncols < 2:
        return False, "under_2x2"
    if t_hard:
        return False, "hard_check:" + t_hard[0]
    return True, "ok"


# --- adapters -------------------------------------------------------------------------------------------------


def topology_from_rows(rows: list[tuple[float, float]], cells: list[list[tuple[float, float]]], present: list[list[bool]] | None = None) -> Topology:
    """A topology from row bands (top, bottom) and per row the (x0, x1) of its cells, left to right. ``present``
    (the dense grid's non-None positions) places a row's cells in the columns they occupy when a span covers
    some; without it the cells fill columns 0, 1, ..."""
    out: list[Cell] = []
    ncols = max((len(p) for p in present), default=0) if present else max((len(r) for r in cells), default=0)
    for r, (row, (top, bot)) in enumerate(zip(cells, rows)):
        cols = [c for c, ok in enumerate(present[r]) if ok] if present else list(range(len(row)))
        for j, (c, (x0, x1)) in enumerate(zip(cols, row)):
            last = (cols[j + 1] - 1) if j + 1 < len(cols) else (ncols - 1 if present else c)  # None positions after a cell are its span
            out.append(Cell(r, c, r, max(c, last), (float(x0), float(top), float(x1), float(bot))))
    return Topology(len(rows), ncols, out)


# --- structure model: per-box plan and checks on the returned grid ------------------------------------------------

# Frozen constants of the model request. A test pins them against the evaluation configuration.
LINE_JOIN_H = 0.5  # a word joins the current line when its centre is within this many median heights of the line's first centre
SEGMENT_GAP_H = 1.0  # consecutive words further apart than this many median heights are separate segments
PREFILTER_MIN_LINES = 2
CAP_K = 2.0
CAP_MARGIN = 16
CAP_CEILING = 1024
MODEL_LIBRARY_VERSION = "4.0.4"
MODEL_MODE = "accurate"
# What a model token is: a word of the PDF text layer. Words the layout pass makes up (vector bullets) are not tokens. A change
# of this definition changes every measured grid, so it is part of the configuration hash and of the document log line.
TOKEN_DEFINITION = "text-layer-v2"
MODEL_REVISION = "fc0f2d45e2218ea24bce5045f58a389aed16dc23"  # the weights commit the measured grids came from
# Hash of the weight files the measured grids came from: the sha256 of each weight file in the mode folder (sha256 2a7d6c92...74d9 for the accurate file), combined by name (see ``weights_sha256`` in the child module).
MODEL_WEIGHTS_SHA256 = "b7682f9ea68ca3c7e1f87a8054e4d7923789cb6e37284ea44e5c985f54004dff"
WORD_LOSS_MAX = 0.01

REASONS = (
    "none", "prefilter", "budget", "deadline", "service_busy", "shadow_busy", "service_down", "unsupported",
    "version_mismatch", "mode_fast", "timeout", "error", "malformed", "cap_hit", "small_grid", "word_loss",
    "collect_error", "spool_full", "invalid_input",
)
# The reasons that make the result depend on load rather than on the document.
LOAD_REASONS = ("service_busy", "deadline", "timeout", "service_down", "error")
GLYPH_DENY = frozenset("•◦‣⁃∙▪■□●○")


def lines_cols(words: list[Word]) -> tuple[int, int]:
    """Text lines in the words and the most segments on one line (the column estimate)."""
    if not words:
        return 0, 0
    hs = sorted(w["bottom"] - w["top"] for w in words)
    h = hs[len(hs) // 2] or 8.0
    lines: list[list[Any]] = []
    for w in sorted(words, key=lambda w: (w["top"] + w["bottom"]) / 2):
        cy = (w["top"] + w["bottom"]) / 2
        if lines and abs(cy - lines[-1][0]) <= LINE_JOIN_H * h:
            lines[-1][1].append(w)
        else:
            lines.append([cy, [w]])
    cols = 0
    for _, ln in lines:
        ln.sort(key=lambda w: w["x0"])
        cols = max(cols, 1 + sum(1 for a, b in zip(ln, ln[1:], strict=False) if b["x0"] - a["x1"] >= SEGMENT_GAP_H * h))
    return len(lines), cols


def step_cap(lines: int, cols: int) -> int:
    return int(min(CAP_CEILING, CAP_K * lines * (cols + 1) + CAP_MARGIN))


@dataclass(frozen=True)
class BoxPlan:
    reason: str  # "none" (send it), "prefilter" or "cap_hit"
    lines: int
    cols_est: int
    cap: int
    cap_reason: str | None = None


def plan_box(words: list[Word]) -> BoxPlan:
    """Whether a box is sent to the model, and with what step cap. Same order as the evaluation harness."""
    lines, cols = lines_cols(words)
    cap = step_cap(lines, cols)
    if lines < PREFILTER_MIN_LINES:
        return BoxPlan("prefilter", lines, cols, cap)
    if lines * (cols + 1) + 1 > CAP_CEILING:  # the table's own tokens cannot fit under the ceiling
        return BoxPlan("cap_hit", lines, cols, cap, "ceiling")
    return BoxPlan("none", lines, cols, cap)


@dataclass(frozen=True)
class GridCheck:
    ok: bool
    reason: str | None  # why it is malformed, else None
    overlaps: int  # grid positions covered by more than one cell (counted, resolved last wins)
    n_rows: int
    n_cols: int


def validate_cells(cells: list[dict[str, Any]], n_rows: int, n_cols: int, token_ids: set[int]) -> GridCheck:
    """Structure checks on a returned grid. Overlapping cells are counted, not malformed."""
    if not cells:
        return GridCheck(False, "no_cells", 0, n_rows, n_cols)
    seen_tokens: set[int] = set()
    covered: set[tuple[int, int]] = set()
    overlaps = 0
    for c in cells:
        r0, r1, c0, c1 = c["r0"], c["r1"], c["c0"], c["c1"]
        if r0 < 0 or c0 < 0 or r1 > n_rows or c1 > n_cols:
            return GridCheck(False, "index_range", overlaps, n_rows, n_cols)
        if r1 <= r0 or c1 <= c0:
            return GridCheck(False, "empty_span", overlaps, n_rows, n_cols)
        for t in c.get("tokens", []):
            if t not in token_ids:
                return GridCheck(False, "unknown_token", overlaps, n_rows, n_cols)
            if t in seen_tokens:
                return GridCheck(False, "token_twice", overlaps, n_rows, n_cols)
            seen_tokens.add(t)
        for r in range(r0, r1):
            for k in range(c0, c1):
                if (r, k) in covered:
                    overlaps += 1
                covered.add((r, k))
    return GridCheck(True, None, overlaps, n_rows, n_cols)


def model_shape(cells: list[dict[str, Any]]) -> tuple[int, int]:
    """Rows and columns that hold at least one cell with a word (empty rows and columns dropped)."""
    rows = {r for c in cells if c.get("tokens") for r in range(c["r0"], c["r1"])}
    cols = {k for c in cells if c.get("tokens") for k in range(c["c0"], c["c1"])}
    return len(rows), len(cols)


def _has_content(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def word_losses(token_texts: dict[int, str], cells: list[dict[str, Any]]) -> tuple[float, float]:
    """Share of the request's words listed in no cell: over all words, and over words with a letter or a digit."""
    listed = {t for c in cells for t in c.get("tokens", [])}
    lost = [t for t in token_texts if t not in listed]
    all_share = len(lost) / len(token_texts) if token_texts else 0.0
    content = [t for t, text in token_texts.items() if _has_content(text)]
    content_lost = [t for t in content if t not in listed]
    return all_share, (len(content_lost) / len(content) if content else 0.0)


def content_words_lost(token_texts: dict[int, str], cells: list[dict[str, Any]]) -> int:
    """Words with a letter or a digit that no cell lists. The strict figure: any one is a loss, beside the 1 % share."""
    listed = {t for c in cells for t in c.get("tokens", [])}
    return sum(1 for t, text in token_texts.items() if _has_content(text) and t not in listed)


def content_excess(grid_texts: list[str], word_texts: list[str]) -> int:
    """Letters and digits the evidence grid holds that the box's own words do not: the multiset difference of the two, summed
    (a grid that holds a caption or a neighbour's text has an excess; a grid that holds fewer is not counted)."""
    from collections import Counter

    have = Counter(ch for t in grid_texts for ch in t if ch.isalnum())
    own = Counter(ch for t in word_texts for ch in t if ch.isalnum())
    return sum((have - own).values())


def would_route(failure: str | None, check: GridCheck | None, shape: tuple[int, int] | None, content_loss: float) -> tuple[str, str]:
    """What a model-first mode would do with this table: ``("model" | "evidence", reason)``. First match wins,
    in the order the reasons are listed: a request failure, malformed, a grid under 2 x 2, word loss above 1 %."""
    if failure and failure != "none":
        return "evidence", failure
    if check is None or not check.ok:
        return "evidence", "malformed"
    if shape is None or shape[0] < 2 or shape[1] < 2:
        return "evidence", "small_grid"
    if content_loss > WORD_LOSS_MAX:
        return "evidence", "word_loss"
    return "model", "none"


def box_class(box_ids: set[int], evidence_tables: list[set[int]]) -> str:
    """How the box's word set relates to the tables the evidence path emits for it (word sets by id)."""
    hits = [t for t in evidence_tables if t & box_ids]
    if not hits:
        return "no_table"
    if len(hits) > 1:
        return "multi"
    t = hits[0]
    if t == box_ids:
        return "same_words"
    if box_ids - t:
        return "box_extra"
    return "table_extra"


def _is_glyph(text: str) -> bool:
    return text in GLYPH_DENY or (len(text) == 1 and unicodedata.category(text) in ("So", "Co"))


def glyph_candidates(token_texts: dict[int, str], cells: list[dict[str, Any]]) -> int:
    """Bullet-like tokens in a cell whose other tokens, not counting more bullet-like ones, are all numbers, dashes or
    bound symbols, or that has no other token (a cell of bullets alone). Two bullets before a number count twice. A
    bullet in a cell of words is not counted. Counted only; nothing is changed."""
    n = 0
    for c in cells:
        texts = [token_texts.get(t, "") for t in c.get("tokens", [])]
        glyphs = sum(1 for x in texts if _is_glyph(x))
        if not glyphs:
            continue
        rest = [x for x in texts if not _is_glyph(x)]
        if all(is_number(x) or is_dash(x) or x in LEFT_SYMBOLS | RIGHT_SYMBOLS for x in rest):
            n += glyphs
    return n


def is_synthetic(word: Word) -> bool:
    """A word the layout pass made up from a vector shape: not in the text layer, never a model token. The layout pass
    sets the ``synthetic`` flag on it; the font name is not the test."""
    return word.get("synthetic") is True


Stroke = _Stroke


def rule_crossings(words_by_id: dict[int, Word], cells: list[dict[str, Any]], h_rules: list[Stroke], v_rules: list[Stroke]) -> int:
    """Cells holding words on both sides of a drawn rule that spans them (see ``drawn_rules.crossings``). Reported only."""
    return _crossings(words_by_id, cells, h_rules, v_rules)[2]


def hard_flags(words_by_id: dict[int, Word], cells: list[dict[str, Any]], shape: tuple[int, int]) -> dict[str, bool]:
    """Flag-only checks: ``bij`` every word in exactly one cell, ``mono`` row and column starts follow the cell
    boxes, ``size`` at least 2 x 2, ``gap`` no cell holds an aligned wide gap (a currency sign or bracket binds to
    the word on its right first). True means the check holds."""
    listed = [t for c in cells for t in c.get("tokens", [])]
    bij = sorted(listed) == sorted(words_by_id)
    tops: dict[int, list[float]] = {}
    lefts: dict[int, list[float]] = {}
    for c in cells:
        if c.get("bbox"):
            tops.setdefault(c["r0"], []).append(c["bbox"][1])
            lefts.setdefault(c["c0"], []).append(c["bbox"][0])
    mono = all(
        statistics.median(tops[a]) <= statistics.median(tops[b]) + 1e-6 for a, b in zip(sorted(tops), sorted(tops)[1:], strict=False)
    ) and all(
        statistics.median(lefts[a]) <= statistics.median(lefts[b]) + 1e-6 for a, b in zip(sorted(lefts), sorted(lefts)[1:], strict=False)
    )
    gap_ok = True
    for c in cells:
        ws = sorted((words_by_id[t] for t in c.get("tokens", []) if t in words_by_id), key=lambda w: (round((w["top"] + w["bottom"]) / 2), w["x0"]))
        for a, b in zip(ws, ws[1:], strict=False):
            h = max(a["bottom"] - a["top"], 1.0)
            same_line = abs((a["top"] + a["bottom"]) / 2 - (b["top"] + b["bottom"]) / 2) <= LINE_JOIN_H * h
            if same_line and a["text"] not in LEFT_SYMBOLS and b["text"] not in RIGHT_SYMBOLS and b["x0"] - a["x1"] >= GAP_UNSPLIT_H * h:
                gap_ok = False
    return {"bij": bij, "mono": mono, "size": shape[0] >= 2 and shape[1] >= 2, "gap": gap_ok}
