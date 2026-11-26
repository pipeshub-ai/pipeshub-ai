"""Placement: the model grid replaces the evidence table before the text classification and survives the rest of the page
pass; nothing is indexed twice, and when it does not survive the page is the evidence result."""
from __future__ import annotations

import logging

import pytest

from app.modules.parsers.pdf import opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_hybrid_emit as emit
from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
from app.modules.parsers.pdf.opencv_layout_analyzer import (
    LayoutRegion,
    LayoutRegionType,
)
from tests.support.table_emit import (
    BOX,
    FakeModelClient,
    canonical,
    cfg,
    fake_boxes,
    fixture_pdf,
    grid_cells,
    make_emitter,
    make_pdf,
    run_page,
    word_counter,
)
from tests.support.table_shadow import grid_words

PDF, BOXES = fixture_pdf()


@pytest.fixture(autouse=True)
def boxes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ola, "_detect_model_boxes", fake_boxes(BOXES))


def parse(mode: str, monkeypatch: pytest.MonkeyPatch, client: FakeModelClient | None = None):  # noqa: ANN201
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", mode)
    if client is not None:
        real = emit.TableEmitter.__init__

        def init(self, c, path, **kw):  # noqa: ANN202
            real(self, c, path, **dict(kw, client=client))

        monkeypatch.setattr(emit.TableEmitter, "__init__", init)
    return parse_pdf_layout(PDF, None, cfg() if mode == "hybrid" else None)


def tables(pages) -> list[LayoutRegion]:
    return [r for p in pages for r in p.regions if r.type == LayoutRegionType.TABLE]


def test_the_fixture_tables_carry_the_model_grids_and_no_word_is_lost_or_doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = parse("evidence", monkeypatch)
    hybrid = parse("hybrid", monkeypatch, FakeModelClient())
    swapped = [t for t in tables(hybrid) if t.from_model_grid]
    assert len(swapped) >= 6
    ev, hy = word_counter(evidence), word_counter(hybrid)
    assert not (ev - hy), f"words in evidence and missing in hybrid: {dict(ev - hy)}"
    assert not (hy - ev), f"new or doubled words in hybrid: {dict(hy - ev)}"


def test_the_model_region_is_the_box_and_page_one_reads_as_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    hybrid = parse("hybrid", monkeypatch, FakeModelClient())
    t = tables([hybrid[0]])[0]
    assert t.from_model_grid and [round(v) for v in t.bbox] == [68, 116, 486, 259]
    assert t.table_grid[0] == ["Item", "2023", "2022", "Change", "Note"] and t.table_grid[1][1] == "$1,037"


def test_a_table_inside_the_box_is_not_also_classified_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    hybrid = parse("hybrid", monkeypatch, FakeModelClient())
    for page in hybrid:
        for t in [r for r in page.regions if r.from_model_grid]:
            for o in page.regions:
                if o is not t and o.type in (LayoutRegionType.TEXT, LayoutRegionType.LIST):
                    assert ola._overlap_ratio(o.bbox, t.bbox) < 0.5


# --- what the page pass does not do to a model grid -------------------------------------------------------------------


def model_region(bbox=(0.0, 0.0, 100.0, 50.0), grid=None) -> LayoutRegion:
    grid = grid or [["a", "b"], ["c", "d"]]
    return LayoutRegion(type=LayoutRegionType.TABLE, bbox=bbox, text=ola._grid_to_text(grid), table_grid=grid, from_evidence=True, from_model_grid=True)


def test_resolve_overlaps_never_merges_a_model_grid() -> None:
    text = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0.0, 10.0, 100.0, 60.0), text="some text under the grid")
    out = ola._resolve_overlaps([model_region(), text], page_w=200.0, page_h=200.0)
    assert len(out) == 2 and sum(r.from_model_grid for r in out) == 1
    plain = model_region()
    plain.from_model_grid = False
    assert len(ola._resolve_overlaps([plain, LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0.0, 10.0, 100.0, 60.0), text="some text under the grid")], page_w=200.0, page_h=200.0)) == 1


def test_a_model_grid_is_not_merged_by_proximity_either() -> None:
    near = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0.0, 52.0, 100.0, 60.0), text="note")
    out = ola._resolve_overlaps([model_region(), near], page_w=200.0, page_h=200.0)
    assert len(out) == 2


