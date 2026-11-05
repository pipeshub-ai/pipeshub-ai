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
from app.services.graph_db.postgres.kg.model import CODE_EPOCH, COLL_CODE
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


async def drain_org(db: KernelDB, org: str, *, page: int = PAGE, max_rows: int | None = None) -> DrainStats:
    """Page after page until the org's log has no committed row left (or ``max_rows`` were taken)."""
    st = DrainStats()
    t0 = time.perf_counter()
    with bind(_ctx(org)):
        while max_rows is None or st.rows < max_rows:
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


async def is_current(tx: Tx, org: str, app_id: str) -> bool:
    """May a reader trust kg for this connector? Its copy is verified (``shadow`` or ``enforce``), no row of the log is unreadable, and no change is waiting that can alter it: none logged
    for it or for the org as a whole (edges, principals), and none logged for another connector while either of the two links records across connectors (``cross_app``: a record's
    parent or group belongs to the other, so a change there reaches this one). A pending connector kg has never seen may link anything, so it is not current either. A node that changed
    connector is logged under both, so neither is current. A connector with a queued restamp job that hides (``fenced``) is not current until the job is done: the stamps of what the change reached are outdated and hidden, and a connector
    answered from them would be wrong for those records. The marks on single records are the reader's own predicate as well (restamp.HIDDEN); the connector-level check is the stricter one."""
    st = await tx.fetchrow("""/* pipeshub:kg.current_mode */ SELECT s.mode, s.cross_app FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
        WHERE s.org_id = $1 AND k.coll = 1 AND k.id = $2""", org, app_id)
    if st is None or st["mode"] not in ("shadow", "enforce"):
        return False
    if await tx.fetchval("""/* pipeshub:kg.app_fenced */ SELECT EXISTS (SELECT 1 FROM kg.restamp_queue q JOIN kg.node_keys k ON k.org_id = q.org_id AND k.sk = q.app_sk
            WHERE q.org_id = $1 AND k.coll = 1 AND k.id = $2 AND q.fenced)""", org, app_id):
        return False                                  # a restamp job hides what the connector's stamps no longer say
    if await tx.fetchval(r"/* pipeshub:kg.poison_any */ SELECT EXISTS (SELECT 1 FROM kg.graph_changes WHERE org_id = $1 AND EXISTS (SELECT 1 FROM unnest(ends) AS e WHERE e IS NULL OR e !~ '^[1-8]:[\s\S]+$' OR length(e) > 258))", org):
        return False                                  # a row that cannot be read may name anything
    pending = [r["app_id"] for r in await tx.fetch("/* pipeshub:kg.pending_apps */ SELECT DISTINCT app_id FROM kg.graph_changes WHERE org_id = $1", org)]
    if any(a in (app_id, "") for a in pending):
        return False
    if not pending:
        return True
    if st["cross_app"]:
        return False
    linked = {r["id"] for r in await tx.fetch("""/* pipeshub:kg.cross_apps */ SELECT k.id FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
        WHERE s.org_id = $1 AND k.coll = 1 AND s.cross_app AND k.id = ANY($2::text[])""", org, pending)}
    known = {r["id"] for r in await tx.fetch("""/* pipeshub:kg.known_apps */ SELECT k.id FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
        WHERE s.org_id = $1 AND k.coll = 1 AND k.id = ANY($2::text[])""", org, pending)}
    return not linked and known >= set(pending)


async def serves(tx: Tx, org: str, app_id: str) -> bool:
    """May a reader answer from kg for this connector? The stricter question, for the readers: :func:`is_current`, and the connector is in ``enforce`` (``shadow`` only compares), it was
    verified by this code (``code_epoch`` is the code's, ``verified_at`` is set), and the org's principals are copied (their pseudo-connector, ``app_sk`` 0, has finished its copy:
    the closure of every user is part of every answer). Anything else is the walk."""
    if not await is_current(tx, org, app_id):
        return False
    row = await tx.fetchrow("""/* pipeshub:kg.serves */ SELECT s.mode, s.code_epoch, s.verified_at, (SELECT p.backfill_cursor FROM kg.projection_state p WHERE p.org_id = s.org_id AND p.app_sk = 0) AS principals
        FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk WHERE s.org_id = $1 AND k.coll = 1 AND k.id = $2""", org, app_id)
    return bool(row and row["mode"] == "enforce" and row["code_epoch"] == CODE_EPOCH and row["verified_at"] is not None and row["principals"] == "done")


async def drain_or_retry(db: KernelDB, org: str, *, page: int = PAGE) -> int:
    """One pass over an org that never raises: the rows taken, or 0 after a failure. A projection that gave up (``RuntimeError``) wrote nothing and leaves its rows in the log:
    it is counted and logged, and the next pass tries again. Any other error is counted as a failed pass and retried the same way."""
    try:
        return (await drain_org(db, org, page=page)).rows
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
