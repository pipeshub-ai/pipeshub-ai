"""Rendering chosen units within a size cap, and the builder's order of operations."""

from __future__ import annotations

import base64
import io
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.models.blocks import BlockType
from app.modules.retrieval.context.builder import KnowledgeContextBuilder
from app.modules.retrieval.context.neighbours import expand_neighbours
from app.modules.retrieval.context.renderer import RenderedKnowledge, render_knowledge
from app.utils.chat_helpers import CitationRefMapper, ImageBudget, RecordIdShortener
from app.utils.image_admission import ImageAdmission
from app.utils.image_policy import permissive_policy

_BUILDER = "app.modules.retrieval.context.builder"


def _record(vrid: str, name: str, blocks: int = 3) -> dict:
    return {
        "id": f"id-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": f"Record ID: id-{vrid}\nName: {name}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [{"type": BlockType.TEXT.value, "data": f"{name} block {i}"} for i in range(blocks)],
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


def _png(shade: int) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), (shade, 255 - shade, 128)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _image(vrid: str, index: int, shade: int, score: float) -> dict:
    return {
        "virtual_record_id": vrid,
        "block_index": index,
        "block_type": BlockType.IMAGE.value,
        "content": _png(shade),
        "image_description": f"chart {shade} " * 6,
        "metadata": {},
        "score": score,
    }


def _render(units: list[dict], records: dict, **kwargs: object) -> RenderedKnowledge:
    kwargs.setdefault("ref_mapper", CitationRefMapper())
    kwargs.setdefault("is_multimodal_llm", False)
    kwargs.setdefault("by_relevance", True)
    return render_knowledge(units, records, **kwargs)


def _mapper_with(refs: int) -> CitationRefMapper:
    mapper = CitationRefMapper()
    for i in range(refs):
        mapper.get_or_create_ref(f"https://elsewhere/{i}")
    return mapper


def _vrids(units: list[dict]) -> list[str]:
    return [u["virtual_record_id"] for u in units]


