"""Permission epochs: counters that move when who may open something changes.

One per connector (``state:epoch:ge:{org}:{connectorId}``), one per person (``state:epoch:pe:{org}:{userId}``, the
person's PipesHub user id) and one for the collections of an organisation (``state:epoch:ge:{org}:kb``). A permission
writer moves them after its write committed (``app.services.cache.invalidation_hooks``); the Node API reads the same
keys (``modules/enterprise_search/services/conversation/privacy/epochs.ts``) and keys the verdicts it saves on them,
so a verdict computed before a change is not used after it.

They live where both sides share state: the deployment's Redis, under its key namespace, or ``platform.kv_entries``
when there is no Redis (``REDIS_MODE=postgres``). A counter that does not exist starts at the time in microseconds,
so one lost to an eviction, a failover or a restore restarts above every value it had and cannot make an old
verdict current again.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.config.configuration_service import ConfigurationService

EPOCH_KEY_PREFIX = "state:epoch:"


def _part(what: str, value: object) -> str:
    # A ':' would let two different ids name one key.
    if not isinstance(value, str) or value == "" or ":" in value:
        raise ValueError(f"a {what} id cannot name a permission epoch")
    return value


def connector_epoch_key(org_id: str, connector_id: str) -> str:
    """Moves when the grants, groups or records of one connector change."""
    return f"{EPOCH_KEY_PREFIX}ge:{_part('organisation', org_id)}:{_part('connector', connector_id)}"


def principal_epoch_key(org_id: str, user_id: str) -> str:
    """Moves when who one person is changes: their account, their teams, their app access."""
    return f"{EPOCH_KEY_PREFIX}pe:{_part('organisation', org_id)}:{_part('user', user_id)}"


def kb_epoch_key(org_id: str) -> str:
    """Moves when a collection of the organisation is granted, its grants change, or its records change."""
    return f"{EPOCH_KEY_PREFIX}ge:{_part('organisation', org_id)}:kb"


class EpochCounters(Protocol):
    async def bump(self, keys: Sequence[str]) -> None:
        """Move each counter by one, creating it above every earlier value; raises when the store cannot be written."""
        ...


class _RedisLike(Protocol):
    async def set(self, name: str, value: str, ex: int | None = None, *, nx: bool = False) -> object: ...
    async def incr(self, name: str) -> int: ...


def _base() -> int:
    return time.time_ns() // 1000


class RedisEpochCounters:
    """The counters on Redis. `client` returns the client to use now: a provider's client is bound to the running loop."""

    def __init__(self, client: Callable[[], _RedisLike], namespace: str = "") -> None:
        self._client = client
        self._namespace = namespace

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}" if self._namespace else key

    async def bump(self, keys: Sequence[str]) -> None:
        client = self._client()
        base = str(_base())
        for key in keys:
            full = self._key(key)
            await client.set(full, base, nx=True)
            await client.incr(full)


class _PgCounterState(Protocol):
    async def incr_from(self, key: str, base: int) -> int: ...


class PgEpochCounters:
    """The counters in ``platform.kv_entries``, one upsert each."""

    def __init__(self, state: _PgCounterState | None = None) -> None:
        if state is None:
            from app.services.pg_state import PgState

            state = PgState()
        self._state = state

    async def bump(self, keys: Sequence[str]) -> None:
        base = _base()
        for key in keys:
            await self._state.incr_from(key, base)


async def create_epoch_counters(config_service: ConfigurationService) -> EpochCounters:
    """The counters of this deployment: PostgreSQL without a Redis server (``REDIS_MODE=postgres``), else its Redis."""
    from app.services.redis.config import RedisConnectionConfig
    from app.services.redis.connection_provider_factory import (
        get_redis_provider,
        uses_pg_state,
    )

    if uses_pg_state():
        return PgEpochCounters()
    conf = await config_service.get_redis_config()
    provider = get_redis_provider(RedisConnectionConfig.from_host_port(
        host=conf.host, port=conf.port, password=conf.password, db=conf.db, tls=conf.tls))
    return RedisEpochCounters(provider.get_client, namespace=provider.key_namespace)  # type: ignore[arg-type]


__all__ = [
    "EPOCH_KEY_PREFIX",
    "EpochCounters",
    "PgEpochCounters",
    "RedisEpochCounters",
    "connector_epoch_key",
    "create_epoch_counters",
    "kb_epoch_key",
    "principal_epoch_key",
]
