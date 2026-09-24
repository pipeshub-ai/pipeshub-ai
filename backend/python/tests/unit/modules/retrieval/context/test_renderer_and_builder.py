"""Rendering within a size budget, and the builder's order of operations."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.blocks import BlockType
from app.modules.retrieval.context.builder import KnowledgeContextBuilder
from app.modules.retrieval.context.renderer import render_knowledge
from app.utils.chat_helpers import CitationRefMapper

_BUILDER = "app.modules.retrieval.context.builder"


def _record(vrid: str, name: str, blocks: int = 3) -> dict:
    return {
        "id": f"id-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": f"Record ID: id-{vrid}\nName: {name}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [
                {"type": BlockType.TEXT.value, "data": f"{name} block {i}"} for i in range(blocks)
            ],
            "block_groups": [],
        },
    }


def _unit(vrid: str, index: int, text: str, score: float | None = None) -> dict:
    unit = {
        "virtual_record_id": vrid,
        "block_index": index,
        "block_type": BlockType.TEXT.value,
        "content": text,
        "metadata": {},
    }
    if score is not None:
        unit["score"] = score
    return unit


class TestRenderKnowledge:
    def test_renders_one_record_per_record_in_the_given_order(self) -> None:
        records = {"b": _record("b", "Beta"), "a": _record("a", "Alpha")}
        units = [_unit("b", 1, "beta hit"), _unit("a", 0, "alpha hit")]

        rendered = render_knowledge(
            units, records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
        )

        assert len(rendered.records) == 2
        assert "Name: Beta" in rendered.records[0]
        assert "[1|ref1] beta hit" in rendered.records[0]
        assert "[0|ref2] alpha hit" in rendered.records[1]
        assert rendered.omitted_records == 0

    def test_over_budget_drops_the_lowest_ranked_records_whole(self) -> None:
        records = {v: _record(v, v.upper()) for v in ("a", "b", "c")}
        units = [_unit(v, 0, "x" * 400) for v in ("a", "b", "c")]
        one_record = len(render_knowledge(
            units[:1], records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
        ).text)
        ref_mapper = CitationRefMapper()

        rendered = render_knowledge(
            units, records, ref_mapper=ref_mapper, is_multimodal_llm=False,
            max_chars=one_record * 2 + 10,
        )

        assert [u["virtual_record_id"] for u in rendered.units] == ["a", "b"]
        assert rendered.omitted_records == 1
        # Refs exist only for what was shown.
        assert len(ref_mapper.ref_to_url) == 2

    def test_the_most_relevant_record_is_kept_even_if_it_alone_is_too_big(self) -> None:
        records = {"a": _record("a", "A"), "b": _record("b", "B")}
        units = [_unit("a", 0, "y" * 5000), _unit("b", 0, "z")]

        rendered = render_knowledge(
            units, records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False, max_chars=100,
        )

        assert [u["virtual_record_id"] for u in rendered.units] == ["a"]
        assert rendered.omitted_records == 1

    def test_an_oversized_record_does_not_cost_the_smaller_ones_after_it(self) -> None:
        records = {v: _record(v, v.upper()) for v in ("a", "b", "c")}
        units = [_unit("a", 0, "a"), _unit("b", 0, "y" * 5000), _unit("c", 0, "c")]
        one_record = len(render_knowledge(
            units[:1], records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
        ).text)

        rendered = render_knowledge(
            units, records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
            max_chars=one_record * 2 + 10,
        )

        assert [u["virtual_record_id"] for u in rendered.units] == ["a", "c"]
        assert rendered.omitted_records == 1

    def test_an_oversized_record_keeps_its_best_units_in_reading_order(self) -> None:
        records = {"a": _record("a", "A", blocks=4)}
        units = [
            _unit("a", 0, "neighbour " * 50),
            _unit("a", 1, "best hit"),
            _unit("a", 2, "weak " * 500, score=0.1),
            _unit("a", 3, "good hit", score=0.8),
        ]
        units[1]["score"] = 0.9
        budget = len(render_knowledge(
            [units[1], units[3]], records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
        ).text) + 10

        rendered = render_knowledge(
            units, records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False, max_chars=budget,
        )

        assert [u["block_index"] for u in rendered.units] == [1, 3]
        assert rendered.omitted_records == 0
        assert len(rendered.text) <= budget

    def test_units_whose_record_failed_to_load_are_skipped(self) -> None:
        records = {"a": _record("a", "A"), "gone": None}
        units = [_unit("gone", 0, "lost"), _unit("a", 0, "kept")]

        rendered = render_knowledge(
            units, records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
        )

        assert [u["virtual_record_id"] for u in rendered.units] == ["a"]
        assert "lost" not in rendered.text


class TestKnowledgeContextBuilder:
    @pytest.fixture
    def records(self) -> dict:
        return {"a": _record("a", "A", blocks=6), "b": _record("b", "B", blocks=6)}

    def _patches(self, units: list[dict], records: dict):
        async def flatten(search_results, blob_store, org_id, is_multimodal_llm, vr_map, *args, **kwargs):
            vr_map.update(records)
            return [dict(u) for u in units]

        return (
            patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten),
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock),
            patch(f"{_BUILDER}.enrich_virtual_record_id_to_result_with_fk_children", new_callable=AsyncMock),
        )

    @pytest.mark.asyncio
    async def test_ranks_cuts_adds_neighbours_then_orders_for_reading(self, records) -> None:
        units = [_unit("a", 4, "a4", 0.2), _unit("b", 2, "b2", 0.9), _unit("a", 1, "a1", 0.1)]
        flatten, graph, fk = self._patches(units, records)
        with flatten, graph as mock_graph, fk as mock_fk:
            knowledge = await KnowledgeContextBuilder(
                blob_store=object(), graph_provider=object(), org_id="o1",
            ).build([], {}, is_multimodal_llm=False, max_units=2)

        # a1 (lowest score) was cut; neighbours only around b2 and a4.
        assert [(u["virtual_record_id"], u["block_index"]) for u in knowledge.units] == [
            ("b", 1), ("b", 2), ("b", 3), ("a", 3), ("a", 4), ("a", 5),
        ]
        mock_graph.assert_awaited_once()
        mock_fk.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_keeps_only_the_records_it_shows(self, records) -> None:
        flatten, graph, fk = self._patches([_unit("b", 0, "b0", 0.9), _unit("a", 0, "a0", 0.1)], records)
        with flatten, graph, fk:
            knowledge = await KnowledgeContextBuilder(
                blob_store=object(), graph_provider=None, org_id="o1",
            ).build([], {}, is_multimodal_llm=False, max_units=1)

        assert set(knowledge.virtual_record_id_to_result) == {"b"}

    @pytest.mark.asyncio
    async def test_foreign_key_tables_only_when_asked(self, records) -> None:
        flatten, graph, fk = self._patches([_unit("a", 0, "a0", 0.5)], records)
        with flatten, graph, fk as mock_fk:
            await KnowledgeContextBuilder(
                blob_store=object(), graph_provider=object(), org_id="o1",
            ).build([], {}, is_multimodal_llm=False, include_fk_children=True)

        mock_fk.assert_awaited_once()
