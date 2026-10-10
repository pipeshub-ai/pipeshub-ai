"""`MCPToolProvider` (`app/agents/agent_loop/mcp_tool_loader.py`) — the MCP
analog of `PipesHubToolLoader.load()`: live discovery per attached instance,
one `register_toolset` group per instance nested under `MCP_PARENT`, and
soft-skip failures recorded on `context.mcp_tool_load_failures`. There is
deliberately NO fallback to the graph node's stored tool list on a
discovery failure — see the module's docstring for why a schema-less
fallback tool was worse than no tool at all."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import mcp_session as mcp_session_module
from app.agents.agent_loop.lazy_tools_wiring import MCP_PARENT
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp.client import ToolListing
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.service import credentials_to_discovery_dict, instance_config_from_dict
from tests.unit.agents.adapter.conftest import make_context


def _instance(instance_id: str = "inst-1", *, type_id: str = "jira_mcp") -> dict[str, Any]:
    return {
        "_id": instance_id, "orgId": "org-1", "createdBy": "user-1",
        "name": "JiraMCP", "typeId": type_id, "transport": "sse", "authMode": "none",
        "createdAt": 0, "updatedAt": 0,
    }


def _context_with_server(
    *, instance_id: str = "inst-1", name: str = "JiraMCP", attached_tools: list[dict] | None = None,
    type_id: str = "jira_mcp", **extra: Any,  # noqa: ANN401
) -> Any:
    mcp_server: dict[str, Any] = {"instanceId": instance_id, "name": name, "displayName": name, "typeId": type_id}
    if attached_tools is not None:
        mcp_server["tools"] = attached_tools
    return make_context(
        mcp_servers=[mcp_server],
        mcp_server_configs={
            instance_id: {"instance": _instance(instance_id, type_id=type_id), "auth": {}, "ownerId": "user-1"},
        },
        **extra,
    )


async def _discover_ok(config: Any, credentials: dict, timeout_seconds: float = 10.0, namespace: str | None = None) -> list[MCPToolInfo]:
    return [MCPToolInfo(
        name="search", namespaced_name=f"mcp_{config.name.lower()}_search",
        description="Search", input_schema={},
    )]


async def _discover_fails(config: Any, credentials: dict, timeout_seconds: float = 10.0, namespace: str | None = None) -> list[MCPToolInfo]:
    raise RuntimeError("connection refused")


def _session_discovery(fake: Any) -> Any:  # noqa: ANN401
    """Runs a `discover_tools`-shaped fake as the turn's tool load (`MCPSessionManager.tools`)."""
    async def tools(self: MCPSessionManager, server: Any, namespace: str) -> tuple[list[MCPToolInfo], None]:  # noqa: ANN401
        config = instance_config_from_dict(server.instance)
        credentials = credentials_to_discovery_dict(server.instance.get("authMode", ""), server.auth)
        return await fake(config, credentials, namespace=namespace), None
    return tools


async def _discover_refused_for_scope(config: Any, credentials: dict, timeout_seconds: float = 10.0, namespace: str | None = None) -> list[MCPToolInfo]:  # noqa: ANN401
    from app.agents.mcp.errors import MCPInsufficientScopeError

    raise MCPInsufficientScopeError("JiraMCP needs more permission", scopes=["read:jira-work"])


