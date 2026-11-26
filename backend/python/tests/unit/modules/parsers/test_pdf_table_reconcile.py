"""Order of evidence for the tables of a page (``PDF_TABLE_EXTRACTION``): ruled tables, group boxes,
NOTE rows, sub-areas and the classes that are not tables.

PDFs are generated here with reportlab. The layout model is stubbed: the tests hand
``extract_layout_regions`` the model boxes.
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import patch

import pdfplumber
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import app.modules.parsers.pdf.opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_reconcile as tr
from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegionType

if TYPE_CHECKING:
    from collections.abc import Callable

W, H = letter
LEAD = 11.0


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


def mbox(b: tuple[float, float, float, float]) -> tuple[tuple[float, float, float, float], float]:
    return ((b[0] / W, b[1] / H, b[2] / W, b[3] / H), 0.98)


def regions(page, boxes: list, mode: str | None, monkeypatch) -> list:
    if mode is None:
        monkeypatch.delenv(tr.ENV_MODE, raising=False)
    else:
        monkeypatch.setenv(tr.ENV_MODE, mode)
    model = {"table": boxes, "picture": []}
    with patch.object(ola, "_detect_model_boxes", return_value=model):
        return ola.extract_layout_regions(page)


def tables(rs: list) -> list:
    return sorted((r for r in rs if r.type == LayoutRegionType.TABLE), key=lambda r: (r.bbox[1], r.bbox[0]))


def grid_table(c: canvas.Canvas, x0: float, y0: float, xs: list[float], rows: list[list[str]],
               row_h: float = 20.0, hline: bool = True, vline: bool = True, rows_h: list[float] | None = None) -> tuple:
    """A ruled grid: column edges ``xs`` (absolute), one row per entry; returns its bbox."""
    c.setLineWidth(0.6)
    c.setStrokeColorRGB(0, 0, 0)
    hs = rows_h or [row_h] * len(rows)
    ys = [y0]
    for h in hs:
        ys.append(ys[-1] + h)
    if hline:
        for y in ys:
            c.line(xs[0], top(y), xs[-1], top(y))
    else:
        for y in (ys[0], ys[1], ys[-1]):
            c.line(xs[0], top(y), xs[-1], top(y))
    if vline:
        for x in xs:
            c.line(x, top(ys[0]), x, top(ys[-1]))
    c.setFont("Helvetica", 8)
    for r, row in enumerate(rows):
        for k, text in enumerate(row):
            for i, ln in enumerate(text.split("\n")):
                c.drawString(xs[k] + 4, top(ys[r] + 11 + i * LEAD), ln)
    return (xs[0], y0, xs[-1], ys[-1])


ROWS = [["Name", "Value", "Meaning"]] + [[f"Item{i}", f"{i}0", f"Meaning of item {i}"] for i in range(1, 6)]
XS = [60.0, 160.0, 240.0, 420.0]


def simple(c: canvas.Canvas) -> None:
    grid_table(c, 60, 300, XS, ROWS)
    c.setFont("Helvetica", 9)
    c.drawString(60, top(292), "Table 4-1. A caption above the grid")


def true_box(extra_top: float = 0.0, extra_bottom: float = 0.0) -> tuple[float, float, float, float]:
    return (60, 300 - extra_top, 420, 300 + 20 * len(ROWS) + extra_bottom)


# --- the switch -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "legacy", "  LEGACY ", "bogus"])
def test_mode_defaults_to_legacy(value, monkeypatch, caplog) -> None:
    if value is None:
        monkeypatch.delenv(tr.ENV_MODE, raising=False)
    else:
        monkeypatch.setenv(tr.ENV_MODE, value)
    tr._warned.clear()
    with caplog.at_level(logging.WARNING):
        assert tr.table_extraction_mode() == "legacy"
        assert tr.table_extraction_mode() == "legacy"
    warned = [r for r in caplog.records if tr.ENV_MODE in r.getMessage()]
    assert len(warned) == (1 if value == "bogus" else 0)


@pytest.mark.parametrize("value", ["shadow", "evidence", "Evidence"])
def test_mode_values(value, monkeypatch) -> None:
    monkeypatch.setenv(tr.ENV_MODE, value)
    assert tr.table_extraction_mode() == value.lower()


def test_unset_switch_is_the_legacy_golden(monkeypatch) -> None:
    pdf, page = page_of(simple)
    try:
        box = [mbox(true_box(extra_top=14))]
        golden = [(r.type, r.bbox, r.text) for r in regions(page, box, "legacy", monkeypatch)]
        assert golden == [(r.type, r.bbox, r.text) for r in regions(page, box, None, monkeypatch)]
        assert golden == [(r.type, r.bbox, r.text) for r in regions(page, box, "bogus", monkeypatch)]
    finally:
        pdf.close()


def test_shadow_output_is_byte_identical_to_legacy(monkeypatch) -> None:
    pdf, page = page_of(simple)
    try:
        box = [mbox(true_box(extra_top=14))]
        legacy = [(r.type, r.bbox, r.text, r.table_grid) for r in regions(page, box, "legacy", monkeypatch)]
        shadow = [(r.type, r.bbox, r.text, r.table_grid) for r in regions(page, box, "shadow", monkeypatch)]
        assert legacy == shadow
    finally:
        pdf.close()


def test_shadow_does_not_call_apply(monkeypatch) -> None:
    pdf, page = page_of(simple)
    try:
        with patch.object(tr, "apply", side_effect=AssertionError("shadow must not apply")):
            regions(page, [mbox(true_box())], "shadow", monkeypatch)
    finally:
        pdf.close()


# --- tier 1: a ruled table -------------------------------------------------------------------


def test_evidence_keeps_the_ruled_table_and_leaves_the_caption_outside_the_grid(monkeypatch) -> None:
    pdf, page = page_of(simple)
    try:
        ts = tables(regions(page, [mbox(true_box(extra_top=14))], "evidence", monkeypatch))
        assert len(ts) == 1
        t = ts[0]
        assert [len(r) for r in t.table_grid] == [3] * 6
        assert t.table_grid[0] == ["Name", "Value", "Meaning"]
        assert "caption" not in " ".join(c or "" for r in t.table_grid for c in r)
        assert abs(t.bbox[1] - 300) < 2.5  # the ruled table's own extent, not the model box
    finally:
        pdf.close()


def test_legacy_region_reaches_up_to_the_caption(monkeypatch) -> None:
    """Pins the difference the evidence path makes (the legacy region keeps the caption line)."""
    pdf, page = page_of(simple)
    try:
        ts = tables(regions(page, [mbox(true_box(extra_top=14))], "legacy", monkeypatch))
        assert len(ts) == 1 and ts[0].bbox[1] < 296
    finally:
        pdf.close()


def test_ruled_row_with_several_records_is_split_by_the_one_row_rule(monkeypatch) -> None:
    """Column rules only between the header and a single body band: records are cut by split_band_records."""
    rows = [["Reg", "Field", "Description"],
            ["ALPHA\nBRAVO\nCHARLIE", "A\nB\nC", "First one\nSecond one\nThird one"]]

    def draw(c) -> None:
        grid_table(c, 60, 300, XS, rows, rows_h=[20, 60], hline=False)

    pdf, page = page_of(draw)
    try:
        ts = tables(regions(page, [mbox((60, 300, 420, 380))], "evidence", monkeypatch))
        assert len(ts) == 1
        assert [r[0] for r in ts[0].table_grid] == ["Reg", "ALPHA", "BRAVO", "CHARLIE"]
        assert ts[0].table_grid[3] == ["CHARLIE", "C", "Third one"]
    finally:
        pdf.close()


def test_notes_row_is_not_part_of_the_grid(monkeypatch) -> None:
    def draw(c) -> None:
        grid_table(c, 60, 300, [*XS[:3], XS[3]], ROWS[:4])
        # a full-width NOTES row under the grid, drawn as a single ruled cell
        c.line(60, top(380), 420, top(380))
        c.line(60, top(400), 420, top(400))
        c.line(60, top(380), 60, top(400))
        c.line(420, top(380), 420, top(400))
        c.drawString(64, top(391), "NOTES: values are examples")

    pdf, page = page_of(draw)
    try:
        ts = tables(regions(page, [mbox((60, 300, 420, 400))], "evidence", monkeypatch))
        assert len(ts) == 1
        flat = " ".join(c or "" for r in ts[0].table_grid for c in r)
        assert "NOTES" not in flat and len(ts[0].table_grid) == 4
        assert ts[0].bbox[3] < 385  # the extent stops above the notes row
    finally:
        pdf.close()


def test_a_last_row_that_starts_with_a_digit_and_has_two_cells_is_kept(monkeypatch) -> None:
    rows = [*ROWS[:4], ["1. step", "10", "Last real row"]]

    def draw(c) -> None:
        grid_table(c, 60, 300, XS, rows)

    pdf, page = page_of(draw)
    try:
        ts = tables(regions(page, [mbox((60, 300, 420, 400))], "evidence", monkeypatch))
        assert len(ts) == 1 and ts[0].table_grid[-1][0] == "1. step"
    finally:
        pdf.close()


# --- group boxes -----------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["side", "stacked"])
def test_group_box_gives_one_table_per_ruled_table(layout, monkeypatch) -> None:
    def draw(c) -> None:
        if layout == "side":
            grid_table(c, 40, 300, [40, 100, 180], ROWS[:4])
            grid_table(c, 260, 300, [260, 320, 420], ROWS[:4])
        else:
            grid_table(c, 60, 300, XS, ROWS[:4])
            grid_table(c, 60, 400, XS, ROWS[:4])

    pdf, page = page_of(draw)
    try:
        box = (40, 300, 420, 380) if layout == "side" else (60, 300, 420, 480)
        ts = tables(regions(page, [mbox(box)], "evidence", monkeypatch))
        assert len(ts) == 2
        assert all(len(t.table_grid) == 4 for t in ts)
    finally:
        pdf.close()


# --- sub-areas fall through to the legacy path -------------------------------------------------


def test_partial_lattice_makes_no_decision(monkeypatch) -> None:
    """A ruled 4 x 2 block with an unruled third column on its own text lines: the model box covers the
    whole table, the ruled block is only part of it."""
    def draw(c) -> None:
        grid_table(c, 60, 300, [60, 160, 240], [["Name", "Value"]] + [[f"Item{i}", f"{i}0"] for i in range(1, 7)], row_h=18)
        c.setFont("Helvetica", 8)
        for i in range(7):
            c.drawString(262, top(300 + 11 + 18 * i), "Meaning" if i == 0 else f"Meaning of item {i}")

    pdf, page = page_of(draw)
    try:
        words = page.extract_words()
        box = (60, 300, 400, 426)
        ds = tr.reconcile(page, words, [box])
        assert ds[0].kind == "none" and ds[0].reason == "partial lattice"
    finally:
        pdf.close()


def test_a_paragraph_beside_a_ruled_table_is_not_a_sub_area(monkeypatch) -> None:
    def draw(c) -> None:
        grid_table(c, 60, 300, [60, 160, 240], ROWS[:4])
        c.setFont("Helvetica", 8)
        for i in range(5):
            c.drawString(262, top(305 + 14 * i), "Some prose that runs beside the table and goes on")

    pdf, page = page_of(draw)
    try:
        ds = tr.reconcile(page, page.extract_words(), [(60, 300, 420, 380)])
        assert ds[0].kind == "ruled"
    finally:
        pdf.close()


# --- not a table -----------------------------------------------------------------------------


def _text_page(lines: list[tuple[float, str, str]]) -> tuple[pdfplumber.PDF, pdfplumber.page.Page]:
    def draw(c) -> None:
        c.setFont("Helvetica", 9)
        for y, a, b in lines:
            c.drawString(70, top(y), a)
            if b:
                c.drawString(100, top(y), b)

    return page_of(draw)


def test_bulleted_list_is_not_a_table(monkeypatch) -> None:
    pdf, page = _text_page([(300 + 14 * i, "-", f"Point number {i} with a few words more") for i in range(5)])
    try:
        ts = tables(regions(page, [mbox((60, 288, 400, 366))], "evidence", monkeypatch))
        assert ts == []
        d = tr.reconcile(page, page.extract_words(), [(60, 288, 400, 366)])[0]
        assert (d.kind, d.not_table) == ("not_table", "list")
    finally:
        pdf.close()


def test_contents_page_is_not_a_table() -> None:
    pdf, page = _text_page([(300 + 14 * i, f"Section {i}", "." * 30 + f" {i * 3 + 1}") for i in range(5)])
    try:
        d = tr.reconcile(page, page.extract_words(), [(60, 288, 400, 366)])[0]
        assert (d.kind, d.not_table) == ("not_table", "toc")
    finally:
        pdf.close()


def test_tagged_prose_is_not_a_table_but_a_term_list_is() -> None:
    long = "text that explains the rule in a good many words so that it counts as prose"

    def tagged(c) -> None:
        c.setFont("Helvetica", 8)
        for i, tag in enumerate(["RULE_A1", "RULE_B2", "RULE_C3"]):
            c.drawString(70, top(300 + 30 * i), tag)
            c.drawString(150, top(300 + 30 * i), long)

    pdf, page = page_of(tagged)
    try:
        d = tr.reconcile(page, page.extract_words(), [(60, 288, 520, 380)])[0]
        assert (d.kind, d.not_table) == ("not_table", "margin_prose")
    finally:
        pdf.close()

    def terms(c) -> None:
        c.setFont("Helvetica", 8)
        for i, term in enumerate(["apple", "banana", "cherry"]):
            c.drawString(70, top(300 + 30 * i), term)
            c.drawString(150, top(300 + 30 * i), long)

    pdf, page = page_of(terms)
    try:
        d = tr.reconcile(page, page.extract_words(), [(60, 288, 520, 380)])[0]
        assert d.kind == "none"
    finally:
        pdf.close()


def test_not_table_box_drops_the_legacy_table_and_blocks_the_aligned_fallback(monkeypatch) -> None:
    pdf, page = _text_page([(300 + 14 * i, "-", f"Point number {i} with a few words more") for i in range(5)])
    try:
        assert tables(regions(page, [mbox((60, 288, 400, 366))], "evidence", monkeypatch)) == []
    finally:
        pdf.close()


# --- floor ---------------------------------------------------------------------------------


def test_ruled_table_whose_text_runs_across_the_columns_is_not_evidence() -> None:
    """Column rules drawn through running text: not the table's grid."""
    def draw(c) -> None:
        c.setFont("Helvetica", 8)
        for i in range(6):
            c.drawString(62, top(311 + 14 * i), "A long sentence of prose that runs across the whole width of the box here")
        c.setLineWidth(0.6)
        for x in (60, 180, 300, 420):
            c.line(x, top(300), x, top(390))
        for y in (300, 390):
            c.line(60, top(y), 420, top(y))
        c.line(60, top(320), 420, top(320))

    pdf, page = page_of(draw)
    try:
        d = tr.reconcile(page, page.extract_words(), [(60, 300, 420, 390)])[0]
        assert d.kind != "ruled"
    finally:
        pdf.close()