class TestRenderKnowledge:
    def test_empty(self) -> None:
        mapper = CitationRefMapper()
        rendered = _render([], {}, ref_mapper=mapper, max_chars=100)
        assert (rendered.units, rendered.text, rendered.omitted_records) == ([], "", 0)
        assert mapper.ref_to_url == {}

    def test_one_record(self) -> None:
        rendered = _render([_unit("a", 0, "alpha", 0.5)], {"a": _record("a", "Alpha")}, max_chars=10_000)
        assert len(rendered.records) == 1
        assert "[0|ref1] alpha" in rendered.text
        assert rendered.omitted_records == 0

    def test_by_relevance_renders_the_best_record_first(self) -> None:
        records = {"a": _record("a", "Alpha"), "b": _record("b", "Beta")}
        rendered = _render([_unit("b", 1, "beta hit", 0.9), _unit("a", 0, "alpha hit", 0.4)], records)
        assert "Name: Beta" in rendered.records[0]
        assert "[1|ref1] beta hit" in rendered.records[0]
        assert "[0|ref2] alpha hit" in rendered.records[1]

    def test_by_default_records_keep_their_id_order(self) -> None:
        records = {"a": _record("a", "Alpha"), "b": _record("b", "Beta")}
        rendered = _render(
            [_unit("b", 1, "beta hit", 0.9), _unit("a", 0, "alpha hit", 0.4)], records, by_relevance=False,
        )
        assert "Name: Alpha" in rendered.records[0]
        assert _vrids(rendered.units) == ["a", "b"]

    def test_the_cap_between_records_drops_the_lowest_ranked_whole(self) -> None:
        records = {v: _record(v, v.upper()) for v in ("a", "b", "c")}
        units = [_unit(v, 0, "x" * 400, s) for v, s in (("a", 0.9), ("c", 0.7), ("b", 0.5))]
        one = len(_render(units[:1], records).text)
        mapper = CitationRefMapper()

        rendered = _render(units, records, ref_mapper=mapper, max_chars=one * 2 + 1)

        assert _vrids(rendered.units) == ["a", "c"]
        assert rendered.omitted_records == 1
        assert len(rendered.text) <= one * 2 + 1
        assert len(mapper.ref_to_url) == 2

    def test_every_record_over_the_cap_keeps_only_the_best_unit(self) -> None:
        records = {v: _record(v, v.upper()) for v in ("a", "b", "c")}
        units = [_unit("a", 0, "y" * 5000, 0.9), _unit("a", 1, "y" * 5000, 0.8),
                 _unit("b", 0, "z" * 5000, 0.7), _unit("c", 0, "w" * 5000, 0.6)]
        mapper = CitationRefMapper()

        rendered = _render(units, records, ref_mapper=mapper, max_chars=100)

        assert [(u["virtual_record_id"], u["block_index"]) for u in rendered.units] == [("a", 0)]
        assert rendered.omitted_records == 2
        assert len(mapper.ref_to_url) == 1

    def test_an_oversized_record_does_not_cost_the_smaller_ones_after_it(self) -> None:
        records = {v: _record(v, v.upper()) for v in ("a", "b", "c")}
        units = [_unit("a", 0, "a", 0.9), _unit("b", 0, "y" * 5000, 0.8), _unit("c", 0, "c", 0.7)]
        one = len(_render(units[:1], records).text)

        rendered = _render(units, records, max_chars=one * 2 + 1)

        assert _vrids(rendered.units) == ["a", "c"]
        assert rendered.omitted_records == 1

    def test_an_oversized_record_keeps_its_best_units_and_neighbours_go_first(self) -> None:
        records = {"a": _record("a", "A", blocks=4)}
        units = [
            _unit("a", 1, "best hit", 0.9),
            _unit("a", 3, "good hit", 0.8),
            _unit("a", 2, "weak " * 500, 0.1),
            _unit("a", 0, "neighbour " * 50),
        ]
        budget = len(_render([units[0], units[1]], records).text) + 10

        rendered = _render(units, records, max_chars=budget)

        assert [u["block_index"] for u in rendered.units] == [1, 3]
        assert rendered.omitted_records == 0
        assert len(rendered.text) <= budget

    def test_tied_scores_keep_retrieval_order_when_one_must_go(self) -> None:
        records = {v: _record(v, v.upper()) for v in ("a", "b")}
        units = [_unit("b", 0, "x" * 300, 0.5), _unit("a", 0, "x" * 300, 0.5)]
        one = len(_render(units[:1], records).text)

        rendered = _render(units, records, max_chars=one + 5)

        assert _vrids(rendered.units) == ["b"]

    def test_units_whose_record_failed_to_load_are_skipped(self) -> None:
        records = {"a": _record("a", "A"), "gone": None}
        rendered = _render([_unit("gone", 0, "lost", 0.9), _unit("a", 0, "kept", 0.5)], records)
        assert _vrids(rendered.units) == ["a"]
        assert "lost" not in rendered.text

    @pytest.mark.parametrize("preloaded", [8, 98, 998])
    def test_citation_refs_growing_a_digit_stay_within_the_cap(self, preloaded: int) -> None:
        """Sizing must see the refs the real render assigns (ref9 -> ref10 ...), not ref1."""
        records = {f"r{i}": _record(f"r{i}", f"R{i}", blocks=12) for i in range(6)}
        units = [
            _unit(f"r{i}", b, "text " * 20, 1.0 - i / 10 - b / 1000)
            for i in range(6) for b in range(12)
        ]
        sizes = []
        for count in range(1, 7):
            probe = _render(units[: 12 * count], records, ref_mapper=_mapper_with(preloaded))
            sizes.append(len(probe.text))
        for cap in (sizes[2], sizes[3] - 1, sizes[4] + 3):
            mapper = _mapper_with(preloaded)
            rendered = _render(units, records, ref_mapper=mapper, max_chars=cap)
            assert len(rendered.text) <= cap
            assert len(mapper.ref_to_url) == preloaded + len(rendered.units)

    def test_short_record_ids_growing_a_digit_stay_within_the_cap(self) -> None:
        records = {f"r{i}": _record(f"r{i}", f"R{i}", blocks=2) for i in range(12)}
        units = [_unit(f"r{i}", 0, "text " * 10, 1.0 - i / 100) for i in range(12)]
        shortener = RecordIdShortener()
        for i in range(8):
            shortener.get_or_create_short_id(f"other-{i}")
        full = len(_render(units, records, record_id_shortener=shortener.copy()).text)

        rendered = _render(units, records, record_id_shortener=shortener, max_chars=full - 1)

        assert len(rendered.text) <= full - 1
        assert rendered.omitted_records == 1
        assert "Record ID: R10" in rendered.text

    @pytest.mark.parametrize("multimodal", [False, True])
    def test_images_over_budget_stay_within_the_cap(self, multimodal: bool) -> None:
        records = {v: _record(v, v.upper(), blocks=4) for v in ("a", "b", "c")}
        units = [
            _image(v, i, shade=40 * n + i, score=0.9 - n / 10 - i / 100)
            for n, v in enumerate(("a", "b", "c")) for i in range(3)
        ]

        def admission() -> ImageAdmission:
            return ImageAdmission(permissive_policy(2), budget=ImageBudget(50))

        full = _render(units, records, is_multimodal_llm=multimodal, image_admission=admission())
        cap = len(full.text) - 1
        real = admission()

        rendered = _render(units, records, is_multimodal_llm=multimodal, image_admission=real, max_chars=cap)

        assert len(rendered.text) <= cap
        assert len(rendered.images) == (2 if multimodal else 0)
        assert real.budget.used == len(rendered.images)
        shown_refs = {u["citation_ref"] for u in rendered.units}
        assert {image["ref"] for image in rendered.images} <= shown_refs

    def test_images_ranked_across_the_whole_result_still_fit(self) -> None:
        """The probe decides images record by record, the real render across all of them."""
        records = {"a": _record("a", "A", blocks=4), "b": _record("b", "B", blocks=4)}
        a_image = {**_image("a", 1, shade=10, score=0.1), "image_description": "a long caption " * 20}
        b_image = {**_image("b", 0, shade=200, score=0.9), "image_description": "short"}
        ranked = [_unit("a", 0, "best text hit", 0.95), b_image, a_image]

        def admission(slots: int) -> ImageAdmission:
            return ImageAdmission(permissive_policy(slots), budget=ImageBudget(50))

        a_alone = _render([ranked[0], a_image], records, is_multimodal_llm=True, image_admission=admission(1))
        b_no_slot = _render([b_image], records, is_multimodal_llm=True, image_admission=admission(0))
        cap = len(a_alone.text) + 1 + len(b_no_slot.text)
        whole = _render(ranked, records, is_multimodal_llm=True, image_admission=admission(1))
        assert len(whole.text) > cap

        rendered = _render(ranked, records, is_multimodal_llm=True, image_admission=admission(1), max_chars=cap)

        assert len(rendered.text) <= cap
        assert rendered.omitted_records + len({u["virtual_record_id"] for u in rendered.units}) == 2

    def test_sizing_spends_no_image_slots(self) -> None:
        records = {"a": _record("a", "A", blocks=4)}
        units = [_image("a", i, shade=60 * i, score=0.9 - i / 10) for i in range(3)]
        one = len(_render(units[:1], records, is_multimodal_llm=True).text)
        real = ImageAdmission(permissive_policy(5), budget=ImageBudget(50))

        rendered = _render(units, records, is_multimodal_llm=True, image_admission=real, max_chars=one + 5)

        assert real.budget.used == len(rendered.images) == 1

    def test_identical_input_renders_byte_identical_output(self) -> None:
        def run() -> str:
            records = {v: _record(v, v.upper(), blocks=6) for v in ("c", "a", "b")}
            units = [_unit(v, i, f"{v}{i} " * 30, 0.5) for v in ("c", "a", "b") for i in range(6)]
            return _render(units, records, max_chars=2_000).text

        first = run()
        assert all(run() == first for _ in range(3))


