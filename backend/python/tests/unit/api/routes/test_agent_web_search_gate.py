"""Which ``chat_stream`` turns keep web search, driven through the real route.

The universal agent's tool pickers (composer and project) never list web search, so a tool
list sent for it must not switch web search off; its Web Search switch decides. A named
agent lists web search in its picker, so leaving it out of the list still turns it off.
"""
from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ORG = "o1"
ASSISTANT = "agentIdPlaceholder"
NAMED = "agent-1"
DUCKDUCKGO = {"provider": "duckduckgo", "configuration": {}, "providers": []}
LOGGER = "agent-web-search-gate-test"


def _agent(**extra: object) -> dict:
    return {"_key": NAMED, "name": "agent", "knowledge": [], "toolsets": [], "models": [],
            "createdBy": "caller-key", "isServiceAccount": False, **extra}


async def _run(agent_id: str, body: dict, *, agent: dict | None = None) -> dict:
    """Run chat_stream; return the query_info the agent loop got."""
    from app.api.routes.agent import chat_stream

    captured: dict = {}

    async def _recording_loop(query_info: dict, *_args: object, **_kwargs: object) -> AsyncIterator[str]:
        captured.update(query_info)
        return
        yield  # pragma: no cover - marks this an async generator

    graph = AsyncMock()
    graph.get_agent = AsyncMock(return_value=agent or _agent())
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": False, "role": "viewer"})
    services = {
        "graph_provider": graph,
        "retrieval_service": MagicMock(),
        "reranker_service": MagicMock(),
        "config_service": AsyncMock(),
        "logger": logging.getLogger(LOGGER),
    }
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    request.headers = {}
    request.app.state.toolset_registry = MagicMock()

    route_patches = [
        ("get_services", {"new_callable": AsyncMock, "return_value": services}),
        ("_get_user_context", {"return_value": {"userId": "caller", "orgId": ORG}}),
        ("_get_user_document", {"new_callable": AsyncMock,
                                "return_value": {"email": "u@x.com", "_key": "caller-key"}}),
        ("_get_org_info", {"new_callable": AsyncMock, "return_value": {"orgId": ORG, "accountType": "enterprise"}}),
        ("get_llm_for_chat", {"new_callable": AsyncMock, "return_value": (MagicMock(), {}, {})}),
        ("load_entity_vector_store", {"new_callable": AsyncMock, "return_value": None}),
        ("get_assistant_agent", {"new_callable": AsyncMock, "return_value": (_agent(_key=ASSISTANT), {})}),
        ("resolve_web_search", {"new_callable": AsyncMock, "return_value": DUCKDUCKGO}),
        ("run_agent_loop_stream", {"new": _recording_loop}),
    ]
    flag_patches = [
        "app.services.featureflag.platform_settings.is_actions_enabled",
        "app.services.featureflag.platform_settings.is_user_context_enabled",
        "app.agents.mcp.service.is_mcp_enabled",
    ]
    with ExitStack() as stack:
        for target, kwargs in route_patches:
            stack.enter_context(patch(f"app.api.routes.agent.{target}", **kwargs))
        for target in flag_patches:
            stack.enter_context(patch(target, new_callable=AsyncMock, return_value=False))
        response = await chat_stream(request, agent_id)
        [chunk async for chunk in response.body_iterator]
    assert captured, "the agent loop never ran"
    return captured


class TestAssistant:
    async def test_a_narrowed_tool_list_keeps_web_search(self) -> None:
        query_info = await _run(ASSISTANT, {
            "query": "who won ipl 2026", "tools": ["jira.search_issues"],
            "agentCapabilities": {"internalSearch": True, "webSearch": True},
        })
        assert query_info["webSearchConfig"] == DUCKDUCKGO

    async def test_an_empty_project_tool_list_keeps_web_search(self) -> None:
        query_info = await _run(ASSISTANT, {"query": "q", "tools": [], "strictScope": True})
        assert query_info["webSearchConfig"] == DUCKDUCKGO

    async def test_the_web_search_switch_turns_it_off(self) -> None:
        query_info = await _run(ASSISTANT, {"query": "q", "agentCapabilities": {"webSearch": False}})
        assert query_info["webSearchConfig"] is None
        assert query_info["webSearch"] is None


class TestNamedAgent:
    WITH_WEB = {"webSearch": {"provider": "duckduckgo"}}

    async def test_leaving_web_search_out_of_the_tool_list_turns_it_off(self) -> None:
        query_info = await _run(NAMED, {"query": "q", "tools": ["jira.search_issues"]}, agent=_agent(**self.WITH_WEB))
        assert query_info["webSearchConfig"] is None

    async def test_the_tool_list_wins_over_the_web_search_switch(self) -> None:
        query_info = await _run(NAMED, {
            "query": "q", "tools": ["jira.search_issues"], "agentCapabilities": {"webSearch": True},
        }, agent=_agent(**self.WITH_WEB))
        assert query_info["webSearchConfig"] is None

    async def test_selecting_web_search_keeps_it(self) -> None:
        query_info = await _run(NAMED, {"query": "q", "tools": ["web_search"]}, agent=_agent(**self.WITH_WEB))
        assert query_info["webSearchConfig"] == DUCKDUCKGO

    async def test_an_agent_without_web_search_gets_none(self) -> None:
        query_info = await _run(NAMED, {"query": "q"})
        assert query_info["webSearchConfig"] is None


class TestReasonIsLogged:
    @staticmethod
    async def _messages(caplog, agent_id: str, body: dict, agent: dict | None = None) -> list[str]:
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await _run(agent_id, body, agent=agent)
        return [r.getMessage() for r in caplog.records if "web search" in r.getMessage()]

    async def test_on_names_the_provider(self, caplog) -> None:
        assert await self._messages(caplog, ASSISTANT, {"query": "q"}) == [
            f"chat_stream: web search on, provider=duckduckgo (agent_id={ASSISTANT})",
        ]

    async def test_switched_off(self, caplog) -> None:
        messages = await self._messages(caplog, ASSISTANT, {"query": "q", "agentCapabilities": {"webSearch": False}})
        assert messages == [f"chat_stream: web search off, reason=capability_off (agent_id={ASSISTANT})"]

    async def test_filtered_out_by_the_tool_list(self, caplog) -> None:
        messages = await self._messages(
            caplog, NAMED, {"query": "q", "tools": ["jira.search_issues"]},
            agent=_agent(webSearch={"provider": "duckduckgo"}),
        )
        assert messages == [f"chat_stream: web search off, reason=tools_filter (agent_id={NAMED})"]

    async def test_not_configured(self, caplog) -> None:
        messages = await self._messages(caplog, NAMED, {"query": "q"})
        assert messages == [f"chat_stream: web search off, reason=not_configured (agent_id={NAMED})"]
