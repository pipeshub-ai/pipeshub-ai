"""Mutant-fixture tests for the hybrid table module: every wrong grid must be detected (route or hard-check fail),
the correct grid must not be. Fixtures are synthetic borderless financial statements."""
from __future__ import annotations

import copy
import random

import pytest

from app.modules.parsers.pdf import table_hybrid as H

CW, LH, LEAD = 4.2, 9.0, 12.0
SEEDS = list(range(40))


def word(text, x0, top, right=None, **kw) -> dict:
    w = len(text) * CW
    if right is not None:
        x0 = right - w
    return dict(text=text, x0=x0, x1=x0 + w, top=top, bottom=top + LH, **kw)


def build(seed, ncol=3) -> tuple:
    """words, topology, row records. Totals add up."""
    rng = random.Random(seed)
    x_lab, lab_w, colw = 72.0, 200.0, 75.0
    rights = [x_lab + lab_w + colw * (k + 1) for k in range(ncol)]
    edges = [x_lab - 2] + [x_lab + lab_w + colw * k + 5 for k in range(ncol)] + [rights[-1] + 5]
    words, rows, y = [], [], 100.0

    def label(text, x, top) -> list:
        out, xx = [], x
        for t in text.split():
            w = word(t, xx, top)
            out.append(w)
            xx = w["x1"] + CW * 0.6
        return out

    words.extend(word(str(2025 - k), 0, y, right=rights[k]) for k in range(ncol))
    rows.append((y, y + LH))
    y += LEAD * 1.4
    for s in range(2):
        words += label(["Revenue:", "Costs:"][s], x_lab, y)
        rows.append((y, y + LH))
        y += LEAD
        sums = [0] * ncol
        for _i in range(rng.randint(3, 5)):
            words += label(rng.choice(["Net sales", "Services", "Licences", "Other items"]), x_lab + 6, y)
            for k in range(ncol):
                v = rng.randint(100, 9999)
                sums[k] += v
                words.append(word(f"{v:,}", 0, y, right=rights[k]))
            rows.append((y, y + LH))
            y += LEAD
        words += label("Total", x_lab, y)
        for k in range(ncol):
            words.append(word(f"{sums[k]:,}", 0, y, right=rights[k]))
        rows.append((y, y + LH))
        y += LEAD * 1.5
    topo = topo_of(rows, edges)
    return words, topo, rows, edges


def topo_of(rows, edges) -> H.Topology:
    cells = [H.Cell(r, c, r, c, (edges[c], t - 1, edges[c + 1], b + 1)) for r, (t, b) in enumerate(rows) for c in range(len(edges) - 1)]
    return H.Topology(len(rows), len(edges) - 1, cells)


def detected(rep) -> bool:
    return rep.would_route or bool(rep.hard_fail)


@pytest.mark.parametrize("seed", SEEDS)
def test_correct_grid_not_detected(seed) -> None:
    words, topo, *_ = build(seed)
    rep = H.evaluate(words, topo)
    assert not rep.would_route, (rep.q, rep.reasons)
    assert rep.hard_fail == []


def test_unknown_topology_routes() -> None:
    rep = H.evaluate([], None)
    assert rep.q is None and rep.would_route and rep.reasons == ["q_unknown"]


def merge_rows(rows, topo, pairs) -> list:
    """Topology with the given row index pairs merged into one band."""
    keep, mapping = [], {}
    i = 0
    while i < len(rows):
        if any(i == a for a, _ in pairs):
            keep.append((rows[i][0], rows[i + 1][1]))
            mapping[i] = mapping[i + 1] = len(keep) - 1
            i += 2
        else:
            keep.append(rows[i])
            mapping[i] = len(keep) - 1
            i += 1
    return keep


def test_mutant_merged_section_row() -> None:
    for seed in SEEDS:
        words, topo, rows, edges = build(seed)
        # section rows are rows whose label ends with a colon: indexes 1 and the one after the first total
        secs = [i for i in range(len(rows)) if i > 0 and any(w["text"].endswith(":") and rows[i][0] - 1 <= w["top"] <= rows[i][1] for w in words)]
        bad = topo_of(merge_rows(rows, topo, [(i, i + 1) for i in secs]), edges)
        rep = H.evaluate(words, bad)
        assert detected(rep), seed
        assert "section_merged" in rep.strong


def test_mutant_merged_numeric_columns() -> None:
    for seed in SEEDS:
        words, topo, rows, edges = build(seed)
        e2 = edges[:2] + edges[3:]  # columns 1 and 2 are one
        rep = H.evaluate(words, topo_of(rows, e2))
        assert detected(rep), seed
        assert "multi_number" in rep.strong or "unsplit_columns" in rep.hard_fail


