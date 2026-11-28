"""The access stamps of kg.records: class, flags and path, written by the projection under the connector's lock, after the structural rows.

For the nodes a projection just wrote (the *seeds*) this module finds every record whose class can have changed (:func:`affected`: the seeds, everything below them in the
hierarchy and every member of a record group among them, to a fixpoint, read from the graph *and* from the rows kg held before, because an edge that is gone leaves
no trace in the graph; the whole connector when its app changed or what the stamps were compiled with, ``opens`` or "has a record-group-level group", changed), compiles each
with the class compiler over the closure of the graph, and writes the difference: ``class_id`` (interned in kg.scope_classes, with its anchors in kg.class_literals), ``self_lit``,
``seed``, ``own_grant``, ``under_deleted``, ``legacy_reason``, ``leaf`` and ``path``. Beside them: ``r13`` (a record of no connector that only a grant on it opens; NULL is not stamped,
which a reader takes as "walk") and ``kg.scopes``, the records and record groups that have an access list of their own, written in the same unit and hidden whenever their record is
(``restamp.HIDDEN_SCOPE``).

``class_id`` NULL is "legacy": the record keeps today's walk. ``legacy_reason`` 0 marks a record that is never readable (deleted or placeholder, the oracle's R11) and 1-8 a refusal
of the compiler (``REASONS``). A record that was never stamped has all of them NULL.

A change that reaches more than ``inline_max()`` records beyond its seeds is not restamped in the writer's unit: in that same unit the records it reaches that kg holds are hidden
(``fence_job``; a change to a whole connector hides the connector with a queue row of kind ``app``) and a restamp job is queued (restamp.py), so a reader never answers from a
stamp the change has outdated; the worker restamps them afterwards. A reach of more than ``REACH_MAX`` records is not found whole in the unit either: it stops there, hides the
connectors whole and leaves the walk below what it found to a subtree job (``restamp.enqueue_subtree``). The seeds themselves are always stamped in the unit. A stamp that reaches a connector the unit holds no lock for
raises :class:`locks.NeedLocks` before anything is written, and the unit starts again with the larger set.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import stampgraph
from app.services.graph_db.postgres.kg.dependents import (
    children_capped,
    children_of,
    held_apps,
    held_keys,
    parents_of,
)
from app.services.graph_db.postgres.kg.locks import NeedLocks, bump
from app.services.graph_db.postgres.kg.model import (
    COLL_CODE,
    KIND,
    MAX_ID,
    RECORD_COLLS,
    Key,
)
from app.services.graph_db.postgres.kg.reader import (
    RECORDS_BYTE_ORDER,
    load_sks,
    records_after,
)
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.services.permissions import classes

if TYPE_CHECKING:
    from app.core.db import Tx
    from app.services.permissions.ports import StampApp, StampNode

REASONS = {"deny": 0, "missing_node": 1, "too_many_paths": 2, "too_many_seeds": 3, "too_many_clauses": 4, "rgl_below_seed": 5, "deep_below_seed": 6, "no_app": 7, "foreign_parent": 8}
SELF = classes.SELF
MAX_DEPTH = 64                      # the primary chain stored in ``path``; deeper (or a cycle) leaves it NULL
INLINE_MAX = 20000                  # records a change may reach before they are hidden and restamped by the worker instead of in the writer's unit
REACH_MAX = 50_000                  # records the writer's unit finds and marks one by one; beyond it the connector is hidden whole and a job walks the rest (restamp.enqueue_subtree)
WALK_STEP = 1000                    # keys per read of the walk below a change


def inline_max() -> int:
    """Records a change may reach beyond its seeds before they are hidden and restamped by the worker (``PG_KG_INLINE_MAX``): a whole number, unset or empty is the default,
    anything else raises (``flush.check_env``)."""
    v = os.environ.get("PG_KG_INLINE_MAX", "")
    if not v:
        return INLINE_MAX
    if not (v.isascii() and v.isdigit()):
        raise ValueError(f"PG_KG_INLINE_MAX={v!r} is not understood: set it to a whole number of records, or leave it unset")
    return int(v)


@dataclass
class StampStats:
    seeds: int = 0
    affected: int = 0
    written: int = 0
    classes: int = 0
    legacy: int = 0
    whole_apps: int = 0
    deferred: int = 0                                    # connectors fenced whole and queued
    hidden: int = 0                                      # records hidden and queued
    keys: set = field(default_factory=set)               # every record-like key this call compiled (the affected set and the seeds)
    diffs: list[str] = field(default_factory=list)       # a dry run: the records whose stamps differ from a fresh compile


def _nks(keys: set[Key]) -> list[str]:
    return sorted(stampgraph.nk(c, i) for c, i in keys)


def aid(connector_nk: str) -> str:
    """The app id of a node's ``connector_id`` (a compiler id), or "" for none."""
    return stampgraph.split(connector_nk)[1]


