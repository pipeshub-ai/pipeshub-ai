"""What is inside a knowledge base (its root, a folder, a flat listing of records), the artifact gallery, agent templates and the legacy hub app.

The callers decide who may open a collection or a folder (``get_user_kb_permission``); these methods list what the graph holds for the ids they are given,
in the ambient org, and ``list_kb_records`` and the artifact gallery start from the user's own grant. Every method resolves the ambient org first (before
any ``try``); every statement is a literal with bound parameters, an optional filter being a null-when-absent bound value.
"""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import FOLDER_MIME_TYPES, CollectionNames, Connectors
from app.core.org_context import current_org_id
from app.services.graph_db.postgres.base import PgBase, _arango, _doc
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx

_KB = Connectors.KNOWLEDGE_BASE.value
_TEMPLATES = CollectionNames.AGENT_TEMPLATES.value
_ORG_FIELD = "orgId"  # the org property of a stored record, returned as a field; the org of a call is the ambient one
# a grant with any other role (an unknown or stale one) opens nothing: fail closed, unlike the other stores, which show the template
_TEMPLATE_ROLES = ["OWNER", "ORGANIZER", "WRITER", "READER"]
_SORT_KEYS = {"name": 1, "created_at": 2, "updated_at": 3, "size": 4}


def _file(f: Any, *, path: bool) -> dict | None:  # noqa: ANN401
    if not f:
        return None
    d = _doc(f)
    out = {"id": d.get("id"), "name": d.get("name"), "extension": d.get("extension"), "mimeType": d.get("mimeType"), "sizeInBytes": d.get("sizeInBytes"),
           "webUrl": d.get("webUrl"), "isFile": d.get("isFile")}
    return {**out, "path": d.get("path")} if path else out


def _child_record(doc: Any, fdoc: Any) -> dict:  # noqa: ANN401
    d = _doc(doc)
    return {"id": d.get("id"), "recordName": d.get("recordName"), "name": d.get("recordName"), "recordType": d.get("recordType"),
            "externalRecordId": d.get("externalRecordId"), "origin": d.get("origin"), "connectorName": d.get("connectorName") or "KNOWLEDGE_BASE",
            "indexingStatus": d.get("indexingStatus"), "version": d.get("version"), "isLatestVersion": d.get("isLatestVersion", True),
            "createdAtTimestamp": d.get("createdAtTimestamp"), "updatedAtTimestamp": d.get("updatedAtTimestamp"),
            "sourceCreatedAtTimestamp": d.get("sourceCreatedAtTimestamp"), "sourceLastModifiedTimestamp": d.get("sourceLastModifiedTimestamp"),
            "webUrl": d.get("webUrl"), "orgId": d.get(_ORG_FIELD), "type": "record", "fileRecord": _file(fdoc, path=True)}


def _folder_item(doc: Any, subs: int, recs: int, parent_id: str | None, path: str | None = None) -> dict:  # noqa: ANN401
    d = _doc(doc)
    out = {"id": d.get("id"), "name": d.get("recordName"), "level": 1, "parent_id": parent_id, "webUrl": d.get("webUrl"), "recordGroupId": d.get("connectorId"),
           "type": "folder", "createdAtTimestamp": d.get("createdAtTimestamp"), "updatedAtTimestamp": d.get("updatedAtTimestamp"),
           "counts": {"subfolders": subs, "records": recs, "totalItems": subs + recs}, "hasChildren": subs > 0 or recs > 0}
    return {**out, "path": path} if parent_id is not None else out


def _filters(row: Any) -> dict:  # noqa: ANN401
    return {"recordTypes": sorted(row["rtypes"]), "origins": sorted(row["origins"]), "connectors": sorted(row["connectors"]), "indexingStatus": sorted(row["statuses"])}


def _artifact(rec: Any, art: Any, perm: Any) -> dict:  # noqa: ANN401
    r, a, p = _doc(rec), _doc(art), _doc(perm)
    return {"id": r.get("id"), "recordName": r.get("recordName"), "recordType": r.get("recordType"), "mimeType": r.get("mimeType"), "sizeInBytes": r.get("sizeInBytes"),
            "version": r.get("version"), "createdAtTimestamp": r.get("createdAtTimestamp"), "updatedAtTimestamp": r.get("updatedAtTimestamp"),
            "artifactDoc": {k: a.get(k) for k in ("name", "artifactType", "conversationId", "visibility", "mimeType", "sizeInBytes", "logicalName", "contentHash",
                                                  "versions", "description", "sourceTool", "isTemporary")},
            "permission": {"role": p.get("role"), "type": p.get("type")}}