def test_a_model_grid_is_never_re_read_from_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):  # noqa: ANN202
        raise AssertionError("re-read")

    monkeypatch.setattr(ola, "_reextract_grid_and_bbox", boom)
    r = model_region()
    assert ola._reextract_region_payload(r, object(), [], 200.0, 200.0) is r and r.table_grid == [["a", "b"], ["c", "d"]]


def test_the_merge_carries_the_flag() -> None:
    assert ola._merge_regions(model_region(), model_region()).from_model_grid


def test_demotion_skips_a_model_grid_that_drops_characters() -> None:
    words = [{"text": t, "x0": 10 + 20 * i, "x1": 25 + 20 * i, "top": 10, "bottom": 20} for i, t in enumerate(["alpha", "beta", "gamma", "delta"])]
    lossy = [["alpha", "beta"]]
    plain = LayoutRegion(type=LayoutRegionType.TABLE, bbox=(0.0, 0.0, 200.0, 40.0), text="x", table_grid=lossy)
    assert all(r.type == LayoutRegionType.TEXT for r in ola._demote_lossy_tables([plain], words))
    kept = model_region(bbox=(0.0, 0.0, 200.0, 40.0), grid=lossy)
    assert ola._demote_lossy_tables([kept], words) == [kept]


def test_the_currency_merge_is_skipped_for_a_model_grid(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    def draw(c) -> None:
        c.setFont("Helvetica", 9)
        for r in range(6):
            y = 792 - 120 - 16 * r
            c.drawString(80, y, "Revenue" if r else "Item")
            if r:
                c.drawString(250, y, "$")
                c.drawRightString(330, y, f"{1000 + r}")
                c.drawString(380, y, "$")
                c.drawRightString(460, y, f"{900 + r}")
            else:
                c.drawString(300, y, "2023")
                c.drawString(430, y, "2022")

    pdf = make_pdf(draw, tmp_path / "cur.pdf")
    box = [70, 108, 480, 220]
    monkeypatch.setattr(ola, "_detect_model_boxes", fake_boxes({"1": [box]}))
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "hybrid")
    real = emit.TableEmitter.__init__
    client = FakeModelClient()
    monkeypatch.setattr(emit.TableEmitter, "__init__", lambda self, c, p, **kw: real(self, c, p, **dict(kw, client=client)))
    t = [r for r in parse_pdf_layout(pdf, None, cfg())[0].regions if r.type == LayoutRegionType.TABLE]
    assert len(t) == 1 and t[0].from_model_grid
    grid = t[0].table_grid
    assert [row[1] for row in grid[1:]] == ["$"] * 5 and [row[3] for row in grid[1:]] == ["$"] * 5  # the sign columns are as the model drew them
    assert ola._merge_currency_columns([list(row) for row in grid]) != grid  # a merge would have changed it


def test_a_caption_sibling_is_kept_out_of_a_model_grid() -> None:
    text = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(0.0, -20.0, 100.0, -2.0), text="Table 3. Results by region")
    assert ola._evidence_table_keeps_caption_out(model_region(), text)


def test_a_link_inside_one_cell_is_injected() -> None:
    words = [{"text": "docs", "x0": 5, "x1": 25, "top": 5, "bottom": 15}, {"text": "other", "x0": 55, "x1": 85, "top": 5, "bottom": 15}]
    r = model_region(grid=[["docs", "other"], ["c", "d"]])
    ola._inject_hyperlinks_into_region(r, [{"x0": 4, "x1": 26, "top": 4, "bottom": 16, "uri": "https://example.com/x"}], words)
    assert r.table_grid[0] == ["docs (https://example.com/x)", "other"] and "https://example.com/x" in r.text and r.from_model_grid


# --- the invariant check and the rerun ------------------------------------------------------------------------------------


def swap_for(region: LayoutRegion) -> emit.Swap:
    import copy

    return emit.Swap(region, copy.deepcopy(region.table_grid), tuple(region.bbox), {})


