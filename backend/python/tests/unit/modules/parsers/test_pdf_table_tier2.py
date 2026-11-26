"""Tier 2: tables whose rows are drawn by horizontal rules and whose columns come from the text."""
from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import patch

import pdfplumber
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import app.modules.parsers.pdf.opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_reconcile as tr
from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegionType

if TYPE_CHECKING:
    from collections.abc import Callable

W, H = letter


def top(y: float) -> float:
    return H - y


def page_of(draw: Callable[[canvas.Canvas], None]) -> tuple[pdfplumber.PDF, pdfplumber.page.Page]:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    draw(c)
    c.showPage()
    c.save()
    pdf = pdfplumber.open(BytesIO(buf.getvalue()))
    return pdf, pdf.pages[0]


def evidence_tables(page: pdfplumber.page.Page, box: tuple[float, float, float, float], monkeypatch) -> list:
    """The tables of the page in evidence mode; the box's decision must be tier 2 (not the legacy fallback)."""
    monkeypatch.setenv(tr.ENV_MODE, "evidence")
    model = {"table": [((box[0] / W, box[1] / H, box[2] / W, box[3] / H), 0.98)], "picture": []}
    decisions: list = []
    real = tr.reconcile

    def recording(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        decisions.extend(out := real(*args, **kwargs))
        return out

    with patch.object(ola, "_detect_model_boxes", return_value=model), patch.object(tr, "reconcile", recording):
        rs = ola.extract_layout_regions(page)
    assert [d.kind for d in decisions] == ["ruled_rows"], [(d.kind, d.reason) for d in decisions]
    return sorted((r for r in rs if r.type == LayoutRegionType.TABLE), key=lambda r: r.bbox[1])


def hrule(c: canvas.Canvas, y: float, x0: float = 60.0, x1: float = 440.0) -> None:
    c.setLineWidth(0.6)
    c.line(x0, top(y), x1, top(y))


def test_a_table_ruled_only_across_gets_its_columns_from_the_text(monkeypatch) -> None:
    xs = [64.0, 164.0, 264.0]
    rows = [["Name", "Value", "Unit"]] + [[f"Row{i}", f"{i}0", "mm"] for i in range(1, 5)]

    def draw(c: canvas.Canvas) -> None:
        for y in (300, 320, 400):
            hrule(c, y)
        c.setFont("Helvetica", 8)
        for r, row in enumerate(rows):
            for x, t in zip(xs, row):
                c.drawString(x, top(313 + 20 * r - (0 if r == 0 else 0)), t)

    pdf, page = page_of(draw)
    try:
        ts = evidence_tables(page, (60, 296, 440, 404), monkeypatch)
        assert len(ts) == 1
        g = ts[0].table_grid
        assert g[0] == ["Name", "Value", "Unit"]
        assert [r[0] for r in g] == ["Name", "Row1", "Row2", "Row3", "Row4"]
        assert all(len(r) == 3 for r in g)
    finally:
        pdf.close()


def test_separately_ruled_key_cells_beside_one_description_are_one_row_each(monkeypatch) -> None:
    """TC-44: a rule spanning only the key column's text closes that key's cell."""
    def draw(c: canvas.Canvas) -> None:
        for y in (300, 320, 420):
            hrule(c, y)
        hrule(c, 368, 60, 150)  # closes the cell of ALPHA
        c.setFont("Helvetica", 8)
        c.drawString(64, top(313), "Key")
        c.drawString(204, top(313), "Meaning")
        c.drawString(64, top(335), "ALPHA")
        c.drawString(64, top(380), "BRAVO")
        for y in (335, 347, 359, 395, 407):  # nothing beside BRAVO (380): the record rule cannot tell it from a wrap
            c.drawString(204, top(y), f"description at {y} of the shared text")

    pdf, page = page_of(draw)
    try:
        ts = evidence_tables(page, (60, 296, 440, 424), monkeypatch)
        assert len(ts) == 1
        g = ts[0].table_grid
        assert [r[0] for r in g] == ["Key", "ALPHA", "BRAVO"]
        assert "at 335" in g[1][1] and "at 359" in g[1][1] and "at 395" in g[2][1]
    finally:
        pdf.close()


def test_header_with_no_rule_above_it_is_not_merged_with_the_first_row(monkeypatch) -> None:
    """TC-45: the first rule is under the header; text above it is the header, the band under it is body."""
    xs = [64.0, 164.0, 264.0]

    def draw(c: canvas.Canvas) -> None:
        for y in (322, 360, 398):
            hrule(c, y)
        c.setFont("Helvetica", 8)
        for x, t in zip(xs, ("Bits", "Field", "Meaning")):
            c.drawString(x, top(312), t)
        for k, y in enumerate((338, 376)):
            for x, t in zip(xs, (f"[{k}]", f"F{k}", f"What F{k} does")):
                c.drawString(x, top(y), t)

    pdf, page = page_of(draw)
    try:
        ts = evidence_tables(page, (60, 300, 440, 402), monkeypatch)
        assert len(ts) == 1
        assert ts[0].table_grid[0] == ["Bits", "Field", "Meaning"]
        assert ts[0].table_grid[1][0] == "[0]" and ts[0].table_grid[2][0] == "[1]"
    finally:
        pdf.close()


def test_a_group_label_over_two_columns_stays_whole_in_its_first_column(monkeypatch) -> None:
    """TC-46: a label crossing a cut belongs to the column it starts in, and does not stop the cut."""
    xs = [64.0, 164.0, 264.0, 364.0]

    def draw(c: canvas.Canvas) -> None:
        for y in (300, 334, 410):
            hrule(c, y)
        c.setFont("Helvetica", 8)
        c.drawString(xs[1] - 10, top(309), "Group label over the two")
        c.drawString(xs[1] - 10, top(321), "middle columns of this table")
        for k in range(3):
            for x, t in zip(xs, (f"R{k}", f"a{k}", f"b{k}", f"c{k}")):
                c.drawString(x, top(350 + 22 * k), t)

    pdf, page = page_of(draw)
    try:
        ts = evidence_tables(page, (60, 296, 440, 414), monkeypatch)
        assert len(ts) == 1
        g = ts[0].table_grid
        assert [len(r) for r in g] == [4] * len(g)
        head = " ".join(g[0]) if len(g) == 4 else " ".join(g[0] + g[1])
        assert "Group label over the two" in head
    finally:
        pdf.close()


def test_a_single_column_of_text_between_rules_is_not_made_a_table(monkeypatch) -> None:
    def draw(c: canvas.Canvas) -> None:
        for y in (300, 330, 400):
            hrule(c, y)
        c.setFont("Helvetica", 8)
        for i in range(6):
            c.drawString(64, top(312 + 12 * i), "a line of prose that fills the width of the page completely")

    pdf, page = page_of(draw)
    try:
        d = tr.reconcile(page, page.extract_words(), [(60, 296, 440, 404)])[0]
        assert d.kind == "none"
    finally:
        pdf.close()



def test_a_header_with_no_rule_above_it_does_not_swallow_the_first_body_row(monkeypatch) -> None:
    """H1: when the text above the first rule is the header, the band under the rule is body even if its first row looks
    like a wrap of the long labels above it."""
    xs = [64.0, 164.0, 264.0]

    def draw(c: canvas.Canvas) -> None:
        for y in (314, 350, 386):
            hrule(c, y)
        c.setFont("Helvetica", 8)
        for x, t in zip(xs, ("Quantity-label-xyz", "Another-long-label", "Third-long-label-ab")):
            c.drawString(x, top(308), t)
        for k, y in enumerate((332, 368)):
            for x, t in zip(xs, (f"R{k}", f"v{k}", f"w{k}")):
                c.drawString(x, top(y), t)

    pdf, page = page_of(draw)
    try:
        ts = evidence_tables(page, (60, 296, 440, 390), monkeypatch)
        assert len(ts) == 1
        g = ts[0].table_grid
        assert g[0][0].startswith("Quantity") and g[1][0] == "R0" and g[2][0] == "R1", g
    finally:
        pdf.close()


def test_a_repaired_cut_passes_the_same_gap_check_as_the_cut_it_replaces() -> None:
    from app.modules.parsers.pdf import table_tiers
    from app.modules.parsers.pdf.table_rows import band_lines

    def w(text: str, x0: float, x1: float) -> dict:
        return {"text": text, "x0": x0, "x1": x1, "top": 0.0, "bottom": 10.0}

    body = band_lines([w("left", 0.0, 10.0), w("right", 15.0, 25.0)])  # a 5 pt gap: under the 8 pt minimum
    assert table_tiers.repair_cuts([13.0], body, 0.0, 100.0) is None
    wide = band_lines([w("left", 0.0, 10.0), w("right", 30.0, 40.0)])
    assert table_tiers.repair_cuts([12.0], wide, 0.0, 100.0) == [12.0]


def test_with_the_header_above_the_first_rule_the_first_band_is_checked_like_the_body() -> None:
    from app.modules.parsers.pdf import table_tiers
    from app.modules.parsers.pdf.table_rows import band_lines

    def w(text: str, x0: float, x1: float, top: float) -> dict:
        return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 8.0}

    words = [w("Name", 2, 30, 6), w("Value", 100, 130, 6),  # the header, above the first rule (y=20)
             w("Alpha", 2, 30, 22), w("one", 100, 120, 22),
             w("Beta", 2, 30, 46), w("two", 100, 120, 46),
             w("Gamma", 2, 30, 66), w("three", 100, 125, 66)]
    ys = [20.0, 40.0, 60.0, 80.0]
    for band_top in (31.0, 51.0):  # a word across the column cut, in the first body band and in the second
        cand, why = table_tiers.ruled_rows((0, 0, 200, 80), ys, band_lines([*words, w("spanning", 20, 110, band_top)]), [])
        assert cand is None and why == "cut through words", band_top


def test_a_line_centred_near_a_row_rule_is_in_one_row_only() -> None:
    from app.modules.parsers.pdf import table_tiers
    from app.modules.parsers.pdf.table_rows import band_lines

    def w(text: str, x0: float, x1: float, top: float) -> dict:
        return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 8.0}

    words = [w("Name", 2, 30, 6), w("Value", 100, 130, 6),
             w("Alpha", 2, 30, 26), w("one", 100, 120, 26),
             w("Beta", 2, 30, 36.5), w("two", 100, 120, 36.5),  # centre 40.5: half a point below the rule at 40
             w("Gamma", 2, 30, 66), w("three", 100, 125, 66)]
    cand, why = table_tiers.ruled_rows((0, 0, 200, 80), [20.0, 40.0, 60.0, 80.0], band_lines(words), [])
    assert cand is not None, why
    flat = [c for row in cand.grid for c in row if c]
    assert sum(c.count("Beta") for c in flat) == 1
