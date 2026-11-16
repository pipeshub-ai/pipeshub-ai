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
from enum import IntEnum
from typing import Any

from app.modules.pipeline.pause_budget import PauseState
from app.services.llm_gateway.fleet_breaker import OWED_TTL_SECONDS, owed_key_name
from app.services.llm_gateway.fleet_redis import FleetRedis, get_fleet_redis

PAUSE_TTL_SECONDS = 8 * 24 * 3600
REDIS_OP_TIMEOUT_S = 2.0

# Every script here touches one job's keys only: the budget hash and its runs' reservations share the ``{<state key>}``
# hash tag, so each is a single-slot script on a cluster.

# Creates the budget hash (inside a script). ARGV positions are those of _COMMIT.
_CREATE = """
local function create(key, since, ledger, scope)
  redis.call('HSET', key, 'since_ms', since, 'ledger_at_since', ledger, 'scope', scope,
    'charged', 0, 'sends', 0, 'trials', 0, 'attempt', 0)
end
"""

# KEYS: budget, runs  ARGV: attempt, sends, charged, trials, since_ms, ledger_at_since, scope, scope_ledger, ttl, create
# Commits one run's counts and releases its reservation. A missing budget starts at ``since_ms`` / ``ledger_at_since``
# when ``create`` is '1' (a PAUSED outcome, whose wall starts now) or the run spent anything. A scope learned after the
# first pause adds that key's ledger now to ``ledger_at_since`` (hold time before it was known is not subtracted).
# Returns the budget after the update, or an empty reply when there is none.
_COMMIT = _CREATE + """
local key, runs = KEYS[1], KEYS[2]
redis.call('HDEL', runs, ARGV[1])
local spent = tonumber(ARGV[2]) + tonumber(ARGV[3]) + tonumber(ARGV[4])
if redis.call('EXISTS', key) == 0 then
  if ARGV[10] ~= '1' and spent == 0 then return {} end
  create(key, ARGV[5], ARGV[6], ARGV[7])
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

# KEYS: budget, runs  ARGV: attempt, absolute cap, now_ms, stale_ms, ttl
# Reserves for run ``attempt`` what the absolute cap has left after the committed sends and the other live runs'
# reservations, and returns it. A reservation older than ``stale_ms`` belongs to a run that died: it is committed as
# fully spent, so a crashed run cannot hand its share to a twin.
_RESERVE = _CREATE + """
local key, runs = KEYS[1], KEYS[2]
local attempt, cap, now, stale = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
local held = 0
local entries = redis.call('HGETALL', runs)
for i = 1, #entries, 2 do
  local field, value = entries[i], entries[i + 1]
  local sep = string.find(value, ':', 1, true)
  local granted, at = tonumber(string.sub(value, 1, sep - 1)), tonumber(string.sub(value, sep + 1))
  if now - at > stale then
    if redis.call('EXISTS', key) == 0 then create(key, now, 0, '') end
    redis.call('HINCRBY', key, 'sends', granted)
    redis.call('EXPIRE', key, tonumber(ARGV[5]))
    redis.call('HDEL', runs, field)
  elseif field ~= attempt then
    held = held + granted
  end
end
local sends = tonumber(redis.call('HGET', key, 'sends') or '0') or 0
local grant = cap - sends - held
if grant < 0 then grant = 0 end
redis.call('HSET', runs, attempt, grant .. ':' .. now)
redis.call('EXPIRE', runs, tonumber(ARGV[5]))
return grant
"""

# KEYS: budget, runs  ARGV: attempt. Deletes the budget unless an attempt after ``attempt`` has written it, and releases
# the run's reservation either way (a live twin's reservation stays).
_CLEAR = """
redis.call('HDEL', KEYS[2], ARGV[1])
local written = tonumber(redis.call('HGET', KEYS[1], 'attempt') or '0') or 0
if written > tonumber(ARGV[1]) then return 0 end
return redis.call('DEL', KEYS[1])
"""

# KEYS: budget, runs  ARGV: attempt. A forced redrive's fresh start: deletes the budget and the reservations of the runs
# up to ``attempt`` (the state's attempt when it was claimed; a claim needs no live run, so those runs are over). A run
# claimed after it, with a higher attempt, keeps its reservation and keeps counting against the cap.
_RESET = """
local limit = tonumber(ARGV[1])
local entries = redis.call('HKEYS', KEYS[2])
for _, field in ipairs(entries) do
  local run = tonumber(field)
  if run == nil or run <= limit then redis.call('HDEL', KEYS[2], field) end
