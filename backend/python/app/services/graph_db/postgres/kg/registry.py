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
_NAMED = "entityRelations edges (a record and the people and organizations it names): not part of the access data"

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
    "sweep_deleted_connector_writes": (NO_PROJECTION, "blocks, stage states and code links, written after a connector's delete"),
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
    # kb.py
    "update_kb_permission": (PROJECTED, ""), "remove_kb_permission": (PROJECTED, ""),
    "delete_kb_hub_app": (PROJECTED, ""),
    # legacy_migrations.py: a knowledge base that was a record group becomes an app, the hierarchy edge moves onto its current collection
    "migrate_legacy_kb_to_app": (PROJECTED, ""), "migrate_legacy_relation_edge": (PROJECTED, ""),
    "migrate_agent_hub_knowledge": (NO_PROJECTION, "agent knowledge sources and their edges to the agents: kg holds no agent data"),
    # permissions.py
    "batch_update_team_member_roles": (PROJECTED, ""), "delete_all_team_permissions": (PROJECTED, ""),
    # core.py, lookups.py: an edge's document changes (a grant's role or type is what the projection reads of it), and the sync's edges and the stale external memberships go
    "update_edge": (PROJECTED, ""), "delete_connector_sync_edges": (PROJECTED, ""), "reap_stale_external_app_relations": (PROJECTED, ""),
    # delete.py: a connector instance and everything of it goes in one unit
    "delete_connector_instance": (PROJECTED, ""),
    # sync_state.py
    "update_user_sync_state": (NO_PROJECTION, "the sync state of a user's relation to an app is not an access field"), "update_drive_sync_state": (NO_PROJECTION, "the sync state of a drive node, not an access field"),
    "backfill_indexed_copy_flags": (NO_PROJECTION, _STATUS), "reset_indexing_status_for_connector": (NO_PROJECTION, _STATUS),
    # entities.py
    "create_named_entities_if_absent": (NO_PROJECTION, _ENTITY),
    "add_named_entity_aliases": (NO_PROJECTION, _ENTITY),
    "replace_named_entity_values": (NO_PROJECTION, _ENTITY),
    "copy_named_entity_mentions": (NO_PROJECTION, _ENTITY),
    "delete_dangling_named_entity_mentions": (NO_PROJECTION, _ENTITY),
    "delete_dangling_named_entity_values": (NO_PROJECTION, _ENTITY),
    "delete_orphan_named_entities": (NO_PROJECTION, _ENTITY),
    "mark_orphan_named_entities": (NO_PROJECTION, _ENTITY),
    "clear_orphan_named_entity_marks": (NO_PROJECTION, _ENTITY),
    "schedule_named_entity_persist_retry": (NO_PROJECTION, _ENTITY),
    "schedule_named_entity_reextract": (NO_PROJECTION, _ENTITY),
    "schedule_named_entity_reproject": (NO_PROJECTION, _ENTITY),
    "clear_named_entity_persist_retry": (NO_PROJECTION, _ENTITY),
    # taxonomy.py: taxonomy nodes, their belongsToCategory / belongsToTopic / belongsToLanguage edges and interCategoryRelations
    "create_taxonomy_node_if_absent": (NO_PROJECTION, _ENTITY),
    "add_taxonomy_aliases": (NO_PROJECTION, _ENTITY),
    "ensure_taxonomy_hierarchy_edge": (NO_PROJECTION, _ENTITY),
    "move_taxonomy_edges": (NO_PROJECTION, _ENTITY),
    # people.py, organizations.py: a person's grants and memberships become the user's; the edges from records to the people and organisations they name,
    # and the owning org of an external organisation, are not held
    "migrate_person_to_user": (PROJECTED, ""),
    "batch_create_entity_relations": (NO_PROJECTION, _NAMED),
    "delete_record_entity_relations": (NO_PROJECTION, _NAMED),
    "replace_record_entity_relations": (NO_PROJECTION, _NAMED),
    "stamp_external_org_parents": (NO_PROJECTION, "parentOrgId of an external organization: kg holds that an organization exists, no field of its document"),
}


class UnclassifiedProviderWrite(RuntimeError):
    """A provider method wrote graph.nodes or graph.edges and the projection registry does not list it."""
