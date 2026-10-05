"""Knowledge hub scopes (Neo4j): the global listing's visibility, precomputed per connector.

The global flatten walks the whole tree on every request to find what a user may see. Most of that walk is
user-independent: a hop either always passes (the child inherits and is STRICT/OPEN), never passes, or passes only
when the child itself is granted (a gate). A node's scope is the nearest gate above it, or itself, or the App. Per
request, the open scopes come from the scope tree and the user's grants, and the page is read from an index.

What the walk does, per hop from parent p to child c (``_kh_v3_granted_hop`` plus the flatten's declared stop):
- dead:  c deleted, p hides its children, or p is a RECORD_GROUP_LEVEL group of this App;
- free:  c inherits from p and its rule is STRICT/OPEN;
- gate:  c is STRICT/OPEN without inheriting, or RESTRICTED and inheriting;
- block: anything else.
The walk stops after ``MAX_DEPTH`` hops from the App, so a node deeper than that is never in a scope.

A connector is listed this way only when the precomputation says exactly what the walk would: a RECORD_LEVEL App,
no RECORD_GROUP_LEVEL group, no node that hides its children, no node with several parents, and no child that
belongs to another connector. The seed arms (granted OPEN nodes the walk does not reach, and the OPEN nodes below
them) depend on the user's grants and stay a walk per request. Any other case, a stale stamp, or any error falls
back to the full query, and the reason is returned so the caller can log it.

Freshness: a sync marks its connector stale before it writes and re-stamps when it ends. The stamp sets ``fresh``
only if no sync started in the meantime (``generation`` is compared and set), and refuses to run while one is.
A stamp records ``STAMP_VERSION``; one written by another version is treated as stale.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient

BATCH = 5000
# The full walk's hop limit (``{1,50}`` in the flatten): a node deeper than this is only listed as a seed.
MAX_DEPTH = 50
# Bumped whenever what a stamp writes changes meaning; a meta of another version is restamped, never read.
STAMP_VERSION = 2

# The listing's sort fields and the stored property each reads. The full query sorts on coalesce(updatedAt, 0)
# and coalesce(createdAt, 0); a connector with a null timestamp is listed by the full query for that sort.
SORT_PROPS = {"name": "khSortName", "updatedAt": "updatedAtTimestamp", "createdAt": "createdAtTimestamp"}
# A predicate that implies the property's type, so the planner reads the index in order.
_LOW = {"khSortName": "''", "updatedAtTimestamp": "-9223372036854775807", "createdAtTimestamp": "-9223372036854775807"}

META_CONSTRAINT = "kh_scope_meta_unique"
INDEXES = (
    "CREATE INDEX kh_scope_conn IF NOT EXISTS FOR (k:KhScope) ON (k.connectorId)",
    "CREATE INDEX kh_rec_conn_sort IF NOT EXISTS FOR (n:Record) ON (n.connectorId, n.khSortName)",
    "CREATE INDEX kh_rec_scope_sort IF NOT EXISTS FOR (n:Record) ON (n.khScope, n.khSortName)",
    "CREATE INDEX kh_rg_conn_sort IF NOT EXISTS FOR (n:RecordGroup) ON (n.connectorId, n.khSortName)",
    "CREATE INDEX kh_rec_conn_updated IF NOT EXISTS FOR (n:Record) ON (n.connectorId, n.updatedAtTimestamp)",
    "CREATE INDEX kh_rec_scope_updated IF NOT EXISTS FOR (n:Record) ON (n.khScope, n.updatedAtTimestamp)",
    "CREATE INDEX kh_rg_conn_updated IF NOT EXISTS FOR (n:RecordGroup) ON (n.connectorId, n.updatedAtTimestamp)",
    "CREATE INDEX kh_rec_conn_created IF NOT EXISTS FOR (n:Record) ON (n.connectorId, n.createdAtTimestamp)",
    "CREATE INDEX kh_rec_scope_created IF NOT EXISTS FOR (n:Record) ON (n.khScope, n.createdAtTimestamp)",
    "CREATE INDEX kh_rg_conn_created IF NOT EXISTS FOR (n:RecordGroup) ON (n.connectorId, n.createdAtTimestamp)",
)
INDEX_WAIT_SECONDS = 120

# A (connectorId, prop) index gives rows in order only when ORDER BY names the prefix too, in the same direction;
# the id tiebreak after it is a partial sort within equal values, in either direction. The range predicate on the
# sort key comes first so a cursor seeks instead of scanning (an OR alone scans the index). A node deleted after
# the stamp keeps its scope until the next one, so the label is tested here too.
ORDERED = """
MATCH (n:{label}) USING INDEX n:{label}(connectorId, {prop})
WHERE n.connectorId = $c AND n.{prop} >= {low} AND n.khScope IN $open
  AND n.orgId = $org AND NOT n:KhPlaceholder AND NOT n:KhDeleted {keyset}
