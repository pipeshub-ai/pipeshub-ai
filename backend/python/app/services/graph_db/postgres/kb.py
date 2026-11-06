"""Knowledge bases: who holds a role on one, the lists of them, the checks of its folders and the validation an upload or a folder creation starts with.

A knowledge base is an ``apps`` node of type KB; a record (or folder) belongs to it by a ``belongsTo`` edge, a folder is a record with a folder mime
type, and a folder's children hang below it by ``PARENT_CHILD`` edges. Every method resolves the ambient org first (before any ``try``, so a missing
org is an error and never an answer such as "not found"); every statement is a literal with bound parameters.
"""
from __future__ import annotations

import json
from typing import Any

from app.config.constants.arangodb import FOLDER_MIME_TYPES, Connectors
from app.core.org_context import current_org_id
from app.models.permission import RETIRED_ROLES, read_role
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    is_live_record,
)
from app.services.graph_db.common.team_grant_role import (
    TEAM_GRANT_ROLE_CAP,
    TEAM_GRANT_ROLES,
)
from app.services.graph_db.common.utils import (
    CONTAINMENT_MAX_DEPTH,
    uploaded_document_id,
)
from app.services.graph_db.postgres.base import PgBase, _arango, _doc, _j
from app.services.graph_db.postgres.records import _vis
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import folder_in_trash

_KB = Connectors.KNOWLEDGE_BASE.value
_ROLES = list(TEAM_GRANT_ROLES)
_SORTS = {"name": 1, "createdAtTimestamp": 2, "updatedAtTimestamp": 3, "userRole": 4}


