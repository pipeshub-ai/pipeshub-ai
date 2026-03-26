"""
Integration-style unit tests for Graph interface APIs using realistic connector scenarios.

Tests use a FakeGraphProvider that implements key IGraphDBProvider methods in-memory,
then run realistic connector scenarios against it to verify permission logic,
hierarchy traversal, deduplication, connector lifecycle, and cross-connector access.

Scenarios covered:
1. Google Drive Connector Setup (20+ tests)
2. Jira Connector with Teams (15+ tests)
3. Permission Inheritance (15+ tests)
4. Record Deduplication (10+ tests)
5. Connector Lifecycle (10+ tests)
6. Multi-Connector Access (10+ tests)
7. Knowledge Base Management (10+ tests)
8. Record Group Permissions (10+ tests)
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Collection name constants (mirror CollectionNames enum values)
# ---------------------------------------------------------------------------

RECORDS = "records"
RECORD_GROUPS = "recordGroups"
USERS = "users"
GROUPS = "groups"
ROLES = "roles"
ORGS = "organizations"
APPS = "apps"
FILES = "files"
TICKETS = "tickets"
ANYONE = "anyone"
TEAMS = "teams"
SYNC_POINTS = "syncPoints"
KNOWLEDGE_BASES = "knowledgeBase"

# Edge collections
PERMISSION = "permission"
BELONGS_TO = "belongsTo"
BELONGS_TO_RECORD_GROUP = "belongsToRecordGroup"
INHERIT_PERMISSIONS = "inheritPermissions"
IS_OF_TYPE = "isOfType"
RECORD_RELATIONS = "recordRelations"
USER_APP_RELATION = "userAppRelation"
ORG_APP_RELATION = "orgAppRelation"
PARENT_CHILD = "parentChild"


# ---------------------------------------------------------------------------
# Helper: deterministic IDs
# ---------------------------------------------------------------------------

def _id() -> str:
    return str(uuid.uuid4())


def _ts() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# FakeGraphProvider
# ---------------------------------------------------------------------------

class FakeGraphProvider:
    """
    In-memory implementation of key IGraphDBProvider methods.

    Collections are stored as ``_collections[collection_name][key] -> document``.
    Edges are stored as ``_edges[edge_collection] -> list[edge_dict]``.

    Edge format follows the generic interface:
        {
            "from_id": ..., "from_collection": ...,
            "to_id": ...,   "to_collection": ...,
            ... extra properties ...
        }

    The provider is intentionally simple; it only implements what the
    scenario tests need.
    """

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, object]]] = {}
        self._edges: dict[str, list[dict[str, object]]] = {}

    # -- helpers --

    def _ensure_collection(self, name: str) -> dict[str, dict[str, object]]:
        if name not in self._collections:
            self._collections[name] = {}
        return self._collections[name]

    def _ensure_edge_collection(self, name: str) -> list[dict[str, object]]:
        if name not in self._edges:
            self._edges[name] = []
        return self._edges[name]

    # ==================== Document Operations ====================

    async def get_document(
        self,
        document_key: str,
        collection: str,
        transaction: str | None = None,
    ) -> dict[str, object] | None:
        col = self._ensure_collection(collection)
        doc = col.get(document_key)
        if doc is not None:
            return {**doc, "id": document_key}
        return None

    async def batch_upsert_nodes(
        self,
        nodes: list[dict[str, object]],
        collection: str,
        transaction: str | None = None,
    ) -> bool | None:
        col = self._ensure_collection(collection)
        for node in nodes:
            key = node.get("id") or node.get("_key")
            if key is None:
                key = _id()
                node["id"] = key
            col[str(key)] = {**node, "id": key}
        return True

    async def delete_nodes(
        self,
        keys: list[str],
        collection: str,
        transaction: str | None = None,
    ) -> bool:
        col = self._ensure_collection(collection)
        for key in keys:
            col.pop(key, None)
        return True

    async def update_node(
        self,
        key: str,
        collection: str,
        node_updates: dict[str, object],
        transaction: str | None = None,
    ) -> bool:
        col = self._ensure_collection(collection)
        if key not in col:
            return False
        col[key].update(node_updates)
        return True

    async def get_all_documents(
        self,
        collection: str,
        transaction: str | None = None,
    ) -> list[dict[str, object]]:
        col = self._ensure_collection(collection)
        return list(col.values())

    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: dict[str, object],
        return_fields: list[str] | None = None,
        transaction: str | None = None,
    ) -> list[dict[str, object]]:
        col = self._ensure_collection(collection)
        results: list[dict[str, object]] = []
        for doc in col.values():
            if all(doc.get(k) == v for k, v in filters.items()):
                if return_fields:
                    results.append({f: doc.get(f) for f in return_fields})
                else:
                    results.append(doc)
        return results

    async def get_nodes_by_field_in(
        self,
        collection: str,
        field_name: str,
        field_values: list[object],
        return_fields: list[str] | None = None,
        transaction: str | None = None,
    ) -> list[dict[str, object]]:
        col = self._ensure_collection(collection)
        results: list[dict[str, object]] = []
        for doc in col.values():
            if doc.get(field_name) in field_values:
                if return_fields:
                    results.append({f: doc.get(f) for f in return_fields})
                else:
                    results.append(doc)
        return results

    # ==================== Edge Operations ====================

    async def batch_create_edges(
        self,
        edges: list[dict[str, object]],
        collection: str,
        transaction: str | None = None,
    ) -> bool:
        edge_list = self._ensure_edge_collection(collection)
        for edge in edges:
            # De-duplicate by (from_id, from_collection, to_id, to_collection)
            dup = False
            for existing in edge_list:
                if (
                    existing.get("from_id") == edge.get("from_id")
                    and existing.get("from_collection") == edge.get("from_collection")
                    and existing.get("to_id") == edge.get("to_id")
                    and existing.get("to_collection") == edge.get("to_collection")
                ):
                    existing.update(edge)
                    dup = True
                    break
            if not dup:
                edge_list.append({**edge})
        return True

    async def get_edge(
        self,
        from_id: str,
        from_collection: str,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: str | None = None,
    ) -> dict[str, object] | None:
        edge_list = self._ensure_edge_collection(collection)
        for edge in edge_list:
            if (
                edge.get("from_id") == from_id
                and edge.get("from_collection") == from_collection
                and edge.get("to_id") == to_id
                and edge.get("to_collection") == to_collection
            ):
                return edge
        return None

    async def delete_edge(
        self,
        from_id: str,
        from_collection: str,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: str | None = None,
    ) -> bool:
        edge_list = self._ensure_edge_collection(collection)
        before = len(edge_list)
        self._edges[collection] = [
            e for e in edge_list
            if not (
                e.get("from_id") == from_id
                and e.get("from_collection") == from_collection
                and e.get("to_id") == to_id
                and e.get("to_collection") == to_collection
            )
        ]
        return len(self._edges[collection]) < before

    async def delete_edges_from(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: str | None = None,
    ) -> int:
        edge_list = self._ensure_edge_collection(collection)
        before = len(edge_list)
        self._edges[collection] = [
            e for e in edge_list
            if not (
                e.get("from_id") == from_id
                and e.get("from_collection") == from_collection
            )
        ]
        return before - len(self._edges[collection])

    async def delete_edges_to(
        self,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: str | None = None,
    ) -> int:
        edge_list = self._ensure_edge_collection(collection)
        before = len(edge_list)
        self._edges[collection] = [
            e for e in edge_list
            if not (
                e.get("to_id") == to_id
                and e.get("to_collection") == to_collection
            )
        ]
        return before - len(self._edges[collection])

    async def get_edges_from_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: str | None = None,
    ) -> list[dict[str, object]]:
        """node_id is 'collection/key' format."""
        edge_list = self._ensure_edge_collection(edge_collection)
        parts = node_id.split("/", 1)
        if len(parts) == 2:
            col, key = parts
            return [
                e for e in edge_list
                if e.get("from_id") == key and e.get("from_collection") == col
            ]
        return [e for e in edge_list if e.get("from_id") == node_id]

    async def get_edges_to_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: str | None = None,
    ) -> list[dict[str, object]]:
        """node_id is 'collection/key' format."""
        edge_list = self._ensure_edge_collection(edge_collection)
        parts = node_id.split("/", 1)
        if len(parts) == 2:
            col, key = parts
            return [
                e for e in edge_list
                if e.get("to_id") == key and e.get("to_collection") == col
            ]
        return [e for e in edge_list if e.get("to_id") == node_id]

    async def get_related_nodes(
        self,
        node_id: str,
        edge_collection: str,
        target_collection: str,
        direction: str = "inbound",
        transaction: str | None = None,
    ) -> list[dict[str, object]]:
        """Simple one-hop traversal."""
        target_col = self._ensure_collection(target_collection)
        if direction == "outbound":
            edges = await self.get_edges_from_node(node_id, edge_collection)
            keys = [str(e["to_id"]) for e in edges if e.get("to_collection") == target_collection]
        else:
            edges = await self.get_edges_to_node(node_id, edge_collection)
            keys = [str(e["from_id"]) for e in edges if e.get("from_collection") == target_collection]
        return [target_col[k] for k in keys if k in target_col]

    # ==================== Query Operations ====================

    async def execute_query(
        self,
        query: str,
        bind_vars: dict[str, object] | None = None,
        transaction: str | None = None,
    ) -> list[dict[str, object]] | None:
        """
        Minimal AQL-like pattern matching.  Does not parse AQL; instead
        recognizes specific patterns used in the test scenarios.
        """
        return []

    # ==================== Permission / Access ====================

    async def get_accessible_virtual_record_ids(
        self,
        user_id: str,
        org_id: str,
        filters: dict[str, list[str]] | None = None,
    ) -> dict[str, str]:
        """
        Reproduce the permission model by walking the 8 connector access paths
        plus KB paths in memory.

        The permission model grants access through:
        1. Direct user->record permission edge
        2. user->group->record (group via belongsTo, record via permission)
        3. user->group/role->record (group via permission, record via permission)
        4. user->org->record (org via belongsTo, record via permission)
        5. user->org->recordGroup->record (via inheritPermissions)
        6. user->group/role->recordGroup->record (via inheritPermissions)
        7. user->recordGroup->record (direct permission to recordGroup, then inheritPermissions)
        8. anyone collection records
        Plus KB paths (team-based and direct).
        """
        filters = filters or {}
        connector_filter = filters.get("apps")
        kb_filter = filters.get("kb")

        # Find user internal key from userId field
        user_doc = await self._find_user_by_user_id(user_id)
        if user_doc is None:
            return {}
        user_key = str(user_doc.get("id") or user_doc.get("_key"))

        # Determine which apps the user has access to
        user_app_ids = await self._get_user_app_ids_internal(user_key)

        virtual_id_map: dict[str, str] = {}

        def _add_record(rec: dict[str, object]) -> None:
            vid = rec.get("virtualRecordId")
            rid = rec.get("id") or rec.get("_key")
            if vid and rid and rec.get("indexingStatus") == "COMPLETED":
                # Apply connector filter
                if connector_filter and rec.get("connectorId") not in connector_filter:
                    return
                # Connector records must belong to user's apps
                if rec.get("origin") == "CONNECTOR" and rec.get("connectorId") not in user_app_ids:
                    return
                virtual_id_map[str(vid)] = str(rid)

        records_col = self._ensure_collection(RECORDS)

        # Path 1: Direct user->record permission
        perm_edges = self._ensure_edge_collection(PERMISSION)
        for e in perm_edges:
            if (
                e.get("from_id") == user_key
                and e.get("from_collection") == USERS
                and e.get("to_collection") == RECORDS
            ):
                rec = records_col.get(str(e["to_id"]))
                if rec:
                    _add_record(rec)

        # Path 2: user belongsTo group -> group has permission to record
        belongs_edges = self._ensure_edge_collection(BELONGS_TO)
        user_groups_via_belongs: set[tuple[str, str]] = set()
        for e in belongs_edges:
            if (
                e.get("from_id") == user_key
                and e.get("from_collection") == USERS
                and e.get("to_collection") in (GROUPS, ROLES)
            ):
                user_groups_via_belongs.add((str(e["to_collection"]), str(e["to_id"])))

        for g_col, g_id in user_groups_via_belongs:
            for e in perm_edges:
                if (
                    e.get("from_id") == g_id
                    and e.get("from_collection") == g_col
                    and e.get("to_collection") == RECORDS
                ):
                    rec = records_col.get(str(e["to_id"]))
                    if rec:
                        _add_record(rec)

        # Path 3: user has permission to group/role -> group/role has permission to record
        user_groups_via_perm: set[tuple[str, str]] = set()
        for e in perm_edges:
            if (
                e.get("from_id") == user_key
                and e.get("from_collection") == USERS
                and e.get("to_collection") in (GROUPS, ROLES)
            ):
                user_groups_via_perm.add((str(e["to_collection"]), str(e["to_id"])))

        for g_col, g_id in user_groups_via_perm:
            for e in perm_edges:
                if (
                    e.get("from_id") == g_id
                    and e.get("from_collection") == g_col
                    and e.get("to_collection") == RECORDS
                ):
                    rec = records_col.get(str(e["to_id"]))
                    if rec:
                        _add_record(rec)

        # Path 4: user belongsTo org -> org has permission to record
        user_orgs: set[str] = set()
        for e in belongs_edges:
            if (
                e.get("from_id") == user_key
                and e.get("from_collection") == USERS
                and e.get("to_collection") == ORGS
            ):
                user_orgs.add(str(e["to_id"]))

        for org_key in user_orgs:
            for e in perm_edges:
                if (
                    e.get("from_id") == org_key
                    and e.get("from_collection") == ORGS
                    and e.get("to_collection") == RECORDS
                ):
                    rec = records_col.get(str(e["to_id"]))
                    if rec:
                        _add_record(rec)

        # Path 5: user->org->recordGroup->records via inheritPermissions
        inherit_edges = self._ensure_edge_collection(INHERIT_PERMISSIONS)

        for org_key in user_orgs:
            for e in perm_edges:
                if (
                    e.get("from_id") == org_key
                    and e.get("from_collection") == ORGS
                    and e.get("to_collection") == RECORD_GROUPS
                ):
                    self._collect_inherited_records(
                        str(e["to_id"]), inherit_edges, records_col, _add_record
                    )

        # Path 6: user->group/role->recordGroup->records via inheritPermissions
        all_user_groups = user_groups_via_belongs | user_groups_via_perm
        for g_col, g_id in all_user_groups:
            for e in perm_edges:
                if (
                    e.get("from_id") == g_id
                    and e.get("from_collection") == g_col
                    and e.get("to_collection") == RECORD_GROUPS
                ):
                    self._collect_inherited_records(
                        str(e["to_id"]), inherit_edges, records_col, _add_record
                    )

        # Path 7: user has direct permission to recordGroup -> records via inheritPermissions
        for e in perm_edges:
            if (
                e.get("from_id") == user_key
                and e.get("from_collection") == USERS
                and e.get("to_collection") == RECORD_GROUPS
            ):
                self._collect_inherited_records(
                    str(e["to_id"]), inherit_edges, records_col, _add_record
                )

        # Path 8: anyone collection
        anyone_col = self._ensure_collection(ANYONE)
        for anyone_doc in anyone_col.values():
            if anyone_doc.get("organization") == org_id:
                file_key = anyone_doc.get("file_key")
                if file_key:
                    rec = records_col.get(str(file_key))
                    if rec:
                        _add_record(rec)

        # KB paths: user has permission edge to KB -> records belongsTo KB
        belongs_to_edges = self._ensure_edge_collection(BELONGS_TO)
        for e in perm_edges:
            if (
                e.get("from_id") == user_key
                and e.get("from_collection") == USERS
                and e.get("to_collection") == KNOWLEDGE_BASES
            ):
                kb_id = str(e["to_id"])
                if kb_filter and kb_id not in kb_filter:
                    continue
                # Find records that belong to this KB
                for be in belongs_to_edges:
                    if (
                        be.get("to_id") == kb_id
                        and be.get("to_collection") == KNOWLEDGE_BASES
                        and be.get("from_collection") == RECORDS
                    ):
                        rec = records_col.get(str(be["from_id"]))
                        if rec:
                            _add_record(rec)

        # Team-based KB access: user belongsTo team, team has permission to KB
        user_teams: set[str] = set()
        for e in belongs_edges:
            if (
                e.get("from_id") == user_key
                and e.get("from_collection") == USERS
                and e.get("to_collection") == TEAMS
            ):
                user_teams.add(str(e["to_id"]))

        for team_id in user_teams:
            for e in perm_edges:
                if (
                    e.get("from_id") == team_id
                    and e.get("from_collection") == TEAMS
                    and e.get("to_collection") == KNOWLEDGE_BASES
                ):
                    kb_id = str(e["to_id"])
                    if kb_filter and kb_id not in kb_filter:
                        continue
                    for be in belongs_to_edges:
                        if (
                            be.get("to_id") == kb_id
                            and be.get("to_collection") == KNOWLEDGE_BASES
                            and be.get("from_collection") == RECORDS
                        ):
                            rec = records_col.get(str(be["from_id"]))
                            if rec:
                                _add_record(rec)

        return virtual_id_map

    def _collect_inherited_records(
        self,
        record_group_id: str,
        inherit_edges: list[dict[str, object]],
        records_col: dict[str, dict[str, object]],
        add_fn: Callable[[dict[str, object]], None],
        depth: int = 5,
    ) -> None:
        """Collect records that inherit permissions from a record group, up to depth levels."""
        if depth <= 0:
            return
        for ie in inherit_edges:
            if ie.get("to_id") == record_group_id and ie.get("to_collection") == RECORD_GROUPS:
                if ie.get("from_collection") == RECORDS:
                    rec = records_col.get(str(ie["from_id"]))
                    if rec:
                        add_fn(rec)
                elif ie.get("from_collection") == RECORD_GROUPS:
                    # Nested record group
                    self._collect_inherited_records(
                        str(ie["from_id"]), inherit_edges, records_col, add_fn, depth - 1
                    )

    async def get_records_by_record_ids(
        self,
        record_ids: list[str],
        org_id: str,
    ) -> list[dict[str, object]]:
        records_col = self._ensure_collection(RECORDS)
        results: list[dict[str, object]] = []
        for rid in record_ids:
            rec = records_col.get(rid)
            if rec and rec.get("orgId") == org_id:
                results.append(rec)
        return results

    async def get_user_by_user_id(self, user_id: str) -> dict[str, object] | None:
        return await self._find_user_by_user_id(user_id)

    async def _find_user_by_user_id(self, user_id: str) -> dict[str, object] | None:
        users_col = self._ensure_collection(USERS)
        for doc in users_col.values():
            if doc.get("userId") == user_id:
                return doc
        return None

    async def _get_user_app_ids_internal(self, user_key: str) -> list[str]:
        """Get app ids the user is linked to via userAppRelation edges."""
        rel_edges = self._ensure_edge_collection(USER_APP_RELATION)
        return [
            str(e["to_id"])
            for e in rel_edges
            if (
                e.get("from_id") == user_key
                and e.get("from_collection") == USERS
                and e.get("to_collection") == APPS
            )
        ]

    # ==================== Sync Points ====================

    async def get_sync_point(
        self,
        key: str,
        collection: str,
        transaction: str | None = None,
    ) -> dict[str, object] | None:
        col = self._ensure_collection(collection)
        return col.get(key)

    async def upsert_sync_point(
        self,
        sync_point_key: str,
        sync_point_data: dict[str, object],
        collection: str,
        transaction: str | None = None,
    ) -> bool:
        col = self._ensure_collection(collection)
        col[sync_point_key] = {**sync_point_data, "id": sync_point_key}
        return True

    async def remove_sync_point(
        self,
        key: str,
        collection: str,
        transaction: str | None = None,
    ) -> None:
        col = self._ensure_collection(collection)
        col.pop(key, None)

    # ==================== Duplicate Detection ====================

    async def find_duplicate_records(
        self,
        record_key: str,
        md5_checksum: str,
        record_type: str | None = None,
        size_in_bytes: int | None = None,
        transaction: str | None = None,
    ) -> list[dict[str, object]]:
        records = self._ensure_collection(RECORDS)
        results: list[dict[str, object]] = []
        for key, rec in records.items():
            if key == record_key:
                continue
            if rec.get("md5Checksum") != md5_checksum:
                continue
            if record_type and rec.get("recordType") != record_type:
                continue
            if size_in_bytes is not None and rec.get("sizeInBytes") != size_in_bytes:
                continue
            results.append(rec)
        return results

    async def find_next_queued_duplicate(
        self,
        record_id: str,
        transaction: str | None = None,
    ) -> dict[str, object] | None:
        records = self._ensure_collection(RECORDS)
        source = records.get(record_id)
        if not source:
            return None
        md5 = source.get("md5Checksum")
        if not md5:
            return None
        for key, rec in records.items():
            if key == record_id:
                continue
            if rec.get("md5Checksum") == md5 and rec.get("indexingStatus") == "QUEUED":
                return rec
        return None

    async def update_queued_duplicates_status(
        self,
        record_id: str,
        new_indexing_status: str,
        virtual_record_id: str | None = None,
        transaction: str | None = None,
    ) -> int:
        records = self._ensure_collection(RECORDS)
        source = records.get(record_id)
        if not source:
            return 0
        md5 = source.get("md5Checksum")
        if not md5:
            return 0
        count = 0
        for key, rec in records.items():
            if key == record_id:
                continue
            if rec.get("md5Checksum") == md5 and rec.get("indexingStatus") == "QUEUED":
                rec["indexingStatus"] = new_indexing_status
                if virtual_record_id:
                    rec["virtualRecordId"] = virtual_record_id
                count += 1
        return count

    # ==================== Bulk Cleanup ====================

    async def delete_all_connector_data(self, connector_id: str) -> dict[str, int]:
        """Delete all records, edges, sync points for a connector."""
        records = self._ensure_collection(RECORDS)
        record_groups = self._ensure_collection(RECORD_GROUPS)
        sync_points = self._ensure_collection(SYNC_POINTS)

        deleted_records: list[str] = []
        for key, rec in list(records.items()):
            if rec.get("connectorId") == connector_id:
                deleted_records.append(key)
                del records[key]

        deleted_groups: list[str] = []
        for key, rg in list(record_groups.items()):
            if rg.get("connectorId") == connector_id:
                deleted_groups.append(key)
                del record_groups[key]

        deleted_sync: list[str] = []
        for key, sp in list(sync_points.items()):
            if sp.get("connectorId") == connector_id:
                deleted_sync.append(key)
                del sync_points[key]

        # Remove edges referencing deleted records or groups
        all_deleted_ids = set(deleted_records + deleted_groups)
        for edge_col_name in self._edges:
            self._edges[edge_col_name] = [
                e for e in self._edges[edge_col_name]
                if e.get("from_id") not in all_deleted_ids
                and e.get("to_id") not in all_deleted_ids
            ]

        return {
            "deleted_records": len(deleted_records),
            "deleted_groups": len(deleted_groups),
            "deleted_sync_points": len(deleted_sync),
        }

    async def delete_connector_sync_edges(
        self,
        connector_id: str,
        transaction: str | None = None,
    ) -> tuple[int, bool]:
        """Delete sync-created edges for a connector."""
        sync_edge_collections = [
            BELONGS_TO, BELONGS_TO_RECORD_GROUP, RECORD_RELATIONS,
            PERMISSION, INHERIT_PERMISSIONS, USER_APP_RELATION,
        ]
        total = 0
        records = self._ensure_collection(RECORDS)
        record_groups = self._ensure_collection(RECORD_GROUPS)
        connector_record_ids = {
            k for k, v in records.items() if v.get("connectorId") == connector_id
        }
        connector_group_ids = {
            k for k, v in record_groups.items() if v.get("connectorId") == connector_id
        }
        related_ids = connector_record_ids | connector_group_ids

        for ec_name in sync_edge_collections:
            edge_list = self._ensure_edge_collection(ec_name)
            before = len(edge_list)
            self._edges[ec_name] = [
                e for e in edge_list
                if e.get("from_id") not in related_ids
                and e.get("to_id") not in related_ids
            ]
            total += before - len(self._edges[ec_name])
        return total, True

    async def delete_sync_points_by_connector_id(
        self,
        connector_id: str,
        transaction: str | None = None,
    ) -> tuple[int, bool]:
        sp_col = self._ensure_collection(SYNC_POINTS)
        to_delete = [k for k, v in sp_col.items() if v.get("connectorId") == connector_id]
        for k in to_delete:
            del sp_col[k]
        return len(to_delete), True

    # ==================== Hierarchy helpers ====================

    async def get_children(
        self,
        parent_record_id: str,
        connector_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Get child records via PARENT_CHILD relation edges or externalParentId."""
        records = self._ensure_collection(RECORDS)
        children: list[dict[str, object]] = []
        child_ids_seen: set[str] = set()
        # Via recordRelations (PARENT_CHILD type)
        rel_edges = self._ensure_edge_collection(RECORD_RELATIONS)
        for e in rel_edges:
            if (
                e.get("from_id") == parent_record_id
                and e.get("from_collection") == RECORDS
                and e.get("to_collection") == RECORDS
                and e.get("relationType") == "PARENT_CHILD"
            ):
                child_key = str(e["to_id"])
                child = records.get(child_key)
                if child and child_key not in child_ids_seen:
                    children.append(child)
                    child_ids_seen.add(child_key)
        # Also check externalParentId for records with matching parent
        parent_rec = records.get(parent_record_id)
        if parent_rec:
            parent_ext_id = parent_rec.get("externalRecordId")
            for rec in records.values():
                rec_id = str(rec.get("id", ""))
                if (
                    rec.get("externalParentId") == parent_ext_id
                    and (connector_id is None or rec.get("connectorId") == connector_id)
                    and rec_id != parent_record_id
                    and rec_id not in child_ids_seen
                ):
                    children.append(rec)
                    child_ids_seen.add(rec_id)
        return children

    async def get_parent(self, record_id: str) -> dict[str, object] | None:
        """Get parent record via externalParentId."""
        records = self._ensure_collection(RECORDS)
        rec = records.get(record_id)
        if not rec:
            return None
        parent_ext_id = rec.get("externalParentId")
        if not parent_ext_id:
            return None
        for r in records.values():
            if r.get("externalRecordId") == parent_ext_id:
                return r
        return None


