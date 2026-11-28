"""The one definition of a drawn rule: strokes, filled rectangles up to the snap distance, outlines; not background fills."""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.modules.parsers.pdf import drawn_rules as dr
from app.modules.parsers.pdf import table_hybrid as th
from app.modules.parsers.pdf import table_hybrid_flow as flow

if TYPE_CHECKING:
    from pathlib import Path


def page(lines=(), rects=()):  # noqa: ANN201
    return SimpleNamespace(lines=list(lines), rects=list(rects))


def rect(x0, top, x1, bottom, **kw):  # noqa: ANN201
    return {"x0": x0, "x1": x1, "top": top, "bottom": bottom, **kw}


def test_the_thickness_is_the_snap_distance_of_the_table_reader() -> None:
    from app.modules.parsers.pdf import table_reconcile

    assert dr.RULE_MAX_THICKNESS == table_reconcile.SNAP == 3.0


def test_a_header_rule_drawn_as_a_filled_bar_of_2_3_points_is_a_rule() -> None:
    h, v = dr.drawn_rules(page(rects=[rect(72.5, 95.9, 719.5, 98.2)]))
    assert len(h) == 1 and h[0][0] == pytest.approx(97.05) and (h[0][1], h[0][2]) == (72.5, 719.5) and v == []


def test_a_bar_up_to_three_points_is_a_rule_and_four_points_is_not() -> None:
    assert len(dr.drawn_rules(page(rects=[rect(10, 100, 200, 103.0)]))[0]) == 1
    assert dr.drawn_rules(page(rects=[rect(10, 100, 200, 104.0, fill=True)])) == ([], [])


def test_the_edges_of_a_background_fill_are_not_rules_but_the_edges_of_an_outline_are() -> None:
    fill = rect(10, 100, 200, 160, fill=True, stroke=False)
    assert dr.drawn_rules(page(rects=[fill])) == ([], [])
    shaded = rect(10, 100, 200, 160, fill=True, stroke=False, linewidth=1.0, stroking_color=(0, 0, 0))  # a fill as pdfplumber reports it
    assert dr.drawn_rules(page(rects=[shaded])) == ([], [])
    outline = rect(10, 100, 200, 160, stroke=True, linewidth=0.5, stroking_color=(0, 0, 0))
    h, v = dr.drawn_rules(page(rects=[outline]))
    assert len(h) == 2 and len(v) == 2


def test_lines_and_thin_vertical_bars_are_rules() -> None:
    h, v = dr.drawn_rules(page(lines=[rect(10, 50, 100, 50.4)], rects=[rect(60, 10, 61.5, 90)]))
    assert len(h) == 1 and len(v) == 1 and v[0][0] == pytest.approx(60.75)
    assert dr.drawn_rules(page(lines=[rect(10, 50, 14, 50.4)])) == ([], [])  # too short to be a rule


def test_an_unreadable_page_has_unknown_rules() -> None:
    class Broken:
        @property
        def lines(self):  # noqa: ANN202
            raise RuntimeError("no")

    assert dr.drawn_rules(Broken()) is None
    assert dr.strokes_touching(None, (0, 0, 1, 1)) is None


def test_strokes_touching_keeps_those_in_or_across_the_box() -> None:
    strokes = ([(50.0, 0.0, 500.0), (900.0, 0.0, 500.0)], [(40.0, 0.0, 100.0), (700.0, 0.0, 100.0)])
    assert dr.strokes_touching(strokes, (10.0, 20.0, 300.0, 120.0)) == {"h": [[50.0, 0.0, 500.0]], "v": [[40.0, 0.0, 100.0]]}


def w(text: str, x0: float, top: float, x1: float, bottom: float) -> dict:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom}


def cell(tokens: list[int]) -> dict:
    return {"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": tokens}


def test_crossings_are_counted_by_direction() -> None:
    words = {0: w("a", 10, 0, 20, 10), 1: w("b", 10, 30, 20, 40), 2: w("c", 60, 0, 70, 10), 3: w("d", 10, 100, 20, 110)}
    h_rule = [(25.0, 0.0, 100.0)]
    v_rule = [(40.0, -5.0, 20.0)]
    assert dr.crossings(words, [cell([0, 1])], h_rule, v_rule) == (1, 0, 1)  # above and below a horizontal rule
    assert dr.crossings(words, [cell([0, 2])], h_rule, v_rule) == (0, 1, 1)  # left and right of a vertical one
    both = {0: w("a", 10, 0, 20, 10), 1: w("b", 60, 40, 70, 50)}
    assert dr.crossings(both, [cell([0, 1])], [(25.0, 0.0, 100.0)], [(40.0, -5.0, 60.0)]) == (1, 1, 1)  # one cell, both: once in "any"
    assert dr.crossings(words, [cell([0])], h_rule, v_rule) == (0, 0, 0)


