"""Keys and values of the MCP hub: what the schema cache and the session pool are keyed by."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_core.mcp_hub.manifest import ToolDescriptor


@dataclass(frozen=True, slots=True)
class SessionKey:
    """One pooled session per (tenant, server, credential owner): never shared across owners."""

    tenant: str
    server_id: str
    owner: str

    def digest(self) -> str:
        raw = json.dumps([self.tenant, self.server_id, self.owner]).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class SchemaKey:
    """A server's tool list as one owner sees it under one stored configuration of the server."""

    tenant: str
    server_id: str
    owner: str
    config_digest: str


@dataclass(frozen=True, slots=True)
class CachedTools:
    tools: tuple[ToolDescriptor, ...]
    server_version: str | None
    protocol_version: str | None
    ttl_s: float


@dataclass(frozen=True, slots=True)
class PoolConfig:
    idle_ttl_s: float = 300.0
    max_age_s: float = 3600.0


@dataclass(frozen=True, slots=True)
class PooledSessionInfo:
    key: SessionKey
    variant: str
    leases: int
    opened_at: float
    last_used: float


def list_ttl_s(ttl_ms: object, ceiling_s: float) -> float:
    """A list result's freshness (2026-07-28 ``CacheableResult.ttlMs``), never longer than the deployment's ceiling."""
    if isinstance(ttl_ms, int | float) and not isinstance(ttl_ms, bool) and ttl_ms >= 0:
        return min(float(ttl_ms) / 1000.0, ceiling_s)
    return ceiling_s


__all__ = ["CachedTools", "PoolConfig", "PooledSessionInfo", "SchemaKey", "SessionKey", "list_ttl_s"]