class TestAListingRefusedForPermission:
    async def test_an_agent_chat_offers_the_sign_in_card(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_refused_for_scope))
        context = _context_with_server(client_name="pipeshub-ai", conversation_id="conv-1", protocol="agui", is_assistant=False)

        await MCPToolProvider().load_into(ToolRegistry(), context)

        assert context.mcp_tool_load_failures == [
            {"instanceId": "inst-1", "name": "JiraMCP", "reason": "needs_permission", "signInHere": True},
        ]
        assert [(s["instanceId"], s["scopes"]) for s in context.mcp_sign_in_needed] == [("inst-1", ["read:jira-work"])]

    async def test_the_assistant_doesnt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It attaches every server a person has; the card would follow every unrelated reply."""
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_refused_for_scope))
        context = _context_with_server(client_name="pipeshub-ai", conversation_id="conv-1", protocol="agui", is_assistant=True)

        await MCPToolProvider().load_into(ToolRegistry(), context)

        assert context.mcp_tool_load_failures == [{"instanceId": "inst-1", "name": "JiraMCP", "reason": "needs_permission"}]
        assert context.mcp_sign_in_needed == []


class TestLoadInto:
    async def test_registers_discovered_tool_and_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_ok))
        registry = ToolRegistry()
        context = _context_with_server()

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jiramcp_search")
        assert registry.tools_in_toolset(MCP_PARENT) == ["mcp_jiramcp_search"]
        group = next(g for g in registry.toolsets() if g.name == "mcp_jiramcp")
        assert group.parent == MCP_PARENT
        assert context.mcp_tool_load_failures == []

    async def test_registered_adapters_carry_the_request_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Image results need the context's multimodal flag and image admission.
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_ok))
        registry = ToolRegistry()
        context = _context_with_server()

        await MCPToolProvider().load_into(registry, context)

        assert registry.resolve_by_name("mcp_jiramcp_search")._context is context

    async def test_no_attached_servers_is_a_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(
            MCPSessionManager, "tools",
            _session_discovery(lambda *a, **k: calls.append(1) or _discover_ok(*a, **k)),
        )
        registry = ToolRegistry()
        context = make_context()

        await MCPToolProvider().load_into(registry, context)

        assert registry.names() == []
        assert calls == []
        assert not any(g.name == MCP_PARENT for g in registry.toolsets())

    async def test_discovery_failure_registers_nothing_even_with_an_attached_tool_list(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The removed fallback used to synthesize a schema-less tool from
        `attached_tools` (name/description only, `input_schema={}`) on a
        discovery failure — deliberately gone now (see the module
        docstring): that tool looked callable but had no parameters for the
        LLM to fill in, producing exactly the "incomplete arguments"
        symptom this whole change exists to avoid. A discovery failure must
        register nothing and record the failure, regardless of whether an
        attached tool list happens to be present."""
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_fails))
        registry = ToolRegistry()
        context = _context_with_server(attached_tools=[
            {"name": "search", "fullName": "mcp_jira_mcp_search", "description": "Search Jira"},
        ])

        await MCPToolProvider().load_into(registry, context)

        assert registry.names() == []
        assert not registry.has("mcp_jira_mcp_search")
        assert context.mcp_tool_load_failures == [
            {"instanceId": "inst-1", "name": "JiraMCP", "reason": "error"},
        ]
        assert not any(g.name == MCP_PARENT for g in registry.toolsets())

    async def test_discovery_failure_with_no_fallback_records_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_fails))
        registry = ToolRegistry()
        context = _context_with_server()  # attached_tools is None — no stored selection to fall back to

        await MCPToolProvider().load_into(registry, context)

        assert registry.names() == []
        assert context.mcp_tool_load_failures == [
            {"instanceId": "inst-1", "name": "JiraMCP", "reason": "error"},
        ]
        assert not any(g.name == MCP_PARENT for g in registry.toolsets())

    async def test_empty_attached_tool_list_is_also_no_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_fails))
        registry = ToolRegistry()
        context = _context_with_server(attached_tools=[])

        await MCPToolProvider().load_into(registry, context)

        assert registry.names() == []
        assert context.mcp_tool_load_failures[0]["reason"] == "error"

    async def test_two_instances_load_independently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _discover(config: Any, credentials: dict, timeout_seconds: float = 10.0, namespace: str | None = None) -> list[MCPToolInfo]:
            if config.id == "inst-1":
                return [MCPToolInfo(name="search", namespaced_name="mcp_jira_search", input_schema={})]
            raise RuntimeError("down")

        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover))
        registry = ToolRegistry()
        context = make_context(
            mcp_servers=[
                {"instanceId": "inst-1", "name": "JiraMCP", "typeId": "jira_mcp"},
                {"instanceId": "inst-2", "name": "SlackMCP", "typeId": "slack_mcp"},
            ],
            mcp_server_configs={
                "inst-1": {"instance": _instance("inst-1", type_id="jira_mcp"), "auth": {}, "ownerId": "user-1"},
                "inst-2": {"instance": _instance("inst-2", type_id="slack_mcp"), "auth": {}, "ownerId": "user-1"},
            },
        )

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jira_search")
        assert context.mcp_tool_load_failures == [
            {"instanceId": "inst-2", "name": "SlackMCP", "reason": "error"},
        ]
        assert any(g.name == MCP_PARENT for g in registry.toolsets())

    async def test_colliding_tool_name_is_skipped_but_group_still_registers_survivors(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _discover(config: Any, credentials: dict, timeout_seconds: float = 10.0, namespace: str | None = None) -> list[MCPToolInfo]:
            return [
                MCPToolInfo(name="search", namespaced_name="mcp_jira_search", input_schema={}),
                MCPToolInfo(name="create", namespaced_name="mcp_jira_create", input_schema={}),
            ]

        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover))
        registry = ToolRegistry()
        # Pre-register a colliding name to force one of the two discovered
        # tools to be skipped as a duplicate.
        from app.agent_loop_lib.tools.base import ToolOutput, ToolParameter

        class _Existing:
            name = "mcp_jira_search"
            short_description = "existing"
            description = "existing"
            path = "/existing/mcp_jira_search"
            parameters: list[ToolParameter] = []

            async def execute(self, **kwargs: Any) -> ToolOutput:
                return ToolOutput(success=True, data="existing")

        registry.register_tool(_Existing())
        context = _context_with_server()

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jira_create")
        assert registry.path_for_name("mcp_jira_search") == "/existing/mcp_jira_search"
        assert context.mcp_tool_load_failures == []

    async def test_all_discovered_tools_colliding_records_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _discover(config: Any, credentials: dict, timeout_seconds: float = 10.0, namespace: str | None = None) -> list[MCPToolInfo]:
            return [MCPToolInfo(name="search", namespaced_name="mcp_jira_search", input_schema={})]

        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover))
        registry = ToolRegistry()

        from app.agent_loop_lib.tools.base import ToolOutput, ToolParameter

        class _Existing:
            name = "mcp_jira_search"
            short_description = "existing"
            description = "existing"
            path = "/existing/mcp_jira_search"
            parameters: list[ToolParameter] = []

            async def execute(self, **kwargs: Any) -> ToolOutput:
                return ToolOutput(success=True, data="existing")

        registry.register_tool(_Existing())
        context = _context_with_server()

        await MCPToolProvider().load_into(registry, context)

        assert context.mcp_tool_load_failures
        assert context.mcp_tool_load_failures[0]["instanceId"] == "inst-1"
        assert context.mcp_tool_load_failures[0]["reason"] == "error"

    async def test_discovery_respects_attached_tools_selection(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _discover(config: Any, credentials: dict, timeout_seconds: float = 10.0, namespace: str | None = None) -> list[MCPToolInfo]:
            return [
                MCPToolInfo(name="search", namespaced_name="mcp_jira_mcp_search", input_schema={}),
                MCPToolInfo(name="create", namespaced_name="mcp_jira_mcp_create", input_schema={}),
            ]

        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover))
        registry = ToolRegistry()
        context = _context_with_server(attached_tools=[
            {"name": "search", "fullName": "mcp_jira_mcp_search"},
        ])

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jira_mcp_search")
        assert not registry.has("mcp_jira_mcp_create")
        assert context.mcp_tool_load_failures == []


