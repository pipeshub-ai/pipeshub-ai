"""`HierarchyView` over ArangoDB. Same definitions as the Neo4j view (see `neo4j/permission_scopes.py`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import instrument_org_methods
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.pending import PendingUnreadable, parse
from app.services.permissions.ports import AppFacts, ScopeNode

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient

_REL = CollectionNames.NODE_RELATIONS.value
_INH = CollectionNames.INHERIT_PERMISSIONS.value
_PERM = CollectionNames.PERMISSION.value
_HIER = '["PARENT_CHILD", "ATTACHMENT"]'

GROUPS = f"FOR g IN {CollectionNames.RECORD_GROUPS.value} FILTER g.connectorId == @app AND g.orgId == @org RETURN g._key"

_SCOPES = f"""
FOR n IN {{collection}}
  FILTER n.connectorId == @app AND n.orgId == @org
  LET deleted = n.isDeleted == true
  LET bad = n.accessRule != null AND n.accessRule NOT IN ["OPEN", "STRICT"]
  LET breaks = LENGTH(
    FOR h IN {_REL} FILTER h._to == n._id AND h.relationshipType IN {_HIER}
      FILTER LENGTH(FOR i IN {_INH} FILTER i._from == n._id AND i._to == h._from LIMIT 1 RETURN 1) == 0
      LIMIT 1 RETURN 1) > 0
  LET keep = deleted
    ? LENGTH(FOR h IN {_REL} FILTER h._from == n._id AND h.relationshipType IN {_HIER} LIMIT 1 RETURN 1) > 0
    : (bad OR breaks OR LENGTH(FOR p IN {_PERM} FILTER p._to == n._id LIMIT 1 RETURN 1) > 0)
  FILTER keep
  RETURN {{id: n._key, connector: n.connectorId, restricted: deleted OR bad OR breaks,
          needsGrant: deleted OR (NOT @special AND (bad OR LENGTH(
            FOR h IN {_REL} FILTER h._to == n._id AND h.relationshipType IN {_HIER}
              FILTER LENGTH(FOR i IN {_INH} FILTER i._from == n._id AND i._to == h._from LIMIT 1 RETURN 1) > 0
              LIMIT 1 RETURN 1) == 0)),
          ruleRestricted: deleted OR bad}}
"""

# Whether something other than a direct grant can admit a node of the app: the app opens everything, or a record
# group of it declares its own readers (the batch check's declared arm). Unknown app: treated as yes.
SPECIAL = f"""
LET found = DOCUMENT("{CollectionNames.APPS.value}", @app)
LET a = found != null AND found.orgId == @org ? found : null
RETURN a == null OR a.permissionModel == "APP_LEVEL" OR a.type == "KB" OR LENGTH(
  FOR g IN {CollectionNames.RECORD_GROUPS.value}
    FILTER g.connectorId == @app AND g.orgId == @org AND g.permissionModel == "RECORD_GROUP_LEVEL" LIMIT 1 RETURN 1) > 0
"""
# Whether the app opens everything (APP_LEVEL or a KB) and its connector type.
APP = f"""
LET a = DOCUMENT("{CollectionNames.APPS.value}", @app)
LET mine = a != null AND a.orgId == @org
LET rgl = mine AND LENGTH(
  FOR g IN {CollectionNames.RECORD_GROUPS.value}
    FILTER g.connectorId == @app AND g.orgId == @org AND g.permissionModel == "RECORD_GROUP_LEVEL" LIMIT 1 RETURN 1) > 0
RETURN {{opens: mine AND (a.permissionModel == "APP_LEVEL" OR a.type == "KB"), type: mine ? (a.type || "") : "", rgl: rgl}}
"""
# The roots registered on the org's connectors (`permissions.pending`), as stored.
PENDING = f"FOR a IN {CollectionNames.APPS.value} FILTER a.orgId == @org AND a.scopeProjectionPendingRoots != null RETURN {{app: a._key, raw: a.scopeProjectionPendingRoots}}"
SCOPE_QUERIES = tuple(_SCOPES.replace("{collection}", c.value) for c in (CollectionNames.RECORDS, CollectionNames.RECORD_GROUPS))


class ArangoHierarchyView:
    def __init__(self, http_client: Callable[[], ArangoHTTPClient | None]) -> None:
        """`http_client` returns the provider's current client (it is replaced when the provider reconnects)."""
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

    async def record_group_ids(self, org_id: str, app_id: str) -> frozenset[str]:
        return frozenset(str(r) for r in await self._rows(GROUPS, {"app": app_id, "org": org_id}))

    async def scope_nodes(self, org_id: str, app_id: str) -> tuple[ScopeNode, ...]:
        special = bool((await self._rows(SPECIAL, {"app": app_id, "org": org_id}))[0])
        out: list[ScopeNode] = []
        for query in SCOPE_QUERIES:
            for row in await self._rows(query, {"app": app_id, "org": org_id, "special": special}):
                r = cast("dict[str, object]", row)
                out.append(ScopeNode(str(r["id"]), str(r["connector"]), bool(r["restricted"]), bool(r["needsGrant"]), bool(r["ruleRestricted"])))
        return tuple(out)

    async def app_facts(self, org_id: str, app_id: str) -> AppFacts:
        row = cast("dict[str, object]", (await self._rows(APP, {"app": app_id, "org": org_id}))[0])
        return AppFacts(bool(row["opens"]), str(row["type"]), bool(row["rgl"]))

    async def pending_scopes(self, org_id: str) -> dict[str, frozenset[str]]:
        try:
            held = {str(r["app"]): frozenset(parse(r["raw"])) for r in cast("list[dict[str, object]]", await self._rows(PENDING, {"org": org_id}))}
            return {app: roots for app, roots in held.items() if roots}
        except PendingUnreadable as exc:  # roots that cannot be read cannot be hidden: the viewer falls back to the legacy filter
            raise ViewerScopesUnavailable("pending_unreadable") from exc


instrument_org_methods(ArangoHierarchyView)  # shadow/enforce check of every org_id argument, as for the providers
