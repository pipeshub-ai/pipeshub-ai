"""Schema enforcement across every Neo4j write path (mocked client, no database).

Contract under test: docs of the graph schema validation change (plan section "Design" and
"Provider wiring"):

* every payload-bearing write is validated BEFORE any statement is issued, on the Arango-shaped
  dict (before ``_arango_to_neo4j_node`` JSON-encodes nested values);
* a batch is all-or-nothing: one invalid element means zero queries and zero transaction calls;
* creates and replaces validate in full, updates are partial, upserts are partial plus a lazy
  "required" check that costs at most ONE existence query per batch;
* targeted writes (``SET n.indexingStatus = $s`` and friends) validate the value they write;
* edge writes validate against the edge schema, endpoint keys of both input formats ignored;
* ``GRAPH_SCHEMA_ENFORCEMENT=off`` validates nothing; collections without a schema never raise.

STATUS: written tests-first. The wiring does not exist yet, so many tests here are RED on
purpose (anything about full validation of creates and replaces, ``additionalProperties`` on
updates, upsert existence checks, targeted writes, edges, the switch, nested objects at the four
post-conversion sites). Tests of behaviour that already holds (partial enum/type checks, nested
values at the pre-conversion sites, read-back nodes being accepted, no-schema collections) are
GREEN and guard against regressions while the wiring lands.

Assertions are on calls and exceptions, not on query text, except where the text IS the
behaviour (a write statement versus a read/existence probe is told apart by Cypher write
keywords). The existence probe contract the fake client answers: any non-write statement is an
existence probe; it answers one row per id (found among the statement's parameters) that is in
``existing``, each row carrying the id under ``id``, ``_key`` and ``key``, the node under ``n`` and
the whole list under ``ids``.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.conditions import EQ, Cond
from app.services.graph_db.neo4j import neo4j_provider as provider_module
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ENV_SWITCH = "GRAPH_SCHEMA_ENFORCEMENT"
WRITE_CYPHER = re.compile(r"\b(SET|MERGE|CREATE|DELETE|REMOVE)\b")
BOGUS = "NOT_A_REAL_VALUE"


# --------------------------------------------------------------------------------------------
# The fake client and helpers
# --------------------------------------------------------------------------------------------


def _strings(value: object) -> list[str]:
    """Every string found anywhere inside *value* (parameters nest rows inside rows)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple, set)):
        return [s for v in value for s in _strings(v)]
    return []


def _answers(existing: set[str]) -> Callable[..., Awaitable[list[dict]]]:
    """Answers statements: constraint checks pass, writes succeed, anything else is an existence probe."""

    async def answer(query: str, parameters: object = None, *args: object, **kwargs: object) -> list[dict]:
        if "SHOW CONSTRAINTS" in query:
            return [{"found": 1}]
        if WRITE_CYPHER.search(query):
            return [{"id": "k", "created": True, "made": [], "updated_count": 1, "deleted": 1, "value": 1, "n": {"id": "k"}}]
        ids = [s for s in dict.fromkeys(_strings(parameters)) if s in existing]
        return [{"id": i, "_key": i, "key": i, "n": {"id": i}, "ids": ids} for i in ids]

    return answer


def _provider(existing: tuple[str, ...] = ("r1", "r2", "r3", "s1", "t1", "k1", "a1")) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(side_effect=_answers(set(existing)))
    return provider


@dataclass(frozen=True)
class Sent:
    query: str
    parameters: dict[str, Any]

    @property
    def is_write(self) -> bool:
        return bool(WRITE_CYPHER.search(self.query))


def _sent(provider: Neo4jProvider) -> list[Sent]:
    out = []
    for call in provider.client.execute_query.await_args_list:
        query = call.args[0] if call.args else call.kwargs["query"]
        parameters = call.args[1] if len(call.args) > 1 else call.kwargs.get("parameters")
        out.append(Sent(query, parameters or {}))
    return out


def _writes(provider: Neo4jProvider) -> list[Sent]:
    return [s for s in _sent(provider) if s.is_write]


def _probes(provider: Neo4jProvider) -> list[Sent]:
    return [s for s in _sent(provider) if not s.is_write and "SHOW CONSTRAINTS" not in s.query]


def _other_client_calls(provider: Neo4jProvider) -> list[str]:
    """Client calls that are not execute_query (begin/commit/run in a transaction ...)."""
    return [str(c) for c in provider.client.mock_calls if not str(c).startswith("call.execute_query")]


def _assert_nothing_sent(provider: Neo4jProvider) -> None:
    assert provider.client.execute_query.await_count == 0, [s.query for s in _sent(provider)]
    assert _other_client_calls(provider) == []


def _assert_no_write(provider: Neo4jProvider) -> None:
    assert _writes(provider) == []
    assert _other_client_calls(provider) == []


async def _rejected(provider: Neo4jProvider, call: Awaitable[Any]) -> None:
    with pytest.raises(SchemaValidationError):
        await call
    _assert_nothing_sent(provider)


@pytest.fixture(autouse=True)
def _strict_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SWITCH, raising=False)


# --------------------------------------------------------------------------------------------
# Payloads
# --------------------------------------------------------------------------------------------

RECORDS = "records"
SKILLS = "agentSkills"
TEMPLATES = "agentTemplates"
NO_SCHEMA = "drives"


def _record(key: str = "r1", **override: object) -> dict[str, object]:
    node = {
        "_key": key, "orgId": "o1", "recordName": "a.txt", "externalRecordId": f"ext-{key}",
        "recordType": "FILE", "origin": "CONNECTOR", "createdAtTimestamp": 1, "connectorId": "c1",
    }
    node.update(override)
    return node


def _skill(key: str = "s1", **override: object) -> dict[str, object]:
    node = {
        "_key": key, "orgId": "o1", "name": "n", "description": "d", "content": "c", "status": "active",
        "createdBy": "u1", "createdAtTimestamp": 1, "resources": {"files": [{"path": "a", "meta": {"x": 1}}]},
    }
    node.update(override)
    return node


def _template(key: str = "t1", **override: object) -> dict[str, object]:
    node = {
        "_key": key, "name": "n", "description": "d", "startMessage": "s", "systemPrompt": "p",
        "tools": [{"name": "x", "description": "y"}],
    }
    node.update(override)
    return node


