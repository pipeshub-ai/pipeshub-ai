"""The projection worker: drains the change log in large batches, off the writers' critical path.

A writer's transaction only appends rows to ``kg.graph_changes`` (the capture triggers, kg_0004). This module projects them: a page of the oldest committed rows of an
org is re-projected from the current graph under the per-(org, app) locks and acknowledged by key (:func:`drain`), page after page until the log is empty. The work is
idempotent and keyed by node, so a node logged many times is projected once per page, a row logged while a page runs is simply drained by the next, and two drainers
never interleave on one connector (the same advisory lock the inline flush and the backfill take).

A projection that gives up (``RuntimeError``: it kept reaching connectors it had not locked) writes nothing; the page stays in the log and the pass is retried later.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.kg.model import COLL_CODE
from app.services.graph_db.postgres.kg.project import decode, project
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
AHEAD = 5000                    # log rows past the page that are checked against what the page projected
PAGE = 25000                      # change-log keys per page: measured best (a page of 5,000 drained at less than half the rate of 25,000)
DRAINED = METRICS_BACKEND.counter("pipeshub_kg_drained_rows_total", "Change-log rows projected by the drainer", ["where"])
LAG = METRICS_BACKEND.counter("pipeshub_kg_drain_failed_total", "Drainer passes that failed and will be retried", ["where"])
RETRY_LATER = METRICS_BACKEND.counter("pipeshub_kg_drain_retry_later_total", "Drainer passes whose projection gave up reaching connectors it had not locked (nothing written, retried)", ["where"])


@dataclass
class DrainStats:
    rows: int = 0
    pages: int = 0
    seconds: float = 0.0

    @property
    def rows_per_s(self) -> float:
        return self.rows / self.seconds if self.seconds else 0.0


def _code_key(e: str) -> tuple[int, str]:
    code, _, nid = e.partition(":")
    return int(code), nid


async def drain(db: KernelDB, org: str, *, limit: int = 25000) -> int:
    """Project and acknowledge the oldest change-log rows of finished transactions that no flush took (written by the query or indexing role, or left by a failed
    flush), up to ``limit`` node keys (the first row is always taken). Runs as the connectors service in an org unit; returns the number of keys taken.
    A ``RuntimeError`` from the projection leaves the rows where they are and propagates: the caller retries later."""
    async def go(tx: Tx) -> int:
        rows = await tx.fetch(r"""/* pipeshub:kg.drain_read */ SELECT id, ends FROM (
            SELECT id, ends, sum(cardinality(ends)) OVER (ORDER BY txid, id) AS run FROM (
              SELECT id, txid, ends FROM kg.graph_changes WHERE org_id = $1 AND txid <> pg_current_xact_id() AND NOT EXISTS (SELECT 1 FROM unnest(ends) AS e WHERE e IS NULL OR e !~ '^[1-8]:[\s\S]+$' OR length(e) > 258) ORDER BY txid, id LIMIT 1000) q) r
            WHERE run - cardinality(ends) < $2 ORDER BY id""", org, limit)
        if not rows:
            return 0
        # The rows behind this page, as they are now: a row whose nodes this projection re-derives from the graph is satisfied by it (it was committed, with its graph change,
        # before the projection reads), so it is acknowledged with the page instead of projecting the same nodes again later. A group that appears pulls all its members in
        # (expansion), which in a bulk import is most of the log.
        ahead = await tx.fetch(r"""/* pipeshub:kg.drain_ahead */ SELECT id, ends FROM kg.graph_changes WHERE org_id = $1 AND txid <> pg_current_xact_id() AND NOT (id = ANY($2::bigint[])) AND NOT EXISTS (SELECT 1 FROM unnest(ends) AS e WHERE e IS NULL OR e !~ '^[1-8]:[\s\S]+$' OR length(e) > 258)
            ORDER BY id LIMIT $3""", org, [r["id"] for r in rows], AHEAD)
        st = await project(tx, org, decode(rows))
        covered = {(COLL_CODE[k[0]], k[1]) for k in st.keys}
        done = [r["id"] for r in rows] + [r["id"] for r in ahead if all(_code_key(e) in covered for e in r["ends"])]
        await KgWriter(tx).changes_delete(done)
        return sum(len(r["ends"]) for r in rows)
    return await db.run(go, write=True)


def _ctx(org: str) -> OrgContext:
    return OrgContext(org, None, "job", "job:kg-drain", "kg-drain")


async def drain_org(db: KernelDB, org: str, *, page: int = PAGE, max_rows: int | None = None, stop: asyncio.Event | None = None) -> DrainStats:
    """Page after page until the org's log has no committed row left (or ``max_rows`` were taken). ``stop``: checked before each page, never inside one (a page commits or rolls back
    whole, in a unit of its own), so a shutdown leaves what is not drained in the log for the next start."""
    st = DrainStats()
    t0 = time.perf_counter()
    with bind(_ctx(org)):
        while (max_rows is None or st.rows < max_rows) and not (stop is not None and stop.is_set()):
            n = await drain(db, org, limit=page)
            if not n:
                break
            st.rows += n
            st.pages += 1
    st.seconds = time.perf_counter() - t0
    DRAINED.inc("drain", value=st.rows)
    return st


async def lag_in(tx: Tx, org: str) -> int:
    """Committed change-log keys not yet projected for the org, in the caller's unit (rows the drain cannot read are counted by ``quarantined``, not here)."""
    return int(await tx.fetchval(r"/* pipeshub:kg.lag */ SELECT COALESCE(sum(cardinality(ends)), 0) FROM kg.graph_changes WHERE org_id = $1 AND NOT EXISTS (SELECT 1 FROM unnest(ends) AS e WHERE e IS NULL OR e !~ '^[1-8]:[\s\S]+$' OR length(e) > 258)", org))


