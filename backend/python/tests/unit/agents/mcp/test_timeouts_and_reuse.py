"""Per-instance MCP timeouts, one discovery budget everywhere, one connection per chat turn,
and the assistant's MCP credentials read once."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT
from pydantic import ValidationError

from app.agents.agent_loop import mcp_session as mcp_session_module
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.mcp import service as mcp_service
from app.agents.mcp.client import (
    DEFAULT_CALL_TIMEOUT_SECONDS,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    LIST_TOOLS_TIMEOUT_SECONDS,
    MCPClientManager,
    ToolListing,
    _Transport,
    call_timeout,
    connect_timeout,
    discovery_timeout,
)
from app.agents.mcp.errors import MCPConnectionError
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerConfig,
    MCPServerInstanceConfig,
    MCPTransport,
    OAuthTokens,
)
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import create_instance, update_instance
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request


def _config(**overrides: Any) -> MCPServerConfig:  # noqa: ANN401
    fields: dict[str, Any] = {
        "id": "inst-1", "org_id": "org-1", "created_by": "u1", "name": "Server",
        "transport": MCPTransport.STDIO, "auth_mode": MCPAuthMode.NONE, "created_at": 0, "updated_at": 0,
    }
    fields.update(overrides)
    return MCPServerConfig(**fields)


def _payload(**overrides: Any) -> MCPServerInstanceConfig:  # noqa: ANN401
    fields: dict[str, Any] = {
        "name": "srv", "transport": MCPTransport.STREAMABLE_HTTP, "auth_mode": MCPAuthMode.NONE,
        "url": "https://mcp.example.com/mcp",
    }
    fields.update(overrides)
    return MCPServerInstanceConfig(**fields)


class TestInstanceTimeouts:
    @pytest.mark.parametrize("field,value", [("connect_timeout_seconds", 0), ("connect_timeout_seconds", 46),
                                             ("call_timeout_seconds", 0.5), ("call_timeout_seconds", 601)])
    def test_out_of_range_values_are_rejected(self, field: str, value: float) -> None:
        with pytest.raises(ValidationError):
            _payload(**{field: value})

    async def test_saved_on_the_record_and_read_back_into_the_config(self) -> None:
        store = FakeConfigService()
        with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=True)):
            record = await create_instance(
                route_request(store), _payload(connect_timeout_seconds=5, call_timeout_seconds=300),
            )
        assert record["connectTimeoutSeconds"] == 5
        assert record["callTimeoutSeconds"] == 300
        config = mcp_service.instance_config_from_dict(record)
        assert (connect_timeout(config), call_timeout(config)) == (5, 300)

    async def test_an_update_without_them_goes_back_to_the_defaults(self) -> None:
        store = FakeConfigService()
        with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=True)):
            record = await create_instance(route_request(store), _payload(call_timeout_seconds=300))
            updated = await update_instance(route_request(store), record["_id"], _payload())
        assert updated["callTimeoutSeconds"] is None
        assert call_timeout(mcp_service.instance_config_from_dict(updated)) == DEFAULT_CALL_TIMEOUT_SECONDS

    def test_defaults_and_the_discovery_budget(self) -> None:
        config = _config()
        assert connect_timeout(config) == DEFAULT_CONNECT_TIMEOUT_SECONDS
        assert call_timeout(config) == DEFAULT_CALL_TIMEOUT_SECONDS
        assert discovery_timeout(config) == DEFAULT_CONNECT_TIMEOUT_SECONDS + LIST_TOOLS_TIMEOUT_SECONDS
        assert discovery_timeout(_config(connect_timeout_seconds=40)) == 40 + LIST_TOOLS_TIMEOUT_SECONDS


class TestListingsDoNotWaitOnASlowServer:
    async def test_a_slow_server_reports_a_timeout_and_the_rest_still_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import time

        from app.agents.mcp.models import MCPToolInfo
        from app.api.routes.mcp_servers import get_my_mcp_servers

        def _instance(instance_id: str, type_id: str) -> dict[str, Any]:
            return {
                "_id": instance_id, "orgId": "org-1", "createdBy": "admin-1", "name": type_id, "typeId": type_id,
                "transport": "streamable_http", "authMode": "none", "url": "https://mcp.example.com",
                "createdAt": 1, "updatedAt": 1,
            }

        store = FakeConfigService({
            "/services/mcp/instances/fast": _instance("fast", "fast"),
            "/services/mcp/instances/slow": _instance("slow", "slow"),
        })

        async def _discover(instance: dict, auth: dict, owner: str, cfg: object, **kwargs: Any) -> tuple:  # noqa: ANN401
            if instance["_id"] == "slow":
                await asyncio.sleep(5)
            return [MCPToolInfo(name="t", namespaced_name=f"mcp_{instance['_id']}_t")], auth

        monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_BUDGET_SECONDS", 0.05)
        started = time.monotonic()
        with patch.object(mcp_servers, "discover_tools_for_owner", new=_discover),              patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)):
            listed = await get_my_mcp_servers(route_request(store, user_id="u1"), include_tools=True)

        assert time.monotonic() - started < 2
        by_id = {i["_id"]: i for i in listed["instances"]}
        assert by_id["fast"]["tools"] and not by_id["fast"]["toolsTimedOut"]
        assert by_id["slow"]["toolsTimedOut"] and "longer than" in by_id["slow"]["toolsError"]
        assert by_id["slow"]["toolsErrorCode"] == "timeout"

    def test_connect_timeout_tops_out_at_45_seconds(self) -> None:
        assert _payload(connect_timeout_seconds=45).connect_timeout_seconds == 45


def _listing_store(count: int) -> FakeConfigService:
    return FakeConfigService({
        f"/services/mcp/instances/s{n}": {
            "_id": f"s{n}", "orgId": "org-1", "createdBy": "admin-1", "name": f"server {n}", "typeId": f"t{n}",
            "transport": "streamable_http", "authMode": "none", "url": "https://mcp.example.com",
            "createdAt": 1, "updatedAt": 1,
        }
        for n in range(count)
    })


async def _list_my_servers(store: FakeConfigService) -> list[dict[str, Any]]:
    with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)):
        listed = await mcp_servers.get_my_mcp_servers(route_request(store, user_id="u1"), include_tools=True)
    return listed["instances"]


async def _list_agent_servers(store: FakeConfigService) -> list[dict[str, Any]]:
    with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)), \
         patch("app.api.routes.toolsets._resolve_agent_with_permission", new=AsyncMock(return_value={"can_edit": True})):
        listed = await mcp_servers.get_agent_mcp_servers(route_request(store, user_id="u1"), "agent-1", include_tools=True)
    return listed["instances"]


class TestAListingDiscoversABoundedNumberOfServers:
    """SCALE-2: one page load doesn't open a connection (or start a process) per server at once,
    and doesn't wait longer than one deadline however many servers there are."""

    @pytest.mark.parametrize("listing", [_list_my_servers, _list_agent_servers])
    async def test_no_more_than_the_limit_at_once(self, listing: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
        from app.agents.mcp.models import MCPToolInfo

        active = 0
        most = 0

        async def _discover(instance: dict, auth: dict, owner: str, cfg: object, **kwargs: Any) -> tuple:  # noqa: ANN401
            nonlocal active, most
            active += 1
            most = max(most, active)
            await asyncio.sleep(0.02)
            active -= 1
            return [MCPToolInfo(name="t", namespaced_name=f"mcp_{instance['_id']}_t")], auth

        monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_CONCURRENCY", 3)
        with patch.object(mcp_servers, "discover_tools_for_owner", new=_discover):
            instances = await listing(_listing_store(10))

        assert most == 3
        assert all(entry["tools"] for entry in instances)

    async def test_servers_whose_turn_comes_after_the_deadline_are_reported_as_timed_out(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import time

        from app.agents.mcp.models import MCPToolInfo

        discovered: list[str] = []

        async def _discover(instance: dict, auth: dict, owner: str, cfg: object, **kwargs: Any) -> tuple:  # noqa: ANN401
            discovered.append(instance["_id"])
            await asyncio.sleep(0.2)
            return [MCPToolInfo(name="t", namespaced_name=f"mcp_{instance['_id']}_t")], auth

        monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_CONCURRENCY", 2)
        monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_DEADLINE_SECONDS", 0.3)
        monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_MIN_SECONDS", 0.05)
        started = time.monotonic()
        with patch.object(mcp_servers, "discover_tools_for_owner", new=_discover):
            instances = await _list_my_servers(_listing_store(10))

        assert time.monotonic() - started < 1.5
        listed = [entry for entry in instances if entry["tools"]]
        late = [entry for entry in instances if entry["toolsTimedOut"]]
        assert len(listed) == 2
        assert len(late) == 8
        assert all(entry["toolsErrorCode"] == "timeout" for entry in late)
        assert all("limited to 0.3 seconds" in entry["toolsError"] for entry in late)
        # Servers whose turn came with less than the minimum left were never contacted.
        assert len(discovered) <= 4

    async def test_a_server_whose_turn_leaves_too_little_time_is_not_contacted(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        discover = AsyncMock()
        # The deadline is shorter than the minimum, so no server is worth starting.
        monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_DEADLINE_SECONDS", 0.5)
        monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_MIN_SECONDS", 1.0)
        with patch.object(mcp_servers, "discover_tools_for_owner", new=discover):
            instances = await _list_my_servers(_listing_store(2))

        discover.assert_not_awaited()
        assert all(entry["toolsErrorCode"] == "timeout" for entry in instances)

    async def test_a_server_over_its_own_budget_is_named_as_slow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _discover(instance: dict, auth: dict, owner: str, cfg: object, **kwargs: Any) -> tuple:  # noqa: ANN401
            await asyncio.sleep(5)
            return [], auth

        monkeypatch.setattr(mcp_servers, "LISTING_DISCOVERY_BUDGET_SECONDS", 0.05)
        with patch.object(mcp_servers, "discover_tools_for_owner", new=_discover):
            (entry,) = await _list_my_servers(_listing_store(1))

        assert "took longer than 0.05 seconds" in entry["toolsError"]

    async def test_without_tools_nothing_is_discovered(self) -> None:
        discover = AsyncMock()
        with patch.object(mcp_servers, "discover_tools_for_owner", new=discover), \
             patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)):
            listed = await mcp_servers.get_my_mcp_servers(route_request(_listing_store(3), user_id="u1"), include_tools=False)

        discover.assert_not_awaited()
        assert all(not entry["toolsTimedOut"] for entry in listed["instances"])


class _SlowClient:
    """The SDK client as far as timeouts go: a call gives up after its read timeout, as the
    SDK's own clock does; `hang_on_connect` never finishes the handshake."""

    hang_on_connect = False

    def __init__(self, _streams: object, **_kwargs: object) -> None:
        self.session = MagicMock(initialize_result=MagicMock(instructions=None))
        self.instructions = None

    async def __aenter__(self) -> "_SlowClient":
        if self.hang_on_connect:
            await asyncio.sleep(3600)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def call_tool(
        self, _name: str, _arguments: dict, read_timeout_seconds: float | None = None, progress_callback: object = None,
    ) -> None:
        await asyncio.sleep(read_timeout_seconds or 3600)
        raise MCPError(REQUEST_TIMEOUT, "Request 'tools/call' timed out")


class TestClientUsesTheInstanceTimeouts:
    async def test_a_call_times_out_at_the_instance_call_timeout(self) -> None:
        with patch("app.agents.mcp.client.build_transport", return_value=_Transport("streams")), \
             patch("app.agents.mcp.client.Client", _SlowClient):
            manager = MCPClientManager(_config(call_timeout_seconds=0.05))
            await manager.open()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(manager.call_tool_in_session("slow", {}), 5)
            await manager.aclose()

    async def test_opening_times_out_at_the_instance_connect_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_SlowClient, "hang_on_connect", True)
        with patch("app.agents.mcp.client.build_transport", return_value=_Transport("streams")), \
             patch("app.agents.mcp.client.Client", _SlowClient):
            with pytest.raises(MCPConnectionError, match="Timed out connecting"):
                await asyncio.wait_for(MCPClientManager(_config(connect_timeout_seconds=0.05)).open(), 5)

    async def test_listing_on_a_closed_session_is_refused(self) -> None:
        with pytest.raises(MCPConnectionError, match="is not open"):
            await MCPClientManager(_config()).list_tools_in_session()


