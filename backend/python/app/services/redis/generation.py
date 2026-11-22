"""Generation counters (cache version counters, invalidation epochs) on the main Redis.

They live on the main Redis with no TTL, where nothing evicts them (the start-up check refuses ``allkeys-*``, and
``volatile-*`` only evicts keys that have a TTL). Only a failover or a restore can lose one. A lost counter must not
restart at 1, or a value a cache entry already carries could come back, so a missing counter is seeded from the
server's own ``TIME`` in microseconds (one clock, not each process's) and incremented in the same script: nothing can
run between the seed and the increment.
"""

from __future__ import annotations

from typing import Any

_SEEDED_INCRBY = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  local t = redis.call('TIME')
  redis.call('SET', KEYS[1], t[1] .. string.format('%06d', tonumber(t[2])))
end
return redis.call('INCRBY', KEYS[1], ARGV[1])
"""


async def seeded_incr(client: Any, key: str, by: int) -> int:  # noqa: ANN401 - a redis-py client or cluster client
    """``INCRBY key by`` on a counter seeded from the server's clock when missing; ``by=0`` reads (and seeds) it."""
    return int(await client.eval(_SEEDED_INCRBY, 1, key, by))
