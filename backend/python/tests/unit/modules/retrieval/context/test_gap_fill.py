"""Small gaps between a record's shown spans are filled with the text between
them, as neighbours, bounded per record."""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, patch

import pytest

from app.models.blocks import BlockType, GroupType
from app.modules.retrieval.context.builder import KnowledgeContextBuilder
from app.modules.retrieval.context.neighbours import (
    GAP_FILL_KEY,
    MAX_FILLED_GAP,
    MAX_GAP_BLOCKS_PER_RECORD,
    expand_neighbours,
    fill_small_gaps,
)
from app.modules.retrieval.context.units import (
    NEIGHBOUR_OF_KEY,
    unit_block_indices,
    without_orphan_neighbours,
)

_TEXT = BlockType.TEXT.value


def _record(vrid: str, types: list[str]) -> dict:
    return {
        "id": f"id-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": f"Record ID: id-{vrid}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [{"type": t, "data": f"{vrid} block {i}"} for i, t in enumerate(types)],
            "block_groups": [],
        },
    }


def _text(vrid: str, index: int) -> dict:
    return {
        "virtual_record_id": vrid, "block_index": index, "block_type": _TEXT,
        "content": f"{vrid} hit {index}", "metadata": {},
    }


def _table(vrid: str, rows: list[int]) -> dict:
    children = [
        {"virtual_record_id": vrid, "block_index": r, "block_type": BlockType.TABLE_ROW.value}
        for r in rows
    ]
    return {
        "virtual_record_id": vrid, "block_index": min(rows),
        "block_type": GroupType.TABLE.value, "content": ("summary", children),
    }


def _keys(units: list[dict]) -> list[tuple[str, int]]:
    return [(u["virtual_record_id"], u["block_index"]) for u in units]