class TestNoNeighbourWithoutItsPassage:
    """A neighbour is context for a hit. The fit used to keep a record's cheap
    neighbours after its hit no longer fitted, showing paragraphs around a
    passage the model never sees."""

    @staticmethod
    def _hits_with_neighbours(n: int) -> tuple[dict, list[dict]]:
        records = {f"v{i}": _record(f"v{i}", f"V{i}", blocks=6) for i in range(n)}
        hits = [_unit(f"v{i}", 1, f"v{i}-hit " + "x" * 3_000, 0.9 - i / 100) for i in range(n)]
        return records, expand_neighbours(hits, records)

    @staticmethod
    def _shown_without_hit(rendered: RenderedKnowledge) -> set[str]:
        with_hit = {u["virtual_record_id"] for u in rendered.units if u.get("score") is not None}
        return {u["virtual_record_id"] for u in rendered.units} - with_hit

    def test_a_record_whose_hit_does_not_fit_is_left_out_not_shown_as_neighbours(self) -> None:
        records, units = self._hits_with_neighbours(10)
        one = len(_render(units[:1] + units[10:12], records).text)

        # Room for four whole records, plus the neighbours of several more.
        rendered = _render(units, records, max_chars=one * 4 + 1_000)

        assert self._shown_without_hit(rendered) == set()
        assert sorted(set(_vrids(rendered.units))) == ["v0", "v1", "v2", "v3"]
        assert rendered.omitted_records == 6

    def test_neighbours_stay_beside_a_hit_that_fits(self) -> None:
        records, units = self._hits_with_neighbours(2)

        rendered = _render(units, records, max_chars=100_000)

        assert sorted((u["virtual_record_id"], u["block_index"]) for u in rendered.units) == [
            ("v0", 0), ("v0", 1), ("v0", 2), ("v1", 0), ("v1", 1), ("v1", 2),
        ]

    def test_trimming_the_last_record_never_leaves_an_orphan(self) -> None:
        records = {"a": _record("a", "A", blocks=8)}
        hits = [_unit("a", 1, "best", 0.9), _unit("a", 5, "weaker " * 40, 0.5)]
        units = expand_neighbours(hits, records)
        best_only = len(_render(expand_neighbours(hits[:1], records), records).text)

        rendered = _render(units, records, max_chars=best_only + 20)

        kept = {u["block_index"] for u in rendered.units}
        assert 1 in kept
        assert not kept & {4, 6} or 5 in kept

    def test_the_best_record_keeps_its_hit_even_alone_over_the_cap(self) -> None:
        records, units = self._hits_with_neighbours(3)

        rendered = _render(units, records, max_chars=500)

        assert {u["block_index"] for u in rendered.units if u["virtual_record_id"] == "v0"} >= {1}
        assert self._shown_without_hit(rendered) == set()

    @pytest.mark.timeout(180)
    def test_the_same_input_selects_the_same_units_under_any_hash_seed(self) -> None:
        from tests.support.hash_seeds import outputs_across_hash_seeds

        outputs = outputs_across_hash_seeds(
            "import json\n"
            "from tests.unit.modules.retrieval.context.test_context_renderer import "
            "TestNoNeighbourWithoutItsPassage as T, _render\n"
            "records, units = T._hits_with_neighbours(10)\n"
            "r = _render(units, records, max_chars=14_000)\n"
            "print(json.dumps([r.text, [(u['virtual_record_id'], u['block_index']) for u in r.units]]))\n"
        )

        assert len(outputs) == 1