# Break a valid record in one way. Updates are partial, so "missing_required" only fails for full validation.
BREAKERS: dict[str, Callable[[dict], dict]] = {
    "bad_enum": lambda n: {**n, "recordType": BOGUS},
    "bad_type": lambda n: {**n, "createdAtTimestamp": "yesterday"},
    "unknown_key": lambda n: {**n, "notAField": 1},
}
MISSING_REQUIRED: Callable[[dict], dict] = lambda n: {k: v for k, v in n.items() if k != "recordName"}  # noqa: E731


def _key(node: dict) -> str:
    return node.get("_key") or node["id"]


# --------------------------------------------------------------------------------------------
# Public write methods: introspection plus a hand-kept classification
# --------------------------------------------------------------------------------------------

# Kinds whose payload carries schema'd fields: each must be exercised below or listed in COVERAGE_GAPS.
PAYLOAD_KINDS = {"create", "upsert", "update", "replace", "targeted", "edge"}
# Kinds that write nothing the caller shapes (or no schema'd data): explicitly exempt.
#   delete      removes data
#   internal    writes only values the provider computes (timestamps, flags, counters) or maintenance
#   ddl         schema / index statements
#   passthrough raw Cypher supplied by the caller
EXEMPT_KINDS = {"delete", "internal", "ddl", "passthrough"}

_C, _U, _UP, _R, _T, _E = "create", "update", "upsert", "replace", "targeted", "edge"
_D, _I = "delete", "internal"

CLASSIFICATION: dict[str, str] = {
    # ---- node creates (full validation)
    "create_node_if_absent": _C, "ensure_nodes": _C, "create_taxonomy_node_if_absent": _C, "create_organization_if_absent": _C,
    "stage_state_create": _C, "create_named_entities_if_absent": _C, "clone_agent_template": _C,
    # ---- node upserts (partial + lazy required)
    "batch_upsert_nodes": _UP, "batch_upsert_people": _UP, "batch_upsert_app_roles": _UP,
    "batch_upsert_app_users": _UP, "batch_upsert_orgs": _UP, "batch_upsert_record_groups": _UP,
    "batch_upsert_records": _UP, "batch_upsert_user_groups": _UP, "upsert_person_by_email": _UP, "upsert_person_by_source_key": _UP,
    "upsert_record_under_parent": _UP, "upsert_sync_point": _UP, "store_page_token": _UP,
    # ---- node updates (partial)
    "update_node": _U, "batch_update_nodes": _U, "update_node_fields_if_match": _U,
    "update_node_fields_if": _U, "update_nodes_fields_if_match": _U, "set_record_properties": _U,
    "update_agent": _U, "update_agent_template": _U, "update_knowledge_base": _U,
    "update_sync_point_if": _U, "stage_state_compare_and_set": _U, "add_taxonomy_aliases": _U,
    "add_named_entity_aliases": _U, "replace_named_entity_values": _U, "apply_verified_user_email": _U,
    # ---- node replace (SET n = $node, full validation)
    "update_node_if_match": _R,
    # ---- targeted raw writes of one schema'd field
    "compare_and_set_indexing_status": _T, "compare_and_set_record_fields": _T, "stamp_external_org_parents": _T,
    "update_indexing_status_for_record_ids": _T, "reset_indexing_status_for_connector": _T,
    "batch_update_connector_status": _T, "update_drive_sync_state": _T, "update_user_sync_state": _T,
    "update_queued_duplicates_status": _T, "claim_push_record": _T, "retag_push_record": _T,
    "record_purge_failure": _T, "increment_node_field": _T, "take_back_kept_record_group": _T,
    # ---- edges
    "batch_create_edges": _E, "create_edges_if_absent": _E, "batch_create_entity_relations": _E,
    "batch_upsert_node_relations": _E, "batch_create_user_app_edges": _E, "update_edge": _E,
    "upsert_authenticated_as": _E, "ensure_app_membership": _E, "ensure_taxonomy_hierarchy_edge": _E,
    "ensure_team_app_edge": _E, "create_record_relation": _E, "create_record_group_relation": _E,
    "create_record_groups_relation": _E, "create_inherit_permissions_relation_record_group": _E,
    "create_kb_permissions": _E, "batch_upsert_record_permissions": _E, "replace_edges_to": _E,
    "replace_record_permissions": _E, "link_record_to_group": _E, "batch_update_team_member_roles": _E,
    "update_agent_permission": _E, "update_kb_permission": _E, "copy_document_relationships": _E,
    "copy_named_entity_mentions": _E, "add_user_to_all_team": _E, "ensure_all_team_with_users": _E,
    "move_taxonomy_edges": _E, "migrate_legacy_relation_edge": _E, "split_link_edges": _E,
    # ---- exempt
    "execute_query": "passthrough", "ensure_schema": "ddl", "ensure_pipeline_schema": "ddl",
    "is_trash_walk_index_ready": "ddl",
}

# Names filled in below from the first run of the classification test (deletes and maintenance).
CLASSIFICATION.update(dict.fromkeys(("batch_delete_edges", "delete_record_entity_relations", "delete_all_edges_for_node", "delete_all_team_permissions", "delete_edge", "delete_edges_between_collections", "delete_edges_by_relationship_types", "delete_edges_by_source_keys", "delete_edges_from", "delete_edges_to", "delete_edges_to_groups", "delete_edges_touching_nodes", "delete_kb_hub_app", "delete_nodes", "delete_nodes_and_edges", "delete_parent_child_edge_to_record", "delete_records_and_relations", "delete_records_recursive", "delete_single_record", "delete_team_member_edges", "hard_delete_agent", "hard_delete_all_agents", "reap_stale_external_app_relations", "remove_authenticated_as", "remove_kb_permission", "remove_nodes_by_field", "remove_sync_point", "remove_user_access_to_connector", "remove_user_access_to_record", "stage_states_delete_for_virtual_records", "stage_states_delete_unheld_revisions", "delete_agent", "delete_agent_template", "delete_blocks_by_connector_id", "delete_blocks_for_records", "delete_connector_instance", "delete_connector_sync_edges", "delete_dangling_named_entity_mentions", "delete_dangling_named_entity_values", "delete_inherit_permissions_relation_record_group", "delete_orphan_named_entities", "delete_record", "delete_record_by_external_id", "delete_sync_points_by_connector_id", "purge_trash_kept_record_groups", "purge_trashed_records", "drop_redundant_group_edges"), _D))
CLASSIFICATION.update(dict.fromkeys(("backfill_app_org_ids", "backfill_block_name_folded", "backfill_hierarchy", "backfill_indexed_copy_flags", "heal_taxonomy_alias_nodes", "migrate_legacy_kb_to_app", "migrate_person_to_user", "normalize_folder_mime_types", "restore_records", "soft_delete_records", "stamp_kh_listing_state", "kh_scope_forget", "kh_scope_mark_changed", "kh_scope_mark_stale", "kh_scope_reset_syncing", "kh_scope_restamp_stale", "kh_scope_stamp", "kh_scope_sync_ended", "clear_named_entity_persist_retry", "clear_orphan_named_entity_marks", "mark_orphan_named_entities", "schedule_named_entity_persist_retry", "schedule_named_entity_reextract", "schedule_named_entity_reproject", "migrate_agent_hub_knowledge", "name_knowledge_hub_parents", "reindex_record_group_records", "reindex_single_record", "unlink_nodes_missed_by_full_sync"), _I))