class KbListingsMixin(PgBase):
    async def _children(self, kb_id: str, folder_id: str | None, skip: int, limit: int, level: int, search: str | None, record_types: list[str] | None,
                        origins: list[str] | None, connectors: list[str] | None, indexing_status: list[str] | None, sort_by: str, sort_order: str,
                        transaction: str | None) -> dict:
        """The folders and then the records directly in the collection (``folder_id`` None) or in the folder, folders first: the page takes folders from ``skip``
        and fills what is left of ``limit`` with records."""
        org = current_org_id()
        try:
            skip, limit = max(skip, 0), max(limit, 0)
            key, asc = _SORT_KEYS.get(sort_by, 1), sort_order.upper() != "DESC"

            async def go(tx: Tx) -> dict:
                if folder_id is None:
                    kb = await tx.fetchrow("/* pipeshub:graph.children_kb */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = $2", org, kb_id)
                    if not kb:
                        return {"success": False, "reason": "Knowledge base not found", "code": 404}
                    rows = await tx.fetch("""/* pipeshub:graph.children_kb_folders */ WITH fo AS (
                          SELECT r.id, r.doc, cnt.subs, cnt.recs FROM graph.nodes r
                          JOIN LATERAL (SELECT count(*) FILTER (WHERE c.doc->>'mimeType' = ANY($3::text[])) AS subs,
                                               count(*) FILTER (WHERE coalesce((c.doc->>'isDeleted')::boolean, false) = false AND NOT (c.doc->>'mimeType' = ANY($3::text[]))) AS recs
                                        FROM graph.edges ce JOIN graph.nodes c ON c.org_id = ce.org_id AND c.coll = 'records' AND c.id = ce.to_id
                                        WHERE ce.org_id = r.org_id AND ce.coll = 'nodeRelations' AND ce.doc->>'relationshipType' = 'PARENT_CHILD' AND ce.from_coll = 'records'
                                          AND ce.from_id = r.id AND ce.to_coll = 'records') cnt ON true
                          WHERE r.org_id = $1 AND r.coll = 'records' AND coalesce((r.doc->>'isDeleted')::boolean, false) = false AND r.doc->>'mimeType' = ANY($3::text[]) AND r.doc->>'orgId' = $1
                            AND EXISTS (SELECT 1 FROM graph.edges b JOIN graph.nodes k ON k.org_id = b.org_id AND k.coll = 'apps' AND k.id = b.to_id AND k.doc->>'type' = $7
                                        WHERE b.org_id = r.org_id AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = r.id AND b.to_coll = 'apps' AND b.to_id = $2)
                            AND NOT EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = r.org_id AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD'
                                            AND p.from_coll = 'records' AND p.to_coll = 'records' AND p.to_id = r.id)
                            AND ($4::text IS NULL OR position(lower($4::text) IN lower(r.doc->>'recordName')) > 0))
                        SELECT (SELECT count(*) FROM fo) AS total, p.doc, p.subs, p.recs, p.rn FROM (SELECT 1) one
                        LEFT JOIN LATERAL (SELECT x.doc, x.subs, x.recs, row_number() OVER (ORDER BY x.doc->>'recordName' COLLATE "C", x.id) AS rn FROM fo x
                                           ORDER BY x.doc->>'recordName' COLLATE "C", x.id OFFSET $5 LIMIT $6) p ON true ORDER BY p.rn""",
                                         org, kb_id, FOLDER_MIME_TYPES, search or None, skip, limit, _KB)
                    container = _doc(kb["doc"])
                    box = {"id": container.get("id"), "name": container.get("groupName") or container.get("name"), "path": "/", "type": "kb",
                           "webUrl": f"/kb/{container.get('id')}", "recordGroupId": container.get("id")}
                else:
                    box = None
                    rows = await tx.fetch("""/* pipeshub:graph.children_folder_folders */ WITH fo AS (
                          SELECT s.id, s.doc, sf.doc->>'path' AS path, cnt.subs, cnt.recs FROM graph.nodes fr
                          JOIN graph.edges e ON e.org_id = fr.org_id AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' = 'PARENT_CHILD' AND e.from_coll = 'records'
                            AND e.from_id = fr.id AND e.to_coll = 'records'
                          JOIN graph.nodes s ON s.org_id = fr.org_id AND s.coll = 'records' AND s.id = e.to_id
                          JOIN LATERAL (SELECT f.doc FROM graph.edges te JOIN graph.nodes f ON f.org_id = te.org_id AND f.coll = 'files' AND f.id = te.to_id
                                        WHERE te.org_id = s.org_id AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = s.id AND f.doc->>'isFile' = 'false'
                                        ORDER BY f.id LIMIT 1) sf ON true
                          JOIN LATERAL (SELECT count(*) FILTER (WHERE cf.doc->>'isFile' = 'false') AS subs,
                                               count(*) FILTER (WHERE coalesce((c.doc->>'isDeleted')::boolean, false) = false AND (cf.id IS NULL OR cf.doc->>'isFile' <> 'false')) AS recs
                                        FROM graph.edges ce JOIN graph.nodes c ON c.org_id = ce.org_id AND c.coll = 'records' AND c.id = ce.to_id
                                        LEFT JOIN LATERAL (SELECT f.id, f.doc FROM graph.edges te JOIN graph.nodes f ON f.org_id = te.org_id AND f.coll = 'files' AND f.id = te.to_id
                                                           WHERE te.org_id = c.org_id AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = c.id ORDER BY f.id LIMIT 1) cf ON true
                                        WHERE ce.org_id = s.org_id AND ce.coll = 'nodeRelations' AND ce.doc->>'relationshipType' = 'PARENT_CHILD' AND ce.from_coll = 'records'
                                          AND ce.from_id = s.id AND ce.to_coll = 'records') cnt ON true
                          WHERE fr.org_id = $1 AND fr.coll = 'records' AND fr.id = $2
                            AND EXISTS (SELECT 1 FROM graph.edges te JOIN graph.nodes f ON f.org_id = te.org_id AND f.coll = 'files' AND f.id = te.to_id AND f.doc->>'isFile' = 'false'
                                        WHERE te.org_id = fr.org_id AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = fr.id)
                            AND ($3::text IS NULL OR position(lower($3::text) IN lower(s.doc->>'recordName')) > 0))
                        SELECT (SELECT count(*) FROM fo) AS total, p.doc, p.path, p.subs, p.recs, p.rn FROM (SELECT 1) one
                        LEFT JOIN LATERAL (SELECT x.doc, x.path, x.subs, x.recs, row_number() OVER (ORDER BY x.doc->>'recordName' COLLATE "C", x.id) AS rn FROM fo x
                                           ORDER BY x.doc->>'recordName' COLLATE "C", x.id OFFSET $4 LIMIT $5) p ON true ORDER BY p.rn""",
                                         org, folder_id, search or None, skip, limit)
                total_folders = rows[0]["total"]
                folders = [_folder_item(r["doc"], r["subs"], r["recs"], folder_id, r["path"] if folder_id else None) for r in rows if r["doc"] is not None]
                if skip < total_folders:
                    rskip, rlimit = 0, max(limit - len(folders), 0)
                else:
                    rskip, rlimit = skip - total_folders, limit
                recs = await tx.fetch("""/* pipeshub:graph.children_records */ WITH recs AS (
                      SELECT r.id, r.doc FROM graph.nodes r
                      WHERE r.org_id = $1 AND r.coll = 'records' AND coalesce((r.doc->>'isDeleted')::boolean, false) = false
                        AND ($3::text IS NOT NULL OR r.doc->>'orgId' = $1)
                        AND CASE WHEN $3::text IS NULL
                          THEN NOT (r.doc->>'mimeType' = ANY($4::text[]))
                            AND EXISTS (SELECT 1 FROM graph.edges b JOIN graph.nodes k ON k.org_id = b.org_id AND k.coll = 'apps' AND k.id = b.to_id AND k.doc->>'type' = $14
                                        WHERE b.org_id = r.org_id AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = r.id AND b.to_coll = 'apps' AND b.to_id = $2)
                            AND NOT EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = r.org_id AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD'
                                            AND p.from_coll = 'records' AND p.to_coll = 'records' AND p.to_id = r.id)
                          ELSE EXISTS (SELECT 1 FROM graph.edges p JOIN graph.nodes pn ON pn.org_id = p.org_id AND pn.coll = 'records' AND pn.id = p.from_id
                                       WHERE p.org_id = r.org_id AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD' AND p.from_coll = 'records'
                                         AND p.from_id = $3 AND p.to_coll = 'records' AND p.to_id = r.id)
                            AND NOT EXISTS (SELECT 1 FROM graph.edges te JOIN graph.nodes ff ON ff.org_id = te.org_id AND ff.coll = 'files' AND ff.id = te.to_id AND ff.doc->>'isFile' = 'false'
                                            WHERE te.org_id = r.org_id AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = r.id) END
                        AND ($5::text IS NULL OR position(lower($5::text) IN lower(r.doc->>'recordName')) > 0 OR position(lower($5::text) IN lower(r.doc->>'externalRecordId')) > 0)
                        AND ($6::text[] IS NULL OR r.doc->>'recordType' = ANY($6::text[])) AND ($7::text[] IS NULL OR r.doc->>'origin' = ANY($7::text[]))
                        AND ($8::text[] IS NULL OR r.doc->>'connectorName' = ANY($8::text[])) AND ($9::text[] IS NULL OR r.doc->>'indexingStatus' = ANY($9::text[])))
                    SELECT s.total, s.rtypes, s.origins, s.connectors, s.statuses, p.doc, p.fdoc, p.rn FROM
                      (SELECT count(*) AS total,
                              coalesce(array_agg(DISTINCT doc->>'recordType') FILTER (WHERE doc->>'recordType' IS NOT NULL), '{}') AS rtypes,
                              coalesce(array_agg(DISTINCT doc->>'origin') FILTER (WHERE doc->>'origin' IS NOT NULL), '{}') AS origins,
                              coalesce(array_agg(DISTINCT coalesce(doc->>'connectorName', 'KNOWLEDGE_BASE')), '{}') AS connectors,
                              coalesce(array_agg(DISTINCT doc->>'indexingStatus') FILTER (WHERE doc->>'indexingStatus' IS NOT NULL), '{}') AS statuses FROM recs) s
                      LEFT JOIN LATERAL (SELECT q.doc, q.rn, (SELECT ff.doc FROM graph.edges te JOIN graph.nodes ff ON ff.org_id = te.org_id AND ff.coll = 'files' AND ff.id = te.to_id
                                                              WHERE te.org_id = $1 AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = q.id ORDER BY ff.id LIMIT 1) AS fdoc
                                         FROM (SELECT x.id, x.doc, row_number() OVER (ORDER BY
                            (CASE WHEN $10::int = 1 AND $11::boolean THEN x.doc->>'recordName' END) COLLATE "C" ASC, (CASE WHEN $10::int = 1 AND NOT $11::boolean THEN x.doc->>'recordName' END) COLLATE "C" DESC,
                            (CASE WHEN $10::int = 2 AND $11::boolean THEN x.doc->'createdAtTimestamp' END) ASC, (CASE WHEN $10::int = 2 AND NOT $11::boolean THEN x.doc->'createdAtTimestamp' END) DESC,
                            (CASE WHEN $10::int = 3 AND $11::boolean THEN x.doc->'updatedAtTimestamp' END) ASC, (CASE WHEN $10::int = 3 AND NOT $11::boolean THEN x.doc->'updatedAtTimestamp' END) DESC,
                            (CASE WHEN $10::int = 4 AND $11::boolean THEN (SELECT sf.doc->'sizeInBytes' FROM graph.edges se JOIN graph.nodes sf ON sf.org_id = se.org_id AND sf.coll = 'files' AND sf.id = se.to_id
                                    WHERE se.org_id = $1 AND se.coll = 'isOfType' AND se.from_coll = 'records' AND se.from_id = x.id ORDER BY sf.id LIMIT 1) END) ASC,
                            (CASE WHEN $10::int = 4 AND NOT $11::boolean THEN (SELECT sf.doc->'sizeInBytes' FROM graph.edges se JOIN graph.nodes sf ON sf.org_id = se.org_id AND sf.coll = 'files' AND sf.id = se.to_id
                                    WHERE se.org_id = $1 AND se.coll = 'isOfType' AND se.from_coll = 'records' AND se.from_id = x.id ORDER BY sf.id LIMIT 1) END) DESC, x.id) AS rn
                                               FROM recs x ORDER BY rn OFFSET $12 LIMIT $13) q) p ON true ORDER BY p.rn""",
                                     org, kb_id, folder_id, FOLDER_MIME_TYPES, search or None, record_types or None, origins or None, connectors or None,
                                     indexing_status or None, key, asc, rskip, rlimit, _KB)
                records = [_child_record(r["doc"], r["fdoc"]) for r in recs if r["doc"] is not None]
                total_records = recs[0]["total"]
                out = {"success": True, "folders": folders, "records": records, "totalCount": total_folders + total_records, "availableFilters": _filters(recs[0])}
                if box:
                    return {**out, "container": box, "level": level, "paginationMode": "folders_first",
                            "counts": {"folders": len(folders), "records": len(records), "totalItems": len(folders) + len(records),
                                       "totalFolders": total_folders, "totalRecords": total_records}}
                return {**out, "counts": {"folders": total_folders, "records": total_records, "totalItems": total_folders + total_records,
                                          "foldersShown": len(folders), "recordsShown": len(records)}}
            return await self._q(go, transaction)
        except Exception as e:
            self.logger.error(f"❌ Failed to get children: {e}")
            return {"success": False, "reason": str(e)}

    async def get_kb_children(self, kb_id: str, skip: int, limit: int, level: int = 1, search: str | None = None, record_types: list[str] | None = None,
                              origins: list[str] | None = None, connectors: list[str] | None = None, indexing_status: list[str] | None = None,
                              sort_by: str = "name", sort_order: str = "asc", transaction: str | None = None) -> dict:
        current_org_id()
        return await self._children(kb_id, None, skip, limit, level, search, record_types, origins, connectors, indexing_status, sort_by, sort_order, transaction)

    async def get_folder_children(self, kb_id: str, folder_id: str, skip: int, limit: int, level: int = 1, search: str | None = None,
                                  record_types: list[str] | None = None, origins: list[str] | None = None, connectors: list[str] | None = None,
                                  indexing_status: list[str] | None = None, sort_by: str = "name", sort_order: str = "asc", transaction: str | None = None) -> dict:
        """The caller must validate that the folder is in the knowledge base (``validate_folder_in_kb``): the listing reads the folder by id."""
        current_org_id()
        return await self._children(kb_id, folder_id, skip, limit, level, search, record_types, origins, connectors, indexing_status, sort_by, sort_order, transaction)

    async def list_kb_records(self, kb_id: str, user_id: str, org_id: str, skip: int, limit: int, search: str | None = None, record_types: list[str] | None = None,
                              origins: list[str] | None = None, connectors: list[str] | None = None, indexing_status: list[str] | None = None,
                              date_from: int | None = None, date_to: int | None = None, sort_by: str = "createdAtTimestamp", sort_order: str = "desc",
                              folder_id: str | None = None, transaction: str | None = None) -> tuple[list[dict], int, dict]:
        """The collection's records one level per folder (a record in two folders is listed in each) and at the root, for a user who holds a role on
        it; with no role the answer is empty. A failed read raises."""
        org = current_org_id()
        role = await self.get_user_kb_permission(kb_id, user_id, transaction)
        if not role:
            return [], 0, {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": [], "folders": []}

        async def go(tx: Tx) -> tuple[list[dict], int, dict]:
            rows = await tx.fetch("""/* pipeshub:graph.list_kb_records */ WITH kb AS (SELECT k.id FROM graph.nodes k WHERE k.org_id = $1 AND k.coll = 'apps' AND k.id = $2 AND k.doc->>'type' = $4),
                folders AS (SELECT f.id, f.doc FROM kb JOIN graph.edges b ON b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.to_coll = 'apps' AND b.to_id = kb.id
                    JOIN graph.nodes f ON f.org_id = $1 AND f.coll = 'records' AND f.id = b.from_id WHERE f.doc->>'mimeType' = ANY($3::text[]) AND ($5::text IS NULL OR f.id = $5)),
                cand AS (
                  SELECT r.id, r.doc, f.id AS fid, f.doc->>'recordName' AS fname FROM folders f
                    JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' = 'PARENT_CHILD' AND e.from_coll = 'records' AND e.from_id = f.id AND e.to_coll = 'records'
                    JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.id = e.to_id
                  UNION ALL
                  SELECT r.id, r.doc, NULL, NULL FROM kb JOIN graph.edges b ON b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.to_coll = 'apps' AND b.to_id = kb.id
                    JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.id = b.from_id
                  WHERE $5::text IS NULL AND NOT EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD'
                                                          AND p.from_coll = 'records' AND p.to_coll = 'records' AND p.to_id = r.id)),
                live AS (SELECT c.* FROM cand c WHERE coalesce((c.doc->>'isDeleted')::boolean, false) = false AND c.doc->>'orgId' = $6 AND NOT (c.doc->>'mimeType' = ANY($3::text[]))),
                kept AS (SELECT c.* FROM live c WHERE ($7::text IS NULL OR position(lower($7::text) IN lower(c.doc->>'recordName')) > 0 OR position(lower($7::text) IN lower(c.doc->>'externalRecordId')) > 0)
                    AND ($8::text[] IS NULL OR c.doc->>'recordType' = ANY($8::text[])) AND ($9::text[] IS NULL OR c.doc->>'origin' = ANY($9::text[]))
                    AND ($10::text[] IS NULL OR c.doc->>'connectorName' = ANY($10::text[])) AND ($11::text[] IS NULL OR c.doc->>'indexingStatus' = ANY($11::text[]))
                    AND ($12::bigint IS NULL OR (c.doc->>'createdAtTimestamp')::numeric >= $12) AND ($13::bigint IS NULL OR (c.doc->>'createdAtTimestamp')::numeric <= $13)),
                ranked AS (SELECT x.id, x.doc, x.fid, x.fname, row_number() OVER (ORDER BY
                    (CASE WHEN jsonb_typeof(x.doc->$14::text) = 'string' AND $17::boolean THEN x.doc->>$14::text END) COLLATE "C" ASC,
                    (CASE WHEN jsonb_typeof(x.doc->$14::text) = 'string' AND NOT $17::boolean THEN x.doc->>$14::text END) COLLATE "C" DESC,
                    (CASE WHEN jsonb_typeof(x.doc->$14::text) = 'number' AND $17::boolean THEN (x.doc->>$14::text)::numeric END) ASC,
                    (CASE WHEN jsonb_typeof(x.doc->$14::text) = 'number' AND NOT $17::boolean THEN (x.doc->>$14::text)::numeric END) DESC, x.id, x.fid) AS rn FROM kept x)
                SELECT s.total, s.live_n, s.facets, s.folders, p.doc, p.fdoc, p.fid, p.fname, p.rn FROM
                  (SELECT (SELECT count(*) FROM ranked) AS total,
                          CASE WHEN $5::text IS NULL THEN (SELECT count(*) FROM live) END AS live_n,
                          CASE WHEN $5::text IS NULL THEN (SELECT coalesce(jsonb_agg(jsonb_build_object('rt', g.rt, 'o', g.o, 'cn', g.cn, 'st', g.st)), '[]'::jsonb)
                           FROM (SELECT doc->>'recordType' AS rt, doc->>'origin' AS o, doc->>'connectorName' AS cn, doc->>'indexingStatus' AS st FROM live GROUP BY 1, 2, 3, 4) g) END AS facets,
                          CASE WHEN $5::text IS NULL THEN (SELECT coalesce(jsonb_agg(jsonb_build_object('id', t.id, 'name', t.doc->'recordName') ORDER BY t.id), '[]'::jsonb) FROM folders t
                           WHERE coalesce((t.doc->>'isDeleted')::boolean, false) = false) END AS folders) s
                  LEFT JOIN LATERAL (SELECT q.doc, q.fid, q.fname, q.rn, (SELECT ff.doc FROM graph.edges te JOIN graph.nodes ff ON ff.org_id = $1 AND ff.coll = 'files' AND ff.id = te.to_id
                        WHERE te.org_id = $1 AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = q.id ORDER BY ff.id LIMIT 1) AS fdoc
                     FROM (SELECT * FROM ranked WHERE rn > $15::bigint AND rn <= $15::bigint + $16::bigint) q) p ON true ORDER BY p.rn""",
                                 org, kb_id, FOLDER_MIME_TYPES, _KB, folder_id, org_id, search or None, record_types or None, origins or None, connectors or None,
                                 indexing_status or None, date_from or None, date_to or None, sort_by, max(skip, 0), max(limit, 0), sort_order.upper() == "ASC")
            records = []
            for r in rows:
                if r["doc"] is None:
                    continue
                d, f = _doc(r["doc"]), (_doc(r["fdoc"]) if r["fdoc"] else None)
                records.append({"id": d.get("id"), "externalRecordId": d.get("externalRecordId"), "externalRevisionId": d.get("externalRevisionId"),
                                "recordName": d.get("recordName"), "recordType": d.get("recordType"), "origin": d.get("origin"),
                                "connectorName": d.get("connectorName") or "KNOWLEDGE_BASE", "indexingStatus": d.get("indexingStatus"),
                                "createdAtTimestamp": d.get("createdAtTimestamp"), "updatedAtTimestamp": d.get("updatedAtTimestamp"),
                                "sourceCreatedAtTimestamp": d.get("sourceCreatedAtTimestamp"), "sourceLastModifiedTimestamp": d.get("sourceLastModifiedTimestamp"),
                                "orgId": d.get(_ORG_FIELD), "version": d.get("version"), "isDeleted": d.get("isDeleted"), "deletedByUserId": d.get("deletedByUserId"),
                                "isLatestVersion": d.get("isLatestVersion", True), "webUrl": d.get("webUrl"), "fileRecord": _file(f, path=False) and
                                {k: v for k, v in _file(f, path=False).items() if k != "path"},
                                "permission": {"role": role, "type": "USER"}, "kb_id": kb_id,
                                "folder": {"id": r["fid"], "name": r["fname"]} if r["fid"] is not None else None})
            # the folders are named only when some record exists (as the other stores' filter query, which unwinds records)
            if folder_id is None:           # the collection's whole listing: what the page statement already counted is the facet set
                combos, folders_json, live_n = _doc_list(rows[0]["facets"]), rows[0]["folders"], rows[0]["live_n"]
            else:                           # one folder's page: the facets still describe the whole collection
                filt = await tx.fetch("""/* pipeshub:graph.list_kb_records_filters */ WITH kb AS (SELECT k.id FROM graph.nodes k WHERE k.org_id = $1 AND k.coll = 'apps' AND k.id = $2 AND k.doc->>'type' = $4),
                    folders AS (SELECT f.id, f.doc FROM kb JOIN graph.edges b ON b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.to_coll = 'apps' AND b.to_id = kb.id
                        JOIN graph.nodes f ON f.org_id = $1 AND f.coll = 'records' AND f.id = b.from_id WHERE f.doc->>'mimeType' = ANY($3::text[])),
                    recs AS (
                      SELECT r.id, r.doc FROM folders f JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' = 'PARENT_CHILD'
                          AND e.from_coll = 'records' AND e.from_id = f.id AND e.to_coll = 'records' JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.id = e.to_id
                      UNION ALL
                      SELECT r.id, r.doc FROM kb JOIN graph.edges b ON b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.to_coll = 'apps' AND b.to_id = kb.id
                        JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.id = b.from_id
                      WHERE NOT EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD'
                                        AND p.from_coll = 'records' AND p.to_coll = 'records' AND p.to_id = r.id)),
                    live AS (SELECT c.* FROM recs c WHERE coalesce((c.doc->>'isDeleted')::boolean, false) = false AND c.doc->>'orgId' = $5 AND NOT (c.doc->>'mimeType' = ANY($3::text[])))
                    SELECT g.rt, g.o, g.cn, g.st, g.n, f.folders
                    FROM (SELECT doc->>'recordType' AS rt, doc->>'origin' AS o, doc->>'connectorName' AS cn, doc->>'indexingStatus' AS st, count(*) AS n FROM live GROUP BY 1, 2, 3, 4) g,
                         (SELECT coalesce(jsonb_agg(jsonb_build_object('id', t.id, 'name', t.doc->'recordName') ORDER BY t.id), '[]'::jsonb) AS folders FROM folders t
                          WHERE coalesce((t.doc->>'isDeleted')::boolean, false) = false) f""",
                                       org, kb_id, FOLDER_MIME_TYPES, _KB, org_id)
                combos = [{"rt": r["rt"], "o": r["o"], "cn": r["cn"], "st": r["st"]} for r in filt]
                folders_json, live_n = (filt[0]["folders"] if filt else "[]"), sum(r["n"] for r in filt)
            facets = {k: sorted({r[c] for r in combos if r[c] is not None}) for k, c in (("recordTypes", "rt"), ("origins", "o"), ("connectors", "cn"), ("indexingStatus", "st"))}
            # the folders are named only when some record exists (as the other stores' filter query, which unwinds records)
            return records, rows[0]["total"], {**facets, "permissions": [role], "folders": _doc_list(folders_json) if live_n else []}
        return await self._q(go, transaction)

    # ---- the artifact gallery ---------------------------------------------------------------------------------------
    async def list_accessible_artifacts(self, user_id: str, org_id: str, skip: int, limit: int, search: str | None, artifact_types: list[str] | None,
                                        conversation_id: str | None, date_from: int | None, date_to: int | None, sort_by: str, sort_order: str,
                                        ownership: str = "all") -> tuple[list[dict], dict[str, int]]:
        """The visible artifacts the user holds a grant on, by the user's own USER grant alone (no team, no group). ``counts`` ignore ``ownership``."""
        org = current_org_id()
        key = {"name": 1, "updatedAtTimestamp": 3, "artifactType": 4}.get(sort_by, 2)
        own = {"mine": 1, "shared": 2}.get(ownership, 0)

        async def go(tx: Tx) -> tuple[list[dict], dict[str, int]]:
            rows = await tx.fetch("""/* pipeshub:graph.list_accessible_artifacts */ WITH g AS (
                  SELECT rec.id, rec.doc AS rdoc, art.doc AS adoc, perm.doc AS pdoc, coalesce(perm.doc->>'role' = 'OWNER', false) AS owned
                  FROM graph.nodes u JOIN graph.edges perm ON perm.org_id = u.org_id AND perm.coll = 'permission' AND perm.from_coll = 'users' AND perm.from_id = u.id
                      AND perm.to_coll = 'records' AND perm.doc->>'type' = 'USER'
                    JOIN graph.nodes rec ON rec.org_id = u.org_id AND rec.coll = 'records' AND rec.id = perm.to_id
                    JOIN graph.edges te ON te.org_id = u.org_id AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = rec.id AND te.to_coll = 'artifacts'
                    JOIN graph.nodes art ON art.org_id = u.org_id AND art.coll = 'artifacts' AND art.id = te.to_id
                  WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $2 AND rec.doc->>'orgId' = $3 AND rec.doc->>'recordType' = 'ARTIFACT'
                    AND coalesce((rec.doc->>'isDeleted')::boolean, false) = false AND art.doc->>'artifactType' <> 'TOOL_RESULT'
                    AND coalesce((art.doc->>'isTemporary')::boolean, false) = false AND coalesce(art.doc->>'visibility', 'VISIBLE') = 'VISIBLE'
                    AND ($4::text IS NULL OR position(lower($4::text) IN lower(coalesce(art.doc->>'name', ''))) > 0 OR position(lower($4::text) IN lower(coalesce(art.doc->>'logicalName', ''))) > 0)
                    AND ($5::text[] IS NULL OR art.doc->>'artifactType' = ANY($5::text[])) AND ($6::text IS NULL OR art.doc->>'conversationId' = $6)
                    AND ($7::bigint IS NULL OR (rec.doc->>'createdAtTimestamp')::numeric >= $7) AND ($8::bigint IS NULL OR (rec.doc->>'createdAtTimestamp')::numeric <= $8))
                SELECT g.rdoc, g.adoc, g.pdoc FROM g WHERE ($9::int = 0 OR ($9::int = 1 AND g.owned) OR ($9::int = 2 AND NOT g.owned))
                ORDER BY (CASE WHEN $10::int = 1 AND $11::boolean THEN g.adoc->>'name' END) COLLATE "C" ASC, (CASE WHEN $10::int = 1 AND NOT $11::boolean THEN g.adoc->>'name' END) COLLATE "C" DESC,
                         (CASE WHEN $10::int = 2 AND $11::boolean THEN g.rdoc->'createdAtTimestamp' END) ASC, (CASE WHEN $10::int = 2 AND NOT $11::boolean THEN g.rdoc->'createdAtTimestamp' END) DESC,
                         (CASE WHEN $10::int = 3 AND $11::boolean THEN g.rdoc->'updatedAtTimestamp' END) ASC, (CASE WHEN $10::int = 3 AND NOT $11::boolean THEN g.rdoc->'updatedAtTimestamp' END) DESC,
                         (CASE WHEN $10::int = 4 AND $11::boolean THEN g.adoc->>'artifactType' END) COLLATE "C" ASC, (CASE WHEN $10::int = 4 AND NOT $11::boolean THEN g.adoc->>'artifactType' END) COLLATE "C" DESC, g.id
                OFFSET $12 LIMIT $13""", org, user_id, org_id, search or None, artifact_types, conversation_id or None, date_from or None, date_to or None, own, key,
                                  (sort_order or "").lower() == "asc", max(skip, 0), max(limit, 0))
            tallies = await tx.fetch("""/* pipeshub:graph.list_accessible_artifacts_counts */ WITH g AS (
                  SELECT rec.id, rec.doc AS rdoc, art.doc AS adoc, perm.doc AS pdoc, coalesce(perm.doc->>'role' = 'OWNER', false) AS owned
                  FROM graph.nodes u JOIN graph.edges perm ON perm.org_id = u.org_id AND perm.coll = 'permission' AND perm.from_coll = 'users' AND perm.from_id = u.id
                      AND perm.to_coll = 'records' AND perm.doc->>'type' = 'USER'
                    JOIN graph.nodes rec ON rec.org_id = u.org_id AND rec.coll = 'records' AND rec.id = perm.to_id
                    JOIN graph.edges te ON te.org_id = u.org_id AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = rec.id AND te.to_coll = 'artifacts'
                    JOIN graph.nodes art ON art.org_id = u.org_id AND art.coll = 'artifacts' AND art.id = te.to_id
                  WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $2 AND rec.doc->>'orgId' = $3 AND rec.doc->>'recordType' = 'ARTIFACT'
                    AND coalesce((rec.doc->>'isDeleted')::boolean, false) = false AND art.doc->>'artifactType' <> 'TOOL_RESULT'
                    AND coalesce((art.doc->>'isTemporary')::boolean, false) = false AND coalesce(art.doc->>'visibility', 'VISIBLE') = 'VISIBLE'
                    AND ($4::text IS NULL OR position(lower($4::text) IN lower(coalesce(art.doc->>'name', ''))) > 0 OR position(lower($4::text) IN lower(coalesce(art.doc->>'logicalName', ''))) > 0)
                    AND ($5::text[] IS NULL OR art.doc->>'artifactType' = ANY($5::text[])) AND ($6::text IS NULL OR art.doc->>'conversationId' = $6)
                    AND ($7::bigint IS NULL OR (rec.doc->>'createdAtTimestamp')::numeric >= $7) AND ($8::bigint IS NULL OR (rec.doc->>'createdAtTimestamp')::numeric <= $8))
                SELECT g.owned, count(*) AS total FROM g GROUP BY g.owned""", org, user_id, org_id, search or None, artifact_types,
                                     conversation_id or None, date_from or None, date_to or None)
            counts = {"mine": 0, "shared": 0}
            for t in tallies:
                counts["mine" if t["owned"] else "shared"] += int(t["total"])
            return [_artifact(r["rdoc"], r["adoc"], r["pdoc"]) for r in rows], counts
        return await self._q(go, None)

    async def get_artifact_detail(self, user_id: str, org_id: str, artifact_id: str) -> dict | None:
        """One visible artifact the user holds a grant on, or None."""
        org = current_org_id()
        r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.get_artifact_detail */ SELECT rec.doc AS rdoc, art.doc AS adoc, perm.doc AS pdoc
            FROM graph.nodes u JOIN graph.edges perm ON perm.org_id = u.org_id AND perm.coll = 'permission' AND perm.from_coll = 'users' AND perm.from_id = u.id
                AND perm.to_coll = 'records' AND perm.to_id = $4 AND perm.doc->>'type' = 'USER'
              JOIN graph.nodes rec ON rec.org_id = u.org_id AND rec.coll = 'records' AND rec.id = perm.to_id
              JOIN graph.edges te ON te.org_id = u.org_id AND te.coll = 'isOfType' AND te.from_coll = 'records' AND te.from_id = rec.id AND te.to_coll = 'artifacts'
              JOIN graph.nodes art ON art.org_id = u.org_id AND art.coll = 'artifacts' AND art.id = te.to_id
            WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $2 AND rec.doc->>'orgId' = $3 AND rec.doc->>'recordType' = 'ARTIFACT'
              AND coalesce((rec.doc->>'isDeleted')::boolean, false) = false AND art.doc->>'artifactType' <> 'TOOL_RESULT'
              AND coalesce((art.doc->>'isTemporary')::boolean, false) = false AND coalesce(art.doc->>'visibility', 'VISIBLE') = 'VISIBLE' LIMIT 1""",
                                                  org, user_id, org_id, artifact_id), None)
        return _artifact(r["rdoc"], r["adoc"], r["pdoc"]) if r else None

    # ---- agent templates and the legacy hub app ---------------------------------------------------------------------
    async def get_template(self, template_id: str, user_id: str, transaction: str | None = None) -> dict | None:
        """The template with the user's access: their own USER grant first, else a grant to a team they belong to. A deleted template, no grant, a
        failed read: None."""
        org = current_org_id()
        try:
            r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.get_template */ SELECT t.doc, a.access, a.role, a.pid, a.created, a.updated FROM graph.nodes t,
                LATERAL (SELECT 'INDIVIDUAL' AS access, p.doc->>'role' AS role, p.doc->>'id' AS pid, p.doc->'createdAtTimestamp' AS created, p.doc->'updatedAtTimestamp' AS updated, 0 AS ord, 0 AS rk
                           FROM graph.nodes u JOIN graph.edges p ON p.org_id = u.org_id AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = u.id
                             AND p.to_coll = 'agentTemplates' AND p.to_id = t.id AND p.doc->>'type' = 'USER' AND p.doc->>'role' = ANY($4::text[])
                           WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $2
                         UNION ALL
                         SELECT 'TEAM', g.doc->>'role', g.doc->>'id', g.doc->'createdAtTimestamp', g.doc->'updatedAtTimestamp', 1,
                                CASE g.doc->>'role' WHEN 'OWNER' THEN 4 WHEN 'ORGANIZER' THEN 3 WHEN 'WRITER' THEN 2 WHEN 'READER' THEN 1 ELSE 0 END
                           FROM graph.nodes u JOIN graph.edges m ON m.org_id = u.org_id AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = u.id
                             AND m.to_coll = 'teams' AND m.doc->>'type' = 'USER'
                             JOIN graph.nodes tm ON tm.org_id = u.org_id AND tm.coll = 'teams' AND tm.id = m.to_id
                             JOIN graph.edges g ON g.org_id = u.org_id AND g.coll = 'permission' AND g.from_coll = 'teams' AND g.from_id = tm.id
                             AND g.to_coll = 'agentTemplates' AND g.to_id = t.id AND g.doc->>'type' = 'TEAM' AND g.doc->>'role' = ANY($4::text[])
                           WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $2
                         ORDER BY ord, rk DESC, pid LIMIT 1) a
                WHERE t.org_id = $1 AND t.coll = 'agentTemplates' AND t.id = $3 AND coalesce((t.doc->>'isDeleted')::boolean, false) = false""",
                                                      org, user_id, template_id, _TEMPLATE_ROLES), transaction)
        except Exception as e:
            self.logger.error("❌ Failed to get template access: %s", e)
            return None
        if not r:
            return None
        role = r["role"]
        return {**_arango(_doc(r["doc"]), _TEMPLATES), "access_type": r["access"], "user_role": role, "permission_id": r["pid"],
                "permission_created_at": _scalar(r["created"]), "permission_updated_at": _scalar(r["updated"]), "can_edit": role in ("OWNER", "WRITER", "ORGANIZER"),
                "can_delete": role == "OWNER", "can_share": role in ("OWNER", "ORGANIZER"), "can_view": True}

    async def clone_agent_template(self, template_id: str, transaction: str | None = None) -> str | None:
        """A copy of the template under a new key, active and not deleted, with no owner and no grants; None for a missing template."""
        current_org_id()
        try:
            template = await self.get_document(template_id, _TEMPLATES, transaction=transaction)
            if template is None:
                return None
            key, now = str(uuid.uuid4()), get_epoch_timestamp_in_ms()
            template.update({"_key": key, "id": key, "isActive": True, "isDeleted": False, "deletedAtTimestamp": None, "deletedByUserId": None,
                             "updatedAtTimestamp": now, "updatedByUserId": None, "createdAtTimestamp": now, "createdBy": None})
            return key if await self.batch_upsert_nodes([template], _TEMPLATES, transaction=transaction) else None
        except Exception as e:
            self.logger.error("❌ Failed to clone agent template: %s", e)
            return None

    async def delete_kb_hub_app(self, org_id: str) -> bool:
        """Delete the org's legacy hub app node with every edge on it, unless a belongsTo or permission edge still points at it (then False)."""
        org = current_org_id()
        hub = f"knowledgeBase_{org_id}"
        try:
            async def go(tx: Tx) -> bool:
                refs = await tx.fetchval("""/* pipeshub:graph.delete_kb_hub_app_refs */ SELECT count(*) FROM graph.edges e WHERE e.org_id = $1 AND e.coll IN ('belongsTo', 'permission')
                    AND e.to_coll = 'apps' AND e.to_id = $2""", org, hub)
                if refs:
                    self.logger.error(f"❌ Refusing to delete legacy KB hub app {hub}: still referenced by belongsTo/permission edges")
                    return False
                await tx.execute("""/* pipeshub:graph.delete_kb_hub_app_edges */ DELETE FROM graph.edges e WHERE e.org_id = $1
                    AND ((e.from_coll = 'apps' AND e.from_id = $2) OR (e.to_coll = 'apps' AND e.to_id = $2))""", org, hub)
                await tx.execute("/* pipeshub:graph.delete_kb_hub_app */ DELETE FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'apps' AND n.id = $2", org, hub)
                return True
            return await self._q(go, None, write=True)
        except Exception as e:
            self.logger.error(f"❌ Failed to delete legacy KB hub app for org {org_id}: {e}")
            return False


def _scalar(v: Any) -> Any:  # noqa: ANN401
    return json.loads(v) if isinstance(v, str) else v


def _doc_list(v: Any) -> list:  # noqa: ANN401
    return json.loads(v) if isinstance(v, str) else list(v or [])
