"""The access stamps of kg.records: class, flags and path, written by the projection under the connector's lock, after the structural rows.

For the nodes a projection just wrote (the *seeds*) this module finds every record whose class can have changed (:func:`affected`: the seeds, everything below them in the
hierarchy and every member of a record group among them, to a fixpoint, read from the graph *and* from the rows kg held before, because an edge that is gone leaves
no trace in the graph; the whole connector when its app changed or what the stamps were compiled with, ``opens`` or "has a record-group-level group", changed), compiles each
with the class compiler over the closure of the graph, and writes the difference: ``class_id`` (interned in kg.scope_classes, with its anchors in kg.class_literals), ``self_lit``,
``seed``, ``own_grant``, ``under_deleted``, ``legacy_reason``, ``leaf`` and ``path``.

``class_id`` NULL is "legacy": the record keeps today's walk. ``legacy_reason`` 0 marks a record that is never readable (deleted or placeholder, the oracle's R11) and 1-8 a refusal
of the compiler (``REASONS``). A record that was never stamped has all of them NULL.

Every record a change reaches is restamped in the writer's unit: there is no hiding of a large reach for a worker to restamp yet (the fences and the restamp queue are a later change),
so a change under a very large folder takes as long as its subtree. A stamp that reaches a connector the unit holds no lock for raises :class:`locks.NeedLocks` before anything is
written, and the unit starts again with the larger set.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import stampgraph
from app.services.graph_db.postgres.kg.dependents import held_keys
from app.services.graph_db.postgres.kg.locks import NeedLocks
from app.services.graph_db.postgres.kg.model import (
    CODE_COLL,
    COLL_CODE,
    HIERARCHY,
    KIND,
    MAX_ID,
    RECORD_COLLS,
    Key,
)
from app.services.graph_db.postgres.kg.reader import load_sks
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.services.permissions import classes

if TYPE_CHECKING:
    from app.core.db import Tx
    from app.services.permissions.ports import StampApp, StampNode

REASONS = {"deny": 0, "missing_node": 1, "too_many_paths": 2, "too_many_seeds": 3, "too_many_clauses": 4, "rgl_below_seed": 5, "deep_below_seed": 6, "no_app": 7, "foreign_parent": 8}
SELF = classes.SELF
MAX_DEPTH = 64                      # the primary chain stored in ``path``; deeper (or a cycle) leaves it NULL


@dataclass
class StampStats:
    seeds: int = 0
    affected: int = 0
    written: int = 0
    classes: int = 0
    legacy: int = 0
    whole_apps: int = 0
    keys: set = field(default_factory=set)               # every record-like key this call compiled (the affected set and the seeds)
    diffs: list[str] = field(default_factory=list)       # a dry run: the records whose stamps differ from a fresh compile


def _ids(keys: set[Key]) -> list[str]:
    """The ids of ``keys`` for a query that filters on ``id``: the rows it returns are checked against the (collection, id) pairs, so equal ids of two collections never mix."""
    return sorted({k[1] for k in keys})


def _nks(keys: set[Key]) -> list[str]:
    return sorted(stampgraph.nk(c, i) for c, i in keys)


def aid(connector_nk: str) -> str:
    """The app id of a node's ``connector_id`` (a compiler id), or "" for none."""
    return stampgraph.split(connector_nk)[1]


async def _keys_of(tx: Tx, org: str, sks: set[int]) -> set[Key]:
    if not sks:
        return set()
    rows = await tx.fetch("/* pipeshub:kg.stamp_keys_of */ SELECT coll, id FROM kg.node_keys WHERE org_id = $1 AND sk = ANY($2::bigint[])", org, sorted(sks))
    return {(CODE_COLL[r["coll"]], r["id"]) for r in rows}


