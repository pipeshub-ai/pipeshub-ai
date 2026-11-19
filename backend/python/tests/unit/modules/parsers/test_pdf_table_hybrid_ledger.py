"""The fallback ledger of the emit phase: every reason is reachable, the order is fixed, and a box that passes carries the
model's grid. A fake service stands in for the model; the page words are generated."""
from __future__ import annotations

import pytest

from app.modules.parsers.pdf import table_hybrid_emit as emit
from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegionType
from tests.support.table_emit import (
    BOX,
    FakeModelClient,
    FakePage,
    make_emitter,
    reasons,
    run_page,
)
from tests.support.table_shadow import grid_words


def words(box=BOX, rows: int = 5, cols: int = 3):  # noqa: ANN201
    return grid_words(box, rows, cols)


def test_a_box_that_passes_carries_the_model_grid() -> None:
    em = make_emitter(FakeModelClient())
    out = run_page(em, words())
    assert reasons(em) == ["none"] and em.rows[0]["route"] == "tf"
    region = out[0]
    assert region.type == LayoutRegionType.TABLE and region.from_model_grid and region.from_evidence
    assert region.bbox == BOX and len(region.table_grid) == 5 and region.table_grid[0] == ["w00", "w01", "w02"]
    assert region.text.splitlines()[1] == "w10 | w11 | w12"


def test_a_not_table_box_is_never_sent() -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    out = run_page(em, words(), kinds=["not_table"])
    assert client.calls == 0 and reasons(em) == [] and out[0].text == "ev"


def test_prefilter_one_line_box() -> None:
    em = make_emitter(FakeModelClient())
    run_page(em, words(rows=1))
    assert reasons(em) == ["prefilter"]


def test_cap_hit_from_the_model_keeps_evidence() -> None:
    em = make_emitter(FakeModelClient({"p1:b0": "cap_hit"}))
    out = run_page(em, words())
    assert reasons(em) == ["cap_hit"] and out[0].text == "ev"


def test_box_no_table_when_the_evidence_path_found_none() -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    run_page(em, words(), ev_boxes=[(400.0, 600.0, 500.0, 700.0)])
    assert reasons(em) == ["box_no_table"] and client.calls == 0


def test_box_multi_counts_every_overlapping_evidence_table() -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    top, bottom = (40.0, 20.0, 300.0, 70.0), (40.0, 72.0, 300.0, 120.0)
    run_page(em, words(), ev_boxes=[top, bottom])
    assert reasons(em) == ["box_multi"] and client.calls == 0


def test_box_table_extra_when_the_evidence_table_holds_a_content_word_outside_the_box() -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    ws = words() + [{"text": "outside", "x0": 40, "x1": 90, "top": 130, "bottom": 140}]  # noqa: RUF005
    run_page(em, ws, ev_boxes=[(40.0, 20.0, 300.0, 150.0)])
    assert reasons(em) == ["box_table_extra"] and client.calls == 0


def test_box_table_extra_is_decided_on_the_words_not_on_the_box_class() -> None:
    # the box also holds words the evidence table lacks (box_class says box_extra), and the table holds one the box lacks
    client = FakeModelClient()
    em = make_emitter(client)
    ws = [*words(), {"text": "left", "x0": 22, "x1": 36, "top": 24, "bottom": 34}]
    run_page(em, ws, ev_boxes=[(20.0, 20.0, 300.0, 80.0)])
    assert em.rows[0]["box_class"] == "box_extra" and reasons(em) == ["box_table_extra"] and client.calls == 0


def test_a_punctuation_word_outside_the_box_does_not_make_box_table_extra() -> None:
    em = make_emitter(FakeModelClient())
    ws = words() + [{"text": "....", "x0": 40, "x1": 90, "top": 130, "bottom": 140}]  # noqa: RUF005
    run_page(em, ws, ev_boxes=[(40.0, 20.0, 300.0, 150.0)])
    assert reasons(em) == ["none"]


def test_box_extra_words_in_the_box_outside_the_evidence_table_are_allowed() -> None:
    em = make_emitter(FakeModelClient())
    ws = words()
    out = run_page(em, ws, ev_boxes=[(40.0, 20.0, 300.0, 80.0)])
    assert reasons(em) == ["none"] and em.rows[0]["box_class"] == "box_extra"
    assert sum(1 for row in out[0].table_grid for c in row if c) == 15  # every word of the box is in the grid


