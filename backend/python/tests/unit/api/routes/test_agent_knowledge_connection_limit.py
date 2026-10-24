"""The Knowledge Sources a connected app's chat turn names.

The universal agent's knowledge is every source the user can read, and the
prompt lists each one with its id. A connected app limited to some sources (a
personal access token or an OAuth app with a knowledge limit) must see only
those: a source the limit names whole as today, one it reaches through some
of its folders or files without its id, and every other source not at all.
"""

import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.selection_scope as selection_scope
from app.api.routes.agent import _knowledge_within_connection
from app.modules.agents.context.source_catalog import SourceCatalog
from app.modules.retrieval.selection_scope import (
    CONNECTION_LIMIT_TOO_LARGE_MESSAGE,
    SelectionTooLargeError,
)

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


async def _stream_with_a_limit_too_large_to_check(capabilities: dict) -> tuple[str, dict]:
    """Run the universal agent's chat_stream for a connected app whose limit
    cannot be checked; return the streamed body and what the agent loop got."""
    from app.api.routes.agent import chat_stream

    captured: dict = {}

    async def recording_loop(query_info: dict, *_args: object, **_kwargs: object) -> AsyncIterator[str]:
        captured.update(query_info)
        return
        yield  # pragma: no cover - marks this an async generator

    graph = AsyncMock()
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": False, "role": "viewer"})
    services = {
        "graph_provider": graph, "retrieval_service": MagicMock(), "reranker_service": MagicMock(),
        "config_service": AsyncMock(), "logger": logging.getLogger("agent-knowledge-limit-test"),
    }
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps({"query": "q", "agentCapabilities": capabilities}).encode())
    request.headers = {}
    request.state.user = {"userId": "caller", "orgId": "o1", "knowledgeLimit": LIMIT}
    assistant = {"_key": "agentIdPlaceholder", "name": "assistant", "knowledge": KNOWLEDGE, "toolsets": [],
                 "models": [], "createdBy": "caller-key", "isServiceAccount": False}
    route_patches = {
        "get_services": {"new_callable": AsyncMock, "return_value": services},
        "_get_user_context": {"return_value": {"userId": "caller", "orgId": "o1"}},
        "_get_user_document": {"new_callable": AsyncMock, "return_value": {"email": "u@x.com", "_key": "caller-key"}},
        "_get_org_info": {"new_callable": AsyncMock, "return_value": {"orgId": "o1", "accountType": "enterprise"}},
        "get_llm_for_chat": {"new_callable": AsyncMock, "return_value": (MagicMock(), {}, {})},
        "load_entity_vector_store": {"new_callable": AsyncMock, "return_value": None},
        "get_assistant_agent": {"new_callable": AsyncMock, "return_value": (assistant, {})},
        "resolve_web_search": {"new_callable": AsyncMock, "return_value": None},
        "run_agent_loop_stream": {"new": recording_loop},
        "connection_coverage": {"new_callable": AsyncMock, "side_effect": SelectionTooLargeError()},
    }
    with ExitStack() as stack:
        for target, kwargs in route_patches.items():
            stack.enter_context(patch(f"app.api.routes.agent.{target}", **kwargs))
        for target in (
            "app.services.featureflag.platform_settings.is_actions_enabled",
            "app.services.featureflag.platform_settings.is_user_context_enabled",
            "app.agents.mcp.service.is_mcp_enabled",
        ):
            stack.enter_context(patch(target, new_callable=AsyncMock, return_value=False))
        response = await chat_stream(request, "agentIdPlaceholder")
        chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(c.decode() if isinstance(c, bytes) else str(c) for c in chunks)
    return body, captured


@pytest.mark.asyncio
class TestALimitTooLargeToCheck:
    async def test_a_turn_with_internal_search_off_still_runs(self) -> None:
        body, query_info = await _stream_with_a_limit_too_large_to_check({"internalSearch": False})

        assert query_info, f"the agent loop never ran: {body[:500]}"
        assert query_info["knowledge"] == []
        assert CONNECTION_LIMIT_TOO_LARGE_MESSAGE not in body

    async def test_a_turn_that_searches_says_the_limit_is_too_large(self) -> None:
        body, query_info = await _stream_with_a_limit_too_large_to_check({"internalSearch": True})

        assert not query_info
        assert "selection_too_large" in body