async def _kg_neighbours(tx: Tx, org: str, keys: set[Key], *, down: bool) -> set[Key]:
    """Children and members (``down``) or parents and groups (not) of ``keys`` in the rows kg held, which still name an edge the graph has since lost.
    Each step is an index lookup on the sks of the keys (primary keys, ``records_parent``, ``records_group``, the links' primary key and ``links_child``)."""
    sk_of = await load_sks(tx, org, {k for k in keys if k[0] in COLL_CODE})
    sks = sorted(sk_of.values())
    if not sks:
        return set()
    found: set[int] = set()
    if down:
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.stamp_kids_parent */ SELECT sk AS x FROM kg.records WHERE org_id = $1 AND parent_sk = ANY($2::bigint[])", org, sks)}
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.stamp_kids_group */ SELECT sk AS x FROM kg.records WHERE org_id = $1 AND group_sk = ANY($2::bigint[])", org, sks)}
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.stamp_kids_links */ SELECT child_sk AS x FROM kg.links WHERE org_id = $1 AND parent_sk = ANY($2::bigint[])", org, sks)}
    else:
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.stamp_ups_parent */ SELECT parent_sk AS x FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[]) AND parent_sk IS NOT NULL", org, sks)}
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.stamp_ups_group */ SELECT group_sk AS x FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[]) AND group_sk IS NOT NULL", org, sks)}
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.stamp_ups_links */ SELECT parent_sk AS x FROM kg.links WHERE org_id = $1 AND child_sk = ANY($2::bigint[])", org, sks)}
    return await _keys_of(tx, org, found)


