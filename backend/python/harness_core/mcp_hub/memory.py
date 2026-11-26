"""In-process adapter of ``ToolSchemaCache``."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_core.clock import Clock
    from harness_core.mcp_hub.types import CachedTools, SchemaKey


class InMemoryToolSchemaCache:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._entries: dict[SchemaKey, tuple[float, CachedTools]] = {}

    async def get(self, key: SchemaKey) -> CachedTools | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, tools = entry
        if self._clock.monotonic() >= expires_at:
            del self._entries[key]
            return None
        return tools

    async def put(self, key: SchemaKey, tools: CachedTools) -> None:
        self._entries[key] = (self._clock.monotonic() + tools.ttl_s, tools)

    async def invalidate(self, tenant: str, server_id: str) -> int:
        stale = [k for k in self._entries if k.tenant == tenant and k.server_id == server_id]
        for key in stale:
            del self._entries[key]
        return len(stale)


__all__ = ["InMemoryToolSchemaCache"]
