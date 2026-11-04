"""Backfill and flip: copy a connector into kg in batches, drain, verify under the connector's lock; only then ``shadow``. Promotion to ``enforce`` verifies again.

1. Batches of PAGE nodes in (collection, id) order. A batch is one unit of work: it projects its page from the graph (the projection takes the connector's lock first and reads
   after it) and saves the cursor in kg.projection_state in the same transaction, so a crash or a kill resumes at the last committed batch, and a provider write that projected and
   acknowledged meanwhile is either in the batch's read or waits for the lock.
2. Drain the change log (the rows of writers that ran meanwhile).
3. Verify, full, in one unit under the connector's lock; with no change waiting and no difference the state becomes ``shadow`` with the digest and the code epoch.
The principals of the org (users, groups, roles, teams, organizations) are one pseudo-connector (app_sk 0), done once per org.

A backfill needs the change log to be kept: with the projection off (``PG_KG_PROJECT`` not 1) the connectors service takes its log rows back, so nothing could tell a writer's change from
the copy, and the call raises. The same take-back is why a connector goes back to the walk when it deletes rows (flush._take_back, modes.demote_org): the next backfill starts over.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.kg import flush, modes
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.locks import lock_apps
from app.services.graph_db.postgres.kg.model import CODE_EPOCH, Key
from app.services.graph_db.postgres.kg.project import project
from app.services.graph_db.postgres.kg.verify import (
    PAGE,
    Result,
    app_nodes,
    verify_app,
    verify_principals,
)
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
PRINCIPAL_COLLS = ("organizations", "users", "groups", "roles", "teams")
BATCHES = METRICS_BACKEND.counter("pipeshub_kg_backfill_batches_total", "Backfill batches committed", ["scope"])
FLIPS = METRICS_BACKEND.counter("pipeshub_kg_backfill_flips_total", "Backfills that ended in a verify, by outcome", ["outcome"])


class ProjectionOff(RuntimeError):
    """A backfill or a promotion was asked for while the projection is off: the change log is not being kept."""


@dataclass
class Outcome:
    flipped: bool
    batches: int
    result: Result | None = None
    pending: int = 0


def _ctx(org: str) -> OrgContext:
    return OrgContext(org, None, "job", "job:kg-backfill", "kg-backfill")


def _need_log() -> None:
    if flush.mode() == "off":
        raise ProjectionOff("the projection is off (PG_KG_PROJECT is not 1): the change log is not kept, so a copy could not be told from a change; turn it on first")


def _parse(cursor: str | None) -> Key | None:
    if not cursor or cursor == "done":
        return None
    c, _, i = cursor.partition("|")
    return (c, i)


def _fmt(k: Key) -> str:
    return f"{k[0]}|{k[1]}"


async def _app_sk(tx: Tx, app_id: str) -> int:
    return (await KgWriter(tx).nodes_upsert({("apps", app_id)}))[("apps", app_id)]


async def principal_page(tx: Tx, org: str, after: Key | None, limit: int) -> list[Key]:
    c0, i0 = after if after else ("", "")
    rows = await tx.fetch("""/* pipeshub:kg.principal_nodes */ SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($2::text[])
        AND (n.coll, n.id) > ($3, $4) ORDER BY n.coll, n.id LIMIT $5""", org, list(PRINCIPAL_COLLS), c0, i0, limit)
    return [(r["coll"], r["id"]) for r in rows]


async def backfill_scope(db: KernelDB, org: str, app_id: str | None, *, page: int = PAGE, max_batches: int | None = None, restart: bool = False) -> int:
    """Copy one connector (``app_id``) or the org's principals (None) in batches; returns the batches committed in this call. Resumable: a call goes on from the saved cursor."""
    _need_log()
    done = 0
    scope = "app" if app_id else "principals"
    with bind(_ctx(org)):
        if restart:                                  # copy again from the start (a verify found a difference the drain could not explain)
            async def reset(tx: Tx) -> None:
                await lock_apps(tx, org, {app_id} if app_id else set(), org_wide=app_id is None)
                app_sk = await _app_sk(tx, app_id) if app_id else 0
                w = KgWriter(tx)
                if app_id:       # rows the copy would never touch again: records of this connector whose node is gone, with their links
                    ghosts = [r["sk"] for r in await tx.fetch("""/* pipeshub:kg.ghosts */ SELECT r.sk FROM kg.records r WHERE r.org_id = $1 AND r.app_sk = $2
                        AND NOT EXISTS (SELECT 1 FROM graph.nodes n WHERE n.org_id = r.org_id AND n.coll = CASE r.coll WHEN 1 THEN 'apps' WHEN 2 THEN 'recordGroups' ELSE 'records' END AND n.id = r.id)""",
                                                         org, app_sk)]
                    if ghosts:
                        links = await tx.fetch("/* pipeshub:kg.ghost_links */ SELECT parent_sk, kind, child_sk FROM kg.links WHERE org_id = $1 AND (child_sk = ANY($2::bigint[]) OR parent_sk = ANY($2::bigint[]))",
                                               org, ghosts)
                        await w.links_delete([(r["parent_sk"], r["kind"], r["child_sk"]) for r in links])
                        await w.records_delete(ghosts)
                await modes.demote(tx, org, app_sk)
            await db.run(reset, write=True)
        while max_batches is None or done < max_batches:
            async def batch(tx: Tx) -> bool:
                app_sk = await _app_sk(tx, app_id) if app_id else 0
                cursor, _ = await modes.state(tx, org, app_sk)
                if cursor == "done":
                    return False
                after = _parse(cursor)
                nodes = await (app_nodes(tx, org, app_id, after, page) if app_id else principal_page(tx, org, after, page))
                w = KgWriter(tx)
                if not nodes:
                    await w.projection_set(app_sk, epoch=0, mode="legacy", digest=None, cursor="done", verified_at=None)
                    return False
                await project(tx, org, set(nodes), lock_for={app_id} if app_id else None, expand_neighbours=False)     # locks first, reads after
                await w.projection_set(app_sk, epoch=0, mode="legacy", digest=None, cursor=_fmt(nodes[-1]), verified_at=None)
                return True
            if not await db.run(batch, write=True):
                break
            done += 1
            BATCHES.inc(scope)
    return done