def _spans(units: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """Merged spans per record, computed independently of the implementation."""
    covered: dict[str, set[int]] = {}
    for unit in units:
        indices = list(unit_block_indices(unit))
        if indices:
            covered.setdefault(unit["virtual_record_id"], set()).update(range(min(indices), max(indices) + 1))
    spans: dict[str, list[tuple[int, int]]] = {}
    for vrid, indices in covered.items():
        ordered = sorted(indices)
        runs = [[ordered[0], ordered[0]]]
        for index in ordered[1:]:
            if index == runs[-1][1] + 1:
                runs[-1][1] = index
            else:
                runs.append([index, index])
        spans[vrid] = [tuple(r) for r in runs]
    return spans


def _gaps(spans: list[tuple[int, int]]) -> list[range]:
    return [range(a[1] + 1, b[0]) for a, b in zip(spans, spans[1:])]


def _random_case(rng: random.Random) -> tuple[list[dict], dict]:
    records: dict[str, dict] = {}
    units: list[dict] = []
    for vrid in ("a", "b"):
        size = rng.randint(1, 30)
        types = [_TEXT if rng.random() < 0.85 else BlockType.IMAGE.value for _ in range(size)]
        records[vrid] = _record(vrid, types)
        units.extend(_text(vrid, index) for index in rng.sample(range(size), rng.randint(0, min(size, 8))))
        if size > 6 and rng.random() < 0.3:
            start = rng.randint(0, size - 5)
            units.append(_table(vrid, [start, start + rng.randint(2, 4)]))
    rng.shuffle(units)
    return expand_neighbours(units, records), records


_CASES = 400


class TestFillSmallGapsProperties:
    def test_only_small_all_text_gaps_are_filled_whole_within_the_cap(self) -> None:
        rng = random.Random(1234)
        for _ in range(_CASES):
            units, records = _random_case(rng)
            before = _spans(units)
            result = fill_small_gaps(units, records)
            fills = result[len(units):]

            assert result[: len(units)] == units
            shown = {(u["virtual_record_id"], i) for u in units for i in unit_block_indices(u)}
            fill_keys = _keys(fills)
            assert len(set(fill_keys)) == len(fill_keys)
            assert not set(fill_keys) & shown
            for fill in fills:
                assert fill[NEIGHBOUR_OF_KEY] and fill[GAP_FILL_KEY]
                assert fill["block_type"] == _TEXT
                assert "score" not in fill

            for vrid, spans in before.items():
                filled = {i for v, i in fill_keys if v == vrid}
                assert len(filled) <= MAX_GAP_BLOCKS_PER_RECORD
                blocks = records[vrid]["block_containers"]["blocks"]
                fillable = []
                for gap in _gaps(spans):
                    gap_set = set(gap)
                    if len(gap) > MAX_FILLED_GAP:
                        assert not gap_set & filled, "a wide gap was touched"
                        continue
                    assert gap_set <= filled or not gap_set & filled, "a gap was filled in part"
                    if all(blocks[i]["type"] == _TEXT for i in gap):
                        fillable.append(gap)
                    else:
                        assert not gap_set & filled
                assert filled <= {i for gap in fillable for i in gap}
                if sum(len(g) for g in fillable) <= MAX_GAP_BLOCKS_PER_RECORD:
                    assert filled == {i for gap in fillable for i in gap}

    def test_filling_twice_adds_nothing(self) -> None:
        rng = random.Random(99)
        for _ in range(_CASES):
            units, records = _random_case(rng)
            once = fill_small_gaps(units, records)
            assert _keys(fill_small_gaps(once, records)) == _keys(once)

    def test_a_fill_goes_with_the_less_relevant_side(self) -> None:
        records = {"a": _record("a", [_TEXT] * 10)}
        units = [_text("a", 7), _text("a", 2)]

        fills = fill_small_gaps(expand_neighbours(units, records), records)[4 + 2:]

        assert _keys(fills) == [("a", 4), ("a", 5)]
        assert all(f[NEIGHBOUR_OF_KEY] == [2] for f in fills)


class TestFillSmallGapsExamples:
    def test_the_sentence_between_two_neighbours_is_added(self) -> None:
        records = {"a": _record("a", [_TEXT] * 8)}
        units = [_text("a", 1), _text("a", 5)]

        result = fill_small_gaps(expand_neighbours(units, records), records)

        assert ("a", 3) in _keys(result)

    def test_rows_a_table_left_out_are_not_a_gap(self) -> None:
        records = {"t": _record("t", [_TEXT] * 8)}
        units = [_table("t", [3, 5])]

        result = fill_small_gaps(units, records)

        assert result == units

    def test_the_cap_holds_for_a_record_with_scattered_hits(self) -> None:
        records = {"a": _record("a", [_TEXT] * 40)}
        units = [_text("a", i) for i in range(0, 40, 2)]

        once = fill_small_gaps(units, records)

        assert len(once) - len(units) == MAX_GAP_BLOCKS_PER_RECORD
        assert _keys(fill_small_gaps(once, records)) == _keys(once)


class TestBuilderAndBudget:
    @staticmethod
    async def _build() -> list[dict]:
        records = {"a": _record("a", [_TEXT] * 8)}
        hits = [dict(_text("a", 1), score=0.9), dict(_text("a", 5), score=0.1)]

        async def flatten(search_results, blob_store, org_id, is_multimodal_llm, vr_map, *args, **kwargs) -> list:
            vr_map.update(records)
            return [dict(u) for u in hits]

        builder = "app.modules.retrieval.context.builder"
        with patch(f"{builder}.get_flattened_results", side_effect=flatten), \
                patch(f"{builder}.enrich_records_with_graph_context", new_callable=AsyncMock), \
                patch(f"{builder}.enrich_virtual_record_id_to_result_with_fk_children", new_callable=AsyncMock):
            knowledge = await KnowledgeContextBuilder(
                blob_store=object(), graph_provider=None, org_id="o", user_id="u",
            ).build([], {}, is_multimodal_llm=False)
        return knowledge.ranked

    @pytest.mark.asyncio
    async def test_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CONTEXT_GAP_FILL", raising=False)
        monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)

        assert not any(unit.get(GAP_FILL_KEY) for unit in await self._build())

    @pytest.mark.asyncio
    async def test_the_builder_fills_the_gap_and_a_budget_drops_it_with_its_hit(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CONTEXT_GAP_FILL", "true")
        ranked = await self._build()

        assert sorted(_keys(ranked)) == [("a", i) for i in range(7)]
        gap = next(u for u in ranked if u["block_index"] == 3)
        assert gap[GAP_FILL_KEY] and gap[NEIGHBOUR_OF_KEY] == [5]

        without_second_hit = [u for u in ranked if u["block_index"] != 5]
        assert ("a", 3) not in _keys(without_orphan_neighbours(without_second_hit))
