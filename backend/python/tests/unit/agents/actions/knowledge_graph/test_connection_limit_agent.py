"""The chat agent's knowledge tools under a connected app's limit name nothing
outside it beside what they return."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import (
    KnowledgeGraph,
    _copies_outside_selection,
)
from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record

_FETCH = "app.agents.actions.knowledge_graph.ops.fetch"
LIMITED = {"apps": ["a"], "records": ["f"], "connectionApps": [], "connectionRecordGroups": [],
           "connectionRecords": ["f"]}


def _context(filters: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        org_id="org-1", user_id="user-1", graph_provider=MagicMock(), full_records_fetched=set(),
        tool_state={"filters": filters}, is_multimodal_llm=False, context_length=128_000, query="",
        retrieval_service=None,
    )


class TestTheFetchTool:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("filters", "sources"), [
        ({"apps": ["a"]}, None),
        (LIMITED, {"apps": [], "recordGroups": [], "records": ["f"]}),
    ])
    async def test_it_hands_the_limit_to_the_read(self, filters, sources) -> None:
        tool = MagicMock(coroutine=AsyncMock(return_value={"ok": False, "not_available_ids": ["f"]}))
        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=tool) as create, \
             patch(f"{_FETCH}._ids_outside_scope", AsyncMock(return_value=[])):
            await execute_fetch_record(
                context=_context(filters), virtual_records={}, citation_ref_mapper=None, record_ids=["f"],
            )

        assert create.call_args.kwargs["connection_sources"] == sources


class TestACopyOutsideTheConversation:
    @pytest.mark.asyncio
    async def test_a_copy_beyond_a_connected_apps_limit_reads_like_no_copy(self) -> None:
        """Telling them apart would show the app what exists beyond its limit."""
        state = {
            "org_id": "org-1", "user_id": "user-1", "logger": MagicMock(), "filters": LIMITED,
            "agent_knowledge": [{"connectorId": "a", "type": "JIRA"}],
        }
        gp = AsyncMock()
        gp.get_knowledge_hub_filter_options = AsyncMock(return_value={"apps": []})
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        state["graph_provider"] = gp
        outside = SimpleNamespace(id="rec-out", name="Board minutes", connector_name="Confluence")

        async def navigate(matches: list) -> tuple[bool, str]:
            resolver = MagicMock()
            resolver.resolve_many = AsyncMock(return_value=SimpleNamespace(matches=matches))
            with patch(
                "app.agents.actions.knowledge_graph.knowledge_graph.RecordResolver", return_value=resolver,
            ), patch(
                "app.agents.actions.knowledge_graph.knowledge_graph.ids_within_scope", new=AsyncMock(return_value=set()),
            ):
                return await KnowledgeGraph(state=state).navigate(node_id="PA-1")

        beyond, absent = await navigate([outside]), await navigate([])

        assert beyond == absent
        assert "Board minutes" not in beyond[1] and "Confluence" not in beyond[1]

    def test_a_signed_in_user_is_told_where_the_copy_is(self) -> None:
        copies = [SimpleNamespace(id="rec-out", name="Board minutes", connector_name="Confluence")]

        assert "Board minutes (Confluence)" in _copies_outside_selection("PA-1", copies)
