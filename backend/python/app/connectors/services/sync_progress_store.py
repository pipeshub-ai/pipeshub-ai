"""Run-scoped connector sync/indexing progress counters, backed by Redis.

A connector "sync run" has two decoupled phases:

- ``DISCOVERING`` — ``run_sync()`` is enumerating the source and publishing
  records to the indexing pipeline. ``discovered`` grows during this phase.
- ``INDEXING`` — discovery has closed (``total`` frozen to ``discovered``) and
  the indexing service is draining the queue. ``indexed``/``failed``/``skipped``
  grow as records reach a terminal indexing state.

Counters are stored in a single Redis hash per connector instance
(``connector_sync_progress:{org_id}:{connector_id}``). All operations are
best-effort: Redis being unavailable must never break a sync or indexing run,
so every method swallows and logs its own errors.

Because discovery (connectors service) and indexing (indexing service) run in
different processes, both write to the same shared Redis keys. Increment
methods are gated on key existence so unrelated record events (e.g. manual KB
uploads, real-time webhook updates with no active run) never create a key.
"""

import logging
import time
import uuid
from typing import Any, Optional

from redis import asyncio as aioredis  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.utils.redis_util import build_redis_url


class SyncPhase:
    DISCOVERING = "DISCOVERING"
    INDEXING = "INDEXING"
    DONE = "DONE"
    IDLE = "IDLE"


_NUMERIC_FIELDS = ("discovered", "indexed", "failed", "skipped", "total")

# A run whose heartbeat is older than this is treated as stalled (crashed
# indexer, dropped Kafka messages) so the UI does not spin forever.
STALE_THRESHOLD_MS = 30 * 60 * 1000


