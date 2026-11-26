"""Records inside one band of a table whose rules draw columns but no lines
between the records.

A ruled grid read from such a page has one row holding every record of the band.
``split_band_records`` is the one rule that tells where a new record starts; any
code that has to cut a band into records calls it.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# Lines are dicts: "w" (the line's words, left to right, each with x0, x1, top,
# bottom and text), "top", "bottom", "cy" (centre height) and "h" (height).
Line = dict[str, Any]

INSET_MAX = 6.0  # cap on the mirrored cell inset, pt
SPACE = 0.3  # share of the line height: the space before a word in the fit test
TOKEN_FILL = 0.85  # a one-word key line this full is a token broken across lines, share of cell width minus 2 pt
FILL = 0.9  # a line fills its cell when it is this wide (cell width minus 2 pt)
CENTRE_TOL = 0.12  # share of the cell width a centred key may be off the cell centre
CENTRE_MIN_INSET = 6.0  # a centred key starts more than this far inside its cell, pt
EDGE_TOL = 3.0  # a segment starts at its column's left text edge, pt
RUN_MIN = 3  # one-word code keys needed for branch (d)

ANNOT = re.compile(r"^\s*(\(|See\b|Note\b|NOTE\b|—)")
CODE = re.compile(r"^[A-Z0-9_≥≤<>+-][A-Z0-9_.:\[\]≥≤<>=+-]*$")


def _cx(w: dict[str, Any]) -> float:
    return (float(w["x0"]) + float(w["x1"])) / 2.0


def band_lines(words: list[dict[str, Any]]) -> list[Line]:
    """Text lines of a band's words (those with text), top to bottom."""
    lines: list[Line] = []
    for w in sorted(words, key=lambda w: ((float(w["top"]) + float(w["bottom"])) / 2.0, float(w["x0"]))):
        cy = (float(w["top"]) + float(w["bottom"])) / 2.0
        if lines and abs(cy - lines[-1]["cy"]) < 0.5 * lines[-1]["h"]:
            lines[-1]["w"].append(w)
        else:
            lines.append({"cy": cy, "h": max(1.0, float(w["bottom"]) - float(w["top"])), "w": [w]})
    for ln in lines:
        ln["w"].sort(key=lambda w: float(w["x0"]))
        ln["top"] = min(float(w["top"]) for w in ln["w"])
        ln["bottom"] = max(float(w["bottom"]) for w in ln["w"])
    return lines


def column_for(cx: float, columns: list[tuple[float, float]]) -> int | None:
    """The column that holds a word centred at ``cx``: the one whose half-open range contains it
    (the last column also holds its right edge), else the nearest one, the first on a tie. This
    is the rule the rendered cells use, and the only one: the band analysis and the cell text
    must agree on which cell a word is in."""
    if not columns:
        return None
    for j, (x0, x1) in enumerate(columns):
        if x0 <= cx < x1 or (j == len(columns) - 1 and cx == x1):
            return j
    return min(range(len(columns)), key=lambda j: (min(abs(cx - columns[j][0]), abs(cx - columns[j][1])), j))


