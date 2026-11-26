"""Cell text and grid of the emit phase: geometric order with raised marks last, dot leaders, the swallowed caption row,
no binding across cells, empty rows and columns, overlapping cells."""
from __future__ import annotations

import pytest

from app.modules.parsers.pdf import table_hybrid_text as tt


def w(text: str, x0: float, top: float, x1: float, bottom: float, size: float = 10.0, **kw) -> dict:
    return {"text": text, "x0": x0, "top": top, "x1": x1, "bottom": bottom, "size": size, **kw}


def cell(r0: int, c0: int, tokens: list[int], r1: int | None = None, c1: int | None = None) -> dict:
    return {"r0": r0, "r1": r1 if r1 is not None else r0 + 1, "c0": c0, "c1": c1 if c1 is not None else c0 + 1, "tokens": tokens}


# --- order -----------------------------------------------------------------------------------------------------------


def test_words_join_in_geometric_order_not_in_id_order() -> None:
    # ids run right to left and bottom to top
    ws = [w("world", 40, 30, 70, 40), w("again", 0, 30, 30, 40), w("hello", 40, 10, 70, 20), w("say", 0, 10, 30, 20)]
    assert tt.join_words(ws) == "say hello again world"


@pytest.fixture
def marks_last(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tt, "RAISED_MARKS_LAST", True)


def test_by_default_a_raised_mark_stays_where_it_stands() -> None:
    assert tt.RAISED_MARKS_LAST is False
    ws = [w("As", 0, 10, 10, 20), w("Reported", 12, 10, 50, 20), w("(1)", 52, 6, 60, 12, size=6), w("(GAAP)", 62, 10, 100, 20)]
    assert tt.join_words(ws) == "As Reported (1) (GAAP)"


def test_a_raised_mark_goes_last_in_its_line(marks_last: None) -> None:
    ws = [w("As", 0, 10, 10, 20), w("Reported", 12, 10, 50, 20), w("(1)", 52, 6, 60, 12, size=6), w("(GAAP)", 62, 10, 100, 20)]
    assert tt.join_words(ws) == "As Reported (GAAP) (1)"


def test_a_mark_pattern_that_is_not_small_and_raised_keeps_its_place(marks_last: None) -> None:
    ws = [w("Item", 0, 10, 30, 20), w("(1)", 32, 10, 50, 20), w("total", 52, 10, 90, 20)]
    assert tt.join_words(ws) == "Item (1) total"
    ws = [w("Item", 0, 10, 30, 20), w("(1)", 32, 8, 40, 14, size=6), w("total", 42, 10, 80, 20)]  # small and raised: last
    assert tt.join_words(ws) == "Item total (1)"
    ws = [w("Item", 0, 10, 30, 20), w("(1)", 32, 14, 40, 20, size=6), w("total", 42, 10, 80, 20)]  # small but on the baseline
    assert tt.join_words(ws) == "Item (1) total"


def test_a_cell_of_marks_alone_has_no_marks() -> None:
    assert tt.raised_marks([w("(1)", 0, 6, 8, 12, size=6)]) == set()


def test_marks_are_reordered_never_deleted(marks_last: None) -> None:
    # an exponent: "2" small and raised after "<": every word is still in the text
    ws = [w("<", 0, 10, 8, 20), w("2", 10, 6, 15, 12, size=6), w("64", 16, 6, 24, 12, size=6)]
    out = tt.join_words(ws)
    assert sorted(out.split()) == sorted(["<", "2", "64"])
    ws = [w("x", 0, 10, 8, 20), w("2", 10, 6, 15, 12, size=6)]
    assert tt.join_words(ws) == "x 2"


def test_non_upright_words_keep_the_id_order() -> None:
    ws = [w("b", 0, 30, 10, 40, upright=False), w("a", 0, 10, 10, 20, upright=False)]
    assert tt.join_words(ws) == "b a"


# --- dot leaders ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("Introduction ......", "Introduction"),
        ("Introduction . . . . .", "Introduction"),
        ("Introduction ....", "Introduction"),
        ("....", ""),
        (". . . . . .", ""),
        ("Introduction ...", "Introduction ..."),
        ("IF you have expenses for . . .", "IF you have expenses for . . ."),
        ("trace_info(...)", "trace_info(...)"),
        ("and so on…", "and so on…"),
        ("v1.2.3.4", "v1.2.3.4"),
    ],
)
def test_dot_leaders_four_or_more_go_three_stay(raw: str, want: str) -> None:
    assert tt.strip_leader(raw).strip() == want


# --- the grid ----------------------------------------------------------------------------------------------------


def test_a_cell_text_is_in_its_top_left_position_and_covered_positions_are_empty() -> None:
    words = [w("Head", 0, 0, 30, 10), w("a", 0, 20, 10, 30), w("b", 50, 20, 60, 30)]
    cells = [cell(0, 0, [0], c1=2), cell(1, 0, [1]), cell(1, 1, [2])]
    em = tt.build_grid(cells, 2, 2, words)
    assert em.grid == [["Head", ""], ["a", "b"]]
    assert em.emitted_ids == {0, 1, 2} and em.spans == 1


def test_empty_rows_and_columns_are_dropped_and_counted() -> None:
    words = [w("a", 0, 0, 10, 10), w("b", 50, 0, 60, 10), w("c", 0, 40, 10, 50), w("d", 50, 40, 60, 50)]
    cells = [cell(0, 0, [0]), cell(0, 2, [1]), cell(2, 0, [2]), cell(2, 2, [3])]
    em = tt.build_grid(cells, 3, 3, words)
    assert em.grid == [["a", "b"], ["c", "d"]]
    assert em.empty_rows_cols_dropped == 2