def test_page_evidence_failure_keeps_the_legacy_tables(monkeypatch) -> None:
    pdf, page = page_of(simple)
    try:
        box = [mbox(true_box())]
        legacy = [(r.type, r.bbox, r.text) for r in regions(page, box, "legacy", monkeypatch)]
        with patch.object(tr, "page_rules", side_effect=RuntimeError("boom")):
            got = [(r.type, r.bbox, r.text) for r in regions(page, box, "evidence", monkeypatch)]
        assert got == legacy
    finally:
        pdf.close()


def test_ruled_table_that_is_a_bullet_list_is_not_evidence() -> None:
    rows = [["-", f"Point number {i} with a few words more"] for i in range(5)]

    def draw(c) -> None:
        grid_table(c, 60, 300, [60, 80, 400], rows, row_h=16)

    pdf, page = page_of(draw)
    try:
        d = tr.reconcile(page, page.extract_words(), [(60, 300, 400, 380)])[0]
        assert d.kind != "ruled"
    finally:
        pdf.close()


def test_ruled_table_whose_grid_loses_text_is_not_evidence() -> None:
    pdf, page = page_of(simple)
    try:
        with patch.object(ola, "_grid_char_coverage", return_value=0.5):
            d = tr.reconcile(page, page.extract_words(), [true_box()])[0]
        assert d.kind == "none" and d.reason == "lossy"
    finally:
        pdf.close()


