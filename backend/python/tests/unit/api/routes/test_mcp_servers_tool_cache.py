"""The server listings and the Discover button with the tool cache."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp.client import ToolListing
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import get_instance_tools, get_my_mcp_servers
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

_CREDENTIALS = "/services/mcp/credentials/inst-1/user-1"


def _store(**credential: Any) -> FakeConfigService:  # noqa: ANN401
    return FakeConfigService({
        "/services/mcp/instances/inst-1": {
            "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-1", "name": "Jira", "typeId": None,
            "transport": "streamable_http", "url": "https://mcp.example.com/mcp", "authMode": "api_token",
            "isCustom": True, "createdAt": 1, "updatedAt": 1,
        },
        _CREDENTIALS: {"isAuthenticated": True, "credentials": {"apiToken": "t"}, "connectedAt": 5, **credential},
    })


def _listing(*names: str) -> AsyncMock:
    return AsyncMock(return_value=ToolListing(
        tools=[{"name": name, "description": f"{name} things", "inputSchema": {"type": "object"}} for name in names],
        instructions="Prefer search.",
    ))


@pytest.fixture(autouse=True)
def _not_admin() -> Any:  # noqa: ANN401
    with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)):
        yield


async def _listed(store: FakeConfigService, discover: AsyncMock) -> dict[str, Any]:
    with patch("app.agents.mcp.discovery.discover_tool_listing", new=discover):
        result = await get_my_mcp_servers(route_request(store, user_id="user-1"), include_tools=True)
    (entry,) = result["instances"]
    return entry


class TestTheListingsAnswerFromTheCache:
    async def test_a_live_listing_is_remembered_and_the_next_one_connects_to_nothing(self) -> None:
        store = _store()
        first = await _listed(store, _listing("search"))
        assert first["toolsCachedAt"] is None

        offline = AsyncMock(side_effect=AssertionError("the cached listing must not connect"))
        second = await _listed(store, offline)

        assert [t["name"] for t in second["tools"]] == ["search"]
        assert second["tools"][0]["namespacedName"] == first["tools"][0]["namespacedName"]
        assert isinstance(second["toolsCachedAt"], int) and second["toolsCachedAt"] > 0
        assert second["toolsError"] is None
        offline.assert_not_awaited()

    async def test_a_reconnected_sign_in_lists_live_again(self) -> None:
        store = _store()
        await _listed(store, _listing("search"))
        store.data[_CREDENTIALS] = {**store.data[_CREDENTIALS], "connectedAt": 6}

        entry = await _listed(store, _listing("search", "create_issue"))

        assert [t["name"] for t in entry["tools"]] == ["search", "create_issue"]
        assert entry["toolsCachedAt"] is None

    async def test_the_discover_button_is_always_live_and_replaces_the_cached_tools(self) -> None:
        store = _store()
        await _listed(store, _listing("search"))

        with patch("app.agents.mcp.discovery.discover_tool_listing", new=_listing("search", "create_issue")), \
             patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=store.data["/services/mcp/instances/inst-1"])):
            result = await get_instance_tools(route_request(store, user_id="user-1"), "inst-1")
        assert [t["name"] for t in result["tools"]] == ["search", "create_issue"]

        entry = await _listed(store, AsyncMock(side_effect=AssertionError("cached")))
        assert [t["name"] for t in entry["tools"]] == ["search", "create_issue"]

    async def test_opening_a_panel_reads_the_cached_tools_and_says_when_they_were_found(self) -> None:
        store = _store()
        await _listed(store, _listing("search"))
        instance = store.data["/services/mcp/instances/inst-1"]

        with patch("app.agents.mcp.discovery.discover_tool_listing", new=AsyncMock(side_effect=AssertionError("cached"))),              patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=instance)):
            result = await get_instance_tools(route_request(store, user_id="user-1"), "inst-1", cached=True)

        assert [t["name"] for t in result["tools"]] == ["search"]
        assert result["tools"][0]["kind"] == "write"
        assert isinstance(result["syncedAt"], int) and result["syncedAt"] > 0

    async def test_with_nothing_cached_a_panel_lists_live(self) -> None:
        store = _store()
        discover = _listing("search")
        with patch("app.agents.mcp.discovery.discover_tool_listing", new=discover),              patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=store.data["/services/mcp/instances/inst-1"])):
            result = await get_instance_tools(route_request(store, user_id="user-1"), "inst-1", cached=True)

        assert [t["name"] for t in result["tools"]] == ["search"]
        assert discover.await_count == 1
        assert isinstance(result["syncedAt"], int)

    async def test_a_failed_discovery_leaves_the_cached_tools(self) -> None:
        from app.agents.mcp.errors import MCPRequestNotSentError

        store = _store()
        await _listed(store, _listing("search"))
        cached = {k: v for k, v in store.data.items() if "tool-catalogs" in k}

        with patch("app.agents.mcp.discovery.discover_tool_listing", new=AsyncMock(side_effect=MCPRequestNotSentError("down"))), \
             patch.object(mcp_servers, "_get_org_instance", new=AsyncMock(return_value=store.data["/services/mcp/instances/inst-1"])):
            with pytest.raises(HTTPException) as exc:
                await get_instance_tools(route_request(store, user_id="user-1"), "inst-1")

        assert exc.value.status_code == 502
        assert {k: v for k, v in store.data.items() if "tool-catalogs" in k} == cached

    async def test_the_kill_switch_lists_live_every_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_TOOL_CACHE_TTL_SECONDS", "0")
        store = _store()
        discover = _listing("search")
        await _listed(store, discover)
        await _listed(store, discover)

        assert discover.await_count == 2
        assert not [k for k in store.data if "tool-catalogs" in k]