# Names a method must start with to count as a write when its body holds no Cypher write keyword
# (it delegates to another method). A write method that matches neither is missed: keep this list wide.
_WRITE_NAME = re.compile(
    r"^(add_|batch_|clear_|clone_|copy_|create_|delete_|ensure_|apply_|kh_scope_(?!enabled)|mark_|migrate_|name_knowledge|"
    r"reindex_|remove_|replace_|schedule_|stamp_|unlink_|update_|upsert_)"
)


def _public_write_methods() -> set[str]:
    found = set()
    for name, function in inspect.getmembers(Neo4jProvider, inspect.isfunction):
        if name.startswith("_"):
            continue
        try:
            source = inspect.getsource(inspect.unwrap(function))
        except (OSError, TypeError):
            source = ""
        if WRITE_CYPHER.search(source) or _WRITE_NAME.match(name):
            found.add(name)
    return found


def test_every_public_write_method_is_classified() -> None:
    """A new public write method must be classified here, so it cannot skip schema validation unnoticed."""
    missing = sorted(_public_write_methods() - CLASSIFICATION.keys())
    assert not missing, (
        f"classify these new Neo4jProvider write methods in CLASSIFICATION (and wire or exempt them): {missing}"
    )


def test_classification_has_no_stale_entries() -> None:
    gone = sorted(set(CLASSIFICATION) - {n for n, _ in inspect.getmembers(Neo4jProvider, inspect.isfunction)})
    assert not gone, f"CLASSIFICATION names methods that no longer exist: {gone}"


def test_classification_kinds_are_known() -> None:
    unknown = {n: k for n, k in CLASSIFICATION.items() if k not in PAYLOAD_KINDS | EXEMPT_KINDS | {_D, _I}}
    assert not unknown


# --------------------------------------------------------------------------------------------
# Node write drivers
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeDriver:
    kind: str
    call: Callable[[Neo4jProvider, str, list[dict]], Awaitable[Any]]
    batch: bool


NODE_DRIVERS: dict[str, NodeDriver] = {
    "ensure_nodes": NodeDriver(_C, lambda p, c, ns: p.ensure_nodes(ns, c), True),
    "create_node_if_absent": NodeDriver(_C, lambda p, c, ns: p.create_node_if_absent(_key(ns[0]), c, ns[0]), False),
    "batch_upsert_nodes": NodeDriver(_UP, lambda p, c, ns: p.batch_upsert_nodes(ns, c), True),
    "update_node": NodeDriver(_U, lambda p, c, ns: p.update_node(_key(ns[0]), c, ns[0]), False),
    "batch_update_nodes": NodeDriver(_U, lambda p, c, ns: p.batch_update_nodes(ns, c), True),
    "update_node_fields_if_match": NodeDriver(
        _U, lambda p, c, ns: p.update_node_fields_if_match(_key(ns[0]), c, ns[0], {"version": 0}), False),
    "update_node_fields_if": NodeDriver(
        _U, lambda p, c, ns: p.update_node_fields_if(_key(ns[0]), c, ns[0], [Cond("version", EQ, 0)]), False),
    "update_nodes_fields_if_match": NodeDriver(
        _U, lambda p, c, ns: p.update_nodes_fields_if_match(c, [(_key(n), n, {"version": 0}) for n in ns]), True),
    "update_node_if_match": NodeDriver(
        _R, lambda p, c, ns: p.update_node_if_match(_key(ns[0]), c, ns[0], "updatedAtTimestamp", 1), False),
}
# The four sites that validated after JSON-encoding nested values.
POST_CONVERSION_SITES = (
    "ensure_nodes", "update_node_fields_if_match", "update_node_fields_if", "update_nodes_fields_if_match",
)

DRIVER_IDS = list(NODE_DRIVERS)
BATCH_DRIVER_IDS = [n for n, d in NODE_DRIVERS.items() if d.batch]


def _breakers_for(kind: str) -> list[str]:
    names = list(BREAKERS)
    if kind in (_C, _R):
        names.append("missing_required")
    return names


def _break(name: str, node: dict) -> dict:
    return MISSING_REQUIRED(node) if name == "missing_required" else BREAKERS[name](node)


INVALID_CASES = [(d, b) for d in DRIVER_IDS for b in _breakers_for(NODE_DRIVERS[d].kind)]


@pytest.mark.parametrize(("driver", "breaker"), INVALID_CASES, ids=[f"{d}-{b}" for d, b in INVALID_CASES])
async def test_invalid_payload_is_rejected_before_any_query(driver: str, breaker: str) -> None:
    p = _provider()
    await _rejected(p, NODE_DRIVERS[driver].call(p, RECORDS, [_break(breaker, _record())]))


@pytest.mark.parametrize("driver", BATCH_DRIVER_IDS)
@pytest.mark.parametrize("breaker", list(BREAKERS))
async def test_one_invalid_element_blocks_the_whole_batch(driver: str, breaker: str) -> None:
    p = _provider()
    batch = [_record("r1"), _record("r2"), _break(breaker, _record("r3"))]
    await _rejected(p, NODE_DRIVERS[driver].call(p, RECORDS, batch))


@pytest.mark.parametrize("driver", ["ensure_nodes", "update_nodes_fields_if_match", "batch_update_nodes"])
async def test_a_bad_first_element_also_blocks_the_batch(driver: str) -> None:
    p = _provider()
    await _rejected(p, NODE_DRIVERS[driver].call(p, RECORDS, [_break("bad_enum", _record("r1")), _record("r2")]))


@pytest.mark.parametrize("driver", DRIVER_IDS)
async def test_valid_payload_issues_the_write(driver: str) -> None:
    p = _provider()
    await NODE_DRIVERS[driver].call(p, RECORDS, [_record("r1"), _record("r2")][: 2 if NODE_DRIVERS[driver].batch else 1])
    assert len(_writes(p)) >= 1
    assert _probes(p) == []


