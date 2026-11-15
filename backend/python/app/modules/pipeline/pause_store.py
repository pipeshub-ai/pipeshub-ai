"""The pause budget's state of each stage job, in Redis (``messaging:pause:{<vrid>:<rev>:<stage>}``).

A hash of ``since_ms``, ``charged``, ``sends``, ``trials`` and ``ledger_at_since`` (see
``pause_budget``). It is not a field of the record or of the stage state (the record node stays closed),
it expires 8 days after its last write (longer than the 24 h wall), and only a forced redrive or a
COMPLETED / SKIPPED settle deletes it: an automatic re-dispatch keeps it, so a revision whose budget is
spent cannot start over by being dispatched again.

Every write is one Lua script, so concurrent attempts of a job never lose an update: a PAUSED outcome adds its
counts (``apply``), and the hash records the highest stage attempt that wrote it, so a clean-up by a run that finished
(``clear``) leaves a budget a later attempt has written.

The jobs a provider hold must redrive are kept here too (``add_redrives``) until each is done, so a redrive that could
not be made at once (Redis down, the job still running) is made by a later pass.

Reads raise ``PauseStoreUnavailable`` when Redis cannot be read: the worker defers a job that has
paused before rather than run it uncounted.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.modules.pipeline.pause_budget import PauseState
from app.services.llm_gateway.fleet_breaker import OWED_TTL_SECONDS, owed_key_name
from app.services.llm_gateway.fleet_redis import FleetRedis, get_fleet_redis

PAUSE_TTL_SECONDS = 8 * 24 * 3600
REDIS_OP_TIMEOUT_S = 2.0

# KEYS: hash  ARGV: attempt, sends, charged, trials, since_ms, ledger_at_since, scope, scope_ledger, ttl
# A new hash starts at ``since_ms`` / ``ledger_at_since``. A scope learned after the first pause adds that key's ledger
# now to ``ledger_at_since`` (hold time before it was known is not subtracted). Returns the hash after the update.
_APPLY = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then
  redis.call('HSET', key, 'since_ms', ARGV[5], 'ledger_at_since', ARGV[6], 'scope', ARGV[7],
    'charged', 0, 'sends', 0, 'trials', 0, 'attempt', 0)
elseif ARGV[7] ~= '' then
  local scope = redis.call('HGET', key, 'scope')
  if (not scope) or scope == '' then
    redis.call('HSET', key, 'scope', ARGV[7])
    redis.call('HINCRBY', key, 'ledger_at_since', tonumber(ARGV[8]))
  end
end
redis.call('HINCRBY', key, 'sends', tonumber(ARGV[2]))
redis.call('HINCRBY', key, 'charged', tonumber(ARGV[3]))
redis.call('HINCRBY', key, 'trials', tonumber(ARGV[4]))
local attempt = tonumber(redis.call('HGET', key, 'attempt') or '0') or 0
if tonumber(ARGV[1]) > attempt then redis.call('HSET', key, 'attempt', ARGV[1]) end
redis.call('EXPIRE', key, tonumber(ARGV[9]))
return redis.call('HGETALL', key)
"""

# KEYS: hash  ARGV: attempt. Deletes the budget unless an attempt after ``attempt`` has written it.
_CLEAR = """
local written = tonumber(redis.call('HGET', KEYS[1], 'attempt') or '0') or 0
if written > tonumber(ARGV[1]) then return 0 end
return redis.call('DEL', KEYS[1])
"""

# KEYS: hash. A fresh charge count for a redriven job; its sends and trials stay.
_RELEASE_CHARGED = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
redis.call('HSET', KEYS[1], 'charged', 0)
return 1
"""


class PauseStoreUnavailable(Exception):
    """The pause budget's Redis could not be read or written."""


def _s(value: Any) -> str:  # noqa: ANN401 - redis replies are bytes or str by client config
    return value.decode() if isinstance(value, bytes) else str(value)


