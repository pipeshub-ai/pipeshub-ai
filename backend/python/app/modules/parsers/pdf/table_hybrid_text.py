"""Hybrid table extraction, emit side, pure part: cell text from our own words and the grid the model's structure implies.

No I/O, no model, no pdfplumber. A model grid is a list of cells (``r0 r1 c0 c1`` with exclusive ends, and the ids of
the request's words in each cell). The text of a cell is built here from the text layer's words, never taken from the
model, in geometric order (lines top to bottom, each left to right) with raised footnote marks last in their line.
Nothing is deleted except a trailing dot leader of four or more dots and a swallowed ``Table <id>`` caption row.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

Word = dict[str, Any]

LEADER_MIN_DOTS = 4  # three dots stay: they are a real ellipsis
_LEADER_RE = re.compile(r"(?:\s*\.){%d,}\s*$" % LEADER_MIN_DOTS)
# "Table 1-13.", "Table B2", "Table T.N.4.6.": a caption row opens with the word and an identifier that has a digit.
CAPTION_RE = re.compile(r"^\s*Table\s+(?:[A-Z]{1,3}[.\-]){0,3}[A-Z]?\d")
MARK_RE = re.compile(r"^\(?\d{1,2}\)?$|^[*†‡§#]{1,3}$")
SMALL_GLYPH = 0.75  # a mark's size is at most this share of the cell's text
RAISE = 0.12  # and its baseline is higher than the line's by this share of the text size
LINE_JOIN_H = 0.5  # words of one line: centres within this many median heights
RAISED_MARKS_LAST = False  # off: against geometric order alone, putting raised marks last lost tables on labelled pages and won none
BBOX_PAD_PT = 0.5  # the table's top edge moves to the dropped caption row's bottom plus this


def has_content(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def strip_leader(text: str) -> str:
    """Remove a trailing run of four or more dots (spaces between them allowed); a cell of leaders alone becomes empty."""
    return _LEADER_RE.sub("", text)


def collapse_space(text: str) -> str:
    return " ".join(text.split())


def _size(w: Word) -> float:
    s = w.get("size")
    return float(s) if s else max(0.1, float(w["bottom"]) - float(w["top"]))


def _cy(w: Word) -> float:
    return (float(w["top"]) + float(w["bottom"])) / 2


def raised_marks(words: list[Word]) -> set[int]:
    """Indices of the words that are raised footnote marks: a mark pattern, smaller than the cell's other text and
    standing above its baseline. A cell with no other text has none."""
    cand = [i for i, w in enumerate(words) if MARK_RE.match(str(w["text"]).strip())]
    rest = [w for i, w in enumerate(words) if i not in cand]
    if not cand or not rest:
        return set()
    ref_size = statistics.median(_size(w) for w in rest)
    out: set[int] = set()
    for i in cand:
        w = words[i]
        near = min(rest, key=lambda r: abs(_cy(r) - _cy(w)))
        line = [r for r in rest if abs(_cy(r) - _cy(near)) <= LINE_JOIN_H * ref_size]
        base_bottom = statistics.median(float(r["bottom"]) for r in line)
        if _size(w) <= SMALL_GLYPH * ref_size and float(w["bottom"]) <= base_bottom - RAISE * ref_size:
            out.add(i)
    return out


def join_words(words: list[Word]) -> str:
    """The cell's words as one text. Upright text: geometric order (with ``RAISED_MARKS_LAST``, raised marks last in their line). A cell with
    non-upright words keeps the order of the word ids (no rotated rebuild)."""
    if not words:
        return ""
    if any(not w.get("upright", True) for w in words):
        return " ".join(str(w["text"]) for w in words)
    marks = raised_marks(words)  # always found: a raised mark belongs to the line it stands on, whatever the order
    body = [w for i, w in enumerate(words) if i not in marks]
    if not body:
        return " ".join(str(w["text"]) for w in sorted(words, key=lambda w: (_cy(w), float(w["x0"]))))
    h = statistics.median(max(0.1, float(w["bottom"]) - float(w["top"])) for w in body)
    lines: list[list[Word]] = []
    for w in sorted(body, key=lambda w: (_cy(w), float(w["x0"]))):
        if lines and abs(_cy(w) - statistics.mean(_cy(x) for x in lines[-1])) <= LINE_JOIN_H * h:
            lines[-1].append(w)
        else:
            lines.append([w])
    raised: list[list[Word]] = [[] for _ in lines]
    for i in sorted(marks):
        m = words[i]
        k = min(range(len(lines)), key=lambda j: abs(_cy(m) - statistics.mean(_cy(x) for x in lines[j])))
        raised[k].append(m)
    out: list[str] = []
    for ln, rs in zip(lines, raised, strict=True):
        if RAISED_MARKS_LAST:
            out.extend(str(w["text"]) for w in sorted(ln, key=lambda w: float(w["x0"])))
            out.extend(str(w["text"]) for w in sorted(rs, key=lambda w: float(w["x0"])))
        else:
            out.extend(str(w["text"]) for w in sorted(ln + rs, key=lambda w: float(w["x0"])))
    return " ".join(out)


def cell_text(words: list[Word]) -> str:
    """Join, then the text rules in their order: dot leader, whitespace."""
    return collapse_space(strip_leader(join_words(words)))


@dataclass
class Emitted:
    grid: list[list[str]]
    bbox_top: float | None  # new top edge after a dropped caption row, else None
    emitted_ids: set[int]  # ids of the words a cell of the final grid holds
    dropped_ids: set[int]  # ids of the words of a dropped caption row
    overlaps: int
    leaders_stripped: int
    caption_row_dropped: bool
    empty_rows_cols_dropped: int
    n_rows_raw: int
    n_cols_raw: int
    shape: tuple[int, int] = (0, 0)
    spans: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def build_grid(cells: list[dict[str, Any]], n_rows: int, n_cols: int, words: list[Word]) -> Emitted:
    """The dense grid of a validated model grid. A cell's text sits in its top-left position, covered positions are
    ``""``. Overlapping cells: the last one owns the position; a cell whose top-left position is taken by a later cell
    loses its text, and its words then count as not emitted (the caller's word-loss rule sees them)."""
    owner: dict[tuple[int, int], int] = {}
    overlaps = 0
    for k, c in enumerate(cells):
        for r in range(c["r0"], c["r1"]):
            for q in range(c["c0"], c["c1"]):
                if (r, q) in owner:
                    overlaps += 1
                owner[(r, q)] = k
    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    emitted_ids: set[int] = set()
    leaders = 0
    spans = 0
    origin_of: dict[tuple[int, int], int] = {}
    for k, c in enumerate(cells):
        if (c["r1"] - c["r0"]) * (c["c1"] - c["c0"]) > 1:
            spans += 1
        if owner.get((c["r0"], c["c0"])) != k:
            continue
        ws = [words[t] for t in c.get("tokens", [])]
        raw = join_words(ws)
        text = collapse_space(strip_leader(raw))
        if text != collapse_space(raw):
            leaders += 1
        grid[c["r0"]][c["c0"]] = text
        origin_of[(c["r0"], c["c0"])] = k
        emitted_ids.update(c.get("tokens", []))

    dropped_ids: set[int] = set()
    caption = False
    top: float | None = None
    row0 = [(q, t) for q, t in enumerate(grid[0]) if t] if grid else []
    if len(row0) == 1 and CAPTION_RE.match(row0[0][1]):
        k = origin_of[(0, row0[0][0])]
        c = cells[k]
        if c["r1"] - c["r0"] == 1:
            ids = set(c.get("tokens", []))
            bottoms = [float(words[t]["bottom"]) for t in ids]
            if bottoms:
                caption, top = True, max(bottoms) + BBOX_PAD_PT
                dropped_ids = ids
                emitted_ids -= ids
                grid = grid[1:]
    raw_rows, raw_cols = len(grid), n_cols
    grid = [row for row in grid if any(row)]
    keep = [q for q in range(n_cols) if any(row[q] for row in grid)]
    grid = [[row[q] for q in keep] for row in grid]
    dropped = (raw_rows - len(grid)) + (raw_cols - len(keep))
    return Emitted(
        grid=grid, bbox_top=top, emitted_ids=emitted_ids, dropped_ids=dropped_ids, overlaps=overlaps, leaders_stripped=leaders,
        caption_row_dropped=caption, empty_rows_cols_dropped=dropped, n_rows_raw=n_rows, n_cols_raw=n_cols,
        shape=(len(grid), len(keep)), spans=spans,
    )


def split_rows_at_rules(
    cells: list[dict[str, Any]], n_rows: int, words: list[Word], h_rules: list[tuple[float, float, float]],
) -> tuple[list[dict[str, Any]], int, int]:
    """Repair a model grid whose cell holds words on both sides of a horizontal drawn rule: the row is cut at the rule.

    A row that holds such a single-row cell becomes one row more per crossing rule; each cell of that row is split by its
    words' position against the rules (an empty part makes no cell), a cell that spans several rows keeps its words and
    stretches over the new rows. Returns the new cells, the new row count and the number of cuts. Token ids are untouched,
    so every word is still in exactly one cell."""
    from app.modules.parsers.pdf.drawn_rules import h_crossing_ys

    by_id = dict(enumerate(words))
    cuts: dict[int, list[float]] = {}
    for c in cells:
        if c["r1"] - c["r0"] != 1:
            continue
        ys = h_crossing_ys([by_id[t] for t in c.get("tokens", []) if t in by_id], h_rules)
        if ys:
            cuts.setdefault(c["r0"], [])
            cuts[c["r0"]] = sorted({*cuts[c["r0"]], *ys})
    if not cuts:
        return cells, n_rows, 0
    base: list[int] = []
    size: list[int] = []
    at = 0
    for r in range(n_rows):
        k = len(cuts.get(r, [])) + 1
        base.append(at)
        size.append(k)
        at += k
    out: list[dict[str, Any]] = []
    for c in cells:
        r0, r1 = c["r0"], c["r1"]
        if r1 - r0 == 1 and r0 in cuts:
            ys = cuts[r0]
            bands: dict[int, list[int]] = {}
            for t in c.get("tokens", []):
                w = by_id[t]
                cy = (w["top"] + w["bottom"]) / 2
                bands.setdefault(sum(cy > y for y in ys), []).append(t)
            for band, toks in sorted(bands.items()):
                out.append({**c, "r0": base[r0] + band, "r1": base[r0] + band + 1, "tokens": toks, "bbox": None})
            if not bands:
                out.append({**c, "r0": base[r0], "r1": base[r0] + 1})
        else:
            out.append({**c, "r0": base[r0], "r1": base[r1 - 1] + size[r1 - 1]})
    return out, at, sum(len(v) for v in cuts.values())


# A word of these alone still changes a value when it is lost (a minus or a bracket before an amount, a percent sign).
_VALUE_SIGNS = frozenset({"-", "+", "\u2212", "\u2013", "(", ")", "%"})


def unassigned_content(words: list[Word], emitted_ids: set[int], dropped_ids: set[int]) -> list[int]:
    """Ids of content words (a letter or a digit, or a sign that changes a value) that no cell of the final grid holds and
    that no stated rule removed."""
    return [
        i for i, w in enumerate(words)
        if i not in emitted_ids and i not in dropped_ids
        and (has_content(str(w["text"])) or str(w["text"]).strip() in _VALUE_SIGNS)
    ]


def dollar_cross_candidates(grid: list[list[str]]) -> int:
    """Counted only: a cell whose last token is a left symbol (``$``, ``(``) and whose right neighbour starts with a number."""
    n = 0
    for row in grid:
        for a, b in zip(row, row[1:], strict=False):
            toks = a.split()
            if toks and toks[-1] in ("$", "(", "€", "£", "¥") and b[:1].isdigit():
                n += 1
    return n