@pytest.mark.parametrize("driver", DRIVER_IDS)
async def test_valid_payload_with_id_instead_of_key_issues_the_write(driver: str) -> None:
    p = _provider()
    node = _record("r1")
    node["id"] = node.pop("_key")
    await NODE_DRIVERS[driver].call(p, RECORDS, [node])
    assert len(_writes(p)) >= 1


@pytest.mark.parametrize("driver", [d for d in DRIVER_IDS if NODE_DRIVERS[d].kind == _U])
async def test_update_accepts_a_partial_payload(driver: str) -> None:
    p = _provider()
    await NODE_DRIVERS[driver].call(p, RECORDS, [{"_key": "r1", "recordName": "renamed"}])
    assert len(_writes(p)) >= 1


@pytest.mark.parametrize("driver", [d for d in DRIVER_IDS if NODE_DRIVERS[d].kind == _U])
async def test_update_with_a_field_outside_the_schema_is_rejected(driver: str) -> None:
    p = _provider()
    await _rejected(p, NODE_DRIVERS[driver].call(p, RECORDS, [{"_key": "r1", "notAField": 1}]))


@pytest.mark.parametrize("driver", [d for d in DRIVER_IDS if NODE_DRIVERS[d].kind in (_C, _R)])
async def test_create_and_replace_need_the_required_fields(driver: str) -> None:
    p = _provider()
    await _rejected(p, NODE_DRIVERS[driver].call(p, RECORDS, [{"_key": "r1", "recordName": "x"}]))


# --------------------------------------------------------------------------------------------
# Nested objects and arrays of objects survive (validate before conversion)
# --------------------------------------------------------------------------------------------

NESTED_DEEP = {"files": [{"path": "a", "meta": {"x": [1, {"y": 2}]}}], "k": {"n": None}}


@pytest.mark.parametrize("driver", DRIVER_IDS)
async def test_nested_object_field_is_not_rejected(driver: str) -> None:
    p = _provider()
    await NODE_DRIVERS[driver].call(p, SKILLS, [_skill("s1", resources=NESTED_DEEP)])
    assert len(_writes(p)) >= 1


@pytest.mark.parametrize("driver", DRIVER_IDS)
async def test_array_of_object_field_is_not_rejected(driver: str) -> None:
    p = _provider()
    tools = [{"name": "a", "description": "d"}, {"name": "b", "extra": {"deep": [{"x": 1}]}}]
    await NODE_DRIVERS[driver].call(p, TEMPLATES, [_template("t1", tools=tools)])
    assert len(_writes(p)) >= 1


@pytest.mark.parametrize("driver", POST_CONVERSION_SITES)
async def test_nested_object_is_still_sent_to_the_database_encoded(driver: str) -> None:
    """Validation moves before the conversion; the conversion itself (JSON string properties) stays."""
    p = _provider()
    await NODE_DRIVERS[driver].call(p, SKILLS, [_skill("s1", resources={"a": {"b": 1}})])
    sent = _writes(p)[-1].parameters
    encoded = [s for s in _strings(sent) if s.startswith("{") and '"a"' in s]
    assert encoded, sent


@pytest.mark.parametrize("driver", DRIVER_IDS)
async def test_wrong_type_in_an_object_field_is_rejected(driver: str) -> None:
    p = _provider()
    await _rejected(p, NODE_DRIVERS[driver].call(p, SKILLS, [_skill("s1", resources="not an object")]))


@pytest.mark.parametrize("driver", DRIVER_IDS)
async def test_invalid_item_inside_an_array_of_objects_is_rejected(driver: str) -> None:
    p = _provider()
    await _rejected(p, NODE_DRIVERS[driver].call(p, TEMPLATES, [_template("t1", tools=[{"description": "no name"}])]))


# --------------------------------------------------------------------------------------------
# Upsert: required fields are checked lazily
# --------------------------------------------------------------------------------------------

PARTIAL = {"_key": "r1", "recordName": "renamed"}


async def test_complete_upsert_payload_needs_no_existence_query() -> None:
    p = _provider(existing=())
    await p.batch_upsert_nodes([_record("r1"), _record("r2")], RECORDS)
    assert _probes(p) == []
    assert len(_writes(p)) == 1


async def test_incomplete_upsert_of_an_existing_node_is_accepted() -> None:
    p = _provider(existing=("r1",))
    await p.batch_upsert_nodes([PARTIAL], RECORDS)
    assert len(_writes(p)) == 1
    assert len(_probes(p)) == 1


async def test_incomplete_upsert_of_an_absent_node_is_rejected_without_a_write() -> None:
    p = _provider(existing=())
    with pytest.raises(SchemaValidationError):
        await p.batch_upsert_nodes([PARTIAL], RECORDS)
    assert _writes(p) == []
    assert _other_client_calls(p) == []
    assert len(_probes(p)) <= 1


async def test_incomplete_upsert_with_id_key_is_accepted_for_an_existing_node() -> None:
    p = _provider(existing=("r1",))
    await p.batch_upsert_nodes([{"id": "r1", "recordName": "renamed"}], RECORDS)
    assert len(_writes(p)) == 1


async def test_mixed_batch_with_existing_and_complete_nodes_is_accepted_with_one_probe() -> None:
    p = _provider(existing=("r2", "r3"))
    batch = [_record("r1"), {"_key": "r2", "recordName": "x"}, {"_key": "r3", "recordName": "y"}]
    await p.batch_upsert_nodes(batch, RECORDS)
    probes = _probes(p)
    assert len(probes) == 1
    asked = _strings(probes[0].parameters)
    assert "r2" in asked
    assert "r3" in asked
    assert "r1" not in asked  # a complete payload never needs the check
    assert len(_writes(p)) == 1


async def test_mixed_batch_with_one_absent_incomplete_node_writes_nothing() -> None:
    p = _provider(existing=("r2",))
    batch = [_record("r1"), {"_key": "r2", "recordName": "x"}, {"_key": "r9", "recordName": "nope"}]
    with pytest.raises(SchemaValidationError):
        await p.batch_upsert_nodes(batch, RECORDS)
    assert _writes(p) == []
    assert _other_client_calls(p) == []
    assert len(_probes(p)) <= 1


async def test_many_incomplete_nodes_cost_one_existence_query() -> None:
    ids = [f"r{i}" for i in range(50)]
    p = _provider(existing=tuple(ids))
    await p.batch_upsert_nodes([{"_key": i, "recordName": "x"} for i in ids], RECORDS)
    assert len(_probes(p)) == 1


