"""Redis-backed `ISignedUrlCache` (R16).

Wraps a single Redis client (already TLS/cluster-mode aware via
`IRedisConnectionProvider`) behind the domain-shaped interface so
`blob_storage.py` never touches a raw Redis client directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.cache.interface import ISignedUrlCache

if TYPE_CHECKING:
    from app.services.redis.connection_provider import RedisClient

__all__ = ["RedisSignedUrlCache"]


class RedisSignedUrlCache(ISignedUrlCache):
    def __init__(self, client: "RedisClient", key_namespace: str = "") -> None:
        self._client = client
        # REDIS_KEY_NAMESPACE (R9), applied here the same way
        # `AccessibleRecordsCache` applies it to its own keys. Without it, two
        # deployments sharing one Redis serve each other's signed URLs --
        # every key is `sigurl:<org>:<doc>`, and org ids do not differ between
        # a staging and a production copy of the same tenant.
        self._prefix = f"{key_namespace}:" if key_namespace else ""

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> str | None:
        return await self._client.get(self._key(key))

    async def set(self, key: str, url: str, ttl_seconds: int) -> None:
        await self._client.set(self._key(key), url, ex=ttl_seconds)

    async def close(self) -> None:
        await self._client.aclose()