# ---------------------------------------------------------------------------
# Shared data builders
# ---------------------------------------------------------------------------

def make_org(org_id: str, name: str = "Test Org") -> dict[str, object]:
    return {"id": org_id, "_key": org_id, "name": name, "orgId": org_id, "isActive": True}


def make_user(
    key: str,
    user_id: str,
    email: str,
    org_id: str,
    full_name: str = "",
) -> dict[str, object]:
    return {
        "id": key,
        "_key": key,
        "userId": user_id,
        "email": email,
        "orgId": org_id,
        "fullName": full_name or email.split("@")[0],
        "isActive": True,
    }


def make_app(app_id: str, org_id: str, name: str, connector_name: str = "DRIVE") -> dict[str, object]:
    return {
        "id": app_id,
        "_key": app_id,
        "orgId": org_id,
        "name": name,
        "connectorName": connector_name,
        "isActive": True,
    }


def make_record(
    record_id: str,
    org_id: str,
    connector_id: str,
    name: str,
    record_type: str = "FILE",
    indexing_status: str = "COMPLETED",
    virtual_record_id: str | None = None,
    external_record_id: str | None = None,
    external_parent_id: str | None = None,
    connector_name: str = "DRIVE",
    origin: str = "CONNECTOR",
    md5_checksum: str | None = None,
    size_in_bytes: int | None = None,
    mime_type: str = "application/pdf",
) -> dict[str, object]:
    return {
        "id": record_id,
        "_key": record_id,
        "orgId": org_id,
        "connectorId": connector_id,
        "connectorName": connector_name,
        "recordName": name,
        "recordType": record_type,
        "indexingStatus": indexing_status,
        "virtualRecordId": virtual_record_id or record_id,
        "externalRecordId": external_record_id or record_id,
        "externalParentId": external_parent_id,
        "origin": origin,
        "md5Checksum": md5_checksum,
        "sizeInBytes": size_in_bytes,
        "mimeType": mime_type,
        "createdAtTimestamp": _ts(),
        "updatedAtTimestamp": _ts(),
        "isDeleted": False,
    }


