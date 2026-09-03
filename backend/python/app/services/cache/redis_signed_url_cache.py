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
    def __init__(self, client: "RedisClient") -> None:
        self._client = client

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, url: str, ttl_seconds: int) -> None:
        await self._client.set(key, url, ex=ttl_seconds)

    async def close(self) -> None:
        await self._client.aclose()
