"""The placement marks of a grant (kg.scope_members, kg_0025): what lets a browse find a viewer's chain-tops from the few grants that can be one, instead of from every grant.

A chain-top (``kh_chain_tops``) is a node the viewer holds a direct OPEN grant on that has no hierarchy parent they may open; the browse lists it under its own record groups the
viewer may open, else under the App. Two marks of each grant row, functions of kg's own rows only (no graph row, no stamp):

- ``parent_held``: the same principal holds, with the same type, a grant on a hierarchy parent of the node (its primary parent or a hierarchy link) that is a live, OPEN,
  non-placeholder record or group of the node's connector and of the organisation. Whoever holds the grant then holds that parent's, which the check admits by itself (the walk's
  seed arm, ``access._is_seed``; kg's class of such a node has the ``@self`` clause), so the node is no chain-top of theirs.
- ``node_group``: the node's one record group, for a record of exactly one group (``group_sk`` and no group link); NULL otherwise. A record whose group the viewer may open is
  listed under that group, never under the App.
- ``node_parent`` (kg_0027): the node's one hierarchy parent, for a live (not deleted, no placeholder, of the organisation), OPEN record with a ``node_group`` whose only
  hierarchy parent is its primary one (``parent_sk`` and no hierarchy link); NULL otherwise. With it a browse of the root has every row the chain-top rule reads of the node
  (its parents and own groups are that parent and that group) from the grant alone; without it the browse reads the node's rows.

All three default to what lists every grant as before (false, NULL, NULL), and a mark is only ever a reason to leave a grant out: a mark that is late can cost reads, never a chain-top.
The projection keeps them exact in the unit that changes what they read (:func:`refresh`), the bootstrap fills an older copy in place, and the verify refuses a copy that holds a
mark its rows no longer give (:data:`BY_APP`, ``unsafe``). The writer only updates grant rows that exist: a grant another unit deleted meanwhile is never made again.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.kg.reader import seeks
from app.services.graph_db.postgres.kg.writers import KgWriter

if TYPE_CHECKING:
    from app.core.db import Tx

CHUNK = 5000
# The marks a grant row ``m`` should carry, from the rows of its node and of the node's parents. Every lookup by key is a seek (as kg.hierarchy, under reader.seeks): a range on
# the key ordered as only the key's own index returns it (a unique key's first row at or after it, kept only when it is the key). With ``k = m.k`` any index on the org answers
# too: in a new install's first drain (no statistics, the rows written by the same unit) the planner read each grant's node by records_pkey bounded on the org alone and its
# parents by records_app, once per grant row, and the unit ran into the statement timeout on every retry.
_WANT = """LEFT JOIN LATERAL (SELECT
      EXISTS (SELECT 1 FROM (SELECT r.parent_sk AS sk UNION ALL (SELECT l.parent_sk FROM kg.links l WHERE l.org_id = $1
            AND (l.child_sk, l.kind) >= (r.sk, 1) AND (l.child_sk, l.kind) <= (r.sk, 1) ORDER BY l.child_sk, l.kind)) p
          CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.sk >= p.sk ORDER BY k.sk LIMIT 1) s WHERE s.sk = p.sk OFFSET 0) pr
        WHERE pr.kind IN (2, 3) AND pr.app_sk = r.app_sk AND NOT pr.deleted AND NOT pr.placeholder AND NOT pr.alien AND coalesce(pr.rule, 'OPEN') = 'OPEN'
          AND EXISTS (SELECT 1 FROM (SELECT k.principal_sk, k.node_sk, k.gtype FROM kg.scope_members k WHERE k.org_id = $1 AND (k.principal_sk, k.node_sk, k.gtype) >= (m.principal_sk, pr.sk, m.gtype)
              ORDER BY k.principal_sk, k.node_sk, k.gtype LIMIT 1) q WHERE q.principal_sk = m.principal_sk AND q.node_sk = pr.sk AND q.gtype = m.gtype)) AS held,
      g.grp,
      CASE WHEN g.grp IS NOT NULL AND r.parent_sk IS NOT NULL AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien AND coalesce(r.rule, 'OPEN') = 'OPEN'
        AND NOT EXISTS (SELECT 1 FROM (SELECT l.child_sk, l.kind FROM kg.links l WHERE l.org_id = $1 AND (l.child_sk, l.kind) >= (r.sk, 1) ORDER BY l.child_sk, l.kind LIMIT 1) l
          WHERE l.child_sk = r.sk AND l.kind = 1) THEN r.parent_sk END AS par
    FROM (SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.sk >= m.node_sk ORDER BY k.sk LIMIT 1) s WHERE s.sk = m.node_sk OFFSET 0) r
    CROSS JOIN LATERAL (SELECT CASE WHEN r.kind = 3 AND NOT EXISTS (SELECT 1 FROM (SELECT l.child_sk, l.kind FROM kg.links l WHERE l.org_id = $1 AND (l.child_sk, l.kind) >= (r.sk, 2)
        ORDER BY l.child_sk, l.kind LIMIT 1) l WHERE l.child_sk = r.sk AND l.kind = 2) THEN r.group_sk END AS grp) g
    WHERE r.kind IN (2, 3) AND r.app_sk = m.app_sk) x ON true"""
_COLS = ("m.principal_sk, m.node_sk, m.gtype, m.parent_held, m.node_group, m.node_parent, coalesce(x.held, false) AS want_held, x.grp AS want_group, "
         "x.par AS want_parent")
# The grants on the nodes $2 (distinct), a seek of scope_members_by_node per node.
ON_NODES = f"""/* pipeshub:kg.grant_marks_on */ SELECT {_COLS} FROM unnest($2::bigint[]) AS n(sk)
    CROSS JOIN LATERAL (SELECT * FROM kg.scope_members m WHERE m.org_id = $1 AND m.node_sk >= n.sk AND m.node_sk <= n.sk ORDER BY m.node_sk) m {_WANT}"""
# The connector $2's grants after the key ($3, $4, $5), $6 of them in key order: a page of the fill and of the verify.
BY_APP = f"""/* pipeshub:kg.grant_marks_by_app */ SELECT {_COLS} FROM (SELECT * FROM kg.scope_members m WHERE m.org_id = $1 AND m.app_sk = $2
      AND (m.app_sk, m.node_sk, m.principal_sk, m.gtype) > ($2, $3, $4, $5) ORDER BY m.app_sk, m.node_sk, m.principal_sk, m.gtype LIMIT $6) m {_WANT}
    ORDER BY m.node_sk, m.principal_sk, m.gtype"""
# The hierarchy children of the nodes $2, of the parent's own connector, that hold a grant: their parent_held reads the parents' rows and grants. A child of another connector
# reads nothing of this parent (parent_held counts a parent of the node's own connector only), and its grants are written under that connector's lock alone.
CHILDREN = """/* pipeshub:kg.grant_marks_children */ SELECT c.sk FROM unnest($2::bigint[]) AS q(sk)
    CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.sk >= q.sk ORDER BY k.sk LIMIT 1) s WHERE s.sk = q.sk OFFSET 0) p
    CROSS JOIN LATERAL ((SELECT r.sk, r.app_sk FROM kg.records r WHERE r.org_id = $1 AND r.parent_sk >= p.sk AND r.parent_sk <= p.sk ORDER BY r.parent_sk, r.sk)
      UNION ALL (SELECT x.sk, x.app_sk FROM (SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND (l.parent_sk, l.kind) >= (p.sk, 1) AND (l.parent_sk, l.kind) <= (p.sk, 1)
          ORDER BY l.parent_sk, l.kind, l.child_sk) l
        CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.sk >= l.child_sk ORDER BY k.sk LIMIT 1) s WHERE s.sk = l.child_sk OFFSET 0) x)) c
    WHERE c.app_sk = p.app_sk
      AND EXISTS (SELECT 1 FROM (SELECT m.node_sk FROM kg.scope_members m WHERE m.org_id = $1 AND m.node_sk >= c.sk ORDER BY m.node_sk LIMIT 1) m WHERE m.node_sk = c.sk)"""
# What of a node's own row a mark reads (of the node, or of it as a parent), before a projection writes the row.
HELD = "/* pipeshub:kg.grant_marks_held */ SELECT sk, kind, app_sk, group_sk, deleted, placeholder, alien, rule FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[])"
MARKED = ("kind", "app_sk", "group_sk", "deleted", "placeholder", "alien", "rule")
# The viewer's grants of the connector $3 that may be chain-tops (no parent the same principal holds covers them), with each node's group and parent marks: the grants of every principal
# the viewer holds in it (kg.principal_closure: the user, their groups, roles, teams and organisation, and the source accounts linked in the connector with theirs). Each
# principal's range of scope_members_tops, in its order (as kg.hierarchy profiles it, sorts off): with no statistics the planner read the connector's grants by
# scope_members_by_app once per principal.
# One row of three arrays: a viewer can hold tens of thousands of grants, and as a row each they took longer to decode than to read (as ``read.LITERALS``).
CANDIDATES = """/* pipeshub:kg.chain_top_grants */ SELECT coalesce(array_agg(m.node_sk), '{}') AS sks, coalesce(array_agg(m.node_group), '{}') AS grps,
      coalesce(array_agg(m.node_parent), '{}') AS pars FROM kg.node_keys u
    JOIN kg.principal_closure c ON c.org_id = u.org_id AND c.user_sk = u.sk AND c.app_sk IN (0, $3)
    CROSS JOIN LATERAL (SELECT m.node_sk, m.node_group, m.node_parent FROM kg.scope_members m WHERE m.org_id = $1
        AND m.principal_sk >= c.principal_sk AND m.principal_sk <= c.principal_sk AND m.app_sk = $3 AND NOT m.parent_held AND m.gtype <> '@uar'
        ORDER BY m.principal_sk, m.app_sk, m.node_sk OFFSET 0) m
    WHERE u.org_id = $1 AND u.coll = 4 AND u.id = $2"""
# The granted rows of the connector ``$3`` among ids ``$2`` that a browse may list as chain-tops (``kg.listing.CHAIN_GRANTED``), for a connector kg serves: kg's copy of the
# rule is then the graph's (a row written without its rule takes the connector off the readers), so no graph row is read.
CHAIN_GRANTED = """/* pipeshub:kg.chain_granted_kg */ SELECT r.sk, r.id, r.kind, r.parent_sk, r.group_sk FROM kg.records r
    WHERE r.org_id = $1 AND r.coll = ANY('{2,3}'::smallint[]) AND r.id = ANY($2::text[]) AND r.app_sk = (SELECT a.sk FROM kg.node_keys a WHERE a.org_id = $1 AND a.coll = 1 AND a.id = $3)
      AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien AND coalesce(r.rule, 'OPEN') = 'OPEN'"""

# The root's chain-top inputs (``KhBrowseMixin._kh_root_tops``), what ``kh_browse._chain_nodes`` hands the walk: of the grants' nodes ``$2`` (sks) of the connector ``$3``, the rows a
# browse may list as chain-tops (as :data:`CHAIN_GRANTED`), by the nodes the walk asks the access check about for them (``judges``: the hierarchy parents a browse opens,
# the App included, and the own groups: a record's group and group links, a group's parent groups), one row per set of judges with the rows' keys and ids. Each node by its
# key (a seek, under reader.seeks, whatever the statistics: as the drain's marks are read) and its links in one range of links_child; the judges' keys once each.
ROOT_CHAIN = """/* pipeshub:kg.root_chain */ WITH c AS MATERIALIZED (SELECT r.sk, r.id, r.kind, r.parent_sk, r.group_sk, l.hier, l.grp
      FROM unnest($2::bigint[]) AS q(sk)
      CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records x WHERE x.org_id = $1 AND x.sk >= q.sk ORDER BY x.sk LIMIT 1) s WHERE s.sk = q.sk OFFSET 0) r
      CROSS JOIN LATERAL (SELECT coalesce(array_agg(k.parent_sk) FILTER (WHERE k.kind = 1), '{}') AS hier, coalesce(array_agg(k.parent_sk) FILTER (WHERE k.kind = 2), '{}') AS grp
          FROM (SELECT k.parent_sk, k.kind FROM kg.links k WHERE k.org_id = $1 AND k.child_sk >= r.sk AND k.child_sk <= r.sk AND k.kind IN (1, 2)
              ORDER BY k.child_sk, k.kind) k) l
      WHERE r.app_sk = $3 AND r.coll = ANY('{2,3}'::smallint[]) AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien AND coalesce(r.rule, 'OPEN') = 'OPEN'),
    e AS MATERIALIZED (SELECT c.sk, x.ref, x.parent FROM c CROSS JOIN LATERAL (SELECT h, true FROM unnest(c.parent_sk || c.hier) AS h
        UNION ALL SELECT o, false FROM unnest(CASE WHEN c.kind = 2 THEN c.parent_sk || c.hier ELSE c.group_sk || c.grp END) AS o) AS x(ref, parent) WHERE x.ref IS NOT NULL),
    k AS MATERIALIZED (SELECT k.sk, k.coll, coalesce(g.alien, false) AS alien FROM (SELECT DISTINCT ref FROM e) q
      CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.node_keys n WHERE n.org_id = $1 AND n.sk >= q.ref ORDER BY n.sk LIMIT 1) s WHERE s.sk = q.ref OFFSET 0) k
      LEFT JOIN LATERAL (SELECT s.alien FROM (SELECT * FROM kg.records x WHERE x.org_id = $1 AND x.sk >= k.sk ORDER BY x.sk LIMIT 1) s WHERE s.sk = k.sk AND k.coll = 2 OFFSET 0) g ON true),
    j AS (SELECT e.sk, array_agg(DISTINCT e.ref ORDER BY e.ref) AS judges FROM e JOIN k ON k.sk = e.ref
      WHERE CASE WHEN e.parent THEN NOT k.alien ELSE k.coll = 2 AND e.ref <> e.sk END GROUP BY e.sk)
  SELECT coalesce(j.judges, '{}') AS judges, array_agg(c.sk) AS sks, array_agg(c.id) AS ids FROM c LEFT JOIN j ON j.sk = c.sk GROUP BY 1"""

def _wrong(rows: list) -> list[tuple[int, int, str, bool, int | None]]:
    return [(r["principal_sk"], r["node_sk"], r["gtype"], r["want_held"], r["want_group"]) for r in rows
            if (r["parent_held"], r["node_group"]) != (r["want_held"], r["want_group"])]


def _wrong_parent(rows: list) -> list[tuple[int, int, str, int | None]]:
    return [(r["principal_sk"], r["node_sk"], r["gtype"], r["want_parent"]) for r in rows if r["node_parent"] != r["want_parent"]]


async def _write(tx: Tx, rows: list) -> int:
    w = KgWriter(tx)
    return (await w.grant_marks_set(wrong) if (wrong := _wrong(rows)) else 0) + (await w.grant_parent_set(par) if (par := _wrong_parent(rows)) else 0)


def unsafe(r: dict) -> bool:
    """A mark that leaves out a grant its rows no longer let it leave out, or one that stands in for rows it no longer gives (``node_parent``)."""
    return ((r["parent_held"] and not r["want_held"]) or (r["node_group"] is not None and r["node_group"] != r["want_group"])
            or (r["node_parent"] is not None and r["node_parent"] != r["want_parent"]))


async def changed(tx: Tx, org: str, sks: list[int], rows: dict[int, dict]) -> set[int]:
    """Of the nodes ``sks``, whose rows a projection is about to write as ``rows``, those whose marks' inputs change (or that are new): their own grants' and their children's."""
    held = {r["sk"]: r for r in await tx.fetch(HELD, org, sks)} if sks else {}
    return {sk for sk in sks if sk not in held or any(held[sk][c] != rows[sk].get(c) for c in MARKED)}


