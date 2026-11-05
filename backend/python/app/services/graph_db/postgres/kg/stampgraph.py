"""The nodes a stamper reads, from graph.nodes and graph.edges: ``StampNode`` (viewer-independent facts of a record, record group or app) for the class compiler.

The same facts as the in-memory graph of the permission harness (``tests/permissions/stamp_world.py``) and the graph adapters, read through one query per fact and level:
hierarchy parents (``nodeRelations`` of a hierarchy type whose source exists), inherited parents (``inheritPermissions`` from the node), the record groups it belongs to and
whether it belongs to its app (``belongsTo``), and whether a grant reaches it (``permission`` from a principal that exists). :func:`closure` loads the parents and group chains
level by level (at most 51 levels, the oracle's hop cap) so that a node the compiler needs but the closure lacks is *absent*, and the compiler refuses it (fail closed).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.model import HIERARCHY, flag
from app.services.permissions.ports import StampApp, StampNode

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.core.db import Tx

MAX_LEVELS = 51
KIND = {"apps": "app", "recordGroups": "group", "records": "record"}
PRINCIPAL_COLLS = ("users", "groups", "roles", "teams", "organizations")
_NODE_COLLS = list(KIND)


def _json(raw: str | None) -> object:
    return None if raw is None else json.loads(raw)


def app_facts(doc: dict | None) -> StampApp:
    """An app opens everything for its members when its permission model is app-level or it is a knowledge base."""
    d = doc or {}
    return StampApp(str(d.get("type") or ""), d.get("permissionModel") == "APP_LEVEL" or d.get("type") == "KB", False)


async def app(tx: Tx, org: str, app_id: str) -> StampApp:
    """The app's facts: ``opens``, its connector type, and whether any of its record groups declares its own readers."""
    row = await tx.fetchrow("""/* pipeshub:kg.stamp_app */ SELECT n.doc->>'type' AS type, n.doc->>'permissionModel' AS model FROM graph.nodes n
        WHERE n.org_id = $1 AND n.coll = 'apps' AND n.id = $2""", org, app_id)
    rgl = await tx.fetchval("""/* pipeshub:kg.stamp_rgl */ SELECT EXISTS (SELECT 1 FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'recordGroups'
        AND n.doc->>'connectorId' = $2 AND n.doc->>'permissionModel' = 'RECORD_GROUP_LEVEL')""", org, app_id)
    base = app_facts({"type": row["type"], "permissionModel": row["model"]} if row else None)
    return StampApp(base.connector_type, base.opens, bool(rgl))


async def nodes(tx: Tx, org: str, ids: Iterable[str]) -> dict[str, StampNode]:
    """The records, record groups and apps among ``ids`` (ids that do not exist are absent)."""
    want = sorted(set(ids))
    if not want:
        return {}
    docs = await tx.fetch("""/* pipeshub:kg.stamp_nodes */ SELECT n.coll, n.id, n.doc->>'connectorId' AS connector, n.doc->>'accessRule' AS rule,
            n.doc->'isDeleted' AS deleted, n.doc->'isPlaceholder' AS placeholder,
            n.doc->>'permissionModel' AS model, n.doc->>'virtualRecordId' AS vrid
        FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($2::text[]) AND n.id = ANY($3::text[])""", org, _NODE_COLLS, want)
    found = {r["id"]: r for r in docs}
    ids_found = sorted(found)
    parents: dict[str, list[str]] = {}
    for r in await tx.fetch("""/* pipeshub:kg.stamp_parents */ SELECT e.to_id AS child, e.from_id AS parent FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.to_id = ANY($2::text[]) AND e.doc->>'relationshipType' = ANY($3::text[])
              AND EXISTS (SELECT 1 FROM graph.nodes p WHERE p.org_id = e.org_id AND p.id = e.from_id AND p.coll = ANY($4::text[]))""", org, ids_found, list(HIERARCHY), _NODE_COLLS):
        parents.setdefault(r["child"], []).append(r["parent"])
    inherits: dict[str, set[str]] = {}
    for r in await tx.fetch("""/* pipeshub:kg.stamp_inherits */ SELECT e.from_id AS child, e.to_id AS parent FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = 'inheritPermissions' AND e.from_id = ANY($2::text[])""", org, ids_found):
        inherits.setdefault(r["child"], set()).add(r["parent"])
    groups: dict[str, list[str]] = {}
    belongs_app: set[str] = set()
    for r in await tx.fetch("""/* pipeshub:kg.stamp_belongs */ SELECT e.from_id AS member, e.to_id AS target, t.coll AS coll FROM graph.edges e
            JOIN graph.nodes t ON t.org_id = e.org_id AND t.id = e.to_id AND t.coll = ANY($3::text[])
            WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_id = ANY($2::text[])""", org, ids_found, ["recordGroups", "apps"]):
        if r["coll"] == "apps":
            if r["target"] == found[r["member"]]["connector"]:
                belongs_app.add(r["member"])                    # membership of the record's own connector only (R12), not of any app
        else:
            groups.setdefault(r["member"], []).append(r["target"])
    granted = {r["node"] for r in await tx.fetch("""/* pipeshub:kg.stamp_granted */ SELECT DISTINCT e.to_id AS node FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = 'permission' AND e.to_id = ANY($2::text[])
              AND EXISTS (SELECT 1 FROM graph.nodes p WHERE p.org_id = e.org_id AND p.id = e.from_id AND p.coll = ANY($3::text[]))""", org, ids_found, list(PRINCIPAL_COLLS))}
    out: dict[str, StampNode] = {}
    for i, r in found.items():
        kind = KIND[r["coll"]]
        connector = i if kind == "app" else (r["connector"] or "")
        if kind == "app":
            out[i] = StampNode(i, "app", i)
            continue
        out[i] = StampNode(i, kind, connector, tuple(sorted(parents.get(i, ()))), frozenset(inherits.get(i, ())), r["rule"], flag(_json(r["deleted"])), flag(_json(r["placeholder"])), i in granted,
                           kind == "group" and r["model"] == "RECORD_GROUP_LEVEL", tuple(sorted(groups.get(i, ()))), r["vrid"], i in belongs_app)
    return out


async def closure(tx: Tx, org: str, seeds: Iterable[str], *, known: dict[str, StampNode] | None = None) -> dict[str, StampNode]:
    """The seeds, every hierarchy ancestor and the record-group chains, level by level (one read of each fact per level). ``known``: nodes the caller has just read."""
    known = dict(known or {})
    missing: set[str] = set()
    frontier = set(seeds) | {i for n in known.values() for i in (*n.parents, *n.groups)}
    for _ in range(MAX_LEVELS):
        want = frontier - known.keys() - missing
        if not want:
            break
        found = await nodes(tx, org, want)
        known.update(found)
        missing |= want - found.keys()
        frontier = {i for n in found.values() for i in (*n.parents, *n.groups)}
    return known
