"""Permissions: who a user is to the graph, and the batch access check that every permission decision goes through."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.org_context import current_org_id
from app.models.permission import ORG_SHARE_PERMISSION_TYPES
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.base import PgBase

if TYPE_CHECKING:
    from app.core.db import Tx

_NO_ACCESS: dict[str, Any] = {"grantee_ids": [], "gated_app_ids": [], "by_connector": {}}


class PermissionsMixin(PgBase):
    async def get_knowledge_hub_access_v3(self, user_key: str, org_id: str, *, transaction: str | None = None) -> dict[str, Any]:
        """Who this user is (the user, the groups, roles and teams they hold a USER permission on, their organization), the apps they pass the
        connector gate for, and the records and groups granted to them, per connector. An org other than the ambient one is nobody's."""
        org = current_org_id()
        if org_id != org:
            return dict(_NO_ACCESS)

        async def go(tx: Tx) -> dict:
            # single-table lookups (edges by the key, nodes by primary key): no plan depends on table statistics
            async def exist(coll: str, ids: list[str]) -> set[str]:
                if not ids:
                    return set()
                return {r["id"] for r in await tx.fetch("/* pipeshub:graph.access_exist */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[])",
                                                        org, coll, ids)}

            via = await tx.fetch("""/* pipeshub:graph.access_grantee_edges */ SELECT e.coll, e.to_coll, e.to_id, e.doc->>'type' AS t FROM graph.edges e WHERE e.org_id = $1
                AND e.coll IN ('permission', 'belongsTo') AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll IN ('groups', 'roles', 'teams', 'organizations')""", org, user_key)
            grantees = sorted(await exist("users", [user_key]))
            for coll in ("groups", "roles", "teams"):
                grantees += sorted(await exist(coll, [r["to_id"] for r in via if r["coll"] == "permission" and r["to_coll"] == coll and r["t"] == "USER"]))
            grantees += sorted(await exist("organizations", [r["to_id"] for r in via if r["coll"] == "belongsTo" and r["to_coll"] == "organizations"]))
            if user_key not in grantees:
                return dict(_NO_ACCESS)
            links = await tx.fetch("""/* pipeshub:graph.access_links */ SELECT e.to_id, e.doc->>'connectorId' AS cid FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = 'authenticatedAs' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = 'users'""", org, user_key)
            live = await exist("users", [r["to_id"] for r in links])
            links = [r for r in links if r["to_id"] in live]          # the source account must exist
            app_ids = {r["to_id"] for r in await tx.fetch("""/* pipeshub:graph.access_gate_edges */ SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1
                AND e.coll IN ('permission', 'userAppRelation') AND e.from_coll = ANY($3::text[]) AND e.to_coll = 'apps' AND e.from_id = ANY($2::text[])""",
                                                  org, grantees, access_sql.PRINCIPALS)}
            app_ids |= {x["cid"] for x in links if x["cid"]}
            gated = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.access_gate */ SELECT a.id FROM graph.nodes a WHERE a.org_id = $1 AND a.coll = 'apps'
                AND a.doc->>'orgId' = $1 AND a.id = ANY($2::text[])""", org, sorted(app_ids))]
            by_conn: dict[str, list[str]] = {a: [] for a in gated}
            for cid, ids in (await access_sql.granted_targets(tx, org, grantees, list(ORG_SHARE_PERMISSION_TYPES))).items():
                if cid in by_conn:
                    by_conn[cid].extend(ids)
            for lk in links:  # a source account the user authenticated as adds its own grants, and its badges', inside that connector
                if not lk["cid"] or lk["cid"] not in by_conn:
                    continue
                badges = [r["to_id"] for r in await tx.fetch("""/* pipeshub:graph.access_linked_groups */ SELECT b.to_id FROM graph.edges b WHERE b.org_id = $1
                    AND b.coll = 'permission' AND b.from_coll = 'users' AND b.from_id = $2 AND b.to_coll IN ('groups', 'roles', 'teams') AND b.doc->>'type' = 'USER'""", org, lk["to_id"])]
                # not from an organization here: the edges are read for the account and its groups only
                by_conn[lk["cid"]].extend((await access_sql.granted_targets(tx, org, [lk["to_id"], *badges], []))
                                          .get(lk["cid"], []))
            return {"grantee_ids": grantees, "gated_app_ids": gated, "by_connector": {k: list(dict.fromkeys(v)) for k, v in by_conn.items()}}
        return await self._q(go, transaction)

    async def _kh_v3_accessible_rows(self, user_key: str, org_id: str, node_ids: list[str], virtual_record_ids: list[str], *,
                                     access: dict[str, Any] | None, transaction: str | None) -> list[dict[str, Any]]:
        """The asked nodes and virtual record ids the user may access, as the rows ``check_access`` reads. An org other than the ambient one admits nothing."""
        org = current_org_id()
        ids = list(dict.fromkeys(i for i in node_ids if i))
        vrids = list(dict.fromkeys(v for v in virtual_record_ids if v))
        if (not ids and not vrids) or org_id != org:
            return []
        if access is None:
            access = await self.get_knowledge_hub_access_v3(user_key, org_id, transaction=transaction)
        return await self._q(lambda tx: access_sql.accessible_rows(tx, org, ids, vrids, access), transaction)