class TestFilterByAttached:
    def test_none_attached_tools_keeps_all_discovered(self) -> None:
        from app.agents.agent_loop.mcp_access import ResolvedMCPServer

        server = ResolvedMCPServer(
            instance_id="inst-1", name="JiraMCP", display_name="JiraMCP",
            instance=_instance(type_id="jira_mcp"), auth={}, owner_id="user-1",
            attached_tools=None,
        )
        discovered = [
            MCPToolInfo(name="search", namespaced_name="mcp_jira_mcp_search", input_schema={}),
            MCPToolInfo(name="create", namespaced_name="mcp_jira_mcp_create", input_schema={}),
        ]
        assert MCPToolProvider._filter_by_attached(server, discovered) == discovered

    def test_attached_tools_keeps_only_selected(self) -> None:
        from app.agents.agent_loop.mcp_access import ResolvedMCPServer

        server = ResolvedMCPServer(
            instance_id="inst-1", name="JiraMCP", display_name="JiraMCP",
            instance=_instance(type_id="jira_mcp"), auth={}, owner_id="user-1",
            attached_tools=[{"name": "search", "fullName": "mcp_jira_mcp_search"}],
        )
        discovered = [
            MCPToolInfo(name="search", namespaced_name="mcp_jira_mcp_search", input_schema={}),
            MCPToolInfo(name="create", namespaced_name="mcp_jira_mcp_create", input_schema={}),
        ]
        filtered = MCPToolProvider._filter_by_attached(server, discovered)
        assert [t.namespaced_name for t in filtered] == ["mcp_jira_mcp_search"]


