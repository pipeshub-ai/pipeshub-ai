"""The legacy ``retrieval__search_internal_knowledge`` tool: a thin wrapper over
``execute_search`` that keeps its old name and ``connector_ids`` parameter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agents.actions.knowledge_graph.ops.results import search_result_summary
from app.agents.actions.retrieval.retrieval import (
    Retrieval,
)
from app.agents.actions.retrieval.retrieval import (
    _search_internal_knowledge_args_summary as args_summary,
)


class TestDelegatesToTheSharedSearch:
    @pytest.mark.asyncio
    async def test_connector_ids_become_source_ids(self) -> None:
        state = {"filters": {}}
        with patch(
            "app.agents.actions.retrieval.retrieval.execute_search",
            new_callable=AsyncMock, return_value="Top 1 block from 1 record",
        ) as search:
            result = await Retrieval(state=state).search_internal_knowledge(
                query="revenue", connector_ids=["c1"],
            )

        assert result == "Top 1 block from 1 record"
        search.assert_awaited_once_with(state, "revenue", source_ids=["c1"])

    @pytest.mark.asyncio
    async def test_without_state_is_an_error_not_a_search(self) -> None:
        with patch(
            "app.agents.actions.retrieval.retrieval.execute_search", new_callable=AsyncMock,
        ) as search:
            result = await Retrieval(state=None).search_internal_knowledge(query="revenue")

        assert json.loads(result)["status"] == "error"
        search.assert_not_awaited()

    def test_result_summary_is_the_shared_one(self) -> None:
        meta = getattr(Retrieval.search_internal_knowledge, TOOL_META_ATTR)
        assert meta.result_summary is search_result_summary


class TestArgsSummary:
    def test_query_only(self) -> None:
        assert args_summary({"query": "bug bash"}) == 'Searched for "bug bash"'

    def test_query_and_unlabelled_connector_ids(self) -> None:
        assert args_summary({"query": "bug bash", "connector_ids": ["c1", "c2"]}) == (
            'Searched for "bug bash"\n2 sources'
        )

    def test_missing_query_returns_none(self) -> None:
        assert args_summary({}) is None