def test_violations_name_a_lost_a_changed_and_a_shared_grid() -> None:
    words = [{"text": "alpha", "x0": 10, "x1": 30, "top": 10, "bottom": 20}]
    r = model_region()
    s = swap_for(r)
    assert emit.grid_violations([r], [s], words) == []
    assert emit.grid_violations([], [s], words) == ["region_lost"]
    r.table_grid = [["x"]]
    assert emit.grid_violations([r], [s], words) == ["grid_changed"]
    r.table_grid = [["a", "b"], ["c", "d"]]
    r.bbox = (0.0, 0.0, 120.0, 50.0)
    assert emit.grid_violations([r], [s], words) == ["grid_changed"]
    r.bbox = (0.0, 0.0, 100.0, 50.0)
    other = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(5.0, 5.0, 40.0, 40.0), text="alpha")
    assert emit.grid_violations([r, other], [s], words) == ["word_in_other_region"]
    away = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(150.0, 5.0, 190.0, 40.0), text="beta")
    assert emit.grid_violations([r, away], [s], words) == []


def test_a_made_up_bullet_inside_a_model_box_is_not_a_conflict() -> None:
    bullet = {"text": "•", "x0": 12, "x1": 16, "top": 10, "bottom": 20, "fontname": "_synthetic_bullet", "synthetic": True}
    stray = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(5.0, 5.0, 40.0, 40.0), text="•")
    r = model_region()
    assert emit.grid_violations([r, stray], [swap_for(r)], [bullet]) == []


