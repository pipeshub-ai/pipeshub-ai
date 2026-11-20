"""Backfill and flip: copy a connector into kg in batches, drain, verify under the connector's lock; only then ``shadow``. Promotion to ``enforce`` verifies again.

1. Batches of PAGE nodes in (collection, id) order. A batch is one unit of work: it projects its page from the graph (the projection takes the connector's lock first and reads
   after it) and saves the cursor in kg.projection_state in the same transaction, so a crash or a kill resumes at the last committed batch, and a provider write that projected and
   acknowledged meanwhile is either in the batch's read or waits for the lock.
2. Drain the change log (the rows of writers that ran meanwhile).
3. Verify, full, in one unit under the connector's lock; with no change waiting and no difference the state becomes ``shadow`` with the digest and the code epoch.
The principals of the org (users, groups, roles, teams, organizations) are one pseudo-connector (app_sk 0), done once per org.

A backfill needs the change log to be kept: with the projection off (``PG_KG_PROJECT=0``) the connectors service takes its log rows back, so nothing could tell a writer's change from
the copy, and the call raises. The same take-back is why a connector goes back to the walk when it deletes rows (flush._take_back, modes.demote_org): the next backfill starts over.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.kg import flush, modes
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.locks import NeedLocks, lock_apps
from app.services.graph_db.postgres.kg.model import CODE_EPOCH, COLL_CODE, Key, _ms, _s
from app.services.graph_db.postgres.kg.project import project
from app.services.graph_db.postgres.kg.reader import RECORDS_BYTE_ORDER
from app.services.graph_db.postgres.kg.stamp import stamp_app
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
    import asyncio

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
    reason: str = ""             # why ``flipped`` is false: "unfinished" (a budget or a stop ended the copy; it keeps its cursor), "pending" (changes waiting in the log), "cleared" (a demotion or restart
                                 # emptied the copy while it was verified), "differs" (the verify found a difference nothing waiting explains)
    stamp_after: str | None = None   # where an unfinished stamping of the connector goes on (``backfill(stamp_after=...)``)


def _ctx(org: str) -> OrgContext:
    return OrgContext(org, None, "job", "job:kg-backfill", "kg-backfill")


def _need_log() -> None:
    if flush.mode() == "off":
        raise ProjectionOff("the projection is off (PG_KG_PROJECT=0): the change log is not kept, so a copy could not be told from a change; turn it on first")


def _parse(cursor: str | None) -> Key | None:
    if not cursor or cursor == "done":
        return None
    c, _, i = cursor.partition("|")
    if c == "records":
        return ("records", "")
    return ("records", i) if c == RECORDS_BYTE_ORDER else (c, i)


def _fmt(k: Key) -> str:
    return f"{RECORDS_BYTE_ORDER if k[0] == 'records' else k[0]}|{k[1]}"


# A copy carries this in its (unused until verified) digest once a batch of it was taken off the readers: it then ends not listed from. A restart or a take-back clears it with the cursor.
INEXACT = "copy:inexact"
COPY_STATE = "/* pipeshub:kg.copy_state */ SELECT backfill_cursor, verify_digest FROM kg.projection_state WHERE org_id = $1 AND app_sk = $2"


async def _copy_state(tx: Tx, org: str, app_sk: int) -> tuple[str | None, bool]:
    """(copy cursor, whether the copy is marked inexact) of a connector, or of the org's principals (``app_sk`` 0)."""
    row = await tx.fetchrow(COPY_STATE, org, app_sk)
    return (row["backfill_cursor"], row["verify_digest"] == INEXACT) if row else (None, False)


async def _app_sk(tx: Tx, app_id: str) -> int:
    return (await KgWriter(tx).nodes_upsert({("apps", app_id)}))[("apps", app_id)]


async def principal_page(tx: Tx, org: str, after: Key | None, limit: int) -> list[Key]:
    c0, i0 = after if after else ("", "")
    rows = await tx.fetch("""/* pipeshub:kg.principal_nodes */ SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($2::text[])
        AND (n.coll, n.id) > ($3, $4) ORDER BY n.coll, n.id LIMIT $5""", org, list(PRINCIPAL_COLLS), c0, i0, limit)
    return [(r["coll"], r["id"]) for r in rows]


