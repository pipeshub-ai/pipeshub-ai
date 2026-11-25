"""R26-LAZY stage (a), ADR-OWD-07 D-LAZY-1 and D-LAZY-7: an attached MCP server is known from stored metadata alone.

The source list (the name and summary the chat model and the engine read, the job's ``JobTask.sources``) is built
from the instance, its catalog entry and this sign-in's stored tool catalog. Building it opens no session. A harness
listing stores what it found, so the next run knows the server's tools unlisted, and an unchanged listing is not
written again on every run.
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import mcp_tool_loader
from app.agents.agent_loop.connected_sources import known_source
from app.agents.agent_loop.mcp_access import MCPAccessResolver
from app.agents.agent_loop.mcp_known import known_servers, remember_listing
from app.agents.mcp import tool_cache
from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.models import MCPToolInfo
from harness_core.mcp_hub.manifest import ManifestReview, ToolDescriptor
from tests.unit.agents.adapter.conftest import make_context

ABOUT = "Release tracker: launch blockers and signoffs."


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.writes: list[str] = []

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, **_: Any) -> Any:  # noqa: ANN401
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, *, ttl_seconds: int | None = None, **_: Any) -> bool:  # noqa: ANN401
        self.values[key] = copy.deepcopy(value)
        self.writes.append(key)
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


def _context(store: _Store, *, attached: list[dict[str, Any]] | None = None) -> Any:  # noqa: ANN401
    server: dict[str, Any] = {"instanceId": "inst-1", "name": "Tracker", "displayName": "Tracker"}
    if attached is not None:
        server["tools"] = attached
    instance = {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "user-1", "name": "Tracker", "typeId": None,
        "transport": "streamable_http", "url": "https://tracker.example.com/mcp", "authMode": "none",
        "description": ABOUT, "createdAt": 0, "updatedAt": 0,
    }
    return make_context(
        config_service=store, mcp_servers=[server],
        mcp_server_configs={"inst-1": {"instance": instance, "auth": {}, "ownerId": "user-1"}},
    )


def _listed(*names: str) -> list[ToolDescriptor]:
    return [ToolDescriptor(name, f"{name} in the tracker", {"type": "object"}, hints={"readOnlyHint": True})
            for name in names]


@pytest.fixture(autouse=True)
def _no_session() -> Any:  # noqa: ANN401
    async def refuse(*_: Any, **__: Any) -> None:  # noqa: ANN401
        raise AssertionError("building the source list opened a session to the server")

    with patch.object(MCPClientManager, "open", refuse):
        yield


async def test_a_server_never_listed_is_named_with_its_own_description_and_no_tool_count() -> None:
    context = _context(_Store())
    (known,) = await known_servers(context, await MCPAccessResolver.resolve(context))
    assert (known.name, known.summary, known.tools) == ("Tracker", ABOUT, None)
    source = known_source(known.name, "mcp_tracker", known.summary, known.count(None))
    assert source.part() == {"name": "Tracker", "kind": "mcp", "summary": ABOUT}
    assert source.line() == f"Tracker — {ABOUT}"


async def test_a_stored_listing_gives_the_tools_narrowed_to_the_attachment_and_their_count() -> None:
    store = _Store()
    context = _context(store, attached=[{"name": "search_items", "fullName": "mcp_tracker_search_items"}])
    (server,) = await MCPAccessResolver.resolve(context)
    await remember_listing(context, server, _listed("search_items", "create_item"), "Use search_items first.")
    (known,) = await known_servers(context, [server])
    assert [t.name for t in known.tools or ()] == ["search_items"]
    assert known.tools[0].annotations == {"readOnlyHint": True}  # type: ignore[index]
    assert known.count(1) == 1 and known.instructions == "Use search_items first."
    assert known_source(known.name, "g", known.summary, known.count(1)).part()["tools"] == 1


async def test_an_unchanged_listing_is_not_written_again() -> None:
    store = _Store()
    context = _context(store)
    (server,) = await MCPAccessResolver.resolve(context)
    await remember_listing(context, server, _listed("search_items"), None)
    written = len(store.writes)
    await remember_listing(context, server, _listed("search_items"), None)
    assert len(store.writes) == written
    await remember_listing(context, server, _listed("search_items", "get_item"), None)
    assert len(store.writes) > written


async def test_a_harness_listing_stores_what_it_found_for_the_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store()
    context = _context(store)
    raw = {t.name: t for t in _listed("search_items", "get_item")}
    infos = [MCPToolInfo(name=n, namespaced_name=f"mcp_tracker_{n}", description=t.description, input_schema={})
             for n, t in raw.items()]

    async def listing(*_: Any, **__: Any) -> tuple[list[MCPToolInfo], dict[str, ToolDescriptor]]:  # noqa: ANN401
        return infos, raw

    async def admit(_config: Any, tools: list[MCPToolInfo], **_: Any) -> tuple[list[MCPToolInfo], ManifestReview]:  # noqa: ANN401
        return tools, ManifestReview(admitted=(), disabled=(), removed=())

    monkeypatch.setattr(mcp_tool_loader, "discover_listing_for", listing)
    monkeypatch.setattr(mcp_tool_loader, "admit_tools", admit)
    monkeypatch.setattr(mcp_tool_loader, "hub_enabled", lambda **_: True)
    await mcp_tool_loader.MCPToolProvider().load_into(ToolRegistry(), context)
    assert tool_cache.enabled()
    (known,) = await known_servers(context, await MCPAccessResolver.resolve(context))
    assert sorted(t.name for t in known.tools or ()) == ["get_item", "search_items"]
