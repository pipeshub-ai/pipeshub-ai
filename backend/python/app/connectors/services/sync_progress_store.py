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
# indexer, dropped Kafka messages) so the UI does not spin forever. Indexers
# heartbeat while processing, so this is a true liveness signal rather than a
# limit on the duration of a single large document.
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
    # Counter updates sit on the per-record hot path. Keep the existence guard,
    # increment, heartbeat and TTL refresh atomic and to one Redis round-trip.
    _INCREMENT_IF_PRESENT_SCRIPT = """
        if redis.call('EXISTS', KEYS[1]) == 0 then
            return 0
        end
        if ARGV[1] ~= '' and redis.call('HGET', KEYS[1], 'runId') ~= ARGV[1] then
            return 0
        end
        if ARGV[5] ~= '' then
            if redis.call('SADD', KEYS[2], ARGV[5]) == 0 then
                return 0
            end
            redis.call('EXPIRE', KEYS[2], ARGV[6])
        end
        redis.call('HINCRBY', KEYS[1], ARGV[2], ARGV[3])
        redis.call('HSET', KEYS[1], 'heartbeatAt', ARGV[4])
        redis.call('EXPIRE', KEYS[1], ARGV[6])
        return 1
    """
    _CLOSE_DISCOVERY_SCRIPT = """
        if redis.call('EXISTS', KEYS[1]) == 0 then
            return 0
        end
        if ARGV[1] ~= '' and redis.call('HGET', KEYS[1], 'runId') ~= ARGV[1] then
            return 0
        end
        local discovered = redis.call('HGET', KEYS[1], 'discovered') or '0'
        redis.call('HSET', KEYS[1],
            'phase', ARGV[2],
            'total', discovered,
            'heartbeatAt', ARGV[3])
        redis.call('EXPIRE', KEYS[1], ARGV[4])
        return 1
    """

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
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=2,
        )
        return cls(logger, redis_client)

    def _key(self, org_id: str, connector_id: str) -> str:
        return f"{self.KEY_PREFIX}{org_id}:{connector_id}"

    def _outcomes_key(self, org_id: str, connector_id: str, run_id: str) -> str:
        return f"{self._key(org_id, connector_id)}:outcomes:{run_id}"

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

    async def add_discovered(
        self, org_id: str, connector_id: str, count: int = 1, *, run_id: str | None = None
    ) -> None:
        if not self._redis or count <= 0 or not org_id or not connector_id:
            return
        key = self._key(org_id, connector_id)
        try:
            await self._increment_if_present(key, "discovered", count, expected_run_id=run_id)
        except Exception as e:
            self.logger.debug(f"add_discovered failed for {connector_id}: {e}")

    async def close_discovery(
        self,
        org_id: str,
        connector_id: str,
        *,
        expected_run_id: Optional[str] = None,
    ) -> None:
        """Freeze the run total to what was discovered and enter INDEXING.

        When ``expected_run_id`` is given, this is a no-op unless it matches the
        run currently stored. This prevents a cancelled/superseded sync task's
        cleanup from closing discovery on the newer run that replaced it.
        """
        if not self._redis or not org_id or not connector_id:
            return
        key = self._key(org_id, connector_id)
        try:
            now = int(time.time() * 1000)
            await self._redis.eval(
                self._CLOSE_DISCOVERY_SCRIPT,
                1,
                key,
                expected_run_id or "",
                SyncPhase.INDEXING,
                now,
                self.TTL_SECONDS,
            )
        except Exception as e:
            self.logger.debug(f"close_discovery failed for {connector_id}: {e}")

    async def record_result(
        self,
        org_id: str,
        connector_id: str,
        *,
        outcome: str,
        run_id: str | None = None,
        record_id: str | None = None,
        count: int = 1,
    ) -> None:
        """Record one record reaching a terminal indexing state.

        outcome is one of "indexed", "failed", "skipped". No-op when the
        connector has no tracked run (key absent).
        """
        field = {"indexed": "indexed", "failed": "failed", "skipped": "skipped"}.get(outcome)
        if not self._redis or not field or not org_id or not connector_id or count <= 0:
            return
        key = self._key(org_id, connector_id)
        try:
            outcomes_key = self._outcomes_key(org_id, connector_id, run_id) if run_id and record_id else ""
            await self._increment_if_present(
                key,
                field,
                count,
                expected_run_id=run_id,
                outcome_key=outcomes_key,
                outcome_id=f"{outcome}:{record_id}" if record_id else None,
            )
        except Exception as e:
            self.logger.debug(f"record_result failed for {connector_id}: {e}")

    async def is_current_run(
        self, org_id: str, connector_id: str, run_id: Optional[str]
    ) -> bool:
        """True if ``run_id`` is still the run stored for this connector.

        Returns True when we cannot tell (Redis unavailable, no run_id, or no
        run stored) so callers fall back to their pre-run-scoped behaviour and
        never get stuck. A superseding run always writes a fresh ``runId`` via
        ``start_run`` before the old task's cleanup runs, so a mismatch here
        reliably means "someone else owns this connector now".
        """
        if not self._redis or not org_id or not connector_id or not run_id:
            return True
        key = self._key(org_id, connector_id)
        try:
            current_run_id = await self._redis.hget(key, "runId")
        except Exception as e:
            self.logger.debug(f"is_current_run failed for {connector_id}: {e}")
            return True
        if current_run_id is None:
            return True
        return current_run_id == run_id

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
            key = self._key(org_id, connector_id)
            run_id = await self._redis.hget(key, "runId")
            keys = [key]
            if run_id:
                keys.append(self._outcomes_key(org_id, connector_id, run_id))
            await self._redis.delete(*keys)
        except Exception as e:
            self.logger.debug(f"clear sync progress failed for {connector_id}: {e}")

    async def _increment_if_present(
        self,
        key: str,
        field: str,
        count: int,
        *,
        expected_run_id: str | None = None,
        outcome_key: str = "",
        outcome_id: str | None = None,
    ) -> None:
        now = int(time.time() * 1000)
        await self._redis.eval(
            self._INCREMENT_IF_PRESENT_SCRIPT,
            2,
            key,
            outcome_key or f"{key}:noop",
            expected_run_id or "",
            field,
            count,
            now,
            outcome_id or "",
            self.TTL_SECONDS,
        )

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