async def test_an_invalid_incomplete_upsert_is_rejected_even_when_the_node_exists() -> None:
    p = _provider(existing=("r1",))
    with pytest.raises(SchemaValidationError):
        await p.batch_upsert_nodes([{"_key": "r1", "recordType": BOGUS}], RECORDS)
    assert _writes(p) == []


async def test_upsert_with_an_unknown_field_is_rejected_even_when_complete() -> None:
    p = _provider()
    await _rejected(p, p.batch_upsert_nodes([_record("r1", notAField=1)], RECORDS))


async def test_upsert_without_a_key_and_incomplete_is_rejected() -> None:
    """A random id is generated for a keyless node, so it cannot exist: an incomplete one is a bad create."""
    p = _provider()
    with pytest.raises(SchemaValidationError):
        await p.batch_upsert_nodes([{"recordName": "x"}], RECORDS)
    assert _writes(p) == []


async def test_a_status_only_upsert_of_an_existing_record_is_accepted() -> None:
    p = _provider(existing=("r1",))
    await p.batch_upsert_nodes([{"id": "r1", "indexingStatus": ProgressStatus.QUEUED.value}], RECORDS)
    assert len(_writes(p)) == 1


# --------------------------------------------------------------------------------------------
# Replace: SET n = $node validates in full; a node read back from the graph is accepted
# --------------------------------------------------------------------------------------------

CREATED_MARK = getattr(provider_module, "_CREATED_MARK", "createdMark")


def _stored_record() -> dict[str, Any]:
    """A record as Neo4j holds it, with every backend-internal property."""
    stored = _record("r1", p_color="red", p_tags=["a", "b"], p_count=3, p_flag=None)
    stored["id"] = stored.pop("_key")
    stored.update({"khSortName": "a.txt", "khScope": "scope-1", CREATED_MARK: True})
    return stored


def _stored_skill() -> dict[str, Any]:
    stored = _skill("s1", resources=json.dumps({"a": {"b": [1, 2]}}), version="1")
    stored["id"] = stored.pop("_key")
    return stored


async def test_read_back_node_can_be_replaced() -> None:
    p = _provider()
    node = p._neo4j_to_arango_node(_stored_record(), RECORDS)
    assert "_id" in node  # reads carry the composite id
    assert await p.update_node_if_match("r1", RECORDS, node, "updatedAtTimestamp", 1) is True
    assert len(_writes(p)) == 1


async def test_raw_stored_node_with_internal_properties_can_be_replaced() -> None:
    p = _provider()
    await p.update_node_if_match("r1", RECORDS, _stored_record(), "updatedAtTimestamp", 1)
    assert len(_writes(p)) == 1


async def test_raw_stored_node_with_internal_properties_can_be_updated() -> None:
    p = _provider()
    await p.update_node("r1", RECORDS, _stored_record())
    await p.batch_update_nodes([_stored_record()], RECORDS)
    assert len(_writes(p)) == 2


async def test_read_back_node_can_be_upserted_without_an_existence_query() -> None:
    p = _provider(existing=())
    await p.batch_upsert_nodes([p._neo4j_to_arango_node(_stored_record(), RECORDS)], RECORDS)
    assert _probes(p) == []
    assert len(_writes(p)) == 1


async def test_read_back_node_with_decoded_object_field_can_be_replaced() -> None:
    p = _provider()
    node = p._neo4j_to_arango_node(_stored_skill(), SKILLS)
    assert isinstance(node["resources"], dict)  # decoded on read
    await p.update_node_if_match("s1", SKILLS, node, "updatedAtTimestamp", 1)
    assert len(_writes(p)) == 1


@pytest.mark.parametrize("field", ["khSortName", "khScope", CREATED_MARK, "p_anything", "_id"])
async def test_internal_properties_never_trip_the_validator(field: str) -> None:
    p = _provider()
    value: Any = "records/r1" if field == "_id" else "v"
    await p.update_node_if_match("r1", RECORDS, _record("r1", **{field: value}), "updatedAtTimestamp", 1)
    assert len(_writes(p)) == 1


async def test_replace_with_a_p_property_of_the_wrong_shape_is_rejected() -> None:
    p = _provider()
    await _rejected(p, p.update_node_if_match("r1", RECORDS, _record("r1", p_bad={"nested": 1}), "updatedAtTimestamp", 1))


# --------------------------------------------------------------------------------------------
# Record properties (p_<name>)
# --------------------------------------------------------------------------------------------


async def test_record_property_values_are_validated_before_the_write() -> None:
    p = _provider()
    await _rejected(p, p.set_record_properties(
        "r1", values={"color": {"nested": "object"}}, previous_names=[], controls={}, conditions=[Cond("version", EQ, 0)],
    ))


async def test_record_properties_of_every_allowed_shape_are_written() -> None:
    p = _provider()
    assert await p.set_record_properties(
        "r1", values={"a": "s", "b": 1, "c": True, "d": ["x", "y"], "e": None}, previous_names=["old"],
        controls={"updatedAtTimestamp": 5}, conditions=[Cond("version", EQ, 0)],
    ) is True
    assert len(_writes(p)) == 1


async def test_record_property_name_outside_the_pattern_is_rejected() -> None:
    p = _provider()
    await _rejected(p, p.set_record_properties(
        "r1", values={"has space": "v"}, previous_names=[], controls={}, conditions=[Cond("version", EQ, 0)],
    ))


# --------------------------------------------------------------------------------------------
# Targeted raw writes
# --------------------------------------------------------------------------------------------

STATUS_FIELDS = ("indexingStatus", "extractionStatus", "parsingStatus")


@pytest.mark.parametrize("status", [s.value for s in ProgressStatus])
async def test_compare_and_set_indexing_status_accepts_every_status(status: str) -> None:
    p = _provider()
    await p.compare_and_set_indexing_status(["r1"], ProgressStatus.QUEUED.value, status)
    assert len(_writes(p)) == 1


async def test_compare_and_set_indexing_status_rejects_a_bogus_status() -> None:
    p = _provider()
    await _rejected(p, p.compare_and_set_indexing_status(["r1"], ProgressStatus.QUEUED.value, BOGUS))


@pytest.mark.parametrize("status", [s.value for s in ProgressStatus])
async def test_reset_indexing_status_for_connector_accepts_every_status(status: str) -> None:
    p = _provider()
    await p.reset_indexing_status_for_connector("c1", status)
    assert len(_writes(p)) == 1


