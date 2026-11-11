"""External organisations of an org: CRM accounts and the ones extracted from document content, and the records that name them.

An external organisation is an ``organizations`` node with ``isExternal`` true that carries the org it belongs to as ``parentOrgId`` (the org's own node
is never one); every read here matches both, as ``Neo4jProvider``'s ``_ENTITY_ORG_SCOPE``. The rows are the ambient org's (``current_org_id()``): an
``org_id`` argument that differs reads nothing and a write refuses it. Ids are ordered as Neo4j orders strings (``COLLATE "C"``).
"""
from __future__ import annotations

from typing import Any

from app.config.constants.arangodb import CollectionNames, EntityOrigin
from app.core.org_context import current_org_id
from app.services.graph_db.postgres.base import _VALIDATOR, _doc
from app.services.graph_db.postgres.identity import IdentityMixin
from app.utils.time_conversion import get_epoch_timestamp_in_ms

_ORGS = CollectionNames.ORGS.value
_PARENT_EDGES = [CollectionNames.PROSPECT.value, CollectionNames.CUSTOMER.value]                                   # org -> the account it sells to
_ACCOUNT_EDGES = [CollectionNames.DEAL_OF.value, CollectionNames.PROSPECT.value, CollectionNames.CUSTOMER.value]    # what makes a connector know an organisation


def _record_cap(cap: int | None) -> int:
    return max(1, int(cap)) if cap is not None else 2**31 - 1      # uncapped counts every record