def test_the_shadow_run_and_the_table_checks_use_this_definition() -> None:
    assert flow.drawn_rules is dr.drawn_rules and flow.strokes_touching is dr.strokes_touching and flow.crossings is dr.crossings
    words = {0: w("a", 10, 0, 20, 10), 1: w("b", 10, 30, 20, 40)}
    assert th.rule_crossings(words, [cell([0, 1])], [(25.0, 0.0, 100.0)], []) == dr.crossings(words, [cell([0, 1])], [(25.0, 0.0, 100.0)], [])[2]


def _bar_pdf(path: Path) -> None:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
    c.setFont("Helvetica", 9)
    c.drawString(80, 792 - 84, "Description")
    c.drawString(300, 792 - 84, "Value")
    c.rect(72, 792 - 98.2, 450, 2.3, fill=1, stroke=0)  # the header rule: a filled bar 2.3 pt high
    c.drawString(80, 792 - 112, "continued row")
    c.drawString(300, 792 - 112, "12")
    c.showPage()
    c.save()


def test_a_header_rule_drawn_as_a_bar_is_spooled_and_a_cell_that_straddles_it_is_a_horizontal_crossing(tmp_path) -> None:
    """The shape of a table whose header rule is a filled bar thicker than the old 1.5 pt limit."""
    import json

    import pdfplumber

    from app.services.docling.table_structure_schema import TableResult
    from tests.support.table_shadow import PAGE_H, PAGE_W, FakeDecision, blank_renderer

    pdf = tmp_path / "bar.pdf"
    _bar_pdf(pdf)
    box = (60.0, 70.0, 540.0, 130.0)
    spool = tmp_path / "s"
    spool.mkdir()
    with pdfplumber.open(str(pdf)) as doc:
        pg = doc.pages[0]
        words = pg.extract_words()
        flow.TableJobCollector(str(spool), str(pdf), renderer=blank_renderer).collect_page(0, PAGE_W, PAGE_H, [FakeDecision(box)], [], None, words, pg)
    rec = json.loads((spool / flow.INDEX_FILE).read_text().splitlines()[0])
    assert any(abs(y - 97.05) < 0.2 for y, _a, _c in rec["rules"]["h"])  # the bar is a rule now
    texts = [x[0] for x in rec["words"]]
    cells = [{"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [texts.index("Description"), texts.index("continued")]},
             {"r0": 0, "r1": 1, "c0": 1, "c1": 2, "tokens": [texts.index("Value"), texts.index("12")]}]
    out = flow.analyse_table(rec, TableResult(id="t", status="ok", n_rows=1, n_cols=2, cells=cells), None)
    assert (out["rule_crossings_h"], out["rule_crossings_v"], out["rule_crossings"]) == (2, 0, 2)


def test_a_rule_through_a_word_is_no_crossing_even_when_a_short_word_touches_both_sides() -> None:
    def w(t: float, b: float, x0: float = 0.0, x1: float = 40.0) -> dict:
        return {"top": t, "bottom": b, "x0": x0, "x1": x1}

    # at y=10: one word above, one below, a short one on the rule, and one the rule cuts through
    ws = [w(0, 5), w(15, 20), w(9.5, 10.5), w(7, 13)]
    assert not dr._crosses_h(ws, 10.0, -10.0, 60.0, 0.0, 40.0, 0.0, 20.0)
    turned = [{"x0": d["top"], "x1": d["bottom"], "top": 0.0, "bottom": 40.0} for d in ws]
    assert not dr._crosses_v(turned, 10.0, -10.0, 60.0, 0.0, 20.0, 0.0, 40.0)
    # without the cut word the rule does separate the cell's words
    assert dr._crosses_h(ws[:3], 10.0, -10.0, 60.0, 0.0, 40.0, 0.0, 20.0)