async def refresh(tx: Tx, org: str, nodes: set[int], parents: set[int]) -> int:
    """Write the marks of the grants on ``nodes`` and on the children of ``parents`` where they differ from what kg's rows give now. In the projection's unit, after its writes."""
    every = set(nodes)
    ps = sorted(parents)
    for i in range(0, len(ps), CHUNK):
        async with seeks(tx):
            every |= {r["sk"] for r in await tx.fetch(CHILDREN, org, ps[i:i + CHUNK])}
    sks = sorted(every)
    rows = []
    for i in range(0, len(sks), CHUNK):
        async with seeks(tx):
            rows += await tx.fetch(ON_NODES, org, sks[i:i + CHUNK])
    return await _write(tx, rows)


async def fill_page(tx: Tx, org: str, app_sk: int, after: tuple[int, int, str], page: int) -> tuple[int, int, str] | None:
    """One page of the connector's grants given their marks; where the next goes on, or None after the last."""
    rows = await tx.fetch(BY_APP, org, app_sk, *after, page)
    await _write(tx, rows)
    return (rows[-1]["node_sk"], rows[-1]["principal_sk"], rows[-1]["gtype"]) if len(rows) == page else None


async def candidates(tx: Tx, org: str, app_sk: int, user_key: str) -> dict[int, tuple[int | None, int | None]]:
    """The viewer's grants of the connector that may be chain-tops: node sk -> (its record group, its one hierarchy parent), each None where the mark is not set. Only for a
    connector kg serves."""
    r = await tx.fetchrow(CANDIDATES, org, user_key, app_sk)
    return dict(zip(r["sks"], zip(r["grps"], r["pars"], strict=True), strict=True))