def _http_401() -> Exception:
    import httpx

    from app.agents.mcp.errors import MCPConnectionError

    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    try:
        raise httpx.HTTPStatusError("rejected", request=request, response=httpx.Response(401, request=request))
    except httpx.HTTPStatusError as cause:
        error = MCPConnectionError("HTTP 401 Unauthorized from https://mcp.example.com/mcp")
        error.__cause__ = cause
        return error


def _oauth_context() -> Any:  # noqa: ANN401
    context = _context_with_server()
    instance = context.mcp_server_configs["inst-1"]["instance"]
    instance["authMode"] = "oauth"
    context.mcp_server_configs["inst-1"]["auth"] = {"isAuthenticated": True, "oauthTokens": {"accessToken": "old"}}
    return context


class _FakeClientManager:
    """Stands in for `MCPClientManager` in the turn's session: lists one tool, or answers
    HTTP 401 to the token named in `rejected`."""

    seen_tokens: list[str | None] = []
    rejected = "Bearer old"

    def __init__(self, config: Any, env: dict | None = None, headers: dict | None = None) -> None:  # noqa: ANN401
        self.config = config
        self.headers = headers or {}

    is_open = True
    server_instructions: str | None = None

    async def open(self) -> "_FakeClientManager":
        return self

    def update_headers(self, headers: dict[str, str]) -> None:
        self.headers.update(headers)

    tools_changed = False

    async def list_tools_in_session(self) -> list[dict[str, Any]]:
        return (await self.fetch_tool_listing_in_session()).tools

    async def fetch_tool_listing_in_session(self) -> ToolListing:
        _FakeClientManager.seen_tokens.append(self.headers.get("Authorization"))
        if self.headers.get("Authorization") == self.rejected:
            raise _http_401()
        return ToolListing(tools=[{"name": "search", "description": "Search", "inputSchema": {}}], instructions=self.server_instructions)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_sessions(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClientManager]:
    monkeypatch.setattr(_FakeClientManager, "seen_tokens", [])
    monkeypatch.setattr(_FakeClientManager, "rejected", "Bearer old")
    monkeypatch.setattr(_FakeClientManager, "server_instructions", None)
    monkeypatch.setattr(mcp_session_module, "MCPClientManager", _FakeClientManager)
    return _FakeClientManager


