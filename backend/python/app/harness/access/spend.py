"""``SpendCounter`` over Redis: one hash per run, charged by one script.

Redis rather than Mongo: the model gateway charges on every model call and
reads before each one, every gateway replica must see the same total, and the
increments must be atomic and idempotent per charge id. Redis gives that in one
round trip (a Lua script: ``SET NX`` on the charge id, then ``HINCRBY``); it is
already a hard dependency (the KV store, the live bus), while Mongo is Node's
store and the Python harness reaches it only through Node. A counter lost with
Redis under-counts one run's past spend; P6.5's ledger (durable, with
reservations) replaces this adapter by binding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from harness_core.access.types import Spend

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from app.services.redis.connection_provider import RedisClient
    from harness_core.access.ports import SpendKey

# Longer than any run's calendar deadline; the totals of a finished run then expire.
SPEND_TTL_S = 14 * 24 * 3600

_CHARGE = """
if redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[3]) then
  redis.call('HINCRBY', KEYS[1], 'usd', ARGV[1])
  redis.call('HINCRBY', KEYS[1], 'tokens', ARGV[2])
end
redis.call('EXPIRE', KEYS[1], ARGV[3])
return redis.call('HMGET', KEYS[1], 'usd', 'tokens', 'stopped')
"""


def _spend(values: list[object]) -> Spend:
    usd, tokens, stopped = [*list(values), None, None, None][:3]
    return Spend(int(str(usd)) if usd is not None else 0, int(str(tokens)) if tokens is not None else 0, stopped == "1")


class RedisSpendCounter:
    def __init__(self, client: RedisClient, *, namespace: str = "", ttl_s: int = SPEND_TTL_S) -> None:
        self._client = client
        self._prefix = f"{namespace}harness:spend:"
        self._ttl_s = ttl_s

    def _key(self, key: SpendKey) -> str:
        # One hash slot per run (a cluster runs the script on both keys).
        return f"{self._prefix}{{{key.tenant}:{key.run_id}}}"

    async def spent(self, key: SpendKey) -> Spend:
        values = await cast("Awaitable[list[object]]", self._client.hmget(self._key(key), ["usd", "tokens", "stopped"]))  # pyright: ignore[reportUnknownMemberType]
        return _spend(values)

    async def charge(self, key: SpendKey, charge_id: str, amount: Spend) -> Spend:
        run = self._key(key)
        result = self._client.eval(
            _CHARGE, 2, run, f"{run}:charge:{charge_id}", str(amount.usd_micros), str(amount.tokens), str(self._ttl_s)
        )
        return _spend(await cast("Awaitable[list[object]]", result))

    async def stop(self, key: SpendKey) -> None:
        run = self._key(key)
        _ = await cast("Awaitable[int]", self._client.hset(run, "stopped", "1"))  # pyright: ignore[reportUnknownMemberType]
        _ = await self._client.expire(run, self._ttl_s)


__all__ = ["SPEND_TTL_S", "RedisSpendCounter"]
