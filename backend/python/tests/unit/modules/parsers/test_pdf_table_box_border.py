"""A layout model's table box that stops just inside a ruled table's border still reads every row and column.

Found live: the box of a 5-row ruled table started 0.3 pt below its top border. Cropped to the box, the border was gone, pdfplumber
merged the header row into the first data row (``['Site\\nAlpha', 'Q1\\n412', ...]``), and the table indexed 4 rows whose text the
model wrote from the merged cells. The same table 12 pt lower on the page, where the box landed on the border, read 5.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pdfplumber
import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.modules.parsers.pdf.opencv_layout_analyzer import (
    _BORDER_SNAP_PT,
    _prefer_model_tables,
    _reextract_grid_for_bbox,
    _snap_to_border,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pdfplumber.page import Page

ROWS = [
    ["Site", "Q1", "Q2", "Q3"],
    ["Alpha", "412", "398", "405"],
    ["Bravo", "377", "381", "366"],
    ["Charlie", "290", "301", "297"],
    ["Delta", "455", "462", "470"],
]
INSIDE = 0.3  # how far inside the border the live box stopped


def _ruled_table_pdf() -> bytes:
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4)
    table = Table(ROWS, colWidths=[90, 70, 70, 70])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.75, colors.black)]))
    body = getSampleStyleSheet()["BodyText"]
    doc.build([Paragraph("The table below lists quarterly handling cost per site.", body), Spacer(1, 12), table])
    return out.getvalue()


@pytest.fixture(scope="module")
def page() -> Iterator[Page]:
    with pdfplumber.open(io.BytesIO(_ruled_table_pdf())) as pdf:
        yield pdf.pages[0]


def _border(page) -> tuple[float, float, float, float]:
    (table,) = page.find_tables()
    return tuple(float(v) for v in table.bbox)


def _read(page, bbox) -> list[list[str]]:
    grid = _reextract_grid_for_bbox(page, bbox, float(page.width), float(page.height))
    assert grid is not None
    return [[(c or "").strip() for c in row] for row in grid]


def _inside(bbox: tuple[float, float, float, float], side: str, by: float = INSIDE) -> tuple[float, float, float, float]:
    x0, top, x1, bottom = bbox
    return {
        "top": (x0, top + by, x1, bottom),
        "bottom": (x0, top, x1, bottom - by),
        "left": (x0 + by, top, x1, bottom),
        "right": (x0, top, x1 - by, bottom),
    }[side]


class TestABoxJustInsideTheBorder:
    def test_the_header_row_stays_its_own_row(self, page) -> None:
        # Kills: cropping to the model's box as it is (the header merges into the first data row, 4 rows).
        assert _read(page, _inside(_border(page), "top")) == ROWS

    @pytest.mark.parametrize("side", ["top", "bottom", "left", "right"])
    def test_every_side_reads_the_whole_table(self, page, side: str) -> None:
        assert _read(page, _inside(_border(page), side)) == ROWS

    def test_the_model_path_builds_the_table_with_every_row(self, page) -> None:
        w, h = float(page.width), float(page.height)
        x0, top, x1, bottom = _inside(_border(page), "top")
        (region,) = _prefer_model_tables(page, w, h, [], page.extract_words(), [((x0 / w, top / h, x1 / w, bottom / h), 0.9)])
        assert [[(c or "").strip() for c in row] for row in region.table_grid] == ROWS

    def test_a_box_on_the_border_is_left_as_it_is(self, page) -> None:
        border = _border(page)
        assert _snap_to_border(page, border) == border
        assert _read(page, border) == ROWS


def _edges(*edges: dict) -> SimpleNamespace:
    return SimpleNamespace(edges=list(edges))


def _h(y: float, x0: float = 100.0, x1: float = 300.0) -> dict:
    return {"orientation": "h", "x0": x0, "x1": x1, "top": y, "bottom": y}


def _v(x: float, top: float = 100.0, bottom: float = 200.0) -> dict:
    return {"orientation": "v", "x0": x, "x1": x, "top": top, "bottom": bottom}


BOX = (100.0, 100.0, 300.0, 200.0)


class TestWhichLinesMoveASide:
    def test_a_border_just_outside_each_side_is_taken_in(self) -> None:
        page = _edges(_h(99.7), _h(200.4), _v(99.6), _v(300.2))
        assert _snap_to_border(page, BOX) == (99.6, 99.7, 300.2, 200.4)

    def test_a_line_farther_out_than_the_snap_distance_is_not(self) -> None:
        # Kills: a box widened to any line above it (a rule under a heading, the next table's border).
        far = 100.0 - _BORDER_SNAP_PT - 1.0
        assert _snap_to_border(_edges(_h(far)), BOX) == BOX

    def test_a_short_line_along_a_side_is_not_a_border(self) -> None:
        # Kills: a side moved to an underline that runs along a fifth of it.
        assert _snap_to_border(_edges(_h(99.7, 100.0, 140.0)), BOX) == BOX

    def test_a_line_inside_the_box_never_shrinks_it(self) -> None:
        assert _snap_to_border(_edges(_h(101.0), _v(299.0)), BOX) == BOX

    def test_a_doubled_border_takes_the_side_to_the_outer_line(self) -> None:
        assert _snap_to_border(_edges(_h(99.0), _h(98.0)), BOX)[1] == 98.0

    def test_a_page_whose_lines_cannot_be_read_keeps_the_box(self) -> None:
        class Broken:
            @property
            def edges(self) -> list:
                raise RuntimeError("bad content stream")

        assert _snap_to_border(Broken(), BOX) == BOX

    def test_a_malformed_edge_is_skipped(self) -> None:
        assert _snap_to_border(_edges({"orientation": "h", "x0": None}, _h(99.7)), BOX)[1] == 99.7
