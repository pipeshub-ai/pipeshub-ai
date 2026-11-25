"""Ports of the MCP hub."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harness_core.mcp_hub.types import CachedTools, SchemaKey


class ToolSchemaCache(Protocol):
    """Discovered tool lists, so a request does not list a server's tools again while they are fresh."""

    async def get(self, key: SchemaKey) -> CachedTools | None:
        """The entry for exactly this key while it is fresh; ``None`` otherwise."""
        ...

    async def put(self, key: SchemaKey, tools: CachedTools) -> None:
        """Store or replace the entry; it is fresh for ``tools.ttl_s`` seconds."""
        ...

    async def invalidate(self, tenant: str, server_id: str) -> int:
        """Drop every entry of the server (every owner and configuration); the number dropped."""
        ...


__all__ = ["ToolSchemaCache"]