async def backfill_scope(db: KernelDB, org: str, app_id: str | None, *, page: int = PAGE, max_batches: int | None = None, restart: bool = False,
                         stop: asyncio.Event | None = None) -> int:
    """Copy one connector (``app_id``) or the org's principals (None) in batches; returns the batches committed in this call. Resumable: a call goes on from the saved cursor."""
    _need_log()
    done = 0
    scope = "app" if app_id else "principals"
    with bind(_ctx(org)):
        if restart:                                  # copy again from the start (a verify found a difference the drain could not explain)
            async def reset(tx: Tx) -> None:
                await lock_apps(tx, org, {app_id} if app_id else set(), org_wide=True)      # an orphan has no record row, so a connector taking it over locks only itself
                app_sk = await _app_sk(tx, app_id) if app_id else 0
                w = KgWriter(tx)
                if app_id:       # rows the copy would never touch again: records of this connector whose node is gone, with their links
                    ghosts = [r["sk"] for r in await tx.fetch("""/* pipeshub:kg.ghosts */ SELECT r.sk FROM kg.records r WHERE r.org_id = $1 AND r.app_sk = $2
                        AND NOT EXISTS (SELECT 1 FROM graph.nodes n WHERE n.org_id = r.org_id AND n.coll = CASE r.coll WHEN 1 THEN 'apps' WHEN 2 THEN 'recordGroups' ELSE 'records' END AND n.id = r.id)""",
                                                         org, app_sk)]
                    orphans = [r["sk"] for r in await tx.fetch("""/* pipeshub:kg.orphan_scopes */ SELECT s.sk FROM kg.scopes s WHERE s.org_id = $1 AND s.app_sk = $2
                        AND NOT EXISTS (SELECT 1 FROM kg.records r WHERE r.org_id = s.org_id AND r.sk = s.sk)""", org, app_sk)]
                    if orphans:      # an access list whose record row is gone (an older process deleted the record alone)
                        await w.scopes_delete(orphans)
                    if ghosts:
                        links = await tx.fetch("/* pipeshub:kg.ghost_links */ SELECT parent_sk, kind, child_sk FROM kg.links WHERE org_id = $1 AND (child_sk = ANY($2::bigint[]) OR parent_sk = ANY($2::bigint[]))",
                                               org, ghosts)
                        await w.links_delete([(r["parent_sk"], r["kind"], r["child_sk"]) for r in links])
                        await w.records_delete(ghosts)
                        await w.scopes_delete(ghosts)
                await modes.demote(tx, org, app_sk)
            await db.run(reset, write=True)
        while (max_batches is None or done < max_batches) and not (stop is not None and stop.is_set()):
            async def batch(tx: Tx) -> bool:
                app_sk = await _app_sk(tx, app_id) if app_id else 0
                cursor, inexact = await _copy_state(tx, org, app_sk)
                if cursor == "done":
                    return False
                after = _parse(cursor)
                nodes = await (app_nodes(tx, org, app_id, after, page) if app_id else principal_page(tx, org, after, page))
                w = KgWriter(tx)
                if not nodes:
                    await lock_apps(tx, org, {app_id} if app_id else set(), org_wide=not app_id)
                    taken_off = False
                else:
                    st = await project(tx, org, set(nodes), lock_for={app_id} if app_id else None, expand_neighbours=False, stamps=False)     # locks first, reads after; the stamps come once the connector is copied
                    taken_off = app_sk in st.off
                if taken_off:
                    # This batch's own projection took the connector off the readers (a value kg cannot hold, or a node whose connector is gone) and cleared the cursor
                    # with it. Starting the copy again would meet the same node every time: the copy goes on past it, marked inexact, and ends not listed from (epoch 0).
                    # The verify names the difference; once the graph no longer holds the value, the drain projects the node again and a verify can pass.
                    inexact = True
                elif await _copy_state(tx, org, app_sk) != (cursor, inexact):
                    # The copy state was read before the lock: a restart that demoted the copy, or another copy that moved its cursor or marked it inexact, has
                    # committed by now. The batch writes nothing over it, and the next batch goes on from the state as it is.
                    return True
                if not nodes:      # the copy is finished, by this code: the listing reads it from here on (kh_listing.head), the access check only once it is verified and promoted
                    await w.projection_set(app_sk, epoch=0 if inexact else CODE_EPOCH, mode="legacy", digest=INEXACT if inexact else None, cursor="done", verified_at=None)
                    return False
                await w.projection_set(app_sk, epoch=0, mode="legacy", digest=INEXACT if inexact else None, cursor=_fmt(nodes[-1]), verified_at=None)
                return True
            if not await db.run(batch, write=True):
                break
            done += 1
            BATCHES.inc(scope)
    return done


