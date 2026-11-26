"""Every Cypher writer of the Neo4j provider keeps a hierarchy edge's children order stamps true, or cannot change what they are made of.

A wide folder's browse reads its children in name order from the stamps on their edges (``khFrom``, ``khOrd``, ``khChild``,
`neo4j_provider.KH_CHILDREN_ORDER_INDEX`) and trusts them: a child whose stamp is stale is read out of its place, and a page that ends
before that place leaves it out. No read can find such a child without reading every child, so a stale stamp must be impossible: every
write that can change a stamp's inputs (a record's or group's name, its stub flag, a hierarchy edge's ends) rewrites the stamp in the same
statement, and the migration that sets the flag the browse waits for counts the stale ones first.

The set of writers is closed (`test_writer_payload_audit._AUDITED_CYPHER_WRITERS`): a new one fails here until it is shown to keep the
stamps (it builds its statement with one of `KEEPS`) or listed with why it cannot change them.
"""

from __future__ import annotations

import ast
import inspect

from app.services.graph_db.neo4j import neo4j_provider
from tests.unit.schema.test_writer_payload_audit import _AUDITED_CYPHER_WRITERS

# What a statement that keeps the stamps is built with: the state rewrites, the stamps themselves, or a refusal of the inputs.
KEEPS = ("_cypher_kh_node_state", "_cypher_kh_state_for", "_cypher_kh_tree_refresh", "_cypher_kh_tree_state", "_cypher_kh_edge_order",
         "_cypher_kh_child_ord", "cypher_kh_stamp_all", "_kh_stamp_unstamped_children_order", "_KH_STATE_INPUTS", "_KH_TREE_TYPES")

# Writers that cannot change a stamp's inputs, and why.
UNTOUCHED = {
    "_delete_unlinked_taxonomy_nodes": "taxonomy nodes",
    "_move_stub_edges_query": "a user stub's edges, to the user that replaces it",
    "_record_group_link_cypher": "the builder of link_record_to_group, whose statement keeps the tree state",
    "_replace_edges_to_cypher": "the builder of replace_edges_to, which refuses hierarchy and inherit edges",
    "_require_unique_id": "a schema constraint",
    "_stash_connector_delete_handles": "the delete handle nodes of a connector",
    "add_taxonomy_aliases": "taxonomy nodes",
    "backfill_app_org_ids": "an App's org",
    "backfill_block_name_folded": "Block nodes",
    "backfill_indexed_copy_flags": "a record's indexed-copy flag",
    "batch_create_entity_relations": "entity relation edges",
    "batch_update_connector_status": "an App's status",
    "batch_update_team_member_roles": "permission edges' roles",
    "batch_upsert_people": "Person nodes",
    "claim_push_record": "a record's external id and version",
    "compare_and_set_indexing_status": "a record's indexing status and run fields",
    "create_organization_if_absent": "Organization nodes",
    "create_taxonomy_node_if_absent": "taxonomy nodes",
    "delete_node_if_match": "deletes a node: its edges and their index entries go with it",
    "delete_records_recursive": "deletes records and sets a survivor's external parent id",
    "ensure_app_membership": "a principal's edge to an App",
    "ensure_taxonomy_hierarchy_edge": "taxonomy edges",
    "ensure_team_app_edge": "a team's edge to an App",
    "heal_taxonomy_alias_nodes": "taxonomy nodes",
    "migrate_legacy_kb_to_app": "turns a group into an App, which is never a browse's child, and moves records' connector",
    "migrate_person_to_user": "permission, app and entity edges of a person",
    "move_taxonomy_edges": "taxonomy edges",
    "purge_trash_kept_record_groups": "deletes groups (and takes their purge lock)",
    "purge_trashed_records": "deletes records (and takes their purge lock)",
    "record_purge_failure": "a record's purge counters",
    "replace_record_entity_relations": "entity relation edges",
    "reset_indexing_status_for_connector": "records' indexing status and run fields",
    "retag_push_record": "a record's external revision",
    "stage_state_compare_and_set": "StageState nodes",
    "stage_state_create": "StageState nodes",
    "stage_states_delete_unheld_revisions": "StageState nodes",
    "stamp_external_org_parents": "organization edges' parent org",
    "store_page_token": "PageToken nodes",
    "take_back_kept_record_group": "a group's deleted-at-source flags",
    "transition_unsupported": "a record's indexing status and reason",
    "update_agent_permission": "permission edges' roles",
    "update_drive_sync_state": "a drive edge's sync state",
    "update_edge": "merges properties into one edge (SET +=): the stamps are made of its ends and its child, never of the edge's own "
                   "properties, so it cannot make them stale",
    "update_kb_permission": "permission edges' roles",
    "update_knowledge_base": "a knowledge base App",
    "update_sync_point_if": "SyncPoint nodes",
    "update_user_sync_state": "a user's edge sync state",
    "upsert_authenticated_as": "AUTHENTICATED_AS edges",
    "upsert_person_by_email": "Person nodes",
    "upsert_person_by_source_key": "Person nodes",
    "upsert_sync_point": "SyncPoint nodes",
}


def _sources() -> dict[str, str]:
    tree = ast.parse(inspect.getsource(neo4j_provider))
    source = inspect.getsource(neo4j_provider)
    return {n.name: ast.get_source_segment(source, n) or "" for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _keeping(sources: dict[str, str]) -> set[str]:
    return {w for w in _AUDITED_CYPHER_WRITERS if any(k in sources.get(w, "") for k in KEEPS)}


def test_every_cypher_writer_keeps_the_children_order_or_cannot_change_it() -> None:
    sources = _sources()
    unclassified = sorted(set(_AUDITED_CYPHER_WRITERS) - _keeping(sources) - set(UNTOUCHED))
    assert unclassified == [], f"writers that neither keep the children order stamps nor are listed with why they cannot change them: {unclassified}"


def test_no_writer_is_listed_as_untouched_once_it_keeps_the_stamps_or_is_gone() -> None:
    sources = _sources()
    assert sorted(set(UNTOUCHED) & _keeping(sources)) == []
    assert sorted(set(UNTOUCHED) - set(_AUDITED_CYPHER_WRITERS)) == []
