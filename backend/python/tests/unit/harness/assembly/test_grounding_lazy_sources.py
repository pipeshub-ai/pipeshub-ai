"""QAFINAL-FU-GROUNDLAZY: the grounding check searches connected MCP servers that are listed at first use.

Since R26-LAZY a run's MCP servers are lazy groups: none of their tools is built when the goal step runs its check,
so the check, which looked only at built tools, found no MCP search, and the engine lane's AC-E1 said "in your
knowledge" with e2esaas connected. These tests go through the real lazy path (``LazyMcpServers.register`` into a
real ``ToolRegistry``), never eager tools: the check picks a server's search from what is stored of it, builds only
that through ``prepare`` (one listing, pinned), checks the listing again, and calls it only if it runs unasked.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import tool_approvals
from app.agents.agent_loop.mcp_access import MCPAccessResolver
from app.agents.agent_loop.mcp_lazy import LazyMcpServers
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp import tool_cache
from app.agents.mcp.models import MCPToolInfo
from app.harness.assembly.grounding_gate import GroundingGate
from harness_core.logs import HarnessLog
from harness_core.tools.base import ToolOutput
from tests.unit.agents.adapter.conftest import make_context

from .test_answer_contract import _run

if TYPE_CHECKING:
    import pytest

LOG = HarnessLog(logging.getLogger("test.grounding_lazy"), {})
REQUEST = 'Prepare a risk report for "SAAS launch"'
SEARCH = "search_documents"
WRITE = "create_ticket"
SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
FOUND = json.dumps({"results": [{"title": "SAAS launch plan", "body": "go-live 1 December"}]})


class _Store:
    async def get_config(self, key: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        return default

    async def set_config(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        return True


def _tool(name: str, read_only: bool) -> dict[str, Any]:
    return {"name": name, "description": f"{name} on e2esaas", "input_schema": SCHEMA, "annotations": {"readOnlyHint": read_only}}


class _World:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, stored: list[dict[str, Any]] | None, live: list[dict[str, Any]],
                 attached: list[str] | None, failure: str | None = None, result: str = FOUND) -> None:
        self.monkeypatch = monkeypatch
        self.stored, self.live, self.attached, self.failure, self.result = stored, live, attached, failure, result
        self.listings: list[str] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def build(self) -> tuple[GroundingGate, LazyMcpServers]:
        server: dict[str, Any] = {"instanceId": "inst-1", "name": "e2esaas", "displayName": "e2esaas"}
        if self.attached is not None:
            server["tools"] = [{"name": t, "fullName": f"mcp_e2esaas_{t}"} for t in self.attached]
        instance = {
            "_id": "inst-1", "orgId": "org-1", "createdBy": "user-1", "name": "e2esaas", "typeId": None,
            "transport": "streamable_http", "url": "https://saas.example.com/mcp", "authMode": "none",
            "description": "The scripted SaaS.", "createdAt": 0, "updatedAt": 0,
        }
        context = make_context(
            config_service=_Store(), mcp_servers=[server],
            mcp_server_configs={"inst-1": {"instance": instance, "auth": {}, "ownerId": "user-1"}},
            has_ui_client=True,
        )
        (resolved,) = await MCPAccessResolver.resolve(context)
        world = self

        async def read(*_: Any, **__: Any) -> Any:  # noqa: ANN401
            if world.stored is None:
                return None
            catalog = tool_cache.CachedCatalog(tools=[tool_cache.CachedTool(**t) for t in world.stored])
            return SimpleNamespace(catalog=catalog)

        async def listed(_self: MCPToolProvider, srv: Any, ctx: Any, names: Any, **__: Any) -> Any:  # noqa: ANN401
            world.listings.append(srv.instance_id)
            adapters = []
            for t in world.live:
                info = MCPToolInfo(
                    name=t["name"], namespaced_name=f"mcp_{names['namespace']}_{t['name']}", description=t["description"],
                    input_schema=t["input_schema"], annotations=t["annotations"],
                )
                adapter = MCPToolAdapter(resolved, info, session_manager=None)  # type: ignore[arg-type]
                ctx.tool_state.setdefault(tool_approvals.TOOLS_BY_PATH, {})[adapter.path] = adapter
                adapters.append(adapter)
            if world.failure is not None:
                ctx.mcp_tool_load_failures.append({"instanceId": srv.instance_id, "reason": world.failure})
            return adapters, world.failure

        async def execute(adapter: MCPToolAdapter, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
            world.calls.append((adapter.tool_name, kwargs))
            return ToolOutput(success=True, data=world.result)

        self.monkeypatch.setattr(tool_cache, "read", read)
        self.monkeypatch.setattr(MCPToolProvider, "list_server", listed)
        self.monkeypatch.setattr(MCPToolAdapter, "execute", execute)
        registry = ToolRegistry()
        lazy = LazyMcpServers(context)
        granted = [f"mcp@inst-1/{t}" for t in self.attached or ()]
        await lazy.register(registry, granted, [] if self.attached is not None else ["inst-1"])
        # The goal step's offered names are the run's top level: lazy MCP tools are not built, so not resolvable.
        assert not any(registry.has(n) for n in lazy.granted_names())
        gate = GroundingGate(_run(REQUEST), context, None, LOG, registry=registry, offered=[], lazy=lazy)
        return gate, lazy


async def test_gl1_a_lazy_servers_stored_search_is_built_at_first_use_and_finds_the_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, stored=[_tool(SEARCH, True), _tool(WRITE, False)],
                   live=[_tool(SEARCH, True), _tool(WRITE, False)], attached=None)
    gate, lazy = await world.build()
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.question is None
    assert (checked.grounding.status, checked.grounding.places) == ("found", ("e2esaas",))
    assert world.calls == [(SEARCH, {"query": "SAAS launch"})]
    # One listing, pinned through the run's own first-use path (journalled with the next call's outcome).
    assert world.listings == ["inst-1"]
    assert set(lazy.pins["inst-1"]) == {SEARCH, WRITE}
    slots, _ = lazy.drain()
    assert "mcp.pins" in slots


async def test_gl2_found_nowhere_names_the_lazy_server_among_the_places_looked_in(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, stored=[_tool(SEARCH, True)], live=[_tool(SEARCH, True)], attached=[SEARCH],
                   result="No results found.")
    gate, _ = await world.build()
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.question is not None
    assert checked.grounding.places == ("e2esaas",)


async def test_gl3_a_server_with_no_stored_read_only_search_is_never_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, stored=[_tool(SEARCH, False), _tool(WRITE, False)],
                   live=[_tool(SEARCH, False), _tool(WRITE, False)], attached=[SEARCH, WRITE])
    gate, _ = await world.build()
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.grounding.places == ()
    assert world.listings == [] and world.calls == []


async def test_gl4_a_search_whose_rule_asks_is_never_listed_or_called(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, stored=[_tool(SEARCH, True)], live=[_tool(SEARCH, True)], attached=[SEARCH])
    gate, _ = await world.build()

    async def rules(*_: Any, **__: Any) -> Any:  # noqa: ANN401
        return tool_approvals._InstanceRules(tool_approvals.ToolRules(tools={SEARCH: "ask"}))  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(tool_approvals, "_rules_for", rules)
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.grounding.places == ()
    assert world.listings == [] and world.calls == []


async def test_gl5_a_listing_that_serves_the_search_as_a_write_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored catalog can be stale: the listing is what the call is held to."""
    world = _World(monkeypatch, stored=[_tool(SEARCH, True)], live=[_tool(SEARCH, False)], attached=[SEARCH])
    gate, _ = await world.build()
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.grounding.places == ()
    assert world.listings == ["inst-1"] and world.calls == []


async def test_gl6_a_search_refused_at_first_use_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """An approvals-required edition refuses an unapproved tool at its listing: no search runs."""
    world = _World(monkeypatch, stored=[_tool(SEARCH, True)], live=[], attached=[SEARCH], failure="mcp_tool_unapproved")
    gate, _ = await world.build()
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.grounding.places == ()
    assert world.calls == []


async def test_gl7_a_server_granted_whole_with_nothing_stored_is_listed_once_to_learn_its_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, stored=None, live=[_tool(SEARCH, True), _tool(WRITE, False)], attached=None)
    gate, _ = await world.build()
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.grounding.status == "found"
    assert world.listings == ["inst-1"]
    assert world.calls == [(SEARCH, {"query": "SAAS launch"})]
