"""Redis cache of MCP `list_tools` results, shared by every query worker.

Without it every agent request connected to every attached MCP server and ran
`list_tools` (up to 30s each) before the first token. Entries live in one hash
per instance (field = caller-derived variant), so an instance update or delete
drops every principal's entry with one DEL. Each field carries its own write
time because the key TTL is refreshed by every write.

Redis never fails or stalls a request: errors fall back to live discovery and
trip a short breaker, like `AccessibleRecordsCache`.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from app.services.cache.interface import IMCPToolSchemaCache, NoopMCPToolSchemaCache
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.redis.connection_provider import RedisClient

__all__ = [
    "MCPToolSchemaCache",
    "get_mcp_tool_schema_cache",
    "set_mcp_tool_schema_cache",
]

_state: dict[str, IMCPToolSchemaCache] = {"cache": NoopMCPToolSchemaCache()}


def set_mcp_tool_schema_cache(cache: IMCPToolSchemaCache) -> None:
    """Register the process-wide cache (service startup)."""
    _state["cache"] = cache


def get_mcp_tool_schema_cache() -> IMCPToolSchemaCache:
    return _state["cache"]


class MCPToolSchemaCache(IMCPToolSchemaCache):
    KEY_PREFIX = "pipeshub:mcp_tools:v1"
    TTL_SECONDS = 600
    OP_TIMEOUT_SECONDS = 2.0
    DOWN_BACKOFF_SECONDS = 30.0

    def __init__(
        self,
        logger: "Logger",
        redis_client: "RedisClient",
        *,
        ttl_seconds: int = TTL_SECONDS,
        key_namespace: str = "",
    ) -> None:
        self.logger = logger
        self._redis: RedisClient | None = redis_client
        self._ttl = ttl_seconds
        self._prefix = f"{key_namespace}:{self.KEY_PREFIX}" if key_namespace else self.KEY_PREFIX
        self._down_until = 0.0

    @classmethod
    async def create(cls, logger: "Logger", config_service: "ConfigurationService") -> IMCPToolSchemaCache:
        """Never raises — an unreachable Redis yields the no-op cache."""
        client = None
        try:
            redis_config = await config_service.get_redis_config()
            provider = get_redis_provider(
                RedisConnectionConfig.from_host_port(
                    host=redis_config.host,
                    port=redis_config.port,
                    password=redis_config.password,
                    db=redis_config.db,
                    tls=redis_config.tls,
                )
            )
            client = provider.create_client(
                ClientOptions(
                    decode_responses=True,
                    socket_timeout_seconds=cls.OP_TIMEOUT_SECONDS,
                    socket_connect_timeout_seconds=cls.OP_TIMEOUT_SECONDS,
                )
            )
            await client.ping()
        except Exception as e:
            logger.warning("MCP tool-schema cache unavailable (%s); discovering live", str(e))
            if client is not None:
                try:
                    await client.aclose()
                except Exception as close_error:
                    logger.debug("Error closing MCP tool-schema cache client: %s", str(close_error))
            return NoopMCPToolSchemaCache()
        return cls(logger, client, key_namespace=provider.key_namespace)

    def _key(self, instance_id: str) -> str:
        return f"{self._prefix}:{instance_id}"

    @property
    def _available(self) -> bool:
        return self._redis is not None and time.monotonic() >= self._down_until

    async def get(self, instance_id: str, variant: str) -> list[dict] | None:
        if not self._available:
            return None
        try:
            raw = await self._redis.hget(self._key(instance_id), variant)
        except Exception as e:
            self._mark_down("read", e)
            return None
        if raw is None:
            return None
        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            return None
        written_at = envelope.get("t") if isinstance(envelope, dict) else None
        tools = envelope.get("tools") if isinstance(envelope, dict) else None
        if not isinstance(written_at, (int, float)) or not isinstance(tools, list):
            return None
        if time.time() - written_at > self._ttl:
            return None
        return tools

    async def set(self, instance_id: str, variant: str, tools: list[dict]) -> None:
        if not self._available:
            return
        key = self._key(instance_id)
        envelope = json.dumps({"t": int(time.time()), "tools": tools}, separators=(",", ":"))
        try:
            await self._redis.hset(key, variant, envelope)
            await self._redis.expire(key, self._ttl)
        except Exception as e:
            self._mark_down("write", e)

    async def invalidate_instance(self, instance_id: str) -> None:
        if not self._available:
            return
        try:
            await self._redis.delete(self._key(instance_id))
        except Exception as e:
            self._mark_down("delete", e)

    async def close(self) -> None:
        client, self._redis = self._redis, None
        if client is not None:
            try:
                await client.aclose()
            except Exception as e:
                self.logger.debug("Error closing MCP tool-schema cache: %s", str(e))

    def _mark_down(self, op: str, error: Exception) -> None:
        first = time.monotonic() >= self._down_until
        self._down_until = time.monotonic() + self.DOWN_BACKOFF_SECONDS
        if first:
            self.logger.warning(
                "MCP tool-schema cache %s failed (%s); bypassing for %ss",
                op, str(error), self.DOWN_BACKOFF_SECONDS,
            )
