"""The legacy `retrieval__search_internal_knowledge` tool: its old name and
`connector_ids` parameter over the one search pipeline, `execute_search`
(tested in `knowledge_graph/test_search.py`)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.retrieval.retrieval import Retrieval

_EXECUTE_SEARCH = "app.agents.actions.retrieval.retrieval.execute_search"


def _make_state(**overrides: object) -> dict:
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "filters": {"apps": ["app-1"], "kb": []},
        "retrieval_service": AsyncMock(),
        "graph_provider": AsyncMock(),
        "config_service": None,
        "logger": MagicMock(),
        "llm": None,
    }
    state.update(overrides)
    return state


class TestRetrievalInit:
    def test_state_from_arg(self) -> None:
        state = _make_state()
        assert Retrieval(state=state).state is state

    def test_state_from_kwargs(self) -> None:
        state = _make_state()
        assert Retrieval(**{"state": state}).state is state

    def test_no_state(self) -> None:
        assert Retrieval().state is None


class TestSearchInternalKnowledge:
    @pytest.mark.asyncio
    async def test_no_state_returns_error(self) -> None:
        result = await Retrieval().search_internal_knowledge(query="q")
        assert json.loads(result)["status"] == "error"

    @pytest.mark.asyncio
    async def test_delegates_with_connector_ids_as_source_ids(self) -> None:
        state = _make_state()
        with patch(_EXECUTE_SEARCH, AsyncMock(return_value="result")) as search:
            result = await Retrieval(state=state).search_internal_knowledge(
                query="pricing", connector_ids=["c1", "c2"],
            )

        assert result == "result"
        search.assert_awaited_once_with(state, "pricing", source_ids=["c1", "c2"])

    @pytest.mark.asyncio
    async def test_no_query_returns_error(self) -> None:
        result = await Retrieval(state=_make_state()).search_internal_knowledge(query=None)
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "No search query" in parsed["message"]

    @pytest.mark.asyncio
    async def test_a_retrieval_error_is_reported_as_one(self) -> None:
        state = _make_state()
        state["retrieval_service"].search_with_filters.return_value = {
            "status_code": 503, "message": "vector store down",
        }

        result = await Retrieval(state=state).search_internal_knowledge(query="pricing")

        parsed = json.loads(result)
        assert (parsed["status"], parsed["message"]) == ("error", "vector store down")