async def _restamp_pages(db: KernelDB, org: str, app_id: str, page: int, after: str | None, max_pages: int | None, stop: asyncio.Event | None) -> tuple[int, str | None, bool]:
    """``restamp_app`` for at most ``max_pages`` pages from ``after`` and not past ``stop``: (pages, where to go on, finished)."""
    pages = 0
    extra: set[str] = set()
    with bind(_ctx(org)):
        while True:
            if (max_pages is not None and pages >= max_pages) or (stop is not None and stop.is_set()):
                return pages, after, False
            async def go(tx: Tx, cursor: str | None = after, held: frozenset[str] = frozenset(extra)) -> str | None:
                await lock_apps(tx, org, {app_id} | held, org_wide=False)
                return await stamp_app(tx, org, app_id, cursor, page, locked={app_id} | held)
            try:
                after = await db.run(go, write=True)
            except NeedLocks as e:
                extra |= e.apps
                continue
            if after is None:
                return pages, None, True
            pages += 1


async def restamp_app(db: KernelDB, org: str, app_id: str, *, page: int = 2000) -> int:
    """Stamp every record and record group of a connector, a page at a time under its lock; returns the pages. The structural rows come first: a page's classes are compiled from the
    graph, whose closure the pages share. A stamp that reaches another connector takes its lock too and redoes the page."""
    return (await _restamp_pages(db, org, app_id, page, None, None, None))[0]


async def _pending(tx: Tx, org: str, app_id: str) -> int:
    """Changes waiting that can alter this connector: its own in the log and those of no connector (edges, principals), and the restamp jobs queued for it (a queued job is a change the
    stamps have not caught up with)."""
    n = await tx.fetchval("/* pipeshub:kg.pending */ SELECT count(*) FROM kg.graph_changes WHERE org_id = $1 AND app_id IN ($2, '') AND txid <> pg_current_xact_id()", org, app_id)
    return n + await tx.fetchval("""/* pipeshub:kg.pending_jobs */ SELECT count(*) FROM kg.restamp_queue q JOIN kg.node_keys k ON k.org_id = q.org_id AND k.sk = q.app_sk
        WHERE q.org_id = $1 AND k.coll = 1 AND k.id = $2""", org, app_id)


async def _copied(db: KernelDB, org: str, app_id: str) -> bool:
    """Both copies (the org's principals and the connector) are finished."""
    async def go(tx: Tx) -> bool:
        return bool(await tx.fetchval("""/* pipeshub:kg.copied */ SELECT coalesce((SELECT p.backfill_cursor = 'done' FROM kg.projection_state p WHERE p.org_id = $1 AND p.app_sk = 0), false)
            AND coalesce((SELECT s.backfill_cursor = 'done' FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
                          WHERE s.org_id = $1 AND k.coll = 1 AND k.id = $2), false)""", org, app_id))
    with bind(_ctx(org)):
        return await db.run(go)


# A copy finished by epoch 7 code holds no trash columns (kg_0019) and nothing else this code derives differently: its trash columns are read from the connector's nodes, derived
# as the projection derives them and written where they differ, then the copy is relabelled this code's epoch with its mode and its verify. It is never copied again.
FILL_FROM = 7
FILL_STATE = """/* pipeshub:kg.trash_fill_state */ SELECT code_epoch, backfill_cursor, mode, verify_digest, verified_at FROM kg.projection_state WHERE org_id = $1 AND app_sk = $2"""
# One page of the connector's records in id order, whatever they hold (``has``: a delete time or batch to derive): a page reads at most ``page`` rows, so a large connector with
# few deleted records never scans past its page under the connector's lock.
FILL_RECORDS = """/* pipeshub:kg.trash_fill_records */ SELECT n.coll, n.id, n.doc->'deletedAtTimestamp' AS ts, n.doc->'deleteBatchId' AS batch,
    (n.doc ? 'deletedAtTimestamp' OR n.doc ? 'deleteBatchId') AS has FROM graph.nodes n
    WHERE n.org_id = $1 AND n.coll = 'records' AND (n.doc->>'connectorId') = $2 AND n.id > $3 ORDER BY (n.doc->>'connectorId'), n.id LIMIT $4"""
