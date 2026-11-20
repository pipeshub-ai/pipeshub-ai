"""Tier 4: tables with no drawn rows, read from the text's own alignment, replacing a legacy table only."""
from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pdfplumber
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import app.modules.parsers.pdf.opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_quality as tq
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


def broken_legacy(page: pdfplumber.page.Page) -> tq.Cand:
    """A legacy grid whose cuts run through the words of the first column."""
    words = [w for w in page.extract_words() if tr._in_box(w, BOX)]
    return tq.candidate_from_cuts("legacy", 0, [[ln] for ln in band_lines(words)], [XS[0] + 6.0, XS[1] + 6.0], BOX)


def test_text_alignment_replaces_a_defective_legacy_table_with_the_aligned_grid() -> None:
    pdf, page = borderless()
    try:
        with patch.object(tr, "legacy_candidate", return_value=broken_legacy(page)):
            d = tr.reconcile(page, page.extract_words(), [BOX], [legacy_region()])[0]
        assert d.kind == "text_tier"
        assert d.tables[0].table_grid == ROWS
    finally:
        pdf.close()


def test_a_legacy_table_without_strong_defects_is_kept_however_good_the_new_grid() -> None:
    pdf, page = borderless()
    try:
        good = tq.candidate_from_cuts("legacy", 0, [[ln] for ln in band_lines([w for w in page.extract_words() if tr._in_box(w, BOX)])], [150.0, 240.0], BOX)
        with patch.object(tr, "legacy_candidate", return_value=good):
            d = tr.reconcile(page, page.extract_words(), [BOX], [legacy_region()])[0]
        assert d.kind == "none" and d.reason.startswith("legacy kept")
    finally:
        pdf.close()


def test_a_legacy_grid_that_cannot_be_re_read_is_kept() -> None:
    pdf, page = borderless()
    try:
        d = tr.reconcile(page, page.extract_words(), [BOX], [legacy_region()])[0]
        assert d.kind == "none" and "cannot be re-read" in d.reason
    finally:
        pdf.close()


def test_replacement_needs_a_strong_defect_and_the_margin() -> None:
    assert tq.replaces_legacy(0.4, 0.9, strong_defect=True)
    assert not tq.replaces_legacy(0.4, 0.9, strong_defect=False)
    assert not tq.replaces_legacy(0.4, 0.4 + tq.DNH_MARGIN - 0.01, strong_defect=True)
    assert tq.replaces_legacy(0.4, 0.4 + tq.DNH_MARGIN, strong_defect=True)


def test_defects_see_words_split_by_a_cut_and_fragment_rows() -> None:
    pdf, page = borderless()
    try:
        words = [w for w in page.extract_words() if tr._in_box(w, BOX)]
        bad = broken_legacy(page)
        assert tq.defects(bad, words, [], [], BOX)["strong"]
        ok = tq.candidate_from_cuts("ok", 0, [[ln] for ln in band_lines(words)], [150.0, 240.0], BOX)
        d = tq.defects(ok, words, [], [], BOX)
        assert not d["strong"] and d["word_split"] == 0
        frag = tq.candidate_from_cuts("frag", 0, [[ln] for ln in band_lines(words)], [150.0, 240.0], BOX)
        for row in frag.cells[1:]:
            row[0] = (row[0][0], row[0][1], "")
        d = tq.defects(frag, words, [], [], BOX)
        assert d["fragment_rows"] == 1.0 and d["strong"] and d["word_split"] == 0 and d["under_split"] == 0
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


def _scored(cand: tq.Cand, words: list[dict]) -> tq.Cand:
    cand.f = tq.features(cand, words, [], [], BOX)
    cand.q = tq.quality(cand.f)
    return cand


def test_a_new_grid_that_scores_lower_or_is_defective_does_not_replace_the_legacy_one() -> None:
    """A grid that scores lower than the legacy grid, or shows continuation rows where the legacy one shows none, does not
    replace it (rows of one text line at a uniform pitch look like wrapped lines to the record rule)."""
    pdf, page = borderless()
    try:
        words = [w for w in page.extract_words() if tr._in_box(w, BOX)]
        lines = band_lines(words)
        good = _scored(tq.candidate_from_cuts("good", 2, [[ln] for ln in lines], [150.0, 240.0], BOX), words)
        merged = tq.candidate_from_cuts("sparse", 2, [[ln] for ln in lines], [150.0, 240.0], BOX)
        for ri, k in ((1, 1), (2, 2), (3, 1), (4, 2)):
            merged.cells[ri][k] = (merged.cells[ri][k][0], merged.cells[ri][k][1], "")
        merged = _scored(merged, words)
        assert merged.q < good.q and not tq.defects(merged, words, [], [], BOX)["strong"]
        frag = tq.candidate_from_cuts("frag", 2, [[ln] for ln in lines], [150.0, 240.0], BOX)
        for row in frag.cells[1:]:
            row[0] = (row[0][0], row[0][1], "")
        frag = _scored(frag, words)
        region = legacy_region()
        with patch.object(tr, "legacy_candidate", return_value=good):
            assert tr.legacy_scores_higher(page, [region], BOX, merged, words, [], [], W, H)
            assert not tr.legacy_scores_higher(page, [region], BOX, good, words, [], [], W, H)
            assert tr.legacy_scores_higher(page, [region], BOX, frag, words, [], [], W, H)
        with patch.object(tr, "legacy_candidate", return_value=None):
            assert not tr.legacy_scores_higher(page, [region], BOX, merged, words, [], [], W, H)
        assert not tr.legacy_scores_higher(page, [], BOX, merged, words, [], [], W, H)
    finally:
        pdf.close()


def test_a_wrapped_key_line_joins_the_row_above_even_though_its_key_cell_has_text() -> None:
    bounds = [(0.0, 100.0), (100.0, 300.0)]
    lines = _lines([("Alpha beta gamma", 10, 0), ("delta", 10, 9), ("Next", 10, 40), ("value", 110, 40)])
    rows = tt.continuation_rows(lines, [100.0], ruled_band=False, bounds=bounds)
    assert [len(r) for r in rows] == [2, 1]
