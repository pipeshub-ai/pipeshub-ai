"""Permissions: who a user is to the graph, and the batch access check that every permission decision goes through."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.neo4j import parse_node_id
from app.core.org_context import current_org_id
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.entities import AppUserGroup, User
from app.models.permission import ORG_SHARE_PERMISSION_TYPES, RETIRED_ROLES
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.team_grant_role import (
    TEAM_GRANT_ROLE_CAP,
    TEAM_GRANT_ROLES,
)
from app.services.graph_db.interface.graph_db_provider import _distinct_connector_types
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.base import PgBase, _arango, _doc, _j
from app.services.graph_db.postgres.identity import _soft
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx

_NO_ACCESS: dict[str, Any] = {"grantee_ids": [], "gated_app_ids": [], "by_connector": {}}


_ROLE_RANK = {"OWNER": 4, "WRITER": 3, "READER": 2}
_AGENT_CAN = {"can_edit": ("OWNER", "WRITER", "ORGANIZER"), "can_delete": ("OWNER",), "can_share": ("OWNER", "ORGANIZER")}
_INHERIT_HOPS = 8     # the longest INHERIT_PERMISSIONS chain the walk to a node's grantees follows, as on the other stores


class PermissionsMixin(PgBase):
    async def get_user_kb_permission(self, kb_id: str, user_id: str, transaction: str | None = None) -> str | None:
        """The user's effective role on a collection: the highest of the direct grant and what each team the collection is shared with gives its members.
        A team's grant gives the role on its own edge, else the member's role in the team, and never more than ``TEAM_GRANT_ROLE_CAP``: a team owner
        does not own what the team is shared with. A retired role reads as READER; a role that is none of the grant roles gives nothing, the direct grant's too."""
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_user_kb_permission */
            WITH me AS (SELECT 1 FROM graph.nodes u WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $2),
            kb AS (SELECT k.id FROM graph.nodes k WHERE k.org_id = $1 AND k.coll = 'apps' AND k.id = $3 AND k.doc->>'type' = 'KB')
            SELECT CASE WHEN d.doc->>'role' = ANY($5::text[]) THEN 'READER' WHEN d.doc->>'role' = ANY($6::text[]) THEN d.doc->>'role' END AS role FROM me, kb JOIN graph.edges d ON d.org_id = $1 AND d.coll = 'permission' AND d.from_coll = 'users' AND d.from_id = $2
              AND d.to_coll = 'apps' AND d.to_id = kb.id AND d.doc->>'type' = 'USER'
            UNION ALL
            SELECT CASE WHEN b.role = 'OWNER' THEN $4::text WHEN b.role = ANY($5::text[]) THEN 'READER' WHEN b.role = ANY($6::text[]) THEN b.role END
            FROM me, kb JOIN graph.edges ut ON ut.org_id = $1 AND ut.coll = 'permission' AND ut.from_coll = 'users' AND ut.from_id = $2 AND ut.to_coll = 'teams' AND ut.doc->>'type' = 'USER'
              JOIN graph.nodes t ON t.org_id = $1 AND t.coll = 'teams' AND t.id = ut.to_id
              JOIN graph.edges tg ON tg.org_id = $1 AND tg.coll = 'permission' AND tg.from_coll = 'teams' AND tg.from_id = t.id AND tg.to_coll = 'apps' AND tg.to_id = kb.id AND tg.doc->>'type' = 'TEAM',
              LATERAL (SELECT CASE WHEN tg.doc->>'role' IS NOT NULL AND tg.doc->>'role' <> '' THEN tg.doc->>'role' ELSE ut.doc->>'role' END AS role) b""",
                                                  org, user_id, kb_id, TEAM_GRANT_ROLE_CAP, sorted(RETIRED_ROLES), list(TEAM_GRANT_ROLES)), transaction)
        # Fail closed: only a grant role (a retired one already read as READER in the statement) counts; any other stored role gives nothing, so it
        # can neither be ranked nor handed back as a role.
        roles = [r["role"] for r in rows if r["role"] in _ROLE_RANK]
        return max(roles, key=_ROLE_RANK.__getitem__) if roles else None

    async def _gate(self, tx: Tx, org: str, user_key: str) -> tuple[list[str], list[str], list[Any]] | None:
        """Who the user is (``grantees``), the apps they pass the connector gate for, and the source accounts they authenticated as; None when the
        user is no node of the graph. The part of ``get_knowledge_hub_access_v3`` that does not read the grants."""
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
            return None
        links = await tx.fetch("""/* pipeshub:graph.access_links */ SELECT e.to_id, e.doc->>'connectorId' AS cid FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = 'authenticatedAs' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = 'users'""", org, user_key)
        live = await exist("users", [r["to_id"] for r in links])
        links = [r for r in links if r["to_id"] in live]          # the source account must exist
        # A grant on a collection (an app of type KB) counts only with a role it recognises, as ``get_user_kb_permission`` reads it: a retired role is
        # READER, a role of OWNER, WRITER or READER is itself, a team's grant without a role takes the member's role in the team; any other role, or none,
        # is no grant. A connector's grant has no role to read.
        app_ids = {r["to_id"] for r in await tx.fetch("""/* pipeshub:graph.access_gate_edges */ SELECT e.to_id FROM graph.edges e
            JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'apps' AND a.id = e.to_id
            WHERE e.org_id = $1 AND e.coll IN ('permission', 'userAppRelation') AND e.from_coll = ANY($3::text[]) AND e.to_coll = 'apps' AND e.from_id = ANY($2::text[])
              AND (e.coll <> 'permission' OR coalesce(a.doc->>'type', '') <> 'KB' OR coalesce(nullif(e.doc->>'role', ''), CASE WHEN e.from_coll = 'teams' THEN (
                    SELECT ut.doc->>'role' FROM graph.edges ut WHERE ut.org_id = $1 AND ut.coll = 'permission' AND ut.from_coll = 'users' AND ut.from_id = $4
                      AND ut.to_coll = 'teams' AND ut.to_id = e.from_id AND ut.doc->>'type' = 'USER' LIMIT 1) END) = ANY($5::text[]))""",
                                              org, grantees, access_sql.PRINCIPALS, user_key, [*_ROLE_RANK, *sorted(RETIRED_ROLES)])}
        app_ids |= {x["cid"] for x in links if x["cid"]}
        gated = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.access_gate */ SELECT a.id FROM graph.nodes a WHERE a.org_id = $1 AND a.coll = 'apps'
            AND a.doc->>'orgId' = $1 AND a.id = ANY($2::text[])""", org, sorted(app_ids))]
        return grantees, gated, links

    async def get_knowledge_hub_access_v3(self, user_key: str, org_id: str, *, transaction: str | None = None) -> dict[str, Any]:
        """Who this user is (the user, the groups, roles and teams they hold a USER permission on, their organization), the apps they pass the
        connector gate for, and the records and groups granted to them, per connector. An org other than the ambient one is nobody's."""
        org = current_org_id()
        if org_id != org:
            return dict(_NO_ACCESS)

        async def go(tx: Tx) -> dict:
            gate = await self._gate(tx, org, user_key)
            if gate is None:
                return dict(_NO_ACCESS)
            grantees, gated, links = gate
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

    # ---- who holds a grant on a node, and the writes of grants -----------------------------------------------------
    async def _users_granted_on(self, node_id: str, limit: int | None, transaction: str | None) -> list[dict]:
        """The users holding a PERMISSION edge on the node or on an ancestor it inherits from (up to ``_INHERIT_HOPS``), nearest first, then by id."""
        key = parse_node_id(node_id)[1] or node_id
        return await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_users_with_permission_to_node */ WITH RECURSIVE anc(coll, id, depth) AS (
              SELECT n.coll, n.id, 0 FROM graph.nodes n WHERE n.org_id = $1 AND n.id = $2
              UNION
              SELECT i.to_coll, i.to_id, a.depth + 1 FROM anc a JOIN graph.edges i ON i.org_id = $1 AND i.coll = 'inheritPermissions'
                AND i.from_coll = a.coll AND i.from_id = a.id WHERE a.depth < $3)
            SELECT u.doc FROM anc
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.to_coll = anc.coll AND p.to_id = anc.id
              JOIN graph.nodes u ON u.org_id = $1 AND u.coll = 'users' AND u.id = p.from_id
            GROUP BY u.id, u.doc ORDER BY min(anc.depth), u.id LIMIT $4""", current_org_id(), key, _INHERIT_HOPS, limit), transaction)

    @_soft(None)
    async def get_first_user_with_permission_to_node(self, node_id: str, node_collection: str, transaction: str | None = None) -> User | None:
        rows = await self._users_granted_on(node_id, 1, transaction)
        return User.from_arango_user(_arango(_doc(rows[0]["doc"]), "users")) if rows else None

    @_soft(list, flag=True)
    async def get_users_with_permission_to_node(self, node_id: str, node_collection: str, transaction: str | None = None, *,
                                                raise_on_error: bool = False) -> list[User]:
        return [User.from_arango_user(_arango(_doc(r["doc"]), "users")) for r in await self._users_granted_on(node_id, None, transaction)]

    @_soft(list, flag=True)
    async def get_groups_with_permission_to_node(self, node_id: str, node_collection: str, transaction: str | None = None, *,
                                                 raise_on_error: bool = False) -> list[AppUserGroup]:
        """The synced user groups (those with an ``externalGroupId``) holding a PERMISSION edge on the node itself."""
        key = parse_node_id(node_id)[1] or node_id
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_groups_with_permission_to_node */ SELECT DISTINCT g.id, g.doc FROM graph.nodes n
            JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'groups' AND p.to_coll = n.coll AND p.to_id = n.id
            JOIN graph.nodes g ON g.org_id = $1 AND g.coll = 'groups' AND g.id = p.from_id AND g.doc->>'externalGroupId' IS NOT NULL
            WHERE n.org_id = $1 AND n.id = $2 ORDER BY g.id""", current_org_id(), key), transaction)
        return [AppUserGroup.from_arango_base_user_group(_arango(_doc(r["doc"]), "groups")) for r in rows]

    async def batch_upsert_record_permissions(self, record_id: str, permissions: list[dict], transaction: str | None = None) -> None:
        """Grants on one record: each edge is pointed at the record, whatever its own target says."""
        if permissions:
            await self.batch_create_edges([{**p, "to_id": record_id, "to_collection": "records"} for p in permissions], "permission", transaction)

    async def create_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str, transaction: str | None = None) -> None:
        now = get_epoch_timestamp_in_ms()
        await self.batch_create_edges([{"from_id": record_id, "from_collection": "records", "to_id": record_group_id, "to_collection": "recordGroups",
                                        "createdAtTimestamp": now, "updatedAtTimestamp": now}], "inheritPermissions", transaction)

    async def remove_user_access_to_record(self, connector_id: str, external_id: str, user_id: str, transaction: str | None = None) -> None:
        """Takes the user's grant on the record (live or in the trash) away; a record that is not there is a no-op."""
        record = await self.get_record_by_external_id(connector_id, external_id, transaction, visibility=RecordVisibility.ALL)
        if record:
            await self.delete_edge(user_id, "users", record.id, "records", "permission", transaction)

    # ---- teams ---------------------------------------------------------------------------------------------------
    async def batch_update_team_member_roles(self, team_id: str, user_roles: list[dict[str, str]], timestamp: int, transaction: str | None = None) -> list[dict]:
        """Sets the role on each listed user's edge to the team (a user with no edge is skipped); the updated edges come back. A user named twice gets the last role."""
        last = {ur["userId"]: ur.get("role") for ur in user_roles}
        if not last:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.batch_update_team_member_roles */ UPDATE graph.edges e
            SET doc = jsonb_strip_nulls(e.doc || jsonb_build_object('role', x.role, 'updatedAtTimestamp', $4::bigint))
            FROM jsonb_to_recordset($3::jsonb) AS x("userId" text, role text)
            WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = x."userId" AND e.to_coll = 'teams' AND e.to_id = $2
            RETURNING e.doc, e.from_id, e.to_id""", current_org_id(), team_id, _j([{"userId": u, "role": r} for u, r in last.items()]), timestamp), transaction, write=True)
        return [{**_doc(r["doc"]), "from_id": r["from_id"], "to_id": r["to_id"], "from_collection": "users", "to_collection": "teams",
                 "_from": f"users/{r['from_id']}", "_to": f"teams/{r['to_id']}"} for r in rows]

    async def delete_all_team_permissions(self, team_id: str, transaction: str | None = None) -> None:
        """Every grant on the team (its members' edges) goes; the team's own grants to apps stay."""
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.delete_all_team_permissions */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'permission'
            AND to_coll = 'teams' AND to_id = $2""", current_org_id(), team_id), transaction, write=True)

    async def get_team_permissions_and_owner_count(self, team_id: str, user_ids: list[str], transaction: str | None = None) -> dict[str, Any]:
        """The team (None when absent), the given users' roles on it, and how many owners it has."""
        org = current_org_id()

        async def go(tx: Tx) -> dict[str, Any]:
            team = await tx.fetchrow("/* pipeshub:graph.team_for_permissions */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'teams' AND id = $2", org, team_id)
            perms = await tx.fetch("""/* pipeshub:graph.team_member_roles */ SELECT p.from_id, p.doc->>'role' AS role FROM graph.edges p JOIN graph.nodes u
                ON u.org_id = p.org_id AND u.coll = 'users' AND u.id = p.from_id WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users'
                AND p.to_coll = 'teams' AND p.to_id = $2 AND p.from_id = ANY($3::text[])""", org, team_id, list(user_ids))
            owners = await tx.fetchval("""/* pipeshub:graph.team_owner_count */ SELECT count(*) FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'permission'
                AND p.to_coll = 'teams' AND p.to_id = $2 AND p.doc->>'role' = 'OWNER'""", org, team_id)
            return {"team": _arango(_doc(team["doc"]), "teams") if team else None, "permissions": {r["from_id"]: r["role"] for r in perms}, "owner_count": owners or 0}
        return await self._q(go, transaction)

    # ---- agents, apps and the knowledge hub ----------------------------------------------------------------------
    async def check_agent_permission(self, agent_id: str, user_id: str, org_id: str) -> dict | None:
        """How the user reaches the agent: their own grant, else their organization's; None for neither, for a deleted agent, and for another org."""
        if org_id != current_org_id():
            return None
        try:
            r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.check_agent_permission */ SELECT p.doc->>'role' AS role, p.doc->>'type' AS t FROM graph.nodes a
                JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.to_coll = 'agentInstances' AND p.to_id = a.id
                  AND ((p.from_coll = 'users' AND p.from_id = $3 AND p.doc->>'type' = 'USER') OR (p.from_coll = 'organizations' AND p.from_id = $1 AND p.doc->>'type' = 'ORG'))
                JOIN graph.nodes g ON g.org_id = $1 AND g.coll = p.from_coll AND g.id = p.from_id
                WHERE a.org_id = $1 AND a.coll = 'agentInstances' AND a.id = $2 AND a.doc->>'isDeleted' IS DISTINCT FROM 'true'
                ORDER BY (p.doc->>'type' = 'USER') DESC LIMIT 1""", org_id, agent_id, user_id), None)
        except Exception as e:
            self.logger.error("Failed to check agent permission: %s", e)
            return None
        if not r:
            return None
        role = r["role"]
        return {"access_type": "INDIVIDUAL" if r["t"] == "USER" else "ORG", "user_role": role, **{k: role in v for k, v in _AGENT_CAN.items()}, "can_view": True}

    async def get_user_permission_app_ids(self, user_key: str, org_id: str, transaction: str | None = None) -> list[str]:
        """Apps of the org the user holds a USER grant on, directly or through a team they are in that holds a TEAM grant on it; hidden ones left out."""
        if org_id != current_org_id():
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_user_permission_app_ids */ WITH ids AS (
              SELECT e.to_id AS app FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = $2
                AND e.to_coll = 'apps' AND e.doc->>'type' = 'USER'
              UNION SELECT g.to_id FROM graph.edges m JOIN graph.nodes t ON t.org_id = m.org_id AND t.coll = 'teams' AND t.id = m.to_id
                JOIN graph.edges g ON g.org_id = m.org_id AND g.coll = 'permission' AND g.from_coll = 'teams' AND g.from_id = t.id AND g.to_coll = 'apps' AND g.doc->>'type' = 'TEAM'
                WHERE m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = $2 AND m.to_coll = 'teams' AND m.doc->>'type' = 'USER')
            SELECT DISTINCT a.id FROM ids JOIN graph.nodes a ON a.org_id = $1 AND a.coll = 'apps' AND a.id = ids.app
            WHERE a.doc->>'orgId' = $1 AND a.doc->>'isHidden' IS DISTINCT FROM 'true'""", org_id, user_key), transaction)
        return [r["id"] for r in rows]

    async def get_gated_apps(self, user_key: str, org_id: str, transaction: str | None = None) -> list[dict]:
        return await self.get_user_apps(user_key, transaction) if org_id == current_org_id() else []

    async def get_knowledge_hub_access_context_v2(self, user_key: str, org_id: str, *, transaction: str | None = None) -> dict[str, list[str]]:
        """Who the user is and the apps they pass the gate for: the gate part of ``get_knowledge_hub_access_v3``, without reading the grants."""
        org = current_org_id()
        if org_id != org:
            return {"grantee_ids": [], "gated_app_ids": []}

        async def go(tx: Tx) -> dict[str, list[str]]:
            gate = await self._gate(tx, org, user_key)
            return {"grantee_ids": list(dict.fromkeys(gate[0])), "gated_app_ids": list(dict.fromkeys(gate[1]))} if gate else {"grantee_ids": [], "gated_app_ids": []}
        return await self._q(go, transaction)

    async def get_accessible_connector_types(self, user_id: str, org_id: str) -> list[str]:
        """Types of the apps the user passes the gate for. A failed read widens the search (an empty list), it never narrows it."""
        try:
            user = await self.get_user_by_user_id(user_id)
            user_key = (user or {}).get("id") or (user or {}).get("_key")
            return _distinct_connector_types(await self.get_gated_apps(user_key, org_id)) if user_key else []
        except Exception as e:
            self.logger.warning("Could not resolve accessible connector types: %s", e)
            return []

    async def get_knowledge_hub_node_access(self, node_id: str, user_key: str, org_id: str, folder_mime_types: list[str],
                                            transaction: str | None = None) -> dict[str, Any] | None:
        """The node's metadata when the user may access it (the batch check); None for a missing node and a refused one alike."""
        if org_id != current_org_id():
            return None
        try:
            checked = await self.check_access(user_key, org_id, node_ids=[node_id], transaction=transaction)
            if node_id not in checked.node_ids:
                return None
            row = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.get_knowledge_hub_node_access */ SELECT n.coll, n.doc, kb.id AS kb_id FROM graph.nodes n
                LEFT JOIN graph.nodes kb ON kb.org_id = n.org_id AND kb.coll = 'apps' AND kb.doc->>'type' = 'KB'
                  AND kb.id = CASE WHEN n.coll = 'apps' THEN n.id ELSE n.doc->>'connectorId' END
                WHERE n.org_id = $1 AND n.id = $2 AND n.coll IN ('records', 'recordGroups', 'apps') AND n.doc->>'orgId' = $1
                ORDER BY n.coll LIMIT 1""", org_id, node_id), transaction)
            if not row:
                return None
            d, coll = _doc(row["doc"]), row["coll"]
            if coll == "records":
                out = {"id": node_id, "name": d.get("recordName"), "nodeType": "folder" if d.get("mimeType") in folder_mime_types else "record", "subType": d.get("recordType"),
                       "connector": d.get("connectorName"), "webUrl": d.get("webUrl"), "recordType": d.get("recordType"), "indexingStatus": d.get("indexingStatus")}
            elif coll == "recordGroups":
                out = {"id": node_id, "name": d.get("groupName"), "nodeType": "recordGroup",
                       "subType": "COLLECTION" if d.get("connectorName") == "KB" else d.get("groupType") or d.get("connectorName"),
                       "connector": d.get("connectorName"), "webUrl": d.get("webUrl"), "recordType": None, "indexingStatus": None}
            else:
                out = {"id": node_id, "name": d.get("name"), "nodeType": "app", "subType": d.get("type"), "connector": d.get("type"), "webUrl": d.get("webUrl"),
                       "recordType": None, "indexingStatus": None}
            out["isPlaceholder"] = node_id in checked.placeholder_ids
            out["userRole"] = await self.get_user_kb_permission(row["kb_id"], user_key, transaction=transaction) if row["kb_id"] else None
            return out
        except PermissionVerificationUnavailableError:
            raise
        except Exception as e:
            self.logger.error("get_knowledge_hub_node_access failed: %s", e)
            return None
