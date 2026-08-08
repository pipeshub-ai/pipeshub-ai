"""Org-scoped indexing queue snapshot for sync-progress UI.

Sums remaining work across this org's connector sync-progress Redis hashes.
Never exposes deployment-wide Redis Streams / Kafka consumer lag (that would
leak other tenants' backlog size). Best-effort: Redis errors return ``None``
so the UI hides the line.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.connectors.services.sync_progress_store import (
    STALE_THRESHOLD_MS,
    SyncPhase,
)

# Per-org sample used to derive a rough drain rate across progress polls.
_THROUGHPUT_SAMPLE_KEY_PREFIX = "indexing_queue:throughput_sample:"
_MIN_SAMPLE_INTERVAL_SECONDS = 5.0
# Card lists poll sync-progress per connector; reuse one org scan briefly.
_SNAPSHOT_CACHE_TTL_SECONDS = 2.0
# Cache keyed by org_id so tenants never share a snapshot.
_snapshot_cache: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}

_PROGRESS_KEY_PREFIX = "connector_sync_progress:"


def clear_indexing_queue_snapshot_cache() -> None:
    """Drop the in-process snapshot cache (tests / after Redis reconnect)."""
    _snapshot_cache.clear()


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value) if value is not None else ""


def _int_field(data: dict[str, Any], key: str, default: int = 0) -> int:
    raw = data.get(key, default)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return int(raw or default)
    except (TypeError, ValueError):
        return default


def _remaining_for_run(run: dict[str, Any]) -> int:
    """Remaining indexable work for one connector run hash."""
    phase = _decode(run.get("phase") or SyncPhase.IDLE)
    if phase in (SyncPhase.IDLE, SyncPhase.DONE, SyncPhase.FAILED, ""):
        return 0

    heartbeat = _int_field(run, "heartbeatAt")
    if heartbeat:
        now_ms = int(time.time() * 1000)
        if (now_ms - heartbeat) > STALE_THRESHOLD_MS:
            return 0

    discovered = _int_field(run, "discovered")
    indexed = _int_field(run, "indexed")
    failed = _int_field(run, "failed")
    skipped = _int_field(run, "skipped")
    total = _int_field(run, "total")
    processed = indexed + failed + skipped
    # While discovering, total is unset — use discovered as the work queued so far.
    denominator = total if total > 0 else discovered
    return max(0, denominator - processed)


async def _scan_org_progress_keys(redis_client: Any, org_id: str) -> list[str]:
    pattern = f"{_PROGRESS_KEY_PREFIX}{org_id}:*"
    keys: list[str] = []
    cursor: int | bytes = 0
    while True:
        cursor, batch = await redis_client.scan(cursor, match=pattern, count=100)
        for key in batch or []:
            key_str = _decode(key)
            # Outcomes sets are sibling keys; they are not run hashes.
            if ":outcomes:" in key_str:
                continue
            keys.append(key_str)
        if cursor == 0 or cursor == b"0" or cursor == "0":
            break
    return keys


async def _sum_org_backlog(redis_client: Any, org_id: str) -> int:
    keys = await _scan_org_progress_keys(redis_client, org_id)
    backlog = 0
    for key in keys:
        try:
            raw = await redis_client.hgetall(key)
        except Exception:
            continue
        if not raw:
            continue
        # Normalize bytes keys from redis-py.
        run = {_decode(k): v for k, v in raw.items()}
        backlog += _remaining_for_run(run)
    return backlog


async def fetch_indexing_queue_snapshot(
    redis_client: Any, org_id: str
) -> Optional[dict[str, Any]]:
    """Return ``{lag, pending, etaSeconds}`` for this org, or ``None``.

    ``lag`` carries the org backlog (FE still sums lag+pending). ``pending``
    is always 0 — PEL is deployment-wide and must not leak.
    """
    if redis_client is None or not org_id:
        return None

    now = time.time()
    cached = _snapshot_cache.get(org_id)
    if cached is not None:
        cached_at, snap = cached
        if now - cached_at < _SNAPSHOT_CACHE_TTL_SECONDS:
            return snap

    try:
        backlog = await _sum_org_backlog(redis_client, org_id)
    except Exception:
        _snapshot_cache[org_id] = (now, None)
        return None

    eta_seconds = await _estimate_eta_seconds(redis_client, org_id, backlog)
    snap = {
        "lag": backlog,
        "pending": 0,
        "etaSeconds": eta_seconds,
    }
    _snapshot_cache[org_id] = (now, snap)
    return snap


async def _estimate_eta_seconds(
    redis_client: Any, org_id: str, backlog: int
) -> Optional[int]:
    """Rough ETA from org backlog drain rate across progress polls."""
    if backlog <= 0:
        return 0
    sample_key = f"{_THROUGHPUT_SAMPLE_KEY_PREFIX}{org_id}"
    now = time.time()
    try:
        prev = await redis_client.hgetall(sample_key)
    except Exception:
        prev = {}

    # Normalize bytes.
    if prev:
        prev = {_decode(k): _decode(v) for k, v in prev.items()}

    rate: Optional[float] = None
    if prev:
        try:
            prev_lag = int(prev.get("lag", 0) or 0)
            prev_at = float(prev.get("at", 0) or 0)
        except (TypeError, ValueError):
            prev_lag, prev_at = 0, 0.0
        dt = now - prev_at
        if prev_at > 0 and dt >= _MIN_SAMPLE_INTERVAL_SECONDS:
            drained = prev_lag - backlog
            if drained > 0:
                rate = drained / dt

    try:
        prev_at = float((prev or {}).get("at", 0) or 0)
    except (TypeError, ValueError):
        prev_at = 0.0
    if prev_at <= 0 or (now - prev_at) >= _MIN_SAMPLE_INTERVAL_SECONDS:
        try:
            await redis_client.hset(
                sample_key,
                mapping={"lag": backlog, "at": str(now)},
            )
            await redis_client.expire(sample_key, 3600)
        except Exception:
            pass

    if rate is None or rate <= 0:
        return None
    return max(1, int(round(backlog / rate)))


async def get_indexing_queue_for_progress(
    logger: logging.Logger, redis_client: Any, org_id: str
) -> Optional[dict[str, Any]]:
    """Wrapper that never raises — sync-progress must stay available."""
    try:
        return await fetch_indexing_queue_snapshot(redis_client, org_id)
    except Exception as exc:
        logger.debug("Indexing queue snapshot unavailable: %s", exc)
        return None