def test_words_of_a_not_table_box_are_kept_from_the_aligned_table_fallback(monkeypatch) -> None:
    pdf, page = _text_page([(300 + 14 * i, "-", f"Point number {i} with a few words more") for i in range(5)])
    seen: list = []
    real = ola._detect_aligned_tables

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["existing"])
        return real(*args, **kwargs)

    try:
        with patch.object(ola, "_detect_aligned_tables", side_effect=spy):
            regions(page, [mbox((60, 288, 400, 366))], "evidence", monkeypatch)
        assert any(r.bbox == (60.0, 288.0, 400.0, 366.0) or tuple(round(v) for v in r.bbox) == (60, 288, 400, 366)
                   for r in seen[0])
    finally:
        pdf.close()


def _callout_below(c) -> None:
    grid_table(c, 60, 300, XS, ROWS[:4])
    c.line(60, top(380), 420, top(380))
    c.line(60, top(420), 420, top(420))
    c.line(60, top(380), 60, top(420))
    c.line(420, top(380), 420, top(420))
    c.drawString(64, top(395), "IMPORTANT: a callout the ruled finder joins to the table")


def test_rows_below_the_model_box_are_not_in_the_grid(monkeypatch) -> None:
    pdf, page = page_of(_callout_below)
    try:
        ts = tables(regions(page, [mbox((60, 300, 420, 380))], "evidence", monkeypatch))
        assert len(ts) == 1
        assert len(ts[0].table_grid) == 4 and "IMPORTANT" not in " ".join(c or "" for r in ts[0].table_grid for c in r)
        assert ts[0].bbox[3] < 384
    finally:
        pdf.close()


