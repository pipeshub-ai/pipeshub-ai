"""Per-box plan, grid checks and comparison helpers of the table-structure shadow run (pure code)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.parsers.pdf import table_hybrid as th

CONFIG = json.loads((Path(__file__).resolve().parents[3] / "fixtures" / "table_hybrid" / "model_config.json").read_text())


def w(text: str, x0: float, top: float, x1: float | None = None, h: float = 10.0) -> dict:
    return {"text": text, "x0": x0, "x1": x1 if x1 is not None else x0 + 5 * len(text), "top": top, "bottom": top + h}


def test_constants_match_the_measured_configuration() -> None:
    assert th.LINE_JOIN_H == CONFIG["line_join_h"]
    assert th.SEGMENT_GAP_H == CONFIG["segment_gap_h"]
    assert th.PREFILTER_MIN_LINES == CONFIG["prefilter_min_lines"]
    assert th.CAP_K == CONFIG["cap_k"]
    assert th.CAP_MARGIN == CONFIG["cap_margin"]
    assert th.CAP_CEILING == CONFIG["cap_ceiling"]
    assert th.WORD_LOSS_MAX == CONFIG["word_loss_max"]
    assert th.MODEL_LIBRARY_VERSION == CONFIG["docling_ibm_models"]
    assert th.MODEL_MODE == CONFIG["mode"]


def test_lines_and_columns_of_a_grid_of_words() -> None:
    words = [w("a", 0, 0), w("b", 50, 0), w("c", 100, 0), w("d", 0, 20), w("e", 50, 20), w("f", 100, 20)]
    assert th.lines_cols(words) == (2, 3)


def test_words_in_one_run_are_one_segment() -> None:
    words = [w("hello", 0, 0, 25), w("world", 28, 0, 53), w("x", 0, 20), w("y", 100, 20)]
    assert th.lines_cols(words) == (2, 2)


def test_no_words_is_zero_lines() -> None:
    assert th.lines_cols([]) == (0, 0)


def test_step_cap_follows_the_frozen_rule_and_the_ceiling() -> None:
    assert th.step_cap(9, 5) == 124
    assert th.step_cap(500, 10) == th.CAP_CEILING


def test_a_single_line_is_prefiltered() -> None:
    plan = th.plan_box([w("a", 0, 0), w("b", 50, 0)])
    assert plan.reason == "prefilter" and plan.lines == 1


def test_a_table_that_cannot_fit_under_the_ceiling_is_a_cap_hit_without_a_call() -> None:
    words = [w("a", 0, 12 * r) for r in range(400)] + [w("b", 60, 12 * r) for r in range(400)]
    plan = th.plan_box(words)
    assert (plan.reason, plan.cap_reason) == ("cap_hit", "ceiling")


def test_an_ordinary_box_is_sent_with_its_cap() -> None:
    words = [w("a", 0, 0), w("b", 60, 0), w("c", 0, 20), w("d", 60, 20)]
    plan = th.plan_box(words)
    assert (plan.reason, plan.cap) == ("none", th.step_cap(2, 2))


def cell(r0, r1, c0, c1, tokens, bbox=None) -> dict:
    return {"r0": r0, "r1": r1, "c0": c0, "c1": c1, "tokens": tokens, "bbox": bbox}


def test_validate_accepts_a_clean_grid() -> None:
    cells = [cell(0, 1, 0, 1, [0]), cell(0, 1, 1, 2, [1]), cell(1, 2, 0, 2, [2])]
    check = th.validate_cells(cells, 2, 2, {0, 1, 2})
    assert check.ok and check.overlaps == 0


@pytest.mark.parametrize(
    ("cells", "why"),
    [
        ([cell(0, 1, 0, 3, [0])], "index_range"),
        ([cell(1, 1, 0, 1, [0])], "empty_span"),
        ([cell(0, 1, 0, 1, [9])], "unknown_token"),
        ([cell(0, 1, 0, 1, [0]), cell(0, 1, 1, 2, [0])], "token_twice"),
        ([], "no_cells"),
    ],
)
def test_validate_rejects_a_malformed_grid(cells, why) -> None:
    check = th.validate_cells(cells, 2, 2, {0, 1})
    assert (check.ok, check.reason) == (False, why)


def test_overlapping_cells_are_counted_not_malformed() -> None:
    cells = [cell(0, 2, 0, 1, [0]), cell(1, 2, 0, 2, [1])]
    check = th.validate_cells(cells, 2, 2, {0, 1})
    assert check.ok and check.overlaps == 1


def test_word_loss_over_content_tokens_ignores_marks() -> None:
    texts = {0: "12", 1: "$", 2: "abc", 3: "•"}
    cells = [cell(0, 1, 0, 1, [0, 2])]
    all_share, content = th.word_losses(texts, cells)
    assert (all_share, content) == (0.5, 0.0)
    cells = [cell(0, 1, 0, 1, [0, 3])]
    _, content = th.word_losses(texts, cells)
    assert content == 0.5


def test_would_route_order() -> None:
    ok = th.GridCheck(True, None, 0, 2, 2)
    assert th.would_route("timeout", None, None, 0.0) == ("evidence", "timeout")
    assert th.would_route(None, th.GridCheck(False, "x", 0, 2, 2), None, 0.0) == ("evidence", "malformed")
    assert th.would_route(None, ok, (1, 3), 0.0) == ("evidence", "small_grid")
    assert th.would_route(None, ok, (3, 3), 0.02) == ("evidence", "word_loss")
    assert th.would_route(None, ok, (3, 3), 0.01) == ("model", "none")


def test_model_shape_drops_empty_rows_and_columns() -> None:
    cells = [cell(0, 1, 0, 1, [0]), cell(0, 1, 1, 2, []), cell(2, 3, 0, 1, [1])]
    assert th.model_shape(cells) == (2, 1)


@pytest.mark.parametrize(
    ("box", "tables", "expected"),
    [
        ({1, 2, 3}, [{1, 2, 3}], "same_words"),
        ({1, 2, 3}, [{1, 2}], "box_extra"),
        ({1, 2}, [{1, 2, 3}], "table_extra"),
        ({1, 2}, [], "no_table"),
        ({1, 2, 3}, [{1}, {2, 3}], "multi"),
    ],
)
def test_box_class(box, tables, expected) -> None:
    assert th.box_class(box, tables) == expected


def test_glyph_candidates_count_a_bullet_beside_a_number_only() -> None:
    texts = {0: "•", 1: "1,515", 2: "•", 3: "text", 4: "!="}
    cells = [cell(0, 1, 0, 1, [0, 1]), cell(0, 1, 1, 2, [2, 3]), cell(0, 1, 2, 3, [4, 1])]
    assert th.glyph_candidates(texts, cells) == 1


def test_hard_flags_detect_a_token_in_two_cells() -> None:
    words = {0: w("a", 0, 0), 1: w("b", 50, 0)}
    words = {k: {**v} for k, v in words.items()}
    good = [cell(0, 1, 0, 1, [0], [0, 0, 10, 10]), cell(0, 1, 1, 2, [1], [50, 0, 60, 10])]
    assert th.hard_flags(words, good, (1, 2))["bij"] is True
    dup = [cell(0, 1, 0, 1, [0, 1]), cell(0, 1, 1, 2, [1])]
    assert th.hard_flags(words, dup, (1, 2))["bij"] is False


def test_glyph_candidates_count_what_the_review_counts() -> None:
    texts = {0: "•", 1: "•", 2: "38", 3: "•", 4: "text", 5: "•", 6: "$", 7: "1", 8: "!="}
    cells = [
        cell(0, 1, 0, 1, [0, 1, 2]),  # two bullets before a number: both count
        cell(0, 1, 1, 2, [3]),  # a bullet alone in a cell
        cell(0, 1, 2, 3, [5, 4]),  # a bullet in a cell of words: not counted
        cell(0, 1, 3, 4, [6, 7]),  # no bullet
        cell(0, 1, 4, 5, [8]),
    ]
    assert th.glyph_candidates(texts, cells) == 3


def test_a_made_up_word_is_recognised_by_its_origin() -> None:
    assert th.is_synthetic({"text": "•", "synthetic": True})
    assert not th.is_synthetic({"text": "•", "fontname": "_synthetic_bullet"})  # the font name is not the test
    assert not th.is_synthetic({"text": "•", "fontname": "Arial"})
    assert not th.is_synthetic({"text": "•"})


def test_a_cell_with_words_on_both_sides_of_a_spanning_rule_is_a_crossing() -> None:
    words = {0: w("a", 10, 0, 20), 1: w("b", 10, 30, 20)}
    one_cell = [cell(0, 1, 0, 1, [0, 1])]
    assert th.rule_crossings(words, one_cell, [(25.0, 0.0, 100.0)], []) == 1
    assert th.rule_crossings(words, one_cell, [(25.0, 60.0, 100.0)], []) == 0  # a rule beside the words
    assert th.rule_crossings(words, one_cell, [(5.0, 0.0, 100.0)], []) == 0  # a rule through the first word only
    two_cells = [cell(0, 1, 0, 1, [0]), cell(1, 2, 0, 1, [1])]
    assert th.rule_crossings(words, two_cells, [(25.0, 0.0, 100.0)], []) == 0
    side = {0: w("a", 10, 0, 20), 1: w("b", 60, 0, 70)}
    assert th.rule_crossings(side, [cell(0, 1, 0, 1, [0, 1])], [], [(40.0, -5.0, 20.0)]) == 1


def test_strict_word_loss_counts_any_unlisted_word_with_a_letter_or_digit() -> None:
    texts = {i: f"w{i}" for i in range(200)}
    texts[200] = "•"
    cells = [cell(0, 1, 0, 1, [i for i in range(200) if i != 7])]
    assert th.content_words_lost(texts, cells) == 1  # the bullet is not content
    assert th.word_losses(texts, cells)[1] == pytest.approx(1 / 200)  # under the 1 % figure
    assert th.content_words_lost(texts, [cell(0, 1, 0, 1, list(range(201)))]) == 0


def test_content_excess_is_what_the_grid_holds_beyond_the_boxs_own_words() -> None:
    words = ["Net", "sales", "1,515", "2,030"]
    assert th.content_excess(["Net sales", "1,515", "2,030"], words) == 0
    assert th.content_excess(["Net sales", "1,515", "2,030", "Table 3"], words) == len("Table3")  # a caption inside the grid
    assert th.content_excess(["Net"], words) == 0  # a grid that holds less is not counted
    assert th.content_excess(["Net", "•", ". ."], words) == 0  # marks are not content
