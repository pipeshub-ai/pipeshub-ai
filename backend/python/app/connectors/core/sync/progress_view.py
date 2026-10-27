"""What a connector is doing, in one answer for the connector card and panel.

Two stages, read from two places:

- reading from the source: the running sync's snapshot and the run history,
  both on the app doc (``sync_progress``);
- making searchable: counted from record state, never from events, so a
  redelivered message cannot be counted twice.

``detail="summary"`` is what a card polls: the app doc plus a count of the
records not yet settled, one index seek. ``detail="full"`` adds the
connector's stats with the reasons records are not searchable, which walks the
connector and so is cached briefly. The run history is small and on the doc,
so both levels carry it.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import AppStatus, CollectionNames, ProgressStatus
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.sync_progress import parse_json_field, parse_run_history
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# No record written, checkpoint saved or phase entered for this long, and not
# waiting on a rate limit: the run is shown as making no progress.
STALL_AFTER_MS = 20 * 60 * 1000
# Indexing speed is measured over this window, and an estimate is given only
# once at least this many records finished in it: fewer is noise.
RATE_WINDOW_MS = 10 * 60 * 1000
MIN_RATE_SAMPLE = 20

_WAITING = (ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value, ProgressStatus.PAUSED.value)
_UNSETTLED = (*_WAITING, ProgressStatus.IN_PROGRESS.value, ProgressStatus.FAILED.value)
_SYNCING = (AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value)

_CACHE_TTL_S = 10.0
_CACHE_MAX = 2000
_cache: dict[tuple[str, str], tuple[float, Any]] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


async def _cached(key: tuple[str, str], load):  # noqa: ANN001, ANN202
    """One load per key per TTL however many viewers poll; the cache is per process."""
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        value = await load()
        if len(_cache) >= _CACHE_MAX:
            now = time.monotonic()
            for stale in [k for k, (exp, _) in _cache.items() if exp <= now]:
                _cache.pop(stale, None)
                _locks.pop(stale, None)
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
                # Keep only locks someone holds; a dropped one is just made again.
                for idle in [k for k, held in _locks.items() if not held.locked()]:
                    del _locks[idle]
        _cache[key] = (time.monotonic() + _CACHE_TTL_S, value)
        return value


def _live_run(doc: dict[str, Any], now: int) -> dict[str, Any] | None:
    """The running sync's snapshot, only while the connector says it is syncing.

    A snapshot left on an idle doc (a write that raced the run's end) is not shown.
    """
    if doc.get("status") not in _SYNCING:
        return None
    run = parse_json_field(doc.get(ConnectorStateKeys.SYNC_PROGRESS))
    if not run:
        return None
    throttled_until = run.get("throttledUntil")
    if not (isinstance(throttled_until, (int, float)) and throttled_until > now):
        throttled_until = None
    last_activity = run.get("lastActivityAt") or run.get("startedAt") or now
    return {
        **run,
        "throttledUntil": throttled_until,
        "stalled": throttled_until is None and now - int(last_activity) > STALL_AFTER_MS,
    }


def _indexing_summary(counts: dict[str, int], indexed_recently: int) -> dict[str, Any]:
    waiting = sum(counts.get(s, 0) for s in _WAITING)
    in_progress = counts.get(ProgressStatus.IN_PROGRESS.value, 0)
    backlog = waiting + in_progress
    per_minute = indexed_recently / (RATE_WINDOW_MS / 60000)
    eta_seconds = (
        int(backlog / per_minute * 60)
        if backlog and indexed_recently >= MIN_RATE_SAMPLE
        else None
    )
    return {
        "waiting": waiting,
        "inProgress": in_progress,
        "failed": counts.get(ProgressStatus.FAILED.value, 0),
        "indexedRecently": indexed_recently,
        "perMinute": round(per_minute, 1),
        "etaSeconds": eta_seconds,
        # Work is queued but nothing of this connector is moving: other work
        # (another connector, another organisation) is ahead of it.
        "waitingInLine": backlog > 0 and in_progress == 0 and indexed_recently == 0,
    }


async def build_progress_view(
    graph_provider: IGraphDBProvider,
    org_id: str,
    connector_id: str,
    *,
    detail: str = "summary",
) -> dict[str, Any] | None:
    """The progress answer for one connector, or None when it does not exist."""
    doc = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
    if not doc:
        return None
    now = get_epoch_timestamp_in_ms()
    since = now - RATE_WINDOW_MS

    async def load_indexing() -> dict[str, Any]:
        counts = await graph_provider.count_connector_records_by_status(connector_id, list(_UNSETTLED))
        indexed_recently = await graph_provider.count_records_indexed_since(connector_id, since)
        return _indexing_summary(counts, indexed_recently)

    indexing = await _cached(("summary", connector_id), load_indexing)
    runs = parse_run_history(doc.get(ConnectorStateKeys.SYNC_RUNS))
    view: dict[str, Any] = {
        "connectorId": connector_id,
        "status": doc.get("status") or AppStatus.IDLE.value,
        "isActive": bool(doc.get("isActive")),
        "run": _live_run(doc, now),
        "lastRun": runs[0] if runs else None,
        "recentRuns": runs,
        "lastError": parse_json_field(doc.get(ConnectorStateKeys.LAST_SYNC_ERROR)),
        "awaitingFirstSync": bool(doc.get(ConnectorStateKeys.AWAITING_FIRST_SYNC)),
        "readyAt": doc.get(ConnectorStateKeys.READY_AT),
        "indexing": indexing,
    }
    if detail == "full":

        async def load_stats() -> dict[str, Any] | None:
            result = await graph_provider.get_connector_stats(org_id, connector_id)
            return result.get("data") if result.get("success") else None

        view["stats"] = await _cached(("stats", connector_id), load_stats)
    return view
