"""A ruled table keeps its ruled grid in the PdfPlumber + OpenCV parser.

Ruled borders drawn as thin filled rectangles sit on the cell edges, so they
stick out past the table's bounding box by half their thickness. The layout
model's table box has the same extent as the ruled table, and re-extracting the
cells inside it must keep the table's own rules and must not fall back to a text
strategy that splits prose at every word gap.

The PDFs are generated here with reportlab; nothing is read from disk.
"""
from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pdfplumber
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import app.modules.parsers.pdf.opencv_layout_analyzer as ola
from app.modules.parsers.pdf.opencv_layout_analyzer import (
    LayoutRegion,
    LayoutRegionType,
)

if TYPE_CHECKING:
    from collections.abc import Callable



def band_lines(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401  # imported inside the call: a missing module fails the test, not the file
    from app.modules.parsers.pdf.table_rows import band_lines as fn

    return fn(*args, **kwargs)


def split_band_records(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    from app.modules.parsers.pdf.table_rows import split_band_records as fn

    return fn(*args, **kwargs)


PAGE_W, PAGE_H = letter
LEADING = 11.0
PAD = 4.0
RULE_COLOR = (0.68, 0.85, 0.90)

NAMES = ["tablename", "overwrite", "patterns", "retention", "sorting", "compression"]
PROSE = [
    "Name of the table that the rule applies to. Leave it empty to match every table.",
    "Whether an existing file is replaced. When it is off the run stops with an error and nothing is written.",
    "Comma separated glob patterns that select input files. A pattern without a slash matches the base name "
    "only. A pattern with a slash is matched against the path relative to the input folder. Patterns are "
    "tried in order and the first match wins. An empty list selects nothing at all.",
    "How many days finished runs are kept before they are removed from the store.",
    "Sort order of the output rows, either ascending or descending by the primary key.",
    "Codec used for the output. Larger values trade time for size and are rarely worth it.",
]


def _wrap(text: str, chars: int = 44) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > chars:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return [*lines, cur]


class Fixture:
    """One drawn table: its true bbox (top-left origin) and expected cell text."""

    def __init__(self, bbox: tuple[float, float, float, float], expected: list[list[str]]) -> None:
        self.bbox = bbox
        self.expected = expected


def _top(y_top: float) -> float:
    return PAGE_H - y_top


def _rule_h(c: canvas.Canvas, x0: float, x1: float, y_top: float, style: str, w: float) -> None:
    if style == "rect":
        c.rect(x0 - w / 2, _top(y_top) - w / 2, (x1 - x0) + w, w, stroke=0, fill=1)
    else:
        c.setLineWidth(w)
        c.line(x0, _top(y_top), x1, _top(y_top))


def _rule_v(c: canvas.Canvas, x_pos: float, y0_top: float, y1_top: float, style: str, w: float) -> None:
    if style == "rect":
        c.rect(x_pos - w / 2, _top(y1_top) - w / 2, w, (y1_top - y0_top) + w, stroke=0, fill=1)
    else:
        c.setLineWidth(w)
        c.line(x_pos, _top(y0_top), x_pos, _top(y1_top))


def draw_ruled_table(
    c: canvas.Canvas,
    x0: float,
    y_top: float,
    col_w: tuple[float, float] = (90.0, 360.0),
    names: list[str] | None = None,
    prose: list[str] | None = None,
    style: str = "rect",
    rule_w: float = 0.45,
    wrap_chars: int = 44,
) -> Fixture:
    """A 2-column ruled table. Each cell gets its own four rules, on the cell edges."""
    names = names or NAMES
    prose = prose or PROSE
    c.setFillColorRGB(*RULE_COLOR)
    c.setStrokeColorRGB(*RULE_COLOR)
    xs = [x0, x0 + col_w[0], x0 + col_w[0] + col_w[1]]
    y = y_top
    expected: list[list[str]] = []
    cells: list[tuple[float, float, float, float, list[str]]] = []
    for name, text in zip(names, prose):
        lines = _wrap(text, wrap_chars)
        h = max(len(lines), 1) * LEADING + 2 * PAD
        cells.append((xs[0], y, xs[1], y + h, [name]))
        cells.append((xs[1], y, xs[2], y + h, lines))
        expected.append([name, " ".join(lines)])
        y += h
    for cx0, cy0, cx1, cy1, _ in cells:
        _rule_h(c, cx0, cx1, cy0, style, rule_w)
        _rule_h(c, cx0, cx1, cy1, style, rule_w)
        _rule_v(c, cx0, cy0, cy1, style, rule_w)
        _rule_v(c, cx1, cy0, cy1, style, rule_w)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 8)
    for cx0, cy0, _, _, lines in cells:
        for i, line in enumerate(lines):
            c.drawString(cx0 + PAD, _top(cy0 + PAD + LEADING * (i + 1) - 2.5), line)
    return Fixture((xs[0], y_top, xs[2], y), expected)


def make_page(draw: Callable[[canvas.Canvas], list[Fixture]]) -> tuple[pdfplumber.PDF, pdfplumber.page.Page, list[Fixture]]:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    fixtures = draw(c)
    c.showPage()
    c.save()
    pdf = pdfplumber.open(BytesIO(buf.getvalue()))
    return pdf, pdf.pages[0], fixtures


def model_box(bbox: tuple[float, float, float, float], inset: float = 0.0) -> tuple[tuple[float, float, float, float], float]:
    x0, y0, x1, y1 = bbox
    return (
        ((x0 + inset) / PAGE_W, (y0 + inset) / PAGE_H, (x1 - inset) / PAGE_W, (y1 - inset) / PAGE_H),
        0.98,
    )


def tables_with_model(page, boxes: list) -> list:
    """The two steps extract_layout_regions runs: rule tables, then the model's boxes."""
    words = page.extract_words()
    rule = ola._detect_tables(page, PAGE_W, PAGE_H, words, stripe_frames=[])
    return [r for r in ola._prefer_model_tables(page, PAGE_W, PAGE_H, rule, words, boxes)
            if r.type == LayoutRegionType.TABLE]


def norm(grid) -> list[list[str]]:
    return [[" ".join((cell or "").split()) for cell in row] for row in grid]


def shape(grid) -> tuple[int, int]:
    return len(grid), max(len(r) for r in grid)


def one_table(draw_kwargs: dict, inset: float | None) -> tuple[list, Fixture]:
    """inset None: no model box. Otherwise the model box is the true bbox shrunk by inset
    (negative: grown)."""
    pdf, page, fx = make_page(lambda c: [draw_ruled_table(c, 36, 240, **draw_kwargs)])
    try:
        boxes = [] if inset is None else [model_box(fx[0].bbox, inset)]
        return tables_with_model(page, boxes), fx[0]
    finally:
        pdf.close()


VARIANTS = {
    "repro_filled_rects": {"style": "rect", "rule_w": 0.45},
    "thick_filled_rects": {"style": "rect", "rule_w": 1.5},
    "stroked_lines": {"style": "stroke", "rule_w": 0.45},
    "thick_stroked_lines": {"style": "stroke", "rule_w": 1.5},
}


@pytest.mark.parametrize("name", list(VARIANTS))
@pytest.mark.parametrize("inset", [0.0, 1.0, -1.0], ids=["box_exact", "box_1pt_inside", "box_1pt_outside"])
def test_model_box_over_a_ruled_table_keeps_the_ruled_grid(name: str, inset: float) -> None:
    tables, fx = one_table(VARIANTS[name], inset)
    assert len(tables) == 1
    grid = tables[0].table_grid
    assert shape(grid) == (6, 2)
    assert norm(grid) == fx.expected


def test_ruled_table_without_a_model_box_is_unchanged() -> None:
    tables, fx = one_table(VARIANTS["repro_filled_rects"], None)
    assert len(tables) == 1 and norm(tables[0].table_grid) == fx.expected


def test_reextract_keeps_the_ruled_grid_for_the_exact_box() -> None:
    pdf, page, fx = make_page(lambda c: [draw_ruled_table(c, 36, 240)])
    try:
        grid = ola._reextract_grid_for_bbox(page, fx[0].bbox, PAGE_W, PAGE_H)
        assert shape(grid) == (6, 2)
        assert norm(grid) == fx[0].expected
    finally:
        pdf.close()


def test_agreeing_model_box_keeps_the_rule_table_object() -> None:
    pdf, page, fx = make_page(lambda c: [draw_ruled_table(c, 36, 240)])
    try:
        words = page.extract_words()
        rule = ola._detect_tables(page, PAGE_W, PAGE_H, words, stripe_frames=[])
        assert len(rule) == 1 and shape(rule[0].table_grid) == (6, 2)
        with patch.object(ola, "_reextract_grid_and_bbox") as reextract:
            out = ola._prefer_model_tables(page, PAGE_W, PAGE_H, rule, words, [model_box(fx[0].bbox, 1.0)])
        reextract.assert_not_called()
        assert len(out) == 1 and out[0] is rule[0]
    finally:
        pdf.close()


def test_model_box_that_disagrees_with_the_rule_table_still_wins() -> None:
    """Extents differ (the rule table is only the top rows), so the model box decides."""
    pdf, page, fx = make_page(lambda c: [draw_ruled_table(c, 36, 240)])
    try:
        words = page.extract_words()
        x0, y0, x1, y1 = fx[0].bbox
        part = ola.LayoutRegion(
            type=LayoutRegionType.TABLE, bbox=(x0, y0, x1, y0 + 80), text="", table_grid=[["a", "b"], ["c", "d"]],
        )
        out = ola._prefer_model_tables(page, PAGE_W, PAGE_H, [part], words, [model_box(fx[0].bbox)])
        assert part not in out
        assert len(out) == 1 and shape(out[0].table_grid) == (6, 2)
    finally:
        pdf.close()


def _two_tables(layout: str) -> Callable[[canvas.Canvas], list[Fixture]]:
    def draw(c: canvas.Canvas) -> list[Fixture]:
        small = {"col_w": (70.0, 150.0), "wrap_chars": 26}
        if layout == "side_by_side":
            a = draw_ruled_table(c, 36, 240, names=NAMES[:3], prose=PROSE[:3], **small)
            b = draw_ruled_table(c, a.bbox[2] + 3.0, 240, names=["alpha", "beta", "gamma"],
                                 prose=PROSE[3:6], **small)
        else:
            a = draw_ruled_table(c, 36, 240, names=NAMES[:3], prose=PROSE[:3])
            b = draw_ruled_table(c, 36, a.bbox[3] + 1.5, names=["alpha", "beta", "gamma"], prose=PROSE[3:6])
        return [a, b]

    return draw


@pytest.mark.parametrize("layout", ["side_by_side", "stacked"])
@pytest.mark.parametrize("inset", [0.0, 1.0, -1.0], ids=["box_exact", "box_1pt_inside", "box_1pt_outside"])
def test_adjacent_ruled_tables_do_not_share_words(layout: str, inset: float) -> None:
    pdf, page, fx = make_page(_two_tables(layout))
    try:
        # A grown model box would swallow the neighbour: grow by 1 pt only on the open sides.
        boxes = [model_box(f.bbox, inset) for f in fx]
        tables = tables_with_model(page, boxes)
        assert len(tables) == 2
        tables.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
        for table, f in zip(tables, fx):
            assert shape(table.table_grid) == (3, 2)
            assert norm(table.table_grid) == f.expected
    finally:
        pdf.close()


def test_unruled_two_column_table_parses_as_before() -> None:
    rows = [(f"Setting{i}", f"Value{i}") for i in range(5)]

    def draw(c: canvas.Canvas) -> list[Fixture]:
        c.setFont("Helvetica", 9)
        for i, (a, b) in enumerate(rows):
            c.drawString(80, _top(300 + 16 * i), a)
            c.drawString(300, _top(300 + 16 * i), b)
        return []

    pdf, page, _ = make_page(draw)
    try:
        box = ((70 / PAGE_W, 288 / PAGE_H, 400 / PAGE_W, 380 / PAGE_H), 0.95)
        tables = tables_with_model(page, [box])
        assert len(tables) == 1
        assert [list(r) for r in tables[0].table_grid] == [list(r) for r in rows]
    finally:
        pdf.close()


def test_ruled_grid_wins_over_a_text_strategy_with_more_columns() -> None:
    """Both strategies keep every word; the ruled 2-column grid must beat the 8-column one."""
    pdf, page, fx = make_page(lambda c: [draw_ruled_table(c, 36, 240)])
    try:
        grid = ola._reextract_grid_for_bbox(page, fx[0].bbox, PAGE_W, PAGE_H)
        assert shape(grid) == (6, 2)
    finally:
        pdf.close()


def test_text_strategies_remain_the_fallback_without_rules() -> None:
    def draw(c: canvas.Canvas) -> list[Fixture]:
        c.setFont("Helvetica", 9)
        for i in range(4):
            c.drawString(80, _top(300 + 16 * i), f"Name{i}")
            c.drawString(200, _top(300 + 16 * i), f"{i}0")
            c.drawString(320, _top(300 + 16 * i), f"{i}5")
        return []

    pdf, page, _ = make_page(draw)
    try:
        grid = ola._reextract_grid_for_bbox(page, (70, 288, 400, 360), PAGE_W, PAGE_H)
        assert grid is not None and shape(grid) == (4, 3)
    finally:
        pdf.close()


def test_reextract_cell_text_never_comes_from_outside_the_box() -> None:
    """Words in the tolerance band around the box, or beyond it, are not cell text."""
    def draw(c: canvas.Canvas) -> list[Fixture]:
        f = draw_ruled_table(c, 36, 240, names=NAMES[:3], prose=PROSE[:3])
        c.setFont("Helvetica", 8)
        c.drawString(40, _top(f.bbox[3] + 6), "OUTSIDE-BELOW")
        c.drawString(40, _top(f.bbox[1] - 3), "OUTSIDE-ABOVE")
        return [f]

    pdf, page, fx = make_page(draw)
    try:
        grid = ola._reextract_grid_for_bbox(page, fx[0].bbox, PAGE_W, PAGE_H)
        text = " ".join(c or "" for r in grid for c in r)
        assert "OUTSIDE" not in text
        assert norm(grid) == fx[0].expected
    finally:
        pdf.close()


def test_table_ruled_only_horizontally_is_still_re_extracted_from_the_model_box() -> None:
    """No vertical rules: the columns come from text alignment, so an agreeing rule table
    is not kept as a ruled grid and the model box's re-extraction decides."""
    header = ("Alpha", "Beta items", "Gamma value")
    rows = [
        ("Row one", "The first item of the list", "Shared by the whole group"),
        ("Row two", "Another item in the list here", "Shared by the whole group"),
        ("Row three", "A third item in the list too", "Shared by the whole group"),
        ("Row four", "The fourth item in this list", "Local to the item"),
        ("Row five", "The fifth item in this list", "Local to the item"),
    ]
    xs = (54.0, 200.0, 420.0)
    top0, step = 300.0, 18.0

    def draw(c: canvas.Canvas) -> list[Fixture]:
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(0.4)
        for r in range(len(rows) + 2):
            c.line(54, _top(top0 + step * r - 4), 552, _top(top0 + step * r - 4))
        c.setFont("Helvetica", 8)
        for r, row in enumerate((header, *rows)):
            for x, text in zip(xs, row):
                c.drawString(x, _top(top0 + r * step + 8), text)
        return []

    pdf, page, _ = make_page(draw)
    try:
        words = page.extract_words()
        rule = ola._detect_tables(page, PAGE_W, PAGE_H, words, stripe_frames=[])
        assert len(rule) == 1 and rule[0].table_grid, "the horizontal rules give a rule table"
        bbox = rule[0].bbox
        assert not ola._has_vertical_rules(page, bbox)
        with patch.object(ola, "_reextract_grid_and_bbox", wraps=ola._reextract_grid_and_bbox) as reextract:
            out = ola._prefer_model_tables(page, PAGE_W, PAGE_H, rule, words, [model_box(bbox)])
        reextract.assert_called_once()
        assert [r for r in out if r.type == LayoutRegionType.TABLE]
    finally:
        pdf.close()


def test_small_ruled_block_inside_a_larger_table_does_not_stand_for_the_table() -> None:
    """Only the inner block of the table is ruled. Its grid is not the table's grid,
    even though words outside its cells would be assigned to its nearest cell."""
    def draw(c: canvas.Canvas) -> list[Fixture]:
        c.setFont("Helvetica", 9)
        for i in range(6):
            for x, text in ((60, f"Name{i}"), (180, f"Mid{i}"), (300, f"Val{i}"), (420, f"End{i}")):
                c.drawString(x, _top(300 + 16 * i), text)
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(0.5)
        for x in (170, 290, 410):
            c.line(x, _top(318), x, _top(366))
        for y in (318, 342, 366):
            c.line(170, _top(y), 410, _top(y))
        return []

    pdf, page, _ = make_page(draw)
    try:
        grid = ola._reextract_grid_for_bbox(page, (50, 288, 480, 388), PAGE_W, PAGE_H)
        assert grid is not None and len(grid) >= 6
    finally:
        pdf.close()


# --- rows without rules between records, boxes that cut or overshoot a table -------------

REC_HEADER = ["Name", "State", "Meaning"]
REC_ROWS = [["ALPHA", "A=1", "First"], ["BRAVO", "A=0", "Second one"], ["CHARLIE", "B=1", "Third"],
            ["DELTA", "B=0", "Fourth one"], ["ECHO", "C=1", "Fifth"]]
REC_XS = [60.0, 220.0, 380.0, 540.0]
REC_TOP, REC_STEP = 300.0, 16.0


def draw_record_table(c: canvas.Canvas, top: float = REC_TOP, rows: list[list[str]] | None = None) -> Fixture:
    """Frame, column rules and a header rule; no rules between the body rows."""
    rows = rows or REC_ROWS
    bottom = top + REC_STEP * (len(rows) + 1) + 4
    c.setLineWidth(0.6)
    for x in REC_XS:
        c.line(x, _top(top), x, _top(bottom))
    for y in (top, top + REC_STEP + 2, bottom):
        c.line(REC_XS[0], _top(y), REC_XS[-1], _top(y))
    c.setFont("Helvetica", 9)
    for r, row in enumerate([REC_HEADER, *rows]):
        for x, text in zip(REC_XS, row):
            c.drawString(x + 4, _top(top + 12 + r * REC_STEP), text)
    return Fixture((REC_XS[0], top, REC_XS[-1], bottom), [REC_HEADER, *rows])


@pytest.mark.parametrize("inset", [0.0, 1.0, -2.0], ids=["box_exact", "box_1pt_inside", "box_2pt_outside"])
def test_rows_without_rules_keep_one_grid_row_per_line(inset: float) -> None:
    pdf, page, fx = make_page(lambda c: [draw_record_table(c)])
    try:
        tables = tables_with_model(page, [model_box(fx[0].bbox, inset)])
        assert len(tables) == 1
        assert norm(tables[0].table_grid) == fx[0].expected
    finally:
        pdf.close()


def test_agreed_rule_table_with_record_lines_is_not_kept_as_one_row() -> None:
    """The rule table pdfplumber finds has a header row and one body row holding every record."""
    pdf, page, fx = make_page(lambda c: [draw_record_table(c)])
    try:
        words = page.extract_words()
        rule = ola._detect_tables(page, PAGE_W, PAGE_H, words, stripe_frames=[])
        assert len(rule) == 1 and len(rule[0].table_grid) == 2
        assert ola._may_hold_record_lines(rule[0].table_grid)
        with patch.object(ola, "_reextract_grid_and_bbox", wraps=ola._reextract_grid_and_bbox) as reextract:
            ola._prefer_model_tables(page, PAGE_W, PAGE_H, rule, words, [model_box(fx[0].bbox, 1.0)])
        reextract.assert_called_once()
    finally:
        pdf.close()


def test_wrapped_cells_of_different_height_stay_one_row() -> None:
    """A one-line cell next to a five-line cell of wrapped prose is one record."""
    tables, fx = one_table(VARIANTS["repro_filled_rects"], 1.0)
    assert shape(tables[0].table_grid) == (6, 2)
    assert norm(tables[0].table_grid) == fx.expected


def draw_band_table(
    c: canvas.Canvas,
    records: list[list[list[str]]],
    widths: tuple[float, ...] = (130.0, 150.0, 150.0),
    top: float = REC_TOP,
    row_rules: bool = False,
    spanning_key: bool = False,
) -> Fixture:
    """A header row and one body band. ``records[r][col]`` are the text lines of a record's
    cell; a record is as tall as its tallest cell. No rules between the records unless
    ``row_rules`` (``spanning_key`` leaves the key column's rules out: a column that spans
    the records breaks every row rule into the pieces right of it)."""
    xs = [60.0]
    for w in widths:
        xs.append(xs[-1] + w)
    lead = 11.0
    heights = [max(len(cell) for cell in rec) for rec in records]
    bottom = top + 18 + sum(heights) * lead + 6
    c.setLineWidth(0.6)
    for x in xs:
        c.line(x, _top(top), x, _top(bottom))
    for y in (top, top + 18, bottom):
        c.line(xs[0], _top(y), xs[-1], _top(y))
    if row_rules:
        y = top + 18 + 3
        for h in heights[:-1]:
            y += h * lead
            c.line(xs[1] if spanning_key else xs[0], _top(y), xs[-1], _top(y))
    c.setFont("Helvetica", 8)
    for x, text in zip(xs, ["Key", "Second", "Third"]):
        c.drawString(x + 4, _top(top + 12), text)
    y = top + 18 + 3
    expected = [["Key", "Second", "Third"][: len(widths)]]
    for rec, h in zip(records, heights):
        for col, lines in enumerate(rec):
            for k, line in enumerate(lines):
                c.drawString(xs[col] + 4, _top(y + 9 + k * lead), line)
        expected.append([" ".join(lines) for lines in rec])
        y += h * lead
    return Fixture((xs[0], top, xs[-1], bottom), expected)


def _band_grid(records: list[list[list[str]]], **kw) -> tuple[list[list[str]], list[list[str]]]:
    pdf, page, fx = make_page(lambda c: [draw_band_table(c, records, **kw)])
    try:
        tables = tables_with_model(page, [model_box(fx[0].bbox, 1.0)])
        assert len(tables) == 1
        return norm(tables[0].table_grid), fx[0].expected
    finally:
        pdf.close()


def test_one_line_records_in_every_column_split_into_rows() -> None:
    recs = [[[k], [v], [m]] for k, v, m in (("KELPA", "F=1", "Below"), ("KELPB", "F=0", "Not below"),
                                           ("KELPC", "G=1", "Equal"), ("KELPD", "G=0", "Other"))]
    grid, expected = _band_grid(recs)
    assert grid == expected


def test_records_with_wrapped_cells_in_other_columns_split_at_the_key_lines() -> None:
    recs = [
        [["Rega"], ["Accumulate the stalled cycles"], ["Short"]],
        [["Regb"], ["Accumulate the cycles in", "the idle state"], ["Short"]],
        [["Regc"], ["One line"], ["Wrapped over", "two lines"]],
    ]
    grid, expected = _band_grid(recs)
    assert grid == expected


def test_a_wrapped_key_cell_is_not_two_records() -> None:
    """'Mode' + 'choice' does not fit the narrow key column, so it is one wrapped label."""
    recs = [[["Mode", "choice"], ["UNIT.MODE_SEL", "[CHOICE]"], ["Value"]]]
    grid, expected = _band_grid(recs, widths=(45.0, 150.0, 150.0))
    assert grid == expected
    assert len(grid) == 2


def test_a_key_name_wrapped_in_the_middle_of_a_word_is_not_two_records() -> None:
    recs = [[["SAFE_USER_D", "EBUG"], ["R/W"], ["Allowed"]]]
    grid, expected = _band_grid(recs, widths=(60.0, 100.0, 100.0))
    assert grid == expected


def test_an_instance_name_and_its_formula_line_stay_one_row() -> None:
    recs = [[["DEMO_INSTANCE_NAME", "+ formula"], ["0A10 20B0h", "+ formula"], ["x"]]]
    grid, expected = _band_grid(recs, widths=(110.0, 110.0, 100.0))
    assert grid == expected


def test_a_header_row_with_wrapped_labels_is_one_row() -> None:
    def draw2(c: canvas.Canvas) -> list[Fixture]:
        xs = [60.0, 190.0, 340.0, 490.0]
        c.setLineWidth(0.6)
        for x in xs:
            c.line(x, _top(300), x, _top(350))
        for y in (300, 330, 350):
            c.line(xs[0], _top(y), xs[-1], _top(y))
        c.setFont("Helvetica", 8)
        for x, (l1, l2) in zip(xs, [("Op", "code"), ("Mode", "flag"), ("Info", "text")]):
            c.drawString(x + 4, _top(311), l1)
            c.drawString(x + 4, _top(322), l2)
        c.drawString(xs[0] + 4, _top(342), "row")
        return []

    pdf, page, _ = make_page(draw2)
    try:
        tables = tables_with_model(page, [model_box((60.0, 300.0, 490.0, 350.0), 1.0)])
        assert tables and len(tables[0].table_grid) == 2
    finally:
        pdf.close()


def test_two_one_word_lines_in_a_narrow_key_column_are_still_one_label() -> None:
    recs = [[["Mode", "choice"], ["A", "B"], ["C"]]]
    grid, expected = _band_grid(recs, widths=(34.0, 150.0, 150.0))
    assert grid == expected


def test_a_label_wrapped_over_three_one_word_lines_next_to_shorter_cells_is_one_label() -> None:
    recs = [[["Module", "faults", "status"], ["GLOBAL_FAULT_REG", "[UNIT_A]"], ["~"]]]
    grid, expected = _band_grid(recs, widths=(46.0, 150.0, 150.0))
    assert grid == expected


def test_a_one_line_key_next_to_a_prose_cell_stays_one_row() -> None:
    recs = [[["option"], ["First line of prose", "second line of prose", "third line of prose"], ["x"]]]
    grid, expected = _band_grid(recs)
    assert grid == expected


def test_row_rules_broken_under_a_spanning_key_column_keep_every_row() -> None:
    recs = [[[""], ["alpha one"], ["x"]], [[""], ["beta two"], ["y"]], [[""], ["gamma three"], ["z"]]]
    recs[0][0] = ["Group"]
    grid, _ = _band_grid(recs, row_rules=True, spanning_key=True)
    body = [r for r in grid if any("alpha" in c or "beta" in c or "gamma" in c for c in r)]
    assert len(body) == 3 and not any("alpha" in c and "beta" in c for r in body for c in r)


def _caption_note_page(c: canvas.Canvas) -> list[Fixture]:
    c.setFont("Helvetica", 9)
    c.drawString(60, _top(278), "CAPTIONLINE of the table")
    fx = draw_record_table(c)
    c.setFont("Helvetica", 9)
    c.drawString(60, _top(fx.bbox[3] + 14), "NOTELINE below the table")
    return [fx]


@pytest.mark.parametrize(
    "dy0,dy1",
    [(-26, 0), (0, 22), (-26, 22)],
    ids=["box_has_caption", "box_has_note", "box_has_both"],
)
def test_a_box_that_cuts_or_overshoots_a_ruled_table_keeps_rows_and_leaves_outside_words(dy0: int, dy1: int) -> None:
    pdf, page, fx = make_page(_caption_note_page)
    try:
        x0, y0, x1, y1 = fx[0].bbox
        box = ((x0 / PAGE_W, (y0 + dy0) / PAGE_H, x1 / PAGE_W, (y1 + dy1) / PAGE_H), 0.98)
        tables = tables_with_model(page, [box])
        assert tables
        text = " ".join(c or "" for t in tables for r in t.table_grid for c in r)
        assert "NOTELINE" not in text
        for t in tables:
            assert t.bbox[3] < y1 + 6, "the note is outside the table region, so it stays a text region"
        body = [r for t in tables for r in norm(t.table_grid)]
        keys = {row[0] for row in REC_ROWS}
        assert [r for r in body if r[0] in keys] == list(fx[0].expected[1:])
        assert not any("ALPHA" in r[0] and "BRAVO" in r[0] for r in body), "no two records in one row"
    finally:
        pdf.close()


SHORT_ROWS = [f"Value {i} of the row" for i in range(6)]


@pytest.mark.parametrize("shift", [6, 12, 25])
def test_a_box_shifted_down_over_a_ruled_table_never_merges_records_or_takes_the_note(shift: int) -> None:
    def draw(c: canvas.Canvas) -> list[Fixture]:
        c.setFont("Helvetica", 9)
        c.drawString(40, _top(228), "CAPTIONLINE of the table")
        fx = draw_ruled_table(c, 36, 240, names=[f"K{i}" for i in range(6)], prose=SHORT_ROWS, style="stroke", rule_w=0.5)
        c.setFont("Helvetica", 9)
        c.drawString(40, _top(fx.bbox[3] + 10), "NOTELINE below the table")
        return [fx]

    pdf, page, fx = make_page(draw)
    try:
        x0, y0, x1, y1 = fx[0].bbox
        box = ((x0 / PAGE_W, (y0 + shift) / PAGE_H, x1 / PAGE_W, (y1 + shift) / PAGE_H), 0.98)
        tables = tables_with_model(page, [box])
        assert tables
        for t in tables:
            for row in norm(t.table_grid):
                assert sum(1 for c in row if c.startswith("K")) <= 1 and not any("K0 K1" in c for c in row)
    finally:
        pdf.close()


def test_table_region_ends_at_the_matched_grid_not_at_the_model_box() -> None:
    pdf, page, fx = make_page(_caption_note_page)
    try:
        x0, y0, x1, y1 = fx[0].bbox
        box = ((x0 / PAGE_W, (y0 - 30) / PAGE_H, x1 / PAGE_W, (y1 + 22) / PAGE_H), 0.98)
        tables = tables_with_model(page, [box])
        assert len(tables) == 1
        assert tables[0].bbox[1] < y0 - 10, "a caption line above the grid stays in the region"
        assert "CAPTIONLINE" in " ".join(tables[0].table_grid[0])
        assert tables[0].bbox[3] < y1 + 3, "the region ends at the grid: the note stays a text region"
        assert norm(tables[0].table_grid)[1:] == fx[0].expected[1:]
    finally:
        pdf.close()


# --- thresholds ------------------------------------------------------------------------

def _two_by_two_block_page(c: canvas.Canvas) -> list[Fixture]:
    c.setLineWidth(0.5)
    for y in (300, 330, 360):
        c.line(100, _top(y), 300, _top(y))
    for x in (100, 200, 300):
        c.line(x, _top(300), x, _top(360))
    c.setFont("Helvetica", 9)
    for r in range(2):
        for k in range(2):
            c.drawString(110 + 100 * k, _top(318 + 30 * r), f"cell{r}{k}")
    return []


@pytest.mark.parametrize("target_area,expect_grid", [(200 * 60 / 0.4, False), (200 * 60 / 0.6, True)],
                         ids=["iou_0.4_rejected", "iou_0.6_accepted"])
def test_target_match_needs_half_the_target_box(target_area: float, expect_grid: bool) -> None:
    pdf, page, _ = make_page(_two_by_two_block_page)
    try:
        w = 200.0
        h = target_area / w
        target = (100.0, 300.0, 100.0 + w, 300.0 + h)
        grid = ola._extract_table_by_words(page, None, target)
        assert (grid is not None) is expect_grid
    finally:
        pdf.close()


def _vertical_rules_page(
    xs: list[float], length: float, filled: bool = False,
) -> tuple[pdfplumber.PDF, pdfplumber.page.Page, list[Fixture]]:
    def draw(c: canvas.Canvas) -> list[Fixture]:
        c.setLineWidth(0.5)
        c.setFillColorRGB(0, 0, 0)
        for x in xs:
            if filled:
                c.rect(x, _top(300 + length), 0.5, length, stroke=0, fill=1)
            else:
                c.line(x, _top(300), x, _top(300 + length))
        return []

    return make_page(draw)


@pytest.mark.parametrize("n,expected", [(2, False), (3, True)])
def test_three_vertical_rules_make_a_table_ruled(n: int, expected: bool) -> None:
    pdf, page, _ = _vertical_rules_page([60.0, 160.0, 260.0][:n], 40.0)
    try:
        assert ola._has_vertical_rules(page, (50.0, 295.0, 270.0, 345.0)) is expected
    finally:
        pdf.close()


@pytest.mark.parametrize("length,expected", [(9.0, False), (11.0, True)])
def test_vertical_rule_must_be_longer_than_ten_points(length: float, expected: bool) -> None:
    pdf, page, _ = _vertical_rules_page([60.0, 160.0, 260.0], length, filled=True)
    try:
        assert ola._has_vertical_rules(page, (50.0, 295.0, 270.0, 345.0)) is expected
    finally:
        pdf.close()


def test_words_outside_the_within_box_or_the_table_are_not_cell_text() -> None:
    def draw(c: canvas.Canvas) -> list[Fixture]:
        fx = draw_ruled_table(c, 36, 240, names=NAMES[:2], prose=PROSE[:2])
        c.setFont("Helvetica", 8)
        c.drawString(40, _top(fx.bbox[3] + 5), "OUTSIDEWORD")
        return [fx]

    pdf, page, fx = make_page(draw)
    try:
        table = page.find_tables()[0]
        x0, y0, x1, y1 = fx[0].bbox
        full = ola._table_grid_by_words(table, region=page, within=(0, 0, PAGE_W, PAGE_H))
        assert "OUTSIDEWORD" not in " ".join(c or "" for r in full for c in r), "outside the table bbox"
        left_only = ola._table_grid_by_words(table, region=page, within=(x0 - 1, y0 - 1, x0 + 80, y1 + 1))
        flat = " ".join(c or "" for r in left_only for c in r)
        assert "tablename" in flat and "Name of" not in flat, "outside the within box"
        no_within = ola._table_grid_by_words(table, region=page)
        assert "Name of" in " ".join(c or "" for r in no_within for c in r)
    finally:
        pdf.close()


def _stub_rule(bbox: tuple[float, float, float, float], grid: list[list[str]]) -> ola.LayoutRegion:
    return ola.LayoutRegion(type=LayoutRegionType.TABLE, bbox=bbox, text="", table_grid=grid)


def test_agreed_rule_table_must_keep_the_words_and_have_two_columns() -> None:
    pdf, page, fx = make_page(lambda c: [draw_ruled_table(c, 36, 240)])
    try:
        words = page.extract_words()
        box = model_box(fx[0].bbox)
        all_text = " ".join(w["text"] for w in words)
        for grid in ([["x", "y"], ["z", "w"]], [["a"], ["b"], ["c"]], [[all_text], [""]]):
            with patch.object(ola, "_reextract_grid_and_bbox", wraps=ola._reextract_grid_and_bbox) as reextract:
                ola._prefer_model_tables(page, PAGE_W, PAGE_H, [_stub_rule(fx[0].bbox, grid)], words, [box])
            reextract.assert_called_once()  # low coverage; a one-column grid; a one-column grid with every word
    finally:
        pdf.close()


def test_lines_grid_with_one_column_does_not_end_the_search() -> None:
    """Horizontal rules and no verticals: the lines grid has one column; the text columns decide."""
    header = ("Alpha", "Beta items", "Gamma value")
    rows = [(f"Row {i}", f"Item number {i} here", f"Value {i}") for i in range(5)]

    def draw(c: canvas.Canvas) -> list[Fixture]:
        c.setLineWidth(0.4)
        for r in range(len(rows) + 2):
            c.line(54, _top(296 + 18 * r), 552, _top(296 + 18 * r))
        for x in (54, 552):
            c.line(x, _top(296), x, _top(296 + 18 * (len(rows) + 1)))
        c.setFont("Helvetica", 8)
        for r, row in enumerate((header, *rows)):
            for x, text in zip((54.0, 200.0, 420.0), row):
                c.drawString(x + 2, _top(308 + 18 * r), text)
        return []

    pdf, page, _ = make_page(draw)
    try:
        grid = ola._reextract_grid_for_bbox(page, (54.0, 296.0, 552.0, 296.0 + 18 * 7), PAGE_W, PAGE_H)
        assert grid is not None and max(len(r) for r in grid) >= 3
    finally:
        pdf.close()


def test_only_a_multi_line_key_cell_may_hold_records() -> None:
    assert not ola._may_hold_record_lines([["a", "b"], ["c", "d"]])
    assert ola._may_hold_record_lines([["a", "b"], ["c\nd", "e"]])
    assert ola._may_hold_record_lines([["", "c\nd"]])


# --- the shared record rule on word lines (split_record_lines) ---------------------------

def _word(text: str, x0: float, top: float, size: float = 8.0) -> dict:
    return {"text": text, "x0": x0, "x1": x0 + 0.5 * size * len(text), "top": top, "bottom": top + size}


def _groups(specs: list[tuple[str, float, float]], cols: list[tuple[float, float]]) -> list[int]:
    """Line counts per record for words given as (text, x0, top)."""
    lines = band_lines([_word(t, x0, top) for t, x0, top in specs])
    return [len(g) for g in split_band_records(lines, cols, ruled_cell=True)]


COLS3 = [(0.0, 60.0), (60.0, 160.0), (160.0, 300.0)]


def test_a_left_aligned_wrapped_key_beside_a_value_list_is_one_record() -> None:
    """A wrapped key and "1 = ..." values one per line: not a centred code key."""
    specs = [
        ("Timer", 2.0, 10.0), ("Control", 24.0, 10.0), ("1", 62.0, 10.0), ("=", 70.0, 10.0), ("Debug", 80.0, 10.0),
        ("The", 162.0, 10.0), ("value", 180.0, 10.0), ("is", 200.0, 10.0),
        ("Domain", 2.0, 19.0), ("0", 62.0, 19.0), ("=", 70.0, 19.0), ("Run", 80.0, 19.0),
        ("Returns", 162.0, 19.0), ("the", 190.0, 19.0), ("mode", 205.0, 19.0),
    ]
    assert _groups(specs, COLS3) == [2]


def test_a_centred_code_key_beside_edge_aligned_fields_starts_records() -> None:
    specs = []
    for key, top in (("Kaa", 10.0), ("Kbb", 19.0), ("Kcc", 28.0)):
        specs += [(key, 18.0, top), ("Alphas", 62.0, top), ("Bravos", 70.0, top), ("Gammas", 162.0, top), ("it", 190.0, top)]
    assert _groups(specs, COLS3) == [1, 1, 1]


def test_a_centred_key_needs_to_be_inside_the_cell_and_near_its_centre() -> None:
    cols = [(0.0, 30.0), (30.0, 160.0), (160.0, 300.0)]

    def band(x0: float) -> list[int]:
        specs = []
        for key, top in (("Kaa", 10.0), ("Kbb", 19.0)):
            specs += [(key, x0, top), ("Alphas", 62.0, top), ("Bravos", 70.0, top), ("Gammas", 162.0, top), ("it", 190.0, top)]
        return _groups(specs, cols)

    assert band(7.0) == [1, 1]
    assert band(2.0) == [2]  # at the left edge
    assert band(14.0) == [2]  # off centre to the right


def test_two_columns_key_and_prose_start_a_record_at_a_key_line_after_a_gap() -> None:
    cols = [(0.0, 50.0), (50.0, 300.0)]
    specs = [("DR3", 2.0, 10.0)]
    for i in range(5):
        specs += [("Selects", 52.0, 10.0 + 9 * i), ("the", 90.0, 10.0 + 9 * i), ("clock", 110.0, 10.0 + 9 * i)]
    specs += [("CLK_SRC", 2.0, 55.0), ("Chooses", 52.0, 55.0), ("a", 90.0, 55.0), ("source", 100.0, 55.0)]
    assert _groups(specs, cols) == [5, 1]


def test_a_key_line_right_below_the_key_is_a_wrap_not_a_record() -> None:
    cols = [(0.0, 40.0), (40.0, 300.0)]
    specs = [("Dra", 2.0, 10.0), ("Selects", 52.0, 10.0), ("clock", 90.0, 10.0),
             ("Clksrc", 2.0, 19.0), ("Chooses", 52.0, 19.0), ("a", 90.0, 19.0)]
    assert _groups(specs, cols) == [2]


def test_a_lower_case_continuation_in_another_column_blocks_a_record() -> None:
    cols = [(0.0, 40.0), (40.0, 300.0)]
    specs = [("DR3", 2.0, 10.0), ("Selects", 52.0, 10.0), ("clock", 270.0, 10.0),
             ("CLK_SRC", 2.0, 55.0), ("and", 52.0, 55.0), ("then", 90.0, 55.0)]
    assert _groups(specs, cols) == [2]
    specs[-2:] = [("And", 52.0, 55.0), ("then", 90.0, 55.0)]
    assert _groups(specs, cols) == [1, 1]


@pytest.mark.parametrize("note", ["(RO)", "(read-only)", "See", "Note", "NOTE", "Note:", "\u2014", "(RW)"])
def test_an_annotation_under_a_key_never_starts_a_record(note: str) -> None:
    cols = [(0.0, 50.0), (50.0, 300.0)]
    specs = [("DR2", 2.0, 10.0), ("Selects", 52.0, 10.0), ("clock", 90.0, 10.0),
             (note, 2.0, 55.0), ("Chooses", 52.0, 55.0), ("source", 90.0, 55.0)]
    assert _groups(specs, cols) == [2]


def test_a_band_without_a_key_on_its_first_line_is_one_record() -> None:
    cols = [(0.0, 50.0), (50.0, 300.0)]
    specs = [("Selects", 52.0, 10.0), ("clock", 90.0, 10.0),
             ("DR3", 2.0, 55.0), ("Chooses", 52.0, 55.0), ("CLK_SRC", 2.0, 100.0), ("Picks", 52.0, 100.0)]
    assert _groups(specs, cols) == [3]


def test_a_grid_with_more_columns_than_the_rules_cut_is_not_the_ruled_grid() -> None:
    pdf, page, _ = _vertical_rules_page([60.0, 160.0, 260.0], 40.0)
    try:
        box = (50.0, 295.0, 270.0, 345.0)
        assert ola._is_ruled_grid(page, box, [["a", "b"], ["c", "d"]])
        assert not ola._is_ruled_grid(page, box, [["a", "b", "c"], ["d", "e", "f"]])
        assert not ola._is_ruled_grid(page, (50.0, 295.0, 170.0, 345.0), [["a"], ["b"]])  # two rules only
    finally:
        pdf.close()


C3 = [(0.0, 30.0), (30.0, 160.0), (160.0, 300.0)]


def _centred_band(key_x0: float, f2: list[float], f3: list[float], words2: int = 2, words3: int = 1) -> list[int]:
    """Three records with a 16 pt wide code key at ``key_x0`` in a 30 pt cell; per-record x of the first word
    of column 2 and 3 (``f2``/``f3``) and their word counts."""
    specs = []
    for i, top in enumerate((10.0, 19.0, 28.0)):
        specs.append((("Dbah", "Dbbh", "Dbch")[i], key_x0, top))
        specs += [(("Alphas", "Bravos")[n], f2[i] + 40.0 * n, top) for n in range(words2)]
        specs += [(("Gammas", "it")[n], f3[i] + 40.0 * n, top) for n in range(words3)]
    return _groups(specs, C3)


def test_a_key_within_a_tenth_of_the_centre_still_counts_as_centred() -> None:
    assert _centred_band(8.5, [62.0] * 3, [162.0] * 3) == [1, 1, 1]  # 1.5 pt off the centre


def test_a_centred_key_must_start_more_than_six_points_inside_the_cell() -> None:
    cols = [(0.0, 20.0), (20.0, 160.0), (160.0, 300.0)]
    specs = []
    for key, top in (("Kaa", 10.0), ("Kbb", 19.0)):
        specs += [(key, 2.0, top), ("Alphas", 62.0, top), ("Bravos", 100.0, top), ("Gammas", 162.0, top), ("it", 190.0, top)]
    assert _groups(specs, cols) == [2]  # 16 pt in a 20 pt cell: centre 10, edge 2


def test_other_columns_count_at_their_own_left_edge_within_three_points() -> None:
    assert _centred_band(7.0, [62.0, 63.5, 62.0], [162.0, 163.5, 162.0]) == [1, 1, 1]
    assert _centred_band(7.0, [62.0, 72.0, 62.0], [162.0, 172.0, 162.0]) == [2, 1]  # line 2 is off the edge
    assert _centred_band(7.0, [62.0, 66.0, 62.0], [162.0, 166.0, 162.0]) == [2, 1]


def test_a_centred_key_needs_two_other_columns_and_a_multi_word_field() -> None:
    assert _centred_band(7.0, [62.0] * 3, [162.0] * 3, words2=2, words3=1) == [1, 1, 1]
    assert _centred_band(7.0, [62.0] * 3, [162.0] * 3, words2=1, words3=1) == [3]  # all single words
    specs = []
    for key, top in (("Kaa", 10.0), ("Kbb", 19.0)):
        specs += [(key, 7.0, top), ("Alphas", 62.0, top), ("Bravos", 102.0, top)]
    assert _groups(specs, C3) == [2]  # one other column only


def test_prose_continuing_in_another_column_blocks_a_centred_start() -> None:
    cols = [(0.0, 30.0), (30.0, 100.0), (100.0, 160.0), (160.0, 300.0)]

    def band(second_col4: list[tuple[str, float]]) -> list[int]:
        specs = [("Kaa", 7.0, 10.0), ("Alphas", 32.0, 10.0), ("Bravos", 64.0, 10.0), ("Gammas", 102.0, 10.0),
                 ("Gammas", 162.0, 10.0), ("Mode", 200.0, 10.0), ("Of", 240.0, 10.0), ("clock", 270.0, 10.0),
                 ("Kbb", 7.0, 19.0), ("Alphas", 32.0, 19.0), ("Bravos", 64.0, 19.0), ("Gammas", 102.0, 19.0)]
        specs += [(t, x, 19.0) for t, x in second_col4]
        return _groups(specs, cols)

    assert band([("Gammas", 162.0)]) == [1, 1]
    assert band([("and", 162.0), ("then", 190.0)]) == [2]  # lower-case continuation
    assert band([("Setting", 162.0), ("the", 195.0), ("bit", 220.0)]) == [2]  # a three-word wrap


def test_a_lower_case_key_after_a_gap_is_not_a_record() -> None:
    cols = [(0.0, 30.0), (30.0, 300.0)]
    specs = [("DR3", 2.0, 10.0), ("Selects", 52.0, 10.0), ("ctrl", 2.0, 55.0), ("Chooses", 52.0, 55.0)]
    assert _groups(specs, cols) == [2]
    specs[2] = ("Ctrl", 2.0, 55.0)
    assert _groups(specs, cols) == [1, 1]


def test_a_key_line_that_fits_after_the_key_above_starts_a_record_by_its_width() -> None:
    cols = [(0.0, 60.0), (60.0, 300.0)]
    # 'Domain' (24 pt) after 'aaaaaaa' (28 pt, ending at 30): 30 + 2.4 + 24 = 56.4 <= 58
    assert _groups([("aaaaaaa", 2.0, 10.0), ("Alpha", 62.0, 10.0), ("Domain", 2.0, 19.0), ("Beta", 62.0, 19.0)], cols) == [1, 1]
    # 'Domains' (28 pt): 30 + 2.4 + 28 = 60.4 > 58: a wrap
    assert _groups([("aaaaaaa", 2.0, 10.0), ("Alpha", 62.0, 10.0), ("Domains", 2.0, 19.0), ("Beta", 62.0, 19.0)], cols) == [2]


def test_words_go_to_the_record_of_their_own_line_not_to_the_nearest_top() -> None:
    """A tall word whose top lies above the previous record's line must not pull that record's words."""
    cells = [(0.0, 0.0, 60.0, 80.0), (60.0, 0.0, 300.0, 80.0)]

    def word(text: str, x0: float, top: float, size: float = 8.0) -> dict:
        w = _word(text, x0, top, size)
        w["chars"] = [w]
        return w

    col0 = [word("Alpha", 2.0, 10.0), word("Beta", 2.0, 28.0)]
    col1 = [word("Selects.", 62.0, 10.0), word("Tall", 62.0, 13.0, 30.0)]  # top 13, centre 28: record B
    bands = ola.split_record_bands([col0, col1], cells)
    assert bands is not None and len(bands) == 2
    assert [[c["text"] for c in cell] for cell in bands[0]] == [["Alpha"], ["Selects."]]
    assert [[c["text"] for c in cell] for cell in bands[1]] == [["Beta"], ["Tall"]]


def test_region_of_a_ruled_grid_is_the_grid_padded_by_the_rule_tolerance() -> None:
    own = (60.0, 100.0, 300.0, 200.0)
    box = (50.0, 90.0, 310.0, 260.0)
    assert ola._region_bbox(own, box) == (58.0, 90.0, 302.0, 202.0)
    # toward a neighbour, a third of the distance past the merge gap at most (none within it)
    below = (60.0, 203.0, 300.0, 300.0)
    left = (10.0, 100.0, 59.0, 200.0)
    padded = ola._region_bbox(own, box, [below, left])
    assert padded == pytest.approx((60.0, 90.0, 302.0, 200.0 + 1.0 / 3.0))
    assert ola._region_bbox(None, box) == box


def test_text_that_belongs_to_the_first_record_only_is_not_copied_to_the_later_ones() -> None:
    """The s04 form: a description written once for two records. It stays in the first record's cell;
    the later record's cell is empty (it is context, never cell text)."""
    recs = [
        [["Alpha"], ["one"], ["Description of both"]],
        [["Beta"], ["two"], []],
    ]
    grid, _ = _band_grid(recs)
    assert grid[1:] == [["Alpha", "one", "Description of both"], ["Beta", "two", ""]]


def test_three_one_word_code_keys_beside_as_many_lines_are_a_list_of_records() -> None:
    cols = [(0.0, 30.0), (30.0, 300.0)]

    def band(keys: list[str]) -> list[int]:
        specs = []
        for i, key in enumerate(keys):
            specs += [(key, 2.0, 10.0 + 9 * i), ("Selects", 52.0, 10.0 + 9 * i), ("clock", 90.0, 10.0 + 9 * i)]
        return _groups(specs, cols)

    assert band(["REG_A", "REG_B", "REG_C"]) == [1, 1, 1]
    assert band(["REG_A", "REG_B"]) == [1, 1]  # two whole codes are a list too
    assert band(["REG_A", "_TAIL_B"]) == [2]  # the second is the tail of a broken identifier
    assert band(["Reg_a", "Reg_b", "Reg_c"]) == [3]  # not code tokens
    assert band(["REGA", "REGB", "REGC"]) == [3]  # code tokens need a digit or an underscore
    assert band(["REG_A", "REG_B", "REG_C X"]) == [3]  # one word per key line
    # fewer lines in the other column than keys: a label wrapped over several lines
    specs = [("REG_A", 2.0, 10.0), ("Selects", 52.0, 10.0), ("REG_B", 2.0, 19.0), ("REG_C", 2.0, 28.0)]
    assert _groups(specs, cols) == [3]


def _code_run(keys_x: float, rows: list[list[tuple[str, float]]], cols: list[tuple[float, float]]) -> list[int]:
    specs = []
    for i, cells in enumerate(rows):
        top = 10.0 + 9 * i
        specs.append((f"POR_XY{i}", keys_x, top))
        specs += [(w, x, top) for w, x in cells]
    return _groups(specs, cols)


def test_a_code_run_needs_a_column_with_as_many_lines_as_there_are_keys() -> None:
    cols = [(0.0, 30.0), (30.0, 160.0), (160.0, 300.0)]
    # column 2 has text beside key 2 only, column 3 beside key 3 only: each has fewer lines than keys
    rows = [[("Selects", 32.0)], [("Alpha", 32.0)], [("Beta", 162.0)]]
    assert _code_run(2.0, rows, cols) == [3]


def test_a_code_run_needs_the_co_start_at_the_column_edge() -> None:
    cols = [(0.0, 30.0), (30.0, 300.0)]
    rows = [[("Selects", 32.0)], [("Alpha", 32.0)], [("Beta", 32.0)]]
    assert _code_run(2.0, rows, cols) == [1, 1, 1]
    rows = [[("Selects", 32.0)], [("Alpha", 90.0)], [("Beta", 32.0)]]  # off the edge on the second key line
    assert _code_run(2.0, rows, cols) == [2, 1]  # the run fails; branch (a) still starts the third record


def test_a_failed_code_run_leaves_the_band_as_one_record_not_to_the_other_rules() -> None:
    cols = [(0.0, 60.0), (60.0, 300.0)]  # a wide key column: branch (a) would split every line
    specs = [
        ("POR_XY0", 2.0, 10.0), ("Selects.", 62.0, 10.0),
        ("POR_XY1", 2.0, 19.0), ("Alpha", 62.0, 19.0), ("beta", 268.0, 19.0),
        ("POR_XY2", 2.0, 28.0), ("Setting", 62.0, 28.0), ("the", 110.0, 28.0), ("bit", 130.0, 28.0),
    ]
    assert _groups(specs, cols) == [1, 2]  # the run fails (the third line continues prose); (a) starts line 2


def _flow(words: list[str], x0: float, top: float) -> list[tuple[str, float, float]]:
    """Words laid left to right from x0 on one line (4 pt per character, 4 pt space)."""
    out, x = [], x0
    for w in words:
        out.append((w, x, top))
        x += 4.0 * len(w) + 4.0
    return out


ALIAS_COLS = [(0.0, 60.0), (60.0, 140.0)]  # the description column is 80 pt wide


def _aliases(lines: list[list[str]]) -> list[int]:
    specs = []
    for i, words in enumerate(lines):
        top = 10.0 + 9 * i
        specs.append((f"POR_XY{i}", 2.0, top))
        specs += _flow(words, 62.0, top)
    return _groups(specs, ALIAS_COLS)


def test_aliases_listed_one_per_line_beside_one_description_are_one_record() -> None:
    """N5a: the description is cut mid-sentence between the aliases."""
    lines = [["Enables", "the", "buffer"], ["domain", "bit", "when"], ["read", "enable", "is"], ["set", "and", "cleared."]]
    assert _aliases(lines) == [4]


def test_a_code_run_with_a_real_new_description_on_every_line_is_a_list_of_records() -> None:
    lines = [["Selects.", "Chip", "one."], ["Alpha.", "Chip", "two."], ["Beta.", "Chip", "six."]]
    assert _aliases(lines) == [1, 1, 1]


def test_aliases_beside_a_lower_case_description_wrap_are_one_record() -> None:
    lines = [["Enables", "the", "buffer"], ["and", "when", "set"], ["then", "done"]]
    assert _aliases(lines) == [3]


def test_a_lower_case_line_after_a_sentence_end_is_not_a_wrap() -> None:
    """R6: a record whose description opens lower-case ('xq9 ...') after a finished sentence."""
    cols = [(0.0, 40.0), (40.0, 300.0)]
    specs = []
    for i, (key, first) in enumerate((("DR0", ["Selects", "the", "clock."]), ("DR1", ["xq9", "unit", "enable."]),
                                      ("DR2", ["used", "by", "the", "core."]))):
        specs.append((key, 2.0, 10.0 + 9 * i))
        specs += _flow(first, 42.0, 10.0 + 9 * i)
    assert _groups(specs, cols) == [1, 1, 1]


@pytest.mark.parametrize("fam", ["L1", "L4", "L5"])
def test_lower_case_descriptions_and_numeric_or_pin_keys_split_into_records(fam: str) -> None:
    """The R6 forms: code keys with lower-case descriptions, numeric keys, pin tables with lower-case signals."""
    if fam == "L1":
        recs = [[["DR0"], ["xq9 unit"], ["Selects the clock."]], [["DR1"], ["if set,"], ["used by the core"]],
                [["DR2"], ["used by"], ["Sets the mode."]]]
    elif fam == "L4":
        recs = [[["0x00"], ["Reset value"], ["Clears the status."]], [["0x04"], ["Bravos"], ["only when set."]],
                [["0x08"], ["Mode"], ["Selects the clock."]]]
    else:
        recs = [[["AB12"], ["port0_aaa"], ["used by the port."]], [["CD7"], ["port1_bbb"], ["if set,"]],
                [["EF3"], ["port2_ccc"], ["Clock line."]]]
    grid, expected = _band_grid(recs)
    assert grid == expected




def test_a_description_continuing_after_a_semicolon_is_a_wrap_not_a_new_record() -> None:
    """A line ending with a semicolon is not a finished sentence: the lower-case line below is a wrap."""
    cols = [(0.0, 120.0), (120.0, 220.0)]
    specs = [("Code", 2.0, 10.0), *_flow(["Writes", "back", "data;"], 122.0, 10.0)]
    specs += [("Label", 2.0, 19.0), *_flow(["initiate", "the", "flush."], 122.0, 19.0)]
    assert _groups(specs, cols) == [2]
    specs[4:] = [("Label", 2.0, 19.0), *_flow(["Initiate", "the", "flush."], 122.0, 19.0)]
    assert _groups(specs, cols) == [1, 1]


# --- pinned by the hand-checked reference and the mutation run ----------------------------

def _four_col_band(c: canvas.Canvas, recs: list[list[list[str]]]) -> Fixture:
    xs = [60.0, 230.0, 260.0, 300.0, 520.0]
    lead = 11.0
    height = sum(max(len(cell) for cell in rec) for rec in recs) * lead + 6
    top, bottom = 300.0, 300.0 + 18 + height
    c.setLineWidth(0.6)
    for x in xs:
        c.line(x, _top(top), x, _top(bottom))
    for y in (top, top + 18, bottom):
        c.line(xs[0], _top(y), xs[-1], _top(y))
    c.setFont("Helvetica", 8)
    for x, text in zip(xs, ["Code", "Op", "Mode", "Description"]):
        c.drawString(x + 4, _top(top + 12), text)
    y = top + 18 + 3
    for rec in recs:
        for col, lines in enumerate(rec):
            for k, line in enumerate(lines):
                c.drawString(xs[col] + 4, _top(y + 9 + k * lead), line)
        y += max(len(cell) for cell in rec) * lead
    return Fixture((xs[0], top, xs[-1], bottom), [])


def test_the_code_run_restriction_of_the_consistency_rule() -> None:
    """A stray key line with nothing beside it collapses the band only for a run of code tokens."""
    cols = [(0.0, 120.0), (120.0, 300.0)]
    specs = [("Alpha", 2.0, 10.0), ("x.", 122.0, 10.0), ("Label", 2.0, 19.0), ("Beta", 2.0, 28.0), ("y.", 122.0, 28.0)]
    assert _groups(specs, cols) == [2, 1]  # not code keys: the stray line joins the first record, Beta starts one


def test_a_key_line_that_fits_but_has_nothing_beside_it_does_not_start_a_record() -> None:
    cols = [(0.0, 120.0), (120.0, 300.0)]
    specs = [("Alpha", 2.0, 10.0), ("x.", 122.0, 10.0), ("Beta", 2.0, 19.0)]
    assert _groups(specs, cols) == [2]


def test_an_alias_stack_collapses_only_when_the_key_lines_are_stacked() -> None:
    cols = [(0.0, 60.0), (60.0, 140.0)]

    def band(third_top: float) -> list[int]:
        specs = [("POR_XY0", 2.0, 10.0), *_flow(["Enables", "the", "buffer"], 62.0, 10.0)]
        specs += [("POR_XY1", 2.0, 19.0), *_flow(["domain", "bit", "when"], 62.0, 19.0)]
        specs += [("POR_XY2", 2.0, third_top), *_flow(["Selects", "one."], 62.0, third_top)]
        return _groups(specs, cols)

    assert band(28.0) == [3]  # stacked key lines: a list of aliases in one record
    assert band(39.0) == [2, 1]  # a blank line (12 pt) before the third alias: the band is not collapsed
    assert band(60.0) == [2, 1]


def test_a_centred_key_that_opens_lower_case_does_not_start_a_record_by_branch_b() -> None:
    cols = [(0.0, 24.0), (24.0, 160.0), (160.0, 300.0)]
    specs = [("Kaa", 4.0, 10.0), ("Alphas", 26.0, 10.0), ("Bravos", 70.0, 10.0), ("Gammas", 162.0, 10.0), ("it", 190.0, 10.0),
             ("reg", 7.0, 19.0), ("Alphas", 26.0, 19.0), ("Bravos", 70.0, 19.0), ("Gammas", 162.0, 19.0), ("it", 190.0, 19.0)]
    assert _groups(specs, cols) == [2]
    specs[5] = ("Reg", 7.0, 19.0)
    assert _groups(specs, cols) == [1, 1]


def test_a_centred_key_below_a_key_that_fills_its_cell_is_a_wrap_not_a_record() -> None:
    cols = [(0.0, 30.0), (30.0, 160.0), (160.0, 300.0)]
    base = [("Alphas", 32.0, 10.0), ("Bravos", 70.0, 10.0), ("Gammas", 162.0, 10.0), ("it", 190.0, 10.0),
            ("Alphas", 32.0, 19.0), ("Bravos", 70.0, 19.0), ("Gammas", 162.0, 19.0), ("it", 190.0, 19.0)]
    wide = [("Kaaabcdefghi", 1.0, 10.0), ("Kbb", 7.0, 19.0)]  # 48 pt: fills the cell and overflows it
    assert _groups(wide[:1] + wide[1:] + base, cols) == [2]
    short = [("Kaa", 1.0, 10.0), ("Kbb", 7.0, 19.0)]
    assert _groups(short + base, cols) == [1, 1]


def test_a_box_shifted_six_points_over_one_line_rows_keeps_every_row() -> None:
    def draw(c: canvas.Canvas) -> list[Fixture]:
        return [draw_ruled_table(c, 36, 240, names=[f"K{i}" for i in range(6)], prose=SHORT_ROWS,
                                 style="stroke", rule_w=0.5)]

    pdf, page, fx = make_page(draw)
    try:
        x0, y0, x1, y1 = fx[0].bbox
        for shift in (0, 3, 6):
            box = ((x0 / PAGE_W, (y0 + shift) / PAGE_H, x1 / PAGE_W, (y1 + shift) / PAGE_H), 0.98)
            tables = tables_with_model(page, [box])
            assert len(tables) == 1, shift
            assert norm(tables[0].table_grid) == fx[0].expected, shift
    finally:
        pdf.close()


def test_a_wrapped_operand_line_that_fits_stays_one_record_through_the_product_path() -> None:
    """A key cell whose list of operands breaks after a comma: the next line is a wrap although its word fits."""
    recs = [[["Item alpha with a long label here", "Namealphaxx one, two,", "three, four, five"], ["R"], ["Text"], ["Does a first thing", "to the operands", "in the list."]],
            [["Item bravo with a long label here", "Namebravoxx six, seven,", "eight, nine, ten"], ["R"], ["Text"], ["Does a second thing", "to the operands", "in the list."]]]
    pdf, page, fx = make_page(lambda c: [_four_col_band(c, recs)])
    try:
        tables = tables_with_model(page, [model_box(fx[0].bbox, 1.0)])
        assert tables and len(tables[0].table_grid) == 3  # header + two records
    finally:
        pdf.close()


def test_identifiers_broken_in_the_middle_of_a_token_across_columns_are_one_record() -> None:
    cols = [(0.0, 62.0), (62.0, 124.0), (124.0, 186.0)]
    specs = []
    for j, (a, b, c3) in enumerate((("ZED1_QRS_ABC1", "QR_U_ABCDE_F1", "TUVWX_YZABC_1"),
                                    ("ZED1_QRS_ABC1", "QR_U_JKLMN_O1", "LMN_BANK3_ABC"),
                                    ("ZED1_QRS_ABC1", "QR_U_STUVW_X1", "LMN_BANK2_ABC"))):
        for i, word in enumerate((a, b, c3)):
            specs.append((word, 62.0 * j + 3.0, 10.0 + 9.6 * i))
    assert _groups(specs, cols) == [3]


def test_a_key_line_that_starts_with_an_underscore_continues_the_token_above() -> None:
    cols = [(0.0, 90.0), (90.0, 300.0)]
    specs = [("REG_ABC", 2.0, 10.0), ("Alpha.", 92.0, 10.0), ("_ABC_D1", 2.0, 19.0), ("Beta.", 92.0, 19.0)]
    assert _groups(specs, cols) == [2]  # the second line opens with an underscore: a wrap
    specs[2] = ("XYZ_ABC", 2.0, 19.0)
    assert _groups(specs, cols) == [1, 1]


def test_two_code_keys_form_a_list_of_records_when_the_second_is_a_whole_code() -> None:
    cols = [(0.0, 80.0), (80.0, 300.0)]
    specs = [("LVL_1", 2.0, 10.0), ("Selects.", 82.0, 10.0), ("Chip", 120.0, 10.0),
             ("LVL_2", 2.0, 19.0), ("Alpha.", 82.0, 19.0), ("Chip", 120.0, 19.0)]
    assert _groups(specs, cols) == [1, 1]


def test_two_code_keys_are_one_record_when_the_second_is_the_tail_of_a_broken_identifier() -> None:
    cols = [(0.0, 50.0), (50.0, 300.0)]
    specs = [("LVL_ABCDEFGHI", 2.0, 10.0), ("Selects.", 52.0, 10.0), ("Chip", 90.0, 10.0),
             ("_TAIL_2", 2.0, 19.0), ("Alpha.", 52.0, 19.0), ("Chip", 90.0, 19.0)]
    assert _groups(specs, cols) == [2]


def test_a_code_key_may_carry_a_footnote_letter_after_a_digit() -> None:
    cols = [(0.0, 80.0), (80.0, 300.0)]
    specs = [("LVL1a", 2.0, 10.0), ("Selects.", 82.0, 10.0), ("Chip", 120.0, 10.0),
             ("LVL2b", 2.0, 19.0), ("Alpha.", 82.0, 19.0), ("Chip", 120.0, 19.0)]
    assert _groups(specs, cols) == [1, 1]
    cols = [(0.0, 34.0), (34.0, 300.0)]  # a narrow key cell: branch (a) cannot start the second record
    assert _groups([("LVL1a", 2.0, 10.0), ("Selects.", 36.0, 10.0), ("LVL2b", 2.0, 19.0), ("Alpha.", 36.0, 19.0)], cols) == [1, 1]
    assert _groups([("LVLxa", 2.0, 10.0), ("Selects.", 36.0, 10.0), ("LVL2b", 2.0, 19.0), ("Alpha.", 36.0, 19.0)], cols) == [2]


# --- constants and guards of the record rule, each pinned at its boundary ------------------

def _fine(specs: list[tuple], cols: list[tuple[float, float]]) -> list[int]:
    """Like ``_groups`` with an explicit character width: specs are (text, x0, top, size, char_width)."""
    words = [
        {"text": t, "x0": x0, "x1": x0 + cw * len(t), "top": top, "bottom": top + size}
        for t, x0, top, size, cw in specs
    ]
    return [len(g) for g in split_band_records(band_lines(words), cols, ruled_cell=True)]


def _w(text: str, x0: float, top: float, cw: float = 4.0) -> tuple:
    return (text, x0, top, 8.0, cw)


CENTRE_COLS = [(0.0, 40.0), (40.0, 160.0), (160.0, 300.0)]


def _centred_pair(first: tuple, key: tuple) -> list[tuple]:
    rest = [("Alphas", 42.0, 10.0), ("Bravos", 70.0, 10.0), ("Gammas", 162.0, 10.0), ("it", 190.0, 10.0)]
    rest2 = [("Alphas", 42.0, 19.0), ("Bravos", 70.0, 19.0), ("Gammas", 162.0, 19.0), ("it", 190.0, 19.0)]
    return [first, *[_w(*r) for r in rest], key, *[_w(*r) for r in rest2]]


def test_a_blank_line_before_a_key_line_means_a_record_but_half_a_blank_line_does_not() -> None:
    cols = [(0.0, 30.0), (30.0, 300.0)]

    def band(gap: float) -> list[int]:
        return _groups([("Alphabet", 2.0, 10.0), ("Selects.", 32.0, 10.0), ("Betaxyz", 2.0, 18.0 + gap), ("Chooses.", 32.0, 18.0 + gap)], cols)

    assert band(4.0) == [2] and band(6.0) == [2]
    assert band(9.0) == [1, 1]


def test_a_lower_case_wrap_after_a_finished_sentence_is_not_a_wrap() -> None:
    cols = [(0.0, 120.0), (120.0, 160.0)]
    done = [("Alpha", 2.0, 10.0), *_flow(["Does", "it."], 122.0, 10.0), ("Beta", 2.0, 19.0), ("unit", 122.0, 19.0)]
    assert _groups(done, cols) == [1, 1]
    cut = [("Alpha", 2.0, 10.0), *_flow(["Does", "it"], 122.0, 10.0), ("Beta", 2.0, 19.0), ("unit", 122.0, 19.0)]
    assert _groups(cut, cols) == [2]


def test_aliases_beside_a_prose_wrap_of_three_words_are_one_record() -> None:
    cols = [(0.0, 60.0), (60.0, 140.0)]

    def band(second: list[str]) -> list[int]:
        specs = [("POR_XY0", 2.0, 10.0), *_flow(["Enables", "the"], 62.0, 10.0),
                 ("POR_XY1", 2.0, 19.0), *_flow(second, 62.0, 19.0),
                 ("POR_XY2", 2.0, 28.0), *_flow(["Done."], 62.0, 28.0)]
        return _groups(specs, cols)

    assert band(["Domainsxx", "bit", "when"]) == [3]
    assert band(["Domainsxx", "bit"]) == [1, 1, 1]  # two words are not a prose wrap


def test_a_key_that_fills_its_cell_is_a_broken_token_at_ninety_percent() -> None:
    cols = CENTRE_COLS

    def band(prev_chars: int) -> list[int]:
        return _fine(_centred_pair(("P" * prev_chars, 2.0, 10.0, 8.0, 1.0), _w("KEY", 14.0, 19.0)), cols)

    assert band(30) == [1, 1]  # 30 of 38 pt: not full
    assert band(35) == [2]  # 35 of 38 pt: full (0.9 x 38 = 34.2)


def test_a_centred_key_may_be_a_tenth_of_the_cell_off_the_centre_but_not_more() -> None:
    pair = lambda x0: _fine(_centred_pair(_w("AAA", 8.0, 10.0), _w("KKK", x0, 19.0)), CENTRE_COLS)  # noqa: E731
    assert pair(14.0) == [1, 1]
    assert pair(17.5) == [1, 1]  # 3.5 pt off a 40 pt cell: inside 12 %
    assert pair(19.5) == [2]  # 5.5 pt off: outside


def test_a_centred_key_must_start_more_than_six_points_inside_its_cell() -> None:
    key = ("KKKKKKKK", 4.0, 19.0, 8.0, 4.0)  # 32 pt wide, centre 20 = the cell centre, but it starts 4 pt in
    assert _fine(_centred_pair(_w("AAA", 8.0, 10.0), key), CENTRE_COLS) == [2]


def test_the_cell_inset_is_capped_at_six_points() -> None:
    cols = [(0.0, 40.0), (40.0, 160.0), (160.0, 300.0)]

    def band(key: tuple) -> list[int]:
        return _fine([_w("AAA", 8.0, 10.0), _w("Alphas", 42.0, 10.0), _w("Gammas", 162.0, 10.0), key, _w("Alphas", 42.0, 19.0)], cols)

    assert band(("K" * 10, 8.0, 19.0, 8.0, 1.0)) == [1, 1]  # 10 pt after a 20 pt end fits a 34 pt text edge (inset 6)
    assert band(_w("KKK", 21.0, 19.0)) == [2]  # but 12 pt after it does not fit once the inset is not smaller


def test_a_word_belongs_to_one_column_the_one_holding_its_centre() -> None:
    cols = [(0.0, 40.0), (40.0, 300.0)]

    def band(kx: float) -> list[int]:
        return _fine([_w("Aaa", 2.0, 10.0), _w("Selects.", 52.0, 10.0), _w("K", kx, 19.0), _w("Chooses.", 52.0, 19.0)], cols)

    assert band(38.3) == [2]  # centre 40.3 is in the second column's range: it is not also a key
    assert band(35.0) == [1, 1]  # centre 37: the key column


def test_a_word_in_a_gap_between_columns_goes_to_the_nearest_within_half_a_point() -> None:
    cols = [(0.0, 40.0), (41.5, 300.0)]

    def band(kx: float) -> list[int]:
        return _fine([_w("Aaa", 2.0, 10.0), _w("Selects.", 52.0, 10.0), _w("K", kx, 19.0), _w("Chooses.", 52.0, 19.0)], cols)

    assert band(38.3) == [1, 1]  # centre 40.3: in the gap, nearest the key column, within the slack
    assert band(40.5) == [2]  # centre 42.5: inside the second column


# --- the guards of the continuation and code-run rules, each pinned at its boundary -------

TOKEN_COLS = [(0.0, 62.0), (62.0, 124.0), (124.0, 186.0)]


def _token_rows(rows: list[tuple[str, ...]]) -> list[int]:
    specs = [_w(word, 62.0 * j + 3.0, 10.0 + 9.6 * i) for i, row in enumerate(rows) for j, word in enumerate(row)]
    return _fine(specs, TOKEN_COLS)


def test_a_broken_token_needs_two_other_columns_that_break_too() -> None:
    key1, key2 = "ABCDEFGHIJ_01", "KLMNOPQRST_02"
    assert _token_rows([(key1, "Y" * 13, "Y" * 5), (key2, "W" * 13, "V" * 5)]) == [1, 1]  # one other column full
    assert _token_rows([(key1, "Y" * 13, "Y" * 13), (key2, "W" * 13, "V" * 13)]) == [2]  # two: broken tokens


def test_a_broken_token_must_end_in_a_letter_or_a_digit() -> None:
    key2 = "KLMNOPQRST_02"
    assert _token_rows([("ABCDEFGHIJ_0.", "Y" * 12 + ".", "Y" * 13), (key2, "W" * 13, "V" * 13)]) == [1, 1]
    assert _token_rows([("ABCDEFGHIJ_01", "Y" * 13, "Y" * 13), (key2, "W" * 13, "V" * 13)]) == [2]


def test_a_token_that_is_only_half_the_cell_is_not_a_broken_token() -> None:
    rows = [("KEY_AB01", "OTHER_A1", "OTHER_B1"), ("KEY_AB02", "OTHER_A2", "OTHER_B2")]  # 32 pt of 60
    assert _token_rows(rows) == [1, 1]


def test_two_code_keys_are_not_a_run_when_the_first_fills_its_cell() -> None:
    rows = [("ABCDEFGHIJ_[1]", "Y" * 13, "Y" * 13), ("KLMNOPQRST_02", "W" * 13, "V" * 13)]  # the first is 56 of 60 pt
    assert _token_rows(rows) == [2]
    rows = [("ABCDEFGHIJ_01", "Y" * 13, "Y" * 13), ("KLMNOPQRST_02", "W" * 13, "V" * 13)]
    assert _token_rows(rows) == [2]  # same result, different reason: both columns are broken tokens


def test_a_footnote_letter_is_stripped_only_after_a_digit() -> None:
    cols = [(0.0, 36.0), (36.0, 300.0)]

    def band(key1: str, key2: str) -> list[int]:
        return _fine([_w(key1, 2.0, 10.0), _w("Selects.", 38.0, 10.0), _w(key2, 2.0, 19.0), _w("Alpha.", 38.0, 19.0)], cols)

    assert band("LV1a", "LV2b") == [1, 1]  # digit then a letter: a footnote, still a code
    assert band("LV_Ab", "LV_Bb") == [2]  # a letter then a letter: plain text, no code run


def test_a_continuation_line_is_not_an_orphan_key_for_the_consistency_rule() -> None:
    cols = [(0.0, 90.0), (90.0, 300.0)]
    specs = [_w("REG_A1", 2.0, 10.0), _w("Selects.", 92.0, 10.0),
             _w("_CONT_1", 2.0, 19.0), _w("then", 92.0, 19.0),
             _w("REG_C1", 2.0, 28.0), _w("Chooses.", 92.0, 28.0)]
    assert _fine(specs, cols) == [2, 1]  # the underscore line continues the first record; the third starts one


# --- review findings ---------------------------------------------------------------------

def test_a_thick_filled_border_is_one_rule_not_two() -> None:
    def draw(c: canvas.Canvas) -> list[Fixture]:
        c.setFillColorRGB(0, 0, 0)
        for x in (60.0, 260.0):
            c.rect(x - 0.75, _top(400), 1.5, 100, stroke=0, fill=1)
        for y in (300, 400):
            c.rect(60, _top(y) - 0.75, 200, 1.5, stroke=0, fill=1)
        return []

    pdf, page, _ = make_page(draw)
    try:
        box = (55.0, 295.0, 265.0, 405.0)
        assert ola._vertical_rule_count(page, box) == 2  # each border has an edge on both sides
        assert not ola._has_vertical_rules(page, box)
        assert not ola._is_ruled_grid(page, box, [["a", "b", "c"], ["d", "e", "f"]])
    finally:
        pdf.close()


class _FakeTable:
    def __init__(self, bbox: tuple[float, float, float, float], cells: int) -> None:
        self.bbox = bbox
        self.cells = [object()] * cells


class _FakePage:
    def __init__(self, tables: list[_FakeTable]) -> None:
        self._tables = tables

    def find_tables(self, settings: dict | None = None) -> list[_FakeTable]:
        return self._tables


def test_a_candidate_is_matched_by_its_exact_overlap_with_the_box_before_the_cell_count_breaks_ties() -> None:
    target = (0.0, 0.0, 100.0, 100.0)
    # IoU 0.501 and 0.496 both round to 0.50; the second has more cells
    good = _FakeTable((0.0, 0.0, 100.0, 50.1), cells=4)
    bad = _FakeTable((0.0, 0.0, 100.0, 49.6), cells=40)
    assert round(ola._iou(good.bbox, target), 2) == round(ola._iou(bad.bbox, target), 2)
    with patch.object(ola, "_table_grid_by_words", side_effect=lambda t, **kw: [[t]]):
        grid = ola._extract_table_by_words(_FakePage([bad, good]), None, target)
    assert grid is not None and grid[0][0] is good
    with patch.object(ola, "_table_grid_by_words", side_effect=lambda t, **kw: [[t]]):
        assert ola._extract_table_by_words(_FakePage([bad]), None, target) is None


def test_a_new_record_forgets_the_columns_of_the_record_before() -> None:
    cols = [(0.0, 60.0), (60.0, 110.0), (110.0, 300.0)]
    specs = [("Alpha", 2.0, 10.0), ("Available", 62.0, 10.0), ("Yes.", 112.0, 10.0),
             ("Beta", 2.0, 19.0), ("No.", 112.0, 19.0),
             ("Gamma", 2.0, 28.0), ("normal", 62.0, 28.0), ("Maybe.", 112.0, 28.0)]
    assert _groups(specs, cols) == [1, 1, 1]


def test_a_tall_word_in_another_column_does_not_hide_the_blank_line_between_keys() -> None:
    cols = [(0.0, 30.0), (30.0, 300.0)]
    specs = [_w("Alphabet", 2.0, 10.0), _w("Selects.", 32.0, 10.0), ("TALL", 100.0, 5.0, 18.0, 4.0),
             _w("Betaxyz", 2.0, 27.0), _w("Chooses.", 32.0, 27.0)]
    assert _fine(specs, cols) == [1, 1]


def _table_with_note_page(c: canvas.Canvas) -> list[Fixture]:
    c.setLineWidth(0.6)
    top, rows = 300.0, 12
    for x in (60, 140, 260):
        c.line(x, _top(top), x, _top(top + 20 * rows))
    for r in range(rows + 1):
        c.line(60, _top(top + 20 * r), 260, _top(top + 20 * r))
    c.setFont("Helvetica", 8)
    for r in range(rows):
        c.drawString(64, _top(top + 20 * r + 13), f"K{r}")
        c.drawString(144, _top(top + 20 * r + 13), f"value {r} text")
    c.drawString(64, _top(top + 20 * rows + 9), "NOTE ab")  # a short note 1 pt under the last rule
    return [Fixture((60.0, top, 260.0, top + 20 * rows), [])]


def test_a_note_merged_into_the_table_region_is_kept_as_text() -> None:
    pdf, page, fx = make_page(_table_with_note_page)
    try:
        x0, y0, x1, y1 = fx[0].bbox
        box = ((x0 - 1) / PAGE_W, (y0 - 1) / PAGE_H, (x1 + 1) / PAGE_W, (y1 + 1) / PAGE_H)
        with patch.object(ola, "_detect_model_boxes", return_value={"table": [(box, 0.98)], "picture": []}):
            regions = ola.extract_layout_regions(page)
        tables = [r for r in regions if r.type == LayoutRegionType.TABLE]
        assert len(tables) == 1 and len(tables[0].table_grid) == 12
        assert "NOTE" not in tables[0].text
        assert tables[0].bbox[3] < y1 + 2, "the table region does not reach the note"
        assert any(r.type == LayoutRegionType.TEXT and "NOTE ab" in r.text for r in regions)
    finally:
        pdf.close()


# --- pins for the review fixes (each fails when the named guard is removed) ----------------

def test_the_region_padding_stops_short_of_a_box_on_the_right() -> None:
    own = (60.0, 100.0, 300.0, 200.0)
    box = (50.0, 90.0, 310.0, 260.0)
    right = (303.0, 100.0, 400.0, 200.0)
    assert ola._region_bbox(own, box, [right]) == pytest.approx((58.0, 90.0, 300.0 + 1.0 / 3.0, 202.0))
    far = (330.0, 100.0, 400.0, 200.0)  # farther than the padding: unchanged
    assert ola._region_bbox(own, box, [far]) == (58.0, 90.0, 302.0, 202.0)
    above = (60.0, 0.0, 300.0, 80.0)  # a neighbour above never shortens the top (it reaches the box)
    assert ola._region_bbox(own, box, [above]) == (58.0, 90.0, 302.0, 202.0)


def _two_side_by_side_tables(c: canvas.Canvas) -> list[Fixture]:
    c.setLineWidth(0.6)
    out = []
    for left in (60.0, 163.5):  # the second table starts 3.5 pt right of the first one's last rule
        right = left + 100.0
        for x in (left, left + 40, right):
            c.line(x, _top(300), x, _top(360))
        for r in range(4):
            c.line(left, _top(300 + 20 * r), right, _top(300 + 20 * r))
        c.setFont("Helvetica", 8)
        for r in range(3):
            c.drawString(left + 4, _top(300 + 20 * r + 13), f"K{r}")
            c.drawString(left + 44, _top(300 + 20 * r + 13), f"v {r}")
        out.append(Fixture((left, 300.0, right, 360.0), []))
    return out


def test_two_ruled_tables_a_few_points_apart_never_overlap_in_the_published_regions() -> None:
    pdf, page, fx = make_page(_two_side_by_side_tables)
    try:
        boxes = [model_box((x0 - 0.5, y0 - 0.5, x1 + 0.5, y1 + 0.5)) for x0, y0, x1, y1 in (f.bbox for f in fx)]
        # rows that may hold several records make the model's box re-extract the grid (the padded region)
        with patch.object(ola, "_may_hold_record_lines", return_value=True):
            tables = tables_with_model(page, boxes)
        tables.sort(key=lambda r: r.bbox[0])
        assert len(tables) == 2 and all(len(t.table_grid) == 3 for t in tables)
        assert tables[0].bbox[2] <= tables[1].bbox[0]
    finally:
        pdf.close()


def test_a_ruled_table_with_nothing_outside_its_grid_keeps_the_padded_region() -> None:
    """The region is cut back to the grid only when words would be left out of it."""
    pdf, page, fx = make_page(lambda c: [draw_ruled_table(c, 36, 240)])
    try:
        x0, y0, x1, y1 = fx[0].bbox
        box = ((x0 - 15) / PAGE_W, (y0 - 15) / PAGE_H, (x1 + 15) / PAGE_W, (y1 + 15) / PAGE_H)  # a box 15 pt too big
        with patch.object(ola, "_detect_model_boxes", return_value={"table": [(box, 0.98)], "picture": []}):
            regions = ola.extract_layout_regions(page)
        tables = [r for r in regions if r.type == LayoutRegionType.TABLE]
        assert len(tables) == 1
        assert tables[0].bbox[0] < x0 - 1.0 and tables[0].bbox[2] > x1 + 1.0 and tables[0].bbox[3] > y1 + 1.0
    finally:
        pdf.close()


def test_a_word_centred_exactly_on_the_edge_between_two_columns_belongs_to_the_second() -> None:
    cols = [(0.0, 40.0), (40.0, 300.0)]
    specs = [_w("Aaa", 2.0, 10.0), _w("Selects.", 52.0, 10.0), _w("K", 38.0, 19.0), _w("Chooses.", 52.0, 19.0)]
    assert _fine(specs, cols) == [2]  # "K" spans 38 to 42: its centre, 40.0, opens the second column


def test_a_tall_word_beside_a_key_does_not_hide_the_blank_line_above_it() -> None:
    cols = [(0.0, 30.0), (30.0, 300.0)]
    specs = [_w("Alphabet", 2.0, 10.0), _w("Selects.", 32.0, 10.0), _w("Betaxyz", 2.0, 27.0),
             ("Chooses.", 32.0, 22.0, 16.0, 4.0)]  # a 16 pt word on the key's line, its top above the key's
    assert _fine(specs, cols) == [1, 1]


def _stacked_aliases(third_top: float, extra: tuple = ()) -> list[int]:
    cols = [(0.0, 60.0), (60.0, 140.0)]
    specs = [_w("POR_XY0", 2.0, 10.0), *[_w(*s[:3]) for s in _flow(["Enables", "the", "buffer"], 62.0, 10.0)]]
    specs += [_w("POR_XY1", 2.0, 19.0), *[_w(*s[:3]) for s in _flow(["domain", "bit", "when"], 62.0, 19.0)]]
    specs += [_w("POR_XY2", 2.0, third_top), *[_w(*s[:3]) for s in _flow(["Selects", "one."], 62.0, third_top)]]
    return _fine(specs + list(extra), cols)


def test_stacked_key_lines_are_judged_by_the_key_words_not_by_a_tall_word_beside_them() -> None:
    assert _stacked_aliases(28.0) == [3]  # each key within a line of the one above: one record
    assert _stacked_aliases(37.0) == [2, 1]  # a blank line before the third key: it starts a record
    # a 16 pt word on the third key's line, or on the second's, must not make the gap look closed
    assert _stacked_aliases(37.0, (("Tall", 118.0, 29.0, 16.0, 4.0),)) == [2, 1]
    assert _stacked_aliases(37.0, (("Tall", 118.0, 15.0, 16.0, 4.0),)) == [2, 1]


def test_a_merged_table_region_is_cut_back_to_the_grid_only_when_words_would_be_left_out() -> None:
    pdf, page, fx = make_page(lambda c: [draw_ruled_table(c, 36, 240)])
    try:
        words = page.extract_words()
        x0, y0, x1, y1 = fx[0].bbox
        padded = (x0 - 2.0, y0 - 2.0, x1 + 2.0, y1 + 2.0)
        region = LayoutRegion(type=LayoutRegionType.TABLE, bbox=padded, text="")
        out = ola._reextract_region_payload(region, page, words, PAGE_W, PAGE_H)
        assert tuple(out.bbox) == padded  # every word of the region is in the grid: the box stays
        taller = (x0 - 2.0, y0 - 2.0, x1 + 2.0, y1 + 40.0)  # reaches into the text under the table
        region = LayoutRegion(type=LayoutRegionType.TABLE, bbox=taller, text="")
        words_below = [*words, {**words[0], "text": "Footer", "top": y1 + 10.0, "bottom": y1 + 18.0,
                                "x0": x0 + 4.0, "x1": x0 + 40.0}]
        out = ola._reextract_region_payload(region, page, words_below, PAGE_W, PAGE_H)
        assert out.bbox[3] <= y1 + 0.5  # cut back to the grid; the caller keeps the footer as text
    finally:
        pdf.close()


def test_a_row_whose_first_cell_is_absent_still_splits_by_the_next_column_of_labels() -> None:
    """The first column's cell spans from the row above (absent here); the labels are one per line in the
    next column with their values beside them. Treating the row as one record would merge the labels."""
    cells = [None, (60.0, 0.0, 160.0, 80.0), (160.0, 0.0, 300.0, 80.0)]

    def word(text: str, x0: float, top: float) -> dict:
        w = _word(text, x0, top)
        w["chars"] = [w]
        return w

    labels = [word("Alphabet", 62.0, 10.0), word("Betaxyz", 62.0, 28.0), word("Gammaray", 62.0, 46.0)]
    values = [word("Selects.", 162.0, 10.0), word("Chooses.", 162.0, 28.0), word("Clears.", 162.0, 46.0)]
    bands = ola.split_record_bands([[], labels, values], cells)
    assert bands is not None and len(bands) == 3
    assert [[c["text"] for c in band[2]] for band in bands] == [["Selects."], ["Chooses."], ["Clears."]]


def _table_note_and_list_page(c: canvas.Canvas) -> list[Fixture]:
    c.setLineWidth(0.6)
    top = 300.0
    for x in (60, 140, 260):
        c.line(x, _top(top), x, _top(top + 60))
    for r in range(4):
        c.line(60, _top(top + 20 * r), 260, _top(top + 20 * r))
    c.setFont("Helvetica", 8)
    for r in range(3):
        c.drawString(64, _top(top + 20 * r + 13), f"K{r}")
        c.drawString(144, _top(top + 20 * r + 13), f"value {r}")
    c.drawString(64, _top(372), "- item one")  # a list under the table, kept apart from it
    c.drawString(200, _top(372), "NOTE ab")  # a note that the table absorbs
    return []


def _table_note_and_list_regions() -> list[LayoutRegion]:
    return [
        LayoutRegion(type=LayoutRegionType.TABLE, bbox=(60.0, 300.0, 260.0, 360.0), text=""),
        LayoutRegion(type=LayoutRegionType.TEXT, bbox=(198.0, 362.0, 240.0, 374.0), text="NOTE ab"),
        LayoutRegion(type=LayoutRegionType.LIST, bbox=(62.0, 362.0, 110.0, 374.0), text="- item one"),
    ]


def test_text_cut_back_from_a_table_is_not_repeated_when_a_list_beside_it_owns_it() -> None:
    pdf, page, _ = make_page(_table_note_and_list_page)
    try:
        words = page.extract_words()
        out = ola._resolve_overlaps(_table_note_and_list_regions(), page=page, all_words=words,
                                    page_w=PAGE_W, page_h=PAGE_H)
        texts = [r for r in out if r.type == LayoutRegionType.TEXT]
        assert sum(r.text.count("item one") for r in out if r.type != LayoutRegionType.TABLE) == 1
        assert [r.text for r in texts] == ["NOTE ab"]  # the note, which no region owned, stays as text
        assert len([r for r in out if r.type == LayoutRegionType.LIST]) == 1
    finally:
        pdf.close()


def test_resolving_overlaps_without_a_word_list_keeps_the_table_region_as_it_is() -> None:
    pdf, page, _ = make_page(_table_note_and_list_page)
    try:
        out = ola._resolve_overlaps(_table_note_and_list_regions(), page=page, all_words=None,
                                    page_w=PAGE_W, page_h=PAGE_H)
        assert any(r.type == LayoutRegionType.TABLE for r in out)
    finally:
        pdf.close()


def test_a_key_centred_in_the_gap_between_two_cells_is_judged_in_the_cell_it_is_rendered_into() -> None:
    """Cells (0, 40) and (41.5, 300): a word centred at 40.75 is equally far from both and is
    rendered into the first. The record analysis must put it in the same cell."""
    cells = [(0.0, 0.0, 40.0, 60.0), (41.5, 0.0, 300.0, 60.0)]

    def word(text: str, x0: float, top: float) -> dict:
        w = _word(text, x0, top)
        w["chars"] = [w]
        return w

    keys = [word("Aaa", 2.0, 10.0), word("K", 38.75, 19.0)]  # "K" is 4 pt wide: centre 40.75
    values = [word("Selects.", 52.0, 10.0), word("Chooses.", 52.0, 19.0)]
    bands = ola.split_record_bands([keys, values], cells)
    assert bands is not None and len(bands) == 2
    assert [[c["text"] for c in band[0]] for band in bands] == [["Aaa"], ["K"]]


def test_column_for_puts_a_gap_word_in_the_nearest_column_and_is_the_splitters_default() -> None:
    from app.modules.parsers.pdf.table_rows import column_for

    cols = [(0.0, 40.0), (41.5, 300.0)]
    assert column_for(20.0, cols) == 0 and column_for(100.0, cols) == 1
    assert column_for(40.0, cols) == 0  # in the gap: equally near both, the first wins (as when rendered)
    assert column_for(40.75, cols) == 0
    assert column_for(41.0, cols) == 1  # nearer the second
    assert column_for(300.0, cols) == 1  # the last column holds its right edge
    assert column_for(-5.0, cols) == 0 and column_for(500.0, cols) == 1
    assert column_for(5.0, []) is None
    # the default assignment of the splitter is the same function
    lines = band_lines([_word("Aaa", 2.0, 10.0), _word("Selects.", 52.0, 10.0),
                        _word("K", 38.75, 19.0), _word("Chooses.", 52.0, 19.0)])
    assert [len(g) for g in split_band_records(lines, cols, ruled_cell=True)] == [1, 1]


@pytest.mark.parametrize("gap", [2.5, 3.0, 4.5, 5.0, 5.9])
@pytest.mark.parametrize("side", ["below", "right"])
def test_padding_never_brings_two_ruled_tables_within_the_merge_distance(gap: float, side: str) -> None:
    a = (60.0, 100.0, 300.0, 200.0)
    b = (60.0, 200.0 + gap, 300.0, 300.0) if side == "below" else (300.0 + gap, 100.0, 500.0, 200.0)
    ra = ola._region_bbox(a, a, [b])
    rb = ola._region_bbox(b, b, [a])
    assert max(rb[1] - ra[3], rb[0] - ra[2]) > ola._PROXIMITY_MERGE_GAP
    regions = [LayoutRegion(type=LayoutRegionType.TABLE, bbox=r, text="t", table_grid=[["a", "b"], ["c", "d"]]) for r in (ra, rb)]
    assert len(ola._resolve_overlaps(regions)) == 2
