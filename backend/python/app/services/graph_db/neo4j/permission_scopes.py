"""`HierarchyView` over Neo4j: the organisation's record groups and scopes, for `app.services.permissions`.

Not `kh_scope`: its scope tree is stamped only for connectors the listing can precompute (no record-group-level
group, no multi-parent node, flag on) and never for a KB, while the resolver needs every connector. The
vocabulary is the same: a *restricted* scope is `kh_scope`'s *gate* (STRICT/OPEN without inheriting, or
RESTRICTED), plus unknown rules, which fail closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.org_context import instrument_org_methods
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.pending import PendingUnreadable, parse
from app.services.permissions.ports import AppFacts, ScopeNode

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient

_HIER = "['PARENT_CHILD', 'ATTACHMENT']"

GROUPS = "MATCH (g:RecordGroup) WHERE g.connectorId = $app AND g.orgId = $org RETURN g.id AS id"

# A scope has an access list of its own: a grant, a rule other than OPEN/STRICT, or a hierarchy parent it does
# not inherit from. A deleted node matters only as a gate above something, so it is listed (restricted) when it
# has children.
SCOPES = f"""
MATCH (n:Record|RecordGroup)
WHERE n.connectorId = $app AND n.orgId = $org
WITH n, coalesce(n.isDeleted, false) AS deleted, NOT (coalesce(n.accessRule, 'OPEN') IN ['OPEN', 'STRICT']) AS bad
WITH n, deleted, bad, EXISTS {{
    MATCH (p)-[h:NODE_RELATION]->(n)
    WHERE h.relationshipType IN {_HIER} AND NOT EXISTS {{ (n)-[:INHERIT_PERMISSIONS]->(p) }}
}} AS breaks
WHERE CASE WHEN deleted THEN EXISTS {{ MATCH (n)-[h:NODE_RELATION]->() WHERE h.relationshipType IN {_HIER} }}
           ELSE bad OR breaks OR EXISTS {{ ()-[:PERMISSION]->(n) }} END
RETURN n.id AS id, n.connectorId AS connector, (deleted OR bad OR breaks) AS restricted,
       (deleted OR (NOT $special AND (bad OR NOT EXISTS {{
           MATCH (n)-[:INHERIT_PERMISSIONS]->(p) MATCH (p)-[h:NODE_RELATION]->(n) WHERE h.relationshipType IN {_HIER}
       }}))) AS needsGrant,
       (deleted OR bad) AS ruleRestricted
"""

# Whether something other than a direct grant can admit a node of the app: the app opens everything, or a record
# group of it declares its own readers (the batch check's declared arm). Unknown app: treated as yes.
SPECIAL = """
OPTIONAL MATCH (a:App {id: $app}) WHERE a.orgId = $org
RETURN a IS NULL OR coalesce(a.permissionModel, '') = 'APP_LEVEL' OR a.type = 'KB'
       OR EXISTS {
           MATCH (g:RecordGroup) WHERE g.connectorId = $app AND g.orgId = $org AND g.permissionModel = 'RECORD_GROUP_LEVEL'
       } AS special
"""

# Whether the app opens everything (APP_LEVEL or a KB) and its connector type.
APP = """
OPTIONAL MATCH (a:App {id: $app}) WHERE a.orgId = $org
RETURN a IS NOT NULL AND (coalesce(a.permissionModel, '') = 'APP_LEVEL' OR a.type = 'KB') AS opens,
       coalesce(a.type, '') AS type,
       a IS NOT NULL AND EXISTS {
           MATCH (g:RecordGroup) WHERE g.connectorId = $app AND g.orgId = $org AND g.permissionModel = 'RECORD_GROUP_LEVEL'
       } AS rgl
"""


# The roots registered on the org's connectors (`permissions.pending`), as stored.
PENDING = "MATCH (a:App) WHERE a.orgId = $org AND a.scopeProjectionPendingRoots IS NOT NULL RETURN a.id AS app, a.scopeProjectionPendingRoots AS raw"


class Neo4jHierarchyView:
    def __init__(self, client: Callable[[], Neo4jClient | None]) -> None:
        """`client` returns the provider's current client (it is replaced when the provider reconnects)."""
        self._get_client = client

    async def _rows(
        self, query: str, org_id: str, app_id: str, **extra: object
    ) -> list[dict[str, object]]:
        try:
            client = self._get_client()
            if client is None:
                raise ConnectionError("graph not connected")
            return await client.execute_query(query, parameters={"app": app_id, "org": org_id, **extra}, plain=True)
        except Exception as exc:
            raise ViewerScopesUnavailable("graph_error") from exc

    async def record_group_ids(self, org_id: str, app_id: str) -> frozenset[str]:
        return frozenset(str(r["id"]) for r in await self._rows(GROUPS, org_id, app_id))

    async def scope_nodes(self, org_id: str, app_id: str) -> tuple[ScopeNode, ...]:
        special = bool((await self._rows(SPECIAL, org_id, app_id))[0]["special"])
        rows = await self._rows(SCOPES, org_id, app_id, special=special)
        return tuple(ScopeNode(str(r["id"]), str(r["connector"]), bool(r["restricted"]), bool(r["needsGrant"]), bool(r["ruleRestricted"])) for r in rows)

    async def app_facts(self, org_id: str, app_id: str) -> AppFacts:
        row = (await self._rows(APP, org_id, app_id))[0]
        return AppFacts(bool(row["opens"]), str(row["type"]), bool(row["rgl"]))

    async def pending_scopes(self, org_id: str) -> dict[str, frozenset[str]]:
        try:
            held = {str(r["app"]): frozenset(parse(r["raw"])) for r in await self._rows(PENDING, org_id, "")}
            return {app: roots for app, roots in held.items() if roots}
        except PendingUnreadable as exc:  # roots that cannot be read cannot be hidden: the viewer falls back to the legacy filter
            raise ViewerScopesUnavailable("pending_unreadable") from exc


instrument_org_methods(Neo4jHierarchyView)  # shadow/enforce check of every org_id argument, as for the providers