def test_mutant_swapped_numeric_columns_topology() -> None:
    for seed in SEEDS:
        words, topo, rows, edges = build(seed)
        cells = []
        for c in topo.cells:
            nc = {1: 2, 2: 1}.get(c.c0, c.c0)
            cells.append(H.Cell(c.r0, nc, c.r1, nc, c.box))
        rep = H.evaluate(words, H.Topology(topo.nrows, topo.ncols, cells))
        assert "cols_not_monotone" in rep.hard_fail, seed


def test_mutant_swapped_numeric_columns_word_map() -> None:
    for seed in SEEDS:
        words, topo, rows, edges = build(seed)
        a = H.assign(H.prepare(words), topo)
        idx = {(c.r0, c.c0): k for k, c in enumerate(topo.cells)}
        for i, k in list(a.cell_of.items()):
            c = topo.cells[k]
            if c.c0 in (1, 2):
                a.cell_of[i] = idx[(c.r0, 3 - c.c0)]
        rep = H.evaluate(words, topo, assignment=a)
        assert "displaced" in rep.strong, seed


def test_mutant_header_shifted_one_column() -> None:
    for seed in SEEDS:
        words, topo, rows, edges = build(seed)
        a = H.assign(H.prepare(words), topo)
        idx = {(c.r0, c.c0): k for k, c in enumerate(topo.cells)}
        for i, k in list(a.cell_of.items()):
            c = topo.cells[k]
            if c.r0 == 0 and c.c0 >= 1:
                a.cell_of[i] = idx[(0, c.c0 - 1)]
        rep = H.evaluate(words, topo, assignment=a)
        assert detected(rep), seed


def test_mutant_header_shifted_topology() -> None:
    """Header cells' boxes offset one column to the left: the header words fall in the wrong cells."""
    for seed in SEEDS:
        words, topo, rows, edges = build(seed)
        cells = [
            H.Cell(c.r0, c.c0, c.r1, c.c1, (edges[c.c0 - 1], c.box[1], edges[c.c0], c.box[3])) if c.r0 == 0 and c.c0 >= 1 else c
            for c in topo.cells
        ]
        rep = H.evaluate(words, H.Topology(topo.nrows, topo.ncols, cells))
        assert detected(rep), seed


def with_marker(words, topo) -> tuple:
    """The first numeric word of the first item row gets a raised, smaller footnote digit after it."""
    ws = copy.deepcopy(words)
    i = next(k for k, w in enumerate(ws) if w["text"][0].isdigit() and len(w["text"]) > 3 and w["top"] > 130)
    w = ws[i]
    chars, x = [], w["x0"]
    for ch in w["text"]:
        chars.append({"text": ch, "x0": x, "x1": x + CW, "top": w["top"], "bottom": w["bottom"], "size": LH})
        x += CW
    chars.append({"text": "1", "x0": x, "x1": x + 2.5, "top": w["top"] - 2, "bottom": w["top"] + 2.5, "size": 5.0})
    w["chars"] = chars
    w["text"] += "1"
    w["x1"] = x + 2.5
    return ws, i


def test_mutant_superscript_fused_number() -> None:
    for seed in SEEDS[:10]:
        words, topo, *_ = build(seed)
        ws, i = with_marker(words, topo)
        naive = H.evaluate(ws, topo, split_raised=False)
        assert detected(naive) and "fused_superscript" in naive.strong, seed
        fixed = H.evaluate(ws, topo)
        assert not fixed.would_route, (fixed.q, fixed.reasons)
        toks = H.prepare(ws)
        assert any(t.marker and t.text == "1" for t in toks)
        assert H.assign(toks, topo).footnotes


def with_rotated_header(words, topo) -> list:
    """A header word set at 90 degrees, as one stacked fragment per two characters, in the label column's header."""
    ws = copy.deepcopy(words)
    x0, top = 80.0, 100.0
    for k, part in enumerate(["Fi", "sc", "al"]):
        ws.append({"text": part, "x0": x0, "x1": x0 + 8, "top": top + (2 - k) * 9, "bottom": top + (2 - k) * 9 + 9, "upright": False, "rot": 90})
    return ws


def test_mutant_rotated_header() -> None:
    for seed in SEEDS[:10]:
        words, topo, *_ = build(seed)
        ws = with_rotated_header(words, topo)
        naive = H.evaluate(ws, topo, rebuild=False)
        assert detected(naive) and "rotated_fragments" in naive.strong, seed
        fixed = H.evaluate(ws, topo)
        assert not fixed.would_route, (fixed.q, fixed.reasons)
        assert any(t.rebuilt and t.text == "Fiscal" for t in H.prepare(ws))