async def _graph_down(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    ids = _ids(keys)
    kids = await tx.fetch("""/* pipeshub:kg.stamp_g_kids */ SELECT e.to_coll, e.to_id, e.from_coll, e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_id = ANY($2::text[])
        AND e.doc->>'relationshipType' = ANY($3::text[]) AND e.to_coll = ANY($4::text[])""", org, ids, list(HIERARCHY), list(RECORD_COLLS))
    members = await tx.fetch("""/* pipeshub:kg.stamp_g_members */ SELECT e.from_coll, e.from_id, e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo'
        AND e.to_id = ANY($2::text[]) AND e.from_coll = ANY($3::text[])""", org, ids, list(RECORD_COLLS))
    return {(r["to_coll"], r["to_id"]) for r in kids if (r["from_coll"], r["from_id"]) in keys} | {(r["from_coll"], r["from_id"]) for r in members if (r["to_coll"], r["to_id"]) in keys}


async def _graph_up(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    ids = _ids(keys)
    ups = await tx.fetch("""/* pipeshub:kg.stamp_g_ups */ SELECT e.from_coll, e.from_id, e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.to_id = ANY($2::text[])
        AND e.doc->>'relationshipType' = ANY($3::text[]) AND e.from_coll = ANY($4::text[])""", org, ids, list(HIERARCHY), list(RECORD_COLLS))
    return {(r["from_coll"], r["from_id"]) for r in ups if (r["to_coll"], r["to_id"]) in keys}


async def app_nodes(tx: Tx, org: str, app_id: str) -> set[Key]:
    rows = await tx.fetch("""/* pipeshub:kg.stamp_app_nodes */ SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($2::text[]) AND n.doc->>'connectorId' = $3""",
                          org, ["records", "recordGroups"], app_id)
    return {(r["coll"], r["id"]) for r in rows}


async def stored_facts(tx: Tx, org: str, app_sk: int) -> tuple[bool, bool] | None:
    r = await tx.fetchrow("/* pipeshub:kg.stamp_facts */ SELECT opens, has_group_level FROM kg.projection_state WHERE org_id = $1 AND app_sk = $2 AND facts_set", org, app_sk)
    return (r["opens"], r["has_group_level"]) if r else None


async def affected(tx: Tx, org: str, seeds: set[Key], apps: dict[str, tuple[int, bool, bool]]) -> tuple[set[Key], set[str]]:
    """Every record-like key whose class can differ after a change to ``seeds``, and the connectors restamped whole.

    ``apps``: app id -> (sk, opens, has_group_level) as the graph says now. A connector is restamped whole when its app node is a seed or the stored facts differ."""
    whole: set[str] = set()
    for app_id, (sk, opens, rgl) in apps.items():
        if ("apps", app_id) in seeds or await stored_facts(tx, org, sk) != (opens, rgl):
            whole.add(app_id)
    out = {k for k in seeds if k[0] in RECORD_COLLS}
    for app_id in whole:
        out |= await app_nodes(tx, org, app_id)
    frontier = set(out)
    while frontier:
        nxt = (await _graph_down(tx, org, frontier)) | (await _kg_neighbours(tx, org, frontier, down=True))
        nxt = {k for k in nxt if k[0] in RECORD_COLLS} - out
        out |= nxt
        frontier = nxt
    return out, whole


def _under_deleted(i: str, nodes: dict[str, StampNode]) -> bool:
    seen = {i}
    stack = list(nodes[i].parents)
    while stack:
        p = stack.pop()
        if p in seen or p not in nodes:
            continue
        seen.add(p)
        if nodes[p].deleted:
            return True
        stack += nodes[p].parents
    return False


def _own_grant(n: StampNode) -> bool:
    return n.granted and not n.deleted and n.kind in ("record", "group") and (not n.parents or any(p != n.connector_id for p in n.parents))


def _lit_key(v: int | str) -> tuple[int, int]:
    return (1, 0) if v == SELF else (0, int(v))


def _encode(clauses: frozenset[frozenset[str]], sk_of: dict[str, int]) -> list[list[int | str]]:
    """Clauses as sorted lists of node sks (SELF as "@self", last), themselves sorted: one canonical text per class."""
    enc = [sorted((SELF if x == SELF else sk_of[x] for x in c), key=_lit_key) for c in clauses]
    return sorted(enc, key=lambda c: [_lit_key(v) for v in c])


async def _paths(tx: Tx, org: str, sks: list[int]) -> dict[int, bytes | None]:
    """``path`` of each sk: the primary parents' sks, root first, 8 bytes big-endian each, self excluded; NULL for a cycle or a chain above MAX_DEPTH."""
    parent: dict[int, int | None] = {}
    frontier = set(sks)
    for _ in range(MAX_DEPTH + 1):
        want = sorted(frontier - parent.keys())
        if not want:
            break
        for r in await tx.fetch("/* pipeshub:kg.stamp_parent_sk */ SELECT sk, parent_sk FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[])", org, want):
            parent[r["sk"]] = r["parent_sk"]
        frontier = {p for sk in want if (p := parent.get(sk)) is not None}
    out: dict[int, bytes | None] = {}
    for sk in sks:
        chain, seen, cur = [], {sk}, parent.get(sk)
        ok = True
        while cur is not None:
            if cur in seen or len(chain) >= MAX_DEPTH or cur not in parent:      # a cycle, too deep, or a parent kg cannot show (not fetched: the chain went on past the levels read)
                ok = False
                break
            seen.add(cur)
            chain.append(cur)
            cur = parent.get(cur)
        out[sk] = b"".join(x.to_bytes(8, "big") for x in reversed(chain)) if ok else None
    return out


async def _facts(tx: Tx, org: str, w: KgWriter, app_ids: set[str]) -> tuple[dict[str, int], dict[str, StampApp]]:
    app_sks = await _app_sks(tx, org, app_ids)
    # an app whose node is gone keeps its key, but its records are refused (``no_app``), as in a graph that never had it
    alive = {r["id"] for r in await tx.fetch("/* pipeshub:kg.stamp_apps_alive */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = ANY($2::text[])", org, sorted(app_ids))}
    return app_sks, {a: await stampgraph.app(tx, org, a) for a in sorted(app_ids) if a in app_sks and a in alive}


async def stamp(tx: Tx, org: str, seeds: set[Key], *, also: set[Key] | None = None, locked: set[str] | None = None, org_wide: bool = False) -> StampStats:
    """Restamp everything a change to ``seeds`` can reach, plus ``also`` (nodes whose own stamps may change but whose descendants' do not: the parents of a node that gained or
    lost a child, whose ``leaf`` flips); returns what it did. Runs in the projection's unit, under the connector's lock, after the structural rows. ``locked``: the connectors the
    unit holds (with ``org_wide``, any): a stamp that reaches another raises :class:`NeedLocks` before anything is written."""
    recs = {k for k in seeds if k[0] in RECORD_COLLS}
    st = StampStats(seeds=len(recs))
    if not recs and not also:
        return st
    w = KgWriter(tx)
    every = recs | {k for k in (also or set()) if k[0] in RECORD_COLLS}
    seed_nodes = await stampgraph.nodes(tx, org, _nks(every))
    gone = {k for k in every if stampgraph.nk(*k) not in seed_nodes}
    app_ids = {aid(n.connector_id) for n in seed_nodes.values() if n.connector_id} | {k[1] for k in every if k[0] == "apps"} | await _former_apps(tx, org, gone)
    _need(app_ids, locked, org_wide=org_wide)
    app_sks, facts = await _facts(tx, org, w, app_ids)
    a_set, whole = await affected(tx, org, recs, {a: (app_sks[a], f.opens, f.has_group_level) for a, f in facts.items()})
    st.whole_apps = len(whole)
    # A record kg holds no row for yet is still in the change log (every committed graph change is) and is stamped when its own projection comes, from the graph as it is
    # then: restamping it here would compile it twice and leave a row of nothing but a key.
    a_set = (await held_keys(tx, org, a_set - every)) | (a_set & every)
    for a in sorted(whole):
        await w.app_facts_set(app_sks[a], opens=facts[a].opens, group_level=facts[a].has_group_level)
    await stamp_keys(tx, org, a_set | every, st, app_sks=app_sks, facts=facts, known=seed_nodes, locked=locked, org_wide=org_wide)
    return st


def _need(app_ids: set[str], locked: set[str] | None, *, org_wide: bool) -> None:
    """The stamps reach connectors the unit holds no lock for: start again with them (before anything is written)."""
    if locked is None or org_wide:
        return
    if missing := {a for a in app_ids if a} - locked:
        raise NeedLocks(missing)


async def stamp_keys(tx: Tx, org: str, keys: set[Key], st: StampStats | None = None, *, app_sks: dict[str, int] | None = None,
                     facts: dict[str, StampApp] | None = None, dry: bool = False, known: dict[str, StampNode] | None = None, locked: set[str] | None = None,
                     org_wide: bool = False) -> StampStats:
    """Compile and write the stamps of exactly these keys (the ones that exist as records or record groups). ``dry``: write nothing, list the differences in ``st.diffs``.
    ``known``: nodes the caller has just read, not read again.

    Raises :class:`NeedLocks`, before writing, when a stamp reaches a connector the unit does not hold (``locked``, unless ``org_wide``)."""
    st = st or StampStats()
    w = KgWriter(tx)
    app_sks = dict(app_sks or {})
    facts = dict(facts or {})
    keys = {k for k in keys if len(k[1]) <= MAX_ID}       # kg holds no key for a longer id (a dry run must not call that a difference); it still counts in the closure as a node of the graph
    ids = _nks(keys)
    nodes = await stampgraph.closure(tx, org, ids, known=known)
    if not dry:
        _need({aid(n.connector_id) for n in nodes.values() if n.connector_id}, locked, org_wide=org_wide)
    present = sorted(i for i in ids if i in nodes and nodes[i].kind in ("record", "group"))
    st.affected += len(present)
    st.keys |= {stampgraph.split(i) for i in present}
    if not present:
        return st
    by_app: dict[str, list[str]] = {}
    for i in present:
        by_app.setdefault(nodes[i].connector_id, []).append(i)
    more = {aid(a) for a in by_app if a and aid(a) not in facts}
    if more:
        extra_sks, extra_facts = await _facts(tx, org, w, more)
        app_sks |= extra_sks
        facts |= extra_facts
    shallow = classes.check_shallow(nodes) is None
    result: dict[str, classes.Klass | classes.Refusal] = {}
    for a, members in by_app.items():
        f = facts.get(aid(a))
        comp = classes.Compiler(nodes, a, opens=f.opens, group_level=f.has_group_level, shallow=shallow) if f else None
        for i in members:
            result[i] = comp.compile(i) if comp else classes.Refusal("no_app")
    literals = {x for k in result.values() if isinstance(k, classes.Klass) for c in k.clauses for x in c if x != SELF}
    sk = await _sks(tx, org, w, {stampgraph.split(i) for i in {*present, *literals} if i in nodes}, create=not dry)
    sk_of = {i: sk[stampgraph.split(i)] for i in {*present, *literals} if i in nodes and stampgraph.split(i) in sk}
    paths = await _paths(tx, org, [sk_of[i] for i in present if i in sk_of])
    group_of = await _group_sks(tx, org, [sk_of[i] for i in present if i in sk_of])
    cur = await _current(tx, org, [sk_of[i] for i in present if i in sk_of])
    kids = await _has_children(tx, org, [sk_of[i] for i in present if i in sk_of])
    has_children = {i for i in present if sk_of.get(i) in kids}
    wanted: dict[bytes, tuple[int, int | None, str]] = {}
    class_hash: dict[str, bytes] = {}
    for i in present:
        k = result[i]
        if not isinstance(k, classes.Klass):
            continue
        a_sk = app_sks.get(aid(nodes[i].connector_id))
        if i not in sk_of or a_sk is None or any(x != SELF and x not in sk_of for c in k.clauses for x in c):
            result[i] = classes.Refusal("missing_node")           # a literal that kg cannot name yet: fail closed
            continue
        enc = _encode(k.clauses, sk_of)
        g_sk = group_of.get(sk_of[i])
        h = hashlib.sha256(json.dumps([a_sk, g_sk, enc], separators=(",", ":")).encode()).digest()
        class_hash[i] = h
        wanted.setdefault(h, (a_sk, g_sk, json.dumps(enc, separators=(",", ":"))))
    class_ids = await _intern(tx, org, w, wanted, st, create=not dry)
    rows: list[dict] = []
    for i in present:
        if i not in sk_of:
            if dry:
                st.diffs.append(f"s|{stampgraph.split(i)[1]} (no key)")
            continue
        n, k = nodes[i], result[i]
        legacy: int | None = None
        class_id: int | None = None
        self_lit = seed = False
        if isinstance(k, classes.Refusal):
            legacy = REASONS.get(k.reason, 1)
            st.legacy += k.reason != "deny"
        else:
            class_id = class_ids[class_hash[i]]
            self_lit = any(SELF in c for c in k.clauses)
            seed = frozenset({SELF}) in k.clauses
        coll = KIND_COLL[n.kind]
        rows.append({"sk": sk_of[i], "coll": COLL_CODE[coll], "id": stampgraph.split(i)[1], "kind": KIND[coll], "path": paths.get(sk_of[i]), "class_id": class_id, "self_lit": self_lit, "seed": seed,
                     "own_grant": _own_grant(n), "under_deleted": _under_deleted(i, nodes), "legacy_reason": legacy, "leaf": n.kind == "record" and i not in has_children})
    changed = [r for r in rows if cur.get(r["sk"]) != _stamp_tuple(r)]
    if dry:
        st.diffs += [f"s|{r['id']}" for r in changed]
    elif changed:
        st.written += await w.stamps_set(changed)
    return st


def _after(cursor: str | None) -> tuple[str | None, str | None]:
    return (None, None) if cursor is None else stampgraph.split(cursor)


async def stamp_app(tx: Tx, org: str, app_id: str, after: str | None, limit: int, st: StampStats | None = None, *, dry: bool = False) -> str | None:
    """One page of the whole-connector restamp (the backfill's last step): the next ``limit`` records and groups after the cursor ``after`` (ordered by collection and id); returns the new cursor or None when done.
    The first page also stores what the stamps are compiled with."""
    w = KgWriter(tx)
    app_sks, facts = await _facts(tx, org, w, {app_id})
    if app_id not in facts:
        return None
    if after is None and not dry:
        await w.app_facts_set(app_sks[app_id], opens=facts[app_id].opens, group_level=facts[app_id].has_group_level)
    rows = await tx.fetch("""/* pipeshub:kg.stamp_app_page */ SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($2::text[]) AND n.doc->>'connectorId' = $3
        AND ($4::text IS NULL OR (n.coll, n.id) > ($4::text, $5::text)) ORDER BY n.coll, n.id LIMIT $6""", org, ["records", "recordGroups"], app_id, *_after(after), limit)
    if not rows:
        return None
    keys = {(r["coll"], r["id"]) for r in rows}
    if not dry:      # a record kg holds no row for is not given one of nothing but stamps: its own projection writes the row
        keys = await held_keys(tx, org, keys)
    await stamp_keys(tx, org, keys, st, app_sks=app_sks, facts=facts, dry=dry)
    return stampgraph.nk(rows[-1]["coll"], rows[-1]["id"])


KIND_COLL = {"app": "apps", "group": "recordGroups", "record": "records"}
_STAMPS = ("path", "class_id", "self_lit", "seed", "own_grant", "under_deleted", "legacy_reason", "leaf")


def _stamp_tuple(r: dict) -> tuple:
    return tuple(r[c] for c in _STAMPS)


async def _current(tx: Tx, org: str, sks: list[int]) -> dict[int, tuple]:
    """What kg holds now for these rows: their stamps, as a tuple."""
    out: dict[int, tuple] = {}
    for i in range(0, len(sks), 5000):
        for r in await tx.fetch("""/* pipeshub:kg.stamp_current */ SELECT sk, path, class_id, self_lit, seed, own_grant, under_deleted, legacy_reason, leaf FROM kg.records
            WHERE org_id = $1 AND sk = ANY($2::bigint[])""", org, sks[i:i + 5000]):
            out[r["sk"]] = tuple(r[c] for c in _STAMPS)
    return out


async def _sks(tx: Tx, org: str, w: KgWriter, keys: set[Key], *, create: bool = True) -> dict[Key, int]:
    have = await load_sks(tx, org, keys)
    missing = keys - have.keys()
    return {**have, **(await w.nodes_upsert(missing) if missing and create else {})}


async def _app_sks(tx: Tx, org: str, app_ids: set[str]) -> dict[str, int]:
    got = await load_sks(tx, org, {("apps", a) for a in app_ids})
    return {k[1]: v for k, v in got.items()}


async def _former_apps(tx: Tx, org: str, gone: set[Key]) -> set[str]:
    """The connector a node that no longer exists was stamped under (its kg row says): its former children and members are restamped through it."""
    ks = sorted((COLL_CODE[k[0]], k[1]) for k in gone if k[0] in COLL_CODE)
    if not ks:
        return set()
    rows = await tx.fetch("""/* pipeshub:kg.stamp_gone_apps */ SELECT a.id FROM kg.node_keys n JOIN unnest($2::smallint[], $3::text[]) AS k(c, i) ON n.coll = k.c AND n.id = k.i
        JOIN kg.records r ON r.org_id = n.org_id AND r.sk = n.sk JOIN kg.node_keys a ON a.org_id = r.org_id AND a.sk = r.app_sk AND a.coll = 1 WHERE n.org_id = $1""",
                          org, [c for c, _ in ks], [i for _, i in ks])
    return {r["id"] for r in rows}


async def _has_children(tx: Tx, org: str, sks: list[int]) -> set[int]:
    """The sks among ``sks`` that kg holds a child of: a row naming it as parent, or a hierarchy link (the unit has written its rows already). It is what the structural rows say, so
    the flag follows the same rows as the grants' (reader.load_known_children)."""
    out: set[int] = set()
    for i in range(0, len(sks), 5000):
        rows = await tx.fetch("""/* pipeshub:kg.stamp_has_children */ SELECT p.sk FROM unnest($2::bigint[]) AS p(sk) WHERE
            EXISTS (SELECT 1 FROM kg.records r WHERE r.org_id = $1 AND r.parent_sk = p.sk)
            OR EXISTS (SELECT 1 FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = p.sk AND l.kind = 1)""", org, sks[i:i + 5000])
        out |= {r["sk"] for r in rows}
    return out


async def _group_sks(tx: Tx, org: str, sks: list[int]) -> dict[int, int | None]:
    out: dict[int, int | None] = {}
    for i in range(0, len(sks), 5000):
        for r in await tx.fetch("/* pipeshub:kg.stamp_group */ SELECT sk, group_sk FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[])", org, sks[i:i + 5000]):
            out[r["sk"]] = r["group_sk"]
    return out


async def _intern(tx: Tx, org: str, w: KgWriter, wanted: dict[bytes, tuple[int, int | None, str]], st: StampStats, *, create: bool = True) -> dict[bytes, int]:
    """The id of each class (by hash), creating the ones kg does not hold, with their anchors."""
    if not wanted:
        return {}
    hashes = sorted(wanted)
    have = {bytes(r["hash"]): r["id"] for r in await tx.fetch("/* pipeshub:kg.stamp_classes */ SELECT hash, id FROM kg.scope_classes WHERE org_id = $1 AND hash = ANY($2::bytea[])", org, hashes)}
    new = [h for h in hashes if h not in have]
    if new and not create:
        return {**have, **dict.fromkeys(new, -1)}                             # a class kg does not hold: its record's stamp differs
    if new:
        ids = [r[0] for r in await tx.fetch("/* pipeshub:kg.stamp_class_ids */ SELECT nextval('kg.class_id_seq') FROM generate_series(1, $1::int)", len(new))]
        rows = []
        anchors: list[tuple[int, int]] = []
        for h, cid in zip(new, ids, strict=True):
            a_sk, g_sk, text = wanted[h]
            rows.append((cid, a_sk, g_sk, text, h))
            have[h] = cid
            for clause in json.loads(text):
                if not clause:
                    anchors.append((a_sk, cid))                              # an always-true clause is anchored on its app
                elif clause != [SELF]:
                    anchors.append((max(x for x in clause if x != SELF), cid))   # the clause's largest literal; {SELF} alone has no anchor (the row's seed flag)
        await w.classes_set(rows)
        await w.class_literals_set(sorted(set(anchors)))
        st.classes += len(new)
    return have