def _server(auth_mode: str = "none") -> ResolvedMCPServer:
    instance = {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "u1", "name": "Jira", "typeId": "jira",
        "transport": "sse", "authMode": auth_mode, "createdAt": 0, "updatedAt": 0,
    }
    auth = {"isAuthenticated": True, "oauthTokens": {"accessToken": "old"}} if auth_mode == "oauth" else {}
    return ResolvedMCPServer(
        instance_id="inst-1", name="Jira", display_name="Jira", instance=instance, auth=auth,
        owner_id="u1", attached_tools=None,
    )


def _unauthorized() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    return httpx.HTTPStatusError("rejected", request=request, response=httpx.Response(401, request=request))


class _Manager:
    built: list[_Manager] = []

    def __init__(self, config: MCPServerConfig, env: dict | None = None, headers: dict | None = None) -> None:
        self.config = config
        self.headers = headers or {}
        self.listings = 0
        self.calls: list[str] = []
        _Manager.built.append(self)

    is_open = True

    async def open(self) -> _Manager:
        return self

    def update_headers(self, headers: dict[str, str]) -> None:
        self.headers.update(headers)

    tools_changed = False

    async def list_tools_in_session(self) -> list[dict[str, Any]]:
        return (await self.fetch_tool_listing_in_session()).tools

    async def fetch_tool_listing_in_session(self) -> ToolListing:
        self.listings += 1
        if self.headers.get("Authorization") == "Bearer old":
            raise _unauthorized()
        return ToolListing(tools=[{"name": "search", "description": "Search", "inputSchema": {}}])

    async def call_tool_in_session(self, tool_name: str, arguments: dict, **_kwargs: object) -> str:
        self.calls.append(tool_name)
        return "ok"

    async def aclose(self) -> None:
        pass


