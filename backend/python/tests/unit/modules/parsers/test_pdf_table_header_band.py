"""Header lines of a borderless table keep their own rows, and statements set side by side are two tables.

Found on a financial-statement PDF: a note table with its years in a header band had no rule between the
header and the first value line, so row 0 read ``['As\\n2012\\nDeferred tax liabilities ...', 'at 31 December\\n2011\\n2,647', '1,994']``.
Every year sat one column left of its values (``2011`` over ``2,647``) and the stored row text said
"2,647 in 2011". A balance sheet and a cash-flow statement printed next to each other were one 11-column grid.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pdfplumber
from reportlab.pdfgen import canvas

from app.modules.parsers.pdf import opencv_layout_analyzer
from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
from app.modules.parsers.pdf.opencv_layout_analyzer import (
    LayoutRegion,
    LayoutRegionType,
    _detect_ruled_row_tables,
    _detect_tables,
    _split_side_by_side_tables,
)
from app.modules.parsers.pdf.pdfplumber_opencv_processor import _plain_row_texts

if TYPE_CHECKING:
    import pytest

PAGE_H = 842.0
FONT = ("Helvetica", 9)


def _page(draw) -> bytes:
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(595.0, PAGE_H))
    c.setFont(*FONT)
    draw(c)
    c.save()
    return out.getvalue()


def _text(c, x: float, top: float, s: str, *, right: bool = False) -> None:
    """Draw ``s`` with its top at ``top`` points from the page top."""
    y = PAGE_H - top - 7.0
    (c.drawRightString if right else c.drawString)(x, y, s)


def _rule(c, x0: float, x1: float, top: float) -> None:
    c.setLineWidth(0.5)
    c.line(x0, PAGE_H - top, x1, PAGE_H - top)


def _tables(pdf: bytes) -> list[list[list[str]]]:
    (page,) = parse_pdf_layout(pdf)
    return [r.table_grid for r in page.regions if r.type == LayoutRegionType.TABLE and r.table_grid]


def _find(grid: list[list[str]], needle: str) -> tuple[int, int]:
    hits = [(ri, ci) for ri, row in enumerate(grid) for ci, cell in enumerate(row) if needle in (cell or "")]
    assert hits, f"{needle!r} is in no cell of {grid}"
    return hits[0]


def _deferred_tax_page(c) -> None:
    _text(c, 72, 300, "The movement in deferred tax is shown in the table below.")
    # Header band: a spanning heading, then the two years left of their right-aligned values.
    _text(c, 408, 387, "As at 31 December")
    _text(c, 389, 408, "2012")
    _text(c, 480, 408, "2011")
    _rule(c, 360, 528, 401)
    _rule(c, 360, 437, 421)
    _rule(c, 451, 528, 421)
    rows = [
        (426, "Deferred tax liabilities to be settled within 12 months", "2,647", "1,994"),
        (440, "Deferred tax liabilities to be settled after more than 12 months", "469", "512"),
        (456, "Total deferred tax liability", "3,116", "2,506"),
    ]
    for top, label, a, b in rows:
        _text(c, 82, top, label)
        _text(c, 435, top, a, right=True)
        _text(c, 526, top, b, right=True)
    _rule(c, 360, 437, 450)
    _rule(c, 451, 528, 450)
    _text(c, 72, 520, "Changes in the balance are as follows:")


class TestHeaderBand:
    def test_year_is_in_the_column_of_its_values(self) -> None:
        grids = _tables(_page(_deferred_tax_page))
        (grid,) = [g for g in grids if any("2,647" in (c or "") for row in g for c in row)]
        assert _find(grid, "2012")[1] == _find(grid, "2,647")[1]
        assert _find(grid, "2011")[1] == _find(grid, "1,994")[1]

    def test_header_is_its_own_row(self) -> None:
        (grid,) = [g for g in _tables(_page(_deferred_tax_page)) if any("2,647" in (c or "") for row in g for c in row)]
        assert _find(grid, "2012")[0] < _find(grid, "2,647")[0]
        for row in grid:
            for cell in row:
                assert not ("2011" in (cell or "") and any(ch.isdigit() for ch in (cell or "").replace("2011", "")))
                assert "2012\n" not in (cell or "")

    def test_wrapped_label_is_not_taken_for_a_header(self) -> None:
        def draw(c) -> None:
            _text(c, 72, 300, "Notes follow.")
            # The label wraps onto a second line; the figures sit on the first.
            _text(c, 82, 400, "Deferred tax liabilities to be")
            _text(c, 82, 411, "settled within 12 months")
            _text(c, 435, 400, "2,647", right=True)
            _text(c, 526, 400, "1,994", right=True)
            for i, (label, a, b) in enumerate([("Other items", "469", "512"), ("Total", "3,116", "2,506")]):
                _text(c, 82, 440 + 14 * i, label)
                _text(c, 435, 440 + 14 * i, a, right=True)
                _text(c, 526, 440 + 14 * i, b, right=True)

        (grid,) = [g for g in _tables(_page(draw)) if any("2,647" in (c or "") for row in g for c in row)]
        (row,) = [r for r in grid if any("2,647" in (c or "") for c in r)]
        assert "Deferred tax liabilities to be" in str(row) and "1,994" in str(row), grid
        assert not any(not any(r[1:]) and "Deferred" in r[0] for r in grid), grid

    def test_wrapped_label_over_the_first_figures_stays_in_their_row(self) -> None:
        """Row 0 of a boxed table holds the years, then a label on two lines with the figures beside the second."""
        with pdfplumber.open(io.BytesIO(_page(lambda c: _boxed_page(c, wrapped=True)))) as doc:
            page = doc.pages[0]
            grids = [r.table_grid for r in _detect_tables(page, page.width, page.height, page.extract_words())]
        (grid,) = [g for g in grids if any("2,647" in (c or "") for row in g for c in row)]
        (row,) = [r for r in grid if any("2,647" in (c or "") for c in r)]
        assert "Liabilities to be settled" in str(row) and "within 12 months" in str(row), grid
        assert _find(grid, "2012")[0] < _find(grid, "2,647")[0]


def _boxed_page(c, *, wrapped: bool = False) -> None:
    """A ruled table: no rule between the years and the first value line, and a sentence between two blocks."""
    _text(c, 72, 340, "Deferred tax balances are shown below.")
    for y in (380, 432, 448, 464, 482, 498, 514, 530):
        _rule(c, 72, 528, y)
    for x in (72, 360, 440, 528):
        c.line(x, PAGE_H - 380, x, PAGE_H - 530)
    _text(c, 389, 386 if wrapped else 392, "2012")
    _text(c, 480, 386 if wrapped else 392, "2011")
    if wrapped:
        _text(c, 82, 404, "Liabilities to be settled")
        first = [(416, "within 12 months", "2,647", "1,994")]
    else:
        first = [(416, "Liabilities within 12 months", "2,647", "1,994")]
    body = [
        *first,
        (436, "Liabilities after 12 months", "469", "512"),
        (452, "Total deferred tax liability", "3,116", "2,506"),
        (486, "Assets within 12 months", "1,290", "1,519"),
        (502, "Assets after 12 months", "396", "816"),
        (518, "Total deferred tax asset", "1,686", "2,335"),
    ]
    _text(c, 82, 469, "The deferred tax assets of the group, which are expected to be recovered, are set out below in full.")
    for top, label, a, b in body:
        _text(c, 82, top, label)
        _text(c, 435, top, a, right=True)
        _text(c, 526, top, b, right=True)


def _check_stacked(grids: list[list[list[str]]]) -> None:
    text = [str(g) for g in grids]
    assert not any("The deferred tax assets" in t for t in text), grids  # the sentence between the blocks is no row
    assert any("Total deferred tax liability" in t and "Liabilities within" in t for t in text), grids
    assert any("Total deferred tax asset" in t and "Assets within" in t for t in text), grids
    assert not any("Liabilities within" in t and "Assets within" in t for t in text), grids


def _two_line_band_page(c) -> None:
    """A boxed table whose row 0 holds a heading over both years, the years, then the first values."""
    for y in (380, 432, 448, 464):
        _rule(c, 72, 528, y)
    for x in (72, 360, 440, 528):
        c.line(x, PAGE_H - 380, x, PAGE_H - 464)
    _text(c, 408, 384, "As at 31 December")
    _text(c, 389, 398, "2012")
    _text(c, 480, 398, "2011")
    for top, label, a, b in [(416, "Liabilities within 12 months", "2,647", "1,994"),
                             (436, "Liabilities after 12 months", "469", "512"),
                             (452, "Total deferred tax liability", "3,116", "2,506")]:
        _text(c, 82, top, label)
        _text(c, 435, top, a, right=True)
        _text(c, 526, top, b, right=True)


class TestHeaderBandIsOneRow:
    def test_heading_and_years_are_one_header_row(self) -> None:
        """Readers take row 0 as the header: a year row under it would be read as data."""
        with pdfplumber.open(io.BytesIO(_page(_two_line_band_page))) as doc:
            page = doc.pages[0]
            grids = [r.table_grid for r in _detect_tables(page, page.width, page.height, page.extract_words())]
        (grid,) = [g for g in grids if any("2,647" in (c or "") for row in g for c in row)]
        assert "As at 31 December" in str(grid[0]) and "2012" in str(grid[0]) and "2011" in str(grid[0]), grid
        assert _find(grid, "2012")[1] == _find(grid, "2,647")[1] and _find(grid, "2,647")[0] == 1, grid


def _ruled_rows_page(c) -> None:
    """Rules between the rows only; the singleton rows below the first are what the row merge re-extracts."""
    for y in (380, 432, 448, 464):
        _rule(c, 72, 528, y)
    for top, label, a, b in [(416, "Liabilities within 12 months", "2,647", "1,994"),
                             (436, "Liabilities after 12 months", "469", "512"),
                             (452, "Total deferred tax liability", "3,116", "2,506")]:
        _text(c, 82, top, label)
        _text(c, 435, top, a, right=True)
        _text(c, 526, top, b, right=True)


class TestHeaderBandInRowMerge:
    def test_the_band_is_not_trimmed_as_a_paragraph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A merged band cell is long ("As at 31 December\n2012"), so the paragraph trim must not see it."""
        band = ["", "As at 31 December\n2012", "2011"]
        real = opencv_layout_analyzer._split_merged_header_band

        def with_band(grid: list[list[str | None]], *args: object) -> list[list[str | None]]:
            out = real(grid, *args)
            return [band, *out] if "Liabilities" in str(out[0]) else out

        monkeypatch.setattr(opencv_layout_analyzer, "_split_merged_header_band", with_band)
        with pdfplumber.open(io.BytesIO(_page(_ruled_rows_page))) as doc:
            page = doc.pages[0]
            singles = [((82.0, 432.0, 526.0, 448.0), [["Liabilities after 12 months", "469", "512"]]),
                       ((82.0, 448.0, 526.0, 464.0), [["Total deferred tax liability", "3,116", "2,506"]])]
            merged = opencv_layout_analyzer._merge_row_tables(singles, page, page.width, page.height)
        (grid,) = [g for _bbox, g in merged]
        assert grid[0] == band, grid
        assert "Total deferred tax liability" in str(grid), grid


