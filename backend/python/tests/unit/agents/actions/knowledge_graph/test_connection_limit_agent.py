"""The chat agent's knowledge tools under a connected app's limit name nothing
outside it beside what they return."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import _copies_outside_selection
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
    def test_a_connected_app_is_not_told_where_the_copy_is(self) -> None:
        copies = [SimpleNamespace(id="rec-out", name="Board minutes", connector_name="Confluence")]

        told = _copies_outside_selection("PA-1", copies, connected_app=True)

        assert "Board minutes" not in told and "Confluence" not in told
        assert told == "PA-1 is outside what this conversation is limited to."

    def test_a_signed_in_user_is_told_where_the_copy_is(self) -> None:
        copies = [SimpleNamespace(id="rec-out", name="Board minutes", connector_name="Confluence")]

        assert "Board minutes (Confluence)" in _copies_outside_selection("PA-1", copies)
