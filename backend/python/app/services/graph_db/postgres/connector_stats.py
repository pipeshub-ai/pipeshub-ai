"""The kept record counts of ``get_connector_stats`` (graph_0021): a read costs one index range per connector, not a walk of every record it holds.

``graph.stats_records`` holds, per counted record and per app that counts it, the key it is counted under (record type, indexing, extraction and entities state), and
``graph.stats_counts`` those rows summed per (app, key). A trigger logs every write that may change them (``graph.stats_changes``); :func:`run`, a phase of the kg worker
in the connectors service, recounts what the log names (:func:`drain`; until the first count starts there is nothing to recount, and the log is dropped) and
counts the organisation whole once (:func:`seed`: its records in pages from a checkpoint, every app counted whole when the last page commits). Both hold the org's stats
lock, so they never interleave. :func:`read` answers from the counts plus the change of the records still
logged, all in one snapshot: the walk's answer at that snapshot. It answers None (the caller walks) for an app not counted whole yet, while a record group or an app is
logged (they move whole subtrees), or while more than ``CAP`` records are logged.

Membership is the walk's: a collection (an app of type KB) counts the records that belong to it; any other app the records of the record groups that chain up to it,
ten groups at most. A record counted by two apps has a row for each.
"""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from app.config.constants.arangodb import FOLDER_MIME_TYPES, Connectors
from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.kg.locks import LOCK_CLASS
from app.services.graph_db.postgres.kg.reader import seeks

if TYPE_CHECKING:
    import asyncio

    from app.core.db import KernelDB, Tx

CAP = 5000              # logged records a read corrects for, and records one apply recounts
LOG_ROWS = 200          # log rows one drain unit takes
DROP_ROWS = 5000        # log rows one statement drops while no app is counted whole
UNITS_PER_PASS = 25     # drain units, and units of the first count, one pass runs for an org
PAGE = CAP              # records one unit of the first count recounts (one apply chunk)
KB = Connectors.KNOWLEDGE_BASE.value

_LOCK = "/* pipeshub:graph.stats_lock */ SELECT pg_try_advisory_xact_lock($1::int, hashtext($2))"
# The current rows of the records $2 (one per app that counts each), as the walk counts them (lookups.STATS_KB / STATS_CONNECTOR): up from each record, ten groups at
# most, to an app that is not a collection, or straight to an app that is one. The collections come from ``k`` (OFFSET 0) for the reason STATS_KB gives.
# Every link is read by its source, a range of one value in the primary key's order (as hierarchy.py spells a key), run under kg.reader.seeks: only that index returns
# it so. As joins, PostgreSQL 18 costed the primary key as a skip scan over to_id (to_coll is bounded, to_id is not) and read the org's whole link table once per hop
# instead (five sequential scans a statement: 1.3 s for 5,000 records on an org of 3M links, past the 60 s statement timeout at 80M).
LIVE = """/* pipeshub:graph.stats_live */ WITH RECURSIVE k AS (SELECT 'records'::text AS rec, 'stageStates'::text AS st OFFSET 0),
  r AS MATERIALIZED (SELECT n.id, n.doc, (n.doc->>'virtualRecordId') || ':' || (n.doc->>'contentRev') || ':entities' AS sid
    FROM (SELECT DISTINCT q FROM unnest($2::text[]) q) q CROSS JOIN k
    CROSS JOIN LATERAL (SELECT x.id, x.doc FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = k.rec AND x.id = q.q LIMIT 1) n
    WHERE coalesce((n.doc->>'isInternal')::boolean, false) = false AND coalesce((n.doc->>'isPlaceholder')::boolean, false) = false
      AND coalesce((n.doc->>'isDeleted')::boolean, false) = false AND NOT coalesce(n.doc->>'mimeType', '') = ANY($3::text[])),
  up(rid, gid, depth) AS (
    SELECT r.id, e.to_id, 1 FROM r CROSS JOIN LATERAL (SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_id >= r.id
        AND e.from_id <= r.id AND e.from_coll = 'records' AND e.to_coll = 'recordGroups' ORDER BY e.from_id OFFSET 0) e
    UNION SELECT up.rid, e.to_id, up.depth + 1 FROM up CROSS JOIN LATERAL (SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo'
        AND e.from_id >= up.gid AND e.from_id <= up.gid AND e.from_coll = 'recordGroups' AND e.to_coll = 'recordGroups' ORDER BY e.from_id OFFSET 0) e WHERE up.depth < 10),
  m AS (SELECT up.rid, e.to_id AS app_id, false AS direct FROM up CROSS JOIN LATERAL (SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo'
          AND e.from_id >= up.gid AND e.from_id <= up.gid AND e.from_coll = 'recordGroups' AND e.to_coll = 'apps' ORDER BY e.from_id OFFSET 0) e
        UNION SELECT r.id, e.to_id, true FROM r CROSS JOIN LATERAL (SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_id >= r.id
          AND e.from_id <= r.id AND e.from_coll = 'records' AND e.to_coll = 'apps' ORDER BY e.from_id OFFSET 0) e)
SELECT n.id AS record_id, m.app_id, coalesce(n.doc->>'recordType', '') AS record_type, coalesce(n.doc->>'indexingStatus', '') AS indexing,
    coalesce(n.doc->>'extractionStatus', '') AS extraction, coalesce(s.doc->>'status', '') AS entities, n.sid
  FROM m JOIN r n ON n.id = m.rid CROSS JOIN k
  LEFT JOIN LATERAL (SELECT x.doc FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = 'apps' AND x.id = m.app_id LIMIT 1) a ON true
  LEFT JOIN LATERAL (SELECT x.doc FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = k.st AND x.id = n.sid LIMIT 1) s ON true
  WHERE m.direct = coalesce(a.doc->>'type' = $4, false)"""
