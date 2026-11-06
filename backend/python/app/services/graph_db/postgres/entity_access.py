"""Knowledge-graph entity search on PostgreSQL: the records of an entity, the ones a user holds a role on, what a user can reach, and the entity index.

The answers are those of ``Neo4jProvider``. Every statement binds the ambient org and the org argument matches the documents' ``orgId``. A failed read
raises: nothing here answers "empty" or "all" for "failed". A list of ids or connectors that is empty matches nothing, and an empty ``within`` admits nothing.
``get_permitted_entity_records`` is not the access decision (it reads neither the access rule nor the connector gate): its callers decide each row with ``check_access``.
Every statement is a literal with bound parameters.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors, PermissionModel
from app.core.org_context import current_org_id
from app.models.entities import EntityType
from app.models.permission import RETIRED_ROLES
from app.models.source_dates import SOURCE_TIMESTAMP_ORIGIN_FIELD, upload_times_undated
from app.services.graph_db.common.record_visibility import HAS_INDEXED_COPY
from app.services.graph_db.common.team_grant_role import TEAM_GRANT_ROLES
from app.services.graph_db.common.utils import (
    CONTAINER_INHERIT_MAX_DEPTH,
    ENTITY_CANDIDATE_SCAN_CAP,
    PERSON_ENTITY_TYPE,
    EntityCandidateRows,
    PermittedEntityRows,
    person_query_ref,
    within_param,
)
from app.services.graph_db.entity_index_queries import (
    APP_STATUS_DELETING,
    ENTITY_INDEX_CANDIDATE_COLLECTIONS,
    ENTITY_INDEX_STATE_FIELD,
    ENTITY_INDEX_SWEPT_AT_FIELD,
    entity_index_source,
)
from app.services.graph_db.postgres.base import PgBase, _arango, _doc, _j

if TYPE_CHECKING:
    from app.core.db import Tx

_C = CollectionNames
# entity type -> (edge collection from the record, the collections of the entity, how its org is matched)
_TARGETS: dict[str, tuple[str, tuple[str, ...], str]] = {
    EntityType.DEPARTMENT.value: (_C.BELONGS_TO_DEPARTMENT.value, (_C.DEPARTMENTS.value,), "none"),
    EntityType.CATEGORY.value: (_C.BELONGS_TO_CATEGORY.value, (_C.CATEGORIES.value,), "none"),
    EntityType.SUBCATEGORY.value: (_C.BELONGS_TO_CATEGORY.value, (_C.SUBCATEGORIES1.value, _C.SUBCATEGORIES2.value, _C.SUBCATEGORIES3.value), "none"),
    EntityType.TOPIC.value: (_C.BELONGS_TO_TOPIC.value, (_C.TOPICS.value,), "none"),
    EntityType.LANGUAGE.value: (_C.BELONGS_TO_LANGUAGE.value, (_C.LANGUAGES.value,), "none"),
    EntityType.NAMED_ENTITY.value: (_C.MENTIONS_ENTITY.value, (_C.NAMED_ENTITIES.value,), "none"),
    EntityType.RECORD_GROUP.value: (_C.BELONGS_TO.value, (_C.RECORD_GROUPS.value,), "org"),
    EntityType.ORGANIZATION.value: (_C.ENTITY_RELATIONS.value, (_C.ORGS.value,), "external"),
}
_FIELDS = ("recordName", "recordType", "connectorId", "virtualRecordId", "webUrl", "hideWeburl", "sourceLastModifiedTimestamp", "updatedAtTimestamp", "connectorName",
           "origin", "createdAtTimestamp", "sourceCreatedAtTimestamp", SOURCE_TIMESTAMP_ORIGIN_FIELD, "indexingStatus", HAS_INDEXED_COPY, "mimeType")
_KB = Connectors.KNOWLEDGE_BASE.value
_RETIRED = sorted(RETIRED_ROLES)


def _row(rid: str, doc: dict) -> dict:
    return {"_key": rid, **{f: doc.get(f) for f in _FIELDS}}


def _refs_by_type(refs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Supported refs grouped by type, the first of a repeated one kept."""
    out: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        rid, typ = ref.get("id"), ref.get("type")
        if not rid or (typ not in (EntityType.RECORD.value, PERSON_ENTITY_TYPE) and typ not in _TARGETS) or (typ, str(rid)) in seen:
            continue
        seen.add((typ, str(rid)))
        out.setdefault(typ, []).append({"id": str(rid), "connectorIds": [str(c) for c in ref.get("connectorIds") or []],
                                        **(person_query_ref(ref) if typ == PERSON_ENTITY_TYPE else {})})
    return out