class TestRefreshOnExpiredTokenAtDiscovery:
    async def test_oauth_401_refreshes_once_and_registers_the_tools(
        self, monkeypatch: pytest.MonkeyPatch, fake_sessions: type[_FakeClientManager],
    ) -> None:
        from app.agents.mcp.models import OAuthTokens

        refresh = AsyncMock(return_value=OAuthTokens(access_token="new"))
        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", refresh)
        registry = ToolRegistry()
        context = _oauth_context()

        await MCPToolProvider().load_into(registry, context)

        assert fake_sessions.seen_tokens == ["Bearer old", "Bearer new"]
        # The rejected token goes along so a refresh another process already did isn't repeated.
        refresh.assert_awaited_once_with("inst-1", "user-1", context.config_service, stale_access_token="old")
        assert registry.has("mcp_jira_mcp_search")
        # The calls reuse the session discovery refreshed, so they carry the new token.
        assert registry.resolve_by_name("mcp_jira_mcp_search")._server.auth["oauthTokens"]["accessToken"] == "new"

    async def test_non_oauth_401_is_not_refreshed(
        self, monkeypatch: pytest.MonkeyPatch, fake_sessions: type[_FakeClientManager],
    ) -> None:
        refresh = AsyncMock()
        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", refresh)
        monkeypatch.setattr(_FakeClientManager, "fetch_tool_listing_in_session", AsyncMock(side_effect=_http_401()))
        context = _context_with_server()

        await MCPToolProvider().load_into(ToolRegistry(), context)

        refresh.assert_not_awaited()
        assert context.mcp_tool_load_failures[0]["reason"] == "unauthorized"

    async def test_a_failed_refresh_records_the_failure_without_retrying(
        self, monkeypatch: pytest.MonkeyPatch, fake_sessions: type[_FakeClientManager],
    ) -> None:
        from app.agents.mcp.token_refresh import MCPTokenRefreshError

        monkeypatch.setattr(
            mcp_session_module, "refresh_credential_record", AsyncMock(side_effect=MCPTokenRefreshError("no refresh token")),
        )
        context = _oauth_context()

        await MCPToolProvider().load_into(ToolRegistry(), context)

        assert fake_sessions.seen_tokens == ["Bearer old"]
        assert context.mcp_tool_load_failures[0]["reason"] == "auth_expired"


class TestOneConnectionPerTurn:
    async def test_discovery_and_calls_share_the_session(
        self, monkeypatch: pytest.MonkeyPatch, fake_sessions: type[_FakeClientManager],
    ) -> None:
        built: list[_FakeClientManager] = []

        def _build(config: Any, env: dict | None = None, headers: dict | None = None) -> _FakeClientManager:  # noqa: ANN401
            manager = _FakeClientManager(config, env=env, headers=headers)
            manager.call_tool_in_session = AsyncMock(return_value="ok")  # type: ignore[method-assign]
            built.append(manager)
            return manager

        monkeypatch.setattr(mcp_session_module, "MCPClientManager", _build)
        context = _context_with_server()
        registry = ToolRegistry()

        await MCPToolProvider().load_into(registry, context)
        await MCPSessionManager(context).call(registry.resolve_by_name("mcp_jira_mcp_search")._server, "search", {})

        assert len(built) == 1
        built[0].call_tool_in_session.assert_awaited_once()
        assert built[0].call_tool_in_session.await_args.args == ("search", {})


async def _discover_named(config: Any, credentials: dict, timeout_seconds: float = 10.0, namespace: str | None = None) -> list[MCPToolInfo]:  # noqa: ANN401
    """Like the real discovery: names come from the namespace the loader passes."""
    from app.agents.mcp.naming import build_namespaced_tool_name, namespace_key

    key = namespace or namespace_key(config.type_id, config.name)
    return [MCPToolInfo(name="search", namespaced_name=build_namespaced_tool_name(key, "search"), input_schema={})]


