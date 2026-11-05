"""Every method of the PostgreSQL graph provider that writes ``graph.nodes`` or ``graph.edges``, and what it means for kg.

``projected``: the method may change what kg holds. Its changes reach kg whatever it does, through the capture triggers (kg_0004), which are the
database layer of the chokepoint; this layer makes the decision explicit and reviewable. ``no_projection``: the method writes collections or fields kg
does not hold, with the reason. A new writer that appears in neither list fails ``test_the_registry_lists_every_writer_of_the_provider``; one that
runs unclassified at run time raises in tests (``PG_KG_GUARD=strict``) and is logged and counted otherwise (its changes are captured regardless).
"""
from __future__ import annotations

PROJECTED = "projected"
NO_PROJECTION = "no_projection"

_CODE = "code graph and block collections (codeFiles, blocks): not part of the access data"
_SYNC = "sync points hold a connector's cursor"
_STATUS = "indexing and extraction status fields: kg.record_state, written by the indexing role"
_ENTITY = "entity and taxonomy collections and their enrichment edges"

WRITERS: dict[str, tuple[str, str]] = {
    "backfill_block_name_folded": (NO_PROJECTION, _CODE),
    "batch_create_edges": (PROJECTED, ""),
    "batch_delete_edges": (PROJECTED, ""),
    "batch_update_connector_status": (NO_PROJECTION, "isActive and isAgentActive of an app are not projected"),
    "batch_update_nodes": (PROJECTED, ""),
    "batch_upsert_app_users": (PROJECTED, ""),
    "batch_upsert_node_relations": (NO_PROJECTION, _CODE),
    "batch_upsert_nodes": (PROJECTED, ""),
    "batch_upsert_records": (PROJECTED, ""),
    "compare_and_set_indexing_status": (NO_PROJECTION, _STATUS),
    "compare_and_set_record_fields": (PROJECTED, ""),
    "copy_document_relationships": (NO_PROJECTION, _ENTITY),
    "create_edges_if_absent": (PROJECTED, ""),
    "create_node_if_absent": (PROJECTED, ""),
    "delete_all_edges_for_node": (PROJECTED, ""),
    "delete_blocks_for_records": (NO_PROJECTION, _CODE),
    "delete_edges_between_collections": (PROJECTED, ""),
    "delete_edges_by_relationship_types": (PROJECTED, ""),
    "delete_edges_by_source_keys": (PROJECTED, ""),
    "delete_edges_from": (PROJECTED, ""),
    "delete_edges_to": (PROJECTED, ""),
    "delete_edges_to_groups": (PROJECTED, ""),
    "delete_edges_touching_nodes": (PROJECTED, ""),
    "delete_nodes": (PROJECTED, ""),
    "delete_parent_child_edge_to_record": (PROJECTED, ""),
    "delete_records_and_relations": (PROJECTED, ""),
    "delete_records_recursive": (PROJECTED, ""),
    "delete_single_record": (PROJECTED, ""),
    "delete_team_member_edges": (PROJECTED, ""),
    "ensure_app_membership": (PROJECTED, ""),
    "ensure_nodes": (NO_PROJECTION, _CODE),
    "ensure_team_app_edge": (PROJECTED, ""),
    "increment_node_field": (NO_PROJECTION, "the counter that serializes an app's structural writes (scopeProjectionGeneration): kg holds no field of it"),
    "remove_authenticated_as": (PROJECTED, ""),
    "remove_sync_point": (NO_PROJECTION, _SYNC),
    "stage_state_compare_and_set": (NO_PROJECTION, "stage states of the indexing pipeline"),
    "stage_state_create": (NO_PROJECTION, "stage states of the indexing pipeline"),
    "stage_states_delete_for_virtual_records": (NO_PROJECTION, "stage states of the indexing pipeline"),
    "stage_states_delete_unheld_revisions": (NO_PROJECTION, "stage states of the indexing pipeline"),
    "update_indexing_status_for_record_ids": (NO_PROJECTION, _STATUS),
    "update_knowledge_base": (PROJECTED, ""),
    "update_node": (PROJECTED, ""),
    "update_node_fields_if_match": (PROJECTED, ""),
    "update_sync_point_if": (NO_PROJECTION, _SYNC),
    "upsert_authenticated_as": (PROJECTED, ""),
    "upsert_sync_point": (NO_PROJECTION, _SYNC),
    # trash.py
    "soft_delete_records": (PROJECTED, ""), "restore_records": (PROJECTED, ""), "purge_trashed_records": (PROJECTED, ""), "purge_trash_kept_record_groups": (PROJECTED, ""),
    "take_back_kept_record_group": (PROJECTED, ""), "claim_push_record": (PROJECTED, ""),
    "record_purge_failure": (NO_PROJECTION, "purge attempt counters of a trashed record"),
    "retag_push_record": (NO_PROJECTION, "the external revision tag of a pushed record: not an access field"),
    # delete.py, lookups.py
    "delete_record": (PROJECTED, ""), "unlink_nodes_missed_by_full_sync": (PROJECTED, ""), "link_record_to_group": (PROJECTED, ""), "update_node_if_match": (PROJECTED, ""),
    "remove_nodes_by_field": (PROJECTED, ""),
}


class UnclassifiedProviderWrite(RuntimeError):
    """A provider method wrote graph.nodes or graph.edges and the projection registry does not list it."""
