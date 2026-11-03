"""`StampGraph` over ArangoDB. Same definitions as the Neo4j view (see `neo4j/permission_stamps.py`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import instrument_org_methods
from app.services.graph_db.neo4j.permission_stamps import to_node
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.ports import StampApp, StampNode

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient

_REL = CollectionNames.NODE_RELATIONS.value
_INH = CollectionNames.INHERIT_PERMISSIONS.value
_PERM = CollectionNames.PERMISSION.value
_BEL = CollectionNames.BELONGS_TO.value
_REC = CollectionNames.RECORDS.value
_GRP = CollectionNames.RECORD_GROUPS.value
_APP = CollectionNames.APPS.value
_HIER = '["PARENT_CHILD", "ATTACHMENT"]'

APP = f"""
LET a = DOCUMENT("{_APP}", @app)
LET mine = a != null AND a.orgId == @org
RETURN {{opens: mine AND (a.permissionModel == "APP_LEVEL" OR a.type == "KB"), type: mine ? (a.type || "") : "",
        groupLevel: LENGTH(FOR g IN {_GRP} FILTER g.connectorId == @app AND g.orgId == @org AND g.permissionModel == "RECORD_GROUP_LEVEL"
                           LIMIT 1 RETURN 1) > 0}}
"""

NODES = f"""
FOR id IN @ids
  LET d = FIRST(FOR c IN [DOCUMENT("{_REC}", id), DOCUMENT("{_GRP}", id), DOCUMENT("{_APP}", id)] FILTER c != null RETURN c)
  FILTER d != null AND d.orgId == @org
  LET coll = PARSE_IDENTIFIER(d._id).collection
  RETURN {{id: id, kind: coll == "{_REC}" ? "record" : (coll == "{_GRP}" ? "group" : "app"), connector: d.connectorId || id,
          rule: d.accessRule, deleted: d.isDeleted == true, placeholder: d.isPlaceholder == true, model: d.permissionModel,
          vrid: d.virtualRecordId,
          parents: (FOR h IN {_REL} FILTER h._to == d._id AND h.relationshipType IN {_HIER} RETURN PARSE_IDENTIFIER(h._from).key),
          inherits: (FOR i IN {_INH} FILTER i._from == d._id RETURN PARSE_IDENTIFIER(i._to).key),
          granted: LENGTH(FOR p IN {_PERM} FILTER p._to == d._id LIMIT 1 RETURN 1) > 0,
          groups: (FOR b IN {_BEL} FILTER b._from == d._id AND STARTS_WITH(b._to, "{_GRP}/") RETURN PARSE_IDENTIFIER(b._to).key),
          belongsApp: LENGTH(FOR b IN {_BEL} FILTER b._from == d._id AND STARTS_WITH(b._to, "{_APP}/") LIMIT 1 RETURN 1) > 0}}
"""

PAGE = f"""
FOR r IN {_REC} FILTER r.connectorId == @app AND r.orgId == @org AND (@after == null OR r._key > @after)
  SORT r._key LIMIT @limit RETURN r._key
"""


class ArangoStampGraph:
    def __init__(self, http_client: Callable[[], ArangoHTTPClient | None]) -> None:
        self._get_client = http_client

    async def _rows(self, query: str, bind: dict[str, object]) -> list[object]:
        try:
            client = self._get_client()
            if client is None:
                raise ConnectionError("graph not connected")
            rows: list[object] = list(await client.execute_aql(query, bind_vars=bind))  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            return rows
        except Exception as exc:
            raise ViewerScopesUnavailable("graph_error") from exc

    async def app(self, org_id: str, app_id: str) -> StampApp:
        row = cast("dict[str, object]", (await self._rows(APP, {"app": app_id, "org": org_id}))[0])
        return StampApp(str(row["type"]), bool(row["opens"]), bool(row["groupLevel"]))

    async def nodes(self, org_id: str, ids: frozenset[str]) -> dict[str, StampNode]:
        rows = await self._rows(NODES, {"ids": sorted(ids), "org": org_id}) if ids else []
        return {n.id: n for n in (to_node(cast("dict[str, object]", r)) for r in rows)}

    async def record_page(self, org_id: str, app_id: str, after: str | None, limit: int) -> list[str]:
        return [str(r) for r in await self._rows(PAGE, {"app": app_id, "org": org_id, "after": after, "limit": limit})]


instrument_org_methods(ArangoStampGraph)
