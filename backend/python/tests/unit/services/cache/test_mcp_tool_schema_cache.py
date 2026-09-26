"""MCPToolSchemaCache: namespaced hash layout, per-field TTL, invalidation, and
a broken Redis never failing the request."""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis

from app.services.cache.interface import NoopMCPToolSchemaCache
from app.services.cache.mcp_tool_schema_cache import (
    MCPToolSchemaCache,
    get_mcp_tool_schema_cache,
    set_mcp_tool_schema_cache,
)

TOOLS = [{"name": "search", "namespacedName": "mcp_x_search", "inputSchema": {}}]


def _cache(**kwargs) -> MCPToolSchemaCache:
    return MCPToolSchemaCache(logging.getLogger("t"), fakeredis.aioredis.FakeRedis(decode_responses=True), **kwargs)


async def test_round_trip_and_namespaced_key() -> None:
    cache = _cache(key_namespace="staging")
    await cache.set("inst-1", "v1", TOOLS)

    assert await cache.get("inst-1", "v1") == TOOLS
    assert await cache.get("inst-1", "v2") is None
    assert await cache._redis.exists("staging:pipeshub:mcp_tools:v1:inst-1")
    assert 0 < await cache._redis.ttl("staging:pipeshub:mcp_tools:v1:inst-1") <= MCPToolSchemaCache.TTL_SECONDS


async def test_field_older_than_ttl_is_a_miss() -> None:
    cache = _cache(ttl_seconds=10)
    await cache._redis.hset(
        "pipeshub:mcp_tools:v1:inst-1", "v1", f'{{"t": {int(time.time()) - 60}, "tools": []}}',
    )
    assert await cache.get("inst-1", "v1") is None


async def test_invalidate_instance_drops_all_variants() -> None:
    cache = _cache()
    await cache.set("inst-1", "a", TOOLS)
    await cache.set("inst-1", "b", TOOLS)
    await cache.set("inst-2", "a", TOOLS)

    await cache.invalidate_instance("inst-1")

    assert await cache.get("inst-1", "a") is None
    assert await cache.get("inst-1", "b") is None
    assert await cache.get("inst-2", "a") == TOOLS


async def test_broken_redis_misses_and_trips_breaker() -> None:
    client = MagicMock()
    client.hget = AsyncMock(side_effect=ConnectionError("down"))
    cache = MCPToolSchemaCache(logging.getLogger("t"), client)

    assert await cache.get("inst-1", "v") is None
    await cache.set("inst-1", "v", TOOLS)
    await cache.invalidate_instance("inst-1")

    assert client.hget.await_count == 1
    client.hset.assert_not_called()


async def test_create_falls_back_to_noop_when_redis_unreachable() -> None:
    config_service = MagicMock()
    config_service.get_redis_config = AsyncMock(side_effect=RuntimeError("no redis"))

    cache = await MCPToolSchemaCache.create(logging.getLogger("t"), config_service)

    assert isinstance(cache, NoopMCPToolSchemaCache)


def test_process_registry_defaults_to_noop() -> None:
    previous = get_mcp_tool_schema_cache()
    try:
        set_mcp_tool_schema_cache(NoopMCPToolSchemaCache())
        assert isinstance(get_mcp_tool_schema_cache(), NoopMCPToolSchemaCache)
        cache = _cache()
        set_mcp_tool_schema_cache(cache)
        assert get_mcp_tool_schema_cache() is cache
    finally:
        set_mcp_tool_schema_cache(previous)