end
return redis.call('DEL', KEYS[1])
"""

# KEYS: index  ARGV: member, the score read. Removes the member only while its score is still the one read.
_REMOVE_IF_SCORE = """
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if score and tonumber(score) == tonumber(ARGV[2]) then return redis.call('ZREM', KEYS[1], ARGV[1]) end
return 0
"""

# KEYS: hash  ARGV: episode. A fresh charge count for a job a hold redrives, once per hold episode: a retry of the same
# reset (its redrive went out but marking it done failed) finds the episode recorded and leaves the charges a new run
# has made since. Its sends and trials stay. Returns 0 with no budget, 2 when this episode's reset was already done.
_RELEASE_CHARGED = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if ARGV[1] ~= '' and redis.call('HGET', KEYS[1], 'reset_episode') == ARGV[1] then return 2 end
redis.call('HSET', KEYS[1], 'charged', 0)
if ARGV[1] ~= '' then redis.call('HSET', KEYS[1], 'reset_episode', ARGV[1]) end
return 1
"""


class ResetResult(IntEnum):
    """What a charge reset did (``_RELEASE_CHARGED``)."""

    NO_BUDGET = 0
    RESET = 1
    # This hold episode's reset was done before: the stored charges are a later run's and stay.
    ALREADY = 2


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

    def _runs_key(self, state_key: str) -> str:
        return self._handle().key(f"messaging:pause:{{{state_key}}}:runs")

    async def reserve(self, state_key: str, *, attempt: int, absolute: int, now_ms: int, stale_ms: int) -> int:
        """Reserve for run ``attempt`` the failed sends the absolute cap has left (``_RESERVE``); returns them."""
        return int(await self._eval(
            _RESERVE, [self._key(state_key), self._runs_key(state_key)], attempt, absolute, now_ms, stale_ms,
            PAUSE_TTL_SECONDS,
        ))

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
        create: bool = True,
    ) -> PauseState | None:
        """Commit one run's counts atomically and release its reservation (``_COMMIT``). With ``create`` (a PAUSED
        outcome) a missing budget starts at ``since_ms``; otherwise one is created only when the run spent anything.
        Returns the budget after, or None when there is none."""
        key = self._key(state_key)
        raw = await self._eval(
            _COMMIT, [key, self._runs_key(state_key)], attempt, sends, charged, trials, since_ms, ledger_at_since, scope,
            scope_ledger, PAUSE_TTL_SECONDS, "1" if create else "0",
        )
        state = self._state(raw, key)
        if state is None and create:
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

    async def reset(self, state_key: str, *, attempt: int) -> None:
        """A forced redrive's fresh start (``_RESET``): the budget and the reservations of the runs up to ``attempt``."""
        await self._eval(_RESET, [self._key(state_key), self._runs_key(state_key)], attempt)

    async def clear(self, state_key: str, *, attempt: int) -> bool:
        """Delete the budget of a run that settled as ``attempt``, unless a later attempt has written it since."""
        return bool(await self._eval(_CLEAR, [self._key(state_key), self._runs_key(state_key)], attempt))

    async def release_charged(self, state_key: str, episode: str = "") -> ResetResult:
        """Give a job a hold redrives a fresh charge count, once per hold ``episode`` (``_RELEASE_CHARGED``); its sends
        and trials stay."""
        return ResetResult(int(await self._eval(_RELEASE_CHARGED, [self._key(state_key)], episode)))

    # ---- the redrives a hold owes ------------------------------------------------------------------
    # The owed set of a key scope is written by the breaker's script that opens the hold (``owed_key_name``), atomically
    # with it. The index of scopes that owe some cannot join that script on a cluster (another slot), so it is a fast
    # path: ``reconcile_owed`` re-indexes from the owed sets themselves, a wake that finds its job owed re-notes its
    # scope, and an entry is removed only by a compare-and-remove against the add counter, so it never races an add.

    def _redrive_index(self) -> str:
        return self._handle().key("messaging:pause:owed-scopes")

    def _owed_key(self, scope: str) -> str:
        return self._handle().key(owed_key_name(scope))

    async def note_owed_scope(self, scope: str) -> None:
        client = self._handle().client()

        async def add() -> None:
            # The score counts the adds: a removal that read an older count leaves the entry.
            await client.zincrby(self._redrive_index(), 1, scope)
            await client.expire(self._redrive_index(), OWED_TTL_SECONDS)

        await self._do(add())

    async def add_redrives(self, scope: str, state_keys: tuple[str, ...] | list[str], episode: str = "") -> None:
        """Owe the jobs ``state_keys`` a redrive under ``scope`` for hold ``episode`` (the breaker's script does this when
        it opens a hold)."""
        if not state_keys:
            return
        client = self._handle().client()
        key = self._owed_key(scope)

        async def add() -> None:
            await client.hset(key, mapping=dict.fromkeys(state_keys, episode))
            await client.expire(key, OWED_TTL_SECONDS)

        await self.note_owed_scope(scope)
        await self._do(add())

    async def owed_episode(self, scope: str, state_key: str) -> str | None:
        """The hold episode ``state_key`` is owed a redrive for, or None when it is owed none."""
        value = await self._do(self._handle().client().hget(self._owed_key(scope), state_key))
        return None if value is None else _s(value)

    async def is_owed(self, scope: str, state_key: str) -> bool:
        return await self.owed_episode(scope, state_key) is not None

    async def pending_redrives(self) -> dict[str, list[tuple[str, str]]]:
        """The redrives still owed, by key scope: ``(state key, hold episode)``. A scope whose set has emptied or
        expired leaves the index, unless it was noted again meanwhile."""
        client = self._handle().client()
        pending: dict[str, list[tuple[str, str]]] = {}
        for raw_scope, score in await self._do(client.zrange(self._redrive_index(), 0, -1, withscores=True)):
            scope = _s(raw_scope)
            owed = await self._do(client.hgetall(self._owed_key(scope)))
            members = sorted((_s(k), _s(v)) for k, v in owed.items())
            if members:
                pending[scope] = members
            else:
                await self._eval(_REMOVE_IF_SCORE, [self._redrive_index()], scope, repr(float(score)))
        return pending

    async def reconcile_owed(self) -> int:
        """Index every scope whose owed set Redis has (a SCAN of the namespace). Returns how many scopes it found."""
        from app.services.llm_gateway.fleet_breaker import FleetBreakerUnavailable
        from app.services.llm_gateway.fleet_hold import _scope_of, scan_keys

        found = 0
        try:
            async for key in scan_keys(self._handle(), "sg:{p:", "}:owed"):
                scope = _scope_of(key, "sg:{p:", "}:owed")
                if scope is not None and await self._do(self._handle().client().hlen(key)):
                    await self.note_owed_scope(scope)
                    found += 1
        except FleetBreakerUnavailable as exc:
            raise PauseStoreUnavailable(str(exc)) from exc
        return found

    async def redrive_done(self, scope: str, state_key: str) -> None:
        await self._do(self._handle().client().hdel(self._owed_key(scope), state_key))
