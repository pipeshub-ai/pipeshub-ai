"""Lookups of records (by address, key, path, thread, status), the listings of a record's descendants and of a group's records, the parent adjacency
for location trails, the move of a record into a group, and a few single-purpose writes.

Every method resolves the ambient org before anything else (and before any ``try``); every statement is a literal with bound parameters, a
variant being a bound flag or a second literal.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    FOLDER_MIME_TYPES,
    CollectionNames,
    Connectors,
    ProgressStatus,
)
from app.core import org_context
from app.core.org_context import current_or_system, current_org_id
from app.exceptions.graph_db_exceptions import (
    GraphQueryError,
    PermissionVerificationUnavailableError,
)
from app.models.entities import FileRecord, Record
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.typed_record import typed_record
from app.services.graph_db.common.utils import (
    CANONICAL_PARENT_RELATION_TYPES,
    build_connector_stats_response,
    jira_issue_browse_url_regex,
    record_status_field,
)
from app.services.graph_db.postgres.base import (
    _LINK_COLLS,
    _VALIDATOR,
    PgBase,
    _arango,
    _doc,
    _j,
    _node,
)
from app.services.graph_db.postgres.hierarchy import _EDGE_COLLS, _remove_records
from app.services.graph_db.postgres.records import _rec, _uniq, _vis
from app.services.graph_db.postgres.trash import _sweep_edges
from app.services.graph_db.vector_membership_queries import PAGE_RECORD_FIELDS
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx


def _typed(rec_raw: Any, typ_raw: Any) -> Record:  # noqa: ANN401
    """The typed record; a record with no type document is the base record (the Neo4j provider would drop the whole listing)."""
    rec = _rec(rec_raw)
    if not typ_raw:
        return Record.from_arango_base_record(rec)
    return typed_record(rec, _arango(_doc(typ_raw), ""))


def _doc_list(v: Any) -> list:  # noqa: ANN401
    return json.loads(v) if isinstance(v, str) else list(v or [])


def _vis_code(v: RecordVisibility) -> int:
    return _vis(v)


class LookupsMixin(PgBase):
    async def _lrows(self, sql: str, *args: Any, transaction: str | None = None, write: bool = False) -> list:  # noqa: ANN401
        return await self._q(lambda tx: tx.fetch(sql, *args), transaction, write=write)

    async def get_record_by_weburl(self, weburl: str, org_id: str | None = None, transaction: str | None = None) -> Record | None:
        """By exact web URL; LINK records are skipped."""
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_record_by_weburl */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
                AND n.doc->>'webUrl' = $2 AND ($3::text IS NULL OR n.doc->>'orgId' = $3) AND n.doc->>'recordType' <> 'LINK' AND coalesce((n.doc->>'isDeleted')::boolean, false) = false LIMIT 1""",
                                org, weburl, org_id or None, transaction=transaction)
            return Record.from_arango_base_record(_arango(d, "records")) if d else None
        except Exception as e:
            self.logger.error(f"❌ Get record by web URL failed: {e}")
            return None

    async def get_record_by_issue_key(self, connector_id: str, issue_key: str, transaction: str | None = None) -> Record | None:
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_record_by_issue_key */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
                AND n.doc->>'connectorId' = $2 AND n.doc->>'recordType' = 'TICKET' AND n.doc->>'webUrl' ~ $3 LIMIT 1""",
                                org, connector_id, jira_issue_browse_url_regex(issue_key), transaction=transaction)
            return Record.from_arango_base_record(_arango(d, "records")) if d else None
        except Exception as e:
            self.logger.error(f"❌ Failed to retrieve record for issue key {connector_id} {issue_key}: {e}")
            return None

    async def get_record_by_conversation_index(self, connector_id: str, conversation_index: str, thread_id: str, org_id: str, user_id: str,
                                               transaction: str | None = None) -> Record | None:
        """A mail record of this thread the user owns (a permission edge of role OWNER on it or on an ancestor it inherits from, up to 8 hops)."""
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_record_by_conversation_index */ WITH RECURSIVE m AS (
                  SELECT r.id, r.doc FROM graph.nodes r JOIN graph.edges e ON e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id
                  JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = 'mails' AND t.id = e.to_id
                  WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'connectorId' = $2 AND r.doc->>'orgId' = $3
                    AND t.doc->>'conversationIndex' = $4 AND t.doc->>'threadId' = $5),
                up(rid, cur_coll, cur, d) AS (
                  SELECT m.id, 'records'::text, m.id, 0 FROM m
                  UNION
                  SELECT up.rid, i.to_coll, i.to_id, up.d + 1 FROM up JOIN graph.edges i ON i.org_id = $1 AND i.coll = 'inheritPermissions'
                    AND i.from_coll = up.cur_coll AND i.from_id = up.cur WHERE up.d < 8)
                SELECT m.doc FROM m WHERE EXISTS (SELECT 1 FROM up JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.to_coll = up.cur_coll AND p.to_id = up.cur
                    AND p.from_coll = 'users' AND p.doc->>'role' = 'OWNER' AND p.doc->>'type' = 'USER'
                  JOIN graph.nodes u ON u.org_id = $1 AND u.coll = 'users' AND u.id = p.from_id AND u.doc->>'userId' = $6 WHERE up.rid = m.id) LIMIT 1""",
                                org, connector_id, org_id, conversation_index, thread_id, user_id, transaction=transaction)
            return Record.from_arango_base_record(_arango(d, "records")) if d else None
        except Exception as e:
            self.logger.error(f"❌ Get record by conversation index failed: {e}")
            return None

    async def get_record_by_path(self, connector_id: str, path: list[str], external_record_group_id: str, transaction: str | None = None) -> dict | None:
        """The record at ``path`` (folder names from the group's top-level folder down) in a record group of the connector."""
        if not path:
            return None
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_record_by_path */ WITH RECURSIVE w(id, i) AS (
                  SELECT n.id, 1 FROM graph.nodes g JOIN graph.edges b ON b.org_id = g.org_id AND b.coll = 'belongsTo' AND b.to_coll = 'recordGroups' AND b.to_id = g.id
                  JOIN graph.nodes n ON n.org_id = b.org_id AND n.coll = 'records' AND n.id = b.from_id
                  WHERE g.org_id = $1 AND g.coll = 'recordGroups' AND g.doc->>'externalGroupId' = $3
                    AND EXISTS (WITH RECURSIVE up(coll, id, d) AS (SELECT 'recordGroups'::text, g.id, 0
                                  UNION SELECT u.to_coll, u.to_id, up.d + 1 FROM up JOIN graph.edges u ON u.org_id = $1 AND u.coll = 'belongsTo' AND u.from_coll = up.coll
                                    AND u.from_id = up.id WHERE up.d < 20)
                                SELECT 1 FROM up WHERE up.coll = 'apps' AND up.id = $2 AND up.d >= 1)
                    AND n.doc->>'recordName' = ($4::text[])[1] AND n.doc->>'externalParentId' IS NULL
                    AND n.doc->>'recordType' = 'FILE' AND n.doc->>'mimeType' = 'text/directory'
                  UNION ALL
                  SELECT c.id, w.i + 1 FROM w JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = 'records' AND e.from_id = w.id
                    AND e.doc->>'relationshipType' = 'PARENT_CHILD'
                  JOIN graph.nodes c ON c.org_id = $1 AND c.coll = 'records' AND c.id = e.to_id
                  WHERE w.i < cardinality($4::text[]) AND c.doc->>'recordName' = ($4::text[])[w.i + 1])
                SELECT n.doc FROM w JOIN graph.nodes n ON n.org_id = $1 AND n.coll = 'records' AND n.id = w.id WHERE w.i = cardinality($4::text[]) LIMIT 1""",
                                org, connector_id, external_record_group_id, list(path), transaction=transaction)
            return _arango(d, "records") if d else None
        except Exception as e:
            self.logger.error(f"❌ Get record by path failed: {e}")
            raise GraphQueryError(f"Could not look up the record at path {path}: {e}") from e

    async def get_records_by_file_path(self, connector_id: str, path: str, transaction: str | None = None) -> list[Record]:
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.get_records_by_file_path */ SELECT r.doc FROM graph.nodes f JOIN graph.nodes r ON r.org_id = f.org_id AND r.coll = 'records'
                AND r.id = f.id WHERE f.org_id = $1 AND f.coll = 'files' AND f.doc->>'path' = $2 AND r.doc->>'connectorId' = $3""",
                                    org, path, connector_id, transaction=transaction)
            return [Record.from_arango_base_record(_rec(r["doc"])) for r in rows]
        except Exception as e:
            self.logger.error(f"❌ Get records by file path failed: {e}")
            raise GraphQueryError(f"Could not look up records at path {path}: {e}") from e

    async def get_records_by_record_type(self, connector_id: str, record_type: str, transaction: str | None = None) -> list[Record]:
        rows = await self._lrows("""/* pipeshub:graph.get_records_by_record_type */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'records'
            AND doc->>'connectorId' = $2 AND doc->>'recordType' = $3""", current_org_id(), connector_id, record_type, transaction=transaction)
        return [Record.from_arango_base_record(_rec(r["doc"])) for r in rows]

    async def get_records_by_status(self, org_id: str, connector_id: str, status_filters: list[str] | None, limit: int | None = None, offset: int = 0,
                                    transaction: str | None = None, record_group_id: str | None = None, is_placeholder: bool | None = None,
                                    after_key: str | None = None, exclude_statuses: list[str] | None = None,
                                    visibility: RecordVisibility = RecordVisibility.LIVE, status_field: str = "indexingStatus") -> list[Record]:
        """Records by status (None or empty: any), keyset-paged by ``after_key`` or by offset; a read that fails raises GraphQueryError."""
        attr = record_status_field(status_field)      # a known field name, bound as a parameter
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.get_records_by_status */ SELECT r.doc AS rec, ty.doc AS typ FROM graph.nodes r JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'orgId' = $2 AND r.doc->>'connectorId' = $3
                  AND (coalesce(cardinality($4::text[]), 0) = 0 OR r.doc->>$11::text = ANY($4::text[])) AND ($12::int = 0 OR coalesce((r.doc->>'isDeleted')::boolean, false) = ($12::int = 2))
                  AND ($5::text IS NULL OR r.doc->>'recordGroupId' = $5)
                  AND ($6::boolean IS NULL OR coalesce((r.doc->>'isPlaceholder')::boolean, false) = $6)
                  AND ($7::text[] IS NULL OR NOT coalesce(r.doc->>'indexingStatus', '') = ANY($7::text[]))
                  AND ($8::text IS NULL OR r.id > $8)
                ORDER BY r.id OFFSET $9 LIMIT $10""", org, org_id, connector_id, status_filters, record_group_id, is_placeholder,
                                    exclude_statuses, after_key, offset if limit is not None else 0, limit, attr, _vis_code(visibility), transaction=transaction)
        except Exception as e:
            self.logger.error(f"❌ Get records by status failed: {e}")
            raise GraphQueryError(f"Could not list records for connector {connector_id}: {e}") from e
        return [_typed(r["rec"], r["typ"]) for r in rows]

    async def get_records_by_weburls(self, weburls: list[str], org_id: str, per_address: int = 2, transaction: str | None = None) -> list[dict[str, Any]]:
        if not weburls or not org_id:
            return []
        rows = await self._lrows("""/* pipeshub:graph.get_records_by_weburls */ SELECT id, "recordType", "connectorName", "webUrl", "isDependentNode" FROM (
              SELECT n.id, n.doc->>'recordType' AS "recordType", n.doc->>'connectorName' AS "connectorName", n.doc->>'webUrl' AS "webUrl",
                     coalesce((n.doc->>'isDependentNode')::boolean, false) AS "isDependentNode",
                     row_number() OVER (PARTITION BY n.doc->>'webUrl' ORDER BY coalesce((n.doc->>'isDependentNode')::boolean, false), n.id) AS rn
              FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'webUrl' = ANY($2::text[]) AND n.doc->>'orgId' = $3
                AND n.doc->>'recordType' <> 'LINK' AND NOT coalesce((n.doc->>'isPlaceholder')::boolean, false) AND coalesce((n.doc->>'isDeleted')::boolean, false) = false) q
            WHERE rn <= $4""", current_org_id(), _uniq(weburls), org_id, per_address, transaction=transaction)
        return [dict(r) for r in rows]

    async def get_records_linking_to_weburls(self, weburls: list[str], org_id: str, connector_names: list[str], limit: int = 100, after: str | None = None,
                                             transaction: str | None = None) -> list[str]:
        if not weburls or not org_id or not connector_names:
            return []
        rows = await self._lrows("""/* pipeshub:graph.get_records_linking_to_weburls */ SELECT DISTINCT h.id FROM graph.nodes link
            JOIN graph.edges e ON e.org_id = link.org_id AND e.coll = 'nodeRelations' AND e.to_coll = 'records' AND e.to_id = link.id
              AND e.doc->>'relationshipType' = 'PARENT_CHILD' AND e.from_coll = 'records'
            JOIN graph.nodes h ON h.org_id = e.org_id AND h.coll = 'records' AND h.id = e.from_id
            WHERE link.org_id = $1 AND link.coll = 'records' AND link.doc->>'webUrl' = ANY($2::text[]) AND link.doc->>'orgId' = $3
              AND link.doc->>'recordType' = 'LINK' AND link.doc->>'connectorName' = ANY($4::text[]) AND coalesce((link.doc->>'isDeleted')::boolean, false) = false
              AND h.doc->>'orgId' = $3 AND coalesce((h.doc->>'isDeleted')::boolean, false) = false AND ($5::text IS NULL OR h.id > $5)
            ORDER BY h.id LIMIT $6""", current_org_id(), list(weburls), org_id, list(connector_names), after, limit, transaction=transaction)
        return [r["id"] for r in rows]

    async def get_issue_sites(self, org_id: str, connector_names: list[str], limit: int | None = None, transaction: str | None = None) -> list[str]:
        if not org_id or not connector_names:
            return []
        rows = await self._lrows("""/* pipeshub:graph.get_issue_sites */ SELECT site FROM (
              SELECT DISTINCT (regexp_match(n.doc->>'webUrl', '^(.*)/browse/'))[1] AS site FROM graph.nodes n
              WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'orgId' = $2 AND n.doc->>'recordType' = 'TICKET'
                AND n.doc->>'connectorName' = ANY($3::text[]) AND n.doc->>'webUrl' LIKE '%/browse/%' AND coalesce((n.doc->>'isDeleted')::boolean, false) = false) q
            WHERE site <> '' ORDER BY site LIMIT $4""", current_org_id(), org_id, list(connector_names), limit, transaction=transaction)
        return [r["site"] for r in rows if r["site"]]

    async def find_duplicate_records(self, record_key: str, md5_checksum: str, org_id: str, record_type: str | None = None, size_in_bytes: int | None = None,
                                     transaction: str | None = None) -> list[dict]:
        """Live records of the org with the same md5 (any connector), other than ``record_key``."""
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.find_duplicate_records */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
                AND n.doc->>'md5Checksum' = $2 AND n.id <> $3 AND n.doc->>'orgId' = $4 AND coalesce((n.doc->>'isDeleted')::boolean, false) = false
                AND ($5::text IS NULL OR n.doc->>'recordType' = $5) AND ($6::bigint IS NULL OR (n.doc->>'sizeInBytes')::bigint = $6)""",
                                    org, md5_checksum, record_key, org_id, record_type, size_in_bytes, transaction=transaction)
            return [_rec(r["doc"]) for r in rows]
        except Exception as e:
            self.logger.error(f"❌ Error finding duplicate records: {e}")
            return []

    async def get_records_pending_duplicate_reconcile(self, due_before_ms: int, limit: int, transaction: str | None = None) -> list[dict]:
        """Under an org context that org's rows; under a system context (the recovery pass) every organisation's, each row carrying its ``orgId``."""
        current_or_system()
        out: list[dict] = []
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                rows = await self._lrows("""/* pipeshub:graph.get_records_pending_duplicate_reconcile */ SELECT n.id AS "_key", n.doc->'duplicateReconcileAttempts' AS a,
                      n.doc->'duplicateReconcileDueAt' AS d FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
                      AND coalesce((n.doc->>'duplicateReconcilePending')::boolean, false) AND coalesce((n.doc->>'duplicateReconcileDueAt')::bigint, 0) < $2 AND coalesce((n.doc->>'isDeleted')::boolean, false) = false
                    ORDER BY n.id LIMIT $3""", org, due_before_ms, max(1, int(limit)), transaction=transaction)
                out += [{"_key": r["_key"], "orgId": org, "duplicateReconcileAttempts": json.loads(r["a"]) if r["a"] else None,
                         "duplicateReconcileDueAt": json.loads(r["d"]) if r["d"] else None} for r in rows]
        self._raise_failed(errors)
        return sorted(out, key=lambda r: r["_key"])[:max(1, int(limit))]

    async def find_file_by_name_in_parent(self, kb_id: str, file_name: str, mime_type: str, parent_folder_id: str | None = None,
                                          exclude_record_id: str | None = None, transaction: str | None = None) -> dict | None:
        """A live file of this name (case-insensitive) and mime type directly under a KB root (no hierarchy parent) or a folder."""
        org = current_org_id()
        try:
            if parent_folder_id is None:      # directly under the collection's root: no hierarchy parent
                rows = await self._lrows("""/* pipeshub:graph.find_file_by_name_in_root */ SELECT r.id, r.doc->>'recordName' AS name, coalesce(r.doc->>'mimeType', ty.doc->>'mimeType') AS mime
                    FROM graph.nodes r JOIN graph.edges e ON e.org_id = r.org_id AND e.coll = 'belongsTo' AND e.from_id = r.id AND e.to_id = $2
                    JOIN graph.nodes kb ON kb.org_id = e.org_id AND kb.coll = 'apps' AND kb.id = e.to_id AND kb.doc->>'type' = 'KB'
                    JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
                    WHERE r.org_id = $1 AND r.coll = 'records' AND coalesce((ty.doc->>'isFile')::boolean, false) AND coalesce((r.doc->>'isDeleted')::boolean, false) = false
                  AND lower(r.doc->>'recordName') = lower($3) AND coalesce(r.doc->>'mimeType', ty.doc->>'mimeType') = $4
                  AND ($5::text IS NULL OR r.id <> $5)
                      AND NOT EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = r.org_id AND p.coll = 'nodeRelations' AND p.to_id = r.id AND p.from_coll = 'records'
                        AND p.doc->>'relationshipType' = 'PARENT_CHILD') LIMIT 1""",
                                        org, kb_id, file_name, str(mime_type or ""), exclude_record_id, transaction=transaction)
            else:
                rows = await self._lrows("""/* pipeshub:graph.find_file_by_name_in_folder */ SELECT r.id, r.doc->>'recordName' AS name, coalesce(r.doc->>'mimeType', ty.doc->>'mimeType') AS mime
                    FROM graph.nodes r JOIN graph.edges e ON e.org_id = r.org_id AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT')
                      AND e.from_coll = 'records' AND e.to_id = r.id AND e.from_id = $2 AND e.doc->>'relationshipType' = 'PARENT_CHILD'
                    JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
                    WHERE r.org_id = $1 AND r.coll = 'records' AND coalesce((ty.doc->>'isFile')::boolean, false) AND coalesce((r.doc->>'isDeleted')::boolean, false) = false
                  AND lower(r.doc->>'recordName') = lower($3) AND coalesce(r.doc->>'mimeType', ty.doc->>'mimeType') = $4
                  AND ($5::text IS NULL OR r.id <> $5) LIMIT 1""", org, parent_folder_id, file_name, str(mime_type or ""), exclude_record_id, transaction=transaction)
            return {"_key": rows[0]["id"], "name": rows[0]["name"], "mimeType": rows[0]["mime"]} if rows else None
        except Exception as e:
            self.logger.error(f"❌ Failed to find file by name: {e}")
            return None

    async def find_slack_burst_record_by_ts(self, connector_id: str, channel_id: str, ts: str, transaction: str | None = None) -> Record | None:
        """The Slack burst message record of a channel whose startTs <= ts <= endTs."""
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.find_slack_burst_record_by_ts */ SELECT r.doc AS rec, m.doc AS typ FROM graph.nodes r
                JOIN graph.edges e ON e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id
                JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = 'messages' AND m.id = e.to_id
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'connectorId' = $2 AND r.doc->>'externalGroupId' = $3
                  AND m.doc->>'startTs' IS NOT NULL AND m.doc->>'endTs' IS NOT NULL AND m.doc->>'startTs' <= $4 AND m.doc->>'endTs' >= $4
                  AND m.doc->>'startTs' <> m.doc->>'endTs' LIMIT 1""", org, connector_id, channel_id, ts, transaction=transaction)
            return _typed(rows[0]["rec"], rows[0]["typ"]) if rows else None
        except Exception as e:
            self.logger.error(f"❌ find_slack_burst_record_by_ts({ts}) failed: {e}")
            return None

    async def get_related_node_field(self, node_id: str, edge_collection: str, target_collection: str, field_name: str, direction: str = "inbound",
                                     transaction: str | None = None) -> list[dict]:
        coll, key = node_id.split("/", 1) if "/" in node_id else ("", node_id)
        out = direction == "outbound"
        org = current_org_id()
        try:
            if out:
                rows = await self._lrows("""/* pipeshub:graph.get_related_node_field_out */ SELECT t.doc->$5::text AS v FROM graph.edges e
                    JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
                    WHERE e.org_id = $1 AND e.coll = $2 AND e.from_coll = $3 AND e.from_id = $4 AND t.coll = $6""",
                                        org, edge_collection, coll, key, field_name, target_collection, transaction=transaction)
            else:
                rows = await self._lrows("""/* pipeshub:graph.get_related_node_field_in */ SELECT t.doc->$5::text AS v FROM graph.edges e
                    JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.from_coll AND t.id = e.from_id
                    WHERE e.org_id = $1 AND e.coll = $2 AND e.to_coll = $3 AND e.to_id = $4 AND t.coll = $6""",
                                        org, edge_collection, coll, key, field_name, target_collection, transaction=transaction)
            return [json.loads(r["v"]) if isinstance(r["v"], str) else r["v"] for r in rows]
        except Exception as e:
            self.logger.error(f"❌ Get related node field failed: {e}")
            return []

    async def get_edges_from_node_with_target_name(self, node_id: str, edge_collection: str, transaction: str | None = None, *,
                                                   raise_on_error: bool = False) -> list[dict]:
        coll, key = node_id.split("/", 1) if "/" in node_id else ("records", node_id)
        colls = _LINK_COLLS if edge_collection == CollectionNames.NODE_RELATIONS.value else [edge_collection]
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.get_edges_from_node_with_target_name */ SELECT e.doc, e.to_coll, e.to_id,
                  coalesce(t.doc->>'name', t.doc->>'departmentName', t.doc->>'recordName', t.doc->>'groupName', e.to_id) AS name
                FROM graph.edges e LEFT JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
                WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = $4""", org, colls, coll, key, transaction=transaction)
        except Exception as e:
            self.logger.error(f"❌ Get edges from node failed: {e}")
            if raise_on_error:
                raise
            return []
        return [{**_doc(r["doc"]), "_from": node_id, "_to": f"{r['to_coll']}/{r['to_id']}", "name": r["name"]} for r in rows]

    async def get_child_record_ids_by_relation_type(self, record_id: str, relation_type: str, transaction: str | None = None,
                                                    limit: int | None = None) -> list[dict[str, Any]]:
        """Records with an edge TO this record of the given relation type (hierarchy or link), with the edge's foreign-key metadata; at most ``limit`` (all when None)."""
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.get_child_record_ids_by_relation_type */ SELECT e.from_id AS rid, e.doc FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.to_coll = 'records' AND e.to_id = $3 AND e.from_coll = 'records'
                  AND e.doc->>'relationshipType' = $4 LIMIT $5""", org, _LINK_COLLS, record_id, relation_type, limit, transaction=transaction)
            return [{"record_id": r["rid"], "childTable": _doc(r["doc"]).get("childTableName") or "", "sourceColumn": _doc(r["doc"]).get("sourceColumn") or "",
                     "targetColumn": _doc(r["doc"]).get("targetColumn") or ""} for r in rows]
        except Exception as e:
            self.logger.warning("Failed to get child record IDs by relation type for record %s: %s", record_id, e)
            return []

    async def get_parent_record_ids_by_relation_type(self, record_id: str, relation_type: str, transaction: str | None = None,
                                                     limit: int | None = None) -> list[dict[str, Any]]:
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.get_parent_record_ids_by_relation_type */ SELECT e.to_id AS rid, e.doc FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_coll = 'records' AND e.from_id = $3 AND e.to_coll = 'records'
                  AND e.doc->>'relationshipType' = $4 LIMIT $5""", org, _LINK_COLLS, record_id, relation_type, limit, transaction=transaction)
            return [{"record_id": r["rid"], "parentTable": _doc(r["doc"]).get("parentTableName") or "", "sourceColumn": _doc(r["doc"]).get("sourceColumn") or "",
                     "targetColumn": _doc(r["doc"]).get("targetColumn") or ""} for r in rows]
        except Exception as e:
            self.logger.warning("Failed to get parent record IDs by relation type for record %s: %s", record_id, e)
            return []

    async def get_address_links_batch(self, record_ids: list[str], org_id: str, limit_per_record: int | None = None,
                                      transaction: str | None = None) -> dict[str, dict[str, list[str]]]:
        """Per record: the live records that link to it (``mentioned_in``) and that it links to (``links_to``) by URL or key, ids sorted."""
        if not record_ids or not org_id:
            return {}
        anchors = _uniq(record_ids)
        rows = await self._lrows("""/* pipeshub:graph.get_address_links_batch */ SELECT a.anchor, x.dir, x.other FROM unnest($2::text[]) a(anchor)
            JOIN LATERAL (
              SELECT 'in' AS dir, e.from_id AS other FROM graph.edges e WHERE e.org_id = $1 AND (e.coll = 'recordLinks' OR (e.coll = 'nodeRelations' AND e.ekey <> '')) AND e.to_coll = 'records' AND e.to_id = a.anchor AND e.from_coll = 'records'
                AND e.doc->>'matchedBy' IN ('url', 'key')
              UNION ALL
              SELECT 'out', e.to_id FROM graph.edges e WHERE e.org_id = $1 AND (e.coll = 'recordLinks' OR (e.coll = 'nodeRelations' AND e.ekey <> '')) AND e.from_coll = 'records' AND e.from_id = a.anchor AND e.to_coll = 'records'
                AND e.doc->>'matchedBy' IN ('url', 'key')) x ON true
            JOIN graph.nodes n ON n.org_id = $1 AND n.coll = 'records' AND n.id = x.other AND n.doc->>'orgId' = $3 AND coalesce((n.doc->>'isDeleted')::boolean, false) = false""",
                                current_org_id(), anchors, org_id, transaction=transaction)
        out = {a: {"mentioned_in": set(), "links_to": set()} for a in anchors}
        for r in rows:
            out[r["anchor"]]["mentioned_in" if r["dir"] == "in" else "links_to"].add(r["other"])
        return {a: {k: sorted(v)[:limit_per_record] if limit_per_record is not None else sorted(v) for k, v in d.items()} for a, d in out.items()}

    async def get_linked_records(self, record_id: str, org_id: str, user_key: str, relation_types: list[str], limit: int = 10,
                                 transaction: str | None = None) -> list[dict[str, Any]]:
        """Cross-reference neighbours of a record the user may access: the first 500 are listed (by id), then filtered by the access check, then cut to
        ``limit``, as the Neo4j provider does (a record with more than 500 cross-references is read in part)."""
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.get_linked_records */ SELECT v.id, v.doc, x.rt,
                  EXISTS (SELECT 1 FROM graph.edges c WHERE c.org_id = v.org_id AND c.coll = 'nodeRelations' AND c.from_coll = 'records' AND c.from_id = v.id
                          AND c.to_coll = 'records' AND c.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT')) AS has_children
                FROM graph.nodes s JOIN LATERAL (
                  SELECT e.to_id AS other, e.doc->>'relationshipType' AS rt FROM graph.edges e WHERE e.org_id = s.org_id AND (e.coll = 'recordLinks' OR (e.coll = 'nodeRelations' AND e.ekey <> '')) AND e.from_coll = 'records'
                    AND e.from_id = s.id AND e.to_coll = 'records' AND e.doc->>'relationshipType' = ANY($4::text[])
                  UNION ALL
                  SELECT e.from_id, e.doc->>'relationshipType' FROM graph.edges e WHERE e.org_id = s.org_id AND (e.coll = 'recordLinks' OR (e.coll = 'nodeRelations' AND e.ekey <> '')) AND e.to_coll = 'records'
                    AND e.to_id = s.id AND e.from_coll = 'records' AND e.doc->>'relationshipType' = ANY($4::text[])) x ON true
                JOIN graph.nodes v ON v.org_id = s.org_id AND v.coll = 'records' AND v.id = x.other AND v.doc->>'orgId' = $3 AND coalesce((v.doc->>'isDeleted')::boolean, false) = false
                WHERE s.org_id = $1 AND s.coll = 'records' AND s.id = $2 AND s.doc->>'orgId' = $3 ORDER BY v.id LIMIT 500""",
                                    org, record_id, org_id, list(relation_types), transaction=transaction)
            items = []
            for r in rows:
                d = _doc(r["doc"])
                items.append({"id": r["id"], "name": d.get("recordName"), "recordType": d.get("recordType"), "connectorName": d.get("connectorName"),
                              "webUrl": d.get("webUrl"), "relationshipType": r["rt"], "hasChildren": bool(r["has_children"]),
                              "indexingStatus": d.get("indexingStatus"), "userRole": None})
            if not items:
                return []
            allowed = (await self.check_access(user_key, org_id, node_ids=[i["id"] for i in items], transaction=transaction)).node_ids
            return [i for i in items if i["id"] in allowed][:limit]
        except PermissionVerificationUnavailableError:
            raise
        except Exception as e:
            self.logger.error(f"❌ get_linked_records failed: {e}")
            return []

    async def get_record_parent_adjacency(self, record_ids: list[str], org_id: str, *, max_depth: int = 20, transaction: str | None = None) -> dict[str, Any]:
        """Upward parent adjacency for location trails: the records, their hierarchy ancestors, and the groups and apps over them."""
        if not record_ids:
            return {"nodes": {}, "parents": {}}
        depth = max(1, min(int(max_depth), 20))
        org = current_org_id()
        try:
            rows = await self._lrows("""/* pipeshub:graph.get_record_parent_adjacency_closure */ WITH RECURSIVE
                anc(id, d) AS (SELECT r.id, 0 FROM graph.nodes r WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[]) AND r.doc->>'orgId' = $3
                  UNION SELECT p.id, anc.d + 1 FROM anc JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.to_coll = 'records' AND e.to_id = anc.id AND e.from_coll = 'records'
                  JOIN graph.nodes p ON p.org_id = $1 AND p.coll = 'records' AND p.id = e.from_id AND p.doc->>'orgId' = $3 WHERE anc.d < $4),
                up(id, d) AS (SELECT id, 0 FROM anc
                  UNION SELECT e.to_id, up.d + 1 FROM up JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_id = up.id WHERE up.d < $4)
                SELECT DISTINCT up.id FROM up LEFT JOIN graph.nodes g ON g.org_id = $1 AND g.coll = 'recordGroups' AND g.id = up.id
                  LEFT JOIN graph.nodes a ON a.org_id = $1 AND a.coll = 'apps' AND a.id = up.id
                WHERE up.d = 0 OR (g.id IS NOT NULL AND g.doc->>'orgId' = $3) OR a.id IS NOT NULL""", org, list(record_ids), org_id, depth, transaction=transaction)
            keys = [r["id"] for r in rows]
            keys += [k for k in record_ids if k not in set(keys)]
            det = await self._lrows("""/* pipeshub:graph.get_record_parent_adjacency_detail */ SELECT k.id,
                  CASE WHEN rec.id IS NOT NULL THEN 'record' WHEN rg.id IS NOT NULL THEN 'recordGroup' ELSE 'app' END AS type,
                  CASE WHEN rec.id IS NOT NULL THEN coalesce(rec.doc->>'recordName', rec.doc->>'name', rec.doc->>'title', rec.id)
                       WHEN rg.id IS NOT NULL THEN coalesce(rg.doc->>'groupName', rg.doc->>'name', rg.id)
                       ELSE coalesce(app.doc->>'name', app.doc->>'appName', app.id) END AS name,
                  coalesce((SELECT jsonb_agg(DISTINCT jsonb_build_object('parent_id', e.from_id, 'parent_type', 'record', 'via', 'nodeRelations'))
                    FROM graph.edges e JOIN graph.nodes p ON p.org_id = $1 AND p.coll = 'records' AND p.id = e.from_id AND p.doc->>'orgId' = $3
                    WHERE rec.id IS NOT NULL AND e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.to_coll = 'records' AND e.to_id = rec.id AND e.from_coll = 'records'), '[]'::jsonb) AS rr,
                  coalesce((SELECT jsonb_agg(DISTINCT jsonb_build_object('parent_id', b.to_id, 'parent_type', CASE b.to_coll WHEN 'apps' THEN 'app' ELSE 'recordGroup' END, 'via', 'belongsTo'))
                    FROM graph.edges b LEFT JOIN graph.nodes pn ON pn.org_id = $1 AND pn.coll = b.to_coll AND pn.id = b.to_id
                    WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_id = k.id AND b.from_coll = coalesce(CASE WHEN rec.id IS NOT NULL THEN 'records' WHEN rg.id IS NOT NULL THEN 'recordGroups' END, 'apps')
                      AND ((b.to_coll = 'recordGroups' AND pn.doc->>'orgId' = $3) OR (b.to_coll = 'apps' AND (pn.doc->>'orgId' IS NULL OR pn.doc->>'orgId' = $3)))), '[]'::jsonb) AS bt
                FROM unnest($2::text[]) k(id)
                LEFT JOIN graph.nodes rec ON rec.org_id = $1 AND rec.coll = 'records' AND rec.id = k.id AND rec.doc->>'orgId' = $3
                LEFT JOIN graph.nodes rg ON rg.org_id = $1 AND rg.coll = 'recordGroups' AND rg.id = k.id AND rg.doc->>'orgId' = $3
                LEFT JOIN graph.nodes app ON app.org_id = $1 AND app.coll = 'apps' AND app.id = k.id AND (app.doc->>'orgId' IS NULL OR app.doc->>'orgId' = $3)
                WHERE rec.id IS NOT NULL OR rg.id IS NOT NULL OR app.id IS NOT NULL""", org, keys, org_id, transaction=transaction)
        except Exception as e:
            self.logger.warning("get_record_parent_adjacency failed: %s", e)
            return {"nodes": {}, "parents": {}}
        nodes: dict[str, Any] = {}
        parents: dict[str, Any] = {}
        for r in det:
            nodes[r["id"]] = {"id": r["id"], "type": r["type"], "name": r["name"] or r["id"]}
            parents[r["id"]] = [*_doc_list(r["rr"]), *_doc_list(r["bt"])]
        return {"nodes": nodes, "parents": parents}

    async def _permitted(self, user_key: str, org_id: str, ids: list[str], transaction: str | None) -> set[str]:
        out: set[str] = set()
        for i in range(0, len(ids), 5000):
            out |= set((await self.check_access(user_key, org_id, node_ids=ids[i:i + 5000], transaction=transaction)).node_ids)
        return out

    async def _records_page(self, ids: list[str], transaction: str | None) -> list[Record]:
        """The typed records of ``ids``, in that order."""
        if not ids:
            return []
        rows = await self._lrows("""/* pipeshub:graph.records_page */ SELECT r.id, r.doc AS rec, ty.doc AS typ FROM graph.nodes r LEFT JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[])""", current_org_id(), ids, transaction=transaction)
        by_id = {r["id"]: r for r in rows}
        return [_typed(by_id[i]["rec"], by_id[i]["typ"]) for i in ids if i in by_id]

    async def _page_ids(self, sql: str, args: tuple, user_key: str | None, org_id: str, limit: int | None, offset: int, transaction: str | None) -> list[str]:
        """``sql`` (a literal ending in ``OFFSET $n LIMIT $m``) selects the candidate ids in id order; without a user the page is cut in SQL, with one the
        whole candidate list is read, the user's access applied, then the page cut."""
        if not user_key:
            rows = await self._lrows(sql, *args, offset if limit is not None else 0, limit, transaction=transaction)
            return [r["id"] for r in rows]
        ids = [r["id"] for r in await self._lrows(sql, *args, 0, None, transaction=transaction)]
        allowed = await self._permitted(user_key, org_id, ids, transaction)
        ids = [i for i in ids if i in allowed]
        return ids[offset:offset + limit] if limit is not None else ids

    async def get_records_by_parent_record(self, parent_record_id: str, connector_id: str, org_id: str, depth: int, user_key: str | None = None,
                                           limit: int | None = None, offset: int = 0, transaction: str | None = None, status_filters: list[str] | None = None,
                                           after_key: str | None = None, exclude_statuses: list[str] | None = None) -> list[Record]:
        """The parent record (depth 0) and its hierarchy descendants down to ``depth`` (-1: unlimited), the user's readable ones, in id order."""
        org = current_org_id()
        try:
            if depth < -1:
                raise ValueError(f"Depth must be >= -1 (where -1 means unlimited). Got: {depth}")
            sql = """/* pipeshub:graph.get_records_by_parent_record */ WITH RECURSIVE w(id, d) AS (
                  SELECT s.id, 0 FROM graph.nodes s WHERE s.org_id = $1 AND s.coll = 'records' AND s.id = $2
                  UNION SELECT e.to_id, w.d + 1 FROM w JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records' AND e.from_id = w.id AND e.to_coll = 'records'
                  WHERE w.d < $3)
                SELECT DISTINCT r.id FROM w JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.id = w.id
                WHERE r.doc->>'connectorId' = $4 AND (r.doc->>'orgId' = $5 OR r.doc->>'orgId' IS NULL) AND coalesce((r.doc->>'isDeleted')::boolean, false) = false AND ($6::text[] IS NULL OR r.doc->>'indexingStatus' = ANY($6::text[])) AND ($7::text[] IS NULL OR NOT coalesce(r.doc->>'indexingStatus', '') = ANY($7::text[])) AND ($8::text IS NULL OR r.id > $8) ORDER BY r.id OFFSET $9 LIMIT $10"""
            ids = await self._page_ids(sql, (org, parent_record_id, 100 if depth == -1 else depth, connector_id, org_id, status_filters or None, exclude_statuses or None, after_key), user_key, org_id,
                                       limit, offset, transaction)
            return await self._records_page(ids, transaction)
        except PermissionVerificationUnavailableError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to retrieve records by parent record {parent_record_id}: {e}", exc_info=True)
            return []

    async def get_records_by_record_group(self, record_group_id: str, connector_id: str, org_id: str, depth: int, user_key: str | None = None,
                                          limit: int | None = None, offset: int = 0, transaction: str | None = None, status_filters: list[str] | None = None,
                                          after_key: str | None = None, exclude_statuses: list[str] | None = None) -> list[Record]:
        """The file records of a record group and of its nested groups down to ``depth`` (-1: unlimited) the user may read, in id order."""
        org = current_org_id()
        try:
            if depth < -1:
                raise ValueError(f"Depth must be >= -1 (where -1 means unlimited). Got: {depth}")
            groups = [r["id"] for r in await self._lrows("""/* pipeshub:graph.record_group_nested */ WITH RECURSIVE nest(id, d) AS (
                  SELECT g.id, 0 FROM graph.nodes g WHERE g.org_id = $1 AND g.coll = 'recordGroups' AND g.id = $2 AND g.doc->>'orgId' = $3
                  UNION SELECT c.id, nest.d + 1 FROM nest JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'belongsTo' AND e.to_coll = 'recordGroups' AND e.to_id = nest.id
                    AND e.from_coll = 'recordGroups' JOIN graph.nodes c ON c.org_id = $1 AND c.coll = 'recordGroups' AND c.id = e.from_id
                    AND (c.doc->>'orgId' = $3 OR c.doc->>'orgId' IS NULL) WHERE nest.d < $4)
                SELECT DISTINCT id FROM nest""", org, record_group_id, org_id, 100 if depth == -1 else depth, transaction=transaction)]
            if groups and user_key:
                allowed = await self._permitted(user_key, org_id, groups, transaction)
                groups = [g for g in groups if g in allowed]
            if not groups:
                return []
            # candidates, then only those reachable from a root (a candidate no other candidate is the hierarchy parent of): a record on a belongsTo cycle
            # with no root, or deeper than the reindex depth below one, is not returned, as in the Neo4j provider
            sql = """/* pipeshub:graph.get_records_by_record_group */ WITH RECURSIVE cand AS (
                  SELECT DISTINCT r.id FROM graph.edges b JOIN graph.nodes r ON r.org_id = b.org_id AND r.coll = 'records' AND r.id = b.from_id
                JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
                WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.to_coll = 'recordGroups' AND b.to_id = ANY($2::text[])
                  AND r.doc->>'connectorId' = $3 AND (r.doc->>'orgId' = $4 OR r.doc->>'orgId' IS NULL) AND coalesce((r.doc->>'isDeleted')::boolean, false) = false AND ($5::text[] IS NULL OR r.doc->>'indexingStatus' = ANY($5::text[])) AND ($6::text[] IS NULL OR NOT coalesce(r.doc->>'indexingStatus', '') = ANY($6::text[])) AND ($7::text IS NULL OR r.id > $7)
                  AND (ty.tcoll <> 'files' OR ty.doc->>'isFile' = 'true')),
                reach(id, d) AS (
                  SELECT c.id, 0 FROM cand c WHERE NOT EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'nodeRelations' AND p.from_coll = 'records'
                    AND p.to_coll = 'records' AND p.to_id = c.id AND p.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND p.from_id IN (SELECT id FROM cand))
                  UNION
                  SELECT e.to_id, reach.d + 1 FROM reach JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = 'records' AND e.from_id = reach.id
                    AND e.to_coll = 'records' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.to_id IN (SELECT id FROM cand) WHERE reach.d < 100)
                SELECT DISTINCT id FROM reach ORDER BY id OFFSET $8 LIMIT $9"""
            ids = await self._page_ids(sql, (org, groups, connector_id, org_id, status_filters or None, exclude_statuses or None, after_key), user_key, org_id, limit, offset, transaction)
            return await self._records_page(ids, transaction)
        except PermissionVerificationUnavailableError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to retrieve records by record group {record_group_id}: {e}", exc_info=True)
            return []

    async def link_record_to_group(self, record_id: str, record_group_id: str | None, *, inherit: bool | None, leaving_group_id: str | None = None,
                                   browse_root: bool | None = None, transaction: str | None = None) -> int:
        """Take a record out of ``leaving_group_id``, then put it in ``record_group_id``, in one transaction (a record that left its group never
        keeps the inherit edge to it). Returns the inherit edges removed. ``inherit`` None leaves that edge alone; ``browse_root`` True hangs the
        record off the group in the hierarchy, False removes that edge, None leaves it."""
        if not (leaving_group_id or record_group_id):
            return 0
        org = current_org_id()
        now = get_epoch_timestamp_in_ms()
        stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
        browse = {"relationshipType": "PARENT_CHILD", **stamps}
        if record_group_id:      # the edges it will write, checked before the first statement
            _VALIDATOR.validate_edge("belongsTo", stamps)
            if inherit:
                _VALIDATOR.validate_edge("inheritPermissions", stamps)
            if browse_root:
                _VALIDATOR.validate_edge("nodeRelations", browse)
        props = _j(stamps)

        async def link(tx: Tx, coll: str) -> None:
            await tx.execute("""/* pipeshub:graph.link_record_to_group_edge */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                SELECT $1, $2, 'records', r.id, 'recordGroups', g.id, '', $5::jsonb FROM graph.nodes r, graph.nodes g
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $3 AND g.org_id = $1 AND g.coll = 'recordGroups' AND g.id = $4
                ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = EXCLUDED.doc""", org, coll, record_id, record_group_id, props)

        async def go(tx: Tx) -> int:
            removed = 0
            if leaving_group_id:
                removed = await tx.fetchval("""/* pipeshub:graph.link_record_to_group_leave */ WITH d AS (DELETE FROM graph.edges WHERE org_id = $1
                    AND coll IN ('belongsTo', 'inheritPermissions') AND from_coll = 'records' AND from_id = $2 AND to_coll = 'recordGroups' AND to_id = $3
                    RETURNING coll) SELECT count(*) FILTER (WHERE coll = 'inheritPermissions') FROM d""", org, record_id, leaving_group_id)
                await tx.execute("""/* pipeshub:graph.link_record_to_group_leave_parent */ DELETE FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT')
                    AND e.from_coll = 'recordGroups' AND e.from_id = $2 AND e.to_coll = 'records' AND e.to_id = $3""", org, leaving_group_id, record_id)
            if record_group_id:
                await link(tx, "belongsTo")
                if inherit:
                    await link(tx, "inheritPermissions")
                elif inherit is not None:
                    removed += await tx.fetchval("""/* pipeshub:graph.link_record_to_group_unlink */ WITH d AS (DELETE FROM graph.edges WHERE org_id = $1
                        AND coll = 'inheritPermissions' AND from_coll = 'records' AND from_id = $2 AND to_coll = 'recordGroups' AND to_id = $3 RETURNING 1)
                        SELECT count(*) FROM d""", org, record_id, record_group_id)
                if browse_root:
                    await tx.execute("""/* pipeshub:graph.link_record_to_group_browse_root */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                        SELECT $1, 'nodeRelations', 'recordGroups', g.id, 'records', r.id, '', $4::jsonb FROM graph.nodes r, graph.nodes g
                        WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2 AND g.org_id = $1 AND g.coll = 'recordGroups' AND g.id = $3
                        ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = EXCLUDED.doc""", org, record_id, record_group_id,
                                     _j(browse))
                elif browse_root is False:
                    await tx.execute("""/* pipeshub:graph.link_record_to_group_unroot */ DELETE FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT')
                        AND e.from_coll = 'recordGroups' AND e.from_id = $3 AND e.to_coll = 'records' AND e.to_id = $2""", org, record_id, record_group_id)
            return removed
        return await self._q(go, transaction, write=True)

    async def batch_upsert_people(self, people: list, transaction: str | None = None) -> None:
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed read
        try:
            if people:
                await self.batch_upsert_nodes([p.to_arango_person() for p in people], CollectionNames.PEOPLE.value, transaction)
        except Exception as e:
            self.logger.error(f"❌ Batch upsert people failed: {e}")
            raise      # as the Neo4j and Arango providers: the caller must not add edges for people that were not written

    async def update_node_if_match(self, key: str, collection: str, node: dict, match_field: str, match_value: Any,  # noqa: ANN401
                                   transaction: str | None = None) -> bool:
        """Replace the document (as ``SET n = $node``) only while ``match_field`` still equals ``match_value``: one statement, so no overwrite
        slips between the check and the write."""
        org = current_org_id()
        if match_value is None:      # a node without the field is not 'null': as Neo4j's ``n[f] = null``, never a match
            return False
        doc = _node(node, collection)
        doc["id"] = key
        _VALIDATOR.validate_node(collection, doc)
        r = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.update_node_if_match */ WITH u AS (UPDATE graph.nodes n SET doc = jsonb_strip_nulls($5::jsonb)
            WHERE n.org_id = $1 AND n.coll = $2 AND n.id = $3 AND n.doc->$4::text = $6::jsonb RETURNING 1) SELECT count(*) FROM u""",
                                                 org, collection, key, match_field, _j(doc), _j(match_value)), transaction, write=True)
        return bool(r)

    async def remove_nodes_by_field(self, collection: str, field_name: str, *, field_value: str | int | bool | None, transaction: str | None = None) -> int:
        org = current_org_id()
        if field_value is None:      # as Neo4j's ``n.f = null``: never true, so nothing (a node lacking the field is not removed)
            return 0

        async def go(tx: Tx) -> int:
            ids = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.remove_nodes_by_field */ SELECT id FROM graph.nodes
                WHERE org_id = $1 AND coll = $2 AND doc->$3::text = $4::jsonb""", org, collection, field_name, _j(field_value))]
            if ids and collection == CollectionNames.RECORDS.value:      # a record goes with its type documents and blocks
                await _remove_records(tx, org, ids)
            elif ids:
                await _sweep_edges(tx, org, _EDGE_COLLS, [f"{collection}/{i}" for i in ids])
                await tx.execute("/* pipeshub:graph.remove_nodes_by_field_delete */ DELETE FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[])", org, collection, ids)
            return len(ids)
        return await self._q(go, transaction, write=True)

    async def get_file_record_by_id(
        self,
        record_id: str,
        transaction: str | None = None
    ) -> FileRecord | None:
        """Get a file record by its internal ID.

        None means the file or its record is not stored. A read that fails
        raises GraphQueryError: callers act on None by treating the file as gone.
        """
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed read
        try:
            file = await self.get_document(
                record_id, CollectionNames.FILES.value, transaction, raise_on_error=True
            )
            if not file:
                return None

            record = await self.get_document(
                record_id, CollectionNames.RECORDS.value, transaction, raise_on_error=True
            )
            if not record:
                return None

            return FileRecord.from_arango_record(file, record)

        except Exception as e:
            self.logger.error(f"❌ Get file record by ID failed: {str(e)}")
            raise GraphQueryError(f"Could not read file record {record_id}: {e}") from e

    async def get_record_owner_source_user_email(self, record_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> str | None:
        """The email of a user holding an OWNER grant of type USER on the record or on what it inherits permissions from (8 hops), the nearest first."""
        org = current_org_id()
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_record_owner_source_user_email */ WITH RECURSIVE anc(coll, id, depth) AS (
                  SELECT n.coll, n.id, 0 FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = $2
                  UNION
                  SELECT i.to_coll, i.to_id, a.depth + 1 FROM anc a JOIN graph.edges i ON i.org_id = $1 AND i.coll = 'inheritPermissions'
                    AND i.from_coll = a.coll AND i.from_id = a.id WHERE a.depth < 8)
                SELECT u.doc->>'email' AS email FROM anc JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.to_coll = anc.coll
                  AND p.to_id = anc.id AND p.doc->>'role' = 'OWNER' AND p.doc->>'type' = 'USER'
                  JOIN graph.nodes u ON u.org_id = $1 AND u.coll = 'users' AND u.id = p.from_id AND u.doc->>'email' IS NOT NULL ORDER BY anc.depth, u.id LIMIT 1""", org, record_id), transaction)
            return rows[0]["email"] if rows else None
        except Exception as e:
            self.logger.error("Get record owner email failed: %s", e)
            if raise_on_error:
                raise
            return None

    async def get_message_id_header_by_key(self, record_key: str, collection: str, transaction: str | None = None) -> str | None:
        org = current_org_id()
        try:
            return await self._q(lambda tx: tx.fetchval("/* pipeshub:graph.get_message_id_header_by_key */ SELECT doc->>'messageIdHeader' FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = $3",
                                                        org, collection, record_key), transaction)
        except Exception as e:
            self.logger.error("Failed to get messageIdHeader for record %s: %s", record_key, e)
            return None

    async def get_related_mails_by_message_id_header(self, message_id_header: str, exclude_key: str, collection: str, transaction: str | None = None) -> list[str]:
        """The keys of the other documents of the collection with this message id header. A read by a document field: there is no index for it."""
        org = current_org_id()
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_related_mails_by_message_id_header */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = $2
                AND doc->>'messageIdHeader' = $3 AND id <> $4 ORDER BY id""", org, collection, message_id_header, exclude_key), transaction)
            return [r["id"] for r in rows]
        except Exception as e:
            self.logger.error("Failed to get related mails by messageIdHeader: %s", e)
            return []


    # ---- keys by external id, linked records, failed records, documents by status, departments, and what is left of a connector's sync ----
    async def filter_linked_record_ids(self, record_ids: list[str], transaction: str | None = None) -> list[str]:
        """The records among ``record_ids`` that belong to a record group or have a canonical hierarchy parent, in the order asked."""
        org = current_org_id()
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return []
        found = {r["id"] for r in await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.filter_linked_record_ids */ SELECT r.id FROM graph.nodes r
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[])
              AND (EXISTS (SELECT 1 FROM graph.edges b JOIN graph.nodes g ON g.org_id = b.org_id AND g.coll = 'recordGroups' AND g.id = b.to_id
                           WHERE b.org_id = r.org_id AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = r.id AND b.to_coll = 'recordGroups')
                   OR EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = r.org_id AND p.coll = 'nodeRelations' AND p.to_coll = 'records' AND p.to_id = r.id
                              AND p.doc->>'relationshipType' = ANY($3::text[])))""", org, ids, list(CANONICAL_PARENT_RELATION_TYPES)), transaction)}
        return [i for i in ids if i in found]

    async def _key_by_external(self, external_id: str, connector_id: str | None, transaction: str | None) -> str | None:
        org = current_org_id()
        try:
            return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.key_by_external_id */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'records'
                AND doc->>'externalRecordId' = $2 AND ($3::text IS NULL OR doc->>'connectorId' = $3) ORDER BY id LIMIT 1""", org, external_id, connector_id), transaction)
        except Exception:
            self.logger.exception("Get record key by external id failed")
            return None

    async def get_key_by_external_file_id(self, external_file_id: str) -> str | None:
        """The key of a record of this org by external id, whatever its connector. With no connector named there is no index for it."""
        return await self._key_by_external(external_file_id, None, None)

    async def get_key_by_external_message_id(self, external_message_id: str, transaction: str | None = None) -> str | None:
        return await self._key_by_external(external_message_id, None, transaction)

    async def get_record_key_by_external_id(self, external_id: str, connector_id: str, transaction: str | None = None) -> str | None:
        return await self._key_by_external(external_id, connector_id, transaction)

    async def get_failed_records_by_org(self, org_id: str, connector_id: str, transaction: str | None = None) -> list[dict]:
        """The live records of the connector whose indexing failed; another org's id reads as none."""
        org = current_org_id()
        try:
            if org_id != org:
                return []
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_failed_records_by_org */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'records'
                AND doc->>'connectorId' = $2 AND doc->>'indexingStatus' = 'FAILED' AND doc->>'orgId' = $1 AND doc->>'isDeleted' IS DISTINCT FROM 'true' ORDER BY id""", org, connector_id), transaction)
            return [_arango(_doc(r["doc"]), "records") for r in rows]
        except Exception:
            self.logger.exception("Get failed records by org failed")
            return []

    async def get_documents_by_status(self, collection: str, status: str, transaction: str | None = None) -> list[dict]:
        """Every document of the collection in this org with this indexing status. A read by a document field of a whole collection: there is no index for it."""
        org = current_org_id()
        try:
            rows = await self._q(lambda tx: tx.fetch("/* pipeshub:graph.get_documents_by_status */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'indexingStatus' = $3 ORDER BY id",
                                                     org, collection, status), transaction)
            return [_arango(_doc(r["doc"]), collection) for r in rows]
        except Exception:
            self.logger.exception("Get documents by status failed")
            return []

    async def get_departments(self, org_id: str | None = None, transaction: str | None = None) -> list[str]:
        """The names of the departments with no org or the given one. Another org's id reads as none; a department without a name is left out."""
        org = current_org_id()
        try:
            if org_id and org_id != org:
                return []
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_departments */ SELECT doc->>'departmentName' AS name FROM graph.nodes WHERE org_id = $1 AND coll = 'departments'
                AND doc->>'departmentName' IS NOT NULL AND ($2::text IS NULL OR doc->>'orgId' IS NULL OR doc->>'orgId' = $2) ORDER BY id""", org, org_id or None), transaction)
            return [r["name"] for r in rows]
        except Exception:
            self.logger.exception("Get departments failed")
            return []

    async def delete_sync_points_by_connector_id(self, connector_id: str, transaction: str | None = None) -> tuple[int, bool]:
        """Deletes the connector's sync points: (how many, whether all went)."""
        current_org_id()
        try:
            return (await self.remove_nodes_by_field("syncPoints", "connectorId", field_value=connector_id, transaction=transaction), True)
        except Exception:
            self.logger.exception("Failed to delete the sync points of connector %s", connector_id)
            return (0, False)

    async def delete_connector_sync_edges(self, connector_id: str, transaction: str | None = None) -> tuple[int, bool]:
        """Deletes the edges a sync made around the connector's records, groups, roles and groups of users and its app, all or none; the nodes and type links stay."""
        current_org_id()
        try:
            node_ids = (await self._collect_connector_entities(connector_id, transaction)).get("all_node_ids") or []
            if not node_ids:
                return (0, True)
            n, failed = await self._delete_all_edges_for_nodes(transaction or "", node_ids, [
                "belongsTo", "nodeRelations", "permission", "inheritPermissions", "userAppRelation", "entityRelations", "anyone"])
            return (n, not failed)
        except Exception:
            self.logger.exception("Error deleting connector sync edges for %s", connector_id)
            return (0, False)

    async def reap_stale_external_app_relations(self, connector_id: str, transaction: str | None = None) -> int:
        """Drops the external-collaborator membership edges of the app whose grant is gone (a USER grant on a live record or group of the connector, directly or
        through a group, role or team), then the persons left with no edge at all; returns how many persons went. A member who is not flagged external is never touched."""
        org = current_org_id()

        async def go(tx: Tx) -> int:
            gone = await tx.fetch("""/* pipeshub:graph.reap_stale_external_edges */ DELETE FROM graph.edges d USING (
                  SELECT u.from_coll, u.from_id FROM graph.edges u WHERE u.org_id = $1 AND u.coll = 'userAppRelation' AND u.to_coll = 'apps' AND u.to_id = $2
                    AND u.doc->>'isExternalUser' = 'true'
                    AND NOT EXISTS (SELECT 1 FROM graph.edges g JOIN graph.nodes t ON t.org_id = g.org_id AND t.coll = g.to_coll AND t.id = g.to_id
                                    WHERE g.org_id = $1 AND g.coll = 'permission' AND g.from_coll = u.from_coll AND g.from_id = u.from_id AND g.doc->>'type' = 'USER'
                                      AND g.to_coll IN ('records', 'recordGroups') AND t.doc->>'connectorId' = $2 AND t.doc->>'isDeleted' IS DISTINCT FROM 'true')
                    AND NOT EXISTS (SELECT 1 FROM graph.edges m JOIN graph.edges g ON g.org_id = m.org_id AND g.coll = 'permission' AND g.from_coll = m.to_coll AND g.from_id = m.to_id
                                      AND g.to_coll IN ('records', 'recordGroups')
                                    JOIN graph.nodes t ON t.org_id = g.org_id AND t.coll = g.to_coll AND t.id = g.to_id
                                    WHERE m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = u.from_coll AND m.from_id = u.from_id AND m.doc->>'type' = 'USER'
                                      AND m.to_coll IN ('groups', 'roles', 'teams') AND t.doc->>'connectorId' = $2 AND t.doc->>'isDeleted' IS DISTINCT FROM 'true')) s
                WHERE d.org_id = $1 AND d.coll = 'userAppRelation' AND d.to_coll = 'apps' AND d.to_id = $2 AND d.from_coll = s.from_coll AND d.from_id = s.from_id
                RETURNING d.from_coll, d.from_id""", org, connector_id)
            people = sorted({r["from_id"] for r in gone if r["from_coll"] == "person"})
            if not people:
                return 0
            return await tx.fetchval("""/* pipeshub:graph.reap_stranded_persons */ WITH d AS (DELETE FROM graph.nodes p WHERE p.org_id = $1 AND p.coll = 'person' AND p.id = ANY($2::text[])
                AND NOT EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = p.org_id AND ((e.from_coll = 'person' AND e.from_id = p.id) OR (e.to_coll = 'person' AND e.to_id = p.id)))
                RETURNING 1) SELECT count(*) FROM d""", org, people)
        try:
            return await self._q(go, transaction, write=True)
        except Exception:
            self.logger.exception("Reap stale external app relations failed for %s", connector_id)
            return 0

    async def find_next_queued_duplicate(self, record_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> dict | None:
        """A queued, live record with the same md5 (and the same size when the reference has one) in the reference record's own org; none when the
        reference has no md5 or no org (the comparison is then null and matches nothing). Reads the md5 by document field, which no index covers: a scan of the org's records."""
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.find_next_queued_duplicate */ SELECT n.doc FROM graph.nodes r JOIN graph.nodes n ON n.org_id = r.org_id AND n.coll = 'records'
                AND n.doc->>'md5Checksum' = r.doc->>'md5Checksum' AND n.doc->>'orgId' = r.doc->>'orgId' AND n.id <> r.id AND n.doc->>'indexingStatus' = 'QUEUED'
                AND coalesce((n.doc->>'isDeleted')::boolean, false) = false AND (r.doc->>'sizeInBytes' IS NULL OR n.doc->>'sizeInBytes' = r.doc->>'sizeInBytes')
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2 ORDER BY n.id LIMIT 1""", org, record_id, transaction=transaction)
            return _arango(d, "records") if d else None
        except Exception as e:
            self.logger.error(f"❌ Failed to find next queued duplicate: {e}")
            if raise_on_error:
                raise
            return None

    async def get_connector_instances_with_filters(self, collection: str, scope: str | None = None, user_id: str | None = None, *, is_admin: bool = False,
                                                   search: str | None = None, page: int = 1, limit: int = 20, transaction: str | None = None) -> tuple[list[dict], int]:
        """One page of the instances the user may see, newest first, and the total. An admin sees the team's and their own; anyone else only their own (with no user: an admin sees the team's, anyone else nothing). ``search`` is a literal, case-insensitive "contains" on name, type and group (the Neo4j provider reads it as a regular expression)."""
        org = current_org_id()
        pattern = None if not search else "%" + search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        try:
            rows = await self._lrows("""/* pipeshub:graph.get_connector_instances_with_filters */ SELECT n.doc, count(*) OVER () AS total FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND ($3::text IS NULL OR n.doc->>'scope' = $3)
                  AND CASE WHEN $4 THEN (n.doc->>'scope' = 'team' OR n.doc->>'createdBy' = $5::text) ELSE n.doc->>'createdBy' = $5::text END
                  AND ($6::text IS NULL OR lower(n.doc->>'name') LIKE $6 OR lower(n.doc->>'type') LIKE $6 OR lower(n.doc->>'appGroup') LIKE $6)
                ORDER BY CASE WHEN jsonb_typeof(n.doc->'createdAtTimestamp') = 'number' THEN (n.doc->>'createdAtTimestamp')::numeric END DESC NULLS LAST, n.id OFFSET $7 LIMIT $8""",
                                  org, collection, scope or None, is_admin, user_id, pattern, (max(page, 1) - 1) * limit, limit, transaction=transaction)
            if rows:
                return [_arango(_doc(r["doc"]), collection) for r in rows], rows[0]["total"]
            total = await self._lrows("""/* pipeshub:graph.get_connector_instances_with_filters_total */ SELECT count(*) AS n FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND ($3::text IS NULL OR n.doc->>'scope' = $3)
                  AND CASE WHEN $4 THEN (n.doc->>'scope' = 'team' OR n.doc->>'createdBy' = $5::text) ELSE n.doc->>'createdBy' = $5::text END
                  AND ($6::text IS NULL OR lower(n.doc->>'name') LIKE $6 OR lower(n.doc->>'type') LIKE $6 OR lower(n.doc->>'appGroup') LIKE $6)""",
                                      org, collection, scope or None, is_admin, user_id, pattern, transaction=transaction)
            return [], total[0]["n"]
        except Exception as e:
            self.logger.error(f"❌ Failed to get connector instances with filters: {e}")
            return [], 0

    async def get_connector_stats(self, org_id: str, connector_id: str, transaction: str | None = None) -> dict:
        """Record counts of a collection (records that belong to its app) or a connector (records of the record groups that chain up to its app, up to 10 hops),
        by type, indexing, extraction and entities state; a record in several groups counts once; internal, placeholder, deleted and folder records stay out."""
        org = current_org_id()
        statuses = [s.value for s in ProgressStatus]
        try:
            app = await self._one("/* pipeshub:graph.get_connector_stats_app */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'apps' AND n.id = $2",
                                  org, connector_id, transaction=transaction)
            is_kb = bool(app) and app.get("type") == Connectors.KNOWLEDGE_BASE.value
            rows = [] if org_id != org else await self._lrows("""/* pipeshub:graph.get_connector_stats */ WITH RECURSIVE g(id, depth) AS (
                  SELECT e.from_id, 1 FROM graph.edges e WHERE e.org_id = $1 AND NOT $3 AND e.coll = 'belongsTo' AND e.from_coll = 'recordGroups'
                    AND e.to_coll = 'apps' AND e.to_id = $2
                  UNION SELECT e.from_id, g.depth + 1 FROM g JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'recordGroups'
                    AND e.to_coll = 'recordGroups' AND e.to_id = g.id WHERE g.depth < 10),
                r AS (SELECT DISTINCT e.from_id AS id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'records'
                    AND ((NOT $3 AND e.to_coll = 'recordGroups' AND e.to_id IN (SELECT id FROM g)) OR ($3 AND e.to_coll = 'apps' AND e.to_id = $2)))
                SELECT n.doc->>'recordType' AS "recordType", n.doc->>'indexingStatus' AS "indexingStatus", n.doc->>'extractionStatus' AS "extractionStatus",
                       s.doc->>'status' AS "entitiesStatus", count(*) AS cnt
                FROM r JOIN graph.nodes n ON n.org_id = $1 AND n.coll = 'records' AND n.id = r.id
                LEFT JOIN graph.nodes s ON s.org_id = $1 AND s.coll = 'stageStates' AND s.id = (n.doc->>'virtualRecordId') || ':' || (n.doc->>'contentRev') || ':entities'
                WHERE coalesce((n.doc->>'isInternal')::boolean, false) = false AND coalesce((n.doc->>'isPlaceholder')::boolean, false) = false
                  AND coalesce((n.doc->>'isDeleted')::boolean, false) = false AND NOT coalesce(n.doc->>'mimeType', '') = ANY($4::text[])
                GROUP BY 1, 2, 3, 4""", org, connector_id, is_kb, FOLDER_MIME_TYPES, transaction=transaction)
            result = build_connector_stats_response([dict(r) for r in rows], statuses, org_id, connector_id, origin="COLLECTION" if is_kb else "CONNECTOR")
            return {"success": True, "data": result}
        except Exception as e:
            self.logger.error(f"❌ Failed to get connector stats: {e}")
            return {"success": False, "message": str(e), "data": None}

    # ---- the vector membership backfill: the next app to walk, and its records a page at a time ------------------------------------------------
    async def get_app_needing_vector_membership_backfill(self, transaction: str | None = None) -> dict | None:
        """The first connector instance (lowest id, byte order) not marked backfilled and not being deleted: under an org context that org's, under a system
        context (the indexing loop) any org's, through the vetted cross-org read of instances. Neo4j picks an arbitrary one; the order makes the walk repeatable."""
        if current_or_system().kind == "system":
            apps = await self._system_apps_page(0, 1_000_000, None, None, None)      # shortcut: reads every instance to pick one; instances are few, a vetted filtered read when they are not
            return next((a for a in apps if a.get("vectorMembershipBackfilled") in (None, False) and (a.get("status") or "") != "DELETING"), None)
        d = await self._one("""/* pipeshub:graph.get_app_needing_vector_membership_backfill */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'apps'
            AND coalesce(n.doc->'vectorMembershipBackfilled', 'null'::jsonb) IN ('null'::jsonb, 'false'::jsonb) AND coalesce(n.doc->>'status', '') <> 'DELETING'
            ORDER BY n.id COLLATE "C" LIMIT 1""", current_org_id(), transaction=transaction)
        return _arango(d, "apps") if d else None

    async def page_records_for_vector_membership_backfill(self, connector_id: str, after_key: str | None, limit: int, transaction: str | None = None) -> list[dict]:
        """The connector's records after ``after_key`` in key order (byte order, as the cursor compares), as ``PAGE_RECORD_FIELDS``. A failure raises: an empty page
        reads as "finished" to the backfill, which would then mark the connector done having processed nothing. Under a system context the connector's own org is used."""
        n = max(1, int(limit))
        sql = """/* pipeshub:graph.page_records_for_vector_membership_backfill */ SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records'
            AND n.doc->>'connectorId' = $2 AND ($3::text IS NULL OR n.id COLLATE "C" > $3::text COLLATE "C") ORDER BY n.id COLLATE "C" LIMIT $4"""
        if current_or_system().kind != "system":
            rows = await self._lrows(sql, current_org_id(), connector_id, after_key or None, n, transaction=transaction)
        else:
            app = await self._system_read(lambda tx: tx.fetchrow("/* pipeshub:graph.system_app */ SELECT org_id FROM graph.system_app($1)", connector_id))
            if not app:
                return []
            holder = app[0]          # the org that holds the connector's app
            with org_context.for_org(holder, job="graph.vector_membership_backfill"):
                rows = await self._lrows(sql, holder, connector_id, after_key or None, n)
        return [{"_key": r["id"], **{f: _doc(r["doc"]).get(f) for f in PAGE_RECORD_FIELDS if f != "_key"}} for r in rows]

    async def _fetch_existing_file_names_in_parent(self, kb_id: str, parent_folder_id: str | None, transaction: str | None = None, *,
                                                   raise_on_error: bool = False) -> set[tuple[str, str]]:
        """``(lower name, mime type)`` of the live files directly under the folder, or at the root of the knowledge base (its records that have no parent
        record and are not folders). A failure answers an empty set unless ``raise_on_error``."""
        org = current_org_id()
        try:
            if parent_folder_id is None:
                rows = await self._lrows("""/* pipeshub:graph.existing_file_names_in_kb_root */ SELECT lower(r.doc->>'recordName') AS name_lower, m.mime AS mime
                    FROM graph.edges b JOIN graph.nodes kb ON kb.org_id = b.org_id AND kb.coll = 'apps' AND kb.id = b.to_id AND kb.doc->>'type' = 'KB'
                    JOIN graph.nodes r ON r.org_id = b.org_id AND r.coll = 'records' AND r.id = b.from_id
                    CROSS JOIN LATERAL (SELECT coalesce(r.doc->>'mimeType', (SELECT f.doc->>'mimeType' FROM graph.edges t JOIN graph.nodes f ON f.org_id = t.org_id
                        AND f.coll = 'files' AND f.id = t.to_id AND f.doc->>'isFile' = 'true' WHERE t.org_id = b.org_id AND t.coll = 'isOfType' AND t.from_coll = 'records'
                        AND t.from_id = r.id AND t.to_coll = 'files' ORDER BY f.id LIMIT 1)) AS mime) m
                    WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.to_coll = 'apps' AND b.to_id = $2
                      AND coalesce((r.doc->>'isDeleted')::boolean, false) = false AND NOT m.mime = ANY($3::text[])
                      AND NOT EXISTS (SELECT 1 FROM graph.edges p WHERE p.org_id = b.org_id AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD'
                        AND p.from_coll = 'records' AND p.to_coll = 'records' AND p.to_id = r.id)""", org, kb_id, FOLDER_MIME_TYPES, transaction=transaction)
            else:
                rows = await self._lrows("""/* pipeshub:graph.existing_file_names_in_folder */ SELECT lower(r.doc->>'recordName') AS name_lower,
                        coalesce(r.doc->>'mimeType', f.doc->>'mimeType') AS mime
                    FROM graph.edges p JOIN graph.nodes r ON r.org_id = p.org_id AND r.coll = 'records' AND r.id = p.to_id
                    JOIN graph.edges t ON t.org_id = p.org_id AND t.coll = 'isOfType' AND t.from_coll = 'records' AND t.from_id = r.id AND t.to_coll = 'files'
                    JOIN graph.nodes f ON f.org_id = p.org_id AND f.coll = 'files' AND f.id = t.to_id AND f.doc->>'isFile' = 'true'
                    WHERE p.org_id = $1 AND p.coll = 'nodeRelations' AND p.doc->>'relationshipType' = 'PARENT_CHILD' AND p.from_coll = 'records' AND p.from_id = $2
                      AND p.to_coll = 'records' AND coalesce((r.doc->>'isDeleted')::boolean, false) = false""", org, parent_folder_id, transaction=transaction)
            return {(r["name_lower"], str(r["mime"] or "")) for r in rows if r["name_lower"]}
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch existing file names: {e}")
            if raise_on_error:
                raise
            return set()