def test_a_grid_that_changed_in_the_page_pass_sends_the_page_back_to_the_evidence_result(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    evidence = parse("evidence", monkeypatch)
    real = ola._resolve_overlaps
    state = {"n": 0}

    def tamper(regions, *a, **k):  # noqa: ANN202
        out = real(regions, *a, **k)
        state["n"] += 1
        for r in out:
            if r.from_model_grid and r.table_grid:
                r.table_grid = [row[:-1] for row in r.table_grid]  # as a re-read of the page would
        return out

    monkeypatch.setattr(ola, "_resolve_overlaps", tamper)
    client = FakeModelClient()
    hybrid = parse("hybrid", monkeypatch, client)
    monkeypatch.setattr(ola, "_resolve_overlaps", real)
    assert not [t for t in tables(hybrid) if t.from_model_grid]
    assert canonical(hybrid) == canonical(evidence)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert '"reason":"tail_conflict"' in text and '"tail_conflicts":5' in text


def test_a_second_table_in_the_box_page_is_not_changed_by_the_other_pages_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    # a conflict on one page reruns only that page; the other pages keep their model grids
    real = ola._resolve_overlaps

    def tamper(regions, *a, page=None, **k):  # noqa: ANN202
        out = real(regions, *a, page=page, **k)
        if page is not None and page.page_number == 3:
            for r in out:
                if r.from_model_grid:
                    r.table_grid = [["x"]]
        return out

    monkeypatch.setattr(ola, "_resolve_overlaps", tamper)
    hybrid = parse("hybrid", monkeypatch, FakeModelClient())
    on3 = [t for t in tables([hybrid[2]]) if t.from_model_grid]
    other = [t for i, p in enumerate(hybrid) if i != 2 for t in tables([p]) if t.from_model_grid]
    assert not on3 and len(other) >= 5


# --- a title inside the box -----------------------------------------------------------------------------------------------


def test_a_swallowed_caption_row_leaves_the_region_and_stays_as_the_words_it_is() -> None:
    ws = grid_words(BOX, rows=5, cols=3)
    title = [{"text": t, "x0": 44 + 40 * i, "x1": 74 + 40 * i, "top": 6, "bottom": 16} for i, t in enumerate(["Table", "1-3.", "Results"])]
    for t in title:  # inside the box vertically
        t["top"], t["bottom"] = BOX[1] + 1, BOX[1] + 9
    ws = [w for w in ws if w["top"] > BOX[1] + 14] + title
    def with_title(tokens):  # noqa: ANN202
        body = [t for t in tokens if t["text"] not in ("Table", "1-3.", "Results")]
        head = [t["id"] for t in tokens if t["text"] in ("Table", "1-3.", "Results")]
        nr, nc, cells = grid_cells(body)
        for c in cells:
            c["r0"] += 1
            c["r1"] += 1
        return nr + 1, nc, [{"r0": 0, "r1": 1, "c0": 0, "c1": nc, "tokens": head}, *cells]

    em = make_emitter(FakeModelClient({"p1:b0": with_title}))
    out = run_page(em, ws)
    row = em.rows[0]
    assert row["route"] == "tf" and row["caption_row_dropped"] and row["region_bbox_shrunk"]
    region = out[0]
    assert region.bbox[1] == pytest.approx(BOX[1] + 9.5) and all("Table" not in c for r in region.table_grid for c in r)
    assert row["word_loss_content"] == 0


# --- overlapping text blocks (a column of rotated margin text inside a wide paragraph block) --------------------------


def _margin_page_words() -> list[dict]:
    prose = [{"text": f"w{i}", "x0": 60 + 40 * (i % 8), "x1": 90 + 40 * (i % 8), "top": 40 + 14 * (i // 8), "bottom": 50 + 14 * (i // 8)} for i in range(40)]
    margin = [{"text": ch, "x0": 28, "x1": 36, "top": 250 + 9 * k, "bottom": 258 + 9 * k, "upright": False} for k, ch in enumerate("Thispublication")]
    return prose + margin


def test_a_word_in_two_overlapping_blocks_goes_to_the_smaller_one_when_asked() -> None:
    words = _margin_page_words()
    wide, strip = (26.0, 36.0, 542.0, 384.0), (26.0, 245.0, 40.0, 400.0)
    count = lambda regions: sum(len(r.text.split()) for r in regions)  # noqa: E731
    plain = ola._classify_text_blocks([wide, strip], [], words)
    assert count(plain) == 40 + 15 + 15  # the 15 margin characters are in both regions
    own = ola._classify_text_blocks([wide, strip], [], words, exclusive_words=True)
    assert count(own) == 40 + 15


def test_hybrid_pages_do_not_emit_a_margin_word_twice(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    def draw(c) -> None:
        c.setFont("Helvetica", 10)
        for r in range(30):
            c.drawString(80, 740 - 14 * r, f"Paragraph line {r} with a few words to make a wide block of prose")
        c.saveState()
        c.translate(40, 200)
        c.rotate(90)
        c.drawString(0, 0, "This publication is available free of charge from the source")
        c.restoreState()

    pdf = make_pdf(draw, tmp_path / "margin.pdf")
    # the native segmentation of such pages gives a wide block that also spans the margin column, and the column's own block
    monkeypatch.setattr(ola, "_segment_blocks_native", lambda words, pw, ph, h_rules, gutter_x=None: [(20.0, 30.0, 560.0, 770.0), (20.0, 100.0, 50.0, 460.0)])
    monkeypatch.setattr(ola, "_resolve_overlaps", lambda regions, **kw: list(regions))  # on real pages the two blocks sit in different column categories and are not merged
    monkeypatch.setattr(ola, "_detect_model_boxes", fake_boxes({"1": [[60, 60, 560, 120]]}))
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "evidence")
    ev = word_counter(parse_pdf_layout(pdf))
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "hybrid")
    real = emit.TableEmitter.__init__
    client = FakeModelClient()
    monkeypatch.setattr(emit.TableEmitter, "__init__", lambda self, c, p, **kw: real(self, c, p, **dict(kw, client=client)))
    hy = word_counter(parse_pdf_layout(pdf, None, cfg()))
    assert sum((ev - hy).values()) > 0, "the page must show the doubling in the evidence output"
    assert not (hy - ev) and sum(hy.values()) < sum(ev.values())


def test_a_page_sent_back_to_the_evidence_result_gives_words_to_text_blocks_as_evidence_does(monkeypatch: pytest.MonkeyPatch) -> None:
    real_resolve, real_classify = ola._resolve_overlaps, ola._classify_text_blocks
    flags: list[bool] = []

    def tamper(regions, *a, **k):  # noqa: ANN202
        out = real_resolve(regions, *a, **k)
        for r in out:
            if r.from_model_grid and r.table_grid:
                r.table_grid = [row[:-1] for row in r.table_grid]
        return out

    def classify(*a, exclusive_words: bool = False, **k):  # noqa: ANN202
        flags.append(exclusive_words)
        return real_classify(*a, exclusive_words=exclusive_words, **k)

    monkeypatch.setattr(ola, "_resolve_overlaps", tamper)
    monkeypatch.setattr(ola, "_classify_text_blocks", classify)
    parse("hybrid", monkeypatch, FakeModelClient())
    # the five table pages conflict: each rerun owns words as evidence does, every first pass as hybrid does
    assert flags.count(False) == 5 and flags.count(True) == len(flags) - 5