APPLY_OLD = """/* pipeshub:graph.stats_apply_old */ DELETE FROM graph.stats_records WHERE org_id = $1 AND record_id = ANY($2::text[])
    RETURNING app_id, record_type, indexing, extraction, entities"""
APPLY_NEW = """/* pipeshub:graph.stats_apply_new */ INSERT INTO graph.stats_records (org_id, record_id, app_id, record_type, indexing, extraction, entities, sid)
    SELECT $1, * FROM unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::text[], $7::text[], $8::text[])"""
ADD_COUNTS = """/* pipeshub:graph.stats_add_counts */ INSERT INTO graph.stats_counts AS c (org_id, app_id, record_type, indexing, extraction, entities, n)
    SELECT $1, * FROM unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::text[], $7::bigint[])
    ON CONFLICT (org_id, app_id, record_type, indexing, extraction, entities) DO UPDATE SET n = c.n + excluded.n"""
PENDING = "/* pipeshub:graph.stats_pending */ SELECT id, keys FROM graph.stats_changes WHERE org_id = $1 ORDER BY id LIMIT $2"
DONE = "/* pipeshub:graph.stats_done */ DELETE FROM graph.stats_changes WHERE org_id = $1 AND id = ANY($2::bigint[])"
COUNTING = """/* pipeshub:graph.stats_counting */ SELECT EXISTS (SELECT 1 FROM graph.stats_sweeps WHERE org_id = $1)
    OR EXISTS (SELECT 1 FROM graph.stats_apps WHERE org_id = $1)"""
DROP = """/* pipeshub:graph.stats_drop */ WITH d AS (DELETE FROM graph.stats_changes WHERE org_id = $1
    AND id IN (SELECT id FROM graph.stats_changes WHERE org_id = $1 ORDER BY id LIMIT $2) RETURNING 1) SELECT count(*) FROM d"""
BY_SID = "/* pipeshub:graph.stats_by_sid */ SELECT DISTINCT record_id FROM graph.stats_records WHERE org_id = $1 AND sid = ANY($2::text[])"
BY_APP = "/* pipeshub:graph.stats_by_app */ SELECT record_id FROM graph.stats_records WHERE org_id = $1 AND app_id = ANY($2::text[])"
# Every record a changed record group or app may have moved in or out: those of the groups up to ten below it (a record further down cannot reach an app through it), and
# the records that belong to a changed app itself.
SUBTREE = """/* pipeshub:graph.stats_subtree */ WITH RECURSIVE g(id, depth) AS (
      SELECT x, 1 FROM unnest($2::text[]) x
      UNION SELECT e.from_id, 1 FROM unnest($3::text[]) a JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'recordGroups' AND e.to_coll = 'apps'
        AND e.to_id = a
      UNION SELECT e.from_id, g.depth + 1 FROM g JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'recordGroups' AND e.to_coll = 'recordGroups'
        AND e.to_id = g.id WHERE g.depth < 10)
    SELECT e.from_id AS id FROM (SELECT DISTINCT id FROM g) g CROSS JOIN LATERAL (SELECT e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo'
        AND e.from_coll = 'records' AND e.to_coll = 'recordGroups' AND e.to_id = g.id OFFSET 0) e
    UNION SELECT e.from_id FROM unnest($3::text[]) a JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'records' AND e.to_coll = 'apps' AND e.to_id = a"""
