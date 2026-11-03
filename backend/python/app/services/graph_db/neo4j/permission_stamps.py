"""`StampGraph` over Neo4j: what a stamper (`app.services.permissions.projector`) reads about records and record groups.

Same definitions as `permission_scopes.py` (hierarchy = PARENT_CHILD and ATTACHMENT node relations; inherits =
INHERIT_PERMISSIONS; granted = a PERMISSION edge into the node). A failed read raises `ViewerScopesUnavailable`, never an
empty answer: an empty answer would stamp a record Container.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.org_context import instrument_org_methods
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.ports import StampApp, StampNode

if TYPE_CHECKING:
    from collections.abc import Callable

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
       EXISTS {{ (n)-[:BELONGS_TO]->(:App) }} AS belongsApp
"""

PAGE = """
MATCH (r:Record) WHERE r.connectorId = $app AND r.orgId = $org AND ($after IS NULL OR r.id > $after)
RETURN r.id AS id ORDER BY r.id LIMIT $limit
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
        return [str(r["id"]) for r in await self._rows(PAGE, app=app_id, org=org_id, after=after, limit=limit)]


instrument_org_methods(Neo4jStampGraph)
