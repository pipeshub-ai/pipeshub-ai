"""The placement marks of a grant (kg.scope_members, kg_0025): what lets a browse find a viewer's chain-tops from the few grants that can be one, instead of from every grant.

A chain-top (``kh_chain_tops``) is a node the viewer holds a direct OPEN grant on that has no hierarchy parent they may open; the browse lists it under its own record groups the
viewer may open, else under the App. Two marks of each grant row, functions of kg's own rows only (no graph row, no stamp):

- ``parent_held``: the same principal holds, with the same type, a grant on a hierarchy parent of the node (its primary parent or a hierarchy link) that is a live, OPEN,
  non-placeholder record or group of the node's connector and of the organisation. Whoever holds the grant then holds that parent's, which the check admits by itself (the walk's
  seed arm, ``access._is_seed``; kg's class of such a node has the ``@self`` clause), so the node is no chain-top of theirs.
- ``node_group``: the node's one record group, for a record of exactly one group (``group_sk`` and no group link); NULL otherwise. A record whose group the viewer may open is
  listed under that group, never under the App.

Both default to what lists every grant as before (false, NULL), and a mark is only ever a reason to leave a grant out: a mark that is late can cost reads, never a chain-top.
The projection keeps them exact in the unit that changes what they read (:func:`refresh`), the bootstrap fills an older copy in place, and the verify refuses a copy that holds a
mark its rows no longer give (:data:`BY_APP`, ``unsafe``). The writer only updates grant rows that exist: a grant another unit deleted meanwhile is never made again.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

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
      CASE WHEN r.kind = 3 AND NOT EXISTS (SELECT 1 FROM (SELECT l.child_sk, l.kind FROM kg.links l WHERE l.org_id = $1 AND (l.child_sk, l.kind) >= (r.sk, 2) ORDER BY l.child_sk, l.kind LIMIT 1) l
          WHERE l.child_sk = r.sk AND l.kind = 2) THEN r.group_sk END AS grp
    FROM (SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.sk >= m.node_sk ORDER BY k.sk LIMIT 1) s WHERE s.sk = m.node_sk OFFSET 0) r
    WHERE r.kind IN (2, 3) AND r.app_sk = m.app_sk) x ON true"""
_COLS = "m.principal_sk, m.node_sk, m.gtype, m.parent_held, m.node_group, coalesce(x.held, false) AS want_held, x.grp AS want_group"
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
# The viewer's grants of the connector $3 that may be chain-tops (no parent the same principal holds covers them), with each node's group: the grants of every principal
# the viewer holds in it (kg.principal_closure: the user, their groups, roles, teams and organisation, and the source accounts linked in the connector with theirs).
CANDIDATES = """/* pipeshub:kg.chain_top_grants */ SELECT m.node_sk, m.node_group FROM kg.node_keys u
    JOIN kg.principal_closure c ON c.org_id = u.org_id AND c.user_sk = u.sk AND c.app_sk IN (0, $3)
    CROSS JOIN LATERAL (SELECT m.node_sk, m.node_group FROM kg.scope_members m WHERE m.org_id = $1 AND m.principal_sk = c.principal_sk AND m.app_sk = $3
        AND NOT m.parent_held AND m.gtype <> '@uar' OFFSET 0) m
    WHERE u.org_id = $1 AND u.coll = 4 AND u.id = $2"""
# The granted rows of the connector ``$3`` among ids ``$2`` that a browse may list as chain-tops (``kg.listing.CHAIN_GRANTED``), for a connector kg serves: kg's copy of the
# rule is then the graph's (a row written without its rule takes the connector off the readers), so no graph row is read.
CHAIN_GRANTED = """/* pipeshub:kg.chain_granted_kg */ SELECT r.sk, r.id, r.kind, r.parent_sk, r.group_sk FROM kg.records r
    WHERE r.org_id = $1 AND r.coll = ANY('{2,3}'::smallint[]) AND r.id = ANY($2::text[]) AND r.app_sk = (SELECT a.sk FROM kg.node_keys a WHERE a.org_id = $1 AND a.coll = 1 AND a.id = $3)
      AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien AND coalesce(r.rule, 'OPEN') = 'OPEN'"""


def _wrong(rows: list) -> list[tuple[int, int, str, bool, int | None]]:
    return [(r["principal_sk"], r["node_sk"], r["gtype"], r["want_held"], r["want_group"]) for r in rows
            if (r["parent_held"], r["node_group"]) != (r["want_held"], r["want_group"])]


def unsafe(r: dict) -> bool:
    """A mark that leaves out a grant its rows no longer let it leave out."""
    return (r["parent_held"] and not r["want_held"]) or (r["node_group"] is not None and r["node_group"] != r["want_group"])


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
    wrong = []
    for i in range(0, len(sks), CHUNK):
        async with seeks(tx):
            rows = await tx.fetch(ON_NODES, org, sks[i:i + CHUNK])
        wrong += _wrong(rows)
    return await KgWriter(tx).grant_marks_set(wrong) if wrong else 0


async def fill_page(tx: Tx, org: str, app_sk: int, after: tuple[int, int, str], page: int) -> tuple[int, int, str] | None:
    """One page of the connector's grants given their marks; where the next goes on, or None after the last."""
    rows = await tx.fetch(BY_APP, org, app_sk, *after, page)
    if wrong := _wrong(rows):
        await KgWriter(tx).grant_marks_set(wrong)
    return (rows[-1]["node_sk"], rows[-1]["principal_sk"], rows[-1]["gtype"]) if len(rows) == page else None


async def candidates(tx: Tx, org: str, app_sk: int, user_key: str) -> dict[int, int | None]:
    """The viewer's grants of the connector that may be chain-tops: node sk -> its record group (None: none, several or a group). Only for a connector kg serves."""
    return {r["node_sk"]: r["node_group"] for r in await tx.fetch(CANDIDATES, org, user_key, app_sk)}