def test_rows_inside_the_model_box_stay_in_the_grid(monkeypatch) -> None:
    pdf, page = page_of(_callout_below)
    try:
        ts = tables(regions(page, [mbox((60, 300, 420, 420))], "evidence", monkeypatch))
        assert len(ts) == 1 and len(ts[0].table_grid) == 5
    finally:
        pdf.close()


def test_notes_row_is_cut_when_a_band_of_the_table_was_split(monkeypatch) -> None:
    rows = [["Reg", "Field", "Description"],
            ["ALPHA\nBRAVO\nCHARLIE", "A\nB\nC", "First one\nSecond one\nThird one"],
            ["DELTA", "D", "Fourth one"]]

    def draw(c) -> None:
        grid_table(c, 60, 300, XS, rows, rows_h=[20, 60, 20], hline=True)
        c.line(60, top(420), 420, top(420))
        c.line(60, top(440), 420, top(440))
        c.line(60, top(420), 60, top(440))
        c.line(420, top(420), 420, top(440))
        c.drawString(64, top(431), "NOTE: split bands and a notes row")

    pdf, page = page_of(draw)
    try:
        ts = tables(regions(page, [mbox((60, 300, 420, 440))], "evidence", monkeypatch))
        assert len(ts) == 1
        flat = " ".join(c or "" for r in ts[0].table_grid for c in r)
        assert "NOTE" not in flat and "DELTA" in flat
    finally:
        pdf.close()


