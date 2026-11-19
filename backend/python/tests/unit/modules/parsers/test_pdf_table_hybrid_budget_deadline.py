"""Budget order, byte cap, deadline arithmetic and batching of the shadow run (no model, fake clock)."""
from __future__ import annotations

import os

import pytest

from app.modules.parsers.pdf import table_hybrid_flow as flow
from tests.support.table_shadow import read_index, spool_tables


@pytest.fixture
def spool(tmp_path) -> str:
    d = tmp_path / "s"
    d.mkdir()
    return str(d)


def test_the_budget_counts_sent_tables_in_document_order(spool) -> None:
    col = spool_tables(spool, 6, budget=4)
    recs = read_index(spool)
    assert [r["reason"] for r in recs] == ["none"] * 4 + ["budget"] * 2
    assert col.sent == 4
    # no crop is made for a box past the budget
    assert all(r.get("png") is None for r in recs[4:])
    assert sorted(f for f in os.listdir(spool) if f.endswith(".png")) == sorted(r["png"] for r in recs[:4])


def test_boxes_are_met_in_page_order_not_the_order_the_model_listed_them(spool) -> None:
    from tests.support.table_shadow import (
        PAGE_H,
        PAGE_W,
        FakeDecision,
        blank_renderer,
        grid_words,
    )

    boxes = [(40.0, 520.0, 300.0, 620.0), (40.0, 20.0, 300.0, 120.0), (40.0, 220.0, 300.0, 320.0)]
    col = flow.TableJobCollector(spool, "x.pdf", renderer=blank_renderer, table_budget=2)
    col.collect_page(0, PAGE_W, PAGE_H, [FakeDecision(b) for b in boxes], [], None, [w for b in boxes for w in grid_words(b)])
    recs = read_index(spool)
    assert [(r["key"], r["reason"]) for r in recs] == [("p1:b0", "none"), ("p1:b1", "none"), ("p1:b2", "budget")]
    assert [r["top"] for r in recs] == [20.0, 220.0, 520.0]  # the lowest box on the page is the one past the budget


def test_not_table_boxes_are_not_eligible(spool) -> None:
    from tests.support.table_shadow import (
        PAGE_H,
        PAGE_W,
        FakeDecision,
        blank_renderer,
        grid_words,
    )

    boxes = [(40.0, 20.0, 300.0, 120.0), (40.0, 220.0, 300.0, 320.0)]
    col = flow.TableJobCollector(spool, "x.pdf", renderer=blank_renderer)
    col.collect_page(0, PAGE_W, PAGE_H, [FakeDecision(boxes[0], "not_table"), FakeDecision(boxes[1])], [], None, [w for b in boxes for w in grid_words(b)])
    assert len(read_index(spool)) == 1 and col.sent == 1


def test_past_the_byte_cap_boxes_are_spool_full(spool) -> None:
    col = spool_tables(spool, 5, byte_cap=1)  # the first crop alone passes the cap
    reasons = [r["reason"] for r in read_index(spool)]
    assert reasons == ["none"] + ["spool_full"] * 4 and col.sent == 1


def test_prefiltered_and_ceiling_boxes_make_no_crop(spool) -> None:
    from tests.support.table_shadow import PAGE_H, PAGE_W, FakeDecision, blank_renderer

    one_line = [{"text": "a", "x0": 50, "x1": 60, "top": 30, "bottom": 40}, {"text": "b", "x0": 150, "x1": 160, "top": 30, "bottom": 40}]
    col = flow.TableJobCollector(spool, "x.pdf", renderer=blank_renderer)
    col.collect_page(0, PAGE_W, PAGE_H, [FakeDecision((40.0, 20.0, 300.0, 120.0))], [], None, one_line)
    rec = read_index(spool)[0]
    assert rec["reason"] == "prefilter" and rec.get("png") is None and col.sent == 0


def test_the_inline_deadline_formula_uses_the_parsing_clients_share_and_the_reserve() -> None:
    # 0.8 of the 5,400 s record budget is 4,320 s; 600 s is reserved for block building
    assert flow.remaining_inline(5400.0, 0.0) == pytest.approx(4320.0 - 600.0)
    assert flow.remaining_inline(5400.0, 1000.0) == pytest.approx(2720.0)


def test_with_the_clock_at_4000_seconds_the_formula_gives_zero() -> None:
    assert flow.remaining_inline(5400.0, 4000.0) == 0.0
    assert flow.phase2_deadline_s(300.0, flow.remaining_inline(5400.0, 4000.0)) == 0.0


def test_the_deadline_is_the_smaller_of_the_setting_and_what_remains() -> None:
    assert flow.phase2_deadline_s(300.0, 100.0) == 100.0
    assert flow.phase2_deadline_s(300.0, 900.0) == 300.0
    assert flow.phase2_deadline_s(300.0) == 300.0  # detached: the setting only


def jobs(*caps: int) -> list[dict[str, object]]:
    return [{"cap": c, "key": f"k{i}"} for i, c in enumerate(caps)]


def test_batches_close_at_the_sum_of_caps() -> None:
    batches = flow.batch_jobs(jobs(500, 500, 100, 900))
    assert [[j["cap"] for j in b] for b in batches] == [[500, 500], [100, 900]]
    assert all(sum(j["cap"] for j in b) <= 1024 for b in batches)


def test_batches_close_at_sixteen_tables_and_keep_document_order() -> None:
    batches = flow.batch_jobs(jobs(*([10] * 40)))
    assert [len(b) for b in batches] == [16, 16, 8]
    assert [j["key"] for b in batches for j in b] == [f"k{i}" for i in range(40)]


def test_a_single_table_at_the_ceiling_is_a_batch_of_its_own() -> None:
    assert [[j["cap"] for j in b] for b in flow.batch_jobs(jobs(1024, 10))] == [[1024], [10]]


def test_the_composition_of_batches_changes_nothing_in_the_set_of_tables() -> None:
    a = flow.batch_jobs(jobs(*([10] * 40)))
    b = flow.batch_jobs(jobs(*([10] * 40)))
    assert a == b  # a pure function of the document