async def test_reset_indexing_status_for_connector_rejects_a_bogus_status() -> None:
    p = _provider()
    await _rejected(p, p.reset_indexing_status_for_connector("c1", BOGUS))


@pytest.mark.parametrize("field", STATUS_FIELDS)
@pytest.mark.parametrize("status", [s.value for s in ProgressStatus])
async def test_compare_and_set_record_fields_accepts_every_status(field: str, status: str) -> None:
    p = _provider()
    await p.compare_and_set_record_fields(["r1"], "rev1", {field: status})
    assert len(_writes(p)) == 1


@pytest.mark.parametrize("field", STATUS_FIELDS)
async def test_compare_and_set_record_fields_rejects_a_bogus_status(field: str) -> None:
    p = _provider()
    await _rejected(p, p.compare_and_set_record_fields(["r1"], "rev1", {field: BOGUS}))


@pytest.mark.parametrize("field", STATUS_FIELDS)
@pytest.mark.parametrize("status", [s.value for s in ProgressStatus])
async def test_targeted_status_write_through_the_field_updater_accepts_every_status(field: str, status: str) -> None:
    p = _provider()
    await p.update_node_fields_if_match("r1", RECORDS, {field: status}, {field: ProgressStatus.QUEUED.value})
    assert len(_writes(p)) == 1


@pytest.mark.parametrize("field", STATUS_FIELDS)
async def test_targeted_status_write_through_the_field_updater_rejects_a_bogus_status(field: str) -> None:
    p = _provider()
    await _rejected(p, p.update_node_fields_if_match("r1", RECORDS, {field: BOGUS}, {field: "QUEUED"}))


@pytest.mark.parametrize(
    ("collection", "field", "good", "bad"),
    [
        (RECORDS, "isDeleted", True, "yes"),
        (RECORDS, "connectorId", "c1", 42),
        ("apps", "isActive", False, "yes"),
        ("users", "isActive", True, "yes"),
    ],
    ids=["isDeleted", "connectorId", "app-isActive", "user-isActive"],
)
class TestTargetedFields:
    async def test_good_value_is_written(self, collection: str, field: str, good: object, bad: object) -> None:
        p = _provider()
        assert await p.update_node_fields_if_match("k1", collection, {field: good}, {field: None}) is True
        assert len(_writes(p)) == 1

    async def test_bad_value_is_rejected_before_the_statement(self, collection: str, field: str, good: object, bad: object) -> None:
        p = _provider()
        await _rejected(p, p.update_node_fields_if_match("k1", collection, {field: bad}, {field: None}))

    async def test_bad_value_through_update_node_is_rejected(self, collection: str, field: str, good: object, bad: object) -> None:
        p = _provider()
        await _rejected(p, p.update_node("k1", collection, {field: bad}))


async def test_status_only_update_of_records_ids_accepts_every_status() -> None:
    """This writer logs and swallows its failures, so a refusal shows as no write rather than as an exception."""
    for status in ProgressStatus:
        p = _provider(existing=("r1",))
        await p.update_indexing_status_for_record_ids(["r1"], status.value)
        assert len(_writes(p)) == 1, status


async def test_status_only_update_of_records_rejects_a_bogus_status_without_writing() -> None:
    p = _provider(existing=("r1",))
    with contextlib.suppress(SchemaValidationError):  # raising is acceptable too
        await p.update_indexing_status_for_record_ids(["r1"], BOGUS)
    assert _writes(p) == []


async def test_connector_status_batch_writes_booleans() -> None:
    p = _provider()
    await p.batch_update_connector_status("apps", ["a1"], is_active=True, is_agent_active=False)
    assert len(_writes(p)) == 1


async def test_connector_status_batch_does_not_write_a_non_boolean() -> None:
    p = _provider()
    with contextlib.suppress(SchemaValidationError):  # this writer swallows other failures; raising is acceptable
        await p.batch_update_connector_status("apps", ["a1"], is_active="yes", is_agent_active=False)  # type: ignore[arg-type]
    assert _writes(p) == []


async def test_user_sync_state_accepts_the_declared_states() -> None:
    for state in ("NOT_STARTED", "IN_PROGRESS", "PAUSED", "COMPLETED", "FAILED"):
        p = _provider()
        await p.update_user_sync_state("u@example.com", state, "DRIVE")
        assert len(_writes(p)) == 1, state


async def test_user_sync_state_does_not_write_a_bogus_state() -> None:
    p = _provider()
    with contextlib.suppress(SchemaValidationError):  # this writer swallows other failures; raising is acceptable
        await p.update_user_sync_state("u@example.com", BOGUS, "DRIVE")
    assert _writes(p) == []


async def test_drive_sync_state_has_no_schema_so_any_value_is_written() -> None:
    p = _provider()
    await p.update_drive_sync_state("d1", "anything at all")
    assert len(_writes(p)) == 1


# --------------------------------------------------------------------------------------------
# Edges
# --------------------------------------------------------------------------------------------


def _arango_ends(frm: str, to: str, frm_coll: str, to_coll: str) -> dict[str, Any]:
    return {"_from": f"{frm_coll}/{frm}", "_to": f"{to_coll}/{to}"}


def _generic_ends(frm: str, to: str, frm_coll: str, to_coll: str) -> dict[str, Any]:
    return {"from_id": frm, "from_collection": frm_coll, "to_id": to, "to_collection": to_coll}


ENDS = {"arango": _arango_ends, "generic": _generic_ends}


@dataclass(frozen=True)
class EdgeFamily:
    collection: str
    from_coll: str
    to_coll: str
    props: dict[str, Any]
    breakers: dict[str, Callable[[dict], dict]]

    def edge(self, fmt: str, i: int = 1, **override: object) -> dict[str, object]:
        return {**ENDS[fmt](f"a{i}", f"b{i}", self.from_coll, self.to_coll), **self.props, **override}


