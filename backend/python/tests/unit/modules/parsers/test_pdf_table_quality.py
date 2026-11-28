"""The score Q and the header-row rules of the table candidates (``table_quality``).

Words are plain dicts here: the rules are geometry only.
"""
from __future__ import annotations

from io import BytesIO

import pdfplumber
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.modules.parsers.pdf import table_quality as tq
from app.modules.parsers.pdf import table_reconcile as tr
from app.modules.parsers.pdf.table_rows import band_lines

H = 8.0


def word(text: str, x0: float, top: float, width: float | None = None) -> dict:
    w = width if width is not None else 5.0 * len(text)
    return {"text": text, "x0": x0, "x1": x0 + w, "top": top, "bottom": top + H}


def line(*items: tuple[str, float], top: float = 0.0) -> dict:
    return band_lines([word(t, x, top) for t, x in items])[0]


CUTS = [100.0, 200.0]


def test_header_wrapped_label_is_one_row() -> None:
    a = line(("Number", 10), ("of", 50), top=0)
    b = line(("tables", 10), top=9)
    assert not tq.header_breaks(a, b, CUTS)


def test_header_incomparable_filled_columns_start_a_new_row() -> None:
    a = line(("Name", 10), ("Value", 110), top=0)
    b = line(("Unit", 210), top=9)
    assert tq.header_breaks(a, b, CUTS)


def test_header_group_label_over_columns_is_its_own_row() -> None:
    a = line(("Decode", 60), ("fields", 100), ("here", 140), top=0)  # crosses the cut at 100
    b = line(("size", 30), ("op", 130), top=9)
    assert tq.group_label(a, b, CUTS) and tq.header_breaks(a, b, CUTS)


def test_header_sub_label_row_sitting_lower_than_a_wrap_is_a_new_row() -> None:
    a = line(("Bits", 10), top=0)
    b = line(("Field", 10), top=H * 2.5)
    assert tq.header_breaks(a, b, CUTS)


def _cand(grid: list[list[str]], cells_x: list[float]) -> tq.Cand:
    rows = [(i * 12.0, i * 12.0 + H) for i in range(len(grid))]
    cells = [[(cells_x[k], cells_x[k + 1], t) for k, t in enumerate(r)] for r in grid]
    return tq.Cand("t", 2, rows, cells, len(cells_x) - 1, [list(r) for r in grid], (cells_x[0], 0, cells_x[-1], 12.0 * len(grid)))


def _words(grid: list[list[str]], cells_x: list[float]) -> list[dict]:
    return [word(t, cells_x[k] + 2, i * 12.0) for i, r in enumerate(grid) for k, t in enumerate(r) if t]


GRID = [["Name", "Value", "Unit"]] + [[f"Row{i}", f"{i}0", "mm"] for i in range(5)]
XS = [0.0, 100.0, 200.0, 300.0]


def test_a_clean_grid_scores_high_and_a_broken_one_lower() -> None:
    words = _words(GRID, XS)
    good = _cand(GRID, XS)
    good.f = tq.features(good, words, [], [], good.bbox)
    broken_grid = [["Name Value", "Unit", ""]] + [[f"Row{i} {i}0", "mm", ""] for i in range(5)]
    broken = _cand(broken_grid, XS)
    broken.f = tq.features(broken, words, [], [], broken.bbox)
    assert tq.quality(good.f) > 0.9
    assert tq.quality(broken.f) < tq.quality(good.f) - 0.3


def test_q_is_zero_for_a_list_a_lossy_grid_and_a_one_column_grid() -> None:
    words = _words(GRID, XS)
    c = _cand(GRID, XS)
    f = tq.features(c, words, [], [], c.bbox)
    assert tq.quality(dict(f, **{"list": True})) == 0.0
    assert tq.quality(dict(f, cov=0.5)) == 0.0
    assert tq.quality(dict(f, cols=1)) == 0.0
    assert tq.quality(dict(f, toc=True)) == 0.0
    assert tq.quality(dict(f, margin_prose=True)) == 0.0


@pytest.mark.parametrize("feature", list(tq.WEIGHTS))
def test_every_weighted_feature_lowers_q(feature: str) -> None:
    c = _cand(GRID, XS)
    f = tq.features(c, _words(GRID, XS), [], [], c.bbox)
    assert tq.quality(dict(f, **{feature: 0.5})) < tq.quality(dict(f, **{feature: 0.0}))


def test_text_crossing_an_undrawn_cut_is_counted_and_a_drawn_rule_exempts_it() -> None:
    grid = [["Name", "Value", "Unit"], ["Aaaaaaa", "1", "x"], ["Bbbbbbb", "2", "y"]]
    words = _words(grid, XS)
    words[3]["x1"] = 99.0  # "Aaaaaaa" ends 3 pt before the next cell's text: under 0.6 line heights across the cut
    words[4]["x0"], words[4]["x1"] = 102.0, 107.0
    c = _cand(grid, XS)
    assert tq.features(c, words, [], [], c.bbox)["xcross"] > 0
    rule = [(100.0, -50.0, 500.0)]
    assert tq.features(c, words, [], rule, c.bbox)["xcross"] < tq.features(c, words, [], [], c.bbox)["xcross"]


def test_features_of_a_lattice_candidate_read_from_a_pdf() -> None:
    buf = BytesIO()
    cv = canvas.Canvas(buf, pagesize=letter)
    cv.setFont("Helvetica", 8)
    xs = [60.0, 160.0, 240.0, 420.0]
    for r in range(5):
        for k, text in enumerate(("Name" if r == 0 else f"Item{r}", "Value" if r == 0 else f"{r}0", "Meaning" if r == 0 else f"About item {r}")):
            cv.drawString(xs[k] + 4, 700 - 20 * r, text)
    for r in range(6):
        cv.line(xs[0], 710 - 20 * r, xs[-1], 710 - 20 * r)
    for x in xs:
        cv.line(x, 710, x, 610)
    cv.showPage()
    cv.save()
    page = pdfplumber.open(BytesIO(buf.getvalue())).pages[0]
    t = page.find_tables()[0]
    c = tq.candidate_from_table("lattice", 1, t)
    h, v = tr.page_rules(page)
    words = [w for w in page.extract_words() if tr._in_box(w, c.bbox)]
    c.f = tq.features(c, words, h, v, c.bbox)
    assert (c.f["rows"], c.f["cols"]) == (5, 3)
    assert c.f["cov"] == 1.0 and c.f["xcross"] == 0.0 and not c.f["list"]
    assert tq.quality(c.f) > 0.9


def test_a_line_on_the_edge_of_two_rows_counts_once_as_a_split_line() -> None:
    xs = [0.0, 100.0, 200.0]
    c = tq.Cand("t", 2, [(0.0, 10.0), (10.0, 20.0)], [[(0.0, 100.0, "a"), (100.0, 200.0, "b")]] * 2, 2,
                [["a", "b"], ["a", "b"]], (xs[0], 0.0, xs[-1], 20.0))
    words = [word("straddling", 90.0, 6.0, 20.0)]  # centre 10: on the edge of both rows, across the cut at 100
    assert tq.defects(c, words, [], [], c.bbox)["word_split"] == 1
