"""Redis coordination for vector-store cleanup and reindex.

Indexing and connectors both need the cleanup phase and the single-flight job
lock. Keys live here so indexing does not import the connectors package.
"""

from __future__ import annotations

import asyncio

from logging import Logger
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

from app.services.messaging.config import RedisConfig

_PHASE_WRITE_ATTEMPTS = 3
_PHASE_WRITE_RETRY_SECONDS = 0.5

JOB_LOCK_KEY = "vector_store_rebuild:job"
CLEANUP_PHASE_KEY = "vector_store_cleanup:phase"

PHASE_DROPPING = "dropping"
PHASE_READY = "ready"
PHASE_FAILED = "failed"

# Short lease plus renewal, not a long TTL: a 24h lease meant one crashed job
# blocked every cleanup and reindex for a day with no recourse but deleting the
# key by hand. The holder renews while it works, so a crash frees the lock within
# one TTL instead.
JOB_LOCK_TTL_SECONDS = 300
JOB_LOCK_RENEW_INTERVAL_SECONDS = 60

# The phase is only meaningful while a cleanup is in flight. Without an expiry a
# cleanup that dies after writing "dropping" blocks the reindex route for ever.
CLEANUP_PHASE_TTL_SECONDS = 6 * 60 * 60

_RELEASE_IF_OWNER_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_REFRESH_IF_OWNER_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def _redis_from_config(redis_config: RedisConfig) -> Redis:
    return Redis(
        host=redis_config.host,
        port=redis_config.port,
        password=redis_config.password,
        db=redis_config.db,
        encoding="utf-8",
        decode_responses=True,
    )


class RebuildJobLock:
    """SET NX lock so cleanup and reindex never run together."""

    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int = JOB_LOCK_TTL_SECONDS,
        token: str | None = None,
    ) -> None:
        self._redis = redis
        self._ttl_seconds = max(1, ttl_seconds)
        self.token = token or str(uuid4())

    async def try_acquire(self) -> bool:
        return bool(
            await self._redis.set(
                JOB_LOCK_KEY,
                self.token,
                nx=True,
                ex=self._ttl_seconds,
            )
        )

    async def refresh(self) -> bool:
        """Extend the lease, but only while we still own it.

        Compare-and-expire in one Lua step. SET XX GET would overwrite a new
        owner's token before returning the previous holder.
        """
        return bool(
            await self._redis.eval(
                _REFRESH_IF_OWNER_LUA,
                1,
                JOB_LOCK_KEY,
                self.token,
                self._ttl_seconds,
            )
        )

    async def release(self) -> None:
        await self._redis.eval(_RELEASE_IF_OWNER_LUA, 1, JOB_LOCK_KEY, self.token)


async def get_cleanup_phase(redis: Redis) -> str | None:
    value = await redis.get(CLEANUP_PHASE_KEY)
    if value is None:
        return None
    return str(value)


async def set_cleanup_phase(redis: Redis, phase: str) -> None:
    await redis.set(CLEANUP_PHASE_KEY, phase, ex=CLEANUP_PHASE_TTL_SECONDS)


async def redis_from_config_service(config_service: Any) -> Redis:
    redis_config = await config_service.get_redis_config()
    return _redis_from_config(redis_config)


async def mark_cleanup_phase(
    config_service: Any,
    phase: str,
    logger: Logger | None = None,
    redis: Redis | None = None,
) -> None:
    """Publish the phase without letting Redis failures fail the recreate.

    Retried, because the connectors side polls this key to decide whether the
    cleanup succeeded: a dropped write there reports a failed job over a
    perfectly healthy collection.
    """
    client = redis
    owns_client = False
    try:
        if client is None:
            client = await redis_from_config_service(config_service)
            owns_client = True
        last_error: Exception | None = None
        for attempt in range(_PHASE_WRITE_ATTEMPTS):
            try:
                await set_cleanup_phase(client, phase)
                return
            except Exception as exc:
                last_error = exc
                if attempt + 1 < _PHASE_WRITE_ATTEMPTS:
                    await asyncio.sleep(_PHASE_WRITE_RETRY_SECONDS)
        if logger is not None and last_error is not None:
            logger.error(
                "Failed to set vector-store cleanup phase to %s after %d attempts: %s "
                "— the collection may be healthy even if the job reports a timeout",
                phase,
                _PHASE_WRITE_ATTEMPTS,
                last_error,
            )
    except Exception:
        if logger is not None:
            logger.exception("Failed to set vector-store cleanup phase to %s", phase)
    finally:
        if owns_client and client is not None:
            await client.aclose()
