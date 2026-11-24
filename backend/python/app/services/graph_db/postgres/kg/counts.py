"""The counters of the typed graph (``kg.counts``, kg_0015): per connector, the rows each class holds of what a listing counts by class (facet 1).

A trigger on ``kg.records`` adds a delta row per key in the writer's own transaction, so the rows of a key, summed, are ``count(*)`` at every snapshot. The projection worker
keeps them few: :func:`fold` replaces a batch of deltas and the folded rows of the same keys with one folded row per key, and :func:`seed` counts a connector whole once
(its rows written before the trigger held no delta), which also leaves the mark (facet 0) that its counts can be read. Both run under the org's counters lock, read what they
replace in one statement (one snapshot) and delete exactly the rows they read, so a delta committed meanwhile is kept, never lost or counted twice.

Reading a count is on unless ``PG_KG_COUNTERS=0`` (any other value raises, ``flush.check_env``): an unfiltered listing counts what the viewer's classes open from here
instead of from the rows (:func:`class_rows`). A count sums only the classes the caller hands in, so it says nothing about rows the viewer may not read. The fold and the
seed run whatever that switch says: the trigger adds deltas on every write, and unfolded they would grow without bound, so reading them costs one index read and nothing
more. A restore keeps no counter row and adds no delta (kg_0015), so the seed counts each connector whole again. Facet 2 (record type and origin) is no longer kept
(kg_0023): nothing read it.

:func:`check` compares a connector's counters with a recount of its rows after each periodic verify (``net.verify_due``) and reports a difference; it changes nothing.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from app.core.org_context import bind
from app.services.graph_db.postgres.kg import flush
from app.services.graph_db.postgres.kg.drainer import _ctx
from app.services.graph_db.postgres.kg.listing import DECIDED, LIVE
from app.services.graph_db.postgres.kg.locks import LOCK_CLASS
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    import asyncio

    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
FOLD_MAX = 4000           # deltas one fold takes: with the folded rows of their keys, its delete stays within two calls of the writer
FOLDS_PER_PASS = 25       # folds one pass of the worker runs for an org, so a steady writer cannot hold the pass
SEEDS_PER_PASS = 5        # connectors one pass counts whole
KEY = ("app_sk", "facet", "class_id", "kind", "folder", "record_type", "origin")

FOLD_READ = """/* pipeshub:kg.counts_fold_read */ WITH d AS MATERIALIZED (SELECT id, app_sk, facet, class_id, kind, folder, record_type, origin, n, folded FROM kg.counts
    WHERE org_id = $1 AND NOT folded ORDER BY id LIMIT $2),
  k AS (SELECT DISTINCT app_sk, facet, class_id, kind, folder, record_type, origin FROM d)