async def app_nodes(tx: Tx, org: str, app_id: str) -> set[Key]:
    rows = await tx.fetch("""/* pipeshub:kg.stamp_app_nodes */ SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($2::text[]) AND n.doc->>'connectorId' = $3""",
                          org, ["records", "recordGroups"], app_id)
    return {(r["coll"], r["id"]) for r in rows}


async def stored_facts(tx: Tx, org: str, app_sk: int) -> tuple[bool, bool] | None:
    r = await tx.fetchrow("/* pipeshub:kg.stamp_facts */ SELECT opens, has_group_level FROM kg.projection_state WHERE org_id = $1 AND app_sk = $2 AND facts_set", org, app_sk)
    return (r["opens"], r["has_group_level"]) if r else None


async def stamped_over(tx: Tx, org: str, app_sk: int, limit: int) -> bool:
    """Does the connector hold more than ``limit`` stamped records? Bounded: reads at most ``limit + 1`` index entries."""
    n = await tx.fetchval("""/* pipeshub:kg.stamp_over */ SELECT count(*) FROM (SELECT 1 FROM kg.records WHERE org_id = $1 AND app_sk = $2 AND class_id IS NOT NULL LIMIT $3) t""", org, app_sk, limit + 1)
    return n > limit


async def affected(tx: Tx, org: str, seeds: set[Key], apps: dict[str, tuple[int, bool, bool]], *, defer_over: int | None = None,
                   cap: int | None = None) -> tuple[set[Key], set[str], set[str], set[Key]]:
    """Every record-like key whose class can differ after a change to ``seeds``, the connectors restamped whole, those of them left to a restamp job, and the keys found whose
    children were not read (empty unless ``cap`` stopped the walk).

    ``apps``: app id -> (sk, opens, has_group_level) as the graph says now. A connector is restamped whole when its app node is a seed or the stored facts differ. With ``defer_over``,
    a connector that holds more stamped records than that is not enumerated: it is returned in the third set for the caller to hide and queue. With ``cap``, the walk stops once it
    has found more than ``cap`` keys beyond the seeds, and every read it makes stops there too: a group of a million members costs the writer's unit ``cap`` rows, not a million."""
    whole: set[str] = set()
    deferred: set[str] = set()
    for app_id, (sk, opens, rgl) in apps.items():
        if ("apps", app_id) in seeds or await stored_facts(tx, org, sk) != (opens, rgl):
            whole.add(app_id)
            if defer_over is not None and await stamped_over(tx, org, sk, defer_over):
                deferred.add(app_id)
    out = {k for k in seeds if k[0] in RECORD_COLLS}
    for app_id in whole - deferred:
        out |= await app_nodes(tx, org, app_id)
    frontier = sorted(out)
    while frontier:
        nxt: set[Key] = set()
        for i in range(0, len(frontier), WALK_STEP):
            room = None if cap is None else cap + 1 - len((out | nxt) - seeds)
            # a read may also return keys found already (the seeds above all): it stops only once it has more rows than those and the room left
            got, cut = await children_capped(tx, org, set(frontier[i:i + WALK_STEP]), None if room is None else max(room, 1) + len(out | nxt))
            new = got - out - nxt
            if room is not None and (cut or len(new) >= room):
                # the parents of this step stay unread, so a child left out here is found again from them
                new = set(sorted(new)[:max(room, 1)])
                return out | nxt | new, whole, deferred, set(frontier[i:]) | (nxt - out) | new
            nxt |= new
        nxt -= out
        out |= nxt
        frontier = sorted(nxt)
    return out, whole, deferred, set()


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
    from app.services.graph_db.postgres.kg import restamp  # restamp imports this module
    bump(org, set(locked or ()), wide=org_wide)

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
    limit = inline_max()
    now = {a: (app_sks[a], f.opens, f.has_group_level) for a, f in facts.items()}
    a_set, whole, deferred, unread = await affected(tx, org, recs, now, defer_over=limit, cap=REACH_MAX)
    st.whole_apps = len(whole)
    # A record kg holds no row for yet is still in the change log (every committed graph change is) and is stamped when its own projection comes, from the graph as it is
    # then: restamping it here would compile it twice and leave a row of nothing but a key.
    a_set = (await held_keys(tx, org, a_set - every)) | (a_set & every)
    skip = frozenset(app_sks[x] for x in deferred)
    rest = a_set - every
    if unread:
        # more than the unit can find one by one: the connectors are hidden whole and a job walks down from what was found and from what was not read below
        st.hidden = await restamp.enqueue_subtree(tx, org, rest | (unread & every), skip_apps=skip, locked=locked, org_wide=org_wide)
        if st.hidden is None:
            # shortcut: a reach through rows of no connector or across connectors is still found whole in the unit; walk it by connector when that is seen at scale
            a_set, whole, deferred, unread = await affected(tx, org, recs, now, defer_over=limit)
            a_set = (await held_keys(tx, org, a_set - every)) | (a_set & every)
            skip, rest = frozenset(app_sks[x] for x in deferred), a_set - every
            st.hidden = 0
        else:
            a_set -= rest
    for a in sorted(whole):
        await w.app_facts_set(app_sks[a], opens=facts[a].opens, group_level=facts[a].has_group_level)
    for a in sorted(deferred):
        await restamp.enqueue_app(tx, org, app_sks[a], "app")
        st.deferred += 1
    if not unread and len(rest) > limit:
        st.hidden = await restamp.enqueue_rows(tx, org, rest, "reach", skip_apps=skip, locked=locked, org_wide=org_wide)
        a_set -= rest
    await stamp_keys(tx, org, a_set | every, st, app_sks=app_sks, facts=facts, skip_apps=deferred, known=seed_nodes, locked=locked, org_wide=org_wide)
    return st


