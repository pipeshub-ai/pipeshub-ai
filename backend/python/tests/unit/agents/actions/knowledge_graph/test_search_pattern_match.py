"""``execute_search``'s keyword-match (grep) path, the disable flags, and the
state it accumulates across calls.

The context pipeline is replaced at its two seams (``KnowledgeContextBuilder``
and ``render_knowledge``) so these tests cover only what the search tool owns.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.modules.retrieval.context.builder import KnowledgeContext
from app.modules.retrieval.context.renderer import RenderedKnowledge

_SEARCH = "app.agents.actions.knowledge_graph.ops.search"


def _state(**overrides) -> dict:
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "filters": {"apps": ["app-1"], "kb": []},
        "retrieval_service": AsyncMock(),
        "graph_provider": AsyncMock(),
        "config_service": AsyncMock(),
        "logger": MagicMock(),
        "llm": None,
    }
    state.update(overrides)
    return state


def _retrieval(results: list[dict]) -> AsyncMock:
    service = AsyncMock()
    service.search_with_filters = AsyncMock(return_value={
        "status_code": 200, "searchResults": results, "virtual_to_record_map": {},
    })
    return service


def _pipeline(units: list[dict], text: str = "record content"):
    builder = MagicMock()
    builder.return_value.build = AsyncMock(return_value=KnowledgeContext(
        units=units, virtual_record_id_to_result={},
    ))
    rendered = RenderedKnowledge(records=[text] if units else [], units=units)
    return (
        patch(f"{_SEARCH}.KnowledgeContextBuilder", builder),
        patch(f"{_SEARCH}.render_knowledge", return_value=rendered),
        patch(f"{_SEARCH}.BlobStorage"),
    )


_HIT = {"virtual_record_id": "vr-1", "block_index": 0, "content": "hit"}


class TestKeywordMatches:
    @pytest.mark.asyncio
    async def test_keyword_matches_are_offered_when_semantic_search_finds_nothing(self) -> None:
        state = _state(retrieval_service=_retrieval([]))
        builder, render, blob = _pipeline([])
        with builder, render, blob, patch(
            f"{_SEARCH}.run_pattern_match_with_llm_grep",
            new_callable=AsyncMock, return_value=[{"raw": "record"}],
        ), patch(
            f"{_SEARCH}.merge_pattern_match_results",
            new_callable=AsyncMock, return_value=[{"virtual_record_id": "vr-a"}],
        ):
            result = await execute_search(state, "revenue")

        assert "found via keyword matching" in result

    @pytest.mark.asyncio
    async def test_grep_failure_falls_back_to_semantic_results(self) -> None:
        state = _state(retrieval_service=_retrieval([_HIT]))
        builder, render, blob = _pipeline([_HIT])
        with builder, render, blob, patch(
            f"{_SEARCH}.run_pattern_match_with_llm_grep",
            new_callable=AsyncMock, side_effect=RuntimeError("pattern match blew up"),
        ):
            result = await execute_search(state, "revenue")

        assert result.startswith("Top 1 block from 1 record")
        assert state["logger"].warning.called

    @pytest.mark.asyncio
    async def test_merge_failure_falls_back_to_semantic_results(self) -> None:
        state = _state(retrieval_service=_retrieval([_HIT]))
        builder, render, blob = _pipeline([_HIT])
        with builder, render, blob, patch(
            f"{_SEARCH}.run_pattern_match_with_llm_grep",
            new_callable=AsyncMock, return_value=[{"raw": "record"}],
        ), patch(
            f"{_SEARCH}.merge_pattern_match_results",
            new_callable=AsyncMock, side_effect=RuntimeError("merge blew up"),
        ):
            result = await execute_search(state, "revenue")

        assert result.startswith("Top 1 block from 1 record")
        assert state["final_results"] == [_HIT]


class TestDisableFlags:
    @pytest.mark.asyncio
    async def test_disable_semantic_skips_vector_search(self) -> None:
        retrieval = _retrieval([])
        state = _state(retrieval_service=retrieval, disable_semantic=True)
        builder, render, blob = _pipeline([])
        with builder, render, blob, patch(
            f"{_SEARCH}.run_pattern_match_with_llm_grep",
            new_callable=AsyncMock, return_value=[{"raw": "record"}],
        ), patch(
            f"{_SEARCH}.merge_pattern_match_results",
            new_callable=AsyncMock, return_value=[{"virtual_record_id": "vr-pm"}],
        ):
            await execute_search(state, "revenue")

        retrieval.search_with_filters.assert_not_called()

    @pytest.mark.asyncio
    async def test_disable_pattern_match_skips_grep(self) -> None:
        state = _state(retrieval_service=_retrieval([_HIT]), disable_pattern_match=True)
        builder, render, blob = _pipeline([_HIT])
        with builder, render, blob, patch(
            f"{_SEARCH}.run_pattern_match_with_llm_grep", new_callable=AsyncMock,
        ) as grep:
            await execute_search(state, "revenue")

        grep.assert_not_called()


class TestAccumulatedStateIsRepaired:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("key", "bad_value", "expected_type"), [
        ("virtual_record_id_to_result", "not-a-dict", dict),
        ("tool_records", "not-a-list", list),
    ])
    async def test_wrongly_typed_state_is_replaced_not_merged(self, key, bad_value, expected_type) -> None:
        state = _state(retrieval_service=_retrieval([_HIT]), **{key: bad_value})
        builder, render, blob = _pipeline([_HIT])
        with builder, render, blob, patch(
            f"{_SEARCH}.run_pattern_match_with_llm_grep", new_callable=AsyncMock, return_value=[],
        ):
            result = await execute_search(state, "revenue")

        assert result.startswith("Top 1 block")
        assert isinstance(state[key], expected_type)