RETURN n.id AS id, n.{prop} AS sortKey
ORDER BY n.connectorId {sdir}, n.{prop} {sdir}, n.id {iddir}
LIMIT $limit
"""
# Few visible rows: seek each open scope, then sort what is found.
SEEK = """
MATCH (n:Record) USING INDEX n:Record(khScope, {prop})
WHERE n.khScope IN $open AND n.{prop} >= {low} AND n.connectorId = $c
  AND n.orgId = $org AND NOT n:KhPlaceholder AND NOT n:KhDeleted {keyset}
RETURN n.id AS id, n.{prop} AS sortKey
ORDER BY n.{prop} {sdir}, n.id {iddir}
LIMIT $limit
"""
NULLS = """
CALL () {{
    MATCH (n:Record) WHERE n.connectorId = $c RETURN n
    UNION
    MATCH (n:RecordGroup) WHERE n.connectorId = $c RETURN n
}}
WITH n WHERE n.khSortName IS NULL AND n.khScope IN $open AND n.orgId = $org AND NOT n:KhPlaceholder
  AND NOT n:KhDeleted {keyset}
RETURN n.id AS id ORDER BY n.id LIMIT $limit
"""
KEYSET = "AND n.{prop} {cmp}= $ks AND (n.{prop} {cmp} $ks OR n.id {idcmp} $kid)"

# The flatten's seed arms, for granted nodes outside every open scope.
SEEDS = """
CALL () {
    UNWIND $seeds AS gid MATCH (sd:Record {id: gid}) RETURN sd
    UNION
    UNWIND $seeds AS gid MATCH (sd:RecordGroup {id: gid}) RETURN sd
}
WITH sd
WHERE sd.connectorId = $c AND NOT sd:KhDeleted AND coalesce(sd.accessRule, 'OPEN') = 'OPEN'
  AND (sd.khScope IS NULL OR NOT sd.khScope IN $open)
  AND NOT EXISTS { MATCH above = (hid:RecordGroup)-[:NODE_RELATION*1..20]->(sd)
                   WHERE coalesce(hid.hideChildren, false)
                     AND all(h IN relationships(above) WHERE h.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']) }
OPTIONAL MATCH (sd) ((pc)-[h:NODE_RELATION]->(cc)
      WHERE h.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT'] AND NOT cc:KhDeleted AND NOT pc:KhHidesChildren
        AND coalesce(cc.accessRule, 'OPEN') = 'OPEN'
        AND (EXISTS { (cc)-[:INHERIT_PERMISSIONS]->(pc) } OR cc.id IN $granted)){1,50} (nc)
WITH collect(DISTINCT sd) + collect(DISTINCT nc) AS xs
UNWIND xs AS x
WITH DISTINCT x
WHERE x IS NOT NULL AND (x:Record OR x:RecordGroup) AND (x.khScope IS NULL OR NOT x.khScope IN $open)
  AND x.orgId = $org AND NOT x:KhPlaceholder
RETURN x.id AS id, x.khSortName AS khSortName, x.updatedAtTimestamp AS updatedAtTimestamp,
       x.createdAtTimestamp AS createdAtTimestamp,
       CASE WHEN x:RecordGroup THEN 'recordGroup' WHEN x:KhFolder THEN 'folder' ELSE 'record' END AS nodeType
"""

# One node, compactly (a million of them are held while stamping):
K, DEL, HIDES, RULE, PM, PH, FOLDER, ORG, NULLNAME, PARENT, NPARENTS, INH, OLD, NULLUPD, NULLCRE, ORIGIN = range(16)

# A timestamp the index would not order as a number (null, or stored as a string) counts as missing: the full
# query keeps it and sorts it where the scope path cannot.
_NOT_NUMBER = "NOT ({p} IS :: INTEGER NOT NULL OR {p} IS :: FLOAT NOT NULL)"
_NODE_FIELDS = f"""
    n.id AS id, n:KhDeleted AS del, n:KhHidesChildren AS hides, coalesce(n.accessRule, 'OPEN') AS rule,
    n.permissionModel AS pm, n:KhPlaceholder AS ph, n:KhFolder AS folder, n.orgId = $org AS sameOrg,
    n.khSortName IS NULL AS nullName, n.khScope AS old,
    {_NOT_NUMBER.format(p="n.updatedAtTimestamp")} AS nullUpd,
    {_NOT_NUMBER.format(p="n.createdAtTimestamp")} AS nullCre, n.origin AS origin,
    [(p)-[h:NODE_RELATION]->(n) WHERE h.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT'] | p.id] AS parents,
    [(n)-[:INHERIT_PERMISSIONS]->(t) | t.id] AS inherits
"""

# Stamps of one connector never overlap in this process (the startup loop and a sync's end can meet).
_stamp_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _q(client: Neo4jClient, cypher: str, params: dict | None = None) -> list[dict]:
    return await client.execute_query(cypher, parameters=params or {})


LISTS = "kh_lists"


@lru_cache(maxsize=512)
def lists_in_map(query: str, names: tuple[str, ...]) -> str:
    """``query`` with each ``$name`` of ``names`` read from the one map parameter ``$kh_lists``. Neo4j keys a
    cached plan on the size class of every list parameter; a list inside a map carries no size."""
    if not names:
        return query
    pattern = re.compile(r"\$(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")
    return pattern.sub(lambda m: f"${LISTS}.{m.group(1)}", query)


async def _ql(client: Neo4jClient, cypher: str, params: dict) -> list[dict]:
    """A listing statement, with its list parameters inside one map."""
    names = tuple(sorted(k for k, v in params.items() if isinstance(v, list)))
    if names:
        cypher = lists_in_map(cypher, names)
        params = {**{k: v for k, v in params.items() if k not in names}, LISTS: {k: params[k] for k in names}}
    return await client.execute_query(cypher, parameters=params)


async def _ensure_meta_constraint(client: Neo4jClient) -> None:
    """One meta per connector. An older build kept a plain index on the same property, which the constraint
    cannot coexist with, and concurrent first stamps could leave duplicates behind: both are cleared first."""
    rows = await _q(client, "SHOW CONSTRAINTS YIELD name WHERE name = $n RETURN name", {"n": META_CONSTRAINT})
    if rows:
        return
    # Of duplicates, the one with the highest generation is kept: it is the one syncs have been marking.
    await _q(client, """
        MATCH (m:KhScopeMeta) WITH m ORDER BY coalesce(m.generation, -1) DESC
        WITH m.connectorId AS c, collect(m) AS ms WHERE size(ms) > 1
        UNWIND ms[1..] AS extra DETACH DELETE extra
    """)
    await _q(client, "DROP INDEX kh_scope_meta_conn IF EXISTS")
    await _q(client, f"CREATE CONSTRAINT {META_CONSTRAINT} IF NOT EXISTS "
                     "FOR (m:KhScopeMeta) REQUIRE m.connectorId IS UNIQUE")


async def ensure_indexes(client: Neo4jClient, logger: logging.Logger | None = None) -> None:
    await _ensure_meta_constraint(client)
    names = []
    for stmt in INDEXES:
        names.append(stmt.split()[2])
        try:
            await _q(client, stmt)
        except Exception as e:
            # An equivalent index under another name serves the same hints.
            if "equivalent" not in str(e).lower():
                raise
            if logger:
                logger.info("kh scope: %s already covered by an equivalent index", stmt.split()[2])
    # A listing hints these indexes; while one is still populating the hint fails and the listing falls back.
    # Only these are waited on: an unrelated index that is slow or failed must not hold the stamp back.
    deadline = time.monotonic() + INDEX_WAIT_SECONDS
    while True:
        rows = await _q(client, "SHOW INDEXES YIELD name, state WHERE name IN $names AND state <> 'ONLINE' "
                                "RETURN name, state", {"names": names})
        if not rows:
            return
        if any(r["state"] == "FAILED" for r in rows) or time.monotonic() > deadline:
            raise RuntimeError(f"kh scope indexes not online: {[(r['name'], r['state']) for r in rows]}")
        await asyncio.sleep(1)


async def mark_stale(client: Neo4jClient, connector_id: str) -> int | None:
    """A sync is about to write: list this connector with the full query until it is re-stamped. KBs keep no
    meta: they are always listed by the full query. Returns the generation this sync owns."""
    rows = await _q(client, """
        MATCH (a:App {id: $c}) WHERE coalesce(a.type, '') <> 'KB'
        MERGE (m:KhScopeMeta {connectorId: $c})
        SET m.fresh = false, m.syncing = true, m.generation = coalesce(m.generation, 0) + 1
        RETURN m.generation AS generation
    """, {"c": connector_id})
    return rows[0]["generation"] if rows else None


async def mark_changed(client: Neo4jClient, connector_id: str) -> None:
    """A write outside a sync (a hard delete, a reindex, a move) changed the tree: list with the full query until
    the next stamp. Unlike ``mark_stale`` it leaves ``syncing`` alone, so the stamp that follows can run. During
    a sync the generation stays: that sync owns it, and the stamp at its end sees this write anyway."""
    await _q(client, """
        MATCH (m:KhScopeMeta {connectorId: $c})
        SET m.fresh = false,
            m.generation = CASE WHEN coalesce(m.syncing, false) THEN m.generation
                                ELSE coalesce(m.generation, 0) + 1 END
    """, {"c": connector_id})


async def sync_ended(client: Neo4jClient, connector_id: str, generation: int | None = None) -> None:
    """The sync that owns ``generation`` is over. A sync that was replaced must not clear the flag of the one that
    replaced it, so with a generation only that sync's mark is cleared."""
    await _q(client, """
        MATCH (m:KhScopeMeta {connectorId: $c}) WHERE $g IS NULL OR m.generation = $g
        SET m.syncing = false
    """, {"c": connector_id, "g": generation})


async def forget(client: Neo4jClient, connector_id: str) -> None:
    """The connector is gone: its meta and scope tree with it."""
    await _q(client, "CALL () { MATCH (k:KhScope {connectorId: $c}) DETACH DELETE k } IN TRANSACTIONS OF 5000 ROWS",
             {"c": connector_id})
    await _q(client, "MATCH (m:KhScopeMeta {connectorId: $c}) DETACH DELETE m", {"c": connector_id})


async def _still_current(client: Neo4jClient, connector_id: str, generation: int) -> bool:
    rows = await _q(client, """
        MATCH (m:KhScopeMeta {connectorId: $c}) WHERE m.generation = $g AND NOT coalesce(m.syncing, false)
        RETURN true AS ok
    """, {"c": connector_id, "g": generation})
    return bool(rows)


async def reset_syncing(client: Neo4jClient) -> None:
    """At startup, before any sync of this process starts, no sync is running: a flag left by a crash would block
    stamping for ever. It must not run once syncs have started, or it clears their flags too."""
    await _q(client, "MATCH (m:KhScopeMeta) WHERE m.syncing SET m.syncing = false")


async def stale_connectors(client: Neo4jClient) -> list[str]:
    """Apps (not KBs) without a fresh stamp of this version."""
    rows = await _q(client, """
        MATCH (a:App) WHERE coalesce(a.type, '') <> 'KB'
        OPTIONAL MATCH (m:KhScopeMeta {connectorId: a.id})
        WITH a, m WHERE m IS NULL OR NOT coalesce(m.fresh, false) OR coalesce(m.version, 0) <> $v
        RETURN a.id AS id
    """, {"v": STAMP_VERSION})
    return [r["id"] for r in rows]


def _node(label: str, r: dict) -> tuple:
    parents = r["parents"]
    parent = parents[0] if parents else None
    return (label, bool(r["del"]), bool(r["hides"]), r["rule"], r["pm"], bool(r["ph"]), bool(r["folder"]),
            bool(r["sameOrg"]), bool(r["nullName"]), parent, len(parents),
            parent is not None and parent in r["inherits"], r["old"], bool(r["nullUpd"]), bool(r["nullCre"]),
            r["origin"])


def _classify(c: tuple, p: tuple) -> str:
    if c[DEL] or p[HIDES]:
        return "dead"
    if p[K] == "RecordGroup" and p[PM] == "RECORD_GROUP_LEVEL":
        return "dead"
    inherits, rule = c[INH], c[RULE]
    if inherits and rule in ("STRICT", "OPEN"):
        return "free"
    if (not inherits and rule in ("STRICT", "OPEN")) or (inherits and rule == "RESTRICTED"):
        return "gate"
    return "block"


def compute_scopes(nodes: dict[str, tuple], connector_id: str, app_node: tuple) -> tuple[dict, dict]:
    """Breadth-first from the App, at most ``MAX_DEPTH`` hops: each reached node's scope, and each gate's parent
    scope (the App's is None)."""
    children: dict[str, list[str]] = defaultdict(list)
    for nid, n in nodes.items():
        if n[PARENT] is not None and (n[PARENT] in nodes or n[PARENT] == connector_id):
            children[n[PARENT]].append(nid)
    scope: dict[str, str] = {connector_id: connector_id}
    scope_parent: dict[str, str | None] = {connector_id: None}
    queue = deque([(connector_id, 0)])
    while queue:
        p, depth = queue.popleft()
        if depth >= MAX_DEPTH:
            continue
        p_node = app_node if p == connector_id else nodes[p]
        for c in children.get(p, ()):
            if c in scope:
                continue
            kind = _classify(nodes[c], p_node)
            if kind == "free":
                scope[c] = scope[p]
            elif kind == "gate":
                scope[c] = c
                scope_parent[c] = scope[p]
            else:
                continue
            queue.append((c, depth + 1))
    return scope, scope_parent


def origin_of(n: tuple) -> str:
    # The full query's coalesce keeps an empty string, so only a missing origin defaults.
    return "CONNECTOR" if n[ORIGIN] is None else n[ORIGIN]


async def _read_nodes(client: Neo4jClient, connector_id: str, org: str, nodes: dict[str, tuple]) -> None:
    # Groups are few: one query. Records are paged by the (connectorId, id) index, in its order, so each page
    # seeks to the cursor; ordering by id alone would re-read the whole connector for every page.
    for r in await _q(client, f"MATCH (n:RecordGroup) WHERE n.connectorId = $c RETURN {_NODE_FIELDS}",
                      {"c": connector_id, "org": org}):
        nodes[r["id"]] = _node("RecordGroup", r)
    after = ""
    while True:
        page = await _q(client, f"""
            MATCH (n:Record) USING INDEX n:Record(connectorId, id)
            WHERE n.connectorId = $c AND n.id > $after
            WITH n ORDER BY n.connectorId, n.id LIMIT $batch
            RETURN {_NODE_FIELDS}
        """, {"c": connector_id, "org": org, "after": after, "batch": BATCH})
        for r in page:
            nodes[r["id"]] = _node("Record", r)
        if len(page) < BATCH:
            return
        after = page[-1]["id"]


async def _foreign_children(client: Neo4jClient, connector_id: str) -> bool:
    """Whether the App or one of its nodes has a child of another (or no) connector: the walk lists those, the
    stamp, which reads this connector's nodes only, cannot."""
    rows = await _q(client, """
        CALL () {
            MATCH (a:App {id: $c})-[h:NODE_RELATION]->(x) WHERE h.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT'] RETURN x
            UNION
            MATCH (p:RecordGroup)-[h:NODE_RELATION]->(x) WHERE p.connectorId = $c AND h.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT'] RETURN x
            UNION
            MATCH (p:Record)-[h:NODE_RELATION]->(x) WHERE p.connectorId = $c AND h.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT'] RETURN x
        }
        WITH x WHERE (x:Record OR x:RecordGroup) AND coalesce(x.connectorId, '') <> $c
        RETURN 1 AS hit LIMIT 1
    """, {"c": connector_id})
    return bool(rows)


async def stamp(client: Neo4jClient, connector_id: str, logger: logging.Logger) -> dict[str, Any]:
    """Recompute and write one connector's scopes. Never runs while its sync does, nor twice at once here."""
    async with _stamp_locks[connector_id]:
        return await _stamp(client, connector_id, logger)


async def _stamp(client: Neo4jClient, connector_id: str, logger: logging.Logger) -> dict[str, Any]:
    t0 = time.perf_counter()
    apps = await _q(client, "MATCH (a:App {id: $c}) RETURN a.type AS type, a.permissionModel AS pm, a.orgId AS org",
                    {"c": connector_id})
    if not apps:
        await forget(client, connector_id)
        return {"connector": connector_id, "stamped": False, "reason": "no such App"}
    app = apps[0]
    if app["type"] == "KB":
        return {"connector": connector_id, "stamped": False, "eligible": False, "reason": "a KB"}
    await _ensure_meta_constraint(client)
    # A meta without a generation (left by an older build) would never compare equal below: give it one.
    meta = await _q(client, """
        MERGE (m:KhScopeMeta {connectorId: $c})
        ON CREATE SET m.fresh = false, m.syncing = false
        SET m.generation = coalesce(m.generation, 0)
        RETURN m.generation AS generation, coalesce(m.syncing, false) AS syncing
    """, {"c": connector_id})
    if meta[0]["syncing"]:
        return {"connector": connector_id, "stamped": False, "reason": "a sync is running"}
    generation = meta[0]["generation"]

    async def ineligible(reason: str) -> dict[str, Any]:
        await _q(client, """
            MATCH (m:KhScopeMeta {connectorId: $c}) WHERE m.generation = $g AND NOT coalesce(m.syncing, false)
            SET m.fresh = true, m.eligible = false, m.reason = $r, m.stampedAt = timestamp(), m.version = $v
        """, {"c": connector_id, "g": generation, "r": reason, "v": STAMP_VERSION})
        logger.info("kh scope: connector %s is listed by the full query (%s)", connector_id, reason)
        return {"connector": connector_id, "stamped": True, "eligible": False, "reason": reason}

    if (app["pm"] or "") == "APP_LEVEL":
        return await ineligible("the App opens everything")

    nodes: dict[str, tuple] = {}
    await _read_nodes(client, connector_id, app["org"], nodes)
    if any(n[NPARENTS] > 1 for n in nodes.values()):
        return await ineligible("a node has several parents")
    if any(n[K] == "RecordGroup" and n[PM] == "RECORD_GROUP_LEVEL" for n in nodes.values()):
        return await ineligible("a RECORD_GROUP_LEVEL group")
    if any(n[HIDES] for n in nodes.values()):
        return await ineligible("a node hides its children")
    if await _foreign_children(client, connector_id):
        return await ineligible("a child belongs to another connector")
    app_node = ("App", False, False, "OPEN", app["pm"], False, False, True, False, None, 0, False, None, False,
                False, None)

    scope, scope_parent = compute_scopes(nodes, connector_id, app_node)

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for nid, sid in scope.items():
        if nid == connector_id:
            continue
        n = nodes[nid]
        if n[PH] or not n[ORG]:
            continue
        counts[sid][2 if n[K] == "RecordGroup" else (1 if n[FOLDER] else 0)] += 1
    # Over every listable node, seeds included (they may be outside any scope): a sort over a property with nulls,
    # and an origin filter on a connector of mixed origins, are decided per connector from these.
    listable = [n for n in nodes.values() if not n[PH] and n[ORG] and not n[DEL]]
    null_names = sum(n[NULLNAME] for n in listable)
    null_updated = sum(n[NULLUPD] for n in listable)
    null_created = sum(n[NULLCRE] for n in listable)
    origins = sorted({origin_of(n) for n in listable})
    del listable

    await ensure_indexes(client, logger)
    # Not fresh while writing: a listing in between uses the full query.
    await _q(client, "MATCH (m:KhScopeMeta {connectorId: $c}) SET m.fresh = false", {"c": connector_id})
    # Only nodes whose scope changed: a re-stamp after a small sync writes a handful, not the whole connector.
    # Each batch first checks that no sync has started: one would be writing the same nodes (lock contention,
    # deadlocks in its transactions), and this stamp could not be marked fresh anyway.
    aborted = {"connector": connector_id, "stamped": False, "reason": "a sync started while stamping"}
    written = 0

    async def write(label: str, rows: list[dict]) -> bool:
        nonlocal written
        if not await _still_current(client, connector_id, generation):
            return False
        await _q(client, f"UNWIND $rows AS row MATCH (n:{label} {{id: row.id}}) SET n.khScope = row.s",
                 {"rows": rows})
        written += len(rows)
        return True

    for label in ("RecordGroup", "Record"):
        rows: list[dict] = []
        for nid, n in nodes.items():
            if n[K] == label and n[OLD] != scope.get(nid):
                rows.append({"id": nid, "s": scope.get(nid)})
            if len(rows) == BATCH:
                if not await write(label, rows):
                    return {**aborted, "written": written}
                rows = []
                # The diff runs on the event loop that also serves requests: yield between batches.
                await asyncio.sleep(0)
        if rows and not await write(label, rows):
            return {**aborted, "written": written}
    if not await _still_current(client, connector_id, generation):
        return {**aborted, "written": written}
    await _q(client, "CALL () { MATCH (k:KhScope {connectorId: $c}) DETACH DELETE k } IN TRANSACTIONS OF 5000 ROWS",
             {"c": connector_id})
    scope_rows = [{"gate": sid, "parent": parent, "nRecord": counts[sid][0], "nFolder": counts[sid][1],
                   "nGroup": counts[sid][2]} for sid, parent in scope_parent.items()]
    for i in range(0, len(scope_rows), BATCH):
        await _q(client, """
            UNWIND $rows AS row
            CREATE (:KhScope {connectorId: $c, gate: row.gate, parent: row.parent,
                              nRecord: row.nRecord, nFolder: row.nFolder, nGroup: row.nGroup})
        """, {"rows": scope_rows[i:i + BATCH], "c": connector_id})
    done = await _q(client, """
        MATCH (m:KhScopeMeta {connectorId: $c}) WHERE m.generation = $g AND NOT coalesce(m.syncing, false)
        SET m.fresh = true, m.eligible = true, m.reason = null, m.stampedAt = timestamp(), m.version = $v,
            m.nodes = $nodes, m.scopes = $scopes, m.nullNames = $nulls, m.nullUpdated = $nullUpd,
            m.nullCreated = $nullCre, m.origins = $origins
        RETURN m.stampedAt AS at
    """, {"c": connector_id, "g": generation, "nodes": len(scope) - 1, "scopes": len(scope_parent),
          "nulls": null_names, "nullUpd": null_updated, "nullCre": null_created, "origins": origins,
          "v": STAMP_VERSION})
    result = {"connector": connector_id, "stamped": bool(done), "eligible": True, "nodes": len(scope) - 1,
              "scopes": len(scope_parent), "unreached": len(nodes) + 1 - len(scope), "written": written,
              "stampedAt": done[0]["at"] if done else None, "seconds": round(time.perf_counter() - t0, 1)}
    if not done:
        result["reason"] = "a sync started while stamping; stays on the full query until the next stamp"
    logger.info("kh scope stamp: %s", result)
    return result


class ScopeTree:
    """One stamp's scope tree: each gate's parent scope and counts, the gates' child lists, and the total."""

    def __init__(self, rows: list[dict]) -> None:
        self.scopes = {r["gate"]: r for r in rows}
        self.everything = sum((r["nRecord"] or 0) + (r["nFolder"] or 0) + (r["nGroup"] or 0) for r in rows) or 1

    def open_scopes(self, app_id: str, granted: set[str]) -> list[str]:
        """The App's scope, and every granted gate whose parents are all open. Only granted gates can open, so
        this walks those and their chains, not every scope of the connector."""
        scopes = self.scopes
        memo: dict[str, bool] = {app_id: app_id in scopes}

        def is_open(sid: str) -> bool:
            chain = []
            cur = sid
            while cur not in memo:
                s = scopes.get(cur)
                if s is None or s["parent"] is None or cur not in granted:
                    memo[cur] = s is not None and s["parent"] is None
                    break
                chain.append(cur)
                cur = s["parent"]
            ok = memo[cur]
            for c in reversed(chain):
                memo[c] = ok
            return memo[sid]

        out = [app_id] if memo[app_id] else []
        out.extend(g for g in granted if g != app_id and g in scopes and is_open(g))
        return out


class ScopeCache:
    """One connector's scope tree in memory, keyed by its stamp time."""

    def __init__(self) -> None:
        self._by_app: dict[str, tuple[Any, ScopeTree]] = {}

    async def get(self, client: Neo4jClient, app_id: str, stamped_at: int | None) -> ScopeTree:
        hit = self._by_app.get(app_id)
        if hit and hit[0] == stamped_at:
            return hit[1]
        rows = await _q(client, """
            MATCH (k:KhScope {connectorId: $c})
            RETURN k.gate AS gate, k.parent AS parent, k.nRecord AS nRecord, k.nFolder AS nFolder, k.nGroup AS nGroup
        """, {"c": app_id})
        tree = ScopeTree(rows)
        self._by_app[app_id] = (stamped_at, tree)
        return tree

    def drop(self, app_id: str) -> None:
        self._by_app.pop(app_id, None)


def _ordered_rows(rows: list[dict], sdir: str, iddir: str) -> list[dict]:
    """By sort key in ``sdir``, then id in ``iddir``: two stable sorts, minor key first."""
    rows = sorted(rows, key=lambda r: r["id"], reverse=iddir == "DESC")
    return sorted(rows, key=lambda r: r["sortKey"], reverse=sdir == "DESC")


class Fallback(Exception):
    """This request is listed by the full query; ``reason`` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _meta(client: Neo4jClient, app_id: str) -> dict | None:
    rows = await _q(client, """
        MATCH (m:KhScopeMeta {connectorId: $c})
        RETURN m.fresh AS fresh, m.eligible AS eligible, coalesce(m.syncing, false) AS syncing,
               m.stampedAt AS stampedAt, m.generation AS generation, coalesce(m.version, 0) AS version,
               m.reason AS reason, m.nullNames AS nullNames,
               m.nullUpdated AS nullUpdated, m.nullCreated AS nullCreated, m.origins AS origins
    """, {"c": app_id})
    return rows[0] if rows else None


async def page_payload(
    client: Neo4jClient,
    cache: ScopeCache,
    app_id: str,
    org_id: str,
    granted_ids: list[str],
    limit: int,
    after: dict | None,
    *,
    include_total: bool,
    sort_field: str = "name",
    sort_dir: str = "ASC",
    direction: str = "next",
    origins: list[str] | None = None,
    connector_ids: list[str] | None = None,
    ordered_share: float = 0.01,
    granted_scopes: dict[str, str | None] | None = None,
    granted_stamp: tuple[Any, Any] | None = None,
) -> dict[str, Any]:
    """The page (``limit`` rows of id, sortKey, nullRank, in the full query's order for ``direction``), total and
    counts as the full query would return them. Raises ``Fallback`` when this connector is not listed by scope for
    this request right now.

    The order is ``_kh_v2_sort_cypher``'s: forward is (nullRank ASC, sortKey ``sort_dir``, id ASC); a previous
    page flips all three and returns the rows nearest the boundary first. ``origins`` and ``connector_ids`` keep
    a connector's nodes all or none (one origin, one connector), so they are decided here, not per node."""
    prop = SORT_PROPS.get(sort_field)
    if prop is None:
        raise Fallback(f"sort {sort_field!r} has no scope index")
    if sort_dir not in ("ASC", "DESC") or direction not in ("next", "prev"):
        raise Fallback(f"sort direction {sort_dir!r}/{direction!r}")
    m = await _meta(client, app_id)
    if m is None:
        raise Fallback("never stamped")
    if m["syncing"]:
        raise Fallback("a sync is running")
    if not m["fresh"]:
        raise Fallback("stale since the last write")
    if m["version"] != STAMP_VERSION:
        raise Fallback(f"stamped by version {m['version']}, this build reads {STAMP_VERSION}")
    if not m["eligible"]:
        raise Fallback(f"ineligible: {m['reason']}")
    nulls_in_prop = {"khSortName": m["nullNames"], "updatedAtTimestamp": m["nullUpdated"],
                     "createdAtTimestamp": m["nullCreated"]}[prop]
    if nulls_in_prop is None:
        raise Fallback("the stamp has no null counts")
    if nulls_in_prop and prop != "khSortName":
        raise Fallback(f"{nulls_in_prop} node(s) without a numeric {sort_field}")
    # The null bucket is walked forward only.
    if nulls_in_prop and direction == "prev":
        raise Fallback("a previous page with null names")
    excluded = False
    if origins:
        if m["origins"] is None:
            raise Fallback("the stamp has no origins")
        mine = set(m["origins"])
        if not mine <= set(origins):
            if mine & set(origins):
                raise Fallback("the origin filter splits this connector")
            excluded = True
    if connector_ids and app_id not in connector_ids:
        excluded = True
    if excluded:
        return {"page": [], "total": 0 if include_total else None, "nRecord": 0 if include_total else None,
                "nFolder": 0 if include_total else None, "nGroup": 0 if include_total else None,
                "path": "excluded", "openScopes": 0, "seedNodes": 0}

    tree = await cache.get(client, app_id, m["stampedAt"])
    if app_id not in tree.scopes:
        raise Fallback("the scope tree has no root")
    granted = set(granted_ids)
    open_ids = tree.open_scopes(app_id, granted)
    # A seed is a granted node outside every open scope. With the scope each grant was read with
    # (``granted_scopes``) only those are sought; without it every grant is. Those scopes count only when
    # they were read under the stamp this page reads (``granted_stamp``, taken before the grants were):
    # a restamp in between could have moved a grant out of an open scope, and it would be skipped here.
    if granted_scopes is None or granted_stamp != (m["stampedAt"], m["generation"]):
        seeds = sorted(granted)
    else:
        opened = set(open_ids)
        seeds = sorted(g for g in granted if granted_scopes.get(g) is None or granted_scopes[g] not in opened)
    extras = await _ql(client, SEEDS, {"seeds": seeds, "granted": sorted(granted), "c": app_id, "open": open_ids,
                                       "org": org_id}) if seeds else []
    counts = {"record": 0, "folder": 0, "recordGroup": 0}
    for sid in open_ids:
        s = tree.scopes[sid]
        counts["record"] += s["nRecord"]
        counts["folder"] += s["nFolder"]
        counts["recordGroup"] += s["nGroup"]
    for e in extras:
        counts[e["nodeType"]] += 1
    total = sum(counts.values())
    ordered = total / tree.everything >= ordered_share

    forward = direction == "next"
    sdir = sort_dir if forward else ("ASC" if sort_dir == "DESC" else "DESC")
    iddir = "ASC" if forward else "DESC"
    cmp, idcmp = (">" if sdir == "ASC" else "<"), (">" if iddir == "ASC" else "<")
    params: dict[str, Any] = {"c": app_id, "open": open_ids, "org": org_id, "limit": limit}
    fmt = {"prop": prop, "low": _LOW[prop], "sdir": sdir, "iddir": iddir}
    rows: list[dict] = []
    after_rank = None if after is None else after.get("nullRank")
    # Forward from a non-null boundary, or from the start; and backward from the null bucket, which on a
    # connector without null names means its last non-null rows (another connector's nulls put the cursor there).
    if after is None or after_rank == 0 or (not forward and after_rank == 1):
        keyset = ""
        if after is not None and after_rank == 0:
            keyset = KEYSET.format(prop=prop, cmp=cmp, idcmp=idcmp)
            params.update({"ks": after.get("sortKey"), "kid": after["id"]})
        recs = await _ql(client, (ORDERED.format(label="Record", keyset=keyset, **fmt) if ordered
                                  else SEEK.format(keyset=keyset, **fmt)), params)
        groups = await _ql(client, ORDERED.format(label="RecordGroup", keyset=keyset, **fmt), params)

        def beyond(key: str | int | float, nid: str) -> bool:
            if after is None or after_rank != 0:
                return True
            ks, kid = after.get("sortKey"), after["id"]
            if key != ks:
                return key > ks if sdir == "ASC" else key < ks
            return nid > kid if iddir == "ASC" else nid < kid

        extra = [{"id": e["id"], "sortKey": e[prop]} for e in extras
                 if e[prop] is not None and beyond(e[prop], e["id"])]
        merged = _ordered_rows(recs + groups + extra, sdir, iddir)[:limit]
        rows = [{"id": r["id"], "sortKey": r["sortKey"], "nullRank": 0} for r in merged]
    if len(rows) < limit and forward and nulls_in_prop:
        nid = after["id"] if after is not None and after_rank == 1 else None
        nulls = [e["id"] for e in extras if e[prop] is None and (nid is None or e["id"] > nid)]
        nkey = "AND n.id > $nid" if nid is not None else ""
        nulls += [r["id"] for r in await _ql(client, NULLS.format(keyset=nkey),
                                              {**params, "limit": limit - len(rows), "nid": nid})]
        rows += [{"id": i, "sortKey": None, "nullRank": 1} for i in sorted(set(nulls))[:limit - len(rows)]]
    # A restamp that started after the meta was read rewrote khScope under these reads: answer with the full query.
    again = await _meta(client, app_id)
    if again is None or again["stampedAt"] != m["stampedAt"] or again["generation"] != m["generation"] \
            or not again["fresh"]:
        raise Fallback("restamped while reading")
    return {
        "page": rows,
        "total": total if include_total else None,
        "nRecord": counts["record"] if include_total else None,
        "nFolder": counts["folder"] if include_total else None,
        "nGroup": counts["recordGroup"] if include_total else None,
        "path": "ordered" if ordered else "seek",
        "openScopes": len(open_ids),
        "seedNodes": len(extras),
    }