class OrganizationsMixin(IdentityMixin):
    async def get_record_organizations(self, record_id: str, org_id: str) -> list[dict[str, Any]]:
        """The external organisations of ``org_id`` the record of ``org_id`` links to by an ``entityRelations`` edge, each once, by id: ``{"id", "name"}``."""
        if not record_id or not org_id or not self._mine(org_id):
            return []
        rows = await self._id_rows("""/* pipeshub:graph.get_record_organizations */ SELECT o.id, o.doc FROM graph.nodes o
            WHERE o.org_id = $1 AND o.coll = 'organizations' AND o.doc->'isExternal' = 'true'::jsonb AND o.doc->>'parentOrgId' = $3
              AND o.id IN (SELECT e.to_id FROM graph.edges e JOIN graph.nodes rec ON rec.org_id = e.org_id AND rec.coll = 'records' AND rec.id = e.from_id
                           WHERE e.org_id = $1 AND e.coll = 'entityRelations' AND e.from_coll = 'records' AND e.from_id = $2 AND e.to_coll = 'organizations' AND rec.doc->>'orgId' = $3)
            ORDER BY o.id COLLATE "C" """, [current_org_id(), record_id, org_id], None)
        return [{"id": r["id"], "name": _doc(r["doc"]).get("name")} for r in rows]

    async def get_record_group_organization(self, record_group_id: str, org_id: str, transaction: str | None = None) -> str | None:
        """The external organisation of ``org_id`` the record group's ``dealOf`` edge points at (the first by id), or None."""
        if not record_group_id or not org_id or not self._mine(org_id):
            return None
        rows = await self._id_rows("""/* pipeshub:graph.get_record_group_organization */ SELECT o.id FROM graph.nodes g
            JOIN graph.edges e ON e.org_id = g.org_id AND e.coll = 'dealOf' AND e.from_coll = 'recordGroups' AND e.from_id = g.id AND e.to_coll = 'organizations'
            JOIN graph.nodes o ON o.org_id = e.org_id AND o.coll = 'organizations' AND o.id = e.to_id
            WHERE g.org_id = $1 AND g.coll = 'recordGroups' AND g.id = $2 AND o.doc->'isExternal' = 'true'::jsonb AND o.doc->>'parentOrgId' = $3
            ORDER BY o.id COLLATE "C" LIMIT 1""", [current_org_id(), record_group_id, org_id], transaction)
        return rows[0]["id"] if rows else None

    async def stamp_external_org_parents(self, org_id: str) -> int:
        """Sets ``parentOrgId`` on the external organisations without one that the org's own node has a ``prospect`` or ``customer`` edge to; how many.
        The rows are locked in id order and checked again once locked, so two stampers never both count one."""
        if not org_id:
            return 0
        self._require_org(org_id)
        _VALIDATOR.validate_node_update(_ORGS, {"parentOrgId": org_id})
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.stamp_external_org_parents */ WITH t AS (SELECT n.id FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'organizations' AND n.doc->'isExternal' = 'true'::jsonb AND coalesce(n.doc->'parentOrgId', 'null'::jsonb) = 'null'::jsonb
                  AND EXISTS (SELECT 1 FROM graph.nodes s WHERE s.org_id = $1 AND s.coll = 'organizations' AND s.id = $2)
                  AND EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = $1 AND e.coll = ANY($3::text[]) AND e.from_coll = 'organizations' AND e.from_id = $2
                                AND e.to_coll = 'organizations' AND e.to_id = n.id)
                ORDER BY n.id COLLATE "C" FOR UPDATE OF n),
            u AS (UPDATE graph.nodes n SET doc = n.doc || jsonb_build_object('parentOrgId', $2::text) FROM t
                WHERE n.org_id = $1 AND n.coll = 'organizations' AND n.id = t.id AND coalesce(n.doc->'parentOrgId', 'null'::jsonb) = 'null'::jsonb RETURNING 1)
            SELECT count(*) FROM u""", current_org_id(), org_id, _PARENT_EDGES), None, write=True))

    async def find_organizations(self, org_id: str, keys: list[str], transaction: str | None = None) -> list[dict[str, Any]]:
        """The external organisations of ``org_id`` whose ``normalizedName`` is one of ``keys``, by id: ``{"id", "name", "normalizedName"}``."""
        wanted = sorted({k for k in keys if k})
        if not org_id or not wanted or not self._mine(org_id):
            return []
        rows = None
        if len(org_id) <= 200 and len(wanted) <= 1000 and all(len(k) <= 1000 for k in wanted):      # the vetted read of graph_0006
            got = await self._id_rows("/* pipeshub:graph.organizations_by_name */ SELECT node_id AS id, doc FROM graph.organizations_by_name($1, $2::text[])", [org_id, wanted], transaction)
            if len(got) < 5000:      # a full page may not be all of them
                rows = sorted(got, key=lambda r: r["id"])      # byte order, as COLLATE "C": a str compares by code point, which is the UTF-8 byte order
        if rows is None:
            rows = await self._id_rows("""/* pipeshub:graph.find_organizations */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = 'organizations'
                AND doc->>'parentOrgId' = $2 AND doc->>'normalizedName' = ANY($3::text[]) AND doc->'isExternal' = 'true'::jsonb ORDER BY id COLLATE "C" """,
                                       [current_org_id(), org_id, wanted], transaction)
        return [{"id": r["id"], "name": (d := _doc(r["doc"])).get("name"), "normalizedName": d.get("normalizedName")} for r in rows]

    async def create_organization_if_absent(self, org_id: str, node: dict[str, Any], transaction: str | None = None) -> None:
        """Creates the extracted organisation (external, active, of ``org_id``) unless a node has its id; an existing node is left as it is."""
        key = node.get("id") or node.get("_key")
        if not org_id or not key or not node.get("name"):
            raise ValueError("an extracted organisation needs an org, an id and a name")
        self._require_org(org_id)
        now = get_epoch_timestamp_in_ms()
        props = {"name": node["name"], "normalizedName": node.get("normalizedName"), "accountType": "enterprise", "isActive": True, "isExternal": True,
                 "parentOrgId": org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now}
        await self.create_node_if_absent(key, _ORGS, {"id": key, **props}, transaction)

    async def get_organization_record_reach(self, org_id: str, keys: list[str], transaction: str | None = None, *,
                                            record_cap: int | None = None) -> dict[str, dict[str, Any]]:
        """For each of ``keys`` that is an external organisation of ``org_id``: how many live records of the org name it over an EXTRACTED edge (each record
        once, even with edges of two types to it), counted up to ``record_cap``, and whether a connector knows it (an INFERRED edge from a live record, or a
        ``dealOf``/``prospect``/``customer`` edge to it)."""
        wanted = sorted({k for k in keys if k})
        if not org_id or not wanted or not self._mine(org_id):
            return {}
        rows = await self._id_rows("""/* pipeshub:graph.get_organization_record_reach */ SELECT o.id,
              (SELECT count(*) FROM (SELECT DISTINCT r.id FROM graph.edges e JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
                 WHERE e.org_id = $1 AND e.coll = 'entityRelations' AND e.from_coll = 'records' AND e.to_coll = 'organizations' AND e.to_id = o.id
                   AND coalesce(e.doc->>'origin', $5) = $6 AND r.doc->>'orgId' = $2 AND coalesce((r.doc->>'isDeleted')::boolean, false) = false LIMIT $4) c) AS records,
              EXISTS (SELECT 1 FROM graph.edges e JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
                 WHERE e.org_id = $1 AND e.coll = 'entityRelations' AND e.from_coll = 'records' AND e.to_coll = 'organizations' AND e.to_id = o.id
                   AND coalesce(e.doc->>'origin', $5) <> $6 AND r.doc->>'orgId' = $2 AND coalesce((r.doc->>'isDeleted')::boolean, false) = false)
              OR EXISTS (SELECT 1 FROM graph.edges a JOIN graph.nodes s ON s.org_id = a.org_id AND s.coll = a.from_coll AND s.id = a.from_id
                 WHERE a.org_id = $1 AND a.coll = ANY($7::text[]) AND a.to_coll = 'organizations' AND a.to_id = o.id) AS inferred
            FROM graph.nodes o WHERE o.org_id = $1 AND o.coll = 'organizations' AND o.id = ANY($3::text[]) AND o.doc->'isExternal' = 'true'::jsonb AND o.doc->>'parentOrgId' = $2""",
                                   [current_org_id(), org_id, wanted, _record_cap(record_cap), EntityOrigin.INFERRED.value, EntityOrigin.EXTRACTED.value, _ACCOUNT_EDGES],
                                   transaction)
        return {r["id"]: {"records": int(r["records"]), "inferred": bool(r["inferred"])} for r in rows}
