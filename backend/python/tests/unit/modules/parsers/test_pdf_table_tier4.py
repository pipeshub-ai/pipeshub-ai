"""Tier 4: tables with no drawn rows, read from the text's own alignment, replacing a legacy table only."""
from __future__ import annotations

from io import BytesIO

import pdfplumber
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import app.modules.parsers.pdf.opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_reconcile as tr
from app.modules.parsers.pdf import table_tiers as tt
from app.modules.parsers.pdf.table_rows import band_lines

W, H = letter
ROWS = [["Name", "Count", "Remark"]] + [[f"Item{i}", str(i * 10), f"remark number {i}"] for i in range(1, 6)]
XS = [72.0, 180.0, 260.0]
BOX = (66.0, 292.0, 420.0, 392.0)


def borderless() -> tuple[pdfplumber.PDF, pdfplumber.page.Page]:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 9)
    for r, row in enumerate(ROWS):
        for x, t in zip(XS, row):
            c.drawString(x, H - (300 + 16 * r), t)
    c.showPage()
    c.save()
    pdf = pdfplumber.open(BytesIO(buf.getvalue()))
    return pdf, pdf.pages[0]


def legacy_region() -> ola.LayoutRegion:
    return ola.LayoutRegion(type=ola.LayoutRegionType.TABLE, bbox=BOX, table_grid=[["junk"]])


def test_text_alignment_replaces_a_legacy_table_with_the_aligned_grid() -> None:
    pdf, page = borderless()
    try:
        d = tr.reconcile(page, page.extract_words(), [BOX], [legacy_region()])[0]
        assert d.kind == "text_tier"
        assert d.tables[0].table_grid == ROWS
    finally:
        pdf.close()


def test_text_alignment_never_makes_a_table_where_legacy_had_none() -> None:
    pdf, page = borderless()
    try:
        d = tr.reconcile(page, page.extract_words(), [BOX], [])[0]
        assert d.kind == "none"
    finally:
        pdf.close()


def _lines(items: list[tuple[str, float, float]]) -> list[dict]:
    return band_lines([{"text": t, "x0": x, "x1": x + 5 * len(t), "top": top, "bottom": top + 8} for t, x, top in items])


def test_a_wrapped_line_joins_the_row_above_and_uniform_lines_do_not() -> None:
    cuts = [100.0]
    bounds = [(0.0, 100.0), (100.0, 300.0)]
    wrapped = _lines([("Key", 10, 0), ("a long description that", 110, 0), ("continues here", 110, 9), ("Next", 10, 30), ("more", 110, 30)])
    rows = tt.continuation_rows(wrapped, cuts, ruled_band=False, bounds=bounds)
    assert [len(r) for r in rows] == [2, 1]
    uniform = _lines([(f"K{i}", 10, 12.0 * i) for i in range(4)] + [(f"v{i}", 110, 12.0 * i) for i in range(4)])
    assert [len(r) for r in tt.continuation_rows(uniform, cuts, ruled_band=False, bounds=bounds)] == [1, 1, 1, 1]


def test_a_cut_through_a_word_moves_to_a_free_channel_or_fails() -> None:
    lines = _lines([("Alpha", 10, 0), ("Beta", 110, 0), ("Gamma", 10, 12), ("Delta", 110, 12)])
    moved = tt.repair_cuts([13.0], lines, 0.0, 300.0)
    assert moved is not None and 38.0 < moved[0] < 110.0  # in the whitespace between the columns
    assert tt.repair_cuts([13.0], lines[:1], 0.0, 300.0) is not None
    assert tt.repair_cuts([30.0], _lines([("Alphabet", 10, 0), ("Gammaray", 10, 12)]), 0.0, 100.0) is None  # no free channel