class TestHeaderBandStackedTables:
    """The header rows taken out of row 0 must not shift which rows each stacked table takes."""

    def test_find_tables_candidate_is_still_cut_at_a_sentence(self) -> None:
        with pdfplumber.open(io.BytesIO(_page(_boxed_page))) as doc:
            page = doc.pages[0]
            regions = _detect_tables(page, page.width, page.height, page.extract_words())
        _check_stacked([r.table_grid for r in regions])

    def test_ruled_rows_candidate_keeps_every_row(self) -> None:
        with pdfplumber.open(io.BytesIO(_page(_boxed_page))) as doc:
            page = doc.pages[0]
            boxed = page.find_tables()
            page.find_tables = lambda *_a, **_k: boxed  # the ruled-rows pass sees the same candidate
            regions = _detect_ruled_row_tables(page, page.width, page.height, [], [], lambda _b: False)
        _check_stacked([r.table_grid for r in regions])


def _statement(c, x: float, rows: list[tuple[str, str, str]], top: float, step: float) -> None:
    for i, (label, a, b) in enumerate(rows):
        t = top + step * i
        _text(c, x, t, label)
        _text(c, x + 170, t, a, right=True)
        _text(c, x + 220, t, b, right=True)


BALANCE = [
    ("Cash and cash equivalents", "16,881", "25,333"),
    ("Trading assets", "4,160", "10,391"),
    ("Other assets", "2,595", "2,071"),
    ("Total assets", "372,168", "377,325"),
    ("Total liabilities", "273,130", "277,184"),
    ("Total equity", "99,038", "100,141"),
]
CASH_FLOW = [
    ("Net cash from operations", "24,914", "(13,064)"),
    ("Purchases of securities", "(9,362)", "(1,855)"),
    ("Cash dividends", "(5,763)", "(5,401)"),
    ("Net cash from investing", "(19,772)", "1,151"),
    ("Net cash from financing", "(13,741)", "21,419"),
    ("Net increase in cash", "(8,452)", "9,991"),
]


