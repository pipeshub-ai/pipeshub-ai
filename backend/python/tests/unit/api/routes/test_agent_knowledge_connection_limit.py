"""The Knowledge Sources a connected app's chat turn names.

The universal agent's knowledge is every source the user can read, and the
prompt lists each one with its id. A connected app limited to some sources (a
personal access token or an OAuth app with a knowledge limit) must see only
those: a source the limit names whole as today, one it reaches through some
of its folders or files without its id, and every other source not at all.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.modules.retrieval.selection_scope as selection_scope
from app.api.routes.agent import _knowledge_within_connection
from app.modules.agents.context.source_catalog import SourceCatalog

KNOWLEDGE = [
    {"connectorId": "kb-whole", "type": "KB", "name": "Finance"},
    {"connectorId": "kb-part", "type": "KB", "name": "Legal"},
    {"connectorId": "kb-out", "type": "KB", "name": "Board minutes"},
    {"connectorId": "jira-out", "type": "JIRA", "name": "Jira"},
]
LIMIT = {"apps": ["kb-whole"], "recordGroups": [], "records": ["folder-in-legal"]}


@pytest.fixture(autouse=True)
def _clear_memo() -> Iterator[None]:
    selection_scope._memo.clear()
    yield
    selection_scope._memo.clear()


def _graph() -> MagicMock:
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
    graph.get_selection_nodes = AsyncMock(return_value={
        "groups": [],
        "records": [{"id": "folder-in-legal", "vrid": "", "connectorId": "kb-part"}],
    })
    return graph


async def _prompt(limit: dict | None) -> str:
    knowledge = await _knowledge_within_connection(_graph(), "user-1", "org-1", KNOWLEDGE, limit)
    return SourceCatalog.from_state({"agent_knowledge": knowledge}).render()


@pytest.mark.asyncio
class TestKnowledgeSourcesUnderAConnectionLimit:
    async def test_a_source_outside_the_limit_is_left_out(self) -> None:
        prompt = await _prompt(LIMIT)

        assert "Board minutes" not in prompt and "kb-out" not in prompt
        assert "jira-out" not in prompt

    async def test_a_source_the_limit_names_whole_is_listed_with_its_id(self) -> None:
        prompt = await _prompt(LIMIT)

        assert "- **Finance** — KB collection — id `kb-whole`" in prompt

    async def test_a_partly_shared_source_is_listed_without_its_id(self) -> None:
        prompt = await _prompt(LIMIT)

        (line,) = [line for line in prompt.splitlines() if "**Legal**" in line]
        assert "kb-part" not in prompt
        assert "only part of it is shared through this connection" in line

    async def test_without_a_limit_every_source_is_listed_as_before(self) -> None:
        knowledge = await _knowledge_within_connection(_graph(), "user-1", "org-1", KNOWLEDGE, None)

        assert knowledge is KNOWLEDGE

    async def test_an_unknown_user_sees_no_source(self) -> None:
        graph = _graph()
        graph.get_user_by_user_id = AsyncMock(return_value=None)

        assert await _knowledge_within_connection(graph, "user-1", "org-1", KNOWLEDGE, LIMIT) == []
