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
import re

from app.services.graph_db.neo4j import neo4j_provider
from tests.support import edition_defaults
from tests.unit.schema.test_writer_payload_audit import _AUDITED_CYPHER_WRITERS

# What a statement that keeps the stamps is built with: the state rewrites, the stamps themselves, or a refusal of the inputs.
KEEPS = ("_cypher_kh_node_state", "_cypher_kh_state_for", "_cypher_kh_tree_refresh", "_cypher_kh_tree_state", "_cypher_kh_edge_order",
         "_cypher_kh_child_ord", "cypher_kh_stamp_all", "_kh_stamp_unstamped_children_order", "_KH_STATE_INPUTS", "_KH_TREE_TYPES")

# A statement of its own that stamps the edges a writer left unstamped: a writer that keeps the stamps with it must reach it on every way out.
STAMPS_APART = "_kh_stamp_unstamped_children_order"

# Writers that change a stamp's inputs and leave the stamps to their only caller, which reaches STAMPS_APART on every way out.
KEPT_BY_CALLER = {
    "_move_legacy_relation_edges": "migrate_legacy_relation_edge",
}

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
    "hard_delete_agent": "an agent, its MCP servers and their tools (none is a browse's child), and their lock flag",
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
    "update_kb_permission": "permission edges' roles",
    "update_knowledge_base": "a knowledge base App",
    "update_sync_point_if": "SyncPoint nodes",
    "update_user_sync_state": "a user's edge sync state",
    "upsert_authenticated_as": "AUTHENTICATED_AS edges",
    "upsert_person_by_email": "Person nodes",
    "upsert_person_by_source_key": "Person nodes",
    "upsert_sync_point": "SyncPoint nodes",
    **edition_defaults.neo4j_untouched_writers_extra(),
}