async def _pending(tx: Tx, org: str, app_id: str) -> int:
    """Changes waiting in the log that can alter this connector: its own and those of no connector (edges, principals)."""
    return await tx.fetchval("/* pipeshub:kg.pending */ SELECT count(*) FROM kg.graph_changes WHERE org_id = $1 AND app_id IN ($2, '') AND txid <> pg_current_xact_id()", org, app_id)


async def backfill(db: KernelDB, org: str, app_id: str, *, page: int = PAGE, max_batches: int | None = None, drain_rounds: int | None = None, restart: bool = False) -> Outcome:
    """The whole sequence for one connector; resumes where an interrupted call stopped. ``flipped`` is true only when the verify found no difference and nothing waits in the log."""
    _need_log()
    n = await backfill_scope(db, org, None, page=page, max_batches=max_batches, restart=restart)
    n += await backfill_scope(db, org, app_id, page=page, max_batches=max_batches, restart=restart)
    with bind(_ctx(org)):
        rounds = 0
        while drain_rounds is None or rounds < drain_rounds:            # until nothing is left (a round takes pages until the log is empty); None = no cap
            if not (await drain_org(db, org)).rows:
                break
            rounds += 1

        async def flip(tx: Tx) -> Outcome:
            await lock_apps(tx, org, {app_id}, org_wide=False)
            res = await verify_app(tx, org, app_id) + await verify_principals(tx, org)
            pending = await _pending(tx, org, app_id)       # after the verify, under the lock: a change that committed meanwhile is in the log, and no take-back can delete it while we hold the lock
            app_sk = await _app_sk(tx, app_id)
            cursor, mode = await modes.state(tx, org, app_sk)
            if pending:      # the log explains a difference: nothing is decided, the caller drains and asks again
                return Outcome(False, n, res, pending)
            if cursor != "done":      # a demotion (or a restart) cleared the copy while we verified: it is not a copy any more
                return Outcome(False, n, res, 0)
            if not res.ok:
                if mode != "legacy":      # nothing waits and kg still differs: back to the walk for readers, and the next backfill starts over
                    await modes.demote(tx, org, app_sk)
                return Outcome(False, n, res, 0)
            await KgWriter(tx).projection_set(app_sk, epoch=CODE_EPOCH, mode="enforce" if mode == "enforce" else "shadow", digest=res.digest, cursor="done", verified_at=dt.datetime.now(dt.timezone.utc))
            return Outcome(True, n, res, 0)
        out = await db.run(flip, write=True)
    FLIPS.inc("shadow" if out.flipped else "refused")
    return out


class Refused(ValueError):
    """A promotion that the state or a verify does not allow."""


async def promote(db: KernelDB, org: str, app_id: str) -> Result:
    """``shadow`` to ``enforce``: the connector must be in ``shadow`` at this code's epoch, and a verify under its lock, now, finds no difference and nothing waiting in the log."""
    _need_log()
    with bind(_ctx(org)):
        async def go(tx: Tx) -> Result:
            await lock_apps(tx, org, {app_id}, org_wide=False)
            app_sk = await _app_sk(tx, app_id)
            row = await tx.fetchrow("/* pipeshub:kg.promote_read */ SELECT mode, code_epoch, verified_at FROM kg.projection_state WHERE org_id = $1 AND app_sk = $2", org, app_sk)
            if row is None or row["mode"] != "shadow" or row["code_epoch"] != CODE_EPOCH or row["verified_at"] is None:
                raise Refused(f"{app_id} is not in shadow at epoch {CODE_EPOCH}: backfill it first")
            res = await verify_app(tx, org, app_id) + await verify_principals(tx, org)
            if pending := await _pending(tx, org, app_id):      # after the verify, under the lock (see the flip)
                raise Refused(f"{app_id} has {pending} change(s) waiting in the log")
            cursor, mode = await modes.state(tx, org, app_sk)
            if mode != "shadow" or cursor != "done":
                raise Refused(f"{app_id} was demoted while it was verified")
            if not res.ok:
                raise Refused(f"{app_id} differs from the graph: {res.summary()}")
            await KgWriter(tx).projection_set(app_sk, epoch=CODE_EPOCH, mode="enforce", digest=res.digest, cursor="done", verified_at=dt.datetime.now(dt.timezone.utc))
            return res
        return await db.run(go, write=True)


async def demote(db: KernelDB, org: str, app_id: str) -> None:
    """Back to the walk (always allowed); the next backfill starts from the first node."""
    with bind(_ctx(org)):
        async def go(tx: Tx) -> None:
            await lock_apps(tx, org, {app_id}, org_wide=False)
            await modes.demote(tx, org, await _app_sk(tx, app_id))
        await db.run(go, write=True)
