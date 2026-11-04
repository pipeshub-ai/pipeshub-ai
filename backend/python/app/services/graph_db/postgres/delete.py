"""Deleting one record with its permission rules, its attachments and the event, and the unlink of what a full sync did not see.

A record goes with its type documents, blocks and every edge (``_delete_records_with_their_types``), in one transaction. Every method resolves the
ambient org before anything else (and before any ``try``); every statement is a literal with bound parameters.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DeleteSource,
    OriginTypes,
)
from app.core.org_context import current_org_id
from app.services.graph_db.common.full_sync_unlink import (
    FULL_SYNC_EDGE_COLLECTIONS,
    split_unseen_by_window,
    unseen_node_keys,
)
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    is_live_record,
)
from app.services.graph_db.common.utils import (
    deleted_record_taxonomy_refs,
    soft_delete_request_result,
)
from app.services.graph_db.postgres.base import PgBase, _doc
from app.services.graph_db.postgres.trash import _sweep_edges

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

_ORG_FIELD = "orgId"  # the org property of a graph document
# The roles that may delete a connector's record on a user's request, as on ArangoDB (Drive, Gmail, Outlook, Local FS); a connector not listed needs
# OWNER or WRITER, so a user who can only view a record never deletes it.
_DELETE_ROLES = {
    Connectors.GOOGLE_DRIVE.value: ("OWNER", "WRITER", "FILEORGANIZER"),
    Connectors.GOOGLE_MAIL.value: ("OWNER", "WRITER"),
    Connectors.OUTLOOK.value: ("OWNER",),
    Connectors.OUTLOOK_INDIVIDUAL.value: ("OWNER",),
    Connectors.LOCAL_FS.value: ("OWNER",),
}
_DEFAULT_DELETE_ROLES = ("OWNER", "WRITER")


class DeleteMixin(PgBase):
    async def _drows(self, sql: str, *args: Any, transaction: str | None = None, write: bool = False) -> list:  # noqa: ANN401
        return await self._q(lambda tx: tx.fetch(sql, *args), transaction, write=write)

    async def _direct_attachment_ids(self, record_id: str, org_id: str, transaction: str | None) -> list[str]:
        rows = await self._drows("""/* pipeshub:graph.records_direct_attachments */ SELECT DISTINCT e.to_id AS id FROM graph.edges e
            JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'records' AND a.id = e.to_id AND a.doc->>'orgId' = $3
            WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = 'records' AND e.from_id = $2 AND e.doc->>'relationshipType' = 'ATTACHMENT'""",
                                current_org_id(), record_id, org_id, transaction=transaction)
        return [r["id"] for r in rows]

    async def _attachments_to_delete(self, record_id: str, org_id: str, transaction: str | None) -> tuple[list[str], list[dict]]:
        """A record's direct ATTACHMENT children and their deleteRecord payloads, read before the delete (two reads for any number of them).

        Attachments already in the trash, from an earlier batch, go too: a restore refuses an item whose parent is in the trash, so with its mail
        gone for good one could never come back."""
        ids = await self._direct_attachment_ids(record_id, org_id, transaction)
        if not ids:
            return [], []
        rows = await self._drows("""/* pipeshub:graph.records_attachment_payload_docs */ SELECT r.doc AS rec, ty.doc AS typ FROM graph.nodes r LEFT JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[]) AND r.doc->>'virtualRecordId' IS NOT NULL""",
                                current_org_id(), ids, transaction=transaction)
        payloads = []
        for r in rows:
            rec = _doc(r["rec"])
            payload = await self._create_deleted_record_event_payload(rec, _doc(r["typ"]) if r["typ"] else None)
            if payload:
                payload["connectorName"], payload["origin"] = rec.get("connectorName"), rec.get("origin")
                payloads.append(payload)
        return ids, payloads

    async def _refuse_without_delete_role(self, record_id: str, record: dict, user_id: str, transaction: str | None) -> dict | None:
        """A refusal unless the user holds a deleting role on the record (granted to them or to a group of theirs); a role that cannot be read refuses."""
        user = await self.get_user_by_user_id(user_id)
        if not user:
            return {"success": False, "code": 404, "reason": f"User not found: {user_id}"}
        user_key = user.get("id") or user.get("_key")
        rows = await self._drows("""/* pipeshub:graph.delete_record_roles */ SELECT p.doc->>'role' AS role FROM graph.edges p
            WHERE p.org_id = $1 AND p.coll = 'permission' AND p.to_coll = 'records' AND p.to_id = $2
              AND ((p.from_coll = 'users' AND p.from_id = $3 AND p.doc->>'type' = 'USER')
                OR (p.doc->>'type' IN ('GROUP', 'ROLE') AND p.from_coll = 'groups' AND EXISTS (SELECT 1 FROM graph.edges b WHERE b.org_id = $1
                    AND b.coll = 'belongsTo' AND b.from_coll = 'users' AND b.from_id = $3 AND b.to_coll = 'groups' AND b.to_id = p.from_id
                    AND b.doc->>'entityType' = 'GROUP')))""", current_org_id(), record_id, user_key, transaction=transaction)
        allowed = _DELETE_ROLES.get(record.get("connectorName", ""), _DEFAULT_DELETE_ROLES)
        if not any(r["role"] in allowed for r in rows):
            return {"success": False, "code": 403, "reason": "User lacks permission to delete this record"}
        return None

    async def delete_record(
        self,
        record_id: str,
        user_id: str,
        org_id: str,
        transaction: str | None = None,
        *,
        soft_delete: bool = False,
        delete_source: DeleteSource = DeleteSource.USER,
    ) -> dict:
        """Main entry point for record deletion. KB records require OWNER or WRITER."""
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed delete
        try:
            # Get record to determine connector type
            record = await self.get_document(record_id, CollectionNames.RECORDS.value, transaction)
            # A record in another org is reported exactly like a missing one so IDs can't be probed.
            if not org_id or not record or record.get(_ORG_FIELD) != org_id:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record not found: {record_id}"
                }

            connector_name = record.get("connectorName", "")
            origin = record.get("origin", "")

            # KB records: require OWNER or WRITER (retired roles read as READER)
            is_kb_record = (
                origin == OriginTypes.UPLOAD.value
                or connector_name == Connectors.KNOWLEDGE_BASE.value
            )
            if is_kb_record:
                kb_context = await self._get_kb_context_for_record(record_id, transaction)
                if not kb_context:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Knowledge base context not found for record {record_id}",
                    }
                user = await self.get_user_by_user_id(user_id)
                if not user:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"User not found: {user_id}",
                    }
                user_key = user.get("id") or user.get("_key")
                user_role = await self.get_user_kb_permission(
                    kb_context.get("kb_id"), user_key, transaction
                )
                if user_role not in ["OWNER", "WRITER"]:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "User lacks permission to delete records",
                    }
                # A folder goes with its contents; deleting only the folder would
                # strand them without a hierarchy parent.
                if soft_delete:
                    result = await self.soft_delete_records(
                        [record_id],
                        record.get("connectorId") or "",
                        delete_source=DeleteSource(delete_source).value,
                        batch_id=str(uuid.uuid4()),
                        deleted_by_user_id=user_key if DeleteSource(delete_source) is DeleteSource.USER else None,
                        transaction=transaction,
                    )
                    return soft_delete_request_result(record_id, record, result)
                cascade = await self.delete_records_recursive(
                    [record_id], record.get("connectorId"), transaction=transaction,
                )
                if not cascade.get("success") or not cascade.get("successfully_deleted"):
                    return {
                        "success": False,
                        "code": cascade.get("code", 500),
                        "reason": cascade.get("reason") or f"Record {record_id} could not be deleted",
                    }
                return {
                    "success": True,
                    "record_id": record_id,
                    "message": "Record deleted successfully",
                    "eventData": cascade.get("eventData"),
                    "connectorId": record.get("connectorId"),
                    "orgId": record.get(_ORG_FIELD),
                    "isKb": True,
                }

            # A user's request needs a deleting role; a connector's own delete (a sync removing what the source removed) is not a user's.
            if DeleteSource(delete_source) is DeleteSource.USER and (refusal := await self._refuse_without_delete_role(record_id, record, user_id, transaction)):
                return refusal

            if soft_delete:
                deleted_by = None
                if DeleteSource(delete_source) is DeleteSource.USER:
                    deleter = await self.get_user_by_user_id(user_id) or {}
                    deleted_by = deleter.get("id") or deleter.get("_key")
                # What the hard delete removes: the record and its direct attachments, as on
                # ArangoDB. One statement marks them all with one batch, so a restore of the
                # mail brings its attachments back with it.
                trash_ids = [record_id]
                if is_live_record(record):
                    trash_ids += await self._direct_attachment_ids(record_id, org_id, transaction)
                result = await self.soft_delete_records(
                    trash_ids,
                    record.get("connectorId") or "",
                    delete_source=DeleteSource(delete_source).value,
                    batch_id=str(uuid.uuid4()),
                    deleted_by_user_id=deleted_by,
                    follow=(),
                    transaction=transaction,
                )
                return soft_delete_request_result(record_id, record, result)

            # Get file record for event publishing before deletion
            file_record = None
            try:
                file_record = await self.get_document(record_id, CollectionNames.FILES.value, transaction)
            except Exception as e:
                self.logger.debug(f"File record not found for record {record_id}: {e}")

            # A mail's attachments go with it, as on ArangoDB: left behind, they stay
            # searchable with nothing left to clean up their vectors.
            attachment_ids, attachment_payloads = await self._attachments_to_delete(
                record_id, org_id, transaction
            )
            taxonomy_refs = await deleted_record_taxonomy_refs(self, [record], self.logger)

            # Create event payload for router to publish
            event_data = None
            try:
                payload = await self._create_deleted_record_event_payload(record, file_record)
                if payload:
                    connector_name = record.get("connectorName", "")
                    origin = record.get("origin", "")

                    # Set connector and origin info
                    if connector_name:
                        payload["connectorName"] = connector_name
                    if origin:
                        payload["origin"] = origin
                    if refs := taxonomy_refs.get(record_id):
                        payload["taxonomyRefs"] = refs

                    event_data = {
                        "eventType": "deleteRecord",
                        "topic": "record-events",
                        "payload": payload,
                        "payloads": [payload, *attachment_payloads],
                    }
            except Exception as e:
                self.logger.error(f"❌ Failed to create deletion event payload: {str(e)}")
                event_data = None

            await self._delete_records_with_their_types([*attachment_ids, record_id], transaction)
            # Their children are left without a parent: the connector's scopes no longer describe its tree.
            if record.get("connectorId"):
                try:
                    await self.kh_scope_mark_changed(record["connectorId"])
                except Exception as e:
                    self.logger.warning(f"kh scope: could not mark {record['connectorId']} changed after a delete: {e}")

            return {
                "success": True,
                "record_id": record_id,
                "message": "Record deleted successfully",
                "attachments_deleted": len(attachment_ids),
                "eventData": event_data,
                # Lets the caller invalidate the right cache entry without
                # re-reading the record it just deleted.
                "connectorId": record.get("connectorId"),
                "orgId": record.get(_ORG_FIELD),
                "isKb": is_kb_record,
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to delete record {record_id}: {e}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Record deletion failed: {e}"
            }

    async def delete_record_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: str | None = None,
        *,
        soft_delete: bool = False,
    ) -> dict | None:
        """Delete a record by external ID; ``soft_delete`` trashes, as a CONNECTOR delete, what the hard delete removes."""
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed delete
        try:
            record = await self.get_record_by_external_id(
                connector_id,
                external_id,
                transaction,
                visibility=RecordVisibility.LIVE if soft_delete else RecordVisibility.ALL,
            )
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found for connector {connector_id}")
                return None

            if soft_delete:
                return await self.delete_record(
                    record.id, user_id, record.org_id, transaction,
                    soft_delete=True, delete_source=DeleteSource.CONNECTOR,
                )
            result = await self.delete_record(record.id, user_id, record.org_id, transaction, delete_source=DeleteSource.CONNECTOR)
            # Raised, as on ArangoDB, so the caller's transaction rolls back.
            if not result.get("success"):
                raise Exception(f"Deletion failed: {result.get('reason', 'Unknown error')}")
            return result

        except Exception as e:
            self.logger.error(f"❌ Delete record by external ID failed: {str(e)}")
            raise

    async def unlink_nodes_missed_by_full_sync(self, connector_id: str, seen: Mapping[str, Collection[str]], *, window_start_ms: int | None = None,
                                               windowed_group_types: Collection[str] = (), transaction: str | None = None) -> tuple[int, bool]:
        """Remove the edges of the nodes of ``connector_id`` that a full sync did not see (the nodes stay); outside its window only the grants.
        Answers ``(edges removed, ok)``; on ``False`` inside a caller's transaction the statement may have aborted it, so the caller must roll back."""
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed delete
        try:
            collected = await self._collect_connector_entities(connector_id, transaction)
            unseen = unseen_node_keys(collected, seen)
            record_fields: dict[str, dict] = {}
            group_fields: dict[str, dict] = {}
            if window_start_ms is not None:
                record_fields = await self._node_fields_by_id(CollectionNames.RECORDS.value, unseen[CollectionNames.RECORDS.value],
                                                              ("sourceCreatedAtTimestamp", "recordGroupId"), transaction)
                group_fields = await self._node_fields_by_id(CollectionNames.RECORD_GROUPS.value, unseen[CollectionNames.RECORD_GROUPS.value],
                                                             ("sourceCreatedAtTimestamp", "groupType"), transaction)
            unlink, outside = split_unseen_by_window(unseen, record_fields, group_fields, window_start_ms, windowed_group_types)
            unlinked, failed = await self._delete_all_edges_for_nodes(transaction or "", unlink, list(FULL_SYNC_EDGE_COLLECTIONS))
            # Outside the window the source was not read: keep the structure, drop the grants.
            ungranted, failed_grants = await self._delete_all_edges_for_nodes(transaction or "", outside, [CollectionNames.PERMISSION.value])
            if failed or failed_grants:
                self.logger.warning(f"Full sync of {connector_id}: could not remove the links of the {len(unlink)} node(s) it missed from {failed or []}, "
                                    f"or the grants of the {len(outside)} outside its window from {failed_grants or []}")
            return (unlinked + ungranted, not failed and not failed_grants)
        except Exception as e:
            self.logger.error(f"Error unlinking nodes missed by the full sync of {connector_id}: {e}", exc_info=True)
            return (0, False)

    async def _collect_connector_entities(self, connector_id: str, transaction: str | None = None, *, include_virtual_record_ids: bool = False) -> dict:
        """The ids of a connector's records, record groups, roles and groups."""
        rows = await self._drows("""/* pipeshub:graph.records_collect_connector_entities */ SELECT coll, array_agg(id) AS ids,
              CASE WHEN coll = 'records' THEN array_agg(DISTINCT doc->>'virtualRecordId') FILTER (WHERE doc->>'virtualRecordId' IS NOT NULL) END AS vrids
            FROM graph.nodes WHERE org_id = $1 AND coll IN ('records', 'recordGroups', 'roles', 'groups') AND doc->>'connectorId' = $2 GROUP BY coll""",
                                current_org_id(), connector_id, transaction=transaction)
        by = {r["coll"]: r for r in rows}
        ids = {c: list(by[c]["ids"]) if c in by else [] for c in ("records", "recordGroups", "roles", "groups")}
        return {"record_keys": ids["records"], "record_ids": ids["records"],
                "virtual_record_ids": list(by["records"]["vrids"] or []) if include_virtual_record_ids and "records" in by else [],
                "record_group_keys": ids["recordGroups"], "role_keys": ids["roles"], "group_keys": ids["groups"],
                "all_node_ids": [f"records/{i}" for i in ids["records"]] + [f"recordGroups/{i}" for i in ids["recordGroups"]]
                                + [f"roles/{i}" for i in ids["roles"]] + [f"groups/{i}" for i in ids["groups"]] + [f"apps/{connector_id}"]}

    async def _node_fields_by_id(self, collection: str, keys: list[str], fields: tuple[str, ...], transaction: str | None = None) -> dict[str, dict]:
        if not keys:
            return {}
        rows = await self._drows("/* pipeshub:graph.records_node_fields_by_id */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[])",
                                current_org_id(), collection, list(keys), transaction=transaction)
        return {r["id"]: {f: _doc(r["doc"]).get(f) for f in fields} for r in rows}

    async def _delete_all_edges_for_nodes(self, transaction: str, node_ids: list[str], edge_collections: list[str]) -> tuple[int, list[str]]:
        """Delete the edges of the given collections around ``'<coll>/<key>'`` nodes: all or none. (count, collections that failed)."""
        org = current_org_id()
        if not node_ids or not edge_collections:
            return (0, [])
        try:
            n = await self._q(lambda tx: _sweep_edges(tx, org, list(edge_collections), node_ids), transaction or None, write=True)
            return (n, [])
        except Exception as e:
            self.logger.error(f"❌ Failed to delete relationships: {e}")
            return (0, list(edge_collections))