# The connector's groups (no index by connector: the org's groups are few beside its records) and the connector itself.
FILL_REST = """/* pipeshub:kg.trash_fill_rest */ SELECT n.coll, n.id, n.doc->'deletedAtTimestamp' AS ts, n.doc->'deleteBatchId' AS batch FROM graph.nodes n
    WHERE n.org_id = $1 AND ((n.coll = 'recordGroups' AND (n.doc->>'connectorId') = $2) OR (n.coll = 'apps' AND n.id = $2))
      AND (n.doc ? 'deletedAtTimestamp' OR n.doc ? 'deleteBatchId')"""
FILL_HELD = """/* pipeshub:kg.trash_fill_held */ SELECT k.sk, k.coll, k.id, k.kind, k.deleted_ms, k.delete_batch FROM unnest($2::smallint[], $3::text[]) AS q(coll, id)
    JOIN kg.records k ON k.org_id = $1 AND k.coll = q.coll AND k.id = q.id"""


async def fill_trash(db: KernelDB, org: str, app_id: str, *, page: int = PAGE, after: str = "", max_pages: int | None = None,
                     stop: asyncio.Event | None = None) -> tuple[str | None, str | None]:
    """Bring a copy finished by epoch 7 code up to this code's epoch in place, from ``after`` (a record id) for at most ``max_pages`` pages. Returns (the mode it was relabelled
    with, None) when done, (None, where to go on) when a bound or ``stop`` came first, and (None, None) when the connector holds no such copy. Each page holds the connector's
    lock, as a copy batch does, so the drain never interleaves with it; a call started again from the beginning writes only what still differs."""
    pages = 0

    async def write(tx: Tx, rows: list) -> None:
        derived = {(COLL_CODE[r["coll"]], r["id"]): (_ms(_json(r["ts"])), _s(_json(r["batch"]))) for r in rows}
        if not derived:
            return
        held = await tx.fetch(FILL_HELD, org, [k[0] for k in derived], [k[1] for k in derived])
        await KgWriter(tx).trash_set([(h["sk"], h["coll"], h["id"], h["kind"], *derived[(h["coll"], h["id"])]) for h in held
                                      if (h["deleted_ms"], h["delete_batch"]) != derived[(h["coll"], h["id"])]])

    async def step(tx: Tx) -> tuple[str, str] | str | None:
        await lock_apps(tx, org, {app_id}, org_wide=False)
        state = await tx.fetchrow(FILL_STATE, org, await _app_sk(tx, app_id))
        if state is None or state["code_epoch"] != FILL_FROM or state["backfill_cursor"] != "done":
            return None
        rows = await tx.fetch(FILL_RECORDS, org, app_id, after, page)
        await write(tx, [r for r in rows if r["has"]])
        if len(rows) == page:
            return ("next", rows[-1]["id"])           # the cursor moves only once this unit has committed: a retried unit reads the same page again
        await write(tx, await tx.fetch(FILL_REST, org, app_id))
        await KgWriter(tx).projection_set(await _app_sk(tx, app_id), epoch=CODE_EPOCH, mode=state["mode"], digest=state["verify_digest"], cursor="done",
                                          verified_at=state["verified_at"])
        return state["mode"]
    with bind(_ctx(org)):
        while True:
            if (stop is not None and stop.is_set()) or (max_pages is not None and pages >= max_pages):
                return None, after
            out = await db.run(step, write=True)
            if not isinstance(out, tuple):
                return out, None
            after = out[1]
            pages += 1


def _json(v: object) -> object:
    return json.loads(v) if isinstance(v, str) else v


async def principals_copied(db: KernelDB, org: str) -> bool:
    async def go(tx: Tx) -> bool:
        return (await modes.state(tx, org, 0))[0] == "done"
    with bind(_ctx(org)):
        return await db.run(go)