def test_overlapping_cells_last_wins_and_the_losing_text_is_not_emitted() -> None:
    words = [w("early", 0, 0, 30, 10), w("late", 0, 0, 30, 10)]
    cells = [cell(0, 0, [0]), cell(0, 0, [1])]
    em = tt.build_grid(cells, 1, 1, words)
    assert em.overlaps == 1 and em.grid == [["late"]]
    assert tt.unassigned_content(words, em.emitted_ids, em.dropped_ids) == [0]  # the caller turns this into a word_loss


def test_a_leader_in_a_cell_is_stripped_and_counted() -> None:
    words = [w("Scope", 0, 0, 30, 10), w(".......", 32, 0, 60, 10), w("12", 70, 0, 80, 10), w("Intro", 0, 20, 30, 30), w("3", 70, 20, 80, 30)]
    em = tt.build_grid([cell(0, 0, [0, 1]), cell(0, 1, [2]), cell(1, 0, [3]), cell(1, 1, [4])], 2, 2, words)
    assert em.grid == [["Scope", "12"], ["Intro", "3"]] and em.leaders_stripped == 1


# --- the caption row -------------------------------------------------------------------------------------------


def caption_grid(title: str, *, second_cell: bool = False) -> tuple[list[dict], list[dict]]:
    words = [w(t, 10 * i, 0, 10 * i + 8, 10) for i, t in enumerate(title.split())]
    n = len(words)
    words += [w("a", 0, 20, 10, 30), w("1", 50, 20, 60, 30), w("b", 0, 40, 10, 50), w("2", 50, 40, 60, 50)]
    cells = [cell(0, 0, list(range(n)), c1=2), cell(1, 0, [n]), cell(1, 1, [n + 1]), cell(2, 0, [n + 2]), cell(2, 1, [n + 3])]
    if second_cell:
        cells = [cell(0, 0, list(range(n - 1))), cell(0, 1, [n - 1])] + cells[1:]  # noqa: RUF005
    return words, cells


@pytest.mark.parametrize("title", ["Table 1-13. Results", "Table B2 Summary", "Table T.N.4.6. Rates", "Table 9-6: Register map"])
def test_a_swallowed_table_caption_row_is_dropped_and_the_box_top_moves(title: str) -> None:
    words, cells = caption_grid(title)
    em = tt.build_grid(cells, 3, 2, words)
    assert em.caption_row_dropped and em.grid == [["a", "1"], ["b", "2"]]
    assert em.bbox_top == pytest.approx(10.5)
    assert em.dropped_ids and not tt.unassigned_content(words, em.emitted_ids, em.dropped_ids)  # a dropped row is not word loss
    assert em.dropped_ids.isdisjoint(em.emitted_ids)


@pytest.mark.parametrize("title", ["Total revenue", "Figure 3 shows", "Tabelle 3 Ergebnisse", "Table of contents"])
def test_other_first_rows_are_kept(title: str) -> None:
    words, cells = caption_grid(title)
    em = tt.build_grid(cells, 3, 2, words)
    assert not em.caption_row_dropped and len(em.grid) == 3 and em.bbox_top is None


def test_a_caption_row_with_two_cells_is_kept() -> None:
    words, cells = caption_grid("Table 1 Results", second_cell=True)
    assert not tt.build_grid(cells, 3, 2, words).caption_row_dropped


# --- no binding across cells, whitespace -------------------------------------------------------------------


def test_a_currency_sign_stays_in_its_cell_and_is_counted() -> None:
    words = [w("$", 0, 0, 5, 10), w("1,234", 40, 0, 70, 10), w("Total", 0, 20, 30, 30), w("5", 40, 20, 50, 30)]
    em = tt.build_grid([cell(0, 0, [0]), cell(0, 1, [1]), cell(1, 0, [2]), cell(1, 1, [3])], 2, 2, words)
    assert em.grid == [["$", "1,234"], ["Total", "5"]]
    assert tt.dollar_cross_candidates(em.grid) == 1


def test_a_percent_sign_in_a_neighbour_cell_is_not_moved() -> None:
    words = [w("12", 0, 0, 10, 10), w("%", 40, 0, 50, 10), w("a", 0, 20, 10, 30), w("b", 40, 20, 50, 30)]
    em = tt.build_grid([cell(0, 0, [0]), cell(0, 1, [1]), cell(1, 0, [2]), cell(1, 1, [3])], 2, 2, words)
    assert em.grid[0] == ["12", "%"]


def test_whitespace_runs_collapse() -> None:
    assert tt.cell_text([w("a", 0, 0, 5, 10), w("  b ", 10, 0, 15, 10)]) == "a b"


def test_has_content_means_a_letter_or_a_digit() -> None:
    assert tt.has_content("x") and tt.has_content("1") and not tt.has_content("....") and not tt.has_content("$")



def test_a_sign_no_cell_holds_is_lost_content() -> None:
    from app.modules.parsers.pdf import table_hybrid_text as tt

    words = [{"text": "-"}, {"text": "125"}, {"text": "+"}, {"text": "·"}]
    # the grid holds the amount but not its sign: the row has lost what the value means
    assert tt.unassigned_content(words, {1}, set()) == [0, 2]
    assert tt.unassigned_content(words, {0, 1, 2}, set()) == []
