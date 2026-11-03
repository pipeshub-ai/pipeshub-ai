"""select_canonical_chain_names: one ancestor chain, same for every backend."""

import pytest

from app.services.graph_db.common.utils import (
    CANONICAL_PARENT_RELATION_TYPES,
    PATH_MAX_CANDIDATES,
    select_canonical_chain_names,
)


def test_constants():
    assert CANONICAL_PARENT_RELATION_TYPES == ("PARENT_CHILD", "ATTACHMENT")
    assert PATH_MAX_CANDIDATES == 64


def test_longest_chain_wins_and_is_root_first():
    rows = [
        {"ids": ["f"], "names": ["f.txt"]},
        {"ids": ["f", "d", "r"], "names": ["f.txt", "Docs", "Root"]},
        {"ids": ["f", "d"], "names": ["f.txt", "Docs"]},
    ]
    assert select_canonical_chain_names(rows, ("names",)) == ["Root", "Docs", "f.txt"]


@pytest.mark.parametrize("reverse", [False, True])
def test_tie_broken_by_smallest_id_sequence_regardless_of_row_order(reverse):
    rows = [
        {"ids": ["f", "p2"], "names": ["f", "Two"]},
        {"ids": ["f", "p1"], "names": ["f", "One"]},
    ]
    if reverse:
        rows.reverse()
    assert select_canonical_chain_names(rows, ("names",)) == ["One", "f"]


def test_first_non_empty_name_field_is_used_and_nameless_nodes_dropped():
    rows = [{"ids": ["g", "p", "q"], "groupNames": ["G", "", None], "names": [None, "Parent", ""]}]
    assert select_canonical_chain_names(rows, ("groupNames", "names")) == ["Parent", "G"]


@pytest.mark.parametrize("rows", [None, [], [None, "x", {"ids": []}, {"ids": "f"}]])
def test_no_usable_candidate_returns_empty(rows):
    assert select_canonical_chain_names(rows, ("names",)) == []


def test_short_name_list_does_not_raise():
    assert select_canonical_chain_names([{"ids": ["a", "b"], "names": ["A"]}], ("names",)) == ["A"]