SELECT * FROM d
UNION ALL SELECT c.id, c.app_sk, c.facet, c.class_id, c.kind, c.folder, c.record_type, c.origin, c.n, c.folded FROM k JOIN kg.counts c ON c.org_id = $1 AND c.folded
    AND c.app_sk = k.app_sk AND c.facet = 1 AND c.class_id = k.class_id AND c.kind = k.kind AND c.folder = k.folder WHERE k.facet = 1"""
UNSEEDED = """/* pipeshub:kg.counts_unseeded */ SELECT s.app_sk FROM kg.projection_state s WHERE s.org_id = $1 AND s.app_sk <> 0
    AND NOT EXISTS (SELECT 1 FROM kg.counts c WHERE c.org_id = s.org_id AND c.app_sk = s.app_sk AND c.facet = 0) ORDER BY s.app_sk LIMIT $2"""
# What a connector's rows count now and, in one row of its own (facet NULL), the ids of every counter row of the connector that the same snapshot sees (the rows a seed replaces).
RECOUNT = f"""/* pipeshub:kg.counts_recount */ SELECT coalesce(array_agg(c.id), '{{}}') AS ids, NULL::smallint AS facet, NULL::bigint AS class_id, NULL::smallint AS kind,
    NULL::boolean AS folder, NULL::text AS record_type, NULL::text AS origin, NULL::bigint AS n FROM kg.counts c WHERE c.org_id = $1 AND c.app_sk = $2
  UNION ALL SELECT NULL, 1::smallint, r.class_id, r.kind, r.folder, NULL, NULL, count(*) FROM kg.records r
    WHERE r.org_id = $1 AND r.app_sk = $2 AND {LIVE} AND {DECIDED} GROUP BY r.class_id, r.kind, r.folder"""
SEEDED = "/* pipeshub:kg.counts_seeded */ SELECT EXISTS (SELECT 1 FROM kg.counts WHERE org_id = $1 AND app_sk = $2 AND facet = 0)"
# The keys of a connector (by app id) whose counters, summed, differ from what its rows count now: one statement, so one snapshot; none until it has been counted whole.
DRIFT = f"""/* pipeshub:kg.counts_drift */ WITH a AS MATERIALIZED (SELECT k.sk FROM kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id = $2
      AND EXISTS (SELECT 1 FROM kg.counts c WHERE c.org_id = $1 AND c.app_sk = k.sk AND c.facet = 0)),
  c AS (SELECT c.class_id, c.kind, c.folder, sum(c.n)::bigint AS n FROM a JOIN kg.counts c ON c.org_id = $1 AND c.app_sk = a.sk AND c.facet = 1 GROUP BY c.class_id, c.kind, c.folder),
  r AS (SELECT r.class_id, r.kind, r.folder, count(*) AS n FROM a JOIN kg.records r ON r.org_id = $1 AND r.app_sk = a.sk WHERE {LIVE} AND {DECIDED}
      GROUP BY r.class_id, r.kind, r.folder)
