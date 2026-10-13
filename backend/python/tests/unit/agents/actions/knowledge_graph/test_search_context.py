"""What ``execute_search`` shows the model, and what it records as shown.

These run the real context pipeline and renderer; only retrieval, blob
loading and graph enrichment are stood in for.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.agents.agent_loop.retrieval_ledger import RetrievalContextLedger
from app.agents.chat_modes.prefetch import prefetch_retrieval
from app.models.blocks import BlockType
from app.modules.retrieval.context.ordering import RELEVANCE_ORDERED_CONTEXT_ENV_VAR
from app.utils.chat_helpers import CitationRefMapper, ImageBudget
from app.utils.image_admission import ImageAdmission, admission_from_state
from app.utils.image_policy import permissive_policy

_SEARCH = "app.agents.actions.knowledge_graph.ops.search"
_BUILDER = "app.modules.retrieval.context.builder"
_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _record(vrid: str, blocks: int = 4) -> dict[str, Any]:
    return {
        "id": f"rec-{vrid}",
        "_id": f"records/rec-{vrid}",
        "virtual_record_id": vrid,
        "record_name": f"Doc {vrid}",
        "context_metadata": f"Record ID: rec-{vrid}\nName: Doc {vrid}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [{"type": BlockType.TEXT.value, "data": f"{vrid} block {i}"} for i in range(blocks)],
            "block_groups": [],
        },
    }


def _unit(vrid: str, index: int, text: str, score: float) -> dict[str, Any]:
    return {
        "virtual_record_id": vrid,
        "block_index": index,
        "block_type": BlockType.TEXT.value,
        "content": text,
        "metadata": {},
        "score": score,
    }


def _state(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": [{"virtual_record_id": vrid} for vrid in ("v0", "v1", "v2", "v3", "v4", "a", "b")],
        "virtual_to_record_map": {},
    }
    return {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": AsyncMock(),
        "config_service": None,
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["app-1"], "kb": []},
        **extra,
    }


async def _search(
    state: dict[str, Any], records: dict[str, Any], units: list[dict[str, Any]], **patches: Any,  # noqa: ANN401
) -> str | list[Any]:
    async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
        vr_map.update(records)
        return [dict(unit) for unit in units]

    with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
            patch(f"{_SEARCH}.BlobStorage"):
        if "cap" in patches:
            with patch(f"{_SEARCH}.DEFAULT_MAX_RESULT_CHARS", patches["cap"]):
                return await execute_search(state, "annual fee")
        return await execute_search(state, "annual fee")


def _five_records() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = {f"v{i}": _record(f"v{i}") for i in range(5)}
    units = [_unit(f"v{i}", 1, f"v{i} " + "x" * 900, 0.9 - i / 10) for i in range(5)]
    return records, units


class TestFitsTheToolResultCap:
    @pytest.mark.asyncio
    async def test_lowest_ranked_records_go_whole_and_the_result_says_so(self) -> None:
        records, units = _five_records()
        state = _state()

        result = await _search(state, records, units, cap=3_000)

        assert isinstance(result, str)
        assert len(result) <= 3_000
        assert "lower-ranked records left out to fit the result size" in result
        assert "v0 xxx" in result
        assert "v4 xxx" not in result

    @pytest.mark.asyncio
    async def test_state_and_trace_hold_exactly_what_was_shown(self) -> None:
        records, units = _five_records()
        state = _state(include_retrieval_context=True)

        result = await _search(state, records, units, cap=3_000)

        shown = {vrid for vrid in records if f"{vrid} xxx" in result}
        assert shown and shown != set(records)
        assert {u["virtual_record_id"] for u in state["final_results"]} == shown
        assert set(state["virtual_record_id_to_result"]) == shown
        assert {r["virtual_record_id"] for r in state["tool_records"]} == shown
        assert set(state["retrieved_by"]) == shown
        assert state["known_record_ids"] == {f"rec-{vrid}" for vrid in shown}
        delta = RetrievalContextLedger().take_delta(state)
        assert {r.virtualRecordId for r in delta.records} == shown
        assert sorted(delta.known_record_ids) == sorted(f"rec-{vrid}" for vrid in shown)
        # One citation ref per block shown; none for the records left out.
        assert len(state["citation_ref_mapper"].ref_to_url) == len(state["final_results"])

    @pytest.mark.asyncio
    async def test_a_long_keyword_hint_is_sized_in_before_the_records(self) -> None:
        records, units = _five_records()
        state = _state(config_service=MagicMock())
        hint = "\n\nAdditional records found via pattern matching:\n" + "h" * 1_200

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            kwargs["virtual_record_id_to_result"]["g1"] = {"_key": "rec-g1", "recordName": "Grep doc"}
            return [{"virtual_record_id": "g1", "source": "pattern_match"}]

        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=[{"virtual_record_id": "g1"}])), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge), \
                patch(f"{_SEARCH}.render_pattern_match_hint", return_value=hint):
            result = await _search(state, records, units, cap=3_000)

        assert len(result) <= 3_000
        assert result.endswith(hint)
        assert "g1" in state["virtual_record_id_to_result"]

    @pytest.mark.asyncio
    async def test_everything_that_fits_is_shown_unchanged(self) -> None:
        records, units = _five_records()
        state = _state()

        result = await _search(state, records, units)

        # Five hits, and the text either side of each in the slots they left.
        assert result.startswith("Top 15 blocks from 5 records (ranked sample — other records may match).")
        assert "left out" not in result


class TestVectorAndGrepHits:
    @pytest.mark.asyncio
    async def test_both_are_shown_and_both_are_recorded_as_shown(self) -> None:
        records = {"v0": _record("v0")}
        state = _state(config_service=MagicMock(), include_retrieval_context=True)
        graph_record = {"_key": "rec-g1", "virtualRecordId": "g1", "recordName": "Grep doc"}

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            kwargs["virtual_record_id_to_result"]["g1"] = graph_record
            return [{"virtual_record_id": "g1", "source": "pattern_match"}]

        grep_hits = [{"virtual_record_id": "v0"}, {"virtual_record_id": "g1"}]
        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=grep_hits)), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge):
            result = await _search(state, records, [_unit("v0", 1, "vector hit", 0.8)])

        assert "vector hit" in result
        assert "Grep doc" in result
        assert set(state["virtual_record_id_to_result"]) == {"v0", "g1"}
        assert state["retrieved_by"]["v0"].layers == ["vector", "pattern_match"]
        assert state["retrieved_by"]["g1"].layers == ["pattern_match"]
        assert (state["retrieved_by"]["g1"].record_id, state["retrieved_by"]["g1"].record_name) == (
            "rec-g1", "Grep doc",
        )
        delta = RetrievalContextLedger().take_delta(state)
        assert {r.virtualRecordId for r in delta.records} == {"v0", "g1"}


class TestGrepOnly:
    @pytest.mark.asyncio
    async def test_a_keyword_hint_over_the_cap_still_answers(self) -> None:
        """No semantic units to cut: the hint goes out and the generic cap trims it."""
        state = _state(config_service=MagicMock())

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            kwargs["virtual_record_id_to_result"]["g1"] = {"_key": "rec-g1", "recordName": "Grep doc"}
            return [{"virtual_record_id": "g1", "source": "pattern_match"}]

        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=[{"virtual_record_id": "g1"}])), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge), \
                patch(f"{_SEARCH}.render_pattern_match_hint", return_value="h" * 5_000):
            result = await _search(state, {}, [], cap=1_000)

        assert result.startswith("No content blocks from semantic search, but 1 record")
        assert result.endswith("h" * 5_000)

    @pytest.mark.asyncio
    async def test_a_record_cut_by_rank_and_listed_by_grep_is_traced_to_grep_only(self) -> None:
        records = {"v0": _record("v0"), "v1": _record("v1")}
        units = [_unit("v0", 1, "kept hit", 0.9), _unit("v1", 1, "cut hit", 0.1)]
        state = _state(
            config_service=MagicMock(), include_retrieval_context=True,
            filters={"apps": ["a1", "a2", "a3", "a4", "a5"], "kb": []},
        )

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            assert "v1" not in kwargs["virtual_record_id_to_result"]
            kwargs["virtual_record_id_to_result"]["v1"] = {"_key": "rec-v1", "recordName": "Doc v1"}
            return [{"virtual_record_id": "v1", "source": "pattern_match"}]

        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=[{"virtual_record_id": "v1"}])), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge), \
                patch(f"{_BUILDER}.rank_units", side_effect=lambda units, limit=None: units[:1]):
            result = await _search(state, records, units)

        assert "cut hit" not in result
        assert state["retrieved_by"]["v0"].layers == ["vector"]
        assert state["retrieved_by"]["v1"].layers == ["pattern_match"]


class TestReadingOrder:
    @pytest.mark.asyncio
    async def test_by_default_records_keep_their_id_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(RELEVANCE_ORDERED_CONTEXT_ENV_VAR, raising=False)
        monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)
        records = {"a": _record("a"), "b": _record("b")}
        units = [_unit("b", 0, "beta hit", 0.9), _unit("a", 0, "alpha hit", 0.2)]

        result = await _search(_state(), records, units)

        assert result.index("alpha hit") < result.index("beta hit")
        assert "most relevant record first" not in result

    @pytest.mark.asyncio
    async def test_the_switch_puts_the_most_relevant_record_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RELEVANCE_ORDERED_CONTEXT_ENV_VAR, "true")
        records = {"a": _record("a"), "b": _record("b")}
        units = [_unit("b", 0, "beta hit", 0.9), _unit("a", 0, "alpha hit", 0.2)]

        result = await _search(_state(), records, units)

        assert result.index("beta hit") < result.index("alpha hit")
        assert "most relevant record first" in result

    @pytest.mark.asyncio
    async def test_identical_searches_render_byte_identical_results(self) -> None:
        records, units = _five_records()
        first = await _search(_state(), records, units, cap=3_000)
        second = await _search(_state(), records, units, cap=3_000)
        assert first == second


class TestWronglyTypedState:
    @pytest.mark.asyncio
    async def test_accumulators_of_the_wrong_type_are_started_over(self) -> None:
        records = {"a": _record("a")}
        state = _state(
            final_results=[None, "oops"],
            virtual_record_id_to_result=["oops"],
            tool_records={"oops": 1},
            image_budget="oops",
            citation_ref_mapper={"oops": 1},
        )

        result = await _search(state, records, [_unit("a", 0, "alpha hit", 0.5)])

        assert "alpha hit" in result
        assert {u["virtual_record_id"] for u in state["final_results"]} == {"a"}
        assert set(state["virtual_record_id_to_result"]) == {"a"}
        assert isinstance(state["citation_ref_mapper"], CitationRefMapper)
        assert isinstance(state["image_budget"], ImageBudget)


def _image_units() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = {"v1": _record("v1")}
    image = {
        "virtual_record_id": "v1",
        "block_index": 0,
        "block_type": BlockType.IMAGE.value,
        "content": _PNG,
        "metadata": {},
        "score": 0.9,
    }
    return records, [image]


class TestImages:
    @pytest.mark.asyncio
    async def test_downscales_are_decided_off_the_event_loop_first(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=True)
        with patch("app.utils.image_admission.ImageAdmission.warm", new_callable=AsyncMock) as warm:
            await _search(state, records, units)
        warm.assert_awaited_once_with([_PNG])

    @pytest.mark.asyncio
    async def test_a_multimodal_model_gets_the_image_with_the_text(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=True)

        result = await _search(state, records, units)

        assert isinstance(result, list)
        assert isinstance(result[0], TextPart)
        assert [type(part) for part in result[1:]] == [ImagePart]
        assert "pending_tool_images" not in state
        assert state["image_budget"].used == 1

    @pytest.mark.asyncio
    async def test_an_image_prefetch_attached_is_not_attached_again_by_a_search(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=True)
        state["image_admission"] = ImageAdmission(permissive_policy(1), budget=state.setdefault("image_budget", ImageBudget()))
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200, "searchResults": [{"virtual_record_id": "v1"}], "virtual_to_record_map": {},
        }

        async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
            vr_map.update(records)
            return [dict(unit) for unit in units]

        with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
                patch(f"{_BUILDER}.enrich_virtual_record_id_to_result_with_fk_children", new_callable=AsyncMock), \
                patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock):
            prefetched = await prefetch_retrieval(
                query="q", org_id="o1", user_id="u1", retrieval_service=retrieval,
                graph_provider=object(), blob_store=object(), filters={}, limit=10,
                is_multimodal_llm=True, previous_conversations=None, logger=MagicMock(),
                image_budget=state["image_budget"], image_admission=admission_from_state(state),
            )
        assert len(prefetched.collected_images) == 1

        result = await _search(state, records, units)

        assert isinstance(result, str)
        assert "shown above" in result
        assert state["image_budget"].used == 1

    @pytest.mark.asyncio
    async def test_without_native_multipart_support_the_image_is_also_stashed(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=True, supports_multipart_tool_result=False)

        result = await _search(state, records, units)

        assert isinstance(result, list)
        assert len(state["pending_tool_images"]) == 1

    @pytest.mark.asyncio
    async def test_a_text_model_gets_text_only(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=False)

        result = await _search(state, records, units)

        assert isinstance(result, str)
        assert "pending_tool_images" not in state

    @pytest.mark.asyncio
    async def test_an_exhausted_image_budget_falls_back_to_text(self) -> None:
        records, units = _image_units()
        budget = ImageBudget(max_images=1)
        budget.try_consume(1)
        state = _state(is_multimodal_llm=True, image_budget=budget)

        result = await _search(state, records, units)

        assert isinstance(result, str)
        assert "not shown" in result


class TestEmpty:
    @pytest.mark.asyncio
    async def test_hits_whose_records_did_not_load_are_an_empty_search(self) -> None:
        result = await _search(_state(), {"v1": None}, [_unit("v1", 0, "lost", 0.9)])

        assert json.loads(result)["result_count"] == 0