def test_small_grid() -> None:
    em = make_emitter(FakeModelClient({"p1:b0": lambda tokens: (1, 1, [{"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [t["id"] for t in tokens]}])}))
    run_page(em, words())
    assert reasons(em) == ["small_grid"]


def test_one_unassigned_content_word_is_a_word_loss() -> None:
    tall = (40.0, 20.0, 300.0, 400.0)
    em = make_emitter(FakeModelClient({"p1:b0": "drop:4"}))
    out = run_page(em, words(tall, rows=20, cols=5), [tall])  # 100 words in the box: 1 percent would have tolerated one
    assert reasons(em) == ["word_loss"] and out[0].text == "ev" and em.rows[0]["word_loss_content"] == 1


def test_an_unassigned_word_without_a_letter_or_digit_is_not_a_word_loss() -> None:
    ws = words() + [{"text": "...", "x0": 70, "x1": 90, "top": 100, "bottom": 110}]  # noqa: RUF005
    em = make_emitter(FakeModelClient({"p1:b0": "drop:15"}))
    run_page(em, ws)
    assert reasons(em) == ["none"]


def test_malformed_grid() -> None:
    em = make_emitter(FakeModelClient({"p1:b0": "malformed"}))
    run_page(em, words())
    assert reasons(em) == ["malformed"]


def test_two_tokens_with_one_box_are_malformed_without_a_request() -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    ws = words()
    ws.append(dict(ws[0], text="again"))
    run_page(em, ws)
    assert reasons(em) == ["malformed"] and client.calls == 0


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [("service_busy", "service_busy"), ("service_down", "service_down"), ("timeout", "timeout"), ("error", "error"), ("unsupported", "unsupported"), ("mode_fast", "mode_fast")],
)
def test_a_failed_request_is_the_evidence_result(outcome: str, reason: str) -> None:
    em = make_emitter(FakeModelClient(outcome=outcome))
    out = run_page(em, words())
    assert reasons(em) == [reason] and out[0].text == "ev"


def test_a_table_the_service_could_not_read_keeps_evidence() -> None:
    em = make_emitter(FakeModelClient({"p1:b0": "err:timeout"}))
    run_page(em, words())
    assert reasons(em) == ["timeout"]


def test_a_service_with_other_weights_is_a_version_mismatch() -> None:
    from tests.support.table_emit import measured_model

    other = measured_model().model_copy(update={"weights_sha256": "0" * 64})
    em = make_emitter(FakeModelClient(model=other))
    out = run_page(em, words())
    assert reasons(em) == ["version_mismatch"] and out[0].text == "ev"


def test_budget_counts_sent_tables_in_document_order() -> None:
    em = make_emitter(FakeModelClient(), max_tables=2)
    boxes = [(40.0, 20.0 + 110 * i, 300.0, 20.0 + 110 * i + 100) for i in range(3)]
    ws = [w for b in boxes for w in grid_words(b)]
    run_page(em, ws, boxes)
    assert reasons(em) == ["none", "none", "budget"]


def test_a_failing_renderer_is_collect_error_for_the_page() -> None:
    from app.modules.parsers.pdf.table_hybrid_emit import TableEmitter

    def boom(_i: int):  # noqa: ANN202
        raise RuntimeError("render")

    from tests.support.table_emit import cfg

    em = TableEmitter(cfg(), "x.pdf", client=FakeModelClient(), renderer=boom)
    out = run_page(em, words())
    assert reasons(em) == ["collect_error"] and out[0].text == "ev"


def test_a_bug_in_the_swap_is_emit_error_and_the_page_is_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    em = make_emitter(FakeModelClient())

    def boom(*a, **k):  # noqa: ANN202
        raise RuntimeError("bug")

    monkeypatch.setattr(emit.tt, "build_grid", boom)
    out = run_page(em, words())
    assert reasons(em) == ["emit_error"] and out[0].text == "ev" and em.totals.routed_tf == 0


# --- rule_conflict: only on ruled boxes ----------------------------------------------------------------------------


def ruled_page() -> FakePage:
    # a horizontal rule that runs through the middle of one model cell's words
    ws = words()
    y = (ws[0]["bottom"] + ws[3]["top"]) / 2  # between printed rows 0 and 1
    return FakePage(lines=[{"x0": 30, "x1": 310, "top": y, "bottom": y}])


def two_by_two_with_a_crossing(tokens: list[dict]) -> tuple[int, int, list[dict]]:
    # one cell holds the words of two printed rows: a drawn rule between them crosses it
    ids = [t["id"] for t in tokens]
    cells = [{"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": ids[:6]}, {"r0": 0, "r1": 1, "c0": 1, "c1": 2, "tokens": ids[6:9]},
             {"r0": 1, "r1": 2, "c0": 0, "c1": 1, "tokens": ids[9:12]}, {"r0": 1, "r1": 2, "c0": 1, "c1": 2, "tokens": ids[12:]}]
    return 2, 2, cells


def test_a_cell_crossed_by_a_horizontal_rule_is_cut_along_it_on_a_ruled_box() -> None:
    ws = words()
    em = make_emitter(FakeModelClient({"p1:b0": two_by_two_with_a_crossing}))
    out = run_page(em, ws, kinds=["ruled"], page=ruled_page())
    row = em.rows[0]
    assert reasons(em) == ["none"] and row["rows_split"] == 1 and row["rule_crossings_h"] >= 1 and row["rule_crossings_v"] == 0
    grid = out[0].table_grid
    assert len(grid) == 3 and grid[0][0] == "w00 w01 w02" and grid[1][0].startswith("w10")  # the cell's two printed rows are two rows
    assert sorted(t for r in grid for c in r for t in c.split()) == sorted(w["text"] for w in ws)  # every word still once


def test_a_group_box_is_not_cut_at_a_rule_but_the_crossing_is_counted() -> None:
    em = make_emitter(FakeModelClient({"p1:b0": two_by_two_with_a_crossing}))
    out = run_page(em, words(), kinds=["group"], page=ruled_page())
    assert reasons(em) == ["none"] and "rows_split" not in em.rows[0] and em.rows[0]["rule_crossings_h"] >= 1 and len(out[0].table_grid) == 2


def test_repair_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emit, "REPAIR_AT_RULES", False)
    em = make_emitter(FakeModelClient({"p1:b0": two_by_two_with_a_crossing}))
    out = run_page(em, words(), kinds=["ruled"], page=ruled_page())
    assert len(out[0].table_grid) == 2


def test_every_ledger_reason_is_named() -> None:
    used = {"prefilter", "cap_hit", "box_no_table", "box_multi", "box_table_extra", "small_grid", "word_loss", "malformed", "service_busy", "service_down",
            "timeout", "error", "unsupported", "mode_fast", "version_mismatch", "budget", "collect_error", "emit_error"}
    assert used <= set(emit.EVIDENCE_REASONS)


# --- tokens: made-up bullets are not tokens --------------------------------------------------------------------------


def test_a_made_up_bullet_is_not_sent_and_never_in_a_cell() -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    ws = words()
    bullets = [{"text": "•", "x0": 42 + 0, "x1": 46, "top": 24 + 18 * r, "bottom": 30 + 18 * r, "fontname": "_synthetic_bullet", "synthetic": True} for r in range(5)]
    out = run_page(em, ws + bullets)
    sent = client.requests[0]["meta"]["tables"][0]["tokens"]
    assert len(sent) == 15 and all(t["text"] != "•" for t in sent)
    assert all("•" not in c for row in out[0].table_grid for c in row)
    assert reasons(em) == ["none"]


def test_telemetry_carries_no_word_text(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.INFO)
    em = make_emitter(FakeModelClient())
    run_page(em, words())
    em.finish()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "table hybrid emit table:" in text and "table hybrid emit document:" in text
    assert "w00" not in text and "w12" not in text


def test_a_crop_box_offset_shifts_the_boxes_and_tokens_of_the_request() -> None:
    from app.modules.parsers.pdf.table_hybrid_emit import TableEmitter
    from tests.support.table_emit import cfg
    from tests.support.table_shadow import PAGE_H, PAGE_W, blank_renderer

    def shifted(i: int):  # noqa: ANN202
        img, w, h = blank_renderer(i)
        return img, w, h, 10.0, 6.0, w, h

    client = FakeModelClient()
    em = TableEmitter(cfg(), "x.pdf", client=client, renderer=shifted)
    ws = words()
    run_page(em, ws)
    spec = client.requests[0]["meta"]["tables"][0]
    assert spec["tokens"][0]["bbox"][0] == pytest.approx((ws[0]["x0"] - 10.0) * 2) and spec["tokens"][0]["bbox"][1] == pytest.approx((ws[0]["top"] - 6.0) * 2)
    sf = spec["scale_factor"]
    assert spec["table_bbox"][0] == pytest.approx(round(BOX[0] - 10.0) * 2 * sf) and spec["table_bbox"][1] == pytest.approx(round(BOX[1] - 6.0) * 2 * sf)
    assert PAGE_W and PAGE_H and reasons(em) == ["none"]


def test_a_word_longer_than_the_endpoint_accepts_is_cut_in_the_request_and_whole_in_the_cell() -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    ws = words()
    ws[0] = dict(ws[0], text="x" * 300)
    out = run_page(em, ws)
    assert len(client.requests[0]["meta"]["tables"][0]["tokens"][0]["text"]) == 256
    assert out[0].table_grid[0][0] == "x" * 300


def test_a_request_the_endpoint_would_refuse_is_invalid_input_before_it_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    monkeypatch.setattr(emit, "MAX_META_BYTES", 10)
    run_page(em, words())
    assert reasons(em) == ["invalid_input"] and client.calls == 0


# --- box_table_extra by content: text the evidence grid holds and the box's words do not ----------------------------------


def _with_grid(em, extra: str):  # noqa: ANN202
    ws = words()
    from tests.support.table_emit import evidence_region
    from tests.support.table_shadow import PAGE_H, PAGE_W, FakeDecision

    ev = evidence_region(BOX, ws)
    ev.table_grid = [[extra, *[w["text"] for w in ws]]]  # the line above the box, read into the first cell by the evidence path
    em.begin(1)
    return em.swap_page(0, None, PAGE_W, PAGE_H, [FakeDecision(BOX, "group")], [ev], ws)


def test_a_line_the_evidence_cell_holds_above_the_box_is_box_table_extra_though_its_centre_is_outside_the_table() -> None:
    client = FakeModelClient()
    em = make_emitter(client)
    out = _with_grid(em, "resolution prior to delivery")
    assert reasons(em) == ["box_table_extra"] and client.calls == 0 and out[0].text == "ev"


def test_one_stray_character_is_not_enough() -> None:
    em = make_emitter(FakeModelClient())
    _with_grid(em, "a")
    assert reasons(em) == ["none"]