BASIC = EdgeFamily(
    "orgAppRelation", "orgs", "apps", {"createdAtTimestamp": 1},
    {
        "missing_created_at": lambda e: {k: v for k, v in e.items() if k != "createdAtTimestamp"},
        "unknown_key": lambda e: {**e, "notAField": 1},
        "bad_type": lambda e: {**e, "createdAtTimestamp": "yesterday"},
    },
)
NODE_RELATIONS = EdgeFamily(
    "nodeRelations", "records", "records", {"relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1},
    {
        "bad_enum": lambda e: {**e, "relationshipType": BOGUS},
        "bad_type": lambda e: {**e, "createdAtTimestamp": "yesterday"},
    },
)
ENTITY_RELATIONS = EdgeFamily(
    "entityRelations", "people", "records", {"edgeType": "ASSIGNED_TO", "createdAtTimestamp": 1},
    {
        "bad_enum": lambda e: {**e, "edgeType": BOGUS},
        "missing_created_at": lambda e: {k: v for k, v in e.items() if k != "createdAtTimestamp"},
    },
)

EdgeCall = Callable[[Neo4jProvider, EdgeFamily, list[dict]], Awaitable[Any]]
EDGE_DRIVERS: dict[str, tuple[EdgeCall, tuple[EdgeFamily, ...]]] = {
    "batch_create_edges": (lambda p, f, es: p.batch_create_edges(es, f.collection), (BASIC, NODE_RELATIONS)),
    "create_edges_if_absent": (lambda p, f, es: p.create_edges_if_absent(es, f.collection), (BASIC, NODE_RELATIONS)),
    "batch_upsert_node_relations": (lambda p, f, es: p.batch_upsert_node_relations(es), (NODE_RELATIONS,)),
    "batch_create_entity_relations": (lambda p, f, es: p.batch_create_entity_relations(es), (ENTITY_RELATIONS,)),
}
EDGE_CASES = [(d, fam) for d, (_, fams) in EDGE_DRIVERS.items() for fam in fams]
EDGE_CASE_IDS = [f"{d}-{fam.collection}" for d, fam in EDGE_CASES]
EDGE_INVALID = [(d, fam, b) for d, fam in EDGE_CASES for b in fam.breakers]
EDGE_INVALID_IDS = [f"{d}-{fam.collection}-{b}" for d, fam, b in EDGE_INVALID]


@pytest.mark.parametrize("fmt", list(ENDS))
@pytest.mark.parametrize(("driver", "family"), EDGE_CASES, ids=EDGE_CASE_IDS)
async def test_valid_edge_in_either_format_is_written(driver: str, family: EdgeFamily, fmt: str) -> None:
    """Endpoint keys (`_from`/`_to`, `from_id`/`to_id`/`from_collection`/`to_collection`) are not edge properties."""
    p = _provider()
    await EDGE_DRIVERS[driver][0](p, family, [family.edge(fmt, 1), family.edge(fmt, 2)])
    assert len(_writes(p)) >= 1


@pytest.mark.parametrize("fmt", list(ENDS))
@pytest.mark.parametrize(("driver", "family", "breaker"), EDGE_INVALID, ids=EDGE_INVALID_IDS)
async def test_invalid_edge_is_rejected_before_any_query(driver: str, family: EdgeFamily, breaker: str, fmt: str) -> None:
    p = _provider()
    await _rejected(p, EDGE_DRIVERS[driver][0](p, family, [family.breakers[breaker](family.edge(fmt))]))


@pytest.mark.parametrize(("driver", "family", "breaker"), EDGE_INVALID, ids=EDGE_INVALID_IDS)
async def test_one_invalid_edge_blocks_the_whole_batch(driver: str, family: EdgeFamily, breaker: str) -> None:
    p = _provider()
    batch = [family.edge("arango", 1), family.edge("generic", 2), family.breakers[breaker](family.edge("arango", 3))]
    await _rejected(p, EDGE_DRIVERS[driver][0](p, family, batch))


async def test_edges_in_a_batch_mixing_both_formats_are_written() -> None:
    p = _provider()
    await p.batch_create_edges([BASIC.edge("arango", 1), BASIC.edge("generic", 2)], BASIC.collection)
    assert len(_writes(p)) >= 1


async def test_basic_edge_without_created_at_is_rejected() -> None:
    p = _provider()
    edge = {**_arango_ends("o1", "a1", "orgs", "apps")}
    await _rejected(p, p.batch_create_edges([edge], "orgAppRelation"))


async def test_a_bogus_relationship_type_is_rejected() -> None:
    p = _provider()
    edge = NODE_RELATIONS.edge("arango", relationshipType=BOGUS)
    await _rejected(p, p.batch_create_edges([edge], "nodeRelations"))


async def test_every_declared_relationship_type_is_written() -> None:
    from app.config.constants.arangodb import RecordRelations

    for relation in RecordRelations:
        p = _provider()
        edge = NODE_RELATIONS.edge("arango", relationshipType=relation.value)
        await p.batch_create_edges([edge], "nodeRelations")
        assert len(_writes(p)) >= 1, relation


async def test_edge_update_validates_the_fields_it_sets() -> None:
    p = _provider()
    await _rejected(p, p.update_edge("users/u1", "apps/a1", {"syncState": BOGUS}, "userAppRelation"))


async def test_edge_update_with_a_valid_state_is_written() -> None:
    p = _provider()
    await p.update_edge("users/u1", "apps/a1", {"syncState": "COMPLETED", "lastSyncUpdate": 5}, "userAppRelation")
    assert len(_writes(p)) == 1


async def test_edge_update_needs_no_required_fields() -> None:
    p = _provider()
    await p.update_edge("orgs/o1", "apps/a1", {"createdAtTimestamp": 7}, "orgAppRelation")
    assert len(_writes(p)) == 1


async def test_edge_update_does_not_trip_additional_properties_on_endpoint_keys() -> None:
    p = _provider()
    await p.update_edge("orgs/o1", "apps/a1", {"_from": "orgs/o1", "_to": "apps/a1", "createdAtTimestamp": 7}, "orgAppRelation")
    assert len(_writes(p)) == 1


# --------------------------------------------------------------------------------------------
# Collections without a schema never raise
# --------------------------------------------------------------------------------------------

GARBAGE = {"_key": "d1", "whatever": {"a": [1, {"b": 2}]}, "n": None, "recordType": BOGUS, "createdAtTimestamp": "x"}


@pytest.mark.parametrize("driver", DRIVER_IDS)
async def test_collection_without_a_schema_accepts_anything(driver: str) -> None:
    p = _provider(existing=())
    await NODE_DRIVERS[driver].call(p, NO_SCHEMA, [GARBAGE])
    assert len(_writes(p)) >= 1
    assert _probes(p) == []  # nothing to check, so nothing to look up


async def test_edge_collection_without_a_schema_accepts_anything() -> None:
    p = _provider()
    edge = {"_from": "drives/d1", "_to": "drives/d2", "anything": {"x": 1}, "createdAtTimestamp": "text"}
    await p.batch_create_edges([edge], "madeUpEdges")
    await p.create_edges_if_absent([edge], "madeUpEdges")
    assert len(_writes(p)) == 2


# --------------------------------------------------------------------------------------------
# GRAPH_SCHEMA_ENFORCEMENT=off
# --------------------------------------------------------------------------------------------

OFF_CASES = [(d, b) for d in DRIVER_IDS for b in _breakers_for(NODE_DRIVERS[d].kind)]


@pytest.mark.parametrize(("driver", "breaker"), OFF_CASES, ids=[f"{d}-{b}" for d, b in OFF_CASES])
async def test_switch_off_validates_nothing_on_node_writes(
    monkeypatch: pytest.MonkeyPatch, driver: str, breaker: str
) -> None:
    monkeypatch.setenv(ENV_SWITCH, "off")
    p = _provider(existing=())
    await NODE_DRIVERS[driver].call(p, RECORDS, [_break(breaker, _record())])
    assert len(_writes(p)) >= 1
    assert _probes(p) == []


@pytest.mark.parametrize(("driver", "family", "breaker"), EDGE_INVALID, ids=EDGE_INVALID_IDS)
async def test_switch_off_validates_nothing_on_edge_writes(
    monkeypatch: pytest.MonkeyPatch, driver: str, family: EdgeFamily, breaker: str
) -> None:
    monkeypatch.setenv(ENV_SWITCH, "off")
    p = _provider()
    await EDGE_DRIVERS[driver][0](p, family, [family.breakers[breaker](family.edge("arango"))])
    assert len(_writes(p)) >= 1


async def test_switch_off_validates_nothing_on_targeted_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SWITCH, "off")
    p = _provider()
    await p.compare_and_set_indexing_status(["r1"], "QUEUED", BOGUS)
    await p.reset_indexing_status_for_connector("c1", BOGUS)
    await p.compare_and_set_record_fields(["r1"], "rev1", {"indexingStatus": BOGUS})
    await p.update_edge("users/u1", "apps/a1", {"syncState": BOGUS}, "userAppRelation")
    assert len(_writes(p)) == 4


