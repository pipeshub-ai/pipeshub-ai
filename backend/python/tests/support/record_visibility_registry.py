"""What every record-reading method of ``IGraphDBProvider`` returns for a record in the trash.

A record is in the trash while ``isDeleted`` is true. Each method is in exactly
one class:

``PARAM``  takes ``visibility: RecordVisibility`` (default ``LIVE``).
``LIVE``   always leaves trashed records out; no caller may see them.
``ALL``    returns trashed records too, on purpose; the reason says why.
``WRITE``  changes records rather than reading them.

``tests/unit/services/graph_db/test_record_visibility_registry.py`` fails when a
record method on the interface is missing here, and
``tests/integration/test_record_visibility_e2e.py`` checks the classes against a
real Neo4j and a real ArangoDB. Adding a method to the interface means adding it
here, which means deciding what it does with the trash.
"""

from __future__ import annotations

from enum import Enum


class Rule(str, Enum):
    PARAM = "PARAM"
    LIVE = "LIVE"
    ALL = "ALL"
    WRITE = "WRITE"


_SYNC_LOOKUP = (
    "connector sync lookup: None makes the caller create the record, so hiding "
    "a trashed one would mint a duplicate"
)
_BY_KEY = "point read by key; the caller already holds the id and decides"
_STRUCTURE = "graph structure around a record the caller already resolved"
_FK_NEIGHBOURS = (
    _STRUCTURE + "; callers that name the neighbours (chat, fetch_full_record) keep only "
    "those get_records_by_record_ids returns LIVE"
)