@pytest.fixture
def managers(monkeypatch: pytest.MonkeyPatch) -> type[_Manager]:
    monkeypatch.setattr(_Manager, "built", [])
    monkeypatch.setattr(mcp_session_module, "MCPClientManager", _Manager)
    return _Manager


class TestSessionDiscovery:
    async def test_discovery_and_calls_use_one_connection(self, managers: type[_Manager]) -> None:
        sessions = MCPSessionManager(make_context())
        server = _server()

        tools = await sessions.discover(server, "jira")
        await sessions.call(server, "search", {})

        assert [t.namespaced_name for t in tools] == ["mcp_jira_search"]
        (only,) = managers.built
        assert only.listings == 1 and only.calls == ["search"]

    async def test_an_expired_token_is_refreshed_once_and_the_session_carries_the_new_one(
        self, managers: type[_Manager], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refresh = AsyncMock(return_value=OAuthTokens(access_token="new"))
        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", refresh)
        sessions = MCPSessionManager(make_context())
        server = _server("oauth")

        tools = await sessions.discover(server, "jira")
        await sessions.call(server, "search", {})

        assert [t.name for t in tools] == ["search"]
        refresh.assert_awaited_once()
        (only,) = managers.built
        assert only.headers["Authorization"] == "Bearer new"
        assert (only.listings, only.calls) == (2, ["search"])

    async def test_a_non_oauth_401_is_not_refreshed(
        self, managers: type[_Manager], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refresh = AsyncMock()
        monkeypatch.setattr(mcp_session_module, "refresh_credential_record", refresh)
        monkeypatch.setattr(_Manager, "fetch_tool_listing_in_session", AsyncMock(side_effect=_unauthorized()))

        with pytest.raises(httpx.HTTPStatusError):
            await MCPSessionManager(make_context()).discover(_server(), "jira")
        refresh.assert_not_awaited()


class TestAssistantPrefetch:
    async def test_every_authenticated_server_is_recorded_for_the_chat(self) -> None:
        instances = [
            {"_id": "a", "name": "A", "authMode": "api_token"},
            {"_id": "b", "name": "B", "authMode": "api_token"},
        ]
        auths = {"a": {"isAuthenticated": True, "apiToken": "t"}, "b": None}
        resolved: dict[str, dict[str, Any]] = {}

        async def _auth(instance: dict, owner_id: str, config_service: object) -> dict | None:
            return auths[instance["_id"]]

        with patch.object(mcp_service, "resolve_effective_user_auth", new=_auth):
            servers = await mcp_service.get_authenticated_mcp_servers(
                "u1", MagicMock(), instances, resolved=resolved,
            )

        assert [s["instanceId"] for s in servers] == ["a"]
        assert resolved == {"a": {"instance": instances[0], "auth": auths["a"]}}