class PauseStore:
    """``fleet`` is bound lazily: a process whose fleet Redis is bound after this store is built still uses it."""

    def __init__(self, fleet: FleetRedis | None = None) -> None:
        self._fleet = fleet

    def _handle(self) -> FleetRedis:
        handle = self._fleet or get_fleet_redis()
        if handle is None:
            raise PauseStoreUnavailable("no Redis is bound for the pause budget")
        return handle

    @property
    def available(self) -> bool:
        return (self._fleet or get_fleet_redis()) is not None

    def _key(self, state_key: str) -> str:
        return self._handle().key(f"messaging:pause:{{{state_key}}}")

    async def _do(self, call: Any) -> Any:  # noqa: ANN401
        try:
            return await asyncio.wait_for(call, REDIS_OP_TIMEOUT_S)
        except PauseStoreUnavailable:
            raise
        except Exception as exc:
            raise PauseStoreUnavailable(f"{type(exc).__name__}: {exc}") from exc

    async def _eval(self, script: str, keys: list[str], *args: object) -> Any:  # noqa: ANN401
        return await self._do(self._handle().client().eval(script, len(keys), *keys, *args))

    @staticmethod
    def _state(raw: Any, key: str) -> PauseState | None:  # noqa: ANN401
        if not raw:
            return None
        if isinstance(raw, list):
            raw = dict(zip(raw[::2], raw[1::2], strict=False))
        fields = {_s(k): _s(v) for k, v in raw.items()}
        try:
            return PauseState(
                since_ms=int(fields["since_ms"]),
                charged=int(fields.get("charged", 0)),
                sends=int(fields.get("sends", 0)),
                trials=int(fields.get("trials", 0)),
                ledger_at_since=int(fields.get("ledger_at_since", 0)),
                scope=fields.get("scope", ""),
            )
        except (KeyError, ValueError):
            # A hash that is not ours: treat it as unreadable rather than as a fresh budget.
            raise PauseStoreUnavailable(f"malformed pause state at {key}") from None

    async def get(self, state_key: str) -> PauseState | None:
        key = self._key(state_key)
        return self._state(await self._do(self._handle().client().hgetall(key)), key)

    async def apply(
        self,
        state_key: str,
        *,
        attempt: int,
        sends: int,
        charged: int,
        trials: int,
        since_ms: int,
        ledger_at_since: int,
        scope: str = "",
        scope_ledger: int = 0,
    ) -> PauseState:
        """Add one PAUSED outcome's counts, atomically; a missing budget starts at ``since_ms``. Returns it after."""
        key = self._key(state_key)
        raw = await self._eval(
            _APPLY, [key], attempt, sends, charged, trials, since_ms, ledger_at_since, scope, scope_ledger, PAUSE_TTL_SECONDS
        )
        state = self._state(raw, key)
        if state is None:
            raise PauseStoreUnavailable(f"the pause state at {key} could not be written")
        return state

    async def put(self, state_key: str, state: PauseState) -> None:
        """Write a whole state (tests and tools; the worker only ever ``apply``s)."""
        key = self._key(state_key)
        client = self._handle().client()
        mapping = {
            "since_ms": state.since_ms,
            "charged": state.charged,
            "sends": state.sends,
            "trials": state.trials,
            "ledger_at_since": state.ledger_at_since,
            "scope": state.scope,
        }

        async def write() -> None:
            await client.hset(key, mapping=mapping)
            await client.expire(key, PAUSE_TTL_SECONDS)

        await self._do(write())

    async def delete(self, state_key: str) -> None:
        await self._do(self._handle().client().delete(self._key(state_key)))

    async def clear(self, state_key: str, *, attempt: int) -> bool:
        """Delete the budget of a run that settled as ``attempt``, unless a later attempt has written it since."""
        return bool(await self._eval(_CLEAR, [self._key(state_key)], attempt))

    async def release_charged(self, state_key: str) -> bool:
        """Give a redriven job a fresh charge count; its sends and trials stay. False when it has no state."""
        return bool(await self._eval(_RELEASE_CHARGED, [self._key(state_key)]))

    # ---- the redrives a hold owes ------------------------------------------------------------------
    # The owed set of a key scope is written by the breaker's script that opens the hold (``owed_key_name``), atomically
    # with it. The index of scopes that owe some is a convenience for the recovery leader: a job finds its own entry
    # without it (``is_owed``), so an index write that failed loses nothing.

    def _redrive_index(self) -> str:
        return self._handle().key("messaging:pause:owed-scopes")

    def _owed_key(self, scope: str) -> str:
        return self._handle().key(owed_key_name(scope))

    async def note_owed_scope(self, scope: str) -> None:
        client = self._handle().client()

        async def add() -> None:
            await client.sadd(self._redrive_index(), scope)
            await client.expire(self._redrive_index(), OWED_TTL_SECONDS)

        await self._do(add())

    async def add_redrives(self, scope: str, state_keys: tuple[str, ...] | list[str]) -> None:
        """Owe the jobs ``state_keys`` a redrive under ``scope`` (the breaker's script does this when it opens a hold)."""
        if not state_keys:
            return
        client = self._handle().client()
        key = self._owed_key(scope)

        async def add() -> None:
            await client.sadd(key, *state_keys)
            await client.expire(key, OWED_TTL_SECONDS)

        await self._do(add())
        await self.note_owed_scope(scope)

    async def is_owed(self, scope: str, state_key: str) -> bool:
        return bool(await self._do(self._handle().client().sismember(self._owed_key(scope), state_key)))

    async def pending_redrives(self) -> dict[str, list[str]]:
        """The redrives still owed, by key scope. A scope whose set has expired or emptied leaves the index."""
        client = self._handle().client()
        pending: dict[str, list[str]] = {}
        for raw_scope in await self._do(client.smembers(self._redrive_index())):
            scope = _s(raw_scope)
            members = sorted(_s(m) for m in await self._do(client.smembers(self._owed_key(scope))))
            if members:
                pending[scope] = members
            else:
                await self._do(client.srem(self._redrive_index(), scope))
        return pending

    async def redrive_done(self, scope: str, state_key: str) -> None:
        await self._do(self._handle().client().srem(self._owed_key(scope), state_key))
