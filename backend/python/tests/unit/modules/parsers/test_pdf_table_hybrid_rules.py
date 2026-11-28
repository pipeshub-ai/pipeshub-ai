"""The repair of a model cell that a horizontal drawn rule runs through (the rule definition itself is drawn_rules.py)."""
from __future__ import annotations

from app.modules.parsers.pdf import table_hybrid_text as tt


def words_two_rows() -> list[dict]:
    return [
        {"text": "head", "x0": 50, "x1": 80, "top": 100, "bottom": 110},
        {"text": "tail", "x0": 90, "x1": 120, "top": 100, "bottom": 110},
        {"text": "next", "x0": 50, "x1": 80, "top": 130, "bottom": 140},
        {"text": "more", "x0": 90, "x1": 120, "top": 130, "bottom": 140},
        {"text": "solo", "x0": 150, "x1": 180, "top": 100, "bottom": 110},
        {"text": "solo2", "x0": 150, "x1": 180, "top": 130, "bottom": 140},
    ]


def test_the_rules_a_cell_would_be_cut_along_are_the_horizontal_crossings() -> None:
    from app.modules.parsers.pdf.drawn_rules import crossings, h_crossing_ys

    ws = words_two_rows()
    assert h_crossing_ys(ws[:4], [(120.0, 40, 130)]) == [120.0]
    assert h_crossing_ys(ws[:4], [(120.0, 100, 110)]) == []  # a rule that does not span the words
    cells = [{"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0, 1, 2, 3]}]
    assert crossings(dict(enumerate(ws)), cells, [(120.0, 40, 130)], []) == (1, 0, 1)


def test_the_row_is_cut_at_the_rule_and_every_cell_of_it_is_split() -> None:
    ws = words_two_rows()
    cells = [
        {"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0, 1, 2, 3]},  # crossed
        {"r0": 0, "r1": 1, "c0": 1, "c1": 2, "tokens": [4, 5]},  # its neighbour holds words on both sides too
        {"r0": 1, "r1": 2, "c0": 0, "c1": 2, "tokens": []},
    ]
    out, n_rows, n_cut = tt.split_rows_at_rules(cells, 2, ws, [(120.0, 40, 200)])
    assert (n_rows, n_cut) == (3, 1)
    got = {(c["r0"], c["c0"]): c["tokens"] for c in out if c["tokens"]}
    assert got == {(0, 0): [0, 1], (1, 0): [2, 3], (0, 1): [4], (1, 1): [5]}
    assert [(c["r0"], c["r1"]) for c in out if not c["tokens"]] == [(2, 3)]  # the row below moved down by one


def test_a_spanning_cell_stretches_over_the_new_rows_and_nothing_changes_without_a_crossing() -> None:
    ws = words_two_rows()
    cells = [
        {"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0, 1, 2, 3]},
        {"r0": 0, "r1": 2, "c0": 1, "c1": 2, "tokens": [4]},
    ]
    out, n_rows, _ = tt.split_rows_at_rules(cells, 2, ws, [(120.0, 40, 130)])
    span = next(c for c in out if c["c0"] == 1)
    assert (span["r0"], span["r1"], n_rows) == (0, 3, 3)
    same, n, k = tt.split_rows_at_rules(cells, 2, ws, [(300.0, 40, 130)])
    assert same is cells and (n, k) == (2, 0)