def test_legacy_table_in_the_box_but_outside_the_ruled_table_stays() -> None:
    from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegion

    ruled = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(60, 300, 420, 380), table_grid=[["a", "b"], ["c", "d"]])
    other = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(100, 440, 400, 520), table_grid=[["x", "y"], ["z", "w"]])
    old = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(60, 300, 420, 400), table_grid=[["old"]])
    d = tr.Decision((60, 300, 420, 540), "ruled", [ruled])
    out, suppressed = tr.apply([old, other], [d])
    assert other in out and old not in out and ruled in out and suppressed == []


def test_a_table_boxed_in_two_parts_is_one_ruled_table_and_not_cut(monkeypatch) -> None:
    pdf, page = page_of(lambda c: grid_table(c, 60, 300, XS, ROWS))
    try:
        upper, lower = (60, 300, 420, 360), (60, 360, 420, 420)
        ds = tr.reconcile(page, page.extract_words(), [upper, lower])
        assert [d.kind for d in ds].count("ruled") == 1
        ruled = next(d for d in ds if d.kind == "ruled")
        assert len(ruled.tables[0].table_grid) == len(ROWS)
        assert next(d for d in ds if d.kind != "ruled").reason == "ruled table used by another box"
    finally:
        pdf.close()


def _shaded_by_line(c: canvas.Canvas) -> None:
    """Two records; the key cell spans each record, the right column carries a rule under every text line."""
    xs = [60.0, 160.0, 420.0]
    c.setLineWidth(0.6)
    ys = [300.0, 320.0, 380.0, 440.0]
    for y in ys:
        c.line(xs[0], top(y), xs[2], top(y))
    for x in xs:
        c.line(x, top(ys[0]), x, top(ys[-1]))
    c.setFont("Helvetica", 8)
    c.drawString(64, top(313), "Tasks")
    c.drawString(164, top(313), "Outcomes")
    for rec, (y0, key) in enumerate(((320.0, "TASK I-1"), (380.0, "TASK I-2"))):
        c.drawString(64, top(y0 + 12), key)
        for i in range(5):
            y = y0 + 12 * (i + 1)
            c.drawString(164, top(y - 2), f"Outcome line {i} of record {rec}")
            if i < 4:
                c.line(xs[1], top(y), xs[2], top(y))