# The organisation's first count (seed) in pages: the PAGE smallest record ids after $2 among its records and the records kept rows still name (a record gone since its
# rows were kept is recounted to none). Each side is a range of its primary key in its order, under kg.reader.seeks, so a page costs its own rows whatever the size of
# a connector; a record counted twice (both sides) is one id.
SWEEP_PAGE = """/* pipeshub:graph.stats_sweep_page */ SELECT id FROM (
    (SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.id > $2 ORDER BY n.id LIMIT $3)
    UNION (SELECT DISTINCT s.record_id FROM graph.stats_records s WHERE s.org_id = $1 AND s.record_id > $2 ORDER BY s.record_id LIMIT $3)) p ORDER BY id LIMIT $3"""
SWEEP = "/* pipeshub:graph.stats_sweep */ SELECT after, done FROM graph.stats_sweeps WHERE org_id = $1"
SWEEP_SET = """/* pipeshub:graph.stats_sweep_set */ INSERT INTO graph.stats_sweeps (org_id, after, done) VALUES ($1, $2, $3)
    ON CONFLICT (org_id) DO UPDATE SET after = excluded.after, done = excluded.done"""
MARK_ALL = """/* pipeshub:graph.stats_mark_all */ INSERT INTO graph.stats_apps (org_id, app_id) SELECT $1, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'apps'
    ON CONFLICT DO NOTHING"""
SEED_MARK = "/* pipeshub:graph.stats_seed_mark */ INSERT INTO graph.stats_apps (org_id, app_id) VALUES ($1, $2) ON CONFLICT DO NOTHING"
UNSEEDED = """/* pipeshub:graph.stats_unseeded */ SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'apps'
    AND NOT EXISTS (SELECT 1 FROM graph.stats_apps a WHERE a.org_id = $1 AND a.app_id = n.id) ORDER BY n.id LIMIT $2"""
SEEDED = "/* pipeshub:graph.stats_seeded */ SELECT EXISTS (SELECT 1 FROM graph.stats_apps WHERE org_id = $1 AND app_id = $2)"
LOGGED = "/* pipeshub:graph.stats_logged */ SELECT DISTINCT k FROM graph.stats_changes c CROSS JOIN LATERAL unnest(c.keys) k WHERE c.org_id = $1 LIMIT $2"
COUNTS = """/* pipeshub:graph.stats_counts */ SELECT record_type, indexing, extraction, entities, n FROM graph.stats_counts WHERE org_id = $1 AND app_id = $2 AND n <> 0"""
KEPT = """/* pipeshub:graph.stats_kept */ SELECT record_type, indexing, extraction, entities FROM graph.stats_records
    WHERE org_id = $1 AND app_id = $2 AND record_id = ANY($3::text[])"""


def _ctx(org: str) -> OrgContext:
    return OrgContext(org, None, "job", "job:graph-stats", "graph-stats")


async def _locked(tx: Tx, org: str) -> bool:
    return await tx.fetchval(_LOCK, LOCK_CLASS, f"{org}|stats")


async def _apply(tx: Tx, org: str, ids: list[str]) -> None:
    """Replace the rows of the records ``ids`` with their current ones and move the counts by the difference."""
    for i in range(0, len(ids), CAP):
        chunk = ids[i:i + CAP]
        diff: Counter = Counter()
        for r in await tx.fetch(APPLY_OLD, org, chunk):
            diff[tuple(r)] -= 1
        async with seeks(tx):
            live = await tx.fetch(LIVE, org, chunk, FOLDER_MIME_TYPES, KB)
        if live:
            await tx.execute(APPLY_NEW, org, *map(list, zip(*live, strict=True)))
        for r in live:
            diff[(r["app_id"], r["record_type"], r["indexing"], r["extraction"], r["entities"])] += 1
        keys = [k for k, n in diff.items() if n]
        if keys:
            await tx.execute(ADD_COUNTS, org, *map(list, zip(*keys, strict=True)), [diff[k] for k in keys])


async def drain(db: KernelDB, org: str) -> int | None:
    """Recount what up to ``LOG_ROWS`` log rows of the org name, in one unit (while no app of the org is counted whole, drop the log instead); the rows taken (0: none),
    or None when another unit holds the org."""
    async def go(tx: Tx) -> int | None:
        if not await _locked(tx, org):
            return None
        if not await tx.fetchval(COUNTING, org):
            # Nothing is read from the counts, and the first count (seed) has not started: every page of it reads the graph after this unit commits (it takes the same
            # lock), so the log, every row of a new install's or a restore's whole graph, names nothing that count will miss. Once it has started, a record written
            # behind its page is counted only through the log, which is then recounted.
            # One batch a unit, so a unit (and the lock) stays bounded; run does not count an app while a full batch says more is left.
            return await tx.fetchval(DROP, org, DROP_ROWS)
        rows = await tx.fetch(PENDING, org, LOG_ROWS)
        if not rows:
            return 0
        keys: dict[str, set[str]] = {"r": set(), "s": set(), "g": set(), "a": set()}
        for r in rows:
            for k in r["keys"]:
                if k[1:2] == ":" and k[:1] in keys:      # anything else names nothing: it is dropped with its row, never left to stop the drain
                    keys[k[0]].add(k[2:])
        ids = set(keys["r"])
        if keys["s"]:
            ids |= {r["record_id"] for r in await tx.fetch(BY_SID, org, sorted(keys["s"]))}
        if keys["g"] or keys["a"]:
            # shortcut: a moved group's records are held in memory at once; a move of a group of millions costs that much once, until a paged recount is needed
            ids |= {r["id"] for r in await tx.fetch(SUBTREE, org, sorted(keys["g"]), sorted(keys["a"]))}
            ids |= {r["record_id"] for r in await tx.fetch(BY_APP, org, sorted(keys["a"]))}
        await _apply(tx, org, sorted(ids))
        await tx.execute(DONE, org, [r["id"] for r in rows])
        return len(rows)
    with bind(_ctx(org)):
        return await db.run(go, write=True)