def _need(app_ids: set[str], locked: set[str] | None, *, org_wide: bool) -> None:
    """The stamps reach connectors the unit holds no lock for: start again with them (before anything is written)."""
    if locked is None or org_wide:
        return
    if missing := {a for a in app_ids if a} - locked:
        raise NeedLocks(missing)


async def stamp_keys(tx: Tx, org: str, keys: set[Key], st: StampStats | None = None, *, app_sks: dict[str, int] | None = None,
                     facts: dict[str, StampApp] | None = None, dry: bool = False, skip_apps: set[str] | None = None, known: dict[str, StampNode] | None = None, locked: set[str] | None = None,
                     org_wide: bool = False) -> StampStats:
    """Compile and write the stamps of exactly these keys (the ones that exist as records or record groups). ``dry``: write nothing, list the differences in ``st.diffs``.
    ``skip_apps``: connectors a restamp job will stamp whole. ``known``: nodes the caller has just read, not read again.

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
    present = sorted(i for i in ids if i in nodes and nodes[i].kind in ("record", "group") and aid(nodes[i].connector_id) not in (skip_apps or ()))
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
    cur_scopes = await _current_scopes(tx, org, [sk_of[i] for i in present if i in sk_of])
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
    scopes: dict[int, tuple] = {}
    for i in present:
        if i not in sk_of:
            if dry:
                st.diffs.append(f"s|{stampgraph.split(i)[1]} (no key)")
            continue
        n, k = nodes[i], result[i]
        legacy: int | None = None
        class_id: int | None = None
        self_lit = seed = r13 = False
        if isinstance(k, classes.Refusal):
            # r13: a live record of no connector that did not come from a connector, opened by a grant on it alone. It keeps its refusal too, so a reader that does not know r13 walks.
            r13 = k.reason == "no_app" and n.kind == "record" and not n.deleted and not n.placeholder and n.origin not in (None, "CONNECTOR")
            legacy = REASONS.get(k.reason, 1)
            st.legacy += k.reason != "deny"
        else:
            class_id = class_ids[class_hash[i]]
            self_lit = any(SELF in c for c in k.clauses)
            seed = frozenset({SELF}) in k.clauses
        coll = KIND_COLL[n.kind]
        rows.append({"sk": sk_of[i], "coll": COLL_CODE[coll], "id": stampgraph.split(i)[1], "kind": KIND[coll], "path": paths.get(sk_of[i]), "class_id": class_id, "self_lit": self_lit, "seed": seed,
                     "own_grant": _own_grant(n), "under_deleted": _under_deleted(i, nodes), "legacy_reason": legacy, "leaf": n.kind == "record" and i not in has_children, "r13": r13})
        if (sc := _scope(n, facts[aid(n.connector_id)], i in has_children) if aid(n.connector_id) in facts else None) is not None:     # a scope belongs to a connector
            scopes[sk_of[i]] = (KIND[coll], app_sks.get(aid(n.connector_id)) or 0, *sc)
    changed = [r for r in rows if cur.get(r["sk"], _NONE)[0] != _stamp_tuple(r)]
    r13_changed = [r for r in rows if cur.get(r["sk"], _NONE)[1] != r["r13"]]
    set_scopes = [(k_, *v) for k_, v in scopes.items() if cur_scopes.get(k_) != v]
    drop_scopes = [r["sk"] for r in rows if r["sk"] in cur_scopes and r["sk"] not in scopes]
    if dry:
        by_sk = {r["sk"]: r["id"] for r in rows}
        st.diffs += [f"s|{r['id']}" for r in changed] + [f"r13|{r['id']}" for r in r13_changed if r not in changed]
        st.diffs += [f"sc|{by_sk[x[0]]}" for x in set_scopes] + [f"sc|{by_sk[x]}" for x in drop_scopes]
    else:
        if changed or r13_changed:
            want = {r["sk"] for r in changed} | {r["sk"] for r in r13_changed}
            await w.stamps_set([r for r in rows if r["sk"] in want])
            st.written += len(changed)
        if set_scopes:
            await w.scopes_set(set_scopes)
        if drop_scopes:
            await w.scopes_delete(drop_scopes)
    return st


def _scope(n: StampNode, f: StampApp, has_child: bool) -> tuple[bool, bool, bool, bool] | None:  # noqa: FBT001
    """(restricted, rule_restricted, needs_grant, deleted) when the node has an access list of its own (a scope), else None; the definition of the harness's ``scope_facts``
    (tests/permissions/scope_layer.py). A deleted node is a scope only while something hangs below it. ``special``: something other than a direct grant can admit a node of the
    app (it opens everything, or a record group declares its own readers). A hierarchy parent in another organisation cannot be represented on PG (row policy, and the
    parent query joins on org_id), so it makes no row; that is the one difference from ``scope_facts``."""
    bad = n.rule not in (None, "OPEN", "STRICT")
    breaks = any(p not in n.inherits for p in n.parents)
    inherits_a_parent = any(p in n.inherits for p in n.parents)
    if not (has_child if n.deleted else (bad or breaks or n.granted)):
        return None
    special = f.opens or f.has_group_level
    return (n.deleted or bad or breaks, n.deleted or bad, n.deleted or (not special and (bad or not inherits_a_parent)), n.deleted)


# The connector's record groups after a cursor in id order (no index by connector: the org's groups are few beside its records); its records come after them, by records_after.
STAMP_GROUPS = """/* pipeshub:kg.stamp_app_page */ SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'recordGroups' AND n.doc->>'connectorId' = $2
    AND ($3::text IS NULL OR n.id > $3) ORDER BY n.id LIMIT $4"""


def _after(cursor: str | None) -> tuple[str | None, str | None]:
    if cursor is None:
        return None, None
    c, i = stampgraph.split(cursor)
    return (RECORDS_BYTE_ORDER, "") if c == "records" else (c, i)      # a cursor older code wrote in the records: see RECORDS_BYTE_ORDER


async def stamp_app(tx: Tx, org: str, app_id: str, after: str | None, limit: int, st: StampStats | None = None, *, dry: bool = False,
                    locked: set[str] | None = None) -> str | None:
    """One page of the whole-connector restamp (the backfill's last step): the next ``limit`` record groups (in id order), then records (in byte order of id, at most 1,000 of
    them a page) after the cursor ``after``; returns the new cursor or None when done.
    The first page also stores what the stamps are compiled with. ``locked``: the connectors the unit holds; a page whose stamps reach another raises :class:`locks.NeedLocks` before it writes
    (a ``dry`` page writes nothing and reads under the connector's lock in a unit that stays open, so it passes none)."""
    if not dry:
        bump(org, {app_id})
    w = KgWriter(tx)
    app_sks, facts = await _facts(tx, org, w, {app_id})
    if app_id not in facts:
        return None
    if after is None and not dry:
        await w.app_facts_set(app_sks[app_id], opens=facts[app_id].opens, group_level=facts[app_id].has_group_level)
    c0, i0 = _after(after)
    rows = [] if c0 == RECORDS_BYTE_ORDER else [("recordGroups", r["id"]) for r in await tx.fetch(STAMP_GROUPS, org, app_id, i0, limit)]
    if len(rows) < limit:
        rows += [("records", i) for i in await records_after(tx, org, app_id, i0 if c0 == RECORDS_BYTE_ORDER else "", limit - len(rows))]
    if not rows:
        return None
    keys = set(rows)
    if not dry:      # a record kg holds no row for is not given one of nothing but stamps: its own projection writes the row
        keys = await held_keys(tx, org, keys)
        # A parent in another connector gained this page's child, which kg may not have held when that connector was stamped: its ``leaf`` is restamped now, under its lock (NeedLocks asks for it).
        keys |= {k for k, a in (await held_apps(tx, org, await parents_of(tx, org, keys))).items() if a != app_id}
        # And the mirror: what hangs below this page in another connector, stamped before its parent had a row, has no path (the whole subtree below it, down to a fixpoint).
        below = {k for k, a in (await held_apps(tx, org, await children_of(tx, org, keys))).items() if a != app_id}
        frontier = below
        while frontier:
            frontier = set(await held_apps(tx, org, await children_of(tx, org, frontier))) - below - keys
            below |= frontier
        keys |= below
    if not dry:      # every connector whose rows this page rewrites, not only its own
        bump(org, {a for a in (await held_apps(tx, org, keys)).values() if a})
    await stamp_keys(tx, org, keys, st, app_sks=app_sks, facts=facts, dry=dry, locked=locked)
    return stampgraph.nk(RECORDS_BYTE_ORDER if rows[-1][0] == "records" else rows[-1][0], rows[-1][1])


KIND_COLL = {"app": "apps", "group": "recordGroups", "record": "records"}
_STAMPS = ("path", "class_id", "self_lit", "seed", "own_grant", "under_deleted", "legacy_reason", "leaf")


def _stamp_tuple(r: dict) -> tuple:
    return tuple(r[c] for c in _STAMPS)


_NONE = ((), None)        # what kg holds for a row it has none of: no stamps, no r13


async def _current(tx: Tx, org: str, sks: list[int]) -> dict[int, tuple]:
    """What kg holds now for these rows: (their stamps as a tuple, r13)."""
    out: dict[int, tuple] = {}
    for i in range(0, len(sks), 5000):
        for r in await tx.fetch("""/* pipeshub:kg.stamp_current */ SELECT sk, path, class_id, self_lit, seed, own_grant, under_deleted, legacy_reason, leaf, r13 FROM kg.records
            WHERE org_id = $1 AND sk = ANY($2::bigint[])""", org, sks[i:i + 5000]):
            out[r["sk"]] = (tuple(r[c] for c in _STAMPS), r["r13"])
    return out


async def _current_scopes(tx: Tx, org: str, sks: list[int]) -> dict[int, tuple]:
    out: dict[int, tuple] = {}
    for i in range(0, len(sks), 5000):
        for r in await tx.fetch("""/* pipeshub:kg.stamp_current_scopes */ SELECT sk, kind, app_sk, restricted, rule_restricted, needs_grant, deleted FROM kg.scopes
            WHERE org_id = $1 AND sk = ANY($2::bigint[])""", org, sks[i:i + 5000]):
            out[r["sk"]] = (r["kind"], r["app_sk"], r["restricted"], r["rule_restricted"], r["needs_grant"], r["deleted"])
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