def test_a_table_shaded_line_by_line_is_read_with_its_real_rows(monkeypatch) -> None:
    pdf, page = page_of(_shaded_by_line)
    try:
        assert len(page.find_tables()[0].rows) > 6  # the finder sees a row at every line
        ts = tables(regions(page, [mbox((60, 300, 420, 440))], "evidence", monkeypatch))
        assert len(ts) == 1
        g = ts[0].table_grid
        assert [len(r) for r in g] == [2, 2, 2]
        assert g[1][0].startswith("TASK I-1") and "record 0" in g[1][1] and "record 1" not in g[1][1]
        assert g[2][0].startswith("TASK I-2") and "Outcome line 4 of record 1" in g[2][1]
    finally:
        pdf.close()


def test_a_table_with_one_stray_row_edge_is_not_redrawn(monkeypatch) -> None:
    def draw(c: canvas.Canvas) -> None:
        grid_table(c, 60, 300, XS, ROWS[:4])
        c.line(160, top(310), 240, top(310))  # one short rule inside a cell

    pdf, page = page_of(draw)
    try:
        t = page.find_tables()[0]
        ws = page.extract_words()
        h, v = tr.page_rules(page)
        assert tr.drawn_rows_and_columns(t, ws, h, v) is None
    finally:
        pdf.close()


def _region(kind: LayoutRegionType, bbox: tuple[float, float, float, float], text: str = "", *, evidence: bool = False) -> ola.LayoutRegion:
    return ola.LayoutRegion(type=kind, bbox=bbox, text=text, from_evidence=evidence)


