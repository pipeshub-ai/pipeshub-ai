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
mark its rows no longer give (:data:`BY_APP`, ``unsafe``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.writers import KgWriter

if TYPE_CHECKING:
    from app.core.db import Tx

CHUNK = 5000
# The marks a grant row ``m`` should carry, from the rows of its node and of the node's parents.
_WANT = """LEFT JOIN LATERAL (SELECT
      EXISTS (SELECT 1 FROM (SELECT r.parent_sk AS sk UNION ALL SELECT l.parent_sk FROM kg.links l WHERE l.org_id = $1 AND l.child_sk = r.sk AND l.kind = 1) p
          JOIN kg.records pr ON pr.org_id = $1 AND pr.sk = p.sk
          JOIN kg.scope_members q ON q.org_id = $1 AND q.principal_sk = m.principal_sk AND q.node_sk = pr.sk AND q.gtype = m.gtype
        WHERE pr.kind IN (2, 3) AND pr.app_sk = r.app_sk AND NOT pr.deleted AND NOT pr.placeholder AND NOT pr.alien AND coalesce(pr.rule, 'OPEN') = 'OPEN') AS held,
      CASE WHEN r.kind = 3 AND NOT EXISTS (SELECT 1 FROM kg.links l WHERE l.org_id = $1 AND l.child_sk = r.sk AND l.kind = 2) THEN r.group_sk END AS grp
    FROM kg.records r WHERE r.org_id = $1 AND r.sk = m.node_sk AND r.kind IN (2, 3) AND r.app_sk = m.app_sk) x ON true"""
_COLS = "m.principal_sk, m.node_sk, m.gtype, m.parent_held, m.node_group, coalesce(x.held, false) AS want_held, x.grp AS want_group"
# The grants on the nodes $2.
ON_NODES = f"/* pipeshub:kg.grant_marks_on */ SELECT {_COLS} FROM kg.scope_members m {_WANT} WHERE m.org_id = $1 AND m.node_sk = ANY($2::bigint[])"
# The connector $2's grants after the key ($3, $4, $5), $6 of them in key order: a page of the fill and of the verify.
BY_APP = f"""/* pipeshub:kg.grant_marks_by_app */ SELECT {_COLS} FROM (SELECT * FROM kg.scope_members m WHERE m.org_id = $1 AND m.app_sk = $2
      AND (m.app_sk, m.node_sk, m.principal_sk, m.gtype) > ($2, $3, $4, $5) ORDER BY m.app_sk, m.node_sk, m.principal_sk, m.gtype LIMIT $6) m {_WANT}
    ORDER BY m.node_sk, m.principal_sk, m.gtype"""
# The hierarchy children of the nodes $2 that hold a grant: their parent_held reads the parents' rows and grants.
CHILDREN = """/* pipeshub:kg.grant_marks_children */ SELECT c.sk FROM (SELECT r.sk FROM kg.records r WHERE r.org_id = $1 AND r.parent_sk = ANY($2::bigint[])
      UNION SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = ANY($2::bigint[]) AND l.kind = 1) c
    WHERE EXISTS (SELECT 1 FROM kg.scope_members m WHERE m.org_id = $1 AND m.node_sk = c.sk)"""
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
        every |= {r["sk"] for r in await tx.fetch(CHILDREN, org, ps[i:i + CHUNK])}
    sks = sorted(every)
    wrong = []
    for i in range(0, len(sks), CHUNK):
        wrong += _wrong(await tx.fetch(ON_NODES, org, sks[i:i + CHUNK]))
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