# The root's judges (``ROOT_CHAIN``'s sks) as ``kg.read.BY_ID`` reads asked ids, by their sks: one seek of records_pkey each instead of one per collection of an id, and the
# columns the root reads of a judge besides (its kind, its alien mark).
JUDGES = """/* pipeshub:kg.root_judges */ SELECT r.sk, r.coll, r.id, r.kind, r.alien, r.deleted, r.placeholder, r.class_id, r.r13, r.path IS NULL AS no_path,
      r.fence_job IS NOT NULL AS hidden, r.app_sk
    FROM unnest($2::bigint[]) AS q(sk)
    CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.sk >= q.sk ORDER BY k.sk LIMIT 1) s WHERE s.sk = q.sk OFFSET 0) r"""
# The connectors of the judges, once each (a handful): their ids, by node_keys' key as kg.read.BY_ID reads a row's connector.
# Of the judges' ids, those kg holds in another of the collections kg.read.BY_ID asks (apps, groups, records): kg.read.decide leaves such an id to the walk,
# whose answer is by id, and so does decide_sks. A seek of records_id per id and other collection, as BY_ID's; the judge's own collection is not sought again.
JUDGE_SHARED_IDS = """/* pipeshub:kg.root_judge_shared_ids */ SELECT DISTINCT q.id FROM unnest($2::text[], $3::smallint[]) AS q(id, coll)
    CROSS JOIN LATERAL (SELECT 1 FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.coll = 1 AND k.id >= q.id ORDER BY k.id LIMIT 1) s WHERE q.coll <> 1 AND s.id = q.id
        UNION ALL SELECT 1 FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.coll = 2 AND k.id >= q.id ORDER BY k.id LIMIT 1) s WHERE q.coll <> 2 AND s.id = q.id
        UNION ALL SELECT 1 FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.coll = 3 AND k.id >= q.id ORDER BY k.id LIMIT 1) s WHERE q.coll <> 3 AND s.id = q.id) o"""