def test_a_caption_line_is_never_folded_into_a_table_read_by_the_order_of_evidence() -> None:
    table_box = (60.0, 300.0, 420.0, 400.0)
    caption_box = (60.0, 284.0, 300.0, 296.0)
    for gate in (ola._table_proximity_neighbor_allowed, ola._table_overlap_with_large_neighbor_allowed):
        legacy_table = _region(LayoutRegionType.TABLE, table_box)
        evidence_table = _region(LayoutRegionType.TABLE, table_box, evidence=True)
        caption = _region(LayoutRegionType.TEXT, caption_box, "Table 5-54. Boot parameters (continued)")
        body = _region(LayoutRegionType.TEXT, (60.0, 402.0, 100.0, 410.0), "Register values are listed below.")
        above = _region(LayoutRegionType.TEXT, caption_box, "PSHUFLW Shuffle Packed Low Words")
        assert gate(legacy_table, caption)  # the legacy flow folds it in
        assert not gate(evidence_table, caption) and not gate(caption, evidence_table)
        assert not gate(evidence_table, above)  # nor any text ending above the table: the header is read from the grid
        assert gate(evidence_table, body)  # other text is not affected


def _key_spans_rows_with_row_rules_right_of_it(c: canvas.Canvas) -> None:
    """A key cell spans four rows; the rules between the rows start at the second column; every row has text in two columns."""
    xs = [60.0, 160.0, 240.0, 420.0]
    c.setLineWidth(0.6)
    for y in (300, 480):
        c.line(xs[0], top(y), xs[3], top(y))
    for y in (330, 360, 390, 420, 450):
        c.line(xs[1], top(y), xs[3], top(y))
    for x in xs:
        c.line(x, top(300), x, top(480))
    c.setFont("Helvetica", 8)
    c.drawString(64, top(315), "Group A")
    for i in range(6):
        c.drawString(164, top(315 + 30 * i), f"Value {i}")
        c.drawString(244, top(315 + 30 * i), f"Description of value {i}")


def test_a_table_whose_rows_have_text_in_several_columns_is_not_redrawn(monkeypatch) -> None:
    pdf, page = page_of(_key_spans_rows_with_row_rules_right_of_it)
    try:
        t = max(page.find_tables(), key=lambda t: t.bbox[3] - t.bbox[1])
        assert len(t.rows) >= 6
        h, v = tr.page_rules(page)
        own = [w for w in page.extract_words() if tr._in_box(w, tuple(t.bbox))]
        assert tr.drawn_rows_and_columns(t, own, h, v) is None
        ts = [r for r in regions(page, [mbox((60, 300, 420, 480))], "evidence", monkeypatch) if r.type == LayoutRegionType.TABLE]
        assert len(ts) == 1 and len(ts[0].table_grid) >= 6
    finally:
        pdf.close()


def _title_in_the_header_band(c: canvas.Canvas) -> None:
    xs = [60.0, 160.0, 240.0, 420.0]
    c.setLineWidth(0.6)
    for y in (300, 330, 350, 370, 390):
        c.line(xs[0], top(y), xs[3], top(y))
    for x in xs:
        c.line(x, top(300), x, top(390))
    c.setFont("Helvetica", 8)
    c.drawString(64, top(309), "PSHUFLW Shuffle Packed Low Words")  # a title running over two cells
    for x, t in zip((64, 164, 244), ("Opcode", "Op/En", "Description")):
        c.drawString(x, top(322), t)
    for r in range(3):
        for x, t in zip((64, 164, 244), (f"Row{r}", "A", f"Does thing {r}")):
            c.drawString(x, top(343 + 20 * r), t)


def test_a_title_line_set_in_the_header_band_is_not_a_header_label(monkeypatch) -> None:
    pdf, page = page_of(_title_in_the_header_band)
    try:
        ts = [r for r in regions(page, [mbox((60, 300, 420, 390))], "evidence", monkeypatch) if r.type == LayoutRegionType.TABLE]
        assert len(ts) == 1
        assert ts[0].table_grid[0] == ["Opcode", "Op/En", "Description"]
        assert [r[0] for r in ts[0].table_grid[1:]] == ["Row0", "Row1", "Row2"]
    finally:
        pdf.close()


def test_a_header_row_read_as_several_grid_rows_keeps_its_grid_and_drops_no_title() -> None:
    # Row 0 read as a header band plus its first line (row_of [0, 0, ...]): the title check does not apply.
    grid = [["Year", "2012"], ["Total", "2,647"], ["Other", "1"]]
    assert tr.drop_title_line(None, grid, [0, 0, 1], []) == (grid, [])  # type: ignore[arg-type]