def make_record_group(
    group_id: str,
    org_id: str,
    connector_id: str,
    name: str,
    group_type: str = "DRIVE",
    external_group_id: str | None = None,
    connector_name: str = "DRIVE",
) -> dict[str, object]:
    return {
        "id": group_id,
        "_key": group_id,
        "orgId": org_id,
        "connectorId": connector_id,
        "connectorName": connector_name,
        "groupName": name,
        "groupType": group_type,
        "externalGroupId": external_group_id or group_id,
        "createdAtTimestamp": _ts(),
        "updatedAtTimestamp": _ts(),
    }


def make_team(team_id: str, org_id: str, name: str) -> dict[str, object]:
    return {
        "id": team_id,
        "_key": team_id,
        "orgId": org_id,
        "name": name,
    }


def make_kb(kb_id: str, org_id: str, name: str) -> dict[str, object]:
    return {
        "id": kb_id,
        "_key": kb_id,
        "orgId": org_id,
        "name": name,
        "createdAtTimestamp": _ts(),
        "updatedAtTimestamp": _ts(),
    }


def perm_edge(
    from_id: str, from_col: str, to_id: str, to_col: str,
    role: str = "READER", perm_type: str = "USER",
) -> dict[str, str]:
    return {
        "from_id": from_id, "from_collection": from_col,
        "to_id": to_id, "to_collection": to_col,
        "role": role, "type": perm_type,
    }


def belongs_edge(from_id: str, from_col: str, to_id: str, to_col: str) -> dict[str, str]:
    return {
        "from_id": from_id, "from_collection": from_col,
        "to_id": to_id, "to_collection": to_col,
    }


def inherit_edge(from_id: str, from_col: str, to_id: str, to_col: str) -> dict[str, str]:
    return {
        "from_id": from_id, "from_collection": from_col,
        "to_id": to_id, "to_collection": to_col,
    }


def relation_edge(
    from_id: str, to_id: str, relation_type: str = "PARENT_CHILD",
) -> dict[str, str]:
    return {
        "from_id": from_id, "from_collection": RECORDS,
        "to_id": to_id, "to_collection": RECORDS,
        "relationType": relation_type,
    }


def user_app_edge(user_key: str, app_id: str) -> dict[str, str]:
    return {
        "from_id": user_key, "from_collection": USERS,
        "to_id": app_id, "to_collection": APPS,
    }


# ===========================================================================
# Scenario 1: Google Drive Connector Setup
# ===========================================================================


