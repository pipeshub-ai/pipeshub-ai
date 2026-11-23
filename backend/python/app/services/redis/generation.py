"""Generation counters (cache version counters, invalidation epochs) on the main Redis.

They live on the main Redis with no TTL, where nothing evicts them (the start-up check refuses ``allkeys-*``, and
``volatile-*`` only evicts keys that have a TTL). Two things can still take one back:

- a lost key (a failover or restore without it): a missing counter is seeded from the server's own ``TIME`` in
  microseconds (one clock, not each process's) in the same script as the increment, so it restarts above every
  value it had and nothing can run between the seed and the increment;
- an older value (a restore from a snapshot, or a failover that dropped the last increments): the counter is back on
  a value it already issued, and a cache entry stored under that value would be current again. So every reading
  also returns the server's identity, ``run_id`` (new on every restart) and ``master_replid`` (new on every
  promotion), read in the same script, on the node that owns the key. Callers make it part of what they compare,
  and a rollback, which always comes with a restart or a promotion, turns every older entry into a miss.
"""

from __future__ import annotations

from typing import Any

_SEEDED_INCRBY = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  local t = redis.call('TIME')
  redis.call('SET', KEYS[1], t[1] .. string.format('%06d', tonumber(t[2])))
end
local value = redis.call('INCRBY', KEYS[1], ARGV[1])
local ident = ''
local server = redis.pcall('INFO', 'server')
if type(server) == 'string' then ident = string.match(server, 'run_id:(%w+)') or '' end
local repl = redis.pcall('INFO', 'replication')
if type(repl) == 'string' then ident = ident .. ':' .. (string.match(repl, 'master_replid:(%w+)') or '') end
return {ident, value}
"""


async def seeded_incr(client: Any, key: str, by: int) -> tuple[str, int]:  # noqa: ANN401 - a redis-py client or cluster client
    """``INCRBY key by`` on a counter seeded from the server's clock when missing; ``by=0`` reads (and seeds) it.

    Returns ``(server identity, value)``. shortcut: a server that refuses ``INFO`` inside a script (the in-process
    Redis of ``REDIS_MODE=postgres``/``inprocess``, which cannot be restored, or a managed variant that blocks it)
    gives an empty identity, and a rollback there is not detected; read the identity outside the script if such a
    managed variant is ever supported.
    """
    ident, value = await client.eval(_SEEDED_INCRBY, 1, key, by)
    return (ident.decode() if isinstance(ident, bytes) else str(ident)), int(value)