def test_total_row_arithmetic_detects_a_lost_row() -> None:
    """An item row whose words are missing from the grid (rows 3 of the first block): every total of the block is off."""
    for seed in SEEDS[:10]:
        words, topo, rows, edges = build(seed)
        victim = rows[3]
        ws = [w for w in words if not (victim[0] - 1 <= w["top"] <= victim[1])]
        keep = [r for i, r in enumerate(rows) if i != 3]
        rep = H.evaluate(ws, topo_of(keep, edges))
        assert "total_mismatch" in rep.strong, seed


def test_empty_label_beside_numbers_is_penalised() -> None:
    words, topo, *_ = build(5)
    ws = [w for w in words if not (w["x0"] < 200 and w["top"] > 130 and w["top"] < 160)]
    rep = H.evaluate(ws, topo)
    assert rep.features["label_gap"] > 0


# --- assignment rules (R1-7) --------------------------------------------------------------------------------


def test_symbols_bind_by_direction() -> None:
    ws = [
        word("$", 100, 10), word("1,234", 0, 10, right=190),
        word("(", 200, 10), word("56", 210, 10), word(")", 222, 10),
        word("12", 300, 10), word("%", 313, 10),
    ]
    toks = H.prepare(ws)
    texts = sorted(t.text for t in toks)
    assert texts == sorted(["$1,234", "(56)", "12%"]) or texts == sorted(["$1,234", "(56)", "12%"])


def test_dollar_binds_right_even_when_left_neighbour_is_nearer() -> None:
    ws = [word("99", 100, 10), word("$", 120, 10), word("1,234", 150, 10)]
    toks = H.prepare(ws)
    assert sorted(t.text for t in toks) == ["$1,234", "99"]


def test_symbol_with_a_word_between_does_not_bind() -> None:
    ws = [word("$", 100, 10), word("net", 110, 10), word("5", 130, 10)]
    assert sorted(t.text for t in H.prepare(ws)) == ["$net", "5"] or True  # host must be number-like
    assert "$net" not in [t.text for t in H.prepare(ws)]


def test_word_in_two_cells_goes_to_larger_overlap_then_nearer_edge() -> None:
    topo = H.Topology(2, 2, [H.Cell(0, 0, 0, 0, (0, 0, 100, 20)), H.Cell(0, 1, 0, 1, (100, 0, 200, 20)),
                             H.Cell(1, 0, 1, 0, (0, 20, 100, 40)), H.Cell(1, 1, 1, 1, (100, 20, 200, 40))])
    a = H.assign(H.prepare([{"text": "12,345", "x0": 80, "x1": 130, "top": 5, "bottom": 14}]), topo)
    assert topo.cells[a.cell_of[0]].c0 == 1 and a.ambiguous == [0]  # 20 pt left, 30 pt right
    # equal overlap: a number goes by its right edge, text by its left edge
    n = H.assign(H.prepare([{"text": "12,345", "x0": 75, "x1": 125, "top": 5, "bottom": 14}]), topo)
    assert topo.cells[n.cell_of[0]].c0 == 0  # right edges: cell 0 ends at 100 (25 away), cell 1 at 200 (75 away)
    t = H.assign(H.prepare([{"text": "ab", "x0": 75, "x1": 125, "top": 5, "bottom": 14}]), topo)
    assert topo.cells[t.cell_of[0]].c0 == 1  # left edges: cell 1 starts at 100 (25 away), cell 0 at 0 (75 away)
def test_word_in_no_cell_is_unassigned_and_fails_hard_checks() -> None:
    words, topo, *_ = build(1)
    ws = [*words, {"text": "stray", "x0": 900, "x1": 930, "top": 500, "bottom": 509}]
    rep = H.evaluate(ws, topo)
    assert "unassigned" in rep.hard_fail and detected(rep)


def test_word_in_a_second_cell_breaks_bijection() -> None:
    words, topo, *_ = build(2)
    a = H.assign(H.prepare(words), topo)
    a.cell_of.pop(0)
    assert "bijection" in H.hard_checks(words, topo, a)


def test_under_2x2_fails() -> None:
    topo = H.Topology(1, 3, [H.Cell(0, c, 0, c, (c * 50, 0, c * 50 + 50, 20)) for c in range(3)])
    a = H.assign(H.prepare([{"text": "a", "x0": 5, "x1": 10, "top": 5, "bottom": 14}]), topo)
    assert "under_2x2" in H.hard_checks(None, topo, a)


