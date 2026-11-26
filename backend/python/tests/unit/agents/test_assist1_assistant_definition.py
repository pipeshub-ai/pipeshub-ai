"""ASSIST1 AS1-9: the system assistant definition (``agentIdPlaceholder``) on the run-definition route.

What Universal Agent Mode gives the caller (``get_assistant_agent``), through the projection a saved agent's
definition takes: the authenticated toolsets, the readable knowledge, the org's default web provider, no models; MCP
tools from the hub's approved manifests, or discovered live while the hub is off (MCPLIVE). The route's scope rule is
the agent chat route's.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.agents.run_definition import ASSISTANT_AGENT_KEY, assistant_mcp_servers
from app.api.middlewares.auth import AUTH_POLICY_ATTR
from app.harness.assembly.descriptor_pins import LiveMcpDescriptors
from tests.support.bound_org import bind_test_org
from tests.support.mcp_tool_admission import mcp_approvals_required  # noqa: F401 - the fixture, used by name

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """``authMiddleware`` binds the caller's org (every caller here is in ``org-1``); these tests call the route
    functions directly."""
    yield from bind_test_org("org-1")


_SESSION = {"userId": "u1", "orgId": "org-1"}
_EXECUTE_PAT = {"userId": "u1", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["agent:execute"]}
_CHAT_ONLY_PAT = {"userId": "u1", "orgId": "org-1", "isOAuth": True, "oauthScopes": ["conversation:chat"]}

_ASSISTANT = {
    "systemPrompt": "You are a helpful AI assistant",
    "models": [],
    "toolsets": [
        {"instanceId": "inst-1", "name": "jira", "tools": [{"name": "search_issues", "fullName": "jira.search_issues"}]},
    ],
    "mcpServers": [{"instanceId": "mcp-1", "name": "Notes", "typeId": "notes", "displayName": "Notes"}],
    "knowledge": [
        {"connectorId": "kb-1", "name": "Handbook", "type": "KB"},
        {"connectorId": "app-1", "name": "Drive", "type": "GOOGLE_DRIVE"},
    ],
}


class _Approved:
    approved_by = "p_admin"
    approved_at = 1

    def __init__(self, tools: dict[str, str]) -> None:
        self.tools = tools


class _Approvals:
    def __init__(self, by_instance: dict[str, dict[str, str]]) -> None:
        self._by_instance = by_instance

    async def approved_mcp_tools(self, instance_id: str) -> _Approved | None:
        tools = self._by_instance.get(instance_id)
        return _Approved(tools) if tools is not None else None

    def toolset_tool(self, toolset: str, tool: str) -> str | None:
        return None


def _route(path: str) -> APIRoute:
    from app.api.routes.agent import router

    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route
    raise AssertionError(f"{path} is not mounted")


async def _scope_checks(route: APIRoute, user: dict[str, Any], agent_id: str) -> None:
    from app.api.routes.agent import require_agent_execute_for_named_agents

    request = SimpleNamespace(state=SimpleNamespace(user=user))
    for dependency in route.dependant.dependencies:
        if getattr(dependency.call, AUTH_POLICY_ATTR, None) is not None:
            await dependency.call(request)
        elif dependency.call is require_agent_execute_for_named_agents:
            await dependency.call(request, agent_id)


async def _admitted(route: APIRoute, user: dict[str, Any], agent_id: str = "agent-1") -> bool:
    try:
        await _scope_checks(route, user, agent_id)
    except HTTPException as refused:
        assert refused.status_code == 403
        return False
    return True


class TestScopeRule:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("user", [_SESSION, _EXECUTE_PAT, _CHAT_ONLY_PAT], ids=["session", "execute-pat", "chat-pat"])
    async def test_a_token_may_fetch_the_definition_exactly_when_it_may_use_universal_agent_mode(
        self, user: dict[str, Any]
    ) -> None:
        legacy = await _admitted(_route("/{agent_id}/chat/stream"), user)
        durable = await _admitted(_route("/{agent_id}/run-definition"), user)
        assert durable == legacy

    @pytest.mark.asyncio
    async def test_a_token_lacking_agent_execute_is_refused_by_both(self) -> None:
        assert not await _admitted(_route("/{agent_id}/chat/stream"), _CHAT_ONLY_PAT)
        assert not await _admitted(_route("/{agent_id}/run-definition"), _CHAT_ONLY_PAT)

    @pytest.mark.asyncio
    async def test_a_chat_token_may_run_the_universal_agent_on_both(self) -> None:
        """Main's #4220: agent chat mode runs the universal agent with the caller's conversation:chat token."""
        assert await _admitted(_route("/{agent_id}/chat/stream"), _CHAT_ONLY_PAT, ASSISTANT_AGENT_KEY)
        assert await _admitted(_route("/{agent_id}/run-definition"), _CHAT_ONLY_PAT, ASSISTANT_AGENT_KEY)


class TestAssistantMcpServers:
    @pytest.mark.asyncio
    async def test_the_hub_names_the_approved_tools_as_live_discovery_names_them(self) -> None:
        servers = await assistant_mcp_servers(
            list(_ASSISTANT["mcpServers"]), _Approvals({"mcp-1": {"lookup_note": "sha256:" + "a" * 64}})
        )
        assert servers[0]["tools"] == [{"name": "lookup_note", "fullName": "mcp_notes_lookup_note"}]

    @pytest.mark.asyncio
    async def test_without_the_hub_the_live_tools_are_named(self) -> None:
        live = LiveMcpDescriptors(_Approvals({}), {"mcp-1": {"lookup_note": "sha256:" + "c" * 64}})
        servers = await assistant_mcp_servers(list(_ASSISTANT["mcpServers"]), live)
        assert servers[0]["tools"] == [{"name": "lookup_note", "fullName": "mcp_notes_lookup_note"}]

    @pytest.mark.asyncio
    async def test_a_server_neither_approved_nor_discovered_names_no_tool(self) -> None:
        assert (await assistant_mcp_servers(list(_ASSISTANT["mcpServers"]), _Approvals({})))[0]["tools"] == []
        live = LiveMcpDescriptors(_Approvals({}), {})
        assert (await assistant_mcp_servers(list(_ASSISTANT["mcpServers"]), live))[0]["tools"] == []


def _request(user: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.state.user = dict(user)
    request.query_params = {}
    request.app.state.toolset_registry = "registry"
    return request


def _services() -> dict[str, Any]:
    graph = AsyncMock()
    return {"graph_provider": graph, "config_service": AsyncMock(), "logger": MagicMock()}


async def _definition(
    *,
    hub: bool,
    mcp: bool = True,
    actions: bool = True,
    web: dict[str, Any] | None = None,
    live: dict[str, dict[str, str]] | None = None,
    answered: frozenset[str] | None = None,
) -> tuple[dict[str, Any], AsyncMock, dict[str, Any]]:
    from app.api.routes.agent import get_agent_run_definition

    services = _services()
    assistant = AsyncMock(return_value=(dict(_ASSISTANT), {}))
    with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
         patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
         patch("app.api.routes.agent.get_assistant_agent", assistant), \
         patch("app.api.routes.agent.resolve_web_search", new_callable=AsyncMock,
               return_value=web if web is not None else {"provider": "duckduckgo", "configuration": {}}), \
         patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock, return_value=actions), \
         patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=mcp), \
         patch("app.harness.mcp_hub.settings.hub_enabled", return_value=hub), \
         patch("app.harness.assembly.descriptor_pins.ToolDescriptors", return_value=_Approvals({"mcp-1": {"lookup_note": "sha256:" + "b" * 64}})), \
         patch(
             "app.api.routes.agent._live_mcp_listing",
             new_callable=AsyncMock,
             return_value=(live or {}, frozenset(live or {}) if answered is None else answered),
         ) as discovered:
        services["discovered"] = discovered
        response = await get_agent_run_definition(_request(_SESSION), ASSISTANT_AGENT_KEY)
    return json.loads(response.body), assistant, services


@pytest.mark.usefixtures("mcp_approvals_required")
class TestAssistantDefinitionRoute:
    @pytest.mark.asyncio
    async def test_with_the_hub_off_the_live_tools_are_granted_with_their_digests(self) -> None:
        body, _, services = await _definition(hub=False, live={"mcp-1": {"lookup_note": "sha256:" + "c" * 64}})
        assert body["definition"]["mcpServers"][0]["tools"] == [
            {"name": "lookup_note", "fullName": "mcp_notes_lookup_note", "descriptorDigest": "sha256:" + "c" * 64}
        ]
        assert services["discovered"].await_args.args[2]["userId"] == "u1"

    @pytest.mark.asyncio
    async def test_with_the_hub_off_a_server_discovery_missed_is_granted_nothing(self) -> None:
        body, _, _ = await _definition(hub=False, live={})
        assert body["definition"]["mcpServers"][0]["tools"] == []

    @pytest.mark.asyncio
    async def test_wp79_adm_1_a_server_whose_listing_held_every_tool_is_granted_nothing(self) -> None:
        body, _, _ = await _definition(hub=True, live={}, answered=frozenset({"mcp-1"}))
        assert body["definition"]["mcpServers"][0]["tools"] == []

    @pytest.mark.asyncio
    async def test_wp79_adm_2_with_the_hub_on_an_approved_tool_the_listing_offers_as_approved_is_granted(
        self,
    ) -> None:
        body, _, _ = await _definition(hub=True, live={"mcp-1": {"lookup_note": "sha256:" + "b" * 64}})
        assert body["definition"]["mcpServers"][0]["tools"] == [
            {"name": "lookup_note", "fullName": "mcp_notes_lookup_note", "descriptorDigest": "sha256:" + "b" * 64}
        ]

    @pytest.mark.asyncio
    async def test_the_platform_switches_apply_as_on_the_agent_route(self) -> None:
        body, _, _ = await _definition(hub=True, mcp=False, actions=False)
        assert body["definition"]["toolsets"] == []
        assert body["definition"]["mcpServers"] == []

    @pytest.mark.asyncio
    async def test_no_web_provider_grants_no_web_search(self) -> None:
        from app.api.routes.agent import get_agent_run_definition

        with patch("app.api.routes.agent.resolve_web_search", new_callable=AsyncMock, return_value=None), \
             patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=_services()), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
             patch("app.api.routes.agent.get_assistant_agent", AsyncMock(return_value=(dict(_ASSISTANT), {}))), \
             patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock, return_value=True), \
             patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=False), \
             patch("app.harness.mcp_hub.settings.hub_enabled", return_value=False):
            response = await get_agent_run_definition(_request(_SESSION), ASSISTANT_AGENT_KEY)
        assert json.loads(response.body)["definition"]["webSearch"] is None


class TestLiveMcpTools:
    @pytest.mark.asyncio
    async def test_discovery_uses_the_callers_own_credentials_and_releases_them(self) -> None:
        from app.api.routes.agent import _live_mcp_listing

        servers = list(_ASSISTANT["mcpServers"])
        configs = {"mcp-1": {"instance": {"_id": "mcp-1"}, "ownerId": "u1"}}
        live = {"mcp-1": {"lookup_note": "sha256:" + "c" * 64}}
        seen: dict[str, Any] = {}

        async def discovered(self: object, context: Any) -> tuple[dict[str, dict[str, str]], frozenset[str]]:  # noqa: ANN401
            seen["context"] = context
            return live, frozenset(live)

        with patch("app.api.routes.agent._resolve_mcp_server_configs", new_callable=AsyncMock,
                   return_value=(configs, servers, [], [])) as resolve, \
             patch("app.agents.agent_loop.mcp_tool_loader.MCPToolProvider.live_listing", discovered), \
             patch("app.agents.agent_loop.context.AgentContext.close_credentials", new_callable=AsyncMock) as closed:
            listed = await _live_mcp_listing(servers, AsyncMock(), _SESSION, {"email": "u1@x"}, MagicMock())
            assert listed == (live, frozenset({"mcp-1"}))

        assert resolve.await_args.kwargs["credential_lookup_id"] == "u1"
        assert resolve.await_args.kwargs["is_service_account"] is False
        assert seen["context"].credential_owner == "u1"
        # WP-79: admission's discovery is a harness run's, so the hub (where it governs them) admits what it lists.
        assert seen["context"].harness_run is True
        assert seen["context"].mcp_server_configs == configs
        closed.assert_awaited_once()