@pytest.mark.parametrize("value", ["strict", "STRICT", ""])
async def test_switch_values_other_than_off_keep_validation_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV_SWITCH, value)
    p = _provider()
    await _rejected(p, p.update_node("r1", RECORDS, {"recordType": BOGUS}))


# --------------------------------------------------------------------------------------------
# Every method named in the classification as payload-bearing is exercised, or its gap is on record
# --------------------------------------------------------------------------------------------

COVERED = (
    set(NODE_DRIVERS) | set(EDGE_DRIVERS)
    | {
        "set_record_properties", "compare_and_set_indexing_status", "compare_and_set_record_fields",
        "update_indexing_status_for_record_ids", "reset_indexing_status_for_connector",
        "batch_update_connector_status", "update_drive_sync_state", "update_user_sync_state", "update_edge",
        "create_organization_if_absent",
    }
)


async def test_an_extracted_organisation_is_validated_before_it_is_written() -> None:
    """It builds its own node from typed arguments; a name of the wrong type is still rejected before any write."""
    p = _provider()
    await p.create_organization_if_absent("org-1", {"id": "o1", "name": "Acme", "normalizedName": "acme"})
    assert p.client.execute_query.await_count == 1

    p = _provider()
    await _rejected(p, p.create_organization_if_absent("org-1", {"id": "o1", "name": 123}))

# Payload-bearing methods this file does not drive, with the reason. Each needs a typed model, a
# multi-statement fixture or a purpose-built graph, and is better pinned next to its own tests;
# they are listed so a gap is a decision on record, not an accident.
COVERAGE_GAPS: dict[str, str] = {
    name: reason
    for names, reason in (
        (("batch_upsert_records", "upsert_record_under_parent", "batch_upsert_people", "upsert_person_by_email", "upsert_person_by_source_key",
          "batch_upsert_app_roles", "batch_upsert_app_users", "batch_upsert_orgs", "batch_upsert_record_groups",
          "batch_upsert_user_groups"), "typed model in, delegates to batch_upsert_nodes; pin through the T3 converters"),
        (("upsert_sync_point", "update_sync_point_if", "store_page_token"), "schema-less collections today"),
        (("stamp_external_org_parents",), "stamps parentOrgId from its org_id argument, which is a plain string no caller can make invalid; it validates {parentOrgId: org_id}"),
        (("update_agent", "update_agent_template", "update_knowledge_base", "clone_agent_template"),
         "multi-statement, permission-checked; delegates to update_node / ensure_nodes"),
        (("stage_state_create", "stage_state_compare_and_set"), "stage-state fixture; own schema"),
        (("create_taxonomy_node_if_absent", "create_named_entities_if_absent", "add_taxonomy_aliases",
          "add_named_entity_aliases", "replace_named_entity_values", "apply_verified_user_email"),
         "taxonomy / entity helpers, multi-statement"),
        (("update_queued_duplicates_status", "claim_push_record", "retag_push_record", "record_purge_failure",
          "increment_node_field", "take_back_kept_record_group"), "values are computed by the provider, not supplied"),
        (("batch_create_user_app_edges", "upsert_authenticated_as", "ensure_app_membership",
          "ensure_taxonomy_hierarchy_edge", "ensure_team_app_edge", "create_record_relation",
          "create_record_group_relation", "create_record_groups_relation",
          "create_inherit_permissions_relation_record_group", "create_kb_permissions",
          "batch_upsert_record_permissions", "replace_edges_to", "replace_record_permissions",
          "link_record_to_group", "batch_update_team_member_roles", "update_agent_permission",
          "update_kb_permission", "copy_document_relationships", "copy_named_entity_mentions",
          "add_user_to_all_team", "ensure_all_team_with_users", "move_taxonomy_edges",
          "migrate_legacy_relation_edge", "split_link_edges"),
         "edge writers with provider-built properties or multi-statement; wire them to validate_edge"),
    )
    for name in names
}


def test_every_payload_bearing_method_is_exercised_or_a_recorded_gap() -> None:
    payload = {n for n, k in CLASSIFICATION.items() if k in PAYLOAD_KINDS}
    unaccounted = sorted(payload - COVERED - COVERAGE_GAPS.keys())
    assert not unaccounted, f"drive these in this file or add them to COVERAGE_GAPS with a reason: {unaccounted}"


def test_gaps_do_not_name_methods_that_are_covered() -> None:
    assert sorted(COVERAGE_GAPS.keys() & COVERED) == []
