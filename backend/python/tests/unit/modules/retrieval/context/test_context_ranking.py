"""Ranking, neighbours and reading order: which units the model sees, and in what order."""

from __future__ import annotations

import pytest

from app.models.blocks import BlockType, GroupType
from app.modules.retrieval.context.neighbours import expand_neighbours
from app.modules.retrieval.context.ordering import (
    RELEVANCE_ORDERED_CONTEXT_ENV_VAR,
    order_for_reading,
    relevance_ordered_context,
)
from app.modules.retrieval.context.ranking import rank_units
from app.modules.retrieval.context.units import (
    NEIGHBOUR_OF_KEY,
    is_orphan_neighbour,
    table_block_span,
    unit_block_indices,
    unit_score,
    without_orphan_neighbours,
)


def _text(vrid: str, index: int | None, score: float | None = None) -> dict:
    unit = {"virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TEXT.value}
    if score is not None:
        unit["score"] = score
    return unit


def _table(vrid: str, row_scores: dict[int, float]) -> dict:
    rows = [
        {"virtual_record_id": vrid, "block_index": i, "block_type": BlockType.TABLE_ROW.value, "score": s}
        for i, s in row_scores.items()
    ]
    return {
        "virtual_record_id": vrid,
        "block_index": min(row_scores),
        "block_type": GroupType.TABLE.value,
        "content": ("summary", rows),
    }


def _record(*block_types: str) -> dict:
    return {"block_containers": {"blocks": [{"type": t, "data": f"block {i}"} for i, t in enumerate(block_types)]}}


def _keys(units: list[dict]) -> list[tuple[str, int | None]]:
    return [(u["virtual_record_id"], u["block_index"]) for u in units]


class TestRankUnits:
    def test_most_relevant_first(self) -> None:
        units = [_text("a", 0, 0.2), _text("b", 0, 0.9), _text("c", 0, 0.5)]
        assert _keys(rank_units(units)) == [("b", 0), ("c", 0), ("a", 0)]

    def test_keeps_only_the_best_units(self) -> None:
        units = [_text("a", 0, 0.2), _text("b", 0, 0.9), _text("c", 0, 0.5)]
        assert _keys(rank_units(units, 2)) == [("b", 0), ("c", 0)]

    @pytest.mark.parametrize("limit", [0, -3])
    def test_a_limit_of_zero_or_less_keeps_nothing(self, limit: int) -> None:
        assert rank_units([_text("a", 0, 0.5)], limit) == []

    def test_empty(self) -> None:
        assert rank_units([]) == []
        assert rank_units([], 5) == []

    def test_a_table_ranks_by_its_best_matched_row(self) -> None:
        units = [_text("a", 0, 0.5), _table("t", {4: 0.1, 5: 0.8})]
        assert _keys(rank_units(units)) == [("t", 4), ("a", 0)]

    def test_ties_and_unscored_units_keep_retrieval_order(self) -> None:
        units = [_text("a", 0), _text("b", 0, 0.5), _text("c", 0, 0.5), _text("d", 0)]
        assert _keys(rank_units(units)) == [("b", 0), ("c", 0), ("a", 0), ("d", 0)]

    def test_non_numeric_and_nan_scores_count_as_unscored(self) -> None:
        units = [_text("a", 0, float("nan")), _text("b", 0, 0.1), {**_text("c", 0), "score": True}]
        assert _keys(rank_units(units)) == [("b", 0), ("a", 0), ("c", 0)]
        assert unit_score(units[0]) is None

    def test_same_input_ranks_the_same_every_time(self) -> None:
        units = [_text(v, i, s) for v, i, s in [("a", 1, 0.5), ("b", 2, 0.5), ("c", 0, 0.7), ("a", 3, None)]]
        assert all(_keys(rank_units(list(units))) == _keys(rank_units(units)) for _ in range(5))


class TestUnitBlockIndices:
    def test_a_table_renders_its_own_and_its_rows_indices(self) -> None:
        assert list(unit_block_indices(_table("t", {3: 0.5, 5: 0.4}))) == [3, 3, 5]

    def test_a_summary_has_none(self) -> None:
        assert list(unit_block_indices(_text("a", None))) == []


class TestExpandNeighbours:
    def test_adds_the_text_either_side_of_a_hit(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 3)}
        expanded = expand_neighbours([_text("a", 1, 0.9)], records)
        assert _keys(expanded) == [("a", 1), ("a", 0), ("a", 2)]
        assert "score" not in expanded[1]

    def test_only_around_units_that_survived_the_cut(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 6)}
        kept = rank_units([_text("a", 1, 0.9), _text("a", 4, 0.1)], 1)
        assert _keys(expand_neighbours(kept, records)) == [("a", 1), ("a", 0), ("a", 2)]

    def test_never_repeats_a_block_already_shown(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 4)}
        units = [_text("a", 1, 0.9), _text("a", 2, 0.8)]
        assert _keys(expand_neighbours(units, records)) == [("a", 1), ("a", 2), ("a", 0), ("a", 3)]

    def test_two_hits_sharing_a_neighbour_add_it_once(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 5)}
        units = [_text("a", 1, 0.9), _text("a", 3, 0.8)]
        assert _keys(expand_neighbours(units, records)) == [("a", 1), ("a", 3), ("a", 0), ("a", 2), ("a", 4)]

    @pytest.mark.parametrize("index,expected", [(0, [("a", 0), ("a", 1)]), (2, [("a", 2), ("a", 1)])])
    def test_document_edges(self, index: int, expected: list) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 3)}
        assert _keys(expand_neighbours([_text("a", index, 0.9)], records)) == expected

    def test_skips_non_text_neighbours(self) -> None:
        records = {"a": _record(BlockType.IMAGE.value, BlockType.TEXT.value, BlockType.TABLE_ROW.value)}
        assert _keys(expand_neighbours([_text("a", 1, 0.9)], records)) == [("a", 1)]

    def test_a_table_takes_the_blocks_around_all_its_rows(self) -> None:
        records = {"t": _record(*[BlockType.TEXT.value] * 8)}
        assert _keys(expand_neighbours([_table("t", {3: 0.5, 5: 0.4})], records))[1:] == [("t", 2), ("t", 6)]

    def test_groups_and_summaries_take_no_neighbours(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 4)}
        group = {"virtual_record_id": "a", "block_index": 1, "block_type": GroupType.LIST.value}
        summary = {"virtual_record_id": "a", "block_index": None, "block_type": BlockType.RECORD_SUMMARY.value}
        assert expand_neighbours([group, summary], records) == [group, summary]

    def test_a_record_that_did_not_load_adds_nothing(self) -> None:
        assert _keys(expand_neighbours([_text("a", 1, 0.9)], {"a": None})) == [("a", 1)]

    def test_a_limit_gives_the_best_hits_their_neighbours_first(self) -> None:
        records = {v: _record(*[BlockType.TEXT.value] * 3) for v in ("a", "b")}
        ranked = [_text("b", 1, 0.9), _text("a", 1, 0.5)]
        assert _keys(expand_neighbours(ranked, records, limit=3)) == [
            ("b", 1), ("a", 1), ("b", 0), ("b", 2), ("a", 0),
        ]
        assert expand_neighbours(ranked, records, limit=0) == ranked


