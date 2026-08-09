"""`RedisNonceStore`: `INonceStore` backed by `SET ... NX EX` -- the
single-command atomic "insert if absent, with TTL" primitive Redis provides
for exactly this pattern (no Lua script needed, unlike the trigger/run
stores' multi-key operations).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.tasks.interface.nonce_store import INonceStore

if TYPE_CHECKING:
    from redis.asyncio import Redis

_PREFIX = "pipeshub:tasks:nonce"


class RedisNonceStore(INonceStore):
    def __init__(self, redis_client: "Redis") -> None:
        self._redis = redis_client

    async def check_and_set(self, scope: str, nonce: str, *, ttl_seconds: int) -> bool:
        key = f"{_PREFIX}:{scope}:{nonce}"
        was_set = await self._redis.set(key, "1", nx=True, ex=ttl_seconds)
        return bool(was_set)
