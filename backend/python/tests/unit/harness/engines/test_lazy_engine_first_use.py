"""R26-LAZY stage (d), ADR-OWD-07 D-LAZY-4: an engine reaches an MCP server's tools through the gateway at first use.

The engine's list never carries an MCP schema: the gateway's ``search_tools`` stands for them whatever the grant's
size. A search that names a server lists it (once), builds what it found and answers their schemas; a direct call of
a deferred tool is built before the gateway resolves it, and one the engine's profile does not allow is never
granted, built or not.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_access import MCPAccessResolver
from app.agents.agent_loop.mcp_lazy import LazyMcpServers
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp.models import MCPToolInfo
from app.harness.engines.durable import engine_disclosure, engine_lazy_port
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.tools.durable_port import RegistryToolPort
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    import pytest

CTX = ToolContext("run-1", "org-1", "t1.0", "att-1", "k")
TRACKER = "Release tracker: launch blockers and signoffs."


class _Store:
    async def get_config(self, key: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        return default

    async def set_config(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        return True


def _context(attached: list[dict[str, Any]] | None) -> Any:  # noqa: ANN401
    server: dict[str, Any] = {"instanceId": "inst-1", "name": "Tracker", "displayName": "Tracker"}
    if attached is not None:
        server["tools"] = attached
    instance = {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "user-1", "name": "Tracker", "typeId": None,
        "transport": "streamable_http", "url": "https://tracker.example.com/mcp", "authMode": "none",
        "description": TRACKER, "createdAt": 0, "updatedAt": 0,
    }
    return make_context(
        config_service=_Store(), mcp_servers=[server],
        mcp_server_configs={"inst-1": {"instance": instance, "auth": {}, "ownerId": "user-1"}},
    )


class _Card:
    tools: tuple[Any, ...] = ()


async def _servers(
    monkeypatch: pytest.MonkeyPatch, *, whole: bool, read_only: bool = True
) -> tuple[ToolRegistry, LazyMcpServers, list[str]]:
    context = _context(None if whole else [{"name": "search_items", "fullName": "mcp_tracker_search_items"}])
    (resolved,) = await MCPAccessResolver.resolve(context)
    listings: list[str] = []

    async def listed(self: MCPToolProvider, server: Any, ctx: Any, names: Any, **_: Any) -> Any:  # noqa: ANN401
        listings.append(server.instance_id)
        info = MCPToolInfo(
            name="search_items", namespaced_name=f"mcp_{names['namespace']}_search_items",
            description="Search the tracker's items.", input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            annotations={"readOnlyHint": read_only},
        )
        return [MCPToolAdapter(resolved, info, session_manager=None)], None  # type: ignore[arg-type]

    monkeypatch.setattr(MCPToolProvider, "list_server", listed)
    registry = ToolRegistry()
    lazy = LazyMcpServers(context)
    await lazy.register(registry, [] if whole else ["mcp@inst-1/search_items"], ["inst-1"] if whole else [])
    return registry, lazy, listings


async def test_ed1_mcp_waits_behind_the_gateway_search_under_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, lazy, listings = await _servers(monkeypatch, whole=False)
    disclosure = engine_disclosure(registry, {"jira__search_issues": None}, [], None, lazy=lazy)  # type: ignore[dict-item]
    assert disclosure is not None and disclosure.applies(())
    assert lazy.granted_names() <= disclosure.deferred
    assert listings == []


async def test_ed2_a_search_naming_a_server_granted_whole_lists_it_once_and_answers_its_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, lazy, listings = await _servers(monkeypatch, whole=True)
    disclosure = engine_disclosure(registry, {}, [], None, lazy=lazy)
    assert disclosure is not None
    answered = await disclosure.search({"query": "tracker launch blockers"}, lambda: registry.schemas(sorted(lazy.granted_names())))
    found = json.loads(answered.text)
    assert [f["name"] for f in found] == [next(iter(lazy.granted_names()))]
    assert found[0]["inputSchema"]["properties"] == {"q": {"type": "string"}}
    await disclosure.search({"query": "tracker"}, lambda: [])
    assert listings == ["inst-1"]


async def test_ed3_a_direct_call_of_a_deferred_tool_is_built_before_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, lazy, listings = await _servers(monkeypatch, whole=False)
    (name,) = lazy.granted_names()
    port = RegistryToolPort(registry, {}, **engine_lazy_port(registry, _deferred(name), lazy, lambda _e: True))
    assert name in port.names and port.describe(name) is None
    await port.execute(ToolCall(id="c1", name=name, arguments={"q": "x"}), CTX)
    assert listings == ["inst-1"]
    effect = port.describe(name)
    assert effect is not None and effect.tool_id == "mcp@inst-1/search_items"


async def test_ed4_a_tool_the_engines_profile_does_not_allow_is_not_granted_once_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, lazy, _ = await _servers(monkeypatch, whole=False, read_only=False)
    (name,) = lazy.granted_names()
    port = RegistryToolPort(registry, {}, **engine_lazy_port(registry, _deferred(name), lazy, lambda e: e.effect == "read"))
    refused = await port.execute(ToolCall(id="c1", name=name, arguments={}), CTX)
    assert refused.status == "error"
    assert port.describe(name) is None


def _deferred(name: str) -> Any:  # noqa: ANN401 - the loop assembler's Catalog, as the engine reads it
    from app.harness.assembly.tool_sources import SourcedTool
    from harness_core.provenance.rules import MCP_RULE

    card = _Card()
    card.tools = (SourcedTool(name, "mcp@inst-1/search_items", "mcp@inst-1/search_items", "external",
                              provenance=MCP_RULE, deferred=True),)
    return card


async def test_ed5_a_direct_call_by_the_servers_own_tool_name_runs_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """R27REST-FU-2: ``ph tools call search_items`` (a name the engine has from the source list, never searched) is a
    first use: the server is listed once and the tool runs as the name it stands for."""
    registry, lazy, listings = await _servers(monkeypatch, whole=False)
    (name,) = lazy.granted_names()
    port = RegistryToolPort(registry, {}, **engine_lazy_port(registry, _deferred(name), lazy, lambda _e: True))
    ran = await port.execute(ToolCall(id="c1", name="search_items", arguments={"q": "x"}), CTX)
    assert listings == ["inst-1"]
    assert "not granted" not in str(ran.content), ran.content
    assert port.describe(name) is not None


async def test_ed6_a_direct_call_lists_a_server_granted_whole_that_names_no_tool_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, lazy, listings = await _servers(monkeypatch, whole=True)
    port = RegistryToolPort(registry, {}, **engine_lazy_port(registry, _deferred("unused"), lazy, lambda _e: True))
    ran = await port.execute(ToolCall(id="c1", name="search_items", arguments={"q": "x"}), CTX)
    assert listings == ["inst-1"]
    assert "not granted" not in str(ran.content), ran.content


async def _echo_server(monkeypatch: pytest.MonkeyPatch) -> tuple[ToolRegistry, LazyMcpServers]:
    """DOCK-R26-FU-1's lane case: the run is granted the echo server's ``echo``, ``env_digest`` and ``where``; its
    listing admits only ``echo`` (the hub held the other two, one unapproved, one changed), and the registry holds
    other tools whose words a query about the held ones shares."""
    from app.agents.agent_loop.mcp_tool_loader import HELD_STATE_KEY
    from harness_core.tools.base import Tool, ToolOutput

    held = {"env_digest": "mcp_tool_unapproved", "where": "mcp_tool_changed"}
    context = _context([{"name": t, "fullName": f"mcp_tracker_{t}"} for t in ("echo", *held)])
    (resolved,) = await MCPAccessResolver.resolve(context)

    async def listed(self: MCPToolProvider, server: Any, ctx: Any, names: Any, **_: Any) -> Any:  # noqa: ANN401
        ctx.tool_state.setdefault(HELD_STATE_KEY, {})[server.instance_id] = held
        info = MCPToolInfo(
            name="echo", namespaced_name=f"mcp_{names['namespace']}_echo", description="Echo the text back.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}}, annotations={"readOnlyHint": True},
        )
        return [MCPToolAdapter(resolved, info, session_manager=None)], None  # type: ignore[arg-type]

    class _Other(Tool):
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        @property
        def path(self) -> str:
            return f"/other/{self._name}"

        @property
        def short_description(self) -> str:
            return f"Where the env digest of {self._name} is."

        @property
        def description(self) -> str:
            return self.short_description

        @property
        def parameters(self) -> list[Any]:
            return []

        async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
            return ToolOutput(success=True, data="")

    monkeypatch.setattr(MCPToolProvider, "list_server", listed)
    registry = ToolRegistry()
    for i in range(12):
        registry.register_tool(_Other(f"env_digest_where_{i}"))
    lazy = LazyMcpServers(context)
    await lazy.register(registry, [f"mcp@inst-1/{t}" for t in ("echo", *held)], [])
    return registry, lazy


async def test_ed7_held_and_ungranted_matches_never_displace_the_allowed_tool_and_held_ones_say_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, lazy = await _echo_server(monkeypatch)
    disclosure = engine_disclosure(registry, {}, [], None, lazy=lazy)
    assert disclosure is not None
    names = sorted(lazy.granted_names())
    for limit in (1, 8):
        answered = await disclosure.search(
            {"query": "echo env digest where", "limit": limit}, lambda: registry.schemas(sorted(lazy.granted_names()))
        )
        text = answered.text
        assert "No tool of this run matches" not in text, text
        found = json.loads(text.split("\n\n", 1)[0])
        assert [f["name"] for f in found] == [n for n in names if n.endswith("_echo")], text
        held = text.split("\n\n", 1)[1]
        assert "env_digest" in held and "isn't approved" in held, held
        # ``where`` ranks below ``echo``: a search of one stops at the first tool that can run.
        assert ("since an admin approved it" in held) == (limit > 1), held


async def test_ed8_a_search_that_finds_only_held_tools_says_why_none_can_be_used(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, lazy = await _echo_server(monkeypatch)
    disclosure = engine_disclosure(registry, {}, [], None, lazy=lazy)
    assert disclosure is not None
    answered = await disclosure.search({"query": "env_digest", "limit": 8}, lambda: registry.schemas(sorted(lazy.granted_names())))
    assert answered.text.startswith("No tool of this run matches 'env_digest'.")
    assert "isn't approved" in answered.text and "Try other words" not in answered.text, answered.text
