"""Org-wide indexing queue snapshot for sync-progress UI.

Reads Redis Streams consumer-group lag for ``record-events``. Best-effort:
Kafka deployments (or Redis errors) return ``None`` so the UI hides the line.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.services.messaging.config import Topic

RECORD_EVENTS_STREAM = Topic.RECORD_EVENTS.value
RECORDS_CONSUMER_GROUP = "records_consumer_group"
# Shared sample used to derive a rough drain rate across progress polls.
_THROUGHPUT_SAMPLE_KEY = "indexing_queue:throughput_sample"
_MIN_SAMPLE_INTERVAL_SECONDS = 5.0
# Card lists poll sync-progress per connector; reuse one XINFO for a few seconds
# so N concurrent polls don't each hit Redis Streams admin commands.
_SNAPSHOT_CACHE_TTL_SECONDS = 2.0
_snapshot_cache: tuple[float, Optional[dict[str, Any]]] | None = None


def _as_group_dict(group: Any) -> dict[str, Any]:
    if isinstance(group, dict):
        return group
    # redis-py may return a flat list [name, val, name, val, ...]
    if isinstance(group, (list, tuple)):
        out: dict[str, Any] = {}
        for i in range(0, len(group) - 1, 2):
            out[str(group[i])] = group[i + 1]
        return out
    return {}


def _int_field(group: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(group.get(key, default) or 0)
    except (TypeError, ValueError):
        return default


async def fetch_indexing_queue_snapshot(redis_client: Any) -> Optional[dict[str, Any]]:
    """Return ``{lag, pending, etaSeconds}`` or ``None`` when unavailable."""
    global _snapshot_cache
    if redis_client is None:
        return None

    now = time.time()
    if _snapshot_cache is not None:
        cached_at, cached = _snapshot_cache
        if now - cached_at < _SNAPSHOT_CACHE_TTL_SECONDS:
            return cached

    try:
        groups = await redis_client.xinfo_groups(RECORD_EVENTS_STREAM)
    except Exception:
        _snapshot_cache = (now, None)
        return None

    target: Optional[dict[str, Any]] = None
    for group in groups or []:
        parsed = _as_group_dict(group)
        if parsed.get("name") == RECORDS_CONSUMER_GROUP:
            target = parsed
            break
    if target is None:
        _snapshot_cache = (now, None)
        return None

    lag = max(0, _int_field(target, "lag"))
    pending = max(0, _int_field(target, "pending"))
    eta_seconds = await _estimate_eta_seconds(redis_client, lag)
    snap = {
        "lag": lag,
        "pending": pending,
        "etaSeconds": eta_seconds,
    }
    _snapshot_cache = (now, snap)
    return snap


async def _estimate_eta_seconds(redis_client: Any, lag: int) -> Optional[int]:
    """Rough ETA from lag drain rate across progress polls. None if unknown."""
    if lag <= 0:
        return 0
    now = time.time()
    try:
        prev = await redis_client.hgetall(_THROUGHPUT_SAMPLE_KEY)
    except Exception:
        prev = {}

    rate: Optional[float] = None
    if prev:
        try:
            prev_lag = int(prev.get("lag", 0) or 0)
            prev_at = float(prev.get("at", 0) or 0)
        except (TypeError, ValueError):
            prev_lag, prev_at = 0, 0.0
        dt = now - prev_at
        if prev_at > 0 and dt >= _MIN_SAMPLE_INTERVAL_SECONDS:
            drained = prev_lag - lag
            if drained > 0:
                rate = drained / dt

    # Refresh the sample when enough time has passed (or first write).
    try:
        prev_at = float((prev or {}).get("at", 0) or 0)
    except (TypeError, ValueError):
        prev_at = 0.0
    if prev_at <= 0 or (now - prev_at) >= _MIN_SAMPLE_INTERVAL_SECONDS:
        try:
            await redis_client.hset(
                _THROUGHPUT_SAMPLE_KEY,
                mapping={"lag": lag, "at": str(now)},
            )
            await redis_client.expire(_THROUGHPUT_SAMPLE_KEY, 3600)
        except Exception:
            pass

    if rate is None or rate <= 0:
        return None
    return max(1, int(round(lag / rate)))


async def get_indexing_queue_for_progress(
    logger: logging.Logger, redis_client: Any
) -> Optional[dict[str, Any]]:
    """Wrapper that never raises — sync-progress must stay available."""
    try:
        return await fetch_indexing_queue_snapshot(redis_client)
    except Exception as exc:
        logger.debug("Indexing queue snapshot unavailable: %s", exc)
        return None