def _split(
    right_top: float, right_step: float, around: bool = False, titles_after: bool = False,
) -> list[LayoutRegion]:
    """Run the side-by-side step on a page of two statements, each with a title text region above it.

    The regions are built by hand: with the table model off (as in unit tests) the titles are read into the grid,
    and the step takes its titles from text set apart from the table, as the model's boxes give them.
    """

    def draw(c) -> None:
        c.setFont("Helvetica-Bold", 12)
        _text(c, 40, 115, "Condensed Balance Sheet")
        _text(c, 310, 115, "Condensed Cash Flow Statement")
        c.setFont(*FONT)
        _statement(c, 40, BALANCE, 150, 14)
        _statement(c, 310, CASH_FLOW, right_top, right_step)

    with pdfplumber.open(io.BytesIO(_page(draw))) as doc:
        page = doc.pages[0]
        regions = [
            LayoutRegion(type=LayoutRegionType.TEXT, bbox=(38, 115, 193, 131), text="Condensed Balance Sheet"),
            LayoutRegion(type=LayoutRegionType.TEXT, bbox=(308, 115, 502, 131), text="Condensed Cash Flow Statement"),
            LayoutRegion(type=LayoutRegionType.TABLE, bbox=(37, 145, 533, 240), text="x", table_grid=[["x", "y"]]),
        ]
        if around:
            # Two-column text above and below, in the order the page's reading order gives a
            # wide table: the left column of the band above, then the right column, then the table.
            above_left = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(38, 60, 260, 100), text="above left")
            above_right = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(308, 60, 530, 100), text="above right")
            below = [LayoutRegion(type=LayoutRegionType.TEXT, bbox=(38, 260, 260, 300), text="below left"),
                     LayoutRegion(type=LayoutRegionType.TEXT, bbox=(308, 260, 530, 300), text="below right")]
            left_title, right_title, table = regions
            regions = [above_left, left_title, above_right, right_title, table, *below]
        if titles_after:
            # A reading order that put the table before its titles.
            regions = [regions[2], regions[0], regions[1]]
        return _split_side_by_side_tables(regions, page, page.extract_words(), page.width, page.height)


