"""MCP tool-schema cache and single-session discovery (P0.15)."""

from __future__ import annotations

import logging
from typing import Any

import fakeredis.aioredis
import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import mcp_session as mcp_session_module
from app.agents.agent_loop.mcp_access import MCPAccessResolver
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.services.cache.mcp_tool_schema_cache import MCPToolSchemaCache
from tests.unit.agents.adapter.conftest import make_context


class _FakeClientManager:
    instances: list["_FakeClientManager"] = []

    def __init__(self, config: Any, env: dict | None = None, headers: dict | None = None) -> None:
        self.config = config
        self.headers = headers or {}
        self.opens = 0
        self.list_calls = 0
        self.tool_calls: list[str] = []
        _FakeClientManager.instances.append(self)

    async def open(self) -> "_FakeClientManager":
        self.opens += 1
        return self

    async def list_tools_in_session(self) -> list[dict]:
        self.list_calls += 1
        return [{"name": "search", "description": "Search", "inputSchema": {"type": "object"}}]

    async def call_tool_in_session(self, tool_name: str, arguments: dict) -> Any:
        self.tool_calls.append(tool_name)
        return {"content": [{"type": "text", "text": "ok"}]}

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _fake_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClientManager.instances = []
    monkeypatch.setattr(mcp_session_module, "MCPClientManager", _FakeClientManager)


@pytest.fixture
def cache() -> MCPToolSchemaCache:
    return MCPToolSchemaCache(logging.getLogger("test"), fakeredis.aioredis.FakeRedis(decode_responses=True))


def _instance(auth_mode: str = "none", *, updated_at: int = 0, use_admin_auth: bool = False) -> dict[str, Any]:
    return {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-1",
        "name": "JiraMCP", "typeId": "jira_mcp", "transport": "sse", "authMode": auth_mode,
        "useAdminAuth": use_admin_auth, "createdAt": 0, "updatedAt": updated_at,
    }


def _context(instance: dict[str, Any], *, owner: str = "user-1", auth: dict | None = None) -> Any:
    return make_context(
        user_id=owner,
        mcp_servers=[{"instanceId": "inst-1", "name": "JiraMCP", "displayName": "Jira", "typeId": "jira_mcp"}],
        mcp_server_configs={"inst-1": {"instance": instance, "auth": auth or {}, "ownerId": owner}},
    )


def _list_calls() -> int:
    return sum(m.list_calls for m in _FakeClientManager.instances)


def _opens() -> int:
    return sum(m.opens for m in _FakeClientManager.instances)


async def _load(cache: MCPToolSchemaCache, context: Any) -> ToolRegistry:
    registry = ToolRegistry()
    await MCPToolProvider(cache).load_into(registry, context)
    assert registry.has("mcp_jira_mcp_search")
    return registry


async def test_second_request_does_not_call_list_tools_or_connect(cache: MCPToolSchemaCache) -> None:
    await _load(cache, _context(_instance()))
    assert _list_calls() == 1

    registry = await _load(cache, _context(_instance()))

    assert _list_calls() == 1
    assert _opens() == 1
    schema = registry.resolve_by_name("mcp_jira_mcp_search").to_schema()
    assert schema.input_schema.get("type") == "object"


async def test_discovery_and_calls_share_one_session(cache: MCPToolSchemaCache) -> None:
    context = _context(_instance())
    registry = await _load(cache, context)

    await registry.resolve_by_name("mcp_jira_mcp_search").execute(q="x")

    assert _opens() == 1
    assert _FakeClientManager.instances[0].tool_calls == ["search"]
    await MCPSessionManager(context).aclose_all()


async def test_cached_request_opens_session_lazily_on_first_call(cache: MCPToolSchemaCache) -> None:
    await _load(cache, _context(_instance()))
    context = _context(_instance())
    registry = await _load(cache, context)
    assert _opens() == 1

    await registry.resolve_by_name("mcp_jira_mcp_search").execute(q="x")

    assert _opens() == 2
    assert _list_calls() == 1


async def test_instance_update_misses_the_cache(cache: MCPToolSchemaCache) -> None:
    await _load(cache, _context(_instance(updated_at=1)))
    await _load(cache, _context(_instance(updated_at=2)))
    assert _list_calls() == 2


async def test_explicit_invalidation_drops_every_variant(cache: MCPToolSchemaCache) -> None:
    await _load(cache, _context(_instance()))
    await cache.invalidate_instance("inst-1")
    await _load(cache, _context(_instance()))
    assert _list_calls() == 2


async def test_oauth_entries_are_not_shared_across_principals(cache: MCPToolSchemaCache) -> None:
    auth = {"isAuthenticated": True, "oauthTokens": {"access_token": "t"}, "updatedAt": 5}
    await _load(cache, _context(_instance("oauth"), owner="user-1", auth=auth))
    await _load(cache, _context(_instance("oauth"), owner="user-2", auth=auth))
    assert _list_calls() == 2

    await _load(cache, _context(_instance("oauth"), owner="user-1", auth=auth))
    assert _list_calls() == 2


async def test_credential_version_change_misses(cache: MCPToolSchemaCache) -> None:
    await _load(cache, _context(_instance("api_token"), auth={"apiToken": "a", "updatedAt": 1}))
    await _load(cache, _context(_instance("api_token"), auth={"apiToken": "b", "updatedAt": 2}))
    assert _list_calls() == 2


async def test_unauthenticated_and_shared_admin_entries_are_shared(cache: MCPToolSchemaCache) -> None:
    await _load(cache, _context(_instance(), owner="user-1"))
    await _load(cache, _context(_instance(), owner="user-2"))
    assert _list_calls() == 1

    admin = _instance("api_token", use_admin_auth=True)
    creds = {"apiToken": "shared", "updatedAt": 1}
    await _load(cache, _context(admin, owner="user-1", auth=creds))
    await _load(cache, _context(admin, owner="user-2", auth=creds))
    assert _list_calls() == 2


async def test_variant_never_contains_principal_or_config(cache: MCPToolSchemaCache) -> None:
    from app.agents.agent_loop.mcp_tool_loader import schema_cache_variant

    context = _context(_instance("oauth"), owner="agent-key-secret", auth={"updatedAt": 1})
    variant = schema_cache_variant(MCPAccessResolver.resolve(context)[0])
    assert "agent-key-secret" not in variant
    assert len(variant) == 64


async def test_failed_discovery_is_not_cached(cache: MCPToolSchemaCache, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(self: Any) -> list[dict]:
        raise RuntimeError("down")

    monkeypatch.setattr(_FakeClientManager, "list_tools_in_session", _boom)
    context = _context(_instance())
    await MCPToolProvider(cache).load_into(ToolRegistry(), context)

    assert context.mcp_tool_load_failures[0]["reason"] == "discovery_failed"
    assert await cache._redis.hgetall("pipeshub:mcp_tools:v1:inst-1") == {}