async def lag(db: KernelDB, org: str) -> int:
    """Committed change-log keys not yet projected for the org (rows the drain cannot read are counted by ``quarantined``, not here)."""
    with bind(_ctx(org)):
        async with db.uow() as tx:
            return await lag_in(tx, org)


async def quarantined(tx: Tx, org: str) -> list[int]:
    """Ids of the change-log rows the drain cannot read (kept in the log, out of every page)."""
    return [r["id"] for r in await tx.fetch(r"/* pipeshub:kg.poison */ SELECT id FROM kg.graph_changes WHERE org_id = $1 AND NOT NOT EXISTS (SELECT 1 FROM unnest(ends) AS e WHERE e IS NULL OR e !~ '^[1-8]:[\s\S]+$' OR length(e) > 258) ORDER BY id", org)]


async def purge_quarantined(db: KernelDB, org: str) -> int:
    """The operator's decision to drop unreadable rows (after looking at them): the nodes they named are repaired by a backfill."""
    async def go(tx: Tx) -> int:
        ids = await quarantined(tx, org)
        return await KgWriter(tx).changes_delete(ids) if ids else 0
    with bind(_ctx(org)):
        return await db.run(go, write=True)


async def drain_or_retry(db: KernelDB, org: str, *, page: int = PAGE, stop: asyncio.Event | None = None) -> int:
    """One pass over an org that never raises: the rows taken, or 0 after a failure. A projection that gave up (``RuntimeError``) wrote nothing and leaves its rows in the log:
    it is counted and logged, and the next pass tries again. Any other error is counted as a failed pass and retried the same way."""
    try:
        return (await drain_org(db, org, page=page, stop=stop)).rows
    except RuntimeError:
        RETRY_LATER.inc("drain")
        logger.warning("kg drain gave up for an org (connectors it had not locked); the rows stay in the log, retrying later")
    except Exception:
        LAG.inc("drain")
        logger.exception("kg drain failed for an org; retrying")
    return 0


async def run(db: KernelDB, orgs: Callable[[], Awaitable[list[str]]], *, page: int = PAGE, idle_s: float = 0.5, stop: asyncio.Event | None = None) -> None:
    """Drain every org ``orgs()`` names, forever (until ``stop`` is set): a pass per org, then a pause when nothing was left or the pass failed."""
    stop = stop or asyncio.Event()
    while not stop.is_set():
        took = 0
        for org in await orgs():
            took += await drain_or_retry(db, org, page=page)
        if not took:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), idle_s)