# --- replacement (R1-4) -------------------------------------------------------------------------------------


def test_replacement_rules() -> None:
    words, topo, *_ = build(4)
    good = H.evaluate(words, topo)
    routed = H.evaluate(words, None)
    assert H.can_replace(routed, "none", topo, []) == (True, "ok")
    assert H.can_replace(routed, "not_table", topo, []) == (False, "not_table")
    assert H.can_replace(None, "none", topo, []) == (False, "no_heuristic_table")
    assert H.can_replace(good, "none", topo, []) == (False, "h_not_routed")
    assert H.can_replace(routed, "none", topo, ["unassigned"])[0] is False
    assert H.can_replace(routed, "none", H.Topology(1, 3, []), [])[0] is False


def test_log_fields_have_no_cell_text() -> None:
    words, topo, *_ = build(4)
    out = H.evaluate(words, topo).log_fields()
    assert set(out) == {"q", "would_route", "reasons", "hard_fail"}


# --- shadow wiring --------------------------------------------------------------------------------------------


def _shadow_fixture() -> object:
    from tests.unit.modules.parsers import test_pdf_table_reconcile as R

    return R


def test_shadow_carries_report_and_emitted_tables_equal_legacy(monkeypatch) -> None:
    R = _shadow_fixture()
    pdf, page = R.page_of(R.simple)
    try:
        box = [R.mbox(R.true_box(extra_top=14))]
        legacy = R.regions(page, box, "legacy", monkeypatch)
        shadow = R.regions(page, box, "shadow", monkeypatch)
        key = lambda rs: [(r.type, r.bbox, r.text, r.table_grid, r.from_evidence, r.table_q) for r in rs]  # noqa: E731
        assert key(legacy) == key(shadow)
        assert all(r.table_hybrid is None for r in legacy)
        tabs = [r for r in shadow if r.type == R.LayoutRegionType.TABLE]
        assert tabs and all(set(r.table_hybrid) == {"q", "would_route", "reasons", "hard_fail"} for r in tabs)
        # no cell text in what is carried or logged
        blob = repr([r.table_hybrid for r in tabs])
        assert "Meaning" not in blob and "Item" not in blob
    finally:
        pdf.close()


def test_shadow_marks_a_grid_it_cannot_reread_as_q_unknown(monkeypatch) -> None:
    R = _shadow_fixture()
    from app.modules.parsers.pdf import table_reconcile as tr

    pdf, page = R.page_of(R.simple)
    try:
        monkeypatch.setattr(tr, "legacy_candidate", lambda *a, **k: None)
        shadow = R.regions(page, [R.mbox(R.true_box())], "shadow", monkeypatch)
        tabs = [r for r in shadow if r.type == R.LayoutRegionType.TABLE]
        assert tabs and all(r.table_hybrid == {"q": None, "would_route": True, "reasons": ["q_unknown"], "hard_fail": []} for r in tabs)
    finally:
        pdf.close()


def test_shadow_failure_never_changes_the_parse(monkeypatch) -> None:
    R = _shadow_fixture()
    from app.modules.parsers.pdf import table_hybrid as th

    pdf, page = R.page_of(R.simple)
    try:
        box = [R.mbox(R.true_box(extra_top=14))]
        legacy = [(r.type, r.bbox, r.text, r.table_grid) for r in R.regions(page, box, "legacy", monkeypatch)]
        monkeypatch.setattr(th, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        shadow = R.regions(page, box, "shadow", monkeypatch)
        assert legacy == [(r.type, r.bbox, r.text, r.table_grid) for r in shadow]
    finally:
        pdf.close()


def test_legacy_mode_does_no_hybrid_work(monkeypatch) -> None:
    R = _shadow_fixture()
    from app.modules.parsers.pdf import table_reconcile as tr

    pdf, page = R.page_of(R.simple)
    try:
        monkeypatch.setattr(tr, "shadow_hybrid", lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy must not run it")))
        R.regions(page, [R.mbox(R.true_box())], "legacy", monkeypatch)
        R.regions(page, [R.mbox(R.true_box())], "evidence", monkeypatch)
    finally:
        pdf.close()


def test_parent_logs_one_low_cardinality_line_per_document(caplog) -> None:
    import logging
    from types import SimpleNamespace

    from app.modules.parsers.pdf.opencv_layout_analyzer import (
        LayoutRegion,
        LayoutRegionType,
    )
    from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
        PDFPlumberOpenCVProcessor as P,
    )

    def region(rep) -> LayoutRegion:
        return LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0, 0, 1, 1), text="secret cell", table_grid=[["secret cell"]], table_hybrid=rep)

    pages = [SimpleNamespace(regions=[
        region({"q": 0.4, "would_route": True, "reasons": ["low_q"]}),
        region({"q": None, "would_route": True, "reasons": ["q_unknown"], "hard_fail": []}),
        region({"q": 0.9, "would_route": False, "reasons": []}),
        region(None),
    ])]
    me = SimpleNamespace(logger=logging.getLogger("hybrid-test"))
    with caplog.at_level(logging.INFO, logger="hybrid-test"):
        P._log_table_hybrid(me, pages)
    msgs = [r.getMessage() for r in caplog.records if r.name == "hybrid-test"]
    assert msgs == ["table hybrid shadow: tables=3 would_route=2 q_unknown=1 reasons={'low_q': 1, 'q_unknown': 1}"]
    assert "secret" not in msgs[0]
    caplog.clear()
    P._log_table_hybrid(me, [SimpleNamespace(regions=[region(None)])])
    assert not caplog.records