def split_band_records(
    lines: list[Line], columns: list[tuple[float, float]], *, ruled_cell: bool,
    column_of_word: Callable[[dict[str, Any]], int | None] | None = None,
) -> list[list[Line]]:
    """Records inside one band.

    ``lines``: the band's text lines, top to bottom. ``columns``: the column
    x-ranges, key column first. ``ruled_cell``: the columns are ruled cells (the
    text area is the cell minus the left inset, mirrored on the right).
    ``column_of_word``: the column index of a word when the caller already knows it (the cell the
    word was rendered into); by default ``column_for`` decides, with the same rule.
    Returns the lines grouped into records, top to bottom. One group means "no
    clear record start": keep the band as one row.

    Definitions. Lines are grouped band-wide (``band_lines``: words whose
    centres are within half a line height are one line); a word belongs to the
    column holding its centre, give or take 0.5 pt, so an overflowing key is
    assigned by its centre. ``fits``: the column's line would have fitted after
    that column's line above it (``x1 + SPACE * h + first word <= right edge``).
    Lower-case wrap: the text starts lower-case, does not fit, and the line above
    it in that column does not end with a full stop. Prose
    continuation: a line of three or more words that does not fit after a line
    that does not end a sentence (``. ; : )``).

    Rules, in order. (1) The key column must have text on the first line, or the
    band is one record. (2) A later line starts a record when its key text is not
    empty, is not an annotation (opening with an opening parenthesis, See, Note
    or a dash), and any of:
      (a) it fits after the key line above, another column has text on it, and no
          other column has a lower-case wrap on it;
      (b) the key is centred in its cell (within ``CENTRE_TOL`` of the centre,
          more than ``CENTRE_MIN_INSET`` inside), does not open lower-case, the
          key line above does not fill its cell, two or more other columns start
          at their left text edge on it (each with a line above that does not
          fill its cell, unless that ends a sentence), one of them with two or
          more words, and no other column continues prose;
      (c) its key line is at least one blank line below the key line above (the
          bottom of the one to the top of this one is at least one line height,
          not one line pitch), another column has text on it, it does not open
          lower-case, and no other column has a lower-case wrap on it;
      (d) the band's key lines are ``RUN_MIN`` or more one-word code tokens (or two,
          when the second is a whole code: the first does not fill the key cell; a code may carry one lower-case
          footnote letter after a digit),
          some other column has at least as many lines, another column starts
          at its left text edge on it (after a line that ends a sentence or does
          not fill its cell), and no other column has a prose continuation on it;
    A key line also continues the one above when that ends with a comma (an operand
    list) or when both are one-word tokens that fill their cells in the key column
    and in at least two other columns (identifiers broken mid-token); it then never
    starts a record.
    (3) Consistency (key column is a list of code aliases): when the key lines are
    code tokens (branch (d)'s run), if a key line after the first is a non-wrap (it fits, or is
    a complete code token under a code key line that does not fill its cell), is
    not annotated, does not start a record, and no other column starts a segment
    at its left text edge with an upper-case letter or a digit on it, and the key
    lines are stacked (each within one line height of the one above), the key
    column is a list inside one record and the whole band is one record.
    (4) Lines that start no record join the record above.

    The function only groups lines; what a caller does with text that exists in
    the first record only is outside this contract."""
    if not lines or not columns:
        return [lines]

    col_index = {tuple(c): j for j, c in enumerate(columns)}

    def in_column(w: dict[str, Any], j: int) -> bool:
        if column_of_word is not None:
            return column_of_word(w) == j
        return column_for(_cx(w), columns) == j

    def seg(ln: Line, x0: float, x1: float) -> tuple[float, float, float, str, bool, bool] | None:
        j = col_index[(x0, x1)]
        ws = [w for w in ln["w"] if in_column(w, j)]
        if not ws:
            return None
        last = str(max(ws, key=lambda w: float(w["x1"]))["text"]).rstrip()
        return (
            float(ws[0]["x0"]), max(float(w["x1"]) for w in ws),
            float(ws[0]["x1"]) - float(ws[0]["x0"]), str(ws[0]["text"]),
            last.endswith((".", ";", ":", ")")), last.endswith("."),
        )

    def n_words(ln: Line, j: int) -> int:
        return sum(1 for w in ln["w"] if in_column(w, j))

    rights: list[float] = []
    lefts: list[float | None] = []
    for x0, x1 in columns:
        starts = [sg[0] for sg in (seg(ln, x0, x1) for ln in lines) if sg]
        inset = max(0.0, min(INSET_MAX, min(starts) - x0)) if (ruled_cell and starts) else 1.0
        rights.append(x1 - inset)
        lefts.append(min(starts) if starts else None)
    if seg(lines[0], *columns[0]) is None:
        return [lines]

    key_w = columns[0][1] - columns[0][0]

    def broken_tokens(prev: list[Any]) -> int:
        """Other columns whose line above is one word filling its cell: tokens broken across lines."""
        return sum(
            1 for j in range(1, len(columns))
            if prev[j] is not None and abs(prev[j][2] - (prev[j][1] - prev[j][0])) < 1.0
            and prev[j][1] - prev[j][0] >= TOKEN_FILL * (columns[j][1] - columns[j][0] - 2)
        )
    key_lines = [ln for ln in lines if seg(ln, *columns[0]) is not None]

    def key_text(ln: Line) -> str:
        return " ".join(str(w["text"]) for w in ln["w"] if in_column(w, 0)).strip()

    def code(ln: Line) -> bool:
        t = key_text(ln)
        # a lower-case footnote letter glued to a digit ("2a") does not make a token plain text
        t = t[:-1] if len(t) > 1 and t[-1].islower() and t[-2].isdigit() else t
        return bool(CODE.match(t)) and any(ch.isdigit() or ch == "_" for ch in t)

    def whole(a: Line, b: Line) -> bool:
        """``b`` is a complete code after ``a``: ``a`` does not fill the key cell, so ``b`` is not the
        tail of an identifier broken at the cell edge (a tail opening with an underscore is a
        continuation line anyway and never starts a record)."""
        sa = seg(a, *columns[0])
        return sa is not None and (sa[1] - sa[0]) < FILL * (key_w - 2)

    def key_box(ln: Line) -> tuple[float, float]:
        """Top and bottom of the key words of ``ln`` (a tall word in another column must not
        move them)."""
        ws = [w for w in ln["w"] if in_column(w, 0)]
        return min(float(w["top"]) for w in ws), max(float(w["bottom"]) for w in ws)

    runs: set[int] = set()
    two_codes = len(key_lines) == 2 and whole(key_lines[0], key_lines[1])
    if (len(key_lines) >= RUN_MIN or two_codes) and all(code(ln) for ln in key_lines):  # CODE has no space
        other_max = max((sum(1 for ln in lines if seg(ln, *c)) for c in columns[1:]), default=0)
        if other_max >= len(key_lines):
            runs = {id(ln) for ln in key_lines[1:]}

    prev: list[tuple[float, float, float, str, bool, bool] | None] = [None] * len(columns)
    groups: list[list[Line]] = [[]]
    prev_key_bottom: float | None = None
    orphan_key = False
    prev_key_text = ""
    prev_key_width = 0.0
    for ln in lines:
        segs = [seg(ln, *c) for c in columns]
        k = segs[0]
        prev_key = prev[0]
        start = False
        fits = False
        annotated = False
        fresh_upper = False
        continues = False
        if k is not None and prev_key is not None and groups[-1]:
            others = [(j, sj) for j, sj in enumerate(segs[1:], 1) if sj is not None]

            def wraps(j: int, sj: tuple[float, float, float, str, bool, bool], ln: Line = ln) -> bool:
                return prev[j] is not None and prev[j][1] + SPACE * ln["h"] + sj[2] > rights[j]

            def filled(j: int) -> bool:
                return (prev[j] is not None and not prev[j][4]
                        and (prev[j][1] - prev[j][0]) >= FILL * (columns[j][1] - columns[j][0] - 2))

            prose_wrap = any(
                n_words(ln, j) >= 3 and prev[j] is not None and not prev[j][4] and wraps(j, sj) for j, sj in others
            )
            prose_cont = prose_wrap or any(sj[3][:1].islower() for j, sj in others)
            lower_cont = any(sj[3][:1].islower() and not prev[j][5] and wraps(j, sj) for j, sj in others if prev[j] is not None)
            fits = prev_key[1] + SPACE * ln["h"] + k[2] <= rights[0]
            fresh = [
                j for j, sj in others
                if lefts[j] is not None and abs(sj[0] - lefts[j]) <= EDGE_TOL and not filled(j)
            ]
            multiword = any(n_words(ln, j) >= 2 for j in fresh)
            key_filled = (prev_key[1] - prev_key[0]) >= FILL * (key_w - 2)
            centred = (abs((k[0] + k[1]) / 2 - (columns[0][0] + columns[0][1]) / 2) <= CENTRE_TOL * key_w
                       and k[0] - columns[0][0] > CENTRE_MIN_INSET)
            a_ = fits and bool(others) and not lower_cont
            b_ = (not k[3][:1].islower() and not key_filled and len(fresh) >= 2 and multiword and centred
                  and not prose_cont)
            key_top, key_bottom = key_box(ln)
            key_gap = key_top - prev_key_bottom if prev_key_bottom is not None else 0.0
            blank_above = key_gap >= max(1.0, key_bottom - key_top)
            c_ = (bool(others) and blank_above
                  and not k[3][:1].islower() and not lower_cont)
            d_ = id(ln) in runs and bool(others) and len(fresh) >= 1 and not lower_cont and not prose_wrap
            start = a_ or b_ or c_ or d_
            annotated = bool(ANNOT.match(key_text(ln)))
            if annotated:
                start = False
            # A key line that continues the one above is never a record: the typesetter broke
            # the line after a comma (an operand list), or in the middle of a token (the line
            # above fills its cell and has no separator at its end, or this one opens with "_"
            # right below it; after a blank line it is a key of its own).
            if prev_key_text.endswith(",") or (key_text(ln).startswith("_") and not blank_above) or (
                prev_key_text and " " not in prev_key_text and prev_key_width >= TOKEN_FILL * (key_w - 2)
                and prev_key_text[-1].isalnum() and broken_tokens(prev) >= 2
            ):
                start = False
                continues = True
            # fresh_upper: another column starts at its own left text edge with a capital or a digit
            fresh_upper = any(
                lefts[j] is not None and abs(sj[0] - lefts[j]) <= EDGE_TOL and not sj[3][:1].islower()
                for j, sj in others
            )
        complete_code = False
        if k is not None and prev_key is not None:
            kt = key_text(ln)
            complete_code = (
                len(kt.split()) == 1 and bool(CODE.match(kt)) and any(ch.isdigit() or ch == "_" for ch in kt)
                and not kt.startswith("_") and bool(CODE.match(prev_key[3]))
                and (prev_key[1] - prev_key[0]) < FILL * (key_w - 2)
            )
        if (k is not None and prev_key is not None and groups[-1] and not start
                and (fits or complete_code) and not annotated and not fresh_upper and not continues):
            orphan_key = True
        if start:
            groups.append([ln])
            # a new record: what the columns held above belongs to the record before
            prev[:] = [None] * len(columns)
        else:
            groups[-1].append(ln)
        for j, sj in enumerate(segs):
            if sj is not None:
                prev[j] = sj
        if k is not None:
            prev_key_bottom = key_box(ln)[1]
            prev_key_text = key_text(ln)
            prev_key_width = k[1] - k[0]
    # A key line that is not a wrap could not start a record. When the key lines are
    # stacked (each within one line height of the one above) the key column is a list
    # inside one record: aliases one per line beside a shared description.
    if (orphan_key and runs and len(groups) > 1
            and all(key_box(b)[0] - key_box(a)[1] < max(1.0, key_box(b)[1] - key_box(b)[0])
                    for a, b in zip(key_lines, key_lines[1:]))):
        return [lines]
    return [g for g in groups if g]
