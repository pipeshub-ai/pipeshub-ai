"""`RedisRateLimiter`: `IRateLimiter` backed by a fixed-window counter.
`INCR` + `EXPIRE NX` in a single Lua call so the window's TTL is only ever
armed once per window (a plain `INCR` + separate `EXPIRE` would reset the
window's expiry on every request, letting a steady stream of requests keep
the window alive forever instead of resetting it every `window_seconds`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.tasks.interface.rate_limiter import IRateLimiter

if TYPE_CHECKING:
    from redis.asyncio import Redis

_PREFIX = "pipeshub:tasks:ratelimit"

_FIXED_WINDOW_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_seconds = tonumber(ARGV[2])

local count = redis.call("INCR", key)
if count == 1 then
    redis.call("EXPIRE", key, window_seconds)
end
if count > limit then
    return 0
end
return 1
"""


class RedisRateLimiter(IRateLimiter):
    def __init__(self, redis_client: "Redis") -> None:
        self._redis = redis_client
        self._script = redis_client.register_script(_FIXED_WINDOW_SCRIPT)

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        result: int = await self._script(
            keys=[f"{_PREFIX}:{key}"], args=[limit, window_seconds],
        )
        return bool(result)