def _two_servers(first: dict[str, Any], second: dict[str, Any]) -> Any:  # noqa: ANN401
    servers, configs = [], {}
    for spec in (first, second):
        instance = {**_instance(spec["id"], type_id=spec.get("type")), "name": spec["name"], "createdAt": spec["created"]}
        servers.append({"instanceId": spec["id"], "name": spec["name"], "displayName": spec["name"], "typeId": spec.get("type")})
        configs[spec["id"]] = {"instance": instance, "auth": {}, "ownerId": "user-1"}
    return make_context(mcp_servers=servers, mcp_server_configs=configs)


class TestUniqueNamesPerRequest:
    async def test_same_named_custom_servers_both_load_and_the_older_keeps_its_names(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.agents.mcp.naming import instance_tag

        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_named))
        registry = ToolRegistry()
        context = _two_servers(
            {"id": "new", "name": "my-server", "created": 20},
            {"id": "old", "name": "My Server", "created": 10},
        )

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_my_server_search")
        tagged = f"mcp_my_server_{instance_tag('new')}_search"
        assert registry.has(tagged)
        assert registry.resolve_by_name("mcp_my_server_search")._server.instance_id == "old"
        groups = {g.name for g in registry.toolsets()}
        assert {"mcp_my_server", f"mcp_my_server_{instance_tag('new')}"} <= groups
        assert context.mcp_tool_load_failures == []

    async def test_a_single_instance_keeps_todays_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_named))
        registry = ToolRegistry()

        await MCPToolProvider().load_into(registry, _context_with_server())

        assert registry.names() == ["mcp_jira_mcp_search"]

    async def test_two_servers_named_alike_but_of_different_types_get_distinct_groups(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_named))
        registry = ToolRegistry()
        context = _two_servers(
            {"id": "a", "name": "Prod", "type": "github", "created": 1},
            {"id": "b", "name": "Prod", "type": "linear", "created": 2},
        )

        await MCPToolProvider().load_into(registry, context)

        assert {"mcp_github_search", "mcp_linear_search"} <= set(registry.names())
        prod_groups = [g for g in registry.toolsets() if g.name.startswith("mcp_prod")]
        assert len(prod_groups) == 2
        assert context.tool_state["mcp_names"]["a"]["group"] == "mcp_prod"

    async def test_attached_tools_match_by_raw_name_whatever_the_saved_full_name(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_named))
        registry = ToolRegistry()
        context = _context_with_server(attached_tools=[{"name": "search", "fullName": "JiraMCP.search"}])

        await MCPToolProvider().load_into(registry, context)

        assert registry.names() == ["mcp_jira_mcp_search"]


class TestServerInstructions:
    async def test_a_loaded_servers_instructions_are_recorded(
        self, monkeypatch: pytest.MonkeyPatch, fake_sessions: type[_FakeClientManager],
    ) -> None:
        monkeypatch.setattr(_FakeClientManager, "rejected", "Bearer nobody-sends-this")
        monkeypatch.setattr(_FakeClientManager, "server_instructions", "Search before creating issues.")
        context = _context_with_server()

        await MCPToolProvider().load_into(ToolRegistry(), context)

        assert context.mcp_server_instructions == [
            {"instanceId": "inst-1", "name": "JiraMCP", "instructions": "Search before creating issues."},
        ]

    async def test_a_server_without_instructions_adds_none(
        self, monkeypatch: pytest.MonkeyPatch, fake_sessions: type[_FakeClientManager],
    ) -> None:
        monkeypatch.setattr(_FakeClientManager, "rejected", "Bearer nobody-sends-this")
        context = _context_with_server()

        await MCPToolProvider().load_into(ToolRegistry(), context)

        assert context.mcp_server_instructions == []

    async def test_a_server_that_failed_to_load_adds_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_discover_fails))
        context = _context_with_server()

        await MCPToolProvider().load_into(ToolRegistry(), context)

        assert context.mcp_server_instructions == []