class TestKnowledgeContextBuilder:
    @pytest.fixture
    def records(self) -> dict:
        return {"a": _record("a", "A", blocks=6), "b": _record("b", "B", blocks=6)}

    def _patches(self, units: list[dict], records: dict) -> tuple:
        async def flatten(search_results, blob_store, org_id, is_multimodal_llm, vr_map, *args, **kwargs) -> list[dict]:
            vr_map.update(records)
            return [dict(u) for u in units]

        return (
            patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten),
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock),
            patch(f"{_BUILDER}.enrich_virtual_record_id_to_result_with_fk_children", new_callable=AsyncMock),
        )

    def _builder(self, graph_provider: object | None = None) -> KnowledgeContextBuilder:
        return KnowledgeContextBuilder(
            blob_store=object(), graph_provider=graph_provider, org_id="o1", user_id="u1",
        )

    @pytest.mark.asyncio
    async def test_ranks_and_cuts_before_neighbours(self, records) -> None:
        units = [_unit("a", 4, "a4", 0.2), _unit("b", 2, "b2", 0.9), _unit("a", 1, "a1", 0.1)]
        flatten, graph, fk = self._patches(units, records)
        with flatten, graph as mock_graph, fk as mock_fk:
            knowledge = await self._builder(object()).build([], {}, is_multimodal_llm=False, max_units=2)

        # a1 lost on score, and no slot was left for a neighbour.
        assert [(u["virtual_record_id"], u["block_index"]) for u in knowledge.ranked] == [("b", 2), ("a", 4)]
        mock_graph.assert_awaited_once()
        assert mock_graph.await_args.kwargs["user_id"] == "u1"
        mock_fk.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hits_carry_the_records_that_name_them(self, records) -> None:
        """Every hit, whatever its connector, is given the records joined to it by
        address or issue key, checked against the asking user."""
        flatten, graph, fk = self._patches([_unit("a", 4, "a4", 0.2)], records)
        with flatten, graph, fk, patch(
            f"{_BUILDER}.enrich_records_with_address_links", new_callable=AsyncMock
        ) as mock_links:
            await self._builder(object()).build([], {}, is_multimodal_llm=False)

        mock_links.assert_awaited_once()
        assert mock_links.await_args.args[2] == "o1"
        assert mock_links.await_args.kwargs["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_neighbours_take_only_the_slots_the_hits_left(self, records) -> None:
        flatten, graph, fk = self._patches([_unit("a", 4, "a4", 0.2), _unit("b", 2, "b2", 0.9)], records)
        with flatten, graph, fk:
            knowledge = await self._builder().build([], {}, is_multimodal_llm=False, max_units=4)

        assert [(u["virtual_record_id"], u["block_index"]) for u in knowledge.ranked] == [
            ("b", 2), ("a", 4), ("b", 1), ("b", 3),
        ]

    @pytest.mark.asyncio
    async def test_without_a_cap_every_hit_gets_its_neighbours(self, records) -> None:
        flatten, graph, fk = self._patches([_unit("a", 1, "a1", 0.5), _unit("b", 4, "b4", 0.9)], records)
        with flatten, graph, fk:
            knowledge = await self._builder().build([], {}, is_multimodal_llm=False)

        assert len(knowledge.ranked) == 6

    @pytest.mark.asyncio
    async def test_keeps_only_the_records_it_shows(self, records) -> None:
        flatten, graph, fk = self._patches([_unit("b", 0, "b0", 0.9), _unit("a", 0, "a0", 0.1)], records)
        with flatten, graph, fk:
            knowledge = await self._builder().build([], {}, is_multimodal_llm=False, max_units=1)

        assert set(knowledge.virtual_record_id_to_result) == {"b"}

    @pytest.mark.asyncio
    async def test_units_of_records_that_did_not_load_are_dropped(self) -> None:
        flatten, graph, fk = self._patches(
            [_unit("gone", 0, "x", 0.9), _unit("a", 0, "a0", 0.1)], {"gone": None, "a": _record("a", "A")},
        )
        with flatten, graph, fk:
            knowledge = await self._builder().build([], {}, is_multimodal_llm=False, max_units=1)

        assert _vrids(knowledge.ranked)[:1] == ["a"]

    @pytest.mark.asyncio
    async def test_nothing_found(self) -> None:
        flatten, graph, fk = self._patches([], {})
        with flatten, graph as mock_graph, fk:
            knowledge = await self._builder(object()).build([], {}, is_multimodal_llm=False, max_units=5)

        assert (knowledge.ranked, knowledge.virtual_record_id_to_result) == ([], {})
        mock_graph.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_foreign_key_tables_only_when_asked(self, records) -> None:
        flatten, graph, fk = self._patches([_unit("a", 0, "a0", 0.5)], records)
        with flatten, graph, fk as mock_fk:
            await self._builder(object()).build([], {}, is_multimodal_llm=False, include_fk_children=True)

        mock_fk.assert_awaited_once()