async def seed(db: KernelDB, org: str, app_id: str) -> bool:
    """One unit of the organisation's first count: the next ``PAGE`` records recounted from the checkpoint (graph.stats_sweeps), and once the last page commits every app
    counted whole. After that an app not counted yet (made since) is counted whole as it is: every record it counts was written, and logged, after the count began.
    False when another unit holds the org."""
    async def go(tx: Tx) -> bool:
        if not await _locked(tx, org):
            return False
        at = await tx.fetchrow(SWEEP, org)
        if at is not None and at["done"]:
            await tx.execute(SEED_MARK, org, app_id)
            return True
        after = "" if at is None else at["after"]
        async with seeks(tx):
            ids = [r["id"] for r in await tx.fetch(SWEEP_PAGE, org, after, PAGE)]
        await _apply(tx, org, ids)
        if len(ids) < PAGE:
            await tx.execute(MARK_ALL, org)
        await tx.execute(SWEEP_SET, org, ids[-1] if ids else after, len(ids) < PAGE)
        return True
    with bind(_ctx(org)):
        return await db.run(go, write=True)


async def run(db: KernelDB, org: str, *, stop: asyncio.Event | None = None) -> int:
    """One pass over an org: drain its log (at most ``UNITS_PER_PASS`` units), then at most as many units of counting whole an app not counted yet. The log rows and
    units done."""
    done = n = 0
    for _ in range(UNITS_PER_PASS):
        if stop is not None and stop.is_set():
            return done
        n = await drain(db, org)
        if n is None:
            return done         # another unit holds the org (it may be dropping the log): an app is counted on a later pass, once a drain here finds the log
        done += n
        if n < LOG_ROWS:
            break
    if n >= DROP_ROWS:
        return done             # the log is being dropped and more may be left: an app counted now would have the rest recounted, a record at a time
    for _ in range(UNITS_PER_PASS):
        if stop is not None and stop.is_set():
            break
        with bind(_ctx(org)):
            apps = [r["id"] for r in await db.run(lambda tx: tx.fetch(UNSEEDED, org, 1))]
        if not apps or not await seed(db, org, apps[0]):
            break
        done += 1
    return done


async def read(tx: Tx, org: str, app_id: str) -> list[dict] | None:
    """The rows of ``get_connector_stats`` for the app (``recordType``, ``indexingStatus``, ``extractionStatus``, ``entitiesStatus``, ``cnt``) at the snapshot of
    ``tx`` (repeatable read), or None when the walk must answer."""
    if not await tx.fetchval(SEEDED, org, app_id):
        return None
    logged = [r["k"] for r in await tx.fetch(LOGGED, org, CAP + 1)]
    if len(logged) > CAP or any(k[:1] in ("g", "a") for k in logged):
        return None
    ids = {k[2:] for k in logged if k[:2] == "r:"}
    if sids := [k[2:] for k in logged if k[:2] == "s:"]:
        ids |= {r["record_id"] for r in await tx.fetch(BY_SID, org, sids)}
    if len(ids) > CAP:
        return None
    rows: Counter = Counter({tuple(r[:4]): r["n"] for r in await tx.fetch(COUNTS, org, app_id)})
    if ids:
        for r in await tx.fetch(KEPT, org, app_id, sorted(ids)):
            rows[tuple(r)] -= 1
        async with seeks(tx):
            live = await tx.fetch(LIVE, org, sorted(ids), FOLDER_MIME_TYPES, KB)
        for r in live:
            if r["app_id"] == app_id:
                rows[(r["record_type"], r["indexing"], r["extraction"], r["entities"])] += 1
    return [{"recordType": k[0] or None, "indexingStatus": k[1] or None, "extractionStatus": k[2] or None, "entitiesStatus": k[3] or None, "cnt": n}
            for k, n in rows.items() if n]