class KbMixin(PgBase):
    async def kb_exists(self, kb_id: str) -> bool:
        """Whether a knowledge base with this id exists, whoever may see it. A failed read raises, so the caller answers 500, not a misleading 404."""
        org = current_org_id()
        return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.kb_exists */ SELECT EXISTS (SELECT 1 FROM graph.nodes k
            WHERE k.org_id = $1 AND k.coll = 'apps' AND k.id = $2 AND k.doc->>'type' = $3)""", org, kb_id, _KB), None))

    async def list_user_knowledge_bases(self, user_id: str, org_id: str, skip: int, limit: int, search: str | None = None,
                                        permissions: list[str] | None = None, sort_by: str = "name", sort_order: str = "asc",
                                        transaction: str | None = None) -> tuple[list[dict], int, dict]:
        """The knowledge bases the user holds a role on, directly or through a team (the highest role wins; the role a team gives is
        ``get_user_kb_permission``'s), with the page, the total after the search and role filters and the roles the user holds. A failed read raises."""
        org = current_org_id()
        key = _SORTS.get(sort_by, 1)
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.list_user_knowledge_bases */ WITH me AS (SELECT 1 FROM graph.nodes u WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $2),
            cand AS (
              SELECT d.to_id AS kb_id, CASE WHEN d.doc->>'role' = ANY($5::text[]) THEN 'READER' ELSE d.doc->>'role' END AS role
              FROM me, graph.edges d WHERE d.org_id = $1 AND d.coll = 'permission' AND d.from_coll = 'users' AND d.from_id = $2 AND d.to_coll = 'apps' AND d.doc->>'type' = 'USER'
              UNION ALL
              SELECT tg.to_id, CASE WHEN b.role = 'OWNER' THEN $4::text WHEN b.role = ANY($5::text[]) THEN 'READER' WHEN b.role = ANY($6::text[]) THEN b.role END
              FROM me, graph.edges ut JOIN graph.nodes t ON t.org_id = ut.org_id AND t.coll = 'teams' AND t.id = ut.to_id
                JOIN graph.edges tg ON tg.org_id = ut.org_id AND tg.coll = 'permission' AND tg.from_coll = 'teams' AND tg.from_id = t.id AND tg.to_coll = 'apps' AND tg.doc->>'type' = 'TEAM',
                LATERAL (SELECT CASE WHEN tg.doc->>'role' IS NOT NULL AND tg.doc->>'role' <> '' THEN tg.doc->>'role' ELSE ut.doc->>'role' END AS role) b
              WHERE ut.org_id = $1 AND ut.coll = 'permission' AND ut.from_coll = 'users' AND ut.from_id = $2 AND ut.to_coll = 'teams' AND ut.doc->>'type' = 'USER'),
            base AS (
              SELECT k.id, k.doc, best.role FROM (SELECT c.kb_id, (array_agg(c.role ORDER BY CASE c.role WHEN 'OWNER' THEN 3 WHEN 'WRITER' THEN 2 ELSE 1 END DESC))[1] AS role
                  FROM cand c WHERE c.role = ANY($6::text[]) GROUP BY c.kb_id) best
                JOIN graph.nodes k ON k.org_id = $1 AND k.coll = 'apps' AND k.id = best.kb_id
              WHERE k.doc->>'orgId' = $3 AND k.doc->>'type' = $7 AND coalesce(k.doc->>'isHidden', '') <> 'true'),
            kept AS (SELECT * FROM base b WHERE ($8::text IS NULL OR position(lower($8::text) IN lower(b.doc->>'name')) > 0) AND ($9::text[] IS NULL OR b.role = ANY($9::text[])))
            SELECT s.total, s.roles, p.doc, p.role, p.folders FROM
              (SELECT (SELECT count(*) FROM kept) AS total, (SELECT coalesce(array_agg(DISTINCT role), '{}') FROM base) AS roles) s
              LEFT JOIN LATERAL (SELECT f.id, f.doc, f.role, (SELECT coalesce(jsonb_agg(jsonb_build_object('id', r.id, 'name', r.doc->'recordName',
                    'createdAtTimestamp', r.doc->'createdAtTimestamp', 'webUrl', r.doc->'webUrl') ORDER BY r.id), '[]'::jsonb)
                  FROM (SELECT DISTINCT n.id, n.doc FROM graph.edges be JOIN graph.nodes n ON n.org_id = be.org_id AND n.coll = 'records' AND n.id = be.from_id
                        WHERE be.org_id = $1 AND be.coll = 'belongsTo' AND be.from_coll = 'records' AND be.to_coll = 'apps' AND be.to_id = f.id
                          AND n.doc->>'mimeType' = ANY($14::text[])) r) AS folders
                FROM kept f
                ORDER BY (CASE WHEN $10::int = 1 AND $13::boolean THEN f.doc->>'name' END) COLLATE "C" ASC, (CASE WHEN $10::int = 1 AND NOT $13::boolean THEN f.doc->>'name' END) COLLATE "C" DESC,
                         (CASE WHEN $10::int = 2 AND $13::boolean THEN f.doc->'createdAtTimestamp' END) ASC, (CASE WHEN $10::int = 2 AND NOT $13::boolean THEN f.doc->'createdAtTimestamp' END) DESC,
                         (CASE WHEN $10::int = 3 AND $13::boolean THEN f.doc->'updatedAtTimestamp' END) ASC, (CASE WHEN $10::int = 3 AND NOT $13::boolean THEN f.doc->'updatedAtTimestamp' END) DESC,
                         (CASE WHEN $10::int = 4 AND $13::boolean THEN f.role END) COLLATE "C" ASC, (CASE WHEN $10::int = 4 AND NOT $13::boolean THEN f.role END) COLLATE "C" DESC, f.id
                OFFSET $11 LIMIT $12) p ON true""",
                                                  org, user_id, org_id, TEAM_GRANT_ROLE_CAP, sorted(RETIRED_ROLES), _ROLES, _KB, search or None, permissions or None,
                                                  key, skip, limit, sort_order.upper() != "DESC", FOLDER_MIME_TYPES), transaction)
        kbs = []
        for r in rows:
            if r["doc"] is None:
                continue
            d = _doc(r["doc"])
            kbs.append({"id": d.get("id"), "name": d.get("name") if d.get("name") is not None else "Untitled", "description": d.get("description"),
                        "createdAtTimestamp": d.get("createdAtTimestamp"), "updatedAtTimestamp": d.get("updatedAtTimestamp"), "createdBy": d.get("createdBy"),
                        "userRole": r["role"], "status": d.get("status"), "folders": _doc_list(r["folders"])})
        return kbs, rows[0]["total"], {"permissions": sorted(rows[0]["roles"]), "sortFields": list(_SORTS), "sortOrders": ["asc", "desc"]}

    # ---- who holds a role on a knowledge base --------------------------------------------------------------------
    async def create_kb_permissions(self, kb_id: str, requester_id: str, user_ids: list[str], team_ids: list[str], role: str,
                                    transaction: str | None = None) -> dict:
        """Grant the users ``role`` and the teams access (a team's grant has no role); all or none."""
        org = current_org_id()
        try:
            now = get_epoch_timestamp_in_ms()
            # Only grants whose collection, user or team exist in this org are written (as Neo4j's MATCH of both ends): an id that does not exist yet
            # must not turn into access the day a node with that id appears.
            found = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.create_kb_permissions_ends */ SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1
                AND ((n.coll = 'apps' AND n.id = $2 AND n.doc->>'type' = $5) OR (n.coll = 'users' AND n.id = ANY($3::text[])) OR (n.coll = 'teams' AND n.id = ANY($4::text[])))""",
                                                      org, kb_id, list(user_ids), list(team_ids), _KB), transaction)
            have = {(r["coll"], r["id"]) for r in found}
            users = [u for u in user_ids if ("users", u) in have] if ("apps", kb_id) in have else []
            teams = [t for t in team_ids if ("teams", t) in have] if ("apps", kb_id) in have else []
            edges = [{"from_id": u, "from_collection": "users", "to_id": kb_id, "to_collection": "apps", "type": "USER", "role": role,
                      "createdAtTimestamp": now, "updatedAtTimestamp": now} for u in users]
            edges += [{"from_id": t, "from_collection": "teams", "to_id": kb_id, "to_collection": "apps", "type": "TEAM",
                       "createdAtTimestamp": now, "updatedAtTimestamp": now} for t in teams]
            await self.batch_create_edges(edges, "permission", transaction=transaction)
            return {"success": True, "grantedCount": len(edges), "grantedUsers": users, "grantedTeams": teams, "role": role, "kbId": kb_id, "details": {}}
        except Exception as e:
            self.logger.error(f"❌ Create KB permissions failed: {e}")
            return {"success": False, "reason": str(e)}

    async def update_kb_permission(self, kb_id: str, requester_id: str, user_ids: list[str], team_ids: list[str], new_role: str,
                                   transaction: str | None = None) -> dict:
        """Set the role of the users who already hold one (the others are skipped); a team's grant has no role, so teams are not touched.
        The requester is not re-checked here: the service has, and counts an OWNER role held through a team."""
        org = current_org_id()
        if not user_ids and not team_ids:
            return {"success": False, "reason": "No users or teams provided", "code": "400"}
        if new_role not in _ROLES:
            return {"success": False, "reason": f"Invalid role. Must be one of: {', '.join(_ROLES)}", "code": "400"}
        try:
            now = get_epoch_timestamp_in_ms()
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.update_kb_permission */ UPDATE graph.edges e SET doc = e.doc || $5::jsonb
                FROM graph.edges o, graph.nodes k, graph.nodes u
                WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = ANY($2::text[]) AND e.to_coll = 'apps' AND e.to_id = $3 AND e.doc->>'type' = 'USER'
                  AND k.org_id = $1 AND k.coll = 'apps' AND k.id = e.to_id AND k.doc->>'type' = $4
                  AND u.org_id = $1 AND u.coll = 'users' AND u.id = e.from_id
                  AND o.org_id = e.org_id AND o.coll = e.coll AND o.from_coll = e.from_coll AND o.from_id = e.from_id AND o.to_coll = e.to_coll AND o.to_id = e.to_id AND o.ekey = e.ekey
                RETURNING e.from_id, o.doc->>'role' AS old_role""",
                                                    org, list(dict.fromkeys(user_ids)), kb_id, _KB,
                                                    _j({"role": new_role, "updatedAtTimestamp": now, "lastUpdatedTimestampAtSource": now})), transaction, write=True)
            return {"success": True, "kb_id": kb_id, "new_role": new_role, "updated_permissions": len(rows), "updated_users": len(rows), "updated_teams": 0,
                    "updates_detail": {"users": {r["from_id"]: {"old_role": r["old_role"], "new_role": new_role} for r in rows}, "teams": {}}}
        except Exception as e:
            self.logger.error(f"❌ Failed to update KB permission: {e}")
            return {"success": False, "reason": str(e), "code": "500"}

    async def remove_kb_permission(self, kb_id: str, user_ids: list[str], team_ids: list[str], transaction: str | None = None) -> dict:
        org = current_org_id()
        try:
            await self._q(lambda tx: tx.execute("""/* pipeshub:graph.remove_kb_permission */ DELETE FROM graph.edges e USING graph.nodes k
                WHERE e.org_id = $1 AND e.coll = 'permission' AND e.to_coll = 'apps' AND e.to_id = $2 AND k.org_id = $1 AND k.coll = 'apps' AND k.id = e.to_id AND k.doc->>'type' = $5
                  AND ((e.from_coll = 'users' AND e.from_id = ANY($3::text[]) AND e.doc->>'type' = 'USER') OR (e.from_coll = 'teams' AND e.from_id = ANY($4::text[]) AND e.doc->>'type' = 'TEAM'))""",
                                                    org, kb_id, list(user_ids), list(team_ids), _KB), transaction, write=True)
            return {"success": True}
        except Exception as e:
            self.logger.error(f"❌ Remove KB permission failed: {e}")
            return {"success": False, "reason": str(e)}

    async def list_kb_permissions(self, kb_id: str, transaction: str | None = None) -> list[dict]:
        """Everyone with a grant on the knowledge base. A deleted user keeps their edges as an inactive node and is not listed. A failed read answers []."""
        org = current_org_id()
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.list_kb_permissions */ SELECT e.from_coll, n.doc AS entity, e.doc AS grant_doc FROM graph.edges e
                JOIN graph.nodes k ON k.org_id = e.org_id AND k.coll = 'apps' AND k.id = e.to_id AND k.doc->>'type' = $3
                JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = e.from_coll AND n.id = e.from_id
                WHERE e.org_id = $1 AND e.coll = 'permission' AND e.to_coll = 'apps' AND e.to_id = $2 AND (e.from_coll <> 'users' OR n.doc->>'isActive' = 'true')
                ORDER BY e.from_coll, e.from_id, e.ekey""", org, kb_id, _KB), transaction)
        except Exception as e:
            self.logger.error(f"❌ List KB permissions failed: {e}")
            return []
        out = []
        for r in rows:
            ent, g = _doc(r["entity"]), _doc(r["grant_doc"])
            user = r["from_coll"] == "users"
            out.append({"id": ent.get("id"), "type": g.get("type", "USER" if user else "TEAM"), "createdAtTimestamp": g.get("createdAtTimestamp", 0),
                        "updatedAtTimestamp": g.get("updatedAtTimestamp", 0), "userId": ent.get("userId") if user else None, "email": ent.get("email") if user else None,
                        "name": (ent.get("fullName") or ent.get("name") or ent.get("userName")) if user else ent.get("name"),
                        "role": read_role(g.get("role")) if user else None})
        return out

    async def get_kb_permissions(self, kb_id: str, user_ids: list[str] | None = None, team_ids: list[str] | None = None,
                                 transaction: str | None = None) -> dict[str, dict[str, str]]:
        """The stored role of each named user and the presence of each named team on the knowledge base: ``{users: {id: role}, teams: {id: None}}``."""
        org = current_org_id()
        result: dict[str, dict] = {"users": {}, "teams": {}}
        if not user_ids and not team_ids:
            return result
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_kb_permissions */ SELECT e.from_coll, e.from_id, e.doc->>'role' AS role FROM graph.edges e
            JOIN graph.nodes k ON k.org_id = e.org_id AND k.coll = 'apps' AND k.id = e.to_id AND k.doc->>'type' = $5
            JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = e.from_coll AND n.id = e.from_id
            WHERE e.org_id = $1 AND e.coll = 'permission' AND e.to_coll = 'apps' AND e.to_id = $2
              AND ((e.from_coll = 'users' AND e.from_id = ANY($3::text[])) OR (e.from_coll = 'teams' AND e.from_id = ANY($4::text[]))) ORDER BY e.ekey""",
                                              org, kb_id, list(user_ids or []), list(team_ids or []), _KB), transaction)
        for r in rows:
            if r["from_coll"] == "users":
                result["users"][r["from_id"]] = r["role"] or ""
            else:
                result["teams"][r["from_id"]] = None
        return result

    async def count_kb_owners(self, kb_id: str, transaction: str | None = None) -> int:
        """The users with an OWNER grant (a team's never counts). A failed read answers 0, so a caller that needs one owner to remain refuses."""
        org = current_org_id()
        try:
            return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.count_kb_owners */ SELECT count(DISTINCT e.from_id) FROM graph.edges e
                JOIN graph.nodes k ON k.org_id = e.org_id AND k.coll = 'apps' AND k.id = e.to_id AND k.doc->>'type' = $3
                JOIN graph.nodes u ON u.org_id = e.org_id AND u.coll = 'users' AND u.id = e.from_id
                WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.to_coll = 'apps' AND e.to_id = $2 AND e.doc->>'role' = 'OWNER'""",
                                                      org, kb_id, _KB), transaction)
        except Exception as e:
            self.logger.error(f"❌ Count KB owners failed: {e}")
            return 0

    # ---- folders of a knowledge base -----------------------------------------------------------------------------
    async def validate_folder_exists_in_kb(self, kb_id: str, folder_id: str, transaction: str | None = None) -> bool:
        """The folder is in the knowledge base, in the trash or not."""
        return await self.validate_folder_in_kb(kb_id, folder_id, transaction, visibility=RecordVisibility.ALL)

    async def validate_folder_in_kb(self, kb_id: str, folder_id: str, transaction: str | None = None, *,
                                    visibility: RecordVisibility = RecordVisibility.LIVE) -> bool:
        """The folder is in the knowledge base and matches ``visibility``. A failed read answers False."""
        org = current_org_id()
        try:
            return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.validate_folder_in_kb */ SELECT EXISTS (SELECT 1 FROM graph.nodes f
                JOIN graph.edges b ON b.org_id = f.org_id AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = f.id AND b.to_coll = 'apps' AND b.to_id = $3
                JOIN graph.nodes k ON k.org_id = f.org_id AND k.coll = 'apps' AND k.id = b.to_id AND k.doc->>'type' = $5
                WHERE f.org_id = $1 AND f.coll = 'records' AND f.id = $2 AND f.doc->>'mimeType' = ANY($4::text[])
                  AND ($6::int = 0 OR coalesce((f.doc->>'isDeleted')::boolean, false) = ($6::int = 2)))""",
                                                      org, folder_id, kb_id, FOLDER_MIME_TYPES, _KB, _vis(visibility)), transaction))
        except Exception as e:
            self.logger.error(f"❌ Failed to validate folder exists: {e}")
            return False

    async def get_and_validate_folder_in_kb(self, kb_id: str, folder_id: str, transaction: str | None = None) -> dict | None:
        """The folder record (with ``name`` and ``recordGroupId``) when it belongs to the knowledge base, else None; trashed or not."""
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_and_validate_folder_in_kb */ SELECT f.doc FROM graph.nodes f
                JOIN graph.edges b ON b.org_id = f.org_id AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = f.id AND b.to_coll = 'apps' AND b.to_id = $3
                JOIN graph.nodes k ON k.org_id = f.org_id AND k.coll = 'apps' AND k.id = b.to_id AND k.doc->>'type' = $5
                WHERE f.org_id = $1 AND f.coll = 'records' AND f.id = $2 AND f.doc->>'mimeType' = ANY($4::text[]) LIMIT 1""",
                                org, folder_id, kb_id, FOLDER_MIME_TYPES, _KB, transaction=transaction)
        except Exception as e:
            self.logger.error(f"❌ Failed to get and validate folder in KB: {e}")
            return None
        return {**_arango(d, "records"), "name": d.get("recordName"), "recordGroupId": d.get("connectorId")} if d else None

    async def find_folder_by_name_in_parent(self, kb_id: str, folder_name: str, parent_folder_id: str | None = None, exclude_folder_id: str | None = None,
                                            transaction: str | None = None, *, raise_on_error: bool = False) -> dict | None:
        """A live folder of that name (case-insensitive) directly under the parent folder, or at the root of the knowledge base (a folder
        with no parent record)."""
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.find_folder_by_name_in_parent */ SELECT f.doc FROM graph.nodes f
                WHERE f.org_id = $1 AND f.coll = 'records' AND f.doc->>'mimeType' = ANY($2::text[]) AND lower(f.doc->>'recordName') = lower($3::text)
                  AND coalesce((f.doc->>'isDeleted')::boolean, false) = false AND ($4::text IS NULL OR f.id <> $4)
                  AND CASE WHEN $5::text IS NULL
                    THEN EXISTS (SELECT 1 FROM graph.edges b JOIN graph.nodes k ON k.org_id = b.org_id AND k.coll = 'apps' AND k.id = b.to_id AND k.doc->>'type' = $6
                          WHERE b.org_id = f.org_id AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = f.id AND b.to_coll = 'apps' AND b.to_id = $7)
                      AND NOT EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = f.org_id AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD'
                          AND p.from_coll = 'records' AND p.to_coll = 'records' AND p.to_id = f.id)
                    ELSE EXISTS (SELECT 1 FROM graph.edges p JOIN graph.nodes pn ON pn.org_id = p.org_id AND pn.coll = 'records' AND pn.id = p.from_id
                          WHERE p.org_id = f.org_id AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD' AND p.from_coll = 'records' AND p.from_id = $5
                            AND p.to_coll = 'records' AND p.to_id = f.id) END
                ORDER BY f.id LIMIT 1""", org, FOLDER_MIME_TYPES, folder_name, exclude_folder_id, parent_folder_id, _KB, kb_id, transaction=transaction)
        except Exception as e:
            self.logger.error(f"❌ Failed to find folder by name: {e}")
            if raise_on_error:
                raise
            return None
        return _arango(d, "records") if d else None

    async def get_folder_depth(self, folder_id: str, transaction: str | None = None) -> int:
        """1 for a folder with no parent, else one more than the longest chain of parents above it."""
        org = current_org_id()
        depth = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.get_folder_depth */ WITH RECURSIVE up(id, depth, seen) AS (
              SELECT n.id, 0, ARRAY[n.id] FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = $2
              UNION ALL
              SELECT e.from_id, up.depth + 1, up.seen || e.from_id FROM up JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations'
                AND e.doc->>'relationshipType' = 'PARENT_CHILD' AND e.from_coll = 'records' AND e.to_coll = 'records' AND e.to_id = up.id
              WHERE NOT e.from_id = ANY(up.seen) AND up.depth < $3)
            SELECT coalesce(max(depth), 0) + 1 FROM up""", org, folder_id, CONTAINMENT_MAX_DEPTH), transaction)
        return int(depth)

    async def get_folder_subtree_height(self, folder_id: str, folder_mime_types: list[str], transaction: str | None = None) -> int:
        """How many levels of folders lie below the folder (0 for none); the records between two folders count as levels."""
        org = current_org_id()
        height = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.get_folder_subtree_height */ WITH RECURSIVE down(id, depth, seen) AS (
              SELECT n.id, 0, ARRAY[n.id] FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = $2
              UNION ALL
              SELECT e.to_id, down.depth + 1, down.seen || e.to_id FROM down JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations'
                AND e.doc->>'relationshipType' = 'PARENT_CHILD' AND e.from_coll = 'records' AND e.from_id = down.id AND e.to_coll = 'records'
              WHERE NOT e.to_id = ANY(down.seen) AND down.depth < $4)
            SELECT coalesce(max(d.depth), 0) FROM down d JOIN graph.nodes s ON s.org_id = $1 AND s.coll = 'records' AND s.id = d.id
            WHERE d.depth >= 1 AND s.doc->>'mimeType' = ANY($3::text[])""", org, folder_id, list(folder_mime_types), CONTAINMENT_MAX_DEPTH), transaction)
        return int(height)

    async def is_record_folder(self, record_id: str, transaction: str | None = None) -> bool:
        """The record's type document says it is not a file. A failed read answers False."""
        org = current_org_id()
        try:
            return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.is_record_folder */ SELECT EXISTS (SELECT 1 FROM graph.nodes r
                JOIN graph.edges t ON t.org_id = r.org_id AND t.coll = 'isOfType' AND t.from_coll = 'records' AND t.from_id = r.id AND t.to_coll = 'files'
                JOIN graph.nodes f ON f.org_id = r.org_id AND f.coll = 'files' AND f.id = t.to_id AND f.doc->>'isFile' = 'false'
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2)""", org, record_id), transaction))
        except Exception as e:
            self.logger.error(f"❌ Is record folder check failed: {e}")
            return False

    # ---- uploads ---------------------------------------------------------------------------------------------------
    async def get_uploaded_document_ids(self, connector_id: str, transaction: str | None = None, *, under_record_ids: list[str] | None = None,
                                        among: list[str] | None = None) -> list[str]:
        """The storage documents of a connector's uploaded files: all of them, or those at or below the given records (reached through
        ``PARENT_CHILD`` and ``ATTACHMENT`` edges within the connector), optionally only those named in ``among``."""
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_uploaded_document_ids */ WITH RECURSIVE w(id) AS (
              SELECT r.id FROM graph.nodes r WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'connectorId' = $2 AND $3::text[] IS NOT NULL AND r.id = ANY($3::text[])
              UNION
              SELECT n.id FROM w JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT')
                AND e.from_coll = 'records' AND e.from_id = w.id AND e.to_coll = 'records'
                JOIN graph.nodes n ON n.org_id = $1 AND n.coll = 'records' AND n.id = e.to_id AND n.doc->>'connectorId' = $2)
            SELECT DISTINCT r.doc->>'origin' AS origin, r.doc->>'externalRecordId' AS ext,
                   (SELECT t.doc->>'isFile' FROM graph.edges te JOIN graph.nodes t ON t.org_id = te.org_id AND t.coll = te.to_coll AND t.id = te.to_id
                    WHERE te.org_id = r.org_id AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = r.id ORDER BY te.to_coll, te.to_id LIMIT 1) AS is_file
            FROM graph.nodes r WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'connectorId' = $2 AND r.doc->>'origin' = $5
              AND ($3::text[] IS NULL OR r.id IN (SELECT id FROM w)) AND ($4::text[] IS NULL OR r.doc->>'externalRecordId' = ANY($4::text[]))""",
                                              org, connector_id, under_record_ids, among, "UPLOAD"), transaction)
        ids = (uploaded_document_id({"origin": r["origin"], "externalRecordId": r["ext"]}, {"isFile": None if r["is_file"] is None else r["is_file"] == "true"}) for r in rows)
        return list(dict.fromkeys(i for i in ids if i))

    async def _fetch_kb_name(self, kb_id: str) -> str | None:
        """The display name for an error message; None when it cannot be read."""
        org = current_org_id()
        try:
            return await self._q(lambda tx: tx.fetchval("/* pipeshub:graph.fetch_kb_name */ SELECT k.doc->>'name' FROM graph.nodes k WHERE k.org_id = $1 AND k.coll = 'apps' AND k.id = $2 AND k.doc->>'type' = $3",
                                                      org, kb_id, _KB), None)
        except Exception:
            return None

    async def _fetch_record_name(self, record_id: str) -> str | None:
        org = current_org_id()
        try:
            return await self._q(lambda tx: tx.fetchval("/* pipeshub:graph.fetch_record_name */ SELECT r.doc->>'recordName' FROM graph.nodes r WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2",
                                                      org, record_id), None)
        except Exception:
            return None

    async def _kb_writer(self, kb_id: str, user_id: str) -> dict:
        """The user and their role when they may write to the knowledge base, else the refusal. A user with no role at all reads as 404 (existence is hidden)."""
        user = await self.get_user_by_user_id(user_id=user_id)
        if not user:
            return {"valid": False, "success": False, "code": 404, "reason": f"User not found: {user_id}"}
        user_key = user.get("id") or user.get("_key")
        if not user_key:
            self.logger.error(f"❌ User record for {user_id} has no 'id' or '_key' field — keys present: {list(user.keys())}")
            return {"valid": False, "success": False, "code": 500, "reason": "Internal error: user record is malformed"}
        if not await self.kb_exists(kb_id):
            return {"valid": False, "success": False, "code": 404, "reason": f"Knowledge base {kb_id} not found"}
        role = await self.get_user_kb_permission(kb_id, user_key)
        if role is None:
            return {"valid": False, "success": False, "code": 404, "reason": f"Knowledge base {kb_id} not found"}
        if role not in ("OWNER", "WRITER"):
            name = await self._fetch_kb_name(kb_id)
            label = f"'{name}' ({kb_id})" if name else kb_id
            return {"valid": False, "success": False, "code": 403,
                    "reason": f"Insufficient permissions on knowledge base {label}. OWNER or WRITER role required, but your role is: {role}."}
        return {"valid": True, "user": user, "user_key": user_key, "user_role": role}

    async def _validate_folder_creation(self, kb_id: str, user_id: str) -> dict:
        current_org_id()
        try:
            return await self._kb_writer(kb_id, user_id)
        except Exception as e:
            self.logger.error(f"❌ Unexpected error in folder creation validation for KB {kb_id}: {e}")
            return {"valid": False, "success": False, "code": 500, "reason": str(e)}

    async def _validate_upload_context(self, kb_id: str, user_id: str, org_id: str, parent_folder_id: str | None = None) -> dict:
        """One validation for every upload: the user may write to the knowledge base and the target folder, if any, is a live folder of it."""
        current_org_id()
        try:
            ok = await self._kb_writer(kb_id, user_id)
            if not ok["valid"]:
                return ok
            if not parent_folder_id:
                return {**ok, "upload_target": "kb_root"}
            parent = await self.get_and_validate_folder_in_kb(kb_id, parent_folder_id)
            if not parent:
                kb_name, folder_name = await self._fetch_kb_name(kb_id), await self._fetch_record_name(parent_folder_id)
                kb_label = f"'{kb_name}' ({kb_id})" if kb_name else kb_id
                folder_label = f"'{folder_name}' ({parent_folder_id})" if folder_name else parent_folder_id
                return {"valid": False, "success": False, "code": 404, "reason": f"Folder {folder_label} was not found in knowledge base {kb_label}. "
                        "The folder may not exist or may belong to a different knowledge base."}
            if not is_live_record(parent):
                return {"valid": False, "success": False, "code": 409, "reason": folder_in_trash(parent.get("recordName"), "upload files to it")}
            return {**ok, "upload_target": "folder", "parent_folder": parent}
        except Exception as e:
            self.logger.error(f"❌ Upload validation failed: {e}")
            return {"valid": False, "success": False, "code": 500, "reason": str(e)}

    async def validate_folder_for_upload(self, kb_id: str, folder_id: str, user_id: str, org_id: str) -> dict:
        """Folder membership and the user's write access, before an upload."""
        return await self._validate_upload_context(kb_id=kb_id, user_id=user_id, org_id=org_id, parent_folder_id=folder_id)


def _doc_list(v: Any) -> list:  # noqa: ANN401
    return json.loads(v) if isinstance(v, str) else list(v or [])
