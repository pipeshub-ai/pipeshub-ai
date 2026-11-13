"""`ScopeSweep`: one pass over an organisation's connectors that closes what a dead process left and finishes what is pending.

For each connector that has something to do, in this order, each step on its own:

1. **Recover** the writes whose lease ran out (`structure_generation.recover_expired_writes`): their roots are registered, or the connector is marked
   for a full pass.
2. **Rewrite** the pending roots (`ScopeRewriteJob`). A connector another rewriter holds is skipped for this pass.
3. **Full pass** for a connector that is still not current after that (the change reached more than subtrees, or recovery marked it) and that an
   operator has back-filled before: `FULL_PASS_PAGES` pages of the back-fill, resumed by the next pass. A connector nobody ever back-filled is
   reported, not started.

A connector that fails is recorded on its app document (`SWEEP_KEY`: failures, the error, when to try again) and retried after a wait that doubles
up to an hour; the others go on. A failure never reaches the caller and never touches a sync: nothing here runs in a sync, and a connector that
fails for ever costs one attempt an hour and shows in the operator view (`scope_view`). `sweep_forever` is the loop the indexing service runs (when the
scope fields are on) and the command runs with `--interval`; several replicas may run it, since every step is a compare-and-set or under the connector's lease.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
from app.services.permissions.currency import app_is_current
from app.services.permissions.pending import PendingUnreadable, parse
from app.services.permissions.structure_generation import (
    OPEN_WRITES_KEY,
    PENDING_KEY,
    OpenWritesUnreadable,
    parse_open_writes,
    recover_expired_writes,
    unclosed_writes,
)
from app.services.vector_db.scope_backfill import (
    BACKFILL_KEYS,
    DONE,
    ConnectorBusy,
    ScopeBackfill,
)
from app.services.vector_db.scope_rewrite import ScopeRewriteJob

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Iterable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.vector_db.interface.vector_db import IVectorDBService
    from app.services.vector_db.membership import CollectionLocator

SWEEP_KEY = "scopeSweepState"
BACKOFF_FIRST_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
FULL_PASS_PAGES = 5  # back-fill pages per connector per pass; the next pass resumes


@dataclass(frozen=True)
class SweepState:
    failures: int = 0
    error: str | None = None
    retry_at: int = 0


def read_state(raw: object) -> SweepState:
    """A value that does not parse is no history (the next failure writes a fresh one): it must not stop a connector being swept."""
    try:
        held = json.loads(raw) if isinstance(raw, str) and raw else {}
        return SweepState(int(held.get("n", 0)), held.get("e"), int(held.get("t", 0)))
    except (ValueError, TypeError, AttributeError):
        return SweepState()


@dataclass(frozen=True)
class SweepRow:
    connector_id: str
    #: "idle", "done" (rewritten), "recovered", "busy", "changed", "full_pass" (a pass ran and finished), "full_pass_running" (it resumes next time),
    #: "deleting" (left to the delete), "open_write" (a write that opened the connector is still inside its lease), "open_write_unleased", "writes_unreadable" and "needs_full_pass"
    #: (never back-filled: the operator has to act), "waiting" (backing off after a failure), "failed"
    action: str
    detail: str = ""


class ScopeSweep:
    def __init__(self, graph_provider: IGraphDBProvider, vector_db: IVectorDBService, locator: CollectionLocator, *,
                 logger: logging.Logger | None = None, clock: Callable[[], float] | None = None,
                 full_pass_pages: int = FULL_PASS_PAGES) -> None:
        self._graph, self._injected, self._clock, self._pages = graph_provider, clock, clock or time.time, full_pass_pages
        self._log = logger or logging.getLogger(__name__)
        self._job = ScopeRewriteJob(graph_provider, vector_db, locator, logger=self._log, clock=self._clock)
        self._backfill = ScopeBackfill(graph_provider, vector_db, locator, self._log, clock=self._clock)

    async def run_once(self, org_id: str, connectors: Collection[str] | None = None) -> list[SweepRow]:
        """Sweep the organisation's connectors (or only ``connectors``). Raises only when the connectors cannot be listed."""
        with for_org(org_id, job="scope-sweep"):
            apps = await self._graph.get_org_apps(org_id, active_only=False, raise_on_error=True) or []
            rows = []
            for app in sorted(apps, key=lambda a: str(a.get("_key") or a.get("id"))):
                connector_id = str(app.get("_key") or app.get("id"))
                if connectors is None or connector_id in connectors:
                    rows.append(await self._one(org_id, connector_id))
            return rows

    async def _one(self, org_id: str, connector_id: str) -> SweepRow:
        try:
            app = await self._app(connector_id)
            if app.get("status") == APP_STATUS_DELETING:      # the delete owns its records and the app document: nothing here writes to either
                return SweepRow(connector_id, "deleting")
            state = read_state(app.get(SWEEP_KEY))
            if not self._has_work(app):
                if state.failures or state.error:      # whatever failed is no longer there: stop showing it
                    await self._record(connector_id, None)
                return SweepRow(connector_id, "idle")
            if state.retry_at > self._clock():
                return SweepRow(connector_id, "waiting", f"{state.failures} failure(s), next try in {int(state.retry_at - self._clock())} s: {state.error}")
            row = await self._work(org_id, connector_id)
            if row.action in ("done", "recovered", "full_pass", "idle") and (state.failures or state.error):
                await self._record(connector_id, None)
            return row
        except Exception as exc:      # one connector's failure never stops the others
            return await self._failed(connector_id, exc)

    @staticmethod
    def _has_work(app: dict[str, Any]) -> bool:
        """Roots to rewrite, writes that opened the connector, or a connector that was once current (an operator ran the back-fill) and is not."""
        return bool(app.get(PENDING_KEY) or app.get(OPEN_WRITES_KEY) or (app.get(BACKFILL_KEYS["state"]) is not None and not app_is_current(app)))

    async def _work(self, org_id: str, connector_id: str) -> SweepRow:
        try:
            recovered = await recover_expired_writes(self._graph, connector_id, clock=self._injected)
        except Exception:      # a recovery that fails must not keep pending roots from being rewritten; the next pass tries it again
            self._log.exception("could not recover the expired writes of %s", connector_id)
            recovered = 0
        app = await self._app(connector_id)
        try:
            roots = parse(app.get(PENDING_KEY))
        except PendingUnreadable as exc:
            raise RuntimeError("the pending roots do not parse; a full pass clears them") from exc
        # Nothing is rewritten for a connector nobody back-filled: no release is possible without a finished pass, so the walk would only repeat.
        if roots and app.get(BACKFILL_KEYS["state"]) is not None:
            result = await self._job.run_connector(org_id, connector_id)
            if result.state in ("busy", "changed"):
                return SweepRow(connector_id, result.state)
            app = await self._app(connector_id)
        if app_is_current(app):
            return SweepRow(connector_id, "done" if roots else "recovered" if recovered else "idle")
        in_flight = unclosed_writes(app)
        if in_flight is None:      # the counters do not read: only the operator's close can say
            return SweepRow(connector_id, "writes_unreadable", "the opened and closed counters do not read: run the back-fill with --close-open-mark once no write is running")
        if in_flight:      # a write is running (or its lease has not run out): wait for it, unless nothing will ever close it
            try:
                unleased = in_flight - len(parse_open_writes(app.get(OPEN_WRITES_KEY)))
            except OpenWritesUnreadable:
                unleased = in_flight
            if unleased > 0:
                return SweepRow(connector_id, "open_write_unleased", f"{unleased} write(s) have no lease to run out: run the back-fill with --close-open-mark once none is running")
            return SweepRow(connector_id, "open_write", "waiting for a write that opened the connector, or for its lease to run out")
        if app.get(BACKFILL_KEYS["state"]) is None:      # never back-filled: not started uninvited
            return SweepRow(connector_id, "needs_full_pass", "the connector was never back-filled: run the back-fill on it once")
        return await self._full_pass(org_id, connector_id)

    async def _full_pass(self, org_id: str, connector_id: str) -> SweepRow:
        try:
            progress = await self._backfill.run_connector(org_id, connector_id, max_pages=self._pages)
        except ConnectorBusy:
            return SweepRow(connector_id, "busy")
        if progress.state == DONE:
            return SweepRow(connector_id, "full_pass", f"{progress.records} record(s), {progress.stale} on the stale marker")
        if progress.state == "failed":
            raise RuntimeError(f"the back-fill failed at {progress.after}: {progress.error}")
        return SweepRow(connector_id, "full_pass_running", f"{progress.records} record(s) so far")

    async def _app(self, connector_id: str) -> dict[str, Any]:
        return dict(await self._graph.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True) or {})

    async def _failed(self, connector_id: str, exc: Exception) -> SweepRow:
        self._log.warning("scope sweep of %s failed: %s: %s", connector_id, type(exc).__name__, exc)
        try:
            before = read_state((await self._app(connector_id)).get(SWEEP_KEY))
            failures = before.failures + 1
            wait = min(BACKOFF_FIRST_SECONDS * 2 ** (failures - 1), BACKOFF_MAX_SECONDS)
            await self._record(connector_id, SweepState(failures, f"{type(exc).__name__}: {exc}"[:300], int(self._clock()) + wait))
        except Exception:
            self._log.exception("could not record the failure of the scope sweep of %s", connector_id)
        return SweepRow(connector_id, "failed", f"{type(exc).__name__}: {exc}")

    async def _record(self, connector_id: str, state: SweepState | None) -> None:
        value = None if state is None else json.dumps({"n": state.failures, "e": state.error, "t": state.retry_at}, separators=(",", ":"))
        await self._graph.update_node(connector_id, CollectionNames.APPS.value, {SWEEP_KEY: value})


async def sweep_forever(sweep: ScopeSweep, orgs: Callable[[], Awaitable[Iterable[str]]], interval: float, *, log: logging.Logger,
                        connectors: Collection[str] | None = None, report: Callable[[str, list[SweepRow]], None] | None = None,
                        rounds: int | None = None, sleep: Callable[[float], Awaitable[None]] | None = None) -> bool:
    """Sweep every organisation, then wait ``interval`` seconds, again and again (``rounds`` times, or until cancelled). A listing that fails (the
    organisations, or one organisation's connectors) is logged and the loop goes on at the next round: a transient error must not end it.
    Returns whether the last round had a failure (a connector's, or a listing)."""
    done, failed = 0, False
    while True:
        failed = False
        try:
            for org in list(await orgs()):
                try:
                    rows = await sweep.run_once(org, connectors)
                except Exception:
                    log.exception("scope sweep of organisation %s: its connectors could not be listed", org)
                    failed = True
                    continue
                failed = failed or any(r.action == "failed" for r in rows)
                if report is not None:
                    report(org, rows)
        except Exception:
            log.exception("scope sweep: the organisations could not be listed")
            failed = True
        done += 1
        if rounds is not None and done >= rounds:
            return failed
        await (sleep or asyncio.sleep)(interval)
