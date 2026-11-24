"""`StampGraph` over Neo4j: what a stamper (`app.services.permissions.projector`) reads about records and record groups.

Same definitions as `permission_scopes.py` (hierarchy = PARENT_CHILD and ATTACHMENT node relations; inherits =
INHERIT_PERMISSIONS; granted = a PERMISSION edge into the node). A failed read raises `ViewerScopesUnavailable`, never an
empty answer: an empty answer would stamp a record Container.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.org_context import instrument_org_methods
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.ports import SUBTREE_DEPTH, StampApp, StampNode

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient

_HIER = "['PARENT_CHILD', 'ATTACHMENT']"

APP = """
OPTIONAL MATCH (a:App {id: $app}) WHERE a.orgId = $org
RETURN a IS NOT NULL AND (coalesce(a.permissionModel, '') = 'APP_LEVEL' OR a.type = 'KB') AS opens, coalesce(a.type, '') AS type,
       EXISTS { MATCH (g:RecordGroup) WHERE g.connectorId = $app AND g.orgId = $org AND g.permissionModel = 'RECORD_GROUP_LEVEL' } AS groupLevel
"""

NODES = f"""
MATCH (n:Record|RecordGroup|App) WHERE n.id IN $ids AND n.orgId = $org
RETURN n.id AS id, CASE WHEN n:Record THEN 'record' WHEN n:RecordGroup THEN 'group' ELSE 'app' END AS kind,
       coalesce(n.connectorId, n.id) AS connector, n.accessRule AS rule, coalesce(n.isDeleted, false) AS deleted,
       coalesce(n.isPlaceholder, false) AS placeholder, n.permissionModel AS model, n.virtualRecordId AS vrid,
       [(p)-[h:NODE_RELATION]->(n) WHERE h.relationshipType IN {_HIER} | p.id] AS parents,
       [(n)-[:INHERIT_PERMISSIONS]->(q) | q.id] AS inherits,
       EXISTS {{ ()-[:PERMISSION]->(n) }} AS granted,
       [(n)-[:BELONGS_TO]->(g:RecordGroup) | g.id] AS groups,
       EXISTS {{ (n)-[:BELONGS_TO]->(a:App) WHERE a.id = n.connectorId }} AS belongsApp
"""

# Paged by the (connectorId, id) index in its order (ensure_schema creates it), as the knowledge-hub scope read does: the cursor seeks, and
# nothing sorts the connector's records. The hint fails the read, never the answer, when the index is missing.
PAGE = """
MATCH (r:Record) USING INDEX r:Record(connectorId, id)
WHERE r.connectorId = $app AND r.id > $after AND r.orgId = $org
WITH r ORDER BY r.connectorId, r.id LIMIT $limit
RETURN r.id AS id
"""

# The subtree is walked one level at a time (one bounded query per level over the frontier, a visited set kept here): a single variable-length
# pattern enumerates PATHS, which grows as 2^depth on stacked diamonds or parallel edges. EVERY edge walked is a hierarchy edge (a record reached
# only through a link edge is not below it) and every node is of this organisation.
START = """
MATCH (a:Record|RecordGroup) WHERE a.id IN $ids AND a.orgId = $org
RETURN a.id AS id, a:Record AS rec, a.virtualRecordId AS vrid
"""

LEVEL = f"""
MATCH (p)-[h:NODE_RELATION]->(c) WHERE p.id IN $frontier AND p.orgId = $org AND c.orgId = $org AND h.relationshipType IN {_HIER}
RETURN DISTINCT c.id AS id, c:Record AS rec, c.virtualRecordId AS vrid
"""


def _ids(value: object) -> tuple[str, ...]:
    return tuple(sorted({str(x) for x in value if x})) if isinstance(value, list) else ()


def to_node(r: dict[str, object]) -> StampNode:
    """One row of `NODES` (the same shape the ArangoDB query returns)."""
    return StampNode(
        id=str(r["id"]), kind=str(r["kind"]), connector_id=str(r["connector"] or ""), parents=_ids(r["parents"]),
        inherits=frozenset(_ids(r["inherits"])), rule=None if r["rule"] is None else str(r["rule"]), deleted=bool(r["deleted"]),
        placeholder=bool(r["placeholder"]), granted=bool(r["granted"]),
        group_level=r["kind"] == "group" and r["model"] == "RECORD_GROUP_LEVEL", groups=_ids(r["groups"]),
        vrid=None if r["vrid"] is None else str(r["vrid"]), belongs_app=bool(r.get("belongsApp")))


class Neo4jStampGraph:
    def __init__(self, client: Callable[[], Neo4jClient | None]) -> None:
        self._get_client = client

    async def _rows(self, query: str, **params: object) -> list[dict[str, object]]:
        try:
            client = self._get_client()
            if client is None:
                raise ConnectionError("graph not connected")
            return await client.execute_query(query, parameters=params, plain=True)
        except Exception as exc:
            raise ViewerScopesUnavailable("graph_error") from exc

    async def app(self, org_id: str, app_id: str) -> StampApp:
        row = (await self._rows(APP, app=app_id, org=org_id))[0]
        return StampApp(str(row["type"]), bool(row["opens"]), bool(row["groupLevel"]))

    async def nodes(self, org_id: str, ids: frozenset[str]) -> dict[str, StampNode]:
        rows = await self._rows(NODES, ids=sorted(ids), org=org_id) if ids else []
        return {n.id: n for n in map(to_node, rows)}

    async def record_page(self, org_id: str, app_id: str, after: str | None, limit: int) -> list[str]:
        return [str(r["id"]) for r in await self._rows(PAGE, app=app_id, org=org_id, after=after or "", limit=limit)]

    async def subtree_vrids(self, org_id: str, ids: Sequence[str]) -> list[str]:
        if not ids:
            return []
        seen: set[str] = set()
        vrids: set[str] = set()

        def take(rows: list[dict[str, object]]) -> list[str]:
            fresh = [str(r["id"]) for r in rows if str(r["id"]) not in seen]
            for r in rows:
                if str(r["id"]) not in seen and r["rec"] and r["vrid"]:
                    vrids.add(str(r["vrid"]))
            seen.update(fresh)
            return fresh

        frontier = take(await self._rows(START, ids=sorted(set(ids)), org=org_id))
        for depth in range(1, SUBTREE_DEPTH + 2):
            if not frontier:
                break
            frontier = take(await self._rows(LEVEL, frontier=sorted(frontier), org=org_id))
            if frontier and depth > SUBTREE_DEPTH:
                raise ViewerScopesUnavailable("subtree_too_deep")
        return sorted(vrids)


instrument_org_methods(Neo4jStampGraph)