REGISTRY: dict[str, tuple[Rule, str]] = {
    # Visibility chosen by the caller.
    "get_record_by_external_id": (Rule.PARAM, "connector sync passes ALL through GraphDataStore"),
    "get_records_by_status": (Rule.PARAM, "reindex, rebuild and sync sweeps must skip the trash"),
    "get_records_by_parent": (Rule.PARAM, "a folder whose children are all trashed reads as empty"),
    "get_records_by_record_ids": (Rule.PARAM, "search hydrates only live records"),
    "get_records_by_virtual_record_id": (
        Rule.PARAM,
        "LIVE decides whether a VRID's vectors may be deleted; the orphan sweeper asks DELETED",
    ),
    "get_record_by_external_revision_id": (
        Rule.PARAM,
        "rename detection asks LIVE: after a hard delete there is no old record, so the item is new",
    ),
    # Gates: a trashed record must never pass.
    "check_record_access_with_details": (Rule.LIVE, "the access check: trashed means no access"),
    "get_accessible_virtual_record_ids": (Rule.LIVE, "the search permission map"),
    "filter_accessible_virtual_record_ids": (Rule.LIVE, "search permission check"),
    "filter_accessible_record_ids": (Rule.LIVE, "search permission check"),
    "get_virtual_record_ids_shared_outside_connector": (
        Rule.LIVE,
        "content a connector delete rebuilds; only a live record elsewhere is re-indexed for it",
    ),
    "find_duplicate_records": (Rule.LIVE, "a copy must not take COMPLETED from a record without vectors"),
    "find_next_queued_duplicate": (Rule.LIVE, "dedup never hands work to a trashed record"),
    "update_queued_duplicates_status": (Rule.LIVE, "dedup never copies status onto a trashed record"),
    "get_failed_records_with_active_users": (Rule.LIVE, "retrying a trashed record would re-index it"),
    "get_failed_records_by_org": (Rule.LIVE, "retrying a trashed record would re-index it"),
    "get_records_by_record_group": (Rule.LIVE, "reindex keyset walk"),
    "get_records_by_parent_record": (Rule.LIVE, "reindex keyset walk"),
    "get_record_by_weburl": (Rule.LIVE, "resolves links and agent references"),
    "get_linked_records": (Rule.LIVE, "shown to users"),
    "get_entity_candidate_records": (Rule.LIVE, "knowledge-graph entity tools list these records to users"),
    "get_permitted_entity_records": (Rule.LIVE, "knowledge-graph entity tools list these records to users"),
    "filter_nodes_with_permission_role": (
        Rule.LIVE, "the Location trail's ancestor check: a trashed folder ends the trail, unnamed",
    ),
    "get_records_pending_duplicate_reconcile": (
        Rule.LIVE, "the reconcile sweep copies taxonomy onto duplicates; a trashed record gets none",
    ),
    # Browse surfaces, already filtered.
    # Arango delegates to list_all_records. Neo4j's get_records takes record ids
    # instead (a signature mismatch that predates soft delete).
    "get_records": (Rule.LIVE, "All Records list"),
    "list_all_records": (Rule.LIVE, "All Records list"),
    "list_kb_records": (Rule.LIVE, "KB record list"),
    "get_kb_children": (Rule.LIVE, "KB browse"),
    "get_folder_children": (Rule.LIVE, "folder browse"),
    "get_knowledge_hub_children": (Rule.LIVE, "Knowledge Hub browse; Neo4j gained the filter in PR1"),
    "get_knowledge_hub_search": (Rule.LIVE, "Knowledge Hub search"),
    "get_connector_stats": (Rule.LIVE, "counts"),
    # Returned whatever their state.
    "get_record_by_id": (Rule.ALL, _BY_KEY + "; content access is gated by TieredRecordAuthorizer"),
    "get_typed_records_batch": (Rule.ALL, _BY_KEY),
    "get_file_record_by_id": (Rule.ALL, _BY_KEY),
    "get_record_by_path": (Rule.ALL, _SYNC_LOOKUP),
    "get_record_key_by_external_id": (Rule.ALL, _SYNC_LOOKUP),
    "get_record_by_conversation_index": (Rule.ALL, _SYNC_LOOKUP),
    "get_record_by_issue_key": (Rule.ALL, _SYNC_LOOKUP),
    "find_slack_burst_record_by_ts": (Rule.ALL, _SYNC_LOOKUP),
    "get_records_by_record_type": (Rule.ALL, _SYNC_LOOKUP),
    "get_related_records_by_relation_type": (Rule.ALL, _SYNC_LOOKUP),
    "get_existing_record_keys": (Rule.ALL, "upsert pre-check by key"),
    "page_records_for_vector_membership_backfill": (Rule.ALL, "backfill walks every stored record"),
    "get_virtual_record_ids_for_record_ids": (Rule.ALL, _BY_KEY),
    "get_child_record_ids_by_relation_type": (Rule.ALL, _FK_NEIGHBOURS),
    "get_parent_record_ids_by_relation_type": (Rule.ALL, _FK_NEIGHBOURS),
    "get_record_relations_batch": (Rule.ALL, _STRUCTURE),
    "get_record_parent_adjacency": (
        Rule.ALL, _STRUCTURE + "; Location drops a trashed ancestor with filter_nodes_with_permission_role",
    ),
    "get_record_parent_info": (Rule.ALL, _STRUCTURE),
    "is_record_folder": (Rule.ALL, _STRUCTURE),
    "is_record_descendant_of": (Rule.ALL, _STRUCTURE),
    "get_record_owner_source_user_email": (Rule.ALL, _STRUCTURE),
    "get_taxonomy_entities_for_record": (Rule.ALL, _STRUCTURE),
    "move_taxonomy_edges": (
        Rule.ALL, "a merge or migration moves a trashed record's edges too, so a restore finds them on the new node",
    ),
    "find_legacy_taxonomy_nodes": (
        Rule.ALL, "counts a trashed record's legacy edges, which migrate-legacy must move as well",
    ),
    "get_record_path": (Rule.ALL, _STRUCTURE),
    "get_record_path_segments": (Rule.ALL, "storage path of a record the caller already resolved"),
    "get_records_in_delete_batch": (
        Rule.ALL,
        "restore reads the batch it brings back, and every record in it is in the trash",
    ),
    "get_purgeable_trashed_records": (
        Rule.ALL, "the purge reads only the trash: records past the retention, never a live one",
    ),
    "list_trashed_records": (
        Rule.ALL, "the Recently deleted page lists only the trash: what a user may restore, never a live record",
    ),
    "get_descendant_virtual_record_ids": (
        Rule.ALL,
        "a storage move takes every stored file under the folder, or a restored record would lose its content",
    ),
    # Writes.
    "reindex_single_record": (Rule.WRITE, "refuses a deleted record"),
    "reindex_record_group_records": (Rule.WRITE, "walks with get_records_by_record_group"),
    "update_indexing_status_for_record_ids": (Rule.WRITE, ""),
    "delete_parent_child_edge_to_record": (Rule.WRITE, ""),
    "batch_upsert_records": (Rule.WRITE, ""),
    "create_record_relation": (Rule.WRITE, ""),
    "batch_upsert_record_permissions": (Rule.WRITE, ""),
    "replace_record_permissions": (Rule.WRITE, "rewrites the permission and inherit edges of a record the caller resolved"),
    "link_record_to_group": (Rule.WRITE, "moves a record the caller resolved between record groups"),
    "delete_records_and_relations": (Rule.WRITE, ""),
    "delete_record": (Rule.WRITE, ""),
    "delete_record_by_external_id": (Rule.WRITE, "looks the record up with ALL; LIVE for a soft delete"),
    "remove_user_access_to_record": (Rule.WRITE, "looks the record up with ALL"),
    "delete_records_recursive": (Rule.WRITE, ""),
    "delete_single_record": (Rule.WRITE, ""),
    "soft_delete_records": (Rule.WRITE, "marks live records only"),
    "restore_records": (Rule.WRITE, "all or nothing: only records still in the trash under the batch named"),
    "purge_trashed_records": (Rule.WRITE, "removes only records still in the trash and due, checked in the delete"),
    "record_purge_failure": (Rule.WRITE, "counts only on records still in the trash"),
}