class TestOrderForReading:
    def test_by_default_records_keep_their_id_order(self) -> None:
        ranked = [_text("b", 7, 0.9), _text("a", 2, 0.8), _text("b", 3, 0.5)]
        assert _keys(order_for_reading(ranked, by_relevance=False)) == [("a", 2), ("b", 3), ("b", 7)]

    def test_by_relevance_records_by_rank_and_blocks_in_document_order(self) -> None:
        ranked = [_text("b", 7, 0.9), _text("a", 2, 0.8), _text("b", 3, 0.5)]
        assert _keys(order_for_reading(ranked, by_relevance=True)) == [("b", 3), ("b", 7), ("a", 2)]

    @pytest.mark.parametrize("by_relevance", [False, True])
    def test_a_record_summary_leads_its_record(self, by_relevance: bool) -> None:
        ordered = order_for_reading([_text("a", 5, 0.9), _text("a", None, 0.4)], by_relevance=by_relevance)
        assert _keys(ordered) == [("a", None), ("a", 5)]

    def test_leaves_the_units_unchanged(self) -> None:
        unit = _text("a", 1, 0.9)
        order_for_reading([unit], by_relevance=True)
        assert unit == _text("a", 1, 0.9)


class TestRelevanceOrderedContextSwitch:
    @pytest.mark.parametrize(
        "high_accuracy,feature,expected",
        [
            (None, None, False),
            ("true", None, True),
            ("true", "false", False),
            (None, "true", True),
            ("false", "  ", False),
        ],
    )
    def test_follows_high_accuracy_mode_unless_set(
        self, monkeypatch: pytest.MonkeyPatch, high_accuracy: str | None, feature: str | None, expected: bool,
    ) -> None:
        for name, value in (("HIGH_ACCURACY_MODE", high_accuracy), (RELEVANCE_ORDERED_CONTEXT_ENV_VAR, feature)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        assert relevance_ordered_context() is expected


def _table_record(rows: range, *, before: int = 1, after: int = 1, old_format: bool = False) -> dict:
    """Caption text, table rows, a note: the shape a parsed document stores."""
    blocks = [{"type": BlockType.TEXT.value, "data": f"caption {i}"} for i in range(before)]
    first = len(blocks)
    blocks += [
        {"type": BlockType.TABLE_ROW.value, "parent_index": 0, "data": {"row_natural_language_text": f"row {i}"}}
        for i in rows
    ]
    last = len(blocks) - 1
    blocks += [{"type": BlockType.TEXT.value, "data": f"note {i}"} for i in range(after)]
    children = (
        [{"block_index": i} for i in range(first, last + 1)] if old_format
        else {"block_ranges": [{"start": first, "end": last}]}
    )
    return {"block_containers": {
        "blocks": blocks,
        "block_groups": [{"type": GroupType.TABLE.value, "children": children}],
    }}


def _table_hit(vrid: str, rows: list[int], *, group: int | None = 0) -> dict:
    unit = _table(vrid, {row: 0.9 - row / 100 for row in rows})
    if group is not None:
        unit["block_group_index"] = group
    return unit


class TestTableNeighbours:
    """A table hit lists only its matched rows; its caption and its note sit
    beside the table's own range."""

    @pytest.mark.parametrize("group", [0, None])
    def test_a_hit_on_middle_rows_gets_the_caption_and_the_note(self, group: int | None) -> None:
        records = {"t": _table_record(range(10))}

        expanded = expand_neighbours([_table_hit("t", [5, 6], group=group)], records)

        assert _keys(expanded)[1:] == [("t", 0), ("t", 11)]
        assert [u["content"] for u in expanded[1:]] == ["caption 0", "note 0"]

    def test_the_old_children_format_is_read_too(self) -> None:
        records = {"t": _table_record(range(10), old_format=True)}
        assert _keys(expand_neighbours([_table_hit("t", [5])], records))[1:] == [("t", 0), ("t", 11)]

    def test_a_table_at_the_start_of_the_record_gets_only_the_note(self) -> None:
        records = {"t": _table_record(range(4), before=0)}
        assert _keys(expand_neighbours([_table_hit("t", [2])], records))[1:] == [("t", 4)]

    def test_a_table_at_the_end_of_the_record_gets_only_the_caption(self) -> None:
        records = {"t": _table_record(range(4), after=0)}
        assert _keys(expand_neighbours([_table_hit("t", [2])], records))[1:] == [("t", 0)]

    def test_a_table_filling_the_record_gets_none(self) -> None:
        records = {"t": _table_record(range(4), before=0, after=0)}
        assert _keys(expand_neighbours([_table_hit("t", [1])], records)) == [("t", 1)]

    def test_a_table_whose_group_is_missing_falls_back_to_its_rows(self) -> None:
        records = {"t": _record(*[BlockType.TEXT.value] * 8)}
        assert _keys(expand_neighbours([_table_hit("t", [3, 5], group=4)], records))[1:] == [("t", 2), ("t", 6)]

    def test_span_of_a_non_table_unit_is_none(self) -> None:
        assert table_block_span(_table_record(range(3)), _text("t", 1, 0.5)) is None


class TestNeighbourAnchors:
    def test_a_neighbour_names_the_unit_it_was_added_for(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 3)}
        expanded = expand_neighbours([_text("a", 1, 0.9)], records)
        assert [u.get(NEIGHBOUR_OF_KEY) for u in expanded] == [None, [1], [1]]

    def test_a_neighbour_shared_by_two_hits_names_both(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 5)}
        expanded = expand_neighbours([_text("a", 1, 0.9), _text("a", 3, 0.8)], records)
        shared = next(u for u in expanded if u["block_index"] == 2)
        assert shared[NEIGHBOUR_OF_KEY] == [1, 3]

    def test_a_neighbour_is_an_orphan_only_without_all_its_units(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 5)}
        first, second = _text("a", 1, 0.9), _text("a", 3, 0.8)
        expanded = expand_neighbours([first, second], records)
        shared = next(u for u in expanded if u["block_index"] == 2)

        assert not is_orphan_neighbour(shared, [second])
        assert is_orphan_neighbour(shared, [])
        assert is_orphan_neighbour(shared, [_text("b", 3, 0.8)])
        assert not is_orphan_neighbour(first, [])

    def test_orphans_are_dropped_and_everything_else_kept_in_order(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 6)}
        expanded = expand_neighbours([_text("a", 1, 0.9), _text("a", 4, 0.8)], records)
        without_second = [u for u in expanded if u["block_index"] != 4]

        assert _keys(without_orphan_neighbours(without_second)) == [("a", 1), ("a", 0), ("a", 2)]

    def test_a_neighbour_of_a_table_stays_with_the_table(self) -> None:
        records = {"t": _table_record(range(10))}
        table = _table_hit("t", [5, 6])
        expanded = expand_neighbours([table], records)

        assert without_orphan_neighbours(expanded) == expanded
        assert without_orphan_neighbours(expanded[1:]) == []