def _sources() -> dict[str, str]:
    tree = ast.parse(inspect.getsource(neo4j_provider))
    source = inspect.getsource(neo4j_provider)
    return {n.name: ast.get_source_segment(source, n) or "" for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _keeping(sources: dict[str, str]) -> set[str]:
    return {w for w in _AUDITED_CYPHER_WRITERS if any(k in sources.get(w, "") for k in KEEPS)}


def _functions() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(inspect.getsource(neo4j_provider))
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _own(fn: ast.AST) -> list[ast.AST]:
    """The nodes of ``fn``'s body, less those of a function defined inside it."""
    out, todo = [], list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(node)
        todo.extend(ast.iter_child_nodes(node))
    return out


def _calls(fn: ast.AST, name: str) -> list[int]:
    return [n.lineno for n in _own(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == name]


def test_every_cypher_writer_keeps_the_children_order_or_cannot_change_it() -> None:
    sources = _sources()
    unclassified = sorted(set(_AUDITED_CYPHER_WRITERS) - _keeping(sources) - set(UNTOUCHED) - set(KEPT_BY_CALLER))
    assert unclassified == [], f"writers that neither keep the children order stamps nor are listed with why they cannot change them: {unclassified}"


def test_no_writer_is_listed_as_untouched_once_it_keeps_the_stamps_or_is_gone() -> None:
    sources = _sources()
    assert sorted(set(UNTOUCHED) & _keeping(sources)) == []
    assert sorted(set(UNTOUCHED) - set(_AUDITED_CYPHER_WRITERS)) == []


def test_a_writer_that_stamps_apart_does_so_on_every_way_out() -> None:
    """A return before the stamp statement leaves what the writer moved unstamped on that path (a migration that found nothing left to move
    after an earlier run moved the edges and stopped before its stamp)."""
    functions = _functions()
    early = {name: [r for r in (n.lineno for n in _own(fn) if isinstance(n, ast.Return)) if r < min(_calls(fn, STAMPS_APART))]
             for name, fn in functions.items() if name != STAMPS_APART and _calls(fn, STAMPS_APART)}
    assert {name: lines for name, lines in early.items() if lines} == {}


def test_a_writer_kept_by_its_caller_has_that_caller_alone_and_the_caller_stamps() -> None:
    functions = _functions()
    for writer, caller in KEPT_BY_CALLER.items():
        callers = [name for name, fn in functions.items() if _calls(fn, writer)]
        assert callers == [caller], (writer, callers)
        assert _calls(functions[caller], STAMPS_APART), caller


# A whole-map write (``SET v += m``, ``SET v = $m``, ``SET v = row.m``) stores whatever keys its map holds. One whose map comes from a caller
# builds it with a helper that drops what the backend keeps for itself (`neo4j_provider._KH_OWN_PROPERTIES`: the listing's sort name and
# scope, the children order stamps); the validator ignores those keys, so it would let a forged or stale one through.
SCRUBS = ("_owned(", "_arango_to_neo4j_node(", "_edges_by_labels(", "_nodes_for_upsert(")
_MAP_SET = re.compile(r"\bSET\s+(?:[^\n]*?,\s*)?(\w+)\s*(\+=|=)\s*(\$\w+|\w+\.\w+|\w+)\b(?!\s*\()")

# Whole-map writes whose map no caller supplies, or which never reach a record, a group or a hierarchy edge.
MAPS_OF_ITS_OWN = {
    "_move_stub_edges_query": "a user stub's edges, moved with their own properties",
    "_record_group_link_cypher": "a map of timestamps the method builds",
    "batch_create_entity_relations": "entity relation edges",
    "create_organization_if_absent": "Organization nodes",
    "ensure_app_membership": "a principal's edge to an App",
    "link_record_to_group": "a map of fixed keys the method builds",
    "stage_state_compare_and_set": "StageState nodes",
    "stage_state_create": "StageState nodes",
    "store_page_token": "PageToken nodes",
    "update_knowledge_base": "a knowledge base App",
    "update_sync_point_if": "SyncPoint nodes",
    "upsert_record_under_parent": "a map of fixed keys the method builds",
    "upsert_sync_point": "SyncPoint nodes",
}


def _map_writers(sources: dict[str, str]) -> set[str]:
    out = set()
    for name, source in sources.items():
        for _var, op, rhs in _MAP_SET.findall(source):
            if op == "+=" or rhs.startswith("$") or "." in rhs or rhs in ("props", "node", "node_data", "person"):
                out.add(name)
    return out


def test_no_whole_map_write_takes_a_backend_key_from_its_caller() -> None:
    sources = _sources()
    unscrubbed = sorted(n for n in _map_writers(sources) if not any(c in sources[n] for c in SCRUBS) and n not in MAPS_OF_ITS_OWN)
    assert unscrubbed == [], f"whole-map writes that do not drop the backend's own keys from their map: {unscrubbed}"
    assert sorted(n for n in MAPS_OF_ITS_OWN if n not in _map_writers(sources) or any(c in sources[n] for c in SCRUBS)) == []


def test_the_helpers_drop_the_backend_keys() -> None:
    forged = {"id": "r", "recordName": "x", "khSortName": "a", "khScope": "s", "khFrom": "p", "khOrd": "0a", "khChild": "r"}
    p = object.__new__(neo4j_provider.Neo4jProvider)
    assert set(p._arango_to_neo4j_node(forged, "records")) == {"id", "recordName"}
    rows = p._edges_by_labels([{**forged, "from_id": "p", "from_collection": "records", "to_id": "r", "to_collection": "records"}])
    assert [set(row["props"]) for group in rows.values() for row in group] == [{"id", "recordName"}]


# --------------------------------------------------------------------------- documents handed out

# A public method that returns a whole node or edge document (``properties(v)``, ``v {.*}``, ``collect(v)``, ``RETURN v``) of a record, a
# group or a hierarchy edge (or of a label or type its caller names) hands it out through a helper that drops what the backend keeps for
# itself, or one built on such a helper: a caller that writes the document back would carry the stamps with it.
HANDS_OUT = ("_owned(", "_neo4j_to_arango_node(", "_neo4j_to_arango_edge(", "_create_typed_record_from_neo4j_simple(")
_BOUND = re.compile(r"\((\w+):(?:Record|RecordGroup|\{[^}]*\})[^)]*\)|\[(\w+):(?:NODE_RELATION|\{[^}]*\})[^\]]*\]")

# Public methods the scan flags whose result carries no whole document of those, and why.
NO_DOCUMENT_HANDED_OUT = {
    "update_node": "returns whether a node was written",
    "batch_update_nodes": "returns whether every node was written",
    "split_link_edges": "a migration: returns counts",
    "migrate_legacy_kb_to_app": "a migration: returns counts",
    "get_entity_access_context": "returns App maps of named fields and group ids",
    "upsert_sync_point": "SyncPoint nodes",
    "check_edge_exists": "returns whether the edge exists",
    "get_uploaded_document_ids": "returns ids",
    "delete_records_recursive": "returns the deleted records' ids and counts",
    "soft_delete_records": "returns the marked records' ids and counts",
    "stage_state_get": "StageState nodes",
    "stage_states_for_revision": "StageState nodes",
    "stage_states_stale": "StageState nodes",
    "stage_states_delete_unheld_revisions": "StageState nodes",
    "migrate_person_to_user": "a migration of a person's edges: returns its mode and counts",
    "reap_stale_external_app_relations": "user-app edges: returns counts",
    "get_records_by_weburls": "returns maps of named fields",
    "get_selection_nodes": "returns maps of named fields",
    "get_knowledge_hub_connector_page_v3": "returns rows of named fields",
    "get_knowledge_hub_breadcrumbs": "returns crumbs of named fields",
    "move_taxonomy_edges": "taxonomy edges: returns counts",
    "heal_taxonomy_alias_nodes": "taxonomy nodes: returns counts",
    "get_team_with_users": "a team's permission edges and team fields",
    "get_user_teams": "a team's permission edges and team fields",
    "get_team_users": "a team's permission edges and team fields",
    "get_team_permissions_and_owner_count": "Teams nodes and permission edges",
    "delete_all_edges_for_node": "returns counts",
    "hard_delete_agent": "agent nodes and edges: returns counts",
}


def _readers_handing_out_documents() -> set[str]:
    source = inspect.getsource(neo4j_provider)
    tree = ast.parse(source)
    provider = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Neo4jProvider")
    out = set()
    for fn in provider.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name.startswith("_"):
            continue
        seg = ast.get_source_segment(source, fn) or ""
        for v in {a or b for a, b in _BOUND.findall(seg)}:
            if re.search(rf"properties\({v}\)|\b{v}\s*\{{\s*\.\*|collect\(\s*(?:DISTINCT\s+)?{v}\s*\)"
                         rf"|RETURN\s+(?:DISTINCT\s+)?{v}\s*(?:,|AS\b|\n|$|LIMIT)|,\s*{v}\s*(?:AS\b|\n)", seg):
                out.add(fn.name)
    return out


def test_every_document_handed_out_drops_the_backend_keys() -> None:
    sources = _sources()
    readers = _readers_handing_out_documents()
    raw = sorted(n for n in readers if not any(h in sources[n] for h in HANDS_OUT) and n not in NO_DOCUMENT_HANDED_OUT)
    assert raw == [], f"public methods handing out a document without dropping the backend's own keys: {raw}"
    assert sorted(n for n in NO_DOCUMENT_HANDED_OUT if n not in readers or any(h in sources[n] for h in HANDS_OUT)) == []