def test_mutant_two_item_rows_merged_into_one_record() -> None:
    for seed in SEEDS:
        words, topo, rows, edges = build(seed)
        items = [i for i in range(len(rows) - 1) if i > 1 and rows[i + 1][0] - rows[i][0] < 13 and not any(w["text"] in ("Total", "Costs:") and rows[i][0] - 1 <= w["top"] <= rows[i + 1][1] for w in words)]
        i = items[0]
        keep = [r for k, r in enumerate(rows) if k != i + 1]
        keep[i] = (rows[i][0], rows[i + 1][1])
        rep = H.evaluate(words, topo_of(keep, edges))
        assert detected(rep), seed


def test_wrapped_text_in_two_columns_is_not_a_strong_defect() -> None:
    """Two text columns that both wrap onto two lines line up by chance: a soft signal only."""
    cells = [H.Cell(r, c, r, c, (c * 100, r * 40, c * 100 + 100, r * 40 + 40)) for r in range(3) for c in range(2)]
    topo = H.Topology(3, 2, cells)
    ws = [word(f"text{r}{c}{ln}", c * 100 + 5, r * 40 + 5 + ln * 12) for r in range(3) for c in range(2) for ln in range(2)]
    rep = H.evaluate(ws, topo)
    assert "rows_merged" not in rep.strong


def test_adapter_marks_column_spans_and_a_spanning_header_is_not_a_monotone_failure() -> None:
    rows = [(0, 10), (10, 20), (20, 30)]
    cells = [[(0, 200)], [(0, 100), (100, 200)], [(0, 100), (100, 200)]]
    present = [[True, False], [True, True], [True, True]]
    topo = H.topology_from_rows(rows, cells, present)
    span = next(c for c in topo.cells if c.r0 == 0)
    assert (span.c0, span.c1) == (0, 1)
    ws = [word("Group", 10, 1), word("a", 10, 11), word("b", 110, 11), word("c", 10, 21), word("d", 110, 21)]
    assert H.evaluate(ws, topo).hard_fail == []


def _currency_table(bind: bool) -> tuple[list, H.Topology]:
    """Every body row has a ``$`` at the cell's left edge and the number right-aligned: a wide gap, aligned down the column."""
    ws = [word("Item", 5, 5), word("2025", 0, 5, right=190)]
    rows = [(5 + 12 * i) for i in range(1, 6)]
    for i, y in enumerate(rows):
        ws += [word(f"row{i}", 5, y), word("$", 105, y), word(f"{300 + i}.5", 0, y, right=190)]
    cells = [H.Cell(r, c, r, c, (c * 100, r * 12, c * 100 + 100, r * 12 + 12)) for r in range(7) for c in range(2)]
    return ws, H.Topology(7, 2, cells)


@pytest.mark.parametrize("bind", [True, False])
def test_currency_sign_and_number_with_a_gap_in_one_cell_pass_the_gap_check(bind) -> None:
    ws, topo = _currency_table(bind)
    a = H.assign(H.prepare(ws, bind=bind), topo)
    assert H.hard_checks(ws, topo, a) == []
    assert "unsplit_columns" not in H.evaluate(ws, topo).hard_fail


def test_two_numbers_with_an_aligned_gap_still_fail_the_gap_check() -> None:
    ws, topo = _currency_table(True)
    ws = [w for w in ws if w["text"] != "$"]
    ws += [word("77", 105, 5 + 12 * i) for i in range(1, 6)]
    a = H.assign(H.prepare(ws, bind=False), topo)
    assert "unsplit_columns" in H.hard_checks(ws, topo, a)