SELECT coalesce(c.class_id, r.class_id) AS class_id, coalesce(c.kind, r.kind) AS kind, coalesce(c.folder, r.folder) AS folder, coalesce(c.n, 0) AS kept, coalesce(r.n, 0) AS counted
  FROM c FULL JOIN r ON c.class_id = r.class_id AND c.kind = r.kind AND c.folder = r.folder WHERE coalesce(c.n, 0) <> coalesce(r.n, 0) ORDER BY 1, 2, 3 LIMIT $3"""
DRIFT_LIMIT = 20          # keys one report names
CHECKED = METRICS_BACKEND.counter("pipeshub_kg_counts_checked_total", "Checks of a connector's counters against a recount of its rows, by what they found (same; differs: the "
                                  "totals read from them are wrong; error: the check did not run)", ["result"])
CLASS_ROWS = """/* pipeshub:kg.counts_class_rows */ SELECT kind, folder, sum(n)::bigint AS n FROM kg.counts
    WHERE org_id = $1 AND app_sk = $2 AND facet = 1 AND class_id = ANY($3::bigint[]) GROUP BY kind, folder"""


def serving() -> bool:
    flush.check_env()
    return os.environ.get("PG_KG_COUNTERS") != "0"


async def _locked(tx: Tx, org: str) -> bool:
    return await tx.fetchval("/* pipeshub:kg.counts_lock */ SELECT pg_try_advisory_xact_lock($1::int, hashtext($2))", LOCK_CLASS, f"{org}|counts")


async def _replace(tx: Tx, ids: list[int], sums: dict[tuple, int]) -> None:
    w = KgWriter(tx)
    await w.counts_delete(ids)
    await w.counts_add([(*k, n) for k, n in sums.items() if n or k[1] == 0])


async def fold(db: KernelDB, org: str, *, limit: int = FOLD_MAX) -> int:
    """Fold up to ``limit`` deltas of the org (oldest first) into the folded rows of their keys, in one unit; the deltas taken (0: none, or another fold holds the org)."""
    async def go(tx: Tx) -> int:
        if not await _locked(tx, org):
            return 0
        rows = await tx.fetch(FOLD_READ, org, limit)
        sums: dict[tuple, int] = {}
        for r in rows:
            k = tuple(r[c] for c in KEY)
            sums[k] = sums.get(k, 0) + r["n"]
        if any(n < 0 for n in sums.values()):
            logger.error("kg counters below zero for an org: they no longer match its rows (a seed of the connector counts it again)")
        if rows:
            await _replace(tx, [r["id"] for r in rows], sums)
        return sum(1 for r in rows if not r["folded"])
    return await db.run(go, write=True)


async def seed(db: KernelDB, org: str, app_sk: int) -> bool:
    """Count one connector whole: its counter rows become what its rows count now, with the mark that its counts can be read. False when another fold holds the org."""
    async def go(tx: Tx) -> bool:
        if not await _locked(tx, org):
            return False
        rows = await tx.fetch(RECOUNT, org, app_sk)
        ids = next(r["ids"] for r in rows if r["facet"] is None)
        sums = {(app_sk, r["facet"], r["class_id"], r["kind"], r["folder"], r["record_type"], r["origin"]): r["n"] for r in rows if r["facet"] is not None}
        await _replace(tx, list(ids), {(app_sk, 0, None, None, None, None, None): 0, **sums})
        await KgWriter(tx).analyze_request(app_sk)        # a connector counted whole is a first load or a restore: its rows are new to the statistics
        return True
    return await db.run(go, write=True)


async def run(db: KernelDB, org: str, *, stop: asyncio.Event | None = None) -> int:
    """One pass of the worker over an org: fold its deltas (at most FOLDS_PER_PASS units), then count whole the connectors not counted yet. The units done."""
    done = 0
    with bind(_ctx(org)):
        for _ in range(FOLDS_PER_PASS):
            if stop is not None and stop.is_set():
                return done
            n = await fold(db, org)
            done += n
            if n < FOLD_MAX:
                break
        async with db.uow() as tx:
            apps = [r["app_sk"] for r in await tx.fetch(UNSEEDED, org, SEEDS_PER_PASS)]
        for app_sk in apps:
            if stop is not None and stop.is_set():
                break
            done += await seed(db, org, app_sk)
    return done


async def class_rows(tx: Tx, org: str, app_sk: int, classes: list[int]) -> dict[tuple[int, bool], int] | None:
    """The rows of a connector that the classes ``classes`` hold, by (kind, folder), as the listing counts them; None until the connector has been counted whole."""
    if not await tx.fetchval(SEEDED, org, app_sk):
        return None
    return {(r["kind"], r["folder"]): r["n"] for r in await tx.fetch(CLASS_ROWS, org, app_sk, classes) if r["n"]}


async def check(db: KernelDB, org: str, app_id: str) -> list[dict] | None:
    """Compare the counters of a connector with a recount of its rows, in one snapshot, and report the keys that differ (at most ``DRIFT_LIMIT``) on the log and in ``CHECKED``.
    It repairs nothing: a difference is a bug in the trigger, the fold or the seed, and a silent repair would hide it. None when the check failed (logged, counted); never raises."""
    try:
        with bind(_ctx(org)):
            rows = await db.run(lambda tx: tx.fetch(DRIFT, org, app_id, DRIFT_LIMIT))
    except Exception:
        CHECKED.inc("error")
        logger.warning("kg counters check of connector %s failed; nothing changes", app_id, exc_info=True)
        return None
    diff = [dict(r) for r in rows]
    CHECKED.inc("differs" if diff else "same")
    if diff:
        logger.error("kg counters of connector %s of org %s differ from its rows (the totals read from them are wrong): %s", app_id, org,
                     ", ".join(f"class {d['class_id']} kind {d['kind']} folder {d['folder']}: kept {d['kept']}, counted {d['counted']}" for d in diff))
    return diff
