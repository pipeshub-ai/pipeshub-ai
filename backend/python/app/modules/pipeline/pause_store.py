"""The pause budget's state of each stage job, in Redis (``messaging:pause:{<vrid>:<rev>:<stage>}``).

A hash of ``since_ms``, ``charged``, ``sends``, ``trials`` and ``ledger_at_since`` (see
``pause_budget``). It is not a field of the record or of the stage state (the record node stays closed),
it expires 8 days after its last write (longer than the 24 h wall), and only a forced redrive or a
COMPLETED / SKIPPED settle deletes it: an automatic re-dispatch keeps it, so a revision whose budget is
spent cannot start over by being dispatched again.

Reads raise ``PauseStoreUnavailable`` when Redis cannot be read: the worker defers a job that has
paused before rather than run it uncounted.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.modules.pipeline.pause_budget import PauseState
from app.services.llm_gateway.fleet_redis import FleetRedis, get_fleet_redis

PAUSE_TTL_SECONDS = 8 * 24 * 3600
REDIS_OP_TIMEOUT_S = 2.0


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

    async def get(self, state_key: str) -> PauseState | None:
        key = self._key(state_key)
        raw = await self._do(self._handle().client().hgetall(key))
        if not raw:
            return None
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

    async def put(self, state_key: str, state: PauseState) -> None:
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

    async def release_charged(self, state_key: str) -> bool:
        """Give a redriven job a fresh charge count; its sends and trials stay. False when it has no state."""
        key = self._key(state_key)
        client = self._handle().client()

        async def reset() -> bool:
            if not await client.exists(key):
                return False
            await client.hset(key, "charged", 0)
            return True

        return bool(await self._do(reset()))