JUDGE_APPS = """/* pipeshub:kg.root_judge_apps */ SELECT n.sk, n.id FROM unnest($2::bigint[]) AS q(sk)
    CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.node_keys k WHERE k.org_id = $1 AND k.sk >= q.sk ORDER BY k.sk LIMIT 1) s WHERE s.sk = q.sk AND s.coll = 1 OFFSET 0) n"""


@dataclass
class Judged:
    rows: dict[int, Any] = field(default_factory=dict)          # sk -> its kg row (the judges kg holds)
    admit: set[int] = field(default_factory=set)                # admitted by kg's classes (before the documents are read: ``access.admitted_rows``)
    deny: set[int] = field(default_factory=set)                 # denied by kg
    walk: set[int] = field(default_factory=set)                 # what ``kg.read.decide`` leaves to the walk: the access check decides them, by id


async def decide_sks(tx: Tx, org: str, sks: list[int], access: dict[str, Any]) -> Judged:
    """``kg.read.decide`` for nodes known by their sks (the root's judges), by the same rules in the same order; a sk kg holds no row of is left out of ``rows`` (the caller asks the
    check about it by its id). Under the caller's kg.read.PROFILE."""
    from app.services.graph_db.postgres.kg import (
        read as kg_read,  # imports drainer, which imports project, which imports this module
    )
    j = Judged()
    gated = set(access["gated_app_ids"])
    granted = access_sql.grants_of(access)
    for i in range(0, len(sks), kg_read.CHUNK):
        for r in await tx.fetch(JUDGES, org, sks[i:i + kg_read.CHUNK]):
            j.rows[r["sk"]] = r
    rows = list(j.rows.values())
    shared = {r["id"] for r in await tx.fetch(JUDGE_SHARED_IDS, org, [r["id"] for r in rows], [r["coll"] for r in rows])} if rows else set()
    apps = sorted({r["app_sk"] for r in j.rows.values() if r["app_sk"] is not None})
    app_id = {r["sk"]: r["id"] for r in await tx.fetch(JUDGE_APPS, org, apps)} if apps else {}
    served: dict[str, bool] = {}
    need: dict[int, int] = {}
    for sk, r in j.rows.items():
        if r["id"] in shared:      # one id in two collections: the walk's answer is by id (kg.read.decide's "ambiguous")
            j.walk.add(sk)
            continue
        app = app_id.get(r["app_sk"])
        if app is not None and app not in served:
            served[app] = await kg_read.serves(tx, org, app)
        if app is None or not served[app]:
            j.walk.add(sk)
        elif r["deleted"]:
            j.deny.add(sk)
        elif r["kind"] == 1:
            (j.admit if app in gated else j.deny).add(sk)
        elif r["placeholder"] or r["hidden"] or r["r13"] is not False or r["no_path"]:
            j.walk.add(sk)
        elif app not in gated:
            j.deny.add(sk)
        elif r["class_id"] is None:
            j.walk.add(sk)
        else:
            need[sk] = r["class_id"]
    if need:
        classes = {c["id"]: (json.loads(c["clauses"]) if isinstance(c["clauses"], str) else list(c["clauses"]))
                   for c in await tx.fetch(kg_read.CLASSES, org, sorted(set(need.values())))}
        by_class = await kg_read.class_verdicts(tx, org, classes, granted)
        await granted.see(tx, org, [j.rows[sk]["id"] for sk in need])
        for sk, c in need.items():
            if by_class.get(c) is None:
                j.walk.add(sk)
                continue
            plain, own = by_class[c]  # type: ignore[misc]
            (j.admit if plain or (own and j.rows[sk]["id"] in granted) else j.deny).add(sk)
    return j