def summarize_run(run: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Derive a UI-friendly view (phase, percent, isActive) from raw counters."""
    if not run:
        return {
            "runId": None,
            "phase": SyncPhase.IDLE,
            "discovered": 0,
            "indexed": 0,
            "failed": 0,
            "skipped": 0,
            "total": 0,
            "processed": 0,
            "percent": None,
            "fullSync": False,
            "startedAt": 0,
            "heartbeatAt": 0,
            "isStale": False,
            "isActive": False,
        }

    phase = run.get("phase") or SyncPhase.IDLE
    discovered = int(run.get("discovered", 0) or 0)
    indexed = int(run.get("indexed", 0) or 0)
    failed = int(run.get("failed", 0) or 0)
    skipped = int(run.get("skipped", 0) or 0)
    total = int(run.get("total", 0) or 0)
    processed = indexed + failed + skipped

    percent: Optional[int]
    if phase == SyncPhase.DISCOVERING:
        settled = False
        percent = None  # total unknown while enumerating -> indeterminate
    elif phase == SyncPhase.INDEXING:
        if total <= 0:
            settled = True
            percent = 100
        else:
            settled = processed >= total
            percent = min(100, round(100 * min(processed, total) / total))
    else:  # DONE / IDLE
        settled = True
        percent = 100

    heartbeat = int(run.get("heartbeatAt", 0) or 0)
    now_ms = int(time.time() * 1000)
    is_stale = bool(heartbeat) and (now_ms - heartbeat) > STALE_THRESHOLD_MS
    is_active = (not settled) and (not is_stale)

    return {
        "runId": run.get("runId"),
        "phase": phase,
        "discovered": discovered,
        "indexed": indexed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "processed": processed,
        "percent": percent,
        "fullSync": bool(run.get("fullSync", False)),
        "startedAt": int(run.get("startedAt", 0) or 0),
        "heartbeatAt": heartbeat,
        "isStale": is_stale,
        "isActive": is_active,
    }


class ConnectorSyncProgressStore:
    """Per-connector-instance run progress counters in Redis."""

    KEY_PREFIX = "connector_sync_progress:"
    # Runs that never settle (crashed indexer, lost messages) self-expire.
    TTL_SECONDS = 24 * 60 * 60

    def __init__(self, logger: logging.Logger, redis_client) -> None:
        self.logger = logger
        self._redis = redis_client

    @classmethod
    async def create(
        cls, logger: logging.Logger, config_service: ConfigurationService
    ) -> "ConnectorSyncProgressStore":
        redis_config = await config_service.get_config(config_node_constants.REDIS.value)
        if not redis_config or not isinstance(redis_config, dict):
            raise ValueError("Redis configuration not found")
        redis_url = build_redis_url(redis_config)
        # from_url returns a client synchronously (see app/health/health.py); the
        # connection is established lazily on first command.
        redis_client = aioredis.from_url(  # type: ignore[no-untyped-call]
            redis_url, encoding="utf-8", decode_responses=True
        )
        return cls(logger, redis_client)

    def _key(self, org_id: str, connector_id: str) -> str:
        return f"{self.KEY_PREFIX}{org_id}:{connector_id}"

    async def start_run(
        self,
        org_id: str,
        connector_id: str,
        *,
        full_sync: bool = False,
        run_id: Optional[str] = None,
    ) -> Optional[str]:
        """Begin a new run, resetting all counters. Returns the run id."""
        run_id = run_id or uuid.uuid4().hex
        if not self._redis or not org_id or not connector_id:
            return run_id
        key = self._key(org_id, connector_id)
        now = int(time.time() * 1000)
        try:
            await self._redis.delete(key)
            await self._redis.hset(
                key,
                mapping={
                    "runId": run_id,
                    "phase": SyncPhase.DISCOVERING,
                    "discovered": 0,
                    "indexed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "total": 0,
                    "fullSync": "1" if full_sync else "0",
                    "startedAt": now,
                    "heartbeatAt": now,
                },
            )
            await self._redis.expire(key, self.TTL_SECONDS)
        except Exception as e:  # best-effort
            self.logger.debug(f"start_run failed for {connector_id}: {e}")
        return run_id

    async def add_discovered(self, org_id: str, connector_id: str, count: int = 1) -> None:
        if not self._redis or count <= 0 or not org_id or not connector_id:
            return
        key = self._key(org_id, connector_id)
        try:
            if not await self._redis.exists(key):
                return
            await self._redis.hincrby(key, "discovered", count)
            await self._touch(key)
        except Exception as e:
            self.logger.debug(f"add_discovered failed for {connector_id}: {e}")

    async def close_discovery(self, org_id: str, connector_id: str) -> None:
        """Freeze the run total to what was discovered and enter INDEXING."""
        if not self._redis or not org_id or not connector_id:
            return
        key = self._key(org_id, connector_id)
        try:
            if not await self._redis.exists(key):
                return
            discovered = int(await self._redis.hget(key, "discovered") or 0)
            await self._redis.hset(
                key, mapping={"phase": SyncPhase.INDEXING, "total": discovered}
            )
            await self._touch(key)
        except Exception as e:
            self.logger.debug(f"close_discovery failed for {connector_id}: {e}")

    async def record_result(
        self, org_id: str, connector_id: str, *, outcome: str
    ) -> None:
        """Record one record reaching a terminal indexing state.

        outcome is one of "indexed", "failed", "skipped". No-op when the
        connector has no tracked run (key absent).
        """
        field = {"indexed": "indexed", "failed": "failed", "skipped": "skipped"}.get(outcome)
        if not self._redis or not field or not org_id or not connector_id:
            return
        key = self._key(org_id, connector_id)
        try:
            if not await self._redis.exists(key):
                return
            await self._redis.hincrby(key, field, 1)
            await self._touch(key)
        except Exception as e:
            self.logger.debug(f"record_result failed for {connector_id}: {e}")

    async def get(self, org_id: str, connector_id: str) -> Optional[dict[str, Any]]:
        if not self._redis or not org_id or not connector_id:
            return None
        key = self._key(org_id, connector_id)
        try:
            raw = await self._redis.hgetall(key)
        except Exception as e:
            self.logger.debug(f"get sync progress failed for {connector_id}: {e}")
            return None
        if not raw:
            return None
        return self._normalize(raw)

    async def clear(self, org_id: str, connector_id: str) -> None:
        if not self._redis or not org_id or not connector_id:
            return
        try:
            await self._redis.delete(self._key(org_id, connector_id))
        except Exception as e:
            self.logger.debug(f"clear sync progress failed for {connector_id}: {e}")

    async def _touch(self, key: str) -> None:
        now = int(time.time() * 1000)
        await self._redis.hset(key, "heartbeatAt", now)
        await self._redis.expire(key, self.TTL_SECONDS)

    @staticmethod
    def _normalize(raw: dict[str, str]) -> dict[str, Any]:
        data: dict[str, Any] = dict(raw)
        for field in _NUMERIC_FIELDS:
            try:
                data[field] = int(raw.get(field, 0) or 0)
            except (TypeError, ValueError):
                data[field] = 0
        for field in ("startedAt", "heartbeatAt"):
            try:
                data[field] = int(raw.get(field, 0) or 0)
            except (TypeError, ValueError):
                data[field] = 0
        data["fullSync"] = str(raw.get("fullSync", "0")) == "1"
        return data


# One store (and one Redis connection) per process. Both the connectors and
# indexing services build this lazily from their own config_service.
_store_singleton: Optional[ConnectorSyncProgressStore] = None


async def get_connector_sync_progress_store(
    logger: logging.Logger, config_service: ConfigurationService
) -> Optional[ConnectorSyncProgressStore]:
    """Return the process-wide store, or None if Redis cannot be reached."""
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    try:
        _store_singleton = await ConnectorSyncProgressStore.create(logger, config_service)
    except Exception as e:
        logger.debug(f"Connector sync progress store unavailable: {e}")
        return None
    return _store_singleton