class TestGoogleDriveConnectorSetup:
    """
    Simulate a Google Drive connector with:
    - 1 org, 3 users (admin, user1, user2)
    - 1 connector instance
    - 2 shared drives (drive1, drive2)
    - Multiple files with parent-child hierarchy
    - User permissions (user1 -> drive1, user2 -> drive2, admin -> both)
    """

    @staticmethod
    def _build_scenario() -> tuple[FakeGraphProvider, dict[str, str]]:
        """Build the complete scenario state and return (provider, ids)."""
        p = FakeGraphProvider()
        ids: dict[str, str] = {}

        org_id = "org-gd-1"
        ids["org_id"] = org_id
        p._ensure_collection(ORGS)[org_id] = make_org(org_id, "Acme Corp")

        admin_key, u1_key, u2_key = "u-admin", "u-user1", "u-user2"
        ids["admin_key"] = admin_key
        ids["u1_key"] = u1_key
        ids["u2_key"] = u2_key

        users_col = p._ensure_collection(USERS)
        users_col[admin_key] = make_user(admin_key, "uid-admin", "admin@acme.com", org_id, "Admin")
        users_col[u1_key] = make_user(u1_key, "uid-user1", "user1@acme.com", org_id, "User One")
        users_col[u2_key] = make_user(u2_key, "uid-user2", "user2@acme.com", org_id, "User Two")

        app_id = "app-gdrive-1"
        ids["app_id"] = app_id
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "Google Drive", "DRIVE")

        # Link all users to the app
        p._ensure_edge_collection(USER_APP_RELATION).extend([
            user_app_edge(admin_key, app_id),
            user_app_edge(u1_key, app_id),
            user_app_edge(u2_key, app_id),
        ])

        # Users belong to org
        p._ensure_edge_collection(BELONGS_TO).extend([
            belongs_edge(admin_key, USERS, org_id, ORGS),
            belongs_edge(u1_key, USERS, org_id, ORGS),
            belongs_edge(u2_key, USERS, org_id, ORGS),
        ])

        # Record groups (shared drives)
        drive1_id, drive2_id = "rg-drive1", "rg-drive2"
        ids["drive1_id"] = drive1_id
        ids["drive2_id"] = drive2_id
        rg_col = p._ensure_collection(RECORD_GROUPS)
        rg_col[drive1_id] = make_record_group(drive1_id, org_id, app_id, "Engineering Drive", "DRIVE", "ext-drive1")
        rg_col[drive2_id] = make_record_group(drive2_id, org_id, app_id, "Marketing Drive", "DRIVE", "ext-drive2")

        # Records in drive1
        d1_folder = "rec-d1-folder"
        d1_file1 = "rec-d1-file1"
        d1_file2 = "rec-d1-file2"
        d1_subfolder = "rec-d1-subfolder"
        d1_subfile = "rec-d1-subfile"
        ids["d1_folder"] = d1_folder
        ids["d1_file1"] = d1_file1
        ids["d1_file2"] = d1_file2
        ids["d1_subfolder"] = d1_subfolder
        ids["d1_subfile"] = d1_subfile

        rec_col = p._ensure_collection(RECORDS)
        rec_col[d1_folder] = make_record(
            d1_folder, org_id, app_id, "Engineering Folder",
            record_type="FILE", external_record_id="ext-d1-folder",
            mime_type="application/vnd.google-apps.folder",
        )
        rec_col[d1_file1] = make_record(
            d1_file1, org_id, app_id, "Design Doc.pdf",
            external_record_id="ext-d1-file1",
            external_parent_id="ext-d1-folder",
        )
        rec_col[d1_file2] = make_record(
            d1_file2, org_id, app_id, "Spec.docx",
            external_record_id="ext-d1-file2",
            external_parent_id="ext-d1-folder",
        )
        rec_col[d1_subfolder] = make_record(
            d1_subfolder, org_id, app_id, "Sub Folder",
            record_type="FILE", external_record_id="ext-d1-subfolder",
            external_parent_id="ext-d1-folder",
            mime_type="application/vnd.google-apps.folder",
        )
        rec_col[d1_subfile] = make_record(
            d1_subfile, org_id, app_id, "Nested File.txt",
            external_record_id="ext-d1-subfile",
            external_parent_id="ext-d1-subfolder",
        )

        # Records in drive2
        d2_file1 = "rec-d2-file1"
        d2_file2 = "rec-d2-file2"
        ids["d2_file1"] = d2_file1
        ids["d2_file2"] = d2_file2

        rec_col[d2_file1] = make_record(
            d2_file1, org_id, app_id, "Campaign Plan.pdf",
            external_record_id="ext-d2-file1",
        )
        rec_col[d2_file2] = make_record(
            d2_file2, org_id, app_id, "Brand Guide.pptx",
            external_record_id="ext-d2-file2",
        )

        # Inherit permissions: records inherit from their drive's record group
        p._ensure_edge_collection(INHERIT_PERMISSIONS).extend([
            inherit_edge(d1_folder, RECORDS, drive1_id, RECORD_GROUPS),
            inherit_edge(d1_file1, RECORDS, drive1_id, RECORD_GROUPS),
            inherit_edge(d1_file2, RECORDS, drive1_id, RECORD_GROUPS),
            inherit_edge(d1_subfolder, RECORDS, drive1_id, RECORD_GROUPS),
            inherit_edge(d1_subfile, RECORDS, drive1_id, RECORD_GROUPS),
            inherit_edge(d2_file1, RECORDS, drive2_id, RECORD_GROUPS),
            inherit_edge(d2_file2, RECORDS, drive2_id, RECORD_GROUPS),
        ])

        # Record relations (parent-child)
        p._ensure_edge_collection(RECORD_RELATIONS).extend([
            relation_edge(d1_folder, d1_file1),
            relation_edge(d1_folder, d1_file2),
            relation_edge(d1_folder, d1_subfolder),
            relation_edge(d1_subfolder, d1_subfile),
        ])

        # Permissions: user1 -> drive1, user2 -> drive2, admin -> both
        p._ensure_edge_collection(PERMISSION).extend([
            perm_edge(u1_key, USERS, drive1_id, RECORD_GROUPS, "READER"),
            perm_edge(u2_key, USERS, drive2_id, RECORD_GROUPS, "READER"),
            perm_edge(admin_key, USERS, drive1_id, RECORD_GROUPS, "OWNER"),
            perm_edge(admin_key, USERS, drive2_id, RECORD_GROUPS, "OWNER"),
        ])

        return p, ids

    @pytest.mark.asyncio
    async def test_org_created(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["org_id"], ORGS)
        assert doc is not None
        assert doc["name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_users_created(self) -> None:
        p, ids = self._build_scenario()
        for key in [ids["admin_key"], ids["u1_key"], ids["u2_key"]]:
            doc = await p.get_document(key, USERS)
            assert doc is not None

    @pytest.mark.asyncio
    async def test_admin_user_has_correct_email(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["admin_key"], USERS)
        assert doc is not None
        assert doc["email"] == "admin@acme.com"

    @pytest.mark.asyncio
    async def test_connector_instance_created(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["app_id"], APPS)
        assert doc is not None
        assert doc["connectorName"] == "DRIVE"

    @pytest.mark.asyncio
    async def test_record_groups_created(self) -> None:
        p, ids = self._build_scenario()
        for gid in [ids["drive1_id"], ids["drive2_id"]]:
            doc = await p.get_document(gid, RECORD_GROUPS)
            assert doc is not None

    @pytest.mark.asyncio
    async def test_drive1_has_correct_name(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["drive1_id"], RECORD_GROUPS)
        assert doc is not None
        assert doc["groupName"] == "Engineering Drive"

    @pytest.mark.asyncio
    async def test_drive2_has_correct_name(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["drive2_id"], RECORD_GROUPS)
        assert doc is not None
        assert doc["groupName"] == "Marketing Drive"

    @pytest.mark.asyncio
    async def test_drive1_records_created(self) -> None:
        p, ids = self._build_scenario()
        for rid in [ids["d1_folder"], ids["d1_file1"], ids["d1_file2"], ids["d1_subfolder"], ids["d1_subfile"]]:
            doc = await p.get_document(rid, RECORDS)
            assert doc is not None

    @pytest.mark.asyncio
    async def test_drive2_records_created(self) -> None:
        p, ids = self._build_scenario()
        for rid in [ids["d2_file1"], ids["d2_file2"]]:
            doc = await p.get_document(rid, RECORDS)
            assert doc is not None

    @pytest.mark.asyncio
    async def test_drive_user1_sees_only_drive1_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-user1", ids["org_id"])
        drive1_records = {ids["d1_folder"], ids["d1_file1"], ids["d1_file2"], ids["d1_subfolder"], ids["d1_subfile"]}
        assert set(vids.values()) == drive1_records

    @pytest.mark.asyncio
    async def test_drive_user1_cannot_see_drive2_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-user1", ids["org_id"])
        drive2_records = {ids["d2_file1"], ids["d2_file2"]}
        assert drive2_records.isdisjoint(set(vids.values()))

    @pytest.mark.asyncio
    async def test_drive_user2_sees_only_drive2_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-user2", ids["org_id"])
        drive2_records = {ids["d2_file1"], ids["d2_file2"]}
        assert set(vids.values()) == drive2_records

    @pytest.mark.asyncio
    async def test_drive_user2_cannot_see_drive1_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-user2", ids["org_id"])
        drive1_records = {ids["d1_folder"], ids["d1_file1"], ids["d1_file2"], ids["d1_subfolder"], ids["d1_subfile"]}
        assert drive1_records.isdisjoint(set(vids.values()))

    @pytest.mark.asyncio
    async def test_admin_sees_all_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-admin", ids["org_id"])
        all_records = {
            ids["d1_folder"], ids["d1_file1"], ids["d1_file2"],
            ids["d1_subfolder"], ids["d1_subfile"],
            ids["d2_file1"], ids["d2_file2"],
        }
        assert set(vids.values()) == all_records

    @pytest.mark.asyncio
    async def test_admin_record_count_is_seven(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-admin", ids["org_id"])
        assert len(vids) == 7

    @pytest.mark.asyncio
    async def test_user1_record_count_is_five(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-user1", ids["org_id"])
        assert len(vids) == 5

    @pytest.mark.asyncio
    async def test_user2_record_count_is_two(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-user2", ids["org_id"])
        assert len(vids) == 2

    @pytest.mark.asyncio
    async def test_folder_has_children(self) -> None:
        p, ids = self._build_scenario()
        children = await p.get_children(ids["d1_folder"], ids["app_id"])
        child_ids = {c["id"] for c in children}
        assert ids["d1_file1"] in child_ids
        assert ids["d1_file2"] in child_ids
        assert ids["d1_subfolder"] in child_ids

    @pytest.mark.asyncio
    async def test_subfolder_has_child(self) -> None:
        p, ids = self._build_scenario()
        children = await p.get_children(ids["d1_subfolder"], ids["app_id"])
        child_ids = {c["id"] for c in children}
        assert ids["d1_subfile"] in child_ids

    @pytest.mark.asyncio
    async def test_file_has_parent(self) -> None:
        p, ids = self._build_scenario()
        parent = await p.get_parent(ids["d1_file1"])
        assert parent is not None
        assert parent["id"] == ids["d1_folder"]

    @pytest.mark.asyncio
    async def test_nested_file_parent_is_subfolder(self) -> None:
        p, ids = self._build_scenario()
        parent = await p.get_parent(ids["d1_subfile"])
        assert parent is not None
        assert parent["id"] == ids["d1_subfolder"]

    @pytest.mark.asyncio
    async def test_nonexistent_user_sees_nothing(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-nobody", ids["org_id"])
        assert len(vids) == 0

    @pytest.mark.asyncio
    async def test_records_fetched_by_ids_match_org(self) -> None:
        p, ids = self._build_scenario()
        recs = await p.get_records_by_record_ids(
            [ids["d1_file1"], ids["d2_file1"]], ids["org_id"]
        )
        assert len(recs) == 2
        for r in recs:
            assert r["orgId"] == ids["org_id"]

    @pytest.mark.asyncio
    async def test_records_fetched_wrong_org_returns_empty(self) -> None:
        p, ids = self._build_scenario()
        recs = await p.get_records_by_record_ids([ids["d1_file1"]], "wrong-org")
        assert len(recs) == 0


# ===========================================================================
# Scenario 2: Jira Connector with Teams
# ===========================================================================


class TestJiraConnectorWithTeams:
    """
    Simulate a Jira connector with team-based project permissions:
    - 1 org, 4 users, 2 teams (engineering, product)
    - 2 projects (PROJ-A, PROJ-B) as record groups
    - Tickets linked to projects
    - Engineering team -> PROJ-A, Product team -> PROJ-B
    - One user in both teams
    """

    @staticmethod
    def _build_scenario() -> tuple[FakeGraphProvider, dict[str, str]]:
        p = FakeGraphProvider()
        ids: dict[str, str] = {}

        org_id = "org-jira-1"
        ids["org_id"] = org_id
        p._ensure_collection(ORGS)[org_id] = make_org(org_id, "Jira Org")

        eng1_key = "u-eng1"
        eng2_key = "u-eng2"
        prod1_key = "u-prod1"
        both_key = "u-both"
        ids["eng1_key"] = eng1_key
        ids["eng2_key"] = eng2_key
        ids["prod1_key"] = prod1_key
        ids["both_key"] = both_key

        users_col = p._ensure_collection(USERS)
        users_col[eng1_key] = make_user(eng1_key, "uid-eng1", "eng1@co.com", org_id, "Engineer 1")
        users_col[eng2_key] = make_user(eng2_key, "uid-eng2", "eng2@co.com", org_id, "Engineer 2")
        users_col[prod1_key] = make_user(prod1_key, "uid-prod1", "prod1@co.com", org_id, "Product 1")
        users_col[both_key] = make_user(both_key, "uid-both", "both@co.com", org_id, "Both Teams")

        app_id = "app-jira-1"
        ids["app_id"] = app_id
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "Jira", "JIRA")

        # Link users to app
        p._ensure_edge_collection(USER_APP_RELATION).extend([
            user_app_edge(eng1_key, app_id),
            user_app_edge(eng2_key, app_id),
            user_app_edge(prod1_key, app_id),
            user_app_edge(both_key, app_id),
        ])

        # Users belong to org
        p._ensure_edge_collection(BELONGS_TO).extend([
            belongs_edge(eng1_key, USERS, org_id, ORGS),
            belongs_edge(eng2_key, USERS, org_id, ORGS),
            belongs_edge(prod1_key, USERS, org_id, ORGS),
            belongs_edge(both_key, USERS, org_id, ORGS),
        ])

        # Teams as groups
        eng_team = "grp-engineering"
        prod_team = "grp-product"
        ids["eng_team"] = eng_team
        ids["prod_team"] = prod_team

        groups_col = p._ensure_collection(GROUPS)
        groups_col[eng_team] = {"id": eng_team, "name": "Engineering", "orgId": org_id}
        groups_col[prod_team] = {"id": prod_team, "name": "Product", "orgId": org_id}

        # Users belong to teams (groups)
        p._ensure_edge_collection(BELONGS_TO).extend([
            belongs_edge(eng1_key, USERS, eng_team, GROUPS),
            belongs_edge(eng2_key, USERS, eng_team, GROUPS),
            belongs_edge(prod1_key, USERS, prod_team, GROUPS),
            belongs_edge(both_key, USERS, eng_team, GROUPS),
            belongs_edge(both_key, USERS, prod_team, GROUPS),
        ])

        # Projects as record groups
        proj_a = "rg-proj-a"
        proj_b = "rg-proj-b"
        ids["proj_a"] = proj_a
        ids["proj_b"] = proj_b

        rg_col = p._ensure_collection(RECORD_GROUPS)
        rg_col[proj_a] = make_record_group(proj_a, org_id, app_id, "Project Alpha", "PROJECT", "PROJ-A", "JIRA")
        rg_col[proj_b] = make_record_group(proj_b, org_id, app_id, "Project Beta", "PROJECT", "PROJ-B", "JIRA")

        # Tickets
        ticket_a1 = "rec-a1"
        ticket_a2 = "rec-a2"
        ticket_a3 = "rec-a3"
        ticket_b1 = "rec-b1"
        ticket_b2 = "rec-b2"
        ids["ticket_a1"] = ticket_a1
        ids["ticket_a2"] = ticket_a2
        ids["ticket_a3"] = ticket_a3
        ids["ticket_b1"] = ticket_b1
        ids["ticket_b2"] = ticket_b2

        rec_col = p._ensure_collection(RECORDS)
        for tid, name, _proj_ext in [
            (ticket_a1, "PROJ-A-1: Fix login", "PROJ-A"),
            (ticket_a2, "PROJ-A-2: Refactor DB", "PROJ-A"),
            (ticket_a3, "PROJ-A-3: Add tests", "PROJ-A"),
            (ticket_b1, "PROJ-B-1: Update roadmap", "PROJ-B"),
            (ticket_b2, "PROJ-B-2: Design review", "PROJ-B"),
        ]:
            rec_col[tid] = make_record(
                tid, org_id, app_id, name,
                record_type="TICKET", connector_name="JIRA",
                external_record_id=tid,
            )

        # Records inherit from project record groups
        p._ensure_edge_collection(INHERIT_PERMISSIONS).extend([
            inherit_edge(ticket_a1, RECORDS, proj_a, RECORD_GROUPS),
            inherit_edge(ticket_a2, RECORDS, proj_a, RECORD_GROUPS),
            inherit_edge(ticket_a3, RECORDS, proj_a, RECORD_GROUPS),
            inherit_edge(ticket_b1, RECORDS, proj_b, RECORD_GROUPS),
            inherit_edge(ticket_b2, RECORDS, proj_b, RECORD_GROUPS),
        ])

        # Team permissions to projects
        p._ensure_edge_collection(PERMISSION).extend([
            perm_edge(eng_team, GROUPS, proj_a, RECORD_GROUPS, "READER"),
            perm_edge(prod_team, GROUPS, proj_b, RECORD_GROUPS, "READER"),
        ])

        return p, ids

    @pytest.mark.asyncio
    async def test_engineering_member_sees_proj_a_only(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-eng1", ids["org_id"])
        assert set(vids.values()) == {ids["ticket_a1"], ids["ticket_a2"], ids["ticket_a3"]}

    @pytest.mark.asyncio
    async def test_engineering_member2_sees_proj_a_only(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-eng2", ids["org_id"])
        assert set(vids.values()) == {ids["ticket_a1"], ids["ticket_a2"], ids["ticket_a3"]}

    @pytest.mark.asyncio
    async def test_product_member_sees_proj_b_only(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-prod1", ids["org_id"])
        assert set(vids.values()) == {ids["ticket_b1"], ids["ticket_b2"]}

    @pytest.mark.asyncio
    async def test_product_member_cannot_see_proj_a(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-prod1", ids["org_id"])
        assert {ids["ticket_a1"], ids["ticket_a2"], ids["ticket_a3"]}.isdisjoint(set(vids.values()))

    @pytest.mark.asyncio
    async def test_both_teams_member_sees_all_tickets(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-both", ids["org_id"])
        all_tickets = {ids["ticket_a1"], ids["ticket_a2"], ids["ticket_a3"], ids["ticket_b1"], ids["ticket_b2"]}
        assert set(vids.values()) == all_tickets

    @pytest.mark.asyncio
    async def test_both_teams_member_count_is_five(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-both", ids["org_id"])
        assert len(vids) == 5

    @pytest.mark.asyncio
    async def test_engineering_count_is_three(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-eng1", ids["org_id"])
        assert len(vids) == 3

    @pytest.mark.asyncio
    async def test_product_count_is_two(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-prod1", ids["org_id"])
        assert len(vids) == 2

    @pytest.mark.asyncio
    async def test_project_a_record_group_exists(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["proj_a"], RECORD_GROUPS)
        assert doc is not None
        assert doc["groupName"] == "Project Alpha"
        assert doc["groupType"] == "PROJECT"

    @pytest.mark.asyncio
    async def test_project_b_record_group_exists(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["proj_b"], RECORD_GROUPS)
        assert doc is not None
        assert doc["groupName"] == "Project Beta"

    @pytest.mark.asyncio
    async def test_team_permission_edge_exists(self) -> None:
        p, ids = self._build_scenario()
        edge = await p.get_edge(ids["eng_team"], GROUPS, ids["proj_a"], RECORD_GROUPS, PERMISSION)
        assert edge is not None
        assert edge["role"] == "READER"

    @pytest.mark.asyncio
    async def test_ticket_records_have_correct_type(self) -> None:
        p, ids = self._build_scenario()
        for tid in [ids["ticket_a1"], ids["ticket_b1"]]:
            doc = await p.get_document(tid, RECORDS)
            assert doc is not None
            assert doc["recordType"] == "TICKET"

    @pytest.mark.asyncio
    async def test_user_belongs_to_engineering_team(self) -> None:
        p, ids = self._build_scenario()
        edge = await p.get_edge(ids["eng1_key"], USERS, ids["eng_team"], GROUPS, BELONGS_TO)
        assert edge is not None

    @pytest.mark.asyncio
    async def test_user_both_belongs_to_two_teams(self) -> None:
        p, ids = self._build_scenario()
        e1 = await p.get_edge(ids["both_key"], USERS, ids["eng_team"], GROUPS, BELONGS_TO)
        e2 = await p.get_edge(ids["both_key"], USERS, ids["prod_team"], GROUPS, BELONGS_TO)
        assert e1 is not None
        assert e2 is not None

    @pytest.mark.asyncio
    async def test_adding_direct_permission_grants_extra_access(self) -> None:
        p, ids = self._build_scenario()
        await p.batch_create_edges(
            [perm_edge(ids["prod1_key"], USERS, ids["ticket_a1"], RECORDS, "READER")],
            PERMISSION,
        )
        vids = await p.get_accessible_virtual_record_ids("uid-prod1", ids["org_id"])
        assert ids["ticket_a1"] in vids.values()
        assert ids["ticket_b1"] in vids.values()


# ===========================================================================
# Scenario 3: Permission Inheritance
# ===========================================================================


class TestPermissionInheritance:
    """
    Test permission inheritance through folder hierarchy:
    KB -> Folder1 -> SubFolder1 -> File1
    Permissions at folder level should cascade down.
    """

    @staticmethod
    def _build_scenario() -> tuple[FakeGraphProvider, dict[str, str]]:
        p = FakeGraphProvider()
        ids: dict[str, str] = {}

        org_id = "org-inherit-1"
        ids["org_id"] = org_id
        p._ensure_collection(ORGS)[org_id] = make_org(org_id, "Inherit Org")

        user1_key = "u-inherit1"
        user2_key = "u-inherit2"
        user3_key = "u-inherit3"
        ids["user1_key"] = user1_key
        ids["user2_key"] = user2_key
        ids["user3_key"] = user3_key

        users_col = p._ensure_collection(USERS)
        users_col[user1_key] = make_user(user1_key, "uid-inh1", "inh1@co.com", org_id)
        users_col[user2_key] = make_user(user2_key, "uid-inh2", "inh2@co.com", org_id)
        users_col[user3_key] = make_user(user3_key, "uid-inh3", "inh3@co.com", org_id)

        app_id = "app-drive-inherit"
        ids["app_id"] = app_id
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "Drive")

        p._ensure_edge_collection(USER_APP_RELATION).extend([
            user_app_edge(user1_key, app_id),
            user_app_edge(user2_key, app_id),
            user_app_edge(user3_key, app_id),
        ])
        p._ensure_edge_collection(BELONGS_TO).extend([
            belongs_edge(user1_key, USERS, org_id, ORGS),
            belongs_edge(user2_key, USERS, org_id, ORGS),
            belongs_edge(user3_key, USERS, org_id, ORGS),
        ])

        kb_id = "rg-kb-inherit"
        folder1_id = "rec-folder1"
        subfolder1_id = "rec-subfolder1"
        file1_id = "rec-file1"
        file2_id = "rec-file2"
        folder2_id = "rec-folder2"
        file3_id = "rec-file3"
        ids.update({
            "kb_id": kb_id, "folder1_id": folder1_id,
            "subfolder1_id": subfolder1_id, "file1_id": file1_id,
            "file2_id": file2_id, "folder2_id": folder2_id,
            "file3_id": file3_id,
        })

        rg_col = p._ensure_collection(RECORD_GROUPS)
        rg_col[kb_id] = make_record_group(kb_id, org_id, app_id, "Knowledge Base")

        rec_col = p._ensure_collection(RECORDS)
        rec_col[folder1_id] = make_record(folder1_id, org_id, app_id, "Folder 1", external_record_id="ext-f1", mime_type="application/vnd.google-apps.folder")
        rec_col[subfolder1_id] = make_record(subfolder1_id, org_id, app_id, "Sub Folder 1", external_record_id="ext-sf1", external_parent_id="ext-f1", mime_type="application/vnd.google-apps.folder")
        rec_col[file1_id] = make_record(file1_id, org_id, app_id, "File 1.pdf", external_record_id="ext-file1", external_parent_id="ext-sf1")
        rec_col[file2_id] = make_record(file2_id, org_id, app_id, "File 2.docx", external_record_id="ext-file2", external_parent_id="ext-f1")
        rec_col[folder2_id] = make_record(folder2_id, org_id, app_id, "Folder 2", external_record_id="ext-f2", mime_type="application/vnd.google-apps.folder")
        rec_col[file3_id] = make_record(file3_id, org_id, app_id, "File 3.txt", external_record_id="ext-file3", external_parent_id="ext-f2")

        p._ensure_edge_collection(INHERIT_PERMISSIONS).extend([
            inherit_edge(folder1_id, RECORDS, kb_id, RECORD_GROUPS),
            inherit_edge(subfolder1_id, RECORDS, kb_id, RECORD_GROUPS),
            inherit_edge(file1_id, RECORDS, kb_id, RECORD_GROUPS),
            inherit_edge(file2_id, RECORDS, kb_id, RECORD_GROUPS),
            inherit_edge(folder2_id, RECORDS, kb_id, RECORD_GROUPS),
            inherit_edge(file3_id, RECORDS, kb_id, RECORD_GROUPS),
        ])

        p._ensure_edge_collection(PERMISSION).extend([
            perm_edge(user1_key, USERS, kb_id, RECORD_GROUPS, "READER"),
            perm_edge(user2_key, USERS, file3_id, RECORDS, "READER"),
        ])

        p._ensure_edge_collection(RECORD_RELATIONS).extend([
            relation_edge(folder1_id, subfolder1_id),
            relation_edge(folder1_id, file2_id),
            relation_edge(subfolder1_id, file1_id),
            relation_edge(folder2_id, file3_id),
        ])

        return p, ids

    @pytest.mark.asyncio
    async def test_permission_inherited_from_kb_to_folder(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh1", ids["org_id"])
        assert ids["folder1_id"] in vids.values()

    @pytest.mark.asyncio
    async def test_permission_inherited_from_kb_to_subfolder(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh1", ids["org_id"])
        assert ids["subfolder1_id"] in vids.values()

    @pytest.mark.asyncio
    async def test_permission_inherited_from_kb_to_deep_file(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh1", ids["org_id"])
        assert ids["file1_id"] in vids.values()

    @pytest.mark.asyncio
    async def test_permission_inherited_to_sibling_folder(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh1", ids["org_id"])
        assert ids["folder2_id"] in vids.values()

    @pytest.mark.asyncio
    async def test_permission_inherited_to_all_six_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh1", ids["org_id"])
        all_recs = {ids["folder1_id"], ids["subfolder1_id"], ids["file1_id"], ids["file2_id"], ids["folder2_id"], ids["file3_id"]}
        assert set(vids.values()) == all_recs

    @pytest.mark.asyncio
    async def test_direct_file_permission_overrides_no_folder_access(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh2", ids["org_id"])
        assert ids["file3_id"] in vids.values()

    @pytest.mark.asyncio
    async def test_direct_permission_user_cannot_see_other_files(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh2", ids["org_id"])
        assert ids["file1_id"] not in vids.values()
        assert ids["folder1_id"] not in vids.values()

    @pytest.mark.asyncio
    async def test_direct_permission_user_sees_only_one_record(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh2", ids["org_id"])
        assert len(vids) == 1

    @pytest.mark.asyncio
    async def test_no_permission_user_sees_nothing(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-inh3", ids["org_id"])
        assert len(vids) == 0

    @pytest.mark.asyncio
    async def test_revoking_kb_permission_removes_all_access(self) -> None:
        p, ids = self._build_scenario()
        await p.delete_edge(ids["user1_key"], USERS, ids["kb_id"], RECORD_GROUPS, PERMISSION)
        vids = await p.get_accessible_virtual_record_ids("uid-inh1", ids["org_id"])
        assert len(vids) == 0

    @pytest.mark.asyncio
    async def test_revoking_direct_permission_removes_file_access(self) -> None:
        p, ids = self._build_scenario()
        await p.delete_edge(ids["user2_key"], USERS, ids["file3_id"], RECORDS, PERMISSION)
        vids = await p.get_accessible_virtual_record_ids("uid-inh2", ids["org_id"])
        assert len(vids) == 0

    @pytest.mark.asyncio
    async def test_adding_permission_grants_new_access(self) -> None:
        p, ids = self._build_scenario()
        await p.batch_create_edges([perm_edge(ids["user3_key"], USERS, ids["file1_id"], RECORDS, "READER")], PERMISSION)
        vids = await p.get_accessible_virtual_record_ids("uid-inh3", ids["org_id"])
        assert ids["file1_id"] in vids.values()

    @pytest.mark.asyncio
    async def test_subfolder_children_via_hierarchy(self) -> None:
        p, ids = self._build_scenario()
        children = await p.get_children(ids["subfolder1_id"])
        assert ids["file1_id"] in {c["id"] for c in children}

    @pytest.mark.asyncio
    async def test_folder_parent_traversal(self) -> None:
        p, ids = self._build_scenario()
        parent = await p.get_parent(ids["subfolder1_id"])
        assert parent is not None
        assert parent["id"] == ids["folder1_id"]

    @pytest.mark.asyncio
    async def test_root_record_has_no_parent(self) -> None:
        p, ids = self._build_scenario()
        parent = await p.get_parent(ids["folder1_id"])
        assert parent is None


# ===========================================================================
# Scenario 4: Record Deduplication
# ===========================================================================


class TestRecordDeduplication:
    """Test duplicate detection via md5Checksum matching."""

    @staticmethod
    def _build_scenario() -> tuple[FakeGraphProvider, dict[str, str]]:
        p = FakeGraphProvider()
        ids: dict[str, str] = {}

        org_id = "org-dedup-1"
        ids["org_id"] = org_id
        app_id = "app-dedup"
        ids["app_id"] = app_id

        rec_col = p._ensure_collection(RECORDS)
        orig_id = "rec-orig"
        ids["orig_id"] = orig_id
        rec_col[orig_id] = make_record(orig_id, org_id, app_id, "Original.pdf", indexing_status="COMPLETED", md5_checksum="abc123hash", size_in_bytes=1024, virtual_record_id="vr-orig")

        dup1_id = "rec-dup1"
        ids["dup1_id"] = dup1_id
        rec_col[dup1_id] = make_record(dup1_id, org_id, app_id, "Copy of Original.pdf", indexing_status="QUEUED", md5_checksum="abc123hash", size_in_bytes=1024)

        dup2_id = "rec-dup2"
        ids["dup2_id"] = dup2_id
        rec_col[dup2_id] = make_record(dup2_id, org_id, app_id, "Another Copy.pdf", indexing_status="QUEUED", md5_checksum="abc123hash", size_in_bytes=1024)

        diff_id = "rec-different"
        ids["diff_id"] = diff_id
        rec_col[diff_id] = make_record(diff_id, org_id, app_id, "Different.pdf", indexing_status="COMPLETED", md5_checksum="xyz789hash", size_in_bytes=2048)

        same_hash_diff_type = "rec-same-hash-diff-type"
        ids["same_hash_diff_type"] = same_hash_diff_type
        rec_col[same_hash_diff_type] = make_record(same_hash_diff_type, org_id, app_id, "Ticket with same hash", record_type="TICKET", indexing_status="QUEUED", md5_checksum="abc123hash", size_in_bytes=1024)

        return p, ids

    @pytest.mark.asyncio
    async def test_find_duplicates_by_md5(self) -> None:
        p, ids = self._build_scenario()
        dups = await p.find_duplicate_records(ids["orig_id"], "abc123hash")
        dup_ids = {d["id"] for d in dups}
        assert ids["dup1_id"] in dup_ids
        assert ids["dup2_id"] in dup_ids
        assert ids["orig_id"] not in dup_ids

    @pytest.mark.asyncio
    async def test_find_duplicates_excludes_self(self) -> None:
        p, ids = self._build_scenario()
        dups = await p.find_duplicate_records(ids["orig_id"], "abc123hash")
        assert ids["orig_id"] not in {d["id"] for d in dups}

    @pytest.mark.asyncio
    async def test_find_duplicates_excludes_different_hash(self) -> None:
        p, ids = self._build_scenario()
        dups = await p.find_duplicate_records(ids["orig_id"], "abc123hash")
        assert ids["diff_id"] not in {d["id"] for d in dups}

    @pytest.mark.asyncio
    async def test_find_duplicates_with_type_filter(self) -> None:
        p, ids = self._build_scenario()
        dups = await p.find_duplicate_records(ids["orig_id"], "abc123hash", record_type="FILE")
        dup_ids = {d["id"] for d in dups}
        assert ids["dup1_id"] in dup_ids
        assert ids["same_hash_diff_type"] not in dup_ids

    @pytest.mark.asyncio
    async def test_find_next_queued_duplicate(self) -> None:
        p, ids = self._build_scenario()
        next_dup = await p.find_next_queued_duplicate(ids["orig_id"])
        assert next_dup is not None
        assert next_dup["md5Checksum"] == "abc123hash"
        assert next_dup["indexingStatus"] == "QUEUED"

    @pytest.mark.asyncio
    async def test_find_next_queued_duplicate_for_nonexistent_record(self) -> None:
        p, _ids = self._build_scenario()
        assert await p.find_next_queued_duplicate("nonexistent") is None

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status(self) -> None:
        p, ids = self._build_scenario()
        count = await p.update_queued_duplicates_status(ids["orig_id"], "COMPLETED", virtual_record_id="vr-orig")
        assert count == 3  # dup1, dup2, same_hash_diff_type

    @pytest.mark.asyncio
    async def test_update_sets_virtual_record_id(self) -> None:
        p, ids = self._build_scenario()
        await p.update_queued_duplicates_status(ids["orig_id"], "COMPLETED", virtual_record_id="vr-orig")
        dup1 = await p.get_document(ids["dup1_id"], RECORDS)
        assert dup1 is not None
        assert dup1["virtualRecordId"] == "vr-orig"
        assert dup1["indexingStatus"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_no_queued_duplicates_for_unique_hash(self) -> None:
        p, ids = self._build_scenario()
        assert await p.find_next_queued_duplicate(ids["diff_id"]) is None

    @pytest.mark.asyncio
    async def test_duplicate_count_for_md5(self) -> None:
        p, ids = self._build_scenario()
        dups = await p.find_duplicate_records(ids["orig_id"], "abc123hash")
        assert len(dups) == 3


# ===========================================================================
# Scenario 5: Connector Lifecycle
# ===========================================================================


class TestConnectorLifecycle:
    """Test full connector lifecycle: create, populate, deactivate, delete."""

    @staticmethod
    def _build_scenario() -> tuple[FakeGraphProvider, dict[str, str]]:
        p = FakeGraphProvider()
        ids: dict[str, str] = {}

        org_id = "org-lifecycle-1"
        ids["org_id"] = org_id
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)

        user_key = "u-lc1"
        ids["user_key"] = user_key
        p._ensure_collection(USERS)[user_key] = make_user(user_key, "uid-lc1", "lc1@co.com", org_id)

        app_id = "app-lc-1"
        ids["app_id"] = app_id
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "Drive Connector")

        p._ensure_edge_collection(USER_APP_RELATION).append(user_app_edge(user_key, app_id))
        p._ensure_edge_collection(BELONGS_TO).append(belongs_edge(user_key, USERS, org_id, ORGS))

        rec1 = "rec-lc1"
        rec2 = "rec-lc2"
        ids["rec1"] = rec1
        ids["rec2"] = rec2
        rec_col = p._ensure_collection(RECORDS)
        rec_col[rec1] = make_record(rec1, org_id, app_id, "File 1.pdf")
        rec_col[rec2] = make_record(rec2, org_id, app_id, "File 2.docx")

        rg_id = "rg-lc1"
        ids["rg_id"] = rg_id
        p._ensure_collection(RECORD_GROUPS)[rg_id] = make_record_group(rg_id, org_id, app_id, "Shared Drive")

        p._ensure_edge_collection(PERMISSION).append(perm_edge(user_key, USERS, rg_id, RECORD_GROUPS, "READER"))
        p._ensure_edge_collection(INHERIT_PERMISSIONS).extend([
            inherit_edge(rec1, RECORDS, rg_id, RECORD_GROUPS),
            inherit_edge(rec2, RECORDS, rg_id, RECORD_GROUPS),
        ])

        sp1_key = "sp-lc1"
        p._ensure_collection(SYNC_POINTS)[sp1_key] = {"id": sp1_key, "connectorId": app_id, "token": "page-token-1"}
        ids["sp1_key"] = sp1_key

        return p, ids

    @pytest.mark.asyncio
    async def test_connector_records_exist(self) -> None:
        p, ids = self._build_scenario()
        assert await p.get_document(ids["rec1"], RECORDS) is not None
        assert await p.get_document(ids["rec2"], RECORDS) is not None

    @pytest.mark.asyncio
    async def test_connector_sync_point_exists(self) -> None:
        p, ids = self._build_scenario()
        sp = await p.get_sync_point(ids["sp1_key"], SYNC_POINTS)
        assert sp is not None
        assert sp["token"] == "page-token-1"

    @pytest.mark.asyncio
    async def test_user_sees_connector_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-lc1", ids["org_id"])
        assert len(vids) == 2

    @pytest.mark.asyncio
    async def test_deactivate_connector_records_still_accessible(self) -> None:
        p, ids = self._build_scenario()
        await p.update_node(ids["app_id"], APPS, {"isActive": False})
        app = await p.get_document(ids["app_id"], APPS)
        assert app is not None
        assert app["isActive"] is False
        vids = await p.get_accessible_virtual_record_ids("uid-lc1", ids["org_id"])
        assert len(vids) == 2

    @pytest.mark.asyncio
    async def test_deactivate_removes_sync_capability(self) -> None:
        p, ids = self._build_scenario()
        await p.update_node(ids["app_id"], APPS, {"isActive": False})
        app = await p.get_document(ids["app_id"], APPS)
        assert app is not None
        assert app["isActive"] is False

    @pytest.mark.asyncio
    async def test_delete_connector_cleans_records(self) -> None:
        p, ids = self._build_scenario()
        result = await p.delete_all_connector_data(ids["app_id"])
        assert result["deleted_records"] == 2

    @pytest.mark.asyncio
    async def test_delete_connector_cleans_record_groups(self) -> None:
        p, ids = self._build_scenario()
        result = await p.delete_all_connector_data(ids["app_id"])
        assert result["deleted_groups"] == 1

    @pytest.mark.asyncio
    async def test_delete_connector_cleans_sync_points(self) -> None:
        p, ids = self._build_scenario()
        result = await p.delete_all_connector_data(ids["app_id"])
        assert result["deleted_sync_points"] == 1

    @pytest.mark.asyncio
    async def test_delete_connector_records_no_longer_exist(self) -> None:
        p, ids = self._build_scenario()
        await p.delete_all_connector_data(ids["app_id"])
        assert await p.get_document(ids["rec1"], RECORDS) is None
        assert await p.get_document(ids["rec2"], RECORDS) is None

    @pytest.mark.asyncio
    async def test_delete_connector_user_sees_nothing(self) -> None:
        p, ids = self._build_scenario()
        await p.delete_all_connector_data(ids["app_id"])
        vids = await p.get_accessible_virtual_record_ids("uid-lc1", ids["org_id"])
        assert len(vids) == 0

    @pytest.mark.asyncio
    async def test_delete_sync_edges_cleans_permission_edges(self) -> None:
        p, ids = self._build_scenario()
        total, success = await p.delete_connector_sync_edges(ids["app_id"])
        assert success is True
        assert total > 0
        vids = await p.get_accessible_virtual_record_ids("uid-lc1", ids["org_id"])
        assert len(vids) == 0

    @pytest.mark.asyncio
    async def test_delete_sync_points_by_connector(self) -> None:
        p, ids = self._build_scenario()
        count, success = await p.delete_sync_points_by_connector_id(ids["app_id"])
        assert count == 1
        assert success is True
        assert await p.get_sync_point(ids["sp1_key"], SYNC_POINTS) is None


# ===========================================================================
# Scenario 6: Multi-Connector Access
# ===========================================================================


class TestMultiConnectorAccess:
    """Test access across 2 connectors (Google Drive + Confluence). Same user has access to both."""

    @staticmethod
    def _build_scenario() -> tuple[FakeGraphProvider, dict[str, str]]:
        p = FakeGraphProvider()
        ids: dict[str, str] = {}

        org_id = "org-multi-1"
        ids["org_id"] = org_id
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)

        user_key = "u-multi1"
        user2_key = "u-multi2"
        ids["user_key"] = user_key
        ids["user2_key"] = user2_key

        users_col = p._ensure_collection(USERS)
        users_col[user_key] = make_user(user_key, "uid-multi1", "multi1@co.com", org_id)
        users_col[user2_key] = make_user(user2_key, "uid-multi2", "multi2@co.com", org_id)

        drive_app = "app-drive-m"
        conf_app = "app-conf-m"
        ids["drive_app"] = drive_app
        ids["conf_app"] = conf_app

        apps_col = p._ensure_collection(APPS)
        apps_col[drive_app] = make_app(drive_app, org_id, "Google Drive", "DRIVE")
        apps_col[conf_app] = make_app(conf_app, org_id, "Confluence", "CONFLUENCE")

        p._ensure_edge_collection(USER_APP_RELATION).extend([
            user_app_edge(user_key, drive_app),
            user_app_edge(user_key, conf_app),
            user_app_edge(user2_key, conf_app),
        ])
        p._ensure_edge_collection(BELONGS_TO).extend([
            belongs_edge(user_key, USERS, org_id, ORGS),
            belongs_edge(user2_key, USERS, org_id, ORGS),
        ])

        drive_rg = "rg-drive-m"
        conf_rg = "rg-conf-m"
        ids["drive_rg"] = drive_rg
        ids["conf_rg"] = conf_rg

        rg_col = p._ensure_collection(RECORD_GROUPS)
        rg_col[drive_rg] = make_record_group(drive_rg, org_id, drive_app, "My Drive", "DRIVE")
        rg_col[conf_rg] = make_record_group(conf_rg, org_id, conf_app, "Dev Space", "CONFLUENCE_SPACES", connector_name="CONFLUENCE")

        drive_rec1 = "rec-drive-m1"
        drive_rec2 = "rec-drive-m2"
        conf_rec1 = "rec-conf-m1"
        conf_rec2 = "rec-conf-m2"
        conf_rec3 = "rec-conf-m3"
        ids["drive_rec1"] = drive_rec1
        ids["drive_rec2"] = drive_rec2
        ids["conf_rec1"] = conf_rec1
        ids["conf_rec2"] = conf_rec2
        ids["conf_rec3"] = conf_rec3

        rec_col = p._ensure_collection(RECORDS)
        rec_col[drive_rec1] = make_record(drive_rec1, org_id, drive_app, "Drive File 1.pdf")
        rec_col[drive_rec2] = make_record(drive_rec2, org_id, drive_app, "Drive File 2.docx")
        rec_col[conf_rec1] = make_record(conf_rec1, org_id, conf_app, "Conf Page 1", record_type="CONFLUENCE_PAGE", connector_name="CONFLUENCE")
        rec_col[conf_rec2] = make_record(conf_rec2, org_id, conf_app, "Conf Page 2", record_type="CONFLUENCE_PAGE", connector_name="CONFLUENCE")
        rec_col[conf_rec3] = make_record(conf_rec3, org_id, conf_app, "Conf Page 3", record_type="CONFLUENCE_PAGE", connector_name="CONFLUENCE")

        p._ensure_edge_collection(INHERIT_PERMISSIONS).extend([
            inherit_edge(drive_rec1, RECORDS, drive_rg, RECORD_GROUPS),
            inherit_edge(drive_rec2, RECORDS, drive_rg, RECORD_GROUPS),
            inherit_edge(conf_rec1, RECORDS, conf_rg, RECORD_GROUPS),
            inherit_edge(conf_rec2, RECORDS, conf_rg, RECORD_GROUPS),
            inherit_edge(conf_rec3, RECORDS, conf_rg, RECORD_GROUPS),
        ])

        p._ensure_edge_collection(PERMISSION).extend([
            perm_edge(user_key, USERS, drive_rg, RECORD_GROUPS, "READER"),
            perm_edge(user_key, USERS, conf_rg, RECORD_GROUPS, "READER"),
            perm_edge(user2_key, USERS, conf_rg, RECORD_GROUPS, "READER"),
        ])

        return p, ids

    @pytest.mark.asyncio
    async def test_user1_sees_records_from_both_connectors(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-multi1", ids["org_id"])
        all_recs = {ids["drive_rec1"], ids["drive_rec2"], ids["conf_rec1"], ids["conf_rec2"], ids["conf_rec3"]}
        assert set(vids.values()) == all_recs

    @pytest.mark.asyncio
    async def test_user1_total_count_is_five(self) -> None:
        p, ids = self._build_scenario()
        assert len(await p.get_accessible_virtual_record_ids("uid-multi1", ids["org_id"])) == 5

    @pytest.mark.asyncio
    async def test_user2_sees_only_confluence_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-multi2", ids["org_id"])
        assert set(vids.values()) == {ids["conf_rec1"], ids["conf_rec2"], ids["conf_rec3"]}

    @pytest.mark.asyncio
    async def test_user2_cannot_see_drive_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-multi2", ids["org_id"])
        assert {ids["drive_rec1"], ids["drive_rec2"]}.isdisjoint(set(vids.values()))

    @pytest.mark.asyncio
    async def test_filtering_by_drive_app(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-multi1", ids["org_id"], filters={"apps": [ids["drive_app"]]})
        assert set(vids.values()) == {ids["drive_rec1"], ids["drive_rec2"]}

    @pytest.mark.asyncio
    async def test_filtering_by_confluence_app(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-multi1", ids["org_id"], filters={"apps": [ids["conf_app"]]})
        assert set(vids.values()) == {ids["conf_rec1"], ids["conf_rec2"], ids["conf_rec3"]}

    @pytest.mark.asyncio
    async def test_cross_connector_isolation_user2_drive_filter(self) -> None:
        """User2 only has confluence access; filtering by drive returns nothing."""
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-multi2", ids["org_id"], filters={"apps": [ids["drive_app"]]})
        assert len(vids) == 0

    @pytest.mark.asyncio
    async def test_records_from_drive_connector_have_correct_connector_id(self) -> None:
        p, ids = self._build_scenario()
        for rid in [ids["drive_rec1"], ids["drive_rec2"]]:
            doc = await p.get_document(rid, RECORDS)
            assert doc is not None
            assert doc["connectorId"] == ids["drive_app"]

    @pytest.mark.asyncio
    async def test_records_from_conf_connector_have_correct_connector_id(self) -> None:
        p, ids = self._build_scenario()
        for rid in [ids["conf_rec1"], ids["conf_rec2"], ids["conf_rec3"]]:
            doc = await p.get_document(rid, RECORDS)
            assert doc is not None
            assert doc["connectorId"] == ids["conf_app"]

    @pytest.mark.asyncio
    async def test_get_records_by_ids_returns_correct_docs(self) -> None:
        p, ids = self._build_scenario()
        recs = await p.get_records_by_record_ids([ids["drive_rec1"], ids["conf_rec1"]], ids["org_id"])
        assert len(recs) == 2
        assert {str(r["id"]) for r in recs} == {ids["drive_rec1"], ids["conf_rec1"]}


# ===========================================================================
# Scenario 7: Knowledge Base Management
# ===========================================================================


class TestKnowledgeBaseManagement:
    """Test KB creation, sharing, folder hierarchy, and role-based access."""

    @staticmethod
    def _build_scenario() -> tuple[FakeGraphProvider, dict[str, str]]:
        p = FakeGraphProvider()
        ids: dict[str, str] = {}

        org_id = "org-kb-1"
        ids["org_id"] = org_id
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)

        owner_key = "u-kb-owner"
        editor_key = "u-kb-editor"
        viewer_key = "u-kb-viewer"
        outsider_key = "u-kb-outsider"
        ids["owner_key"] = owner_key
        ids["editor_key"] = editor_key
        ids["viewer_key"] = viewer_key
        ids["outsider_key"] = outsider_key

        users_col = p._ensure_collection(USERS)
        users_col[owner_key] = make_user(owner_key, "uid-kb-owner", "owner@co.com", org_id)
        users_col[editor_key] = make_user(editor_key, "uid-kb-editor", "editor@co.com", org_id)
        users_col[viewer_key] = make_user(viewer_key, "uid-kb-viewer", "viewer@co.com", org_id)
        users_col[outsider_key] = make_user(outsider_key, "uid-kb-outsider", "outsider@co.com", org_id)

        kb_app = "knowledgeBase_kb1"
        ids["kb_app"] = kb_app
        p._ensure_collection(APPS)[kb_app] = make_app(kb_app, org_id, "Knowledge Base", "KNOWLEDGE_BASE")

        for uk in [owner_key, editor_key, viewer_key, outsider_key]:
            p._ensure_edge_collection(USER_APP_RELATION).append(user_app_edge(uk, kb_app))

        p._ensure_edge_collection(BELONGS_TO).extend([
            belongs_edge(owner_key, USERS, org_id, ORGS),
            belongs_edge(editor_key, USERS, org_id, ORGS),
            belongs_edge(viewer_key, USERS, org_id, ORGS),
            belongs_edge(outsider_key, USERS, org_id, ORGS),
        ])

        kb_id = "kb-1"
        ids["kb_id"] = kb_id
        p._ensure_collection(KNOWLEDGE_BASES)[kb_id] = make_kb(kb_id, org_id, "Engineering KB")

        kb_rec1 = "rec-kb1"
        kb_rec2 = "rec-kb2"
        kb_folder = "rec-kb-folder"
        kb_folder_file = "rec-kb-folder-file"
        ids["kb_rec1"] = kb_rec1
        ids["kb_rec2"] = kb_rec2
        ids["kb_folder"] = kb_folder
        ids["kb_folder_file"] = kb_folder_file

        rec_col = p._ensure_collection(RECORDS)
        rec_col[kb_rec1] = make_record(kb_rec1, org_id, kb_app, "KB Doc 1.pdf", origin="UPLOAD", connector_name="KNOWLEDGE_BASE")
        rec_col[kb_rec2] = make_record(kb_rec2, org_id, kb_app, "KB Doc 2.md", origin="UPLOAD", connector_name="KNOWLEDGE_BASE")
        rec_col[kb_folder] = make_record(kb_folder, org_id, kb_app, "Docs Folder", origin="UPLOAD", connector_name="KNOWLEDGE_BASE", external_record_id="ext-kb-folder", mime_type="text/directory")
        rec_col[kb_folder_file] = make_record(kb_folder_file, org_id, kb_app, "Nested KB File.txt", origin="UPLOAD", connector_name="KNOWLEDGE_BASE", external_record_id="ext-kb-ff", external_parent_id="ext-kb-folder")

        p._ensure_edge_collection(BELONGS_TO).extend([
            belongs_edge(kb_rec1, RECORDS, kb_id, KNOWLEDGE_BASES),
            belongs_edge(kb_rec2, RECORDS, kb_id, KNOWLEDGE_BASES),
            belongs_edge(kb_folder, RECORDS, kb_id, KNOWLEDGE_BASES),
            belongs_edge(kb_folder_file, RECORDS, kb_id, KNOWLEDGE_BASES),
        ])

        p._ensure_edge_collection(RECORD_RELATIONS).append(relation_edge(kb_folder, kb_folder_file))

        p._ensure_edge_collection(PERMISSION).extend([
            perm_edge(owner_key, USERS, kb_id, KNOWLEDGE_BASES, "OWNER"),
            perm_edge(editor_key, USERS, kb_id, KNOWLEDGE_BASES, "EDITOR"),
            perm_edge(viewer_key, USERS, kb_id, KNOWLEDGE_BASES, "VIEWER"),
        ])

        return p, ids

    @pytest.mark.asyncio
    async def test_kb_created(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["kb_id"], KNOWLEDGE_BASES)
        assert doc is not None
        assert doc["name"] == "Engineering KB"

    @pytest.mark.asyncio
    async def test_owner_has_full_access(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-kb-owner", ids["org_id"])
        assert {ids["kb_rec1"], ids["kb_rec2"], ids["kb_folder"], ids["kb_folder_file"]}.issubset(set(vids.values()))

    @pytest.mark.asyncio
    async def test_editor_can_see_all_kb_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-kb-editor", ids["org_id"])
        assert {ids["kb_rec1"], ids["kb_rec2"], ids["kb_folder"], ids["kb_folder_file"]}.issubset(set(vids.values()))

    @pytest.mark.asyncio
    async def test_viewer_can_see_all_kb_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-kb-viewer", ids["org_id"])
        assert {ids["kb_rec1"], ids["kb_rec2"], ids["kb_folder"], ids["kb_folder_file"]}.issubset(set(vids.values()))

    @pytest.mark.asyncio
    async def test_outsider_cannot_access_kb(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-kb-outsider", ids["org_id"])
        assert {ids["kb_rec1"], ids["kb_rec2"], ids["kb_folder"], ids["kb_folder_file"]}.isdisjoint(set(vids.values()))

    @pytest.mark.asyncio
    async def test_owner_permission_edge_has_owner_role(self) -> None:
        p, ids = self._build_scenario()
        edge = await p.get_edge(ids["owner_key"], USERS, ids["kb_id"], KNOWLEDGE_BASES, PERMISSION)
        assert edge is not None
        assert edge["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_editor_permission_edge_has_editor_role(self) -> None:
        p, ids = self._build_scenario()
        edge = await p.get_edge(ids["editor_key"], USERS, ids["kb_id"], KNOWLEDGE_BASES, PERMISSION)
        assert edge is not None
        assert edge["role"] == "EDITOR"

    @pytest.mark.asyncio
    async def test_kb_folder_has_child(self) -> None:
        p, ids = self._build_scenario()
        children = await p.get_children(ids["kb_folder"])
        assert ids["kb_folder_file"] in {c["id"] for c in children}

    @pytest.mark.asyncio
    async def test_kb_folder_file_parent_is_folder(self) -> None:
        p, ids = self._build_scenario()
        parent = await p.get_parent(ids["kb_folder_file"])
        assert parent is not None
        assert parent["id"] == ids["kb_folder"]

    @pytest.mark.asyncio
    async def test_removing_kb_permission_revokes_access(self) -> None:
        p, ids = self._build_scenario()
        await p.delete_edge(ids["viewer_key"], USERS, ids["kb_id"], KNOWLEDGE_BASES, PERMISSION)
        vids = await p.get_accessible_virtual_record_ids("uid-kb-viewer", ids["org_id"])
        assert {ids["kb_rec1"], ids["kb_rec2"], ids["kb_folder"], ids["kb_folder_file"]}.isdisjoint(set(vids.values()))

    @pytest.mark.asyncio
    async def test_kb_filter_returns_only_matching_kb(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-kb-owner", ids["org_id"], filters={"kb": [ids["kb_id"]]})
        assert {ids["kb_rec1"], ids["kb_rec2"], ids["kb_folder"], ids["kb_folder_file"]}.issubset(set(vids.values()))

    @pytest.mark.asyncio
    async def test_kb_filter_with_wrong_kb_returns_empty(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-kb-owner", ids["org_id"], filters={"kb": ["nonexistent-kb"]})
        assert {ids["kb_rec1"], ids["kb_rec2"], ids["kb_folder"], ids["kb_folder_file"]}.isdisjoint(set(vids.values()))

    @pytest.mark.asyncio
    async def test_team_based_kb_access(self) -> None:
        """Add a team that has access to the KB, verify team member sees KB records."""
        p, ids = self._build_scenario()
        team_id = "team-kb-access"
        p._ensure_collection(TEAMS)[team_id] = make_team(team_id, ids["org_id"], "KB Access Team")
        p._ensure_edge_collection(BELONGS_TO).append(belongs_edge(ids["outsider_key"], USERS, team_id, TEAMS))
        p._ensure_edge_collection(PERMISSION).append(perm_edge(team_id, TEAMS, ids["kb_id"], KNOWLEDGE_BASES, "VIEWER"))
        vids = await p.get_accessible_virtual_record_ids("uid-kb-outsider", ids["org_id"])
        assert {ids["kb_rec1"], ids["kb_rec2"], ids["kb_folder"], ids["kb_folder_file"]}.issubset(set(vids.values()))


# ===========================================================================
# Scenario 8: Record Group Permissions
# ===========================================================================


class TestRecordGroupPermissions:
    """Test record group (Slack channel) permissions: members can access, non-members cannot."""

    @staticmethod
    def _build_scenario() -> tuple[FakeGraphProvider, dict[str, str]]:
        p = FakeGraphProvider()
        ids: dict[str, str] = {}

        org_id = "org-rg-1"
        ids["org_id"] = org_id
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)

        member_key = "u-rg-member"
        nonmember_key = "u-rg-nonmember"
        ids["member_key"] = member_key
        ids["nonmember_key"] = nonmember_key

        users_col = p._ensure_collection(USERS)
        users_col[member_key] = make_user(member_key, "uid-rg-member", "member@co.com", org_id)
        users_col[nonmember_key] = make_user(nonmember_key, "uid-rg-nonmember", "nonmember@co.com", org_id)

        app_id = "app-slack-rg"
        ids["app_id"] = app_id
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "Slack", "SLACK")

        p._ensure_edge_collection(USER_APP_RELATION).extend([user_app_edge(member_key, app_id), user_app_edge(nonmember_key, app_id)])
        p._ensure_edge_collection(BELONGS_TO).extend([belongs_edge(member_key, USERS, org_id, ORGS), belongs_edge(nonmember_key, USERS, org_id, ORGS)])

        channel_id = "rg-channel-general"
        ids["channel_id"] = channel_id
        p._ensure_collection(RECORD_GROUPS)[channel_id] = make_record_group(channel_id, org_id, app_id, "#general", "SLACK_CHANNEL", "C123", "SLACK")

        msg1 = "rec-msg1"
        msg2 = "rec-msg2"
        msg3 = "rec-msg3"
        ids["msg1"] = msg1
        ids["msg2"] = msg2
        ids["msg3"] = msg3

        rec_col = p._ensure_collection(RECORDS)
        for mid, name in [(msg1, "Message 1"), (msg2, "Message 2"), (msg3, "Message 3")]:
            rec_col[mid] = make_record(mid, org_id, app_id, name, record_type="MESSAGE", connector_name="SLACK")

        p._ensure_edge_collection(INHERIT_PERMISSIONS).extend([
            inherit_edge(msg1, RECORDS, channel_id, RECORD_GROUPS),
            inherit_edge(msg2, RECORDS, channel_id, RECORD_GROUPS),
            inherit_edge(msg3, RECORDS, channel_id, RECORD_GROUPS),
        ])
        p._ensure_edge_collection(PERMISSION).append(perm_edge(member_key, USERS, channel_id, RECORD_GROUPS, "READER"))

        return p, ids

    @pytest.mark.asyncio
    async def test_member_sees_channel_records(self) -> None:
        p, ids = self._build_scenario()
        vids = await p.get_accessible_virtual_record_ids("uid-rg-member", ids["org_id"])
        assert set(vids.values()) == {ids["msg1"], ids["msg2"], ids["msg3"]}

    @pytest.mark.asyncio
    async def test_nonmember_cannot_see_channel_records(self) -> None:
        p, ids = self._build_scenario()
        assert len(await p.get_accessible_virtual_record_ids("uid-rg-nonmember", ids["org_id"])) == 0

    @pytest.mark.asyncio
    async def test_adding_membership_grants_access(self) -> None:
        p, ids = self._build_scenario()
        await p.batch_create_edges([perm_edge(ids["nonmember_key"], USERS, ids["channel_id"], RECORD_GROUPS, "READER")], PERMISSION)
        vids = await p.get_accessible_virtual_record_ids("uid-rg-nonmember", ids["org_id"])
        assert set(vids.values()) == {ids["msg1"], ids["msg2"], ids["msg3"]}

    @pytest.mark.asyncio
    async def test_removing_membership_revokes_access(self) -> None:
        p, ids = self._build_scenario()
        await p.delete_edge(ids["member_key"], USERS, ids["channel_id"], RECORD_GROUPS, PERMISSION)
        assert len(await p.get_accessible_virtual_record_ids("uid-rg-member", ids["org_id"])) == 0

    @pytest.mark.asyncio
    async def test_member_count_is_three(self) -> None:
        p, ids = self._build_scenario()
        assert len(await p.get_accessible_virtual_record_ids("uid-rg-member", ids["org_id"])) == 3

    @pytest.mark.asyncio
    async def test_record_group_document_exists(self) -> None:
        p, ids = self._build_scenario()
        doc = await p.get_document(ids["channel_id"], RECORD_GROUPS)
        assert doc is not None
        assert doc["groupType"] == "SLACK_CHANNEL"

    @pytest.mark.asyncio
    async def test_adding_new_record_to_group_visible_to_member(self) -> None:
        p, ids = self._build_scenario()
        new_msg = "rec-msg-new"
        p._ensure_collection(RECORDS)[new_msg] = make_record(new_msg, ids["org_id"], ids["app_id"], "New Message", record_type="MESSAGE", connector_name="SLACK")
        p._ensure_edge_collection(INHERIT_PERMISSIONS).append(inherit_edge(new_msg, RECORDS, ids["channel_id"], RECORD_GROUPS))
        vids = await p.get_accessible_virtual_record_ids("uid-rg-member", ids["org_id"])
        assert new_msg in vids.values()
        assert len(vids) == 4

    @pytest.mark.asyncio
    async def test_removing_record_from_group_hides_it(self) -> None:
        p, ids = self._build_scenario()
        await p.delete_edge(ids["msg1"], RECORDS, ids["channel_id"], RECORD_GROUPS, INHERIT_PERMISSIONS)
        vids = await p.get_accessible_virtual_record_ids("uid-rg-member", ids["org_id"])
        assert ids["msg1"] not in vids.values()
        assert len(vids) == 2

    @pytest.mark.asyncio
    async def test_anyone_access_grants_record_to_all(self) -> None:
        """Test that adding a record to the 'anyone' collection makes it accessible to non-members."""
        p, ids = self._build_scenario()
        p._ensure_collection(ANYONE)["anyone-1"] = {"id": "anyone-1", "organization": ids["org_id"], "file_key": ids["msg1"]}
        vids = await p.get_accessible_virtual_record_ids("uid-rg-nonmember", ids["org_id"])
        assert ids["msg1"] in vids.values()

    @pytest.mark.asyncio
    async def test_group_based_permission_via_belongs_to(self) -> None:
        """Test that a user gets access through a group that has permission to the record group."""
        p, ids = self._build_scenario()
        group_id = "grp-slack-access"
        p._ensure_collection(GROUPS)[group_id] = {"id": group_id, "name": "Slack Group", "orgId": ids["org_id"]}
        p._ensure_edge_collection(BELONGS_TO).append(belongs_edge(ids["nonmember_key"], USERS, group_id, GROUPS))
        p._ensure_edge_collection(PERMISSION).append(perm_edge(group_id, GROUPS, ids["channel_id"], RECORD_GROUPS, "READER"))
        vids = await p.get_accessible_virtual_record_ids("uid-rg-nonmember", ids["org_id"])
        assert set(vids.values()) == {ids["msg1"], ids["msg2"], ids["msg3"]}


# ===========================================================================
# Additional Edge Case Tests
# ===========================================================================


class TestEdgeCases:
    """Additional edge cases across scenarios."""

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_creates_documents(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([{"id": "n1", "name": "Node 1"}, {"id": "n2", "name": "Node 2"}], "test_col")
        d1 = await p.get_document("n1", "test_col")
        d2 = await p.get_document("n2", "test_col")
        assert d1 is not None and d1["name"] == "Node 1"
        assert d2 is not None and d2["name"] == "Node 2"

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_updates_existing(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([{"id": "n1", "name": "Old"}], "test_col")
        await p.batch_upsert_nodes([{"id": "n1", "name": "New"}], "test_col")
        doc = await p.get_document("n1", "test_col")
        assert doc is not None
        assert doc["name"] == "New"

    @pytest.mark.asyncio
    async def test_delete_nodes_removes_documents(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([{"id": "n1"}, {"id": "n2"}], "test_col")
        await p.delete_nodes(["n1"], "test_col")
        assert await p.get_document("n1", "test_col") is None
        assert await p.get_document("n2", "test_col") is not None

    @pytest.mark.asyncio
    async def test_update_node_modifies_fields(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([{"id": "n1", "name": "A", "count": 0}], "test_col")
        await p.update_node("n1", "test_col", {"count": 5})
        doc = await p.get_document("n1", "test_col")
        assert doc is not None
        assert doc["count"] == 5
        assert doc["name"] == "A"

    @pytest.mark.asyncio
    async def test_update_node_returns_false_for_missing(self) -> None:
        p = FakeGraphProvider()
        assert await p.update_node("missing", "test_col", {"x": 1}) is False

    @pytest.mark.asyncio
    async def test_get_document_returns_none_for_missing(self) -> None:
        p = FakeGraphProvider()
        assert await p.get_document("missing", "test_col") is None

    @pytest.mark.asyncio
    async def test_batch_create_edges_deduplicates(self) -> None:
        p = FakeGraphProvider()
        edge = {"from_id": "a", "from_collection": "x", "to_id": "b", "to_collection": "y", "role": "R1"}
        await p.batch_create_edges([edge], "test_edges")
        await p.batch_create_edges([{**edge, "role": "R2"}], "test_edges")
        all_edges = p._edges["test_edges"]
        assert len(all_edges) == 1
        assert all_edges[0]["role"] == "R2"

    @pytest.mark.asyncio
    async def test_delete_edge_returns_false_for_missing(self) -> None:
        p = FakeGraphProvider()
        assert await p.delete_edge("a", "x", "b", "y", "test_edges") is False

    @pytest.mark.asyncio
    async def test_delete_edges_from_node(self) -> None:
        p = FakeGraphProvider()
        await p.batch_create_edges([
            {"from_id": "a", "from_collection": "x", "to_id": "b1", "to_collection": "y"},
            {"from_id": "a", "from_collection": "x", "to_id": "b2", "to_collection": "y"},
            {"from_id": "c", "from_collection": "x", "to_id": "b3", "to_collection": "y"},
        ], "test_edges")
        assert await p.delete_edges_from("a", "x", "test_edges") == 2
        assert len(p._edges["test_edges"]) == 1

    @pytest.mark.asyncio
    async def test_delete_edges_to_node(self) -> None:
        p = FakeGraphProvider()
        await p.batch_create_edges([
            {"from_id": "a1", "from_collection": "x", "to_id": "b", "to_collection": "y"},
            {"from_id": "a2", "from_collection": "x", "to_id": "b", "to_collection": "y"},
            {"from_id": "a3", "from_collection": "x", "to_id": "c", "to_collection": "y"},
        ], "test_edges")
        assert await p.delete_edges_to("b", "y", "test_edges") == 2
        assert len(p._edges["test_edges"]) == 1

    @pytest.mark.asyncio
    async def test_get_edges_from_node(self) -> None:
        p = FakeGraphProvider()
        await p.batch_create_edges([
            {"from_id": "a", "from_collection": "x", "to_id": "b1", "to_collection": "y"},
            {"from_id": "a", "from_collection": "x", "to_id": "b2", "to_collection": "y"},
        ], "test_edges")
        assert len(await p.get_edges_from_node("x/a", "test_edges")) == 2

    @pytest.mark.asyncio
    async def test_get_edges_to_node(self) -> None:
        p = FakeGraphProvider()
        await p.batch_create_edges([
            {"from_id": "a1", "from_collection": "x", "to_id": "b", "to_collection": "y"},
            {"from_id": "a2", "from_collection": "x", "to_id": "b", "to_collection": "y"},
        ], "test_edges")
        assert len(await p.get_edges_to_node("y/b", "test_edges")) == 2

    @pytest.mark.asyncio
    async def test_get_related_nodes_outbound(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([{"id": "b1", "name": "B1"}, {"id": "b2", "name": "B2"}], "y")
        await p.batch_create_edges([
            {"from_id": "a", "from_collection": "x", "to_id": "b1", "to_collection": "y"},
            {"from_id": "a", "from_collection": "x", "to_id": "b2", "to_collection": "y"},
        ], "test_edges")
        assert len(await p.get_related_nodes("x/a", "test_edges", "y", direction="outbound")) == 2

    @pytest.mark.asyncio
    async def test_get_related_nodes_inbound(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([{"id": "a1", "name": "A1"}, {"id": "a2", "name": "A2"}], "x")
        await p.batch_create_edges([
            {"from_id": "a1", "from_collection": "x", "to_id": "b", "to_collection": "y"},
            {"from_id": "a2", "from_collection": "x", "to_id": "b", "to_collection": "y"},
        ], "test_edges")
        assert len(await p.get_related_nodes("y/b", "test_edges", "x", direction="inbound")) == 2

    @pytest.mark.asyncio
    async def test_get_nodes_by_filters(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([
            {"id": "1", "type": "A", "status": "active"},
            {"id": "2", "type": "B", "status": "active"},
            {"id": "3", "type": "A", "status": "inactive"},
        ], "test_col")
        results = await p.get_nodes_by_filters("test_col", {"type": "A", "status": "active"})
        assert len(results) == 1
        assert results[0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_get_nodes_by_field_in(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([{"id": "1", "status": "A"}, {"id": "2", "status": "B"}, {"id": "3", "status": "C"}], "test_col")
        assert len(await p.get_nodes_by_field_in("test_col", "status", ["A", "C"])) == 2

    @pytest.mark.asyncio
    async def test_get_all_documents(self) -> None:
        p = FakeGraphProvider()
        await p.batch_upsert_nodes([{"id": "a"}, {"id": "b"}, {"id": "c"}], "test_col")
        assert len(await p.get_all_documents("test_col")) == 3

    @pytest.mark.asyncio
    async def test_incomplete_record_not_in_accessible_ids(self) -> None:
        """Records with indexingStatus != COMPLETED should not appear."""
        p = FakeGraphProvider()
        org_id = "org-ec-1"
        user_key = "u-ec1"
        app_id = "app-ec1"
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)
        p._ensure_collection(USERS)[user_key] = make_user(user_key, "uid-ec1", "ec@co.com", org_id)
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "App")
        p._ensure_edge_collection(USER_APP_RELATION).append(user_app_edge(user_key, app_id))
        p._ensure_edge_collection(BELONGS_TO).append(belongs_edge(user_key, USERS, org_id, ORGS))
        p._ensure_collection(RECORDS)["rec-queued"] = make_record("rec-queued", org_id, app_id, "Queued File", indexing_status="QUEUED")
        p._ensure_edge_collection(PERMISSION).append(perm_edge(user_key, USERS, "rec-queued", RECORDS, "READER"))
        assert "rec-queued" not in (await p.get_accessible_virtual_record_ids("uid-ec1", org_id)).values()

    @pytest.mark.asyncio
    async def test_connector_record_without_app_relation_not_visible(self) -> None:
        """User must have userAppRelation to the app to see its connector records."""
        p = FakeGraphProvider()
        org_id = "org-ec-2"
        user_key = "u-ec2"
        app_id = "app-ec2"
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)
        p._ensure_collection(USERS)[user_key] = make_user(user_key, "uid-ec2", "ec2@co.com", org_id)
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "App")
        # Deliberately NOT adding userAppRelation
        p._ensure_edge_collection(BELONGS_TO).append(belongs_edge(user_key, USERS, org_id, ORGS))
        p._ensure_collection(RECORDS)["rec-no-app"] = make_record("rec-no-app", org_id, app_id, "Orphan File")
        p._ensure_edge_collection(PERMISSION).append(perm_edge(user_key, USERS, "rec-no-app", RECORDS, "READER"))
        assert "rec-no-app" not in (await p.get_accessible_virtual_record_ids("uid-ec2", org_id)).values()

    @pytest.mark.asyncio
    async def test_org_level_permission_grants_access(self) -> None:
        """Test path 4: user->org->record permission."""
        p = FakeGraphProvider()
        org_id = "org-ec-3"
        user_key = "u-ec3"
        app_id = "app-ec3"
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)
        p._ensure_collection(USERS)[user_key] = make_user(user_key, "uid-ec3", "ec3@co.com", org_id)
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "App")
        p._ensure_edge_collection(USER_APP_RELATION).append(user_app_edge(user_key, app_id))
        p._ensure_edge_collection(BELONGS_TO).append(belongs_edge(user_key, USERS, org_id, ORGS))
        p._ensure_collection(RECORDS)["rec-org-perm"] = make_record("rec-org-perm", org_id, app_id, "Org Record")
        p._ensure_edge_collection(PERMISSION).append(perm_edge(org_id, ORGS, "rec-org-perm", RECORDS, "READER"))
        assert "rec-org-perm" in (await p.get_accessible_virtual_record_ids("uid-ec3", org_id)).values()

    @pytest.mark.asyncio
    async def test_role_based_permission_to_record_group(self) -> None:
        """Test path 6: user->role->recordGroup->records."""
        p = FakeGraphProvider()
        org_id = "org-ec-4"
        user_key = "u-ec4"
        app_id = "app-ec4"
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)
        p._ensure_collection(USERS)[user_key] = make_user(user_key, "uid-ec4", "ec4@co.com", org_id)
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "App")
        p._ensure_edge_collection(USER_APP_RELATION).append(user_app_edge(user_key, app_id))
        p._ensure_edge_collection(BELONGS_TO).append(belongs_edge(user_key, USERS, org_id, ORGS))
        p._ensure_collection(ROLES)["role-admin"] = {"id": "role-admin", "name": "Admin Role", "orgId": org_id}
        p._ensure_edge_collection(PERMISSION).append(perm_edge(user_key, USERS, "role-admin", ROLES, "MEMBER"))
        p._ensure_collection(RECORD_GROUPS)["rg-role-test"] = make_record_group("rg-role-test", org_id, app_id, "Role Drive")
        p._ensure_edge_collection(PERMISSION).append(perm_edge("role-admin", ROLES, "rg-role-test", RECORD_GROUPS, "READER"))
        p._ensure_collection(RECORDS)["rec-role-access"] = make_record("rec-role-access", org_id, app_id, "Role File")
        p._ensure_edge_collection(INHERIT_PERMISSIONS).append(inherit_edge("rec-role-access", RECORDS, "rg-role-test", RECORD_GROUPS))
        assert "rec-role-access" in (await p.get_accessible_virtual_record_ids("uid-ec4", org_id)).values()

    @pytest.mark.asyncio
    async def test_org_record_group_permission_path(self) -> None:
        """Test path 5: user->org->recordGroup->records via inheritPermissions."""
        p = FakeGraphProvider()
        org_id = "org-ec-5"
        user_key = "u-ec5"
        app_id = "app-ec5"
        p._ensure_collection(ORGS)[org_id] = make_org(org_id)
        p._ensure_collection(USERS)[user_key] = make_user(user_key, "uid-ec5", "ec5@co.com", org_id)
        p._ensure_collection(APPS)[app_id] = make_app(app_id, org_id, "App")
        p._ensure_edge_collection(USER_APP_RELATION).append(user_app_edge(user_key, app_id))
        p._ensure_edge_collection(BELONGS_TO).append(belongs_edge(user_key, USERS, org_id, ORGS))
        p._ensure_collection(RECORD_GROUPS)["rg-org-test"] = make_record_group("rg-org-test", org_id, app_id, "Org Drive")
        p._ensure_edge_collection(PERMISSION).append(perm_edge(org_id, ORGS, "rg-org-test", RECORD_GROUPS, "READER"))
        p._ensure_collection(RECORDS)["rec-org-rg"] = make_record("rec-org-rg", org_id, app_id, "Org RG File")
        p._ensure_edge_collection(INHERIT_PERMISSIONS).append(inherit_edge("rec-org-rg", RECORDS, "rg-org-test", RECORD_GROUPS))
        assert "rec-org-rg" in (await p.get_accessible_virtual_record_ids("uid-ec5", org_id)).values()
