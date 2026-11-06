"""Permissions: who a user is to the graph, and the batch access check that every permission decision goes through."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors, RecordTypes
from app.config.constants.neo4j import parse_node_id
from app.core.org_context import current_org_id
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.entities import AppUserGroup, User, substitute_user_email
from app.models.permission import ORG_SHARE_PERMISSION_TYPES, RETIRED_ROLES
from app.models.source_dates import DATED_ONLY_TIME_RANGE_KEY, upload_times_undated
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.team_grant_role import (
    TEAM_GRANT_ROLE_CAP,
    TEAM_GRANT_ROLES,
)
from app.services.graph_db.interface.graph_db_provider import (
    STRICT_SCOPE_FILTER_KEY,
    _distinct_connector_types,
    requested_scope_ids,
)
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.base import _VALIDATOR, PgBase, _arango, _doc, _j
from app.services.graph_db.postgres.identity import _soft
from app.services.graph_db.taxonomy import own_record_labels
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx

_NO_ACCESS: dict[str, Any] = {"grantee_ids": [], "gated_app_ids": [], "by_connector": {}}


_ROLE_RANK = {"OWNER": 4, "WRITER": 3, "READER": 2}
_AGENT_CAN = {"can_edit": ("OWNER", "WRITER", "ORGANIZER"), "can_delete": ("OWNER",), "can_share": ("OWNER", "ORGANIZER")}
_KB_TYPE = Connectors.KNOWLEDGE_BASE.value
_CTX_RANK = {"OWNER": 6, "ADMIN": 5, "EDITOR": 4, "WRITER": 3, "READER": 1}
_DETAIL_RANK = {"OWNER": 3, "WRITER": 2, "READER": 1}      # the roles a record's access details name
_CTX_UP = ("OWNER", "ADMIN", "EDITOR", "WRITER")
_CHECK_CHUNK = 2000     # candidate records handed to the access check at once
# the metadata filters of a search: key, the edge from the record, the collection of the item, the field its name is in
_META_FILTERS = (("departments", "belongsToDepartment", "departments", "departmentName"), ("categories", "belongsToCategory", "categories", "name"),
                 ("subcategories1", "belongsToCategory", "subcategories1", "name"), ("subcategories2", "belongsToCategory", "subcategories2", "name"),
                 ("subcategories3", "belongsToCategory", "subcategories3", "name"), ("languages", "belongsToLanguage", "languages", "name"),
                 ("topics", "belongsToTopic", "topics", "name"))


def _ctx_perms(role: str, *, admin: bool | None = None) -> dict[str, Any]:
    """What a role may do inside a folder, collection or connector; at the root (``admin`` given) only an administrator may."""
    up = admin if admin is not None else role in _CTX_UP
    return {"role": role, "canUpload": up, "canCreateFolders": up, "canEdit": up,
            "canDelete": admin if admin is not None else role in ("OWNER", "ADMIN", "WRITER"),
            "canManagePermissions": admin if admin is not None else role in ("OWNER", "ADMIN")}


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

    async def get_selection_nodes(self, org_id: str, *, group_ids: list[str], record_ids: list[str],
                                  exact_record_ids: list[str], limit: int) -> dict[str, list[dict[str, Any]]]:
        """What a selection covers, from the hierarchy alone (see the interface). Walks down a level at a time with single-table lookups,
        through live nodes only, and stops once more than ``limit`` nodes are found (the caller reads that as too large). Another org reads nothing."""
        org = current_org_id()
        if org_id != org:
            return {"groups": [], "records": []}

        async def walk(tx: Tx, coll: str, roots: list[str], exact: list[str]) -> list[dict[str, Any]]:
            found: dict[str, dict[str, Any]] = {}

            async def live(ids: list[str]) -> list[Any]:
                return await tx.fetch("""/* pipeshub:graph.selection_nodes_live */ SELECT id, doc->>'connectorId' AS cid, doc->>'virtualRecordId' AS vrid FROM graph.nodes
                    WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[]) AND coalesce((doc->>'isDeleted')::boolean, false) = false""", org, coll, ids)

            rows = await live(list(roots))
            for hop in range(access_sql.MAX_HOPS + 1):
                fresh = [r for r in rows if r["id"] not in found]
                found.update((r["id"], r) for r in fresh)
                frontier = [r["id"] for r in fresh]
                if not frontier or len(found) > limit or hop == access_sql.MAX_HOPS:
                    break
                # only live children not found yet, and no more than would take the walk past the limit: a wide level is never read in full
                rows = await tx.fetch("""/* pipeshub:graph.selection_nodes_children */ SELECT DISTINCT n.id, n.doc->>'connectorId' AS cid, n.doc->>'virtualRecordId' AS vrid
                    FROM graph.edges e JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = e.to_coll AND n.id = e.to_id
                    WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = $2 AND e.to_coll = $2 AND e.from_id = ANY($3::text[])
                      AND e.doc->>'relationshipType' = ANY($4::text[]) AND coalesce((n.doc->>'isDeleted')::boolean, false) = false AND n.id <> ALL($5::text[])
                    LIMIT $6""", org, coll, frontier, ["PARENT_CHILD", "ATTACHMENT"], list(found), limit + 1 - len(found))
            if exact:
                found.update((r["id"], r) for r in await live(exact) if r["id"] not in found)
            return list(found.values())[:limit + 1]

        async def go(tx: Tx) -> dict[str, list[dict[str, Any]]]:
            groups = await walk(tx, "recordGroups", group_ids, []) if group_ids else []
            records = await walk(tx, "records", record_ids, exact_record_ids) if record_ids or exact_record_ids else []
            return {"groups": [{"id": g["id"], "connectorId": g["cid"]} for g in groups],
                    "records": [{"id": r["id"], "vrid": r["vrid"], "connectorId": r["cid"]} for r in records]}
        return await self._q(go, None)

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
        org = current_org_id()
        for role in set(last.values()):
            _VALIDATOR.validate_edge_update("permission", {"role": role, "updatedAtTimestamp": timestamp})
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.batch_update_team_member_roles */ UPDATE graph.edges e
            SET doc = jsonb_strip_nulls(e.doc || jsonb_build_object('role', x.role, 'updatedAtTimestamp', $4::bigint))
            FROM jsonb_to_recordset($3::jsonb) AS x("userId" text, role text)
            WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = x."userId" AND e.to_coll = 'teams' AND e.to_id = $2
            RETURNING e.doc, e.from_id, e.to_id""", org, team_id, _j([{"userId": u, "role": r} for u, r in last.items()]), timestamp), transaction, write=True)
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

    # ---- the context role, the record access details, and the accessible search scope ----------------------------
    async def _role_on(self, tx: Tx, org: str, user_key: str, coll: str, node_id: str, ranks: dict[str, int] = _CTX_RANK) -> tuple[str, str] | None:
        """The highest role the user holds on a record or record group: their own grant, a group's, a role's, a team's (never above WRITER) or the
        organization's, on the node or on what it inherits from (20 hops) or the collection it belongs to; a source account the user authenticated
        as counts inside that connector. ``(role, source)``, a retired role read as READER; None when no grant gives one."""
        rows = await tx.fetch("""/* pipeshub:graph.node_roles */ WITH RECURSIVE anc(coll, id, depth) AS (
              SELECT n.coll, n.id, 0 FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2 AND n.id = $3
              UNION
              SELECT i.to_coll, i.to_id, a.depth + 1 FROM anc a JOIN graph.edges i ON i.org_id = $1 AND i.coll = 'inheritPermissions'
                AND i.from_coll = a.coll AND i.from_id = a.id WHERE a.depth < 20),
            targets AS (
              SELECT coll, id, depth FROM anc WHERE depth = 0 OR coll IN ('recordGroups', 'apps')
              UNION SELECT b.to_coll, b.to_id, 0 FROM graph.edges b JOIN graph.nodes k ON k.org_id = $1 AND k.coll = 'apps' AND k.id = b.to_id AND k.doc->>'type' = 'KB'
                WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = $2 AND b.from_id = $3),
            principals AS (
              SELECT u.id FROM graph.nodes u WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $4
              UNION SELECT l.to_id FROM graph.edges l JOIN graph.nodes s ON s.org_id = $1 AND s.coll = 'users' AND s.id = l.to_id
                WHERE l.org_id = $1 AND l.coll = 'authenticatedAs' AND l.from_coll = 'users' AND l.from_id = $4 AND l.to_coll = 'users'
                  AND l.doc->>'connectorId' = (SELECT x.doc->>'connectorId' FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = $2 AND x.id = $3))
            SELECT 'direct' AS kind, t.coll AS tcoll, t.id AS tid, p.doc->>'role' AS role, NULL::text AS member_role, k.doc->>'type' AS app_type
              FROM targets t JOIN principals pr ON true
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = pr.id AND p.to_coll = t.coll AND p.to_id = t.id
                AND p.doc->>'type' = 'USER' AND coalesce(p.doc->>'role', '') <> ''
              LEFT JOIN graph.nodes k ON k.org_id = $1 AND k.coll = 'apps' AND k.id = t.id AND t.coll = 'apps'
            UNION ALL
            SELECT 'group', t.coll, t.id, p.doc->>'role', NULL, NULL FROM targets t
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll IN ('groups', 'roles') AND p.to_coll = t.coll AND p.to_id = t.id AND coalesce(p.doc->>'role', '') <> ''
              JOIN graph.nodes g ON g.org_id = $1 AND g.coll = p.from_coll AND g.id = p.from_id
              WHERE EXISTS (SELECT 1 FROM principals pr JOIN graph.edges m ON m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = pr.id
                AND m.to_coll = p.from_coll AND m.to_id = p.from_id AND m.doc->>'type' = 'USER')
            UNION ALL
            SELECT 'team', t.coll, t.id, p.doc->>'role', m.doc->>'role', NULL FROM targets t
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'teams' AND p.to_coll = t.coll AND p.to_id = t.id AND p.doc->>'type' = 'TEAM'
              JOIN graph.nodes g ON g.org_id = $1 AND g.coll = 'teams' AND g.id = p.from_id
              JOIN principals pr ON true
              JOIN graph.edges m ON m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = pr.id AND m.to_coll = 'teams' AND m.to_id = p.from_id
                AND m.doc->>'type' = 'USER'
            UNION ALL
            SELECT 'org', t.coll, t.id, p.doc->>'role', NULL, NULL FROM targets t JOIN principals pr ON true
              JOIN graph.edges b ON b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'users' AND b.from_id = pr.id AND b.to_coll = 'organizations' AND b.doc->>'entityType' = 'ORGANIZATION'
              JOIN graph.nodes o ON o.org_id = $1 AND o.coll = 'organizations' AND o.id = b.to_id
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'organizations' AND p.from_id = o.id AND p.to_coll = t.coll AND p.to_id = t.id
                AND p.doc->>'type' = 'ORG' AND coalesce(p.doc->>'role', '') <> ''""", org, coll, node_id, user_key)
        best: tuple[str, str] | None = None
        for r in rows:
            role = r["role"]
            if r["kind"] == "team":
                role = r["role"] if r["role"] else r["member_role"]
                role = TEAM_GRANT_ROLE_CAP if role == "OWNER" else role
            if role in RETIRED_ROLES:
                role = "READER"
            if r["kind"] == "direct" and r["app_type"] == _KB_TYPE and role not in TEAM_GRANT_ROLES:
                continue      # a direct grant on a collection counts only with a collection role
            if r["kind"] == "team" and role not in TEAM_GRANT_ROLES:
                continue
            if role not in ranks:
                continue
            source = ("KNOWLEDGE_BASE" if r["app_type"] == _KB_TYPE else "DIRECT") if r["kind"] == "direct" else {
                "group": "GROUP", "team": "KNOWLEDGE_BASE_TEAM" if r["tcoll"] == "apps" else "GROUP", "org": "ORGANIZATION"}[r["kind"]]
            if r["kind"] == "direct" and not (r["tcoll"] == "apps" or (r["tcoll"], r["tid"]) == (coll, node_id)):
                source = "DIRECT_USER_RECORD_GROUP"
            if best is None or ranks[role] > ranks[best[0]]:
                best = (role, source)
        return best

    async def _app_role(self, tx: Tx, org: str, user_key: str, app_id: str) -> str | None:
        """The user's role on an app (a connector or a collection); None when nothing relates them."""
        user = await tx.fetchrow("/* pipeshub:graph.ctx_user */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id = $2", org, user_key)
        app = await tx.fetchrow("/* pipeshub:graph.ctx_app */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = $2", org, app_id)
        if not user or not app:
            return None
        u, a = _doc(user["doc"]), _doc(app["doc"])
        rows = await tx.fetch("""/* pipeshub:graph.ctx_app_edges */ SELECT 'own' AS kind, NULL::text AS role, NULL::text AS member_role FROM graph.edges e
              WHERE e.org_id = $1 AND e.coll = 'userAppRelation' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = 'apps' AND e.to_id = $3
            UNION ALL SELECT 'own', NULL, NULL FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'authenticatedAs' AND e.from_coll = 'users' AND e.from_id = $2
              AND e.to_coll = 'users' AND e.doc->>'connectorId' = $3
            UNION ALL SELECT 'team_rel', NULL, NULL FROM graph.edges m JOIN graph.nodes t ON t.org_id = m.org_id AND t.coll = 'teams' AND t.id = m.to_id
              JOIN graph.edges e ON e.org_id = m.org_id AND e.coll = 'userAppRelation' AND e.from_coll = 'teams' AND e.from_id = t.id AND e.to_coll = 'apps' AND e.to_id = $3
              WHERE m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = $2 AND m.to_coll = 'teams' AND m.doc->>'type' = 'USER'
            UNION ALL SELECT 'direct', p.doc->>'role', NULL FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = $2
              AND p.to_coll = 'apps' AND p.to_id = $3 AND p.doc->>'type' = 'USER'
            UNION ALL SELECT 'team_grant', g.doc->>'role', m.doc->>'role' FROM graph.edges m JOIN graph.nodes t ON t.org_id = m.org_id AND t.coll = 'teams' AND t.id = m.to_id
              JOIN graph.edges g ON g.org_id = m.org_id AND g.coll = 'permission' AND g.from_coll = 'teams' AND g.from_id = t.id AND g.to_coll = 'apps' AND g.to_id = $3
                AND g.doc->>'type' = 'TEAM'
              WHERE m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = $2 AND m.to_coll = 'teams' AND m.doc->>'type' = 'USER'""",
                              org, user_key, app_id)
        kinds = {r["kind"] for r in rows}
        direct = [r["role"] for r in rows if r["kind"] == "direct"]
        team_roles = set()
        for r in rows:
            if r["kind"] == "team_grant":
                base = r["role"] if r["role"] else r["member_role"]
                team_roles.add(TEAM_GRANT_ROLE_CAP if base == "OWNER" else "READER" if base in RETIRED_ROLES else base if base in TEAM_GRANT_ROLES else None)
        team_role = "WRITER" if "WRITER" in team_roles else "READER" if "READER" in team_roles else None      # a team never gives OWNER
        raw = direct[0] if direct else None
        direct_role = ("READER" if raw in RETIRED_ROLES else raw if (a.get("type") != _KB_TYPE or raw in TEAM_GRANT_ROLES) else None) if direct else None
        own, team_rel = "own" in kinds, "team_rel" in kinds
        if not own and not team_rel and direct_role is None and (not direct or a.get("type") == _KB_TYPE) and team_role is None:
            return None
        if direct_role is not None and team_role is not None and _CTX_RANK.get(team_role, 0) > _CTX_RANK.get(direct_role, 0):
            return team_role
        if direct_role is not None:
            return direct_role
        if team_role is not None:
            return team_role
        admin = u.get("role") == "ADMIN" or u.get("orgRole") == "ADMIN"
        if not own and team_rel:
            return "EDITOR" if admin else "READER"
        if admin:
            return "EDITOR" if (a.get("scope") or "personal") == "team" else "OWNER"
        return "OWNER" if a.get("createdBy") in (u.get("id"), u.get("userId")) and a.get("createdBy") else "READER"

    async def get_knowledge_hub_context_permissions(self, user_key: str, org_id: str, parent_id: str | None, transaction: str | None = None,
                                                    parent_type: str | None = None) -> dict[str, Any]:
        """What the user may do inside a folder, collection or connector, from their role on it. At the root only an administrator may do anything.
        Anything that cannot be read answers the lowest role with no abilities, never more."""
        org = current_org_id()
        graph_type = None
        if parent_id:
            graph_type = ("record" if parent_type in ("folder", "record") else parent_type) if parent_type else None
            if graph_type not in ("record", "recordGroup", "app"):
                raise ValueError(f"Invalid or unsupported parent_type: {parent_type}")
        try:
            if org_id != org:
                return _ctx_perms("READER")

            if parent_id and parent_id not in (await self.check_access(user_key, org_id, node_ids=[parent_id], transaction=transaction)).node_ids:
                return _ctx_perms("READER")    # no abilities on a node the access check refuses (lost connector gate, deleted target), whatever grant remains

            async def go(tx: Tx) -> dict[str, Any]:
                if not parent_id:
                    row = await tx.fetchrow("/* pipeshub:graph.ctx_root_user */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id = $2", org, user_key)
                    if not row:
                        return _ctx_perms("READER")
                    d = _doc(row["doc"])
                    admin = d.get("role") == "ADMIN" or d.get("orgRole") == "ADMIN"
                    return _ctx_perms("ADMIN" if admin else "MEMBER", admin=admin)
                coll = {"record": "records", "recordGroup": "recordGroups", "app": "apps"}[graph_type]
                if graph_type == "app":
                    return _ctx_perms(await self._app_role(tx, org, user_key, parent_id) or "READER")
                got = await self._role_on(tx, org, user_key, coll, parent_id)
                return _ctx_perms(got[0] if got else "READER")
            return await self._q(go, transaction)
        except Exception as e:
            self.logger.error("Get knowledge hub context permissions failed: %s", e)
            return _ctx_perms("READER")

    async def check_record_access_with_details(self, user_id: str, org_id: str, record_id: str, transaction: str | None = None) -> dict | None:
        """The record with how the user reaches it, or None. The batch access check decides whether they may; a placeholder is refused like a record they
        cannot read. A failed read raises, never an answer of None."""
        if org_id != current_org_id():
            return None
        user = await self.get_user_by_user_id(user_id, raise_on_error=True)
        user_key = (user or {}).get("id") or (user or {}).get("_key")
        if not user_key:
            return None
        record = await self.get_document(record_id, "records", transaction, raise_on_error=True)
        if not record:
            return None
        checked = await self.check_access(user_key, org_id, node_ids=[record_id], transaction=transaction)
        if record_id not in checked.node_ids or record_id in checked.placeholder_ids:
            return None
        org = current_org_id()
        type_coll = {RecordTypes.FILE.value: CollectionNames.FILES.value, RecordTypes.MAIL.value: CollectionNames.MAILS.value, RecordTypes.TICKET.value: CollectionNames.TICKETS.value}.get(record.get("recordType"))

        async def go(tx: Tx) -> tuple:
            best = await self._role_on(tx, org, user_key, "records", record_id, _DETAIL_RANK)
            meta = await tx.fetch("""/* pipeshub:graph.record_labels */ SELECT e.coll AS ecoll, m.coll AS mcoll, m.id AS mid, m.doc AS mdoc, e.doc AS edoc FROM graph.edges e
                JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = e.to_coll AND m.id = e.to_id
                WHERE e.org_id = $1 AND e.from_coll = 'records' AND e.from_id = $2 AND e.coll IN ('belongsToDepartment', 'belongsToCategory', 'belongsToTopic', 'belongsToLanguage')
                  AND m.coll IN ('departments', 'categories', 'subcategories1', 'subcategories2', 'subcategories3', 'topics', 'languages')""", org, record_id)
            return best, meta
        best, meta = await self._q(go, transaction)
        extra = await self.get_document(record_id, type_coll, transaction) if type_coll else None
        email, connector_name = (user or {}).get("email"), record.get("connectorName")
        if extra and extra.get("webUrl"):
            extra["webUrl"] = substitute_user_email(extra["webUrl"], email, connector_name)
        labels: dict[str, list] = {k: [] for k in ("departments", "categories", "subcategories1", "subcategories2", "subcategories3", "topics", "languages")}
        for m in meta:
            d, ed = _doc(m["mdoc"]), _doc(m["edoc"])
            item = {"id": m["mid"], "name": d.get("departmentName") if m["mcoll"] == "departments" else d.get("name")}
            if m["mcoll"] != "departments":
                item |= {"extractedName": ed.get("extractedName"), "extractedNames": ed.get("extractedNames"), "canonical": d.get("normalizedName") is not None,
                         "migrated": ed.get("migratedFrom") is not None}
            labels[m["mcoll"]].append(item)
        kb_info = None
        app = await self.get_document(record.get("connectorId"), "apps", transaction) if record.get("connectorId") and connector_name == Connectors.KNOWLEDGE_BASE.value else None
        if app and app.get("type") == _KB_TYPE and await self.get_user_kb_permission(app["id"], user_key, transaction=transaction):
            kb_info = {"id": app["id"], "name": app.get("name"), "orgId": org}
        role, source = best if best else ("READER", "CONNECTOR")      # a connector item carries no role; what the check admitted is read access
        record_out = {**record, "id": record["id"]}
        record_out.pop("_key", None)
        if record_out.get("webUrl"):
            record_out["webUrl"] = substitute_user_email(record_out["webUrl"], email, connector_name)
        rt = record.get("recordType")
        return {"record": {**record_out, "fileRecord": extra if rt == RecordTypes.FILE.value else None, "mailRecord": extra if rt == RecordTypes.MAIL.value else None,
                           "ticketRecord": extra if rt == RecordTypes.TICKET.value else None},
                "knowledgeBase": kb_info, "folder": None, "metadata": own_record_labels(labels),
                "permissions": [{"id": record["id"], "name": record.get("recordName"), "type": rt, "relationship": role, "accessType": source}]}

    async def get_accessible_virtual_record_ids(self, user_id: str, org_id: str, filters: dict[str, list[str]] | None = None, time_range: dict[str, int] | None = None, *,
                                                raise_on_error: bool = False, exclude_app_ids: frozenset[str] = frozenset()) -> dict[str, str]:
        """``{virtualRecordId: recordId}`` of the indexed, live records the user may open, inside the apps they pass the gate for (or the ones the filters
        name), narrowed by the metadata filters and the source time range. The batch access check decides each record, so this agrees with it.
        The candidates are what the user can reach: every record of a collection or an app-level connector (the user may open them all), and for a
        record-level connector what a walk down from the app, from the records and groups granted to the user and from the groups that declare a scope
        reaches (``access.reachable_records``). Records the user cannot reach are never read. The grants are read once."""
        try:
            org = current_org_id()
            if org_id != org:
                return {}
            user = await self.get_user_by_user_id(user_id, raise_on_error=raise_on_error)
            user_key = (user or {}).get("id") or (user or {}).get("_key")
            if not user_key:
                return {}
            filters = filters or {}
            scope = requested_scope_ids(filters)
            if filters.get(STRICT_SCOPE_FILTER_KEY) and scope is None:
                return {}
            meta = [(edge, coll, field, filters[k]) for k, edge, coll, field in _META_FILTERS if filters.get(k)]
            access = await self.get_knowledge_hub_access_v3(user_key, org_id)
            gated = [a for a in access["gated_app_ids"] if a not in exclude_app_ids]
            docs = {r["id"]: _doc(r["doc"]) for r in await self._q(lambda tx: tx.fetch(
                "/* pipeshub:graph.accessible_apps */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = ANY($2::text[])", org, gated), None)}
            chosen = {i: d for i, d in docs.items() if (i in scope if scope is not None else d.get("type") != _KB_TYPE or not d.get("isHidden"))}
            kbs = [i for i, d in chosen.items() if d.get("type") == _KB_TYPE]
            wide = [i for i, d in chosen.items() if d.get("type") != _KB_TYPE and d.get("permissionModel") == "APP_LEVEL"]
            level = {i: d for i, d in chosen.items() if i not in kbs and i not in wide}
            if not chosen:
                return {}
            tr = time_range or {}
            dated_only = bool(tr.get(DATED_ONLY_TIME_RANGE_KEY)) and upload_times_undated()
            undated = upload_times_undated() and not dated_only
            bounds = [tr.get(k) for k in ("source_created_after_ms", "source_created_before_ms", "source_updated_after_ms", "source_updated_before_ms")]

            scope = (_j([{"edge": e, "coll": c, "field": f, "vals": v} for e, c, f, v in meta]), *bounds, undated, dated_only)

            async def candidates(tx: Tx) -> list[Any]:
                """The scope filters are applied where the records are read: every record of a collection or app-level connector in one statement, and the
                records the walk reached, by key, in another."""
                rows = list(await tx.fetch("""/* pipeshub:graph.accessible_candidates_app_wide */ SELECT r.id, r.doc->>'virtualRecordId' AS vrid FROM graph.nodes r
                    WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'orgId' = $1 AND r.doc->>'virtualRecordId' IS NOT NULL
                      AND ((r.doc->>'connectorId' = ANY($2::text[])) OR (r.doc->>'connectorId' = ANY($3::text[]) AND r.doc->>'origin' = 'UPLOAD'))
                      AND (r.doc->>'indexingStatus' = 'COMPLETED' OR r.doc->>'hasIndexedCopy' = 'true') AND r.doc->>'isDeleted' IS DISTINCT FROM 'true'
                      AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset($4::jsonb) f(edge text, coll text, field text, vals jsonb) WHERE NOT EXISTS (
                          SELECT 1 FROM graph.edges e JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = f.coll AND m.id = e.to_id
                          WHERE e.org_id = $1 AND e.coll = f.edge AND e.from_coll = 'records' AND e.from_id = r.id AND m.doc->f.field IN (SELECT jsonb_array_elements(f.vals))))
                      AND ($5::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceCreatedAtTimestamp') = 'number' THEN (r.doc->>'sourceCreatedAtTimestamp')::numeric END) >= $5)
                      AND ($6::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceCreatedAtTimestamp') = 'number' THEN (r.doc->>'sourceCreatedAtTimestamp')::numeric END) <= $6
                           OR ($9 AND coalesce(r.doc->>'sourceTimestampOrigin', '') = 'upload'))
                      AND ($7::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceLastModifiedTimestamp') = 'number' THEN (r.doc->>'sourceLastModifiedTimestamp')::numeric END) >= $7)
                      AND ($8::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceLastModifiedTimestamp') = 'number' THEN (r.doc->>'sourceLastModifiedTimestamp')::numeric END) <= $8
                           OR ($9 AND coalesce(r.doc->>'sourceTimestampOrigin', '') = 'upload'))
                      AND (NOT $10 OR coalesce(r.doc->>'sourceTimestampOrigin', '') <> 'upload')
                    ORDER BY r.id""", org, wide, kbs, *scope)) if wide or kbs else []
                have = {r["id"] for r in rows}
                walked = [i for i in sorted(await access_sql.reachable_records(tx, org, access, sorted(level))) if i not in have] if level else []
                for i in range(0, len(walked), _CHECK_CHUNK * 5):
                    rows += await tx.fetch("""/* pipeshub:graph.accessible_candidates */ SELECT r.id, r.doc->>'virtualRecordId' AS vrid FROM graph.nodes r
                    WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[]) AND r.doc->>'orgId' = $1 AND r.doc->>'virtualRecordId' IS NOT NULL
                      AND r.doc->>'connectorId' = ANY($3::text[])
                      AND (r.doc->>'indexingStatus' = 'COMPLETED' OR r.doc->>'hasIndexedCopy' = 'true') AND r.doc->>'isDeleted' IS DISTINCT FROM 'true'
                      AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset($4::jsonb) f(edge text, coll text, field text, vals jsonb) WHERE NOT EXISTS (
                          SELECT 1 FROM graph.edges e JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = f.coll AND m.id = e.to_id
                          WHERE e.org_id = $1 AND e.coll = f.edge AND e.from_coll = 'records' AND e.from_id = r.id AND m.doc->f.field IN (SELECT jsonb_array_elements(f.vals))))
                      AND ($5::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceCreatedAtTimestamp') = 'number' THEN (r.doc->>'sourceCreatedAtTimestamp')::numeric END) >= $5)
                      AND ($6::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceCreatedAtTimestamp') = 'number' THEN (r.doc->>'sourceCreatedAtTimestamp')::numeric END) <= $6
                           OR ($9 AND coalesce(r.doc->>'sourceTimestampOrigin', '') = 'upload'))
                      AND ($7::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceLastModifiedTimestamp') = 'number' THEN (r.doc->>'sourceLastModifiedTimestamp')::numeric END) >= $7)
                      AND ($8::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceLastModifiedTimestamp') = 'number' THEN (r.doc->>'sourceLastModifiedTimestamp')::numeric END) <= $8
                           OR ($9 AND coalesce(r.doc->>'sourceTimestampOrigin', '') = 'upload'))
                      AND (NOT $10 OR coalesce(r.doc->>'sourceTimestampOrigin', '') <> 'upload')
                    ORDER BY r.id""", org, walked[i:i + _CHECK_CHUNK * 5], sorted(level), *scope)
                return sorted(rows, key=lambda r: r["id"])
            kept = await self._q(candidates, None)
            out: dict[str, str] = {}
            for i in range(0, len(kept), _CHECK_CHUNK):
                chunk = kept[i:i + _CHECK_CHUNK]
                rows = await self._kh_v3_accessible_rows(user_key, org_id, [c["id"] for c in chunk], [], access=access, transaction=None)
                ok = {r["id"] for r in rows if not r.get("isInternal") and not r.get("isPlaceholder")}
                for c in chunk:
                    if c["id"] in ok:
                        out.setdefault(c["vrid"], c["id"])
            return out
        except Exception as e:
            self.logger.error("Get accessible virtual record IDs failed: %s", e)
            if raise_on_error:
                raise
            return {}
