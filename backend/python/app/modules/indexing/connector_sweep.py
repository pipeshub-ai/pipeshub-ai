"""One versioned pass over every connector's records, one page per tick.

Shared by ``record_label_repair`` and ``stored_content_heal``: one Redis
leader, connectors in key order, a resumable cursor and per-pass counters on
the app document, a bounded number of passes for a connector that hit
failures, and a version stamp so a finished connector is not swept again
until the version is bumped. A subclass decides what one page does.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.modules.indexing.vector_membership_backfill import LeaderLock
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_APPS = CollectionNames.APPS.value
_BACKOFF_FACTOR = 2
_MAX_BACKOFF_MULTIPLIER = 16


@dataclass(frozen=True)
class SweepFields:
    """Names of the sweep's fields on the app document."""

    state: str
    after_key: str
    attempts: str
    exhausted: str
    failures: str
    counters: tuple[str, ...]


@dataclass
class PageResult:
    counts: dict[str, int] = field(default_factory=dict)
    # The cursor when the page was left part done; the next tick resumes after it.
    stop_after: str | None = None
    lost_leadership: bool = False
    # Nothing was done and the cursor stays: try the same page again later.
    deferred: bool = False
    # Nothing was done and the cursor stays, but the wait is short: what the
    # page needs (e.g. capacity downstream) frees up on its own.
    waiting: bool = False


def int_field(value: Any) -> int:  # noqa: ANN401
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def key_of(doc: dict[str, Any]) -> str | None:
    key = doc.get("_key") or doc.get("id")
    return key if isinstance(key, str) and key else None


class ConnectorSweep(ABC):
    name: str
    version: str
    fields: SweepFields
    max_attempts: int
    # Logged when a connector is given up on: what is left undone.
    give_up_consequence: str

    def __init__(
        self,
        *,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        lock: LeaderLock,
        page_size: int,
    ) -> None:
        self.logger = logger
        self.graph = graph_provider
        self.lock = lock
        self.page_size = max(1, page_size)

    @abstractmethod
    async def process_page(
        self, app: dict[str, Any], app_key: str, rows: list[dict[str, Any]],
    ) -> PageResult: ...

    async def tick(self) -> str:
        """``not_leader``, ``no_apps``, ``idle`` (every connector done), ``page``,
        ``waiting`` or ``deferred``."""
        if not await self.lock.try_acquire():
            return "not_leader"
        apps = await self.graph.get_all_documents(_APPS)
        if not apps:
            return "no_apps"
        pending = sorted(
            (
                app for app in apps
                if key_of(app)
                and app.get(self.fields.state) != self.version
                and app.get("status") != APP_STATUS_DELETING
            ),
            key=lambda app: key_of(app) or "",
        )
        if not pending:
            return "idle"
        return await self._page(pending[0])

    async def _page(self, app: dict[str, Any]) -> str:
        fields = self.fields
        app_key = key_of(app) or ""
        after_key = app.get(fields.after_key)
        after_key = after_key if isinstance(after_key, str) and after_key else None
        rows = await self.graph.page_records_for_vector_membership_backfill(
            app_key, after_key, self.page_size,
        )
        result = await self.process_page(app, app_key, rows)
        if result.deferred:
            return "deferred"
        if result.waiting:
            return "waiting"
        if result.lost_leadership:
            self.logger.warning(
                "%s: lost leadership mid-page | connector=%s after=%s; "
                "the next leader resumes from the same cursor", self.name, app_key, after_key,
            )
            return "page"
        if not await self.lock.refresh():
            return "page"

        # The counters describe one pass: the first page of a pass starts them
        # again, whether the pass is new or a retry.
        totals = {
            name: int_field(result.counts.get(name)) + (int_field(app.get(name)) if after_key else 0)
            for name in (*fields.counters, fields.failures)
        }
        self.logger.info(
            "%s: page done | connector=%s after=%s records=%d %s", self.name, app_key, after_key,
            len(rows), " ".join(f"{k}={int_field(result.counts.get(k))}" for k in totals),
        )
        cursor = result.stop_after
        if cursor is None and len(rows) >= self.page_size:
            cursor = next((k for row in reversed(rows) if (k := key_of(row))), None)
        if cursor:
            await self.graph.update_node(app_key, _APPS, {fields.after_key: cursor, **totals})
            return "page"
        await self._finish(app, app_key, totals)
        return "page"

    async def _finish(self, app: dict[str, Any], app_key: str, totals: dict[str, int]) -> None:
        fields = self.fields
        failures = totals[fields.failures]
        attempts = int_field(app.get(fields.attempts)) + (1 if failures else 0)
        if failures and attempts < self.max_attempts:
            self.logger.warning(
                "%s: connector %s had %d failure(s); retrying from the start (attempt %d/%d)",
                self.name, app_key, failures, attempts, self.max_attempts,
            )
            await self.graph.update_node(app_key, _APPS, {
                fields.after_key: None, fields.attempts: attempts, **totals,
            })
            return
        if failures:
            self.logger.error(
                "%s: giving up on connector %s after %d attempts with %d failure(s); %s",
                self.name, app_key, attempts, failures, self.give_up_consequence,
            )
        else:
            self.logger.info(
                "%s: connector %s done | %s", self.name, app_key,
                " ".join(f"{k}={v}" for k, v in totals.items()),
            )
        await self.graph.update_node(app_key, _APPS, {
            fields.state: self.version,
            fields.after_key: None,
            fields.attempts: attempts,
            fields.exhausted: bool(failures),
            **totals,
        })


async def run_connector_sweep_loop(
    *,
    logger: Logger,
    name: str,
    make_lock: Callable[[], Awaitable[LeaderLock]],
    make_sweep: Callable[[LeaderLock], Awaitable[ConnectorSweep | None]],
    startup_grace_seconds: float,
    busy_interval_seconds: float,
    idle_interval_seconds: float,
    deferred_interval_seconds: float,
    error_interval_seconds: float,
    sleep: Callable[[float], Awaitable[Any]] | None = None,
) -> None:
    """Tick until every connector is done. ``make_sweep`` answers None while a
    dependency is not ready; the sweep is kept across a lost Redis connection."""
    # Resolved per call, not as a default: tests patch asyncio.sleep.
    sleep = sleep or asyncio.sleep
    logger.info("%s: starting in %.0fs", name, startup_grace_seconds)
    await sleep(startup_grace_seconds)

    lock: LeaderLock | None = None
    sweep: ConnectorSweep | None = None
    backoff = 1
    try:
        while True:
            interval = idle_interval_seconds
            try:
                if lock is None:
                    lock = await make_lock()
                if sweep is None:
                    sweep = await make_sweep(lock)
                if sweep is None:
                    logger.warning("%s: skipped this tick; a dependency is not ready", name)
                else:
                    sweep.lock = lock
                    outcome = await sweep.tick()
                    backoff = 1
                    if outcome == "idle":
                        logger.info("%s: every connector is done", name)
                        return
                    if outcome in ("page", "waiting"):
                        interval = busy_interval_seconds
                    elif outcome == "deferred":
                        interval = deferred_interval_seconds
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s: tick failed", name)
                if lock is not None:
                    await lock.close()
                    lock = None
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF_MULTIPLIER)
                interval = error_interval_seconds * backoff
            await sleep(interval)
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()


__all__ = [
    "ConnectorSweep",
    "PageResult",
    "SweepFields",
    "int_field",
    "key_of",
    "run_connector_sweep_loop",
]