class TestSideBySideStatements:
    def test_two_statements_are_two_tables(self) -> None:
        # The right statement has a row pitch and offset of its own, as two statements printed apart do.
        tables = [r for r in _split(right_top=153, right_step=12.5) if r.type == LayoutRegionType.TABLE]
        assert len(tables) == 2, [r.bbox for r in tables]
        left, right = sorted(tables, key=lambda r: r.bbox[0])
        assert "Trading" in str(left.table_grid) and "Purchases" not in str(left.table_grid) and "372,168" in str(left.table_grid)
        assert "Purchases" in str(right.table_grid) and "Total assets" not in str(right.table_grid) and "24,914" in str(right.table_grid)

    def test_each_title_is_read_with_its_own_table(self) -> None:
        # The left title, then its table, then the right title and its table; the text around keeps its place.
        regions = _split(right_top=153, right_step=12.5, around=True)
        assert [(r.type, r.bbox[0] < 285.0) for r in regions] == [
            (LayoutRegionType.TEXT, True), (LayoutRegionType.TEXT, False),
            (LayoutRegionType.TEXT, True), (LayoutRegionType.TABLE, True),
            (LayoutRegionType.TEXT, False), (LayoutRegionType.TABLE, False),
            (LayoutRegionType.TEXT, True), (LayoutRegionType.TEXT, False),
        ], [(r.type, r.bbox) for r in regions]
        assert [r.text for r in regions if r.type == LayoutRegionType.TEXT] == [
            "above left", "above right", "Condensed Balance Sheet", "Condensed Cash Flow Statement",
            "below left", "below right",
        ]

    def test_a_title_read_after_the_table_is_emitted_once(self) -> None:
        regions = _split(right_top=153, right_step=12.5, titles_after=True)
        assert [r.text if r.type == LayoutRegionType.TEXT else (r.bbox[0] < 285.0) for r in regions] == [
            "Condensed Balance Sheet", True, "Condensed Cash Flow Statement", False,
        ], [(r.type, r.bbox) for r in regions]

    def test_a_part_that_loses_words_keeps_the_table_whole(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A part whose grid holds under 90% of the words in its box would drop text the whole table kept.
        monkeypatch.setattr(opencv_layout_analyzer, "_reextract_grid_for_bbox", lambda *_a: [["Cash", "1"], ["Total", "2"]])
        tables = [r for r in _split(right_top=153, right_step=12.5) if r.type == LayoutRegionType.TABLE]
        assert [r.table_grid for r in tables] == [[["x", "y"]]]

    def test_rows_that_line_up_across_the_gutter_are_one_table(self) -> None:
        tables = [r for r in _split(right_top=150, right_step=14) if r.type == LayoutRegionType.TABLE]
        assert len(tables) == 1

    def test_two_figure_columns_under_two_headings_are_not_cut(self) -> None:
        """One table: name, two holding columns, then country and two capital columns; its first line holds two headings."""

        def draw(c) -> None:
            _text(c, 40, 100, "Note 30 Principal subsidiaries")
            _text(c, 40, 130, "Name of subsidiary")
            _text(c, 315, 130, "Country of incorporation")
            rows = [("Alpha Holdings Ltd", "100", "100", "United Kingdom", "12,500", "12,000"),
                    ("Beta Insurance plc", "75", "70", "Ireland", "8,200", "8,100"),
                    ("Gamma Re SA", "51", "51", "Luxembourg", "40,000", "38,500"),
                    ("Delta Services", "100", "90", "Malta", "1,000", "1,000"),
                    ("Epsilon Life", "60", "60", "Cyprus", "6,750", "6,600"),
                    ("Zeta Asset Mgmt", "90", "85", "Jersey", "3,100", "2,900")]
            for i, (name, h1, h2, country, c1, c2) in enumerate(rows):
                top = 150 + 14 * i
                _text(c, 40, top, name)
                _text(c, 220, top, h1, right=True)
                _text(c, 260, top, h2, right=True)
                _text(c, 315, top, country)
                _text(c, 500, top, c1, right=True)
                _text(c, 550, top, c2, right=True)

        grids = _tables(_page(draw))
        assert len(grids) == 1, grids
        row = next(r for r in grids[0] if "Alpha" in str(r))
        assert "United Kingdom" in str(row) and "12,500" in str(row)

    def test_text_columns_under_two_headings_are_not_cut(self) -> None:
        """A schedule of names, holdings, countries and capital: each row spans the gutter."""

        def draw(c) -> None:
            _text(c, 72, 100, "Note 30 Principal subsidiaries")
            _text(c, 72, 130, "Name of subsidiary")
            _text(c, 330, 130, "Country of incorporation")
            rows = [("Alpha Holdings Ltd", "100", "United Kingdom", "12,500"), ("Beta Insurance plc", "75", "Ireland", "8,200"),
                    ("Gamma Re SA", "51", "Luxembourg", "40,000"), ("Delta Services", "100", "Malta", "1,000"),
                    ("Epsilon Life", "60", "Cyprus", "6,750"), ("Zeta Asset Mgmt", "90", "Jersey", "3,100")]
            for i, (name, held, country, capital) in enumerate(rows):
                top = 150 + 14 * i
                _text(c, 72, top, name)
                _text(c, 250, top, held + "%", right=True)
                _text(c, 330, top, country)
                _text(c, 520, top, capital, right=True)

        grids = _tables(_page(draw))
        assert len(grids) == 1, grids
        row = next(r for r in grids[0] if "Alpha" in str(r))
        assert "United Kingdom" in str(row) and "12,500" in str(row)

    def test_one_table_with_a_wide_gap_is_not_cut(self) -> None:
        def draw(c) -> None:
            _text(c, 72, 120, "Quarterly cost per site")
            for i, (label, a, b) in enumerate([("Alpha", "412", "398"), ("Bravo", "377", "381"),
                                                 ("Charlie", "290", "301"), ("Delta", "455", "462"),
                                                 ("Echo", "311", "320")]):
                t = 150 + 14 * i
                _text(c, 72, t, label)
                _text(c, 420, t, a, right=True)
                _text(c, 500, t, b, right=True)

        assert len(_tables(_page(draw))) == 1


def _two_label_columns_page(c) -> None:
    """A label split over two columns, years over right-aligned values: F3's header row has two empty leading cells."""
    _text(c, 72, 300, "Deferred tax assets are shown below.")
    _text(c, 389, 330, "2012")
    _text(c, 480, 330, "2011")
    rows = [
        ("Deferred tax assets", "to be recovered within 12 months", "(3,290)", "(2,519)"),
        ("Deferred tax assets", "to be recovered after more than 12 months", "(2,396)", "(2,816)"),
        ("Total deferred tax", "assets", "(5,686)", "(5,335)"),
    ]
    for i, (a, b, x, y) in enumerate(rows):
        top = 350 + 14 * i
        _text(c, 72, top, a)
        _text(c, 190, top, b)
        _text(c, 435, top, x, right=True)
        _text(c, 526, top, y, right=True)


class TestHeaderBandRowText:
    def test_no_cell_is_lost_from_the_row_text(self) -> None:
        """The header band leaves cells empty; the row text must still hold every cell (it needs unique column keys)."""
        (grid,) = [g for g in _tables(_page(_two_label_columns_page)) if any("(3,290)" in (c or "") for row in g for c in row)]
        assert not any((c or "").strip() for c in grid[0][:2]), grid  # the band's leading cells are empty
        texts = _plain_row_texts([str(c or "") for c in grid[0]], grid[1:])
        for row, text in zip(grid[1:], texts):
            for cell in row:
                if (cell or "").strip():
                    assert cell.strip() in text, (row, text)