async def backfill(db: KernelDB, org: str, app_id: str, *, page: int = PAGE, max_batches: int | None = None, drain_rounds: int | None = None, restart: bool = False,
                   stop: asyncio.Event | None = None, stamp_pages: int | None = None, stamp_after: str | None = None, stamp_page: int = 2000) -> Outcome:
    """The whole sequence for one connector; resumes where an interrupted call stopped. ``flipped`` is true only when the verify found no difference and nothing waits in the log.
    ``max_batches`` (per copy), ``drain_rounds`` (each round takes at most ``page`` log rows), ``stamp_pages`` and ``stop`` bound the call: when one is reached the outcome is not flipped and
    nothing is stamped or verified past it: "unfinished" (a copy or the stamping stopped at its budget or at ``stop``; the copy keeps its cursor and the stamping goes on from
    ``Outcome.stamp_after``) or "pending" (the log still has rows). The verify itself is one unit and is not interrupted."""
    _need_log()
    n = await backfill_scope(db, org, None, page=page, max_batches=max_batches, restart=restart, stop=stop)
    n += await backfill_scope(db, org, app_id, page=page, max_batches=max_batches, restart=restart, stop=stop)
    if not await _copied(db, org, app_id):
        return Outcome(False, n, None, 0, "unfinished")
    with bind(_ctx(org)):
        rounds = 0
        while (drain_rounds is None or rounds < drain_rounds) and not (stop is not None and stop.is_set()):     # until nothing is left (a round takes pages until the log is empty); None = no cap
            bounded = {"page": page, "max_rows": page} if drain_rounds is not None else {}      # a page commits whole: a bounded round must also take small pages
            if not (await drain_org(db, org, stop=stop, **bounded)).rows:
                break
            rounds += 1
        if stop is not None and stop.is_set():
            return Outcome(False, n, None, 0, "unfinished", stamp_after)

        async def waiting(tx: Tx) -> int:
            return await _pending(tx, org, app_id)
        if left := await db.run(waiting):          # a log that keeps gaining rows is never caught up with: the caller waits and looks again, nothing is stamped meanwhile
            return Outcome(False, n, None, left, "pending", stamp_after)
        pages, after, finished = await _restamp_pages(db, org, app_id, stamp_page, stamp_after, stamp_pages, stop)
        if not finished:
            return Outcome(False, n, None, 0, "unfinished", after)

        async def flip(tx: Tx) -> Outcome:
            await lock_apps(tx, org, {app_id}, org_wide=False)
            res = await verify_app(tx, org, app_id) + await verify_principals(tx, org)
            pending = await _pending(tx, org, app_id)       # after the verify, under the lock: a change that committed meanwhile is in the log, and no take-back can delete it while we hold the lock
            app_sk = await _app_sk(tx, app_id)
            cursor, mode = await modes.state(tx, org, app_sk)
            if pending:      # the log explains a difference: nothing is decided, the caller drains and asks again
                return Outcome(False, n, res, pending, "pending")
            if cursor != "done" or (await modes.state(tx, org, 0))[0] != "done":      # a demotion (or a restart) cleared the connector's copy or the principals' while we verified: it is not a copy any more
                return Outcome(False, n, res, 0, "cleared")
            if not res.ok:
                if mode != "legacy":      # nothing waits and kg still differs: back to the walk for readers, and the next backfill starts over
                    await modes.demote(tx, org, app_sk)
                else:      # a copy never served: it keeps its cursor, and loses this code's epoch so that the listing does not read it either
                    await KgWriter(tx).projection_set(app_sk, epoch=0, mode="legacy", digest=None, cursor=cursor, verified_at=None)
                return Outcome(False, n, res, 0, "differs")
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
            w = KgWriter(tx)
            await w.projection_set(app_sk, epoch=CODE_EPOCH, mode="enforce", digest=res.digest, cursor="done", verified_at=dt.datetime.now(dt.timezone.utc))
            await w.analyze_request(app_sk)
            return res
        return await db.run(go, write=True)


async def demote(db: KernelDB, org: str, app_id: str) -> None:
    """Back to the walk (always allowed); the next backfill starts from the first node."""
    with bind(_ctx(org)):
        async def go(tx: Tx) -> None:
            await lock_apps(tx, org, {app_id}, org_wide=False)
            await modes.demote(tx, org, await _app_sk(tx, app_id))
        await db.run(go, write=True)
