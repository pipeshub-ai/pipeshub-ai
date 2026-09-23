"""Ranking, neighbours and reading order: what the model reads, and in what order."""

from __future__ import annotations

from app.models.blocks import BlockType, GroupType
from app.modules.retrieval.context.neighbours import expand_neighbours
from app.modules.retrieval.context.ordering import RELEVANCE_RANK_KEY, order_for_reading
from app.modules.retrieval.context.ranking import RelevanceRanker


def _text(vrid: str, index: int, score: float | None = None) -> dict:
    unit = {"virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TEXT.value}
    if score is not None:
        unit["score"] = score
    return unit


def _table(vrid: str, row_scores: dict[int, float]) -> dict:
    rows = [
        {"virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TABLE_ROW.value, "score": score}
        for index, score in row_scores.items()
    ]
    return {
        "virtual_record_id": vrid,
        "block_index": min(row_scores),
        "block_type": GroupType.TABLE.value,
        "content": ("summary", rows),
    }


def _record(*block_types: str) -> dict:
    return {
        "block_containers": {
            "blocks": [{"type": t, "data": f"block {i}"} for i, t in enumerate(block_types)],
        },
    }


def _keys(units: list[dict]) -> list[tuple[str, int | None]]:
    return [(u["virtual_record_id"], u["block_index"]) for u in units]


class TestRelevanceRanker:
    def test_most_relevant_first(self) -> None:
        units = [_text("a", 0, 0.2), _text("b", 0, 0.9), _text("c", 0, 0.5)]
        assert _keys(RelevanceRanker().rank(units)) == [("b", 0), ("c", 0), ("a", 0)]

    def test_keeps_only_the_best_units(self) -> None:
        units = [_text("a", 0, 0.2), _text("b", 0, 0.9), _text("c", 0, 0.5)]
        assert _keys(RelevanceRanker().rank(units, limit=2)) == [("b", 0), ("c", 0)]

    def test_a_table_ranks_by_its_best_matched_row(self) -> None:
        units = [_text("a", 0, 0.5), _table("t", {4: 0.1, 5: 0.8})]
        assert _keys(RelevanceRanker().rank(units)) == [("t", 4), ("a", 0)]

    def test_ties_and_unscored_units_keep_retrieval_order(self) -> None:
        units = [_text("a", 0), _text("b", 0, 0.5), _text("c", 0, 0.5), _text("d", 0)]
        assert _keys(RelevanceRanker().rank(units)) == [("b", 0), ("c", 0), ("a", 0), ("d", 0)]


class TestExpandNeighbours:
    def test_adds_the_text_either_side_of_a_hit(self) -> None:
        records = {"a": _record(BlockType.TEXT.value, BlockType.TEXT.value, BlockType.TEXT.value)}
        expanded = expand_neighbours([_text("a", 1, 0.9)], records)
        assert _keys(expanded) == [("a", 1), ("a", 0), ("a", 2)]
        assert "score" not in expanded[1]

    def test_only_around_units_that_survived(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 6)}
        kept = RelevanceRanker().rank([_text("a", 1, 0.9), _text("a", 4, 0.1)], limit=1)
        assert _keys(expand_neighbours(kept, records)) == [("a", 1), ("a", 0), ("a", 2)]

    def test_never_repeats_a_block_already_shown(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 4)}
        units = [_text("a", 1, 0.9), _text("a", 2, 0.8)]
        assert _keys(expand_neighbours(units, records)) == [("a", 1), ("a", 2), ("a", 0), ("a", 3)]

    def test_skips_non_text_neighbours_and_document_edges(self) -> None:
        records = {"a": _record(BlockType.TEXT.value, BlockType.IMAGE.value)}
        assert _keys(expand_neighbours([_text("a", 0, 0.9)], records)) == [("a", 0)]

    def test_a_table_takes_the_blocks_around_all_its_rows(self) -> None:
        records = {"t": _record(*[BlockType.TEXT.value] * 8)}
        expanded = expand_neighbours([_table("t", {3: 0.5, 5: 0.4})], records)
        assert _keys(expanded)[1:] == [("t", 2), ("t", 6)]

    def test_groups_and_summaries_take_no_neighbours(self) -> None:
        records = {"a": _record(*[BlockType.TEXT.value] * 4)}
        group = {"virtual_record_id": "a", "block_index": 1, "block_type": GroupType.LIST.value}
        summary = {"virtual_record_id": "a", "block_index": None, "block_type": BlockType.RECORD_SUMMARY.value}
        assert expand_neighbours([group, summary], records) == [group, summary]


class TestOrderForReading:
    def test_records_by_rank_and_blocks_in_document_order(self) -> None:
        ranked = [_text("b", 7, 0.9), _text("a", 2, 0.8), _text("b", 3, 0.5)]
        assert _keys(order_for_reading(ranked)) == [("b", 3), ("b", 7), ("a", 2)]

    def test_a_record_summary_leads_its_record(self) -> None:
        summary = {"virtual_record_id": "a", "block_index": None, "score": 0.4}
        ordered = order_for_reading([_text("a", 5, 0.9), summary])
        assert _keys(ordered) == [("a", None), ("a", 5)]

    def test_every_unit_carries_its_records_rank(self) -> None:
        ordered = order_for_reading([_text("b", 1, 0.9), _text("a", 1, 0.5), _text("b", 2)])
        assert [(u["virtual_record_id"], u[RELEVANCE_RANK_KEY]) for u in ordered] == [
            ("b", 1), ("b", 1), ("a", 2),
        ]