class EntityAccessMixin(PgBase):
    # ---- the records of an entity ------------------------------------------------------------------------------
    async def _scan(self, tx: Tx, org: str, org_id: str, typ: str, ref: dict, record_types: list[str] | None, within: list | None, offset: int, limit: int) -> tuple[list, bool]:
        """One page of the live records of the org that ``ref`` links to, newest first, from at most ``ENTITY_CANDIDATE_SCAN_CAP`` found; whether the cap was reached.
        A record found by its own name is shown whether or not its content is indexed, the others only when it is."""
        args = (org, org_id, ref["connectorIds"], list(record_types) if record_types else None, _j(within) if within is not None else None,
                upload_times_undated(), offset, limit, ENTITY_CANDIDATE_SCAN_CAP)
        if typ == PERSON_ENTITY_TYPE:
            rows = await tx.fetch("""/* pipeshub:graph.get_entity_candidate_records_person */ WITH s AS (SELECT r.id, r.doc FROM graph.nodes r
                  WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'orgId' = $2 AND coalesce((r.doc->>'isDeleted')::boolean, false) = false
                    AND (r.doc->>'indexingStatus' = 'COMPLETED' OR coalesce((r.doc->>'hasIndexedCopy')::boolean, false))
                    AND r.doc->>'connectorId' = ANY($3::text[]) AND ($4::text[] IS NULL OR r.doc->>'recordType' = ANY($4::text[]))
                    AND ($5::jsonb IS NULL OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements($5::jsonb) s WHERE NOT coalesce(
                      r.doc->>'connectorId' IN (SELECT jsonb_array_elements_text(s->'apps')) OR r.id IN (SELECT jsonb_array_elements_text(s->'records'))
                      OR EXISTS (SELECT 1 FROM graph.edges b WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = r.id AND b.to_coll = 'recordGroups'
                                   AND b.to_id IN (SELECT jsonb_array_elements_text(s->'groups'))), false)))
                    AND r.id IN (SELECT e.from_id FROM graph.edges e JOIN graph.nodes p ON p.org_id = e.org_id AND p.coll = 'users' AND p.id = e.to_id
                                   WHERE e.org_id = $1 AND e.coll = 'entityRelations' AND e.from_coll = 'records' AND e.to_coll = 'users' AND e.to_id = ANY($10::text[])
                                     AND p.doc->>'orgId' = $2 AND e.doc->>'edgeType' = ANY($11::text[])
                                 UNION SELECT e.to_id FROM graph.edges e JOIN graph.nodes p ON p.org_id = e.org_id AND p.coll = 'users' AND p.id = e.from_id
                                   WHERE e.org_id = $1 AND e.coll = 'entityRelations' AND e.from_coll = 'users' AND e.to_coll = 'records' AND e.from_id = ANY($10::text[])
                                     AND p.doc->>'orgId' = $2 AND e.doc->>'edgeType' = ANY($12::text[]))
                  ORDER BY r.id LIMIT $9),
                o AS (SELECT s.id, s.doc, row_number() OVER (ORDER BY CASE WHEN $6::boolean AND s.doc->>'sourceTimestampOrigin' = 'upload' THEN 0::float8
                    ELSE coalesce(CASE WHEN jsonb_typeof(s.doc->'sourceLastModifiedTimestamp') = 'number' THEN (s.doc->>'sourceLastModifiedTimestamp')::float8 END,
                                  CASE WHEN jsonb_typeof(s.doc->'updatedAtTimestamp') = 'number' THEN (s.doc->>'updatedAtTimestamp')::float8 END, 0) END DESC, s.id) AS ord FROM s)
                SELECT o.id, o.doc, o.ord, (SELECT count(*) FROM s) AS scanned FROM o WHERE o.ord > $7::int AND o.ord <= $7::int + $8::int
                UNION ALL SELECT NULL, NULL, 0, (SELECT count(*) FROM s) WHERE NOT EXISTS (SELECT 1 FROM o WHERE o.ord > $7::int AND o.ord <= $7::int + $8::int) ORDER BY ord""",
                                  *args, ref["userIds"], ref["outTypes"], ref["inTypes"])
        else:
            edge, colls, scope = ("", (), "none") if typ == EntityType.RECORD.value else _TARGETS[typ]
            rows = await tx.fetch("""/* pipeshub:graph.get_entity_candidate_records */ WITH s AS (SELECT r.id, r.doc FROM graph.nodes r
                  WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'orgId' = $2 AND coalesce((r.doc->>'isDeleted')::boolean, false) = false
                    AND ($11::text = '' OR r.doc->>'indexingStatus' = 'COMPLETED' OR coalesce((r.doc->>'hasIndexedCopy')::boolean, false))
                    AND r.doc->>'connectorId' = ANY($3::text[]) AND ($4::text[] IS NULL OR r.doc->>'recordType' = ANY($4::text[]))
                    AND ($5::jsonb IS NULL OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements($5::jsonb) s WHERE NOT coalesce(
                      r.doc->>'connectorId' IN (SELECT jsonb_array_elements_text(s->'apps')) OR r.id IN (SELECT jsonb_array_elements_text(s->'records'))
                      OR EXISTS (SELECT 1 FROM graph.edges b WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = r.id AND b.to_coll = 'recordGroups'
                                   AND b.to_id IN (SELECT jsonb_array_elements_text(s->'groups'))), false)))
                    AND (CASE WHEN $11::text = '' THEN r.id = $10::text AND $7::int = 0 ELSE r.id IN (SELECT e.from_id FROM graph.edges e
                           JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = e.to_coll AND n.id = e.to_id
                           WHERE e.org_id = $1 AND e.coll = $11::text AND e.from_coll = 'records' AND e.to_coll = ANY($12::text[]) AND e.to_id = $10::text
                             AND ($13::text = 'none' OR ($13::text = 'org' AND n.doc->>'orgId' = $2)
                                  OR ($13::text = 'external' AND n.doc->>'isExternal' = 'true' AND n.doc->>'parentOrgId' = $2))) END)
                  ORDER BY r.id LIMIT $9),
                o AS (SELECT s.id, s.doc, row_number() OVER (ORDER BY CASE WHEN $6::boolean AND s.doc->>'sourceTimestampOrigin' = 'upload' THEN 0::float8
                    ELSE coalesce(CASE WHEN jsonb_typeof(s.doc->'sourceLastModifiedTimestamp') = 'number' THEN (s.doc->>'sourceLastModifiedTimestamp')::float8 END,
                                  CASE WHEN jsonb_typeof(s.doc->'updatedAtTimestamp') = 'number' THEN (s.doc->>'updatedAtTimestamp')::float8 END, 0) END DESC, s.id) AS ord FROM s)
                SELECT o.id, o.doc, o.ord, (SELECT count(*) FROM s) AS scanned FROM o WHERE o.ord > CASE WHEN $11::text = '' THEN 0 ELSE $7::int END AND o.ord <= CASE WHEN $11::text = '' THEN 1 ELSE $7::int + $8::int END
                UNION ALL SELECT NULL, NULL, 0, (SELECT count(*) FROM s) WHERE NOT EXISTS (SELECT 1 FROM o WHERE o.ord > CASE WHEN $11::text = '' THEN 0 ELSE $7::int END
                  AND o.ord <= CASE WHEN $11::text = '' THEN 1 ELSE $7::int + $8::int END) ORDER BY ord""",
                                  *args[:9], ref["id"], edge, list(colls), scope)
        found = [(r["id"], _doc(r["doc"])) for r in rows if r["id"]]
        return found, bool(rows) and int(rows[0]["scanned"] or 0) >= ENTITY_CANDIDATE_SCAN_CAP

    async def get_entity_candidate_records(self, refs: list[dict[str, Any]], org_id: str, *, record_types: list[str] | None = None, limit_per_entity: int = 20,
                                           offset: int = 0, transaction: str | None = None) -> dict[tuple[str, str], EntityCandidateRows]:
        """For each entity, up to ``limit_per_entity`` of the org's live, indexed records linked to it (one found by its own name: whether indexed or not),
        newest first, from ``offset``, in the connectors the ref names; ``capped`` when the scan stopped at ``ENTITY_CANDIDATE_SCAN_CAP``."""
        org = current_org_id()
        if not refs or not org_id:
            return {}
        results: dict[tuple[str, str], EntityCandidateRows] = {}
        for typ, typed in _refs_by_type(refs).items():
            for ref in typed:
                results.setdefault((typ, ref["id"]), EntityCandidateRows())
                found, capped = await self._q(lambda tx, ref=ref, typ=typ: self._scan(tx, org, org_id, typ, ref, record_types, None, max(0, offset), max(1, limit_per_entity)), transaction)
                results[(typ, ref["id"])] = EntityCandidateRows((_row(i, d) for i, d in found), capped=capped and typ != EntityType.RECORD.value)
        return results

    async def get_permitted_entity_records(self, refs: list[dict[str, Any]], org_id: str, user_key: str, *, app_level_connector_ids: list[str],
                                           record_types: list[str] | None = None, limit_per_entity: int = 20, offset: int = 0, window: int = 200,
                                           timeout_seconds: float | None = None, within: list[dict[str, list[str]]] | None = None) -> dict[tuple[str, str], PermittedEntityRows]:
        """The window ``[offset, offset + window)`` of each entity's candidates, walked in order for up to ``limit_per_entity`` rows the user may read: one in a
        connector of ``app_level_connector_ids``, or one the user holds a permission role on. A user the graph does not know gets no rows. ``timeout_seconds`` is not applied."""
        org = current_org_id()
        if not refs or not org_id or not user_key:
            return {}
        limit, apps = max(1, limit_per_entity), set(app_level_connector_ids)
        scopes = within_param(within)
        results: dict[tuple[str, str], PermittedEntityRows] = {}
        for typ, typed in _refs_by_type(refs).items():
            for ref in typed:
                results.setdefault((typ, ref["id"]), PermittedEntityRows())

                async def go(tx: Tx, ref: dict = ref, typ: str = typ) -> PermittedEntityRows | None:
                    if not await tx.fetchval("/* pipeshub:graph.get_permitted_entity_records_user */ SELECT count(*) FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id = $2", org, user_key):
                        return None
                    found, capped = await self._scan(tx, org, org_id, typ, ref, record_types, scopes, max(0, offset), max(1, window))
                    hits = []
                    for pos, (rid, doc) in enumerate(found):
                        if doc.get("connectorId") in apps or await self._role_on(tx, org, user_key, "records", rid):
                            hits.append({"pos": pos, "row": _row(rid, doc)})
                            if len(hits) >= limit:
                                break
                    return PermittedEntityRows.from_window(hits, limit=limit, window_size=len(found), capped=capped and typ != EntityType.RECORD.value)
                rows = await self._q(go, None)
                if rows is not None:
                    results[(typ, ref["id"])] = rows
        return results

    # ---- what a user can reach ---------------------------------------------------------------------------------
    async def get_entity_access_context(self, user_id: str, org_id: str, source_ids: list[str] | None = None, transaction: str | None = None,
                                        exclude_app_ids: frozenset[str] = frozenset()) -> dict[str, Any] | None:
        """The apps and record groups the user can reach (None when the user is not in the graph): apps linked to the user, directly or through a team, collections
        shared with them or their team, and the connector of each source account they authenticated as; the record groups of the apps that are neither a collection nor
        app-level that they hold a grant on (their own, a group's or role's, their team's, the organization's; the source account's only in its connector), and what
        inherits from them (through record groups, from a group not hiding its children)."""
        org = current_org_id()
        user = await self.get_user_by_user_id(user_id, raise_on_error=True)
        if not user:
            return None
        uk = user["id"]
        want, skip = list(source_ids or []), sorted(exclude_app_ids)

        async def go(tx: Tx) -> dict[str, Any]:
            linked = await tx.fetch("""/* pipeshub:graph.get_entity_access_context_principals */ SELECT e.to_id, e.doc->>'connectorId' AS cid FROM graph.edges e
                JOIN graph.nodes s ON s.org_id = e.org_id AND s.coll = 'users' AND s.id = e.to_id
                WHERE e.org_id = $1 AND e.coll = 'authenticatedAs' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = 'users'""", org, uk)
            principals = [(uk, None), *((r["to_id"], r["cid"]) for r in linked)]
            rows = await tx.fetch("""/* pipeshub:graph.get_entity_access_context_apps */ SELECT DISTINCT a.id, a.doc FROM graph.nodes a WHERE a.org_id = $1 AND a.coll = 'apps' AND a.id IN (
                  SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'userAppRelation' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = 'apps'
                  UNION SELECT t.to_id FROM graph.edges p JOIN graph.nodes tm ON tm.org_id = p.org_id AND tm.coll = 'teams' AND tm.id = p.to_id
                    JOIN graph.edges t ON t.org_id = p.org_id AND t.coll = 'userAppRelation' AND t.from_coll = 'teams' AND t.from_id = tm.id AND t.to_coll = 'apps'
                    WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = $2 AND p.to_coll = 'teams' AND p.doc->>'type' = 'USER'
                  UNION SELECT k.to_id FROM graph.edges k JOIN graph.nodes kb ON kb.org_id = k.org_id AND kb.coll = 'apps' AND kb.id = k.to_id AND kb.doc->>'type' = $4 AND kb.doc->>'orgId' = $3
                    WHERE k.org_id = $1 AND k.coll = 'permission' AND k.from_coll = 'users' AND k.from_id = $2 AND k.to_coll = 'apps' AND k.doc->>'type' = 'USER'
                      AND (CASE WHEN k.doc->>'role' = ANY($5::text[]) THEN 'READER' ELSE k.doc->>'role' END) = ANY($6::text[])
                  UNION SELECT g.to_id FROM graph.edges p JOIN graph.nodes tm ON tm.org_id = p.org_id AND tm.coll = 'teams' AND tm.id = p.to_id
                    JOIN graph.edges g ON g.org_id = p.org_id AND g.coll = 'permission' AND g.from_coll = 'teams' AND g.from_id = tm.id AND g.to_coll = 'apps' AND g.doc->>'type' = 'TEAM'
                    JOIN graph.nodes kb ON kb.org_id = g.org_id AND kb.coll = 'apps' AND kb.id = g.to_id AND kb.doc->>'type' = $4 AND kb.doc->>'orgId' = $3
                    WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = $2 AND p.to_coll = 'teams' AND p.doc->>'type' = 'USER'
                  UNION SELECT unnest($7::text[]))
                AND ((cardinality($8::text[]) = 0 AND NOT coalesce((a.doc->>'isHidden')::boolean, false)) OR a.id = ANY($8::text[])) AND NOT a.id = ANY($9::text[]) ORDER BY a.id""",
                                  org, uk, org_id, _KB, _RETIRED, list(TEAM_GRANT_ROLES), [cid for _, cid in principals if cid], want, skip)
            apps = [{"id": r["id"], **{k: _doc(r["doc"]).get(k) for k in ("name", "type", "permissionModel")}} for r in rows]
            levels = [a["id"] for a in apps if (a["type"] or "") != _KB and (a["permissionModel"] or "") != PermissionModel.APP_LEVEL.value]
            seeds = await tx.fetch("""/* pipeshub:graph.get_entity_access_context_seeds */ WITH pr AS (SELECT * FROM unnest($3::text[], $4::text[]) AS p(uid, cid)),
                reach AS (
                  SELECT e.to_id AS rg, pr.cid FROM pr JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = pr.uid
                    AND e.to_coll = 'recordGroups' AND e.doc->>'type' = 'USER'
                  UNION ALL SELECT g.to_id, pr.cid FROM pr JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = pr.uid
                    AND e.to_coll IN ('groups', 'roles') AND e.doc->>'type' = 'USER'
                    JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = e.to_coll AND m.id = e.to_id
                    JOIN graph.edges g ON g.org_id = $1 AND g.coll = 'permission' AND g.from_coll = e.to_coll AND g.from_id = m.id AND g.to_coll = 'recordGroups'
                  UNION ALL SELECT g.to_id, pr.cid FROM pr JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = pr.uid
                    AND e.to_coll = 'teams' AND e.doc->>'type' = 'USER'
                    JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = 'teams' AND m.id = e.to_id
                    JOIN graph.edges g ON g.org_id = $1 AND g.coll = 'permission' AND g.from_coll = 'teams' AND g.from_id = m.id AND g.to_coll = 'recordGroups' AND g.doc->>'type' = 'TEAM'
                  UNION ALL SELECT g.to_id, NULL FROM graph.edges b JOIN graph.nodes o ON o.org_id = b.org_id AND o.coll = 'organizations' AND o.id = b.to_id
                    JOIN graph.edges g ON g.org_id = $1 AND g.coll = 'permission' AND g.from_coll = 'organizations' AND g.from_id = o.id AND g.to_coll = 'recordGroups' AND g.doc->>'type' = 'ORG'
                    WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'users' AND b.from_id = $2 AND b.to_coll = 'organizations' AND b.doc->>'entityType' = 'ORGANIZATION')
                SELECT DISTINCT rg.id, coalesce((rg.doc->>'hideChildren')::boolean, false) AS hide FROM reach JOIN graph.nodes rg ON rg.org_id = $1 AND rg.coll = 'recordGroups' AND rg.id = reach.rg
                WHERE rg.doc->>'orgId' = $5 AND coalesce((rg.doc->>'isDeleted')::boolean, false) = false AND rg.doc->>'connectorId' = ANY($6::text[])
                  AND (reach.cid IS NULL OR rg.doc->>'connectorId' = reach.cid)""",
                                    org, uk, [p for p, _ in principals], [c for _, c in principals], org_id, levels)
            roots = [r["id"] for r in seeds if not r["hide"]]
            nested = await tx.fetch("""/* pipeshub:graph.get_entity_access_context_nested */ WITH RECURSIVE w(id, depth) AS (
                  SELECT x, 0 FROM unnest($2::text[]) x
                  UNION SELECT i.from_id, w.depth + 1 FROM w JOIN graph.edges i ON i.org_id = $1 AND i.coll = 'inheritPermissions' AND i.to_coll = 'recordGroups' AND i.to_id = w.id
                    AND i.from_coll = 'recordGroups' JOIN graph.nodes c ON c.org_id = i.org_id AND c.coll = 'recordGroups' AND c.id = i.from_id WHERE w.depth < $3::int)
                SELECT DISTINCT c.id FROM w JOIN graph.nodes c ON c.org_id = $1 AND c.coll = 'recordGroups' AND c.id = w.id
                WHERE w.depth >= 1 AND c.doc->>'orgId' = $4 AND coalesce((c.doc->>'isDeleted')::boolean, false) = false AND c.doc->>'connectorId' = ANY($5::text[])""",
                                     org, roots, CONTAINER_INHERIT_MAX_DEPTH, org_id, levels)
            return {"user_key": uk, "apps": apps, "record_group_ids": sorted({r["id"] for r in seeds} | {r["id"] for r in nested})}
        return await self._q(go, transaction)

    # ---- the entity index --------------------------------------------------------------------------------------
    async def get_entity_index_candidate(self, collection: str, marker: str, *, sweep_before: int | None = None, transaction: str | None = None) -> dict | None:
        """One app (or organization) whose entity index is not at ``marker`` (an organization also when it was swept before ``sweep_before``); an app being deleted,
        and an external organization, never. The ambient org, or every org under a system context, in order."""
        if collection not in ENTITY_INDEX_CANDIDATE_COLLECTIONS:
            raise ValueError(f"{collection!r} does not carry entity index state")
        errors: list[tuple[str, Exception]] = []
        for org_id in [None] if transaction else await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                row = await self._q(lambda tx, org=org: tx.fetchrow("""/* pipeshub:graph.get_entity_index_candidate */ SELECT n.doc FROM graph.nodes n
                    WHERE n.org_id = $1 AND n.coll = $2 AND (coalesce(n.doc->>$3, '') <> $4 OR ($5::bigint IS NOT NULL AND (n.doc->>$6 IS NULL OR n.doc->$6 < to_jsonb($5::bigint))))
                      AND (CASE WHEN $2 = 'apps' THEN coalesce(n.doc->>'status', '') <> $7 ELSE coalesce(n.doc->>'isExternal', 'false') <> 'true' END) ORDER BY n.id LIMIT 1""",
                                                              org, collection, ENTITY_INDEX_STATE_FIELD, marker, sweep_before, ENTITY_INDEX_SWEPT_AT_FIELD, APP_STATUS_DELETING), transaction)
                if row:
                    return _arango(_doc(row["doc"]), collection)
        self._raise_failed(errors)
        return None

    async def page_entity_index_source(self, source: str, scope_id: str, after_key: str | None, limit: int, transaction: str | None = None) -> list[dict]:
        """One page, in key order after ``after_key``, of a source's rows in a scope (a connector, or an org): its ``_key``, name and the fields the source lists.
        An unknown source raises whatever the scope."""
        spec = entity_index_source(source)
        if not scope_id:
            return []
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.page_entity_index_source */ SELECT n.id, n.doc FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = $2 AND (n.doc->>$3 = $4 OR ($5::boolean AND n.doc->>$3 IS NULL))
              AND (NOT $6::boolean OR (n.doc->>'normalizedName' IS NOT NULL AND n.doc->>'mergedInto' IS NULL))
              AND NOT EXISTS (SELECT 1 FROM unnest($7::text[]) f WHERE n.doc->f IS DISTINCT FROM 'true'::jsonb)
              AND ($8::text IS NULL OR n.id > $8) ORDER BY n.id LIMIT $9""",
                                             org, spec.collection, spec.scope_field, scope_id, spec.include_global, spec.canonical_only, list(spec.required_true), after_key or None, max(1, int(limit))), transaction)
        return [self._index_row(spec, r["id"], _doc(r["doc"])) for r in rows]

    @staticmethod
    def _index_row(spec: Any, key: str, doc: dict) -> dict:  # noqa: ANN401
        name, fallback = doc.get(spec.name_field), doc.get(spec.name_fallback) if spec.name_fallback else None
        if spec.name_fallback:
            name = name if (str(name).strip() != "" if spec.blank_name_falls_back else name not in (None, "")) and name is not None else fallback
        return {"_key": key, "name": name, **{f: doc.get(f) for f in spec.fields}, **{f: doc.get(f) or [] for f in spec.list_fields}}

    async def stage_state_org_for_virtual_record(self, virtual_record_id: str) -> str | None:
        """The org a stage state of this virtual record names, or None: the ambient org's own, or the first org's under a system context. A stage state that names
        another org than the one it is stored in is not an answer."""
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                row = await self._q(lambda tx, org=org: tx.fetchrow("""/* pipeshub:graph.stage_state_org_for_virtual_record */ SELECT n.doc->>'orgId' AS org FROM graph.nodes n
                    WHERE n.org_id = $1 AND n.coll = 'stageStates' AND n.doc->>'virtualRecordId' = $2 AND n.doc->>'orgId' = $1 ORDER BY n.id LIMIT 1""", org, virtual_record_id), None)
                if row:
                    return row["org"]
        self._raise_failed(errors)
        return None
