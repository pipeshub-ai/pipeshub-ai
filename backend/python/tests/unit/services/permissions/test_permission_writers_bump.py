"""Wiring guard: a handler that changes who may read what moves the viewer-scope version.

The version is the only thing that makes a cached scope stale, so a new writer of team membership, users, apps or KB
permissions that forgets to move it leaves cached scopes wrong until the entry expires. These tests read the source
(like `tests/permissions/test_jira_wiring_guard.py`) and fail when a listed handler stops bumping, when an event is
added to the entity consumer without a decision, or when a KB change bumps every organisation.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import fakeredis

from app.services.cache import invalidation_hooks as hooks
from app.services.messaging.kafka.handlers import entity as entity_events
from app.services.permissions.cache import ViewerScopeCache

APP = Path(__file__).resolve().parents[4] / "app"
BUMPS = {"notify_principals_changed", "notify_connector_permissions_changed", "notify_connector_sync_completed",
         "notify_kb_records_changed"}
# The processor's own helpers that call `notify_connector_permissions_changed` (checked by the test of `_access_changed`),
# and the team routes' wrapper of `notify_principals_changed`.
PROCESSOR_BUMPS = {"_access_changed", "_drop_access_after", "_principals_changed"}
# Entity events that change no one's access (an organisation appearing or being renamed).
NO_ACCESS_CHANGE = {"orgCreated", "orgUpdated"}


def _tree(rel: str) -> ast.Module:
    return ast.parse((APP / rel).read_text())


def _calls(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "")
    return out


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def test_every_team_write_route_bumps() -> None:
    tree = _tree("api/routes/entity.py")
    writers = [
        fn for fn in ast.walk(tree) if isinstance(fn, ast.AsyncFunctionDef)
        if any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ("post", "put", "patch", "delete")
               and d.args and "team" in ast.unparse(d.args[0]) for d in fn.decorator_list)]
    assert {w.name for w in writers} >= {"create_team", "update_team", "delete_team"}
    assert [w.name for w in writers if not {"notify_principals_changed", "_principals_changed"} & _calls(w)] == []


def test_every_entity_event_decides_whether_it_bumps() -> None:
    tree = _tree("services/messaging/kafka/handlers/entity.py")
    dispatched = {c.value for n in ast.walk(_function(tree, "_dispatch")) if isinstance(n, ast.Compare)
                  for c in n.comparators if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    assert dispatched, "the dispatcher moved: update this guard"
    assert dispatched - NO_ACCESS_CHANGE == set(entity_events.PRINCIPAL_EVENTS)
    assert "notify_principals_changed" in _calls(_function(tree, "process_event"))


async def test_a_principal_event_moves_the_version_and_an_org_event_does_not() -> None:
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    cache = ViewerScopeCache(lambda: client)
    hooks.init_viewer_scope_version(cache)
    try:
        service = entity_events.EntityEventService(MagicMock(), MagicMock(), MagicMock())

        async def handled(_event: str, _payload: dict) -> bool:
            return True

        service._dispatch = handled  # type: ignore[method-assign]
        before = await cache.version("org-1")
        assert before is not None
        for event in sorted(entity_events.PRINCIPAL_EVENTS):
            await service.process_event(event, {"orgId": "org-1"})
        after = await cache.version("org-1")
        assert after == before + len(entity_events.PRINCIPAL_EVENTS)
        await service.process_event("orgCreated", {"orgId": "org-1"})
        assert await cache.version("org-1") == after
        await service.process_event("userDeleted", {"orgId": "org-2"})
        assert await cache.version("org-1") == after  # another organisation's event leaves this one alone
    finally:
        hooks.reset_viewer_scope_version()


def test_no_kb_change_bumps_every_organisation() -> None:
    """`notify_kb_records_changed(kb_id)` with no organisation moves the global counter, which drops every
    organisation's cached scopes and structure."""
    bare: list[str] = []
    for path in APP.rglob("*.py"):
        if path.name == "invalidation_hooks.py":
            continue
        source = path.read_text(encoding="utf-8-sig")
        if "notify_kb_records_changed" not in source:
            continue
        bare.extend(
            f"{path.relative_to(APP)}:{n.lineno}" for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "notify_kb_records_changed"
            and len(n.args) + len(n.keywords) < 2)
    assert bare == []


def test_connector_permission_writes_still_bump_through_the_hook() -> None:
    tree = _tree("connectors/core/base/data_processor/data_source_entities_processor.py")
    assert "notify_connector_permissions_changed" in _calls(_function(tree, "_access_changed"))
    assert BUMPS & _calls(_function(tree, "_access_changed"))


# Writers of PERMISSION / USER_APP_RELATION edges outside the graph providers and the connector processor (whose
# `_access_changed` is checked above). Each must call a bump. Artifact unshare narrows access; the connector
# creator's group only widens it, and is listed so the list is complete.
LISTED_WRITERS = {
    "services/artifact_registry/sharing.py": "unshare",
    "connectors/core/base/connector/connector_service.py": "ensure_connector_group_permission",
}


def _enclosing_functions(tree: ast.Module) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)]


def test_listed_permission_writers_bump() -> None:
    for rel, name in LISTED_WRITERS.items():
        fn = _function(_tree(rel), name)
        assert "notify_principals_changed" in _calls(fn), f"{rel}::{name} writes permission edges and must bump"


DELETE_CALLS = {"batch_delete_edges", "delete_edge", "delete_edges_from", "delete_edges_to",
                "delete_edges_by_relationship_types", "delete_edges_to_groups", "delete_edges_between_collections"}
# Provider methods that remove edges or nodes people read through (a connector with its apps, a group, a role, a record
# group, a team's or a knowledge base's members): every caller outside the providers is flagged like a direct edge delete.
COMPOUND_DELETES = {"delete_connector_instance", "delete_user_group_by_id", "remove_user_access_to_connector",
                    "remove_kb_permission", "delete_team_member_edges", "delete_all_team_permissions", "delete_kb_hub_app",
                    "reap_stale_external_app_relations"}
# `delete_nodes_and_edges` is flagged when it removes nodes that hold or carry access.
ACCESS_NODE_COLLECTIONS = ("GROUPS", "ROLES", "RECORD_GROUPS", "APPS", "TEAMS")
SYNC = "connector writer: its sync end (`notify_connector_sync_completed`) or the permission hook (`_access_changed`) bumps"
STORE = "pass-through wrapper of the data store: its callers are listed or bump"
AGENT = "agent and skill edges, not records: no viewer scope reads them"
# function -> why it may delete PERMISSION / USER_APP_RELATION edges, or edges in a collection the call does not name
# with a literal, without bumping itself. An entry that no longer matches a flagged function fails the test.
NO_BUMP = {
    "agents/agent_loop/skills/graph_store.py::_sync_relation_edges": AGENT,
    "agents/agent_loop/skills/graph_store.py::detach_from_agents": AGENT,
    "api/routes/agent.py::update_agent": "agent sharing edges, not records: no viewer scope reads them",
    "connectors/core/base/data_processor/data_source_entities_processor.py::_drop_shared_groups": SYNC,
    "connectors/core/base/data_processor/data_source_entities_processor.py::_drop_stale_group_parents": SYNC,
    "connectors/core/base/data_store/graph_data_store.py::batch_delete_edges": STORE,
    "connectors/core/base/data_store/graph_data_store.py::delete_edge": STORE,
    "connectors/core/base/data_store/graph_data_store.py::delete_edges_between_collections": STORE,
    "connectors/core/base/data_store/graph_data_store.py::delete_edges_by_relationship_types": STORE,
    "connectors/core/base/data_store/graph_data_store.py::delete_edges_from": STORE,
    "connectors/core/base/data_store/graph_data_store.py::delete_edges_to": STORE,
    "connectors/core/base/data_store/graph_data_store.py::delete_edges_to_groups": STORE,
    "connectors/core/base/data_store/graph_data_store.py::delete_inherit_permissions_relation_record": STORE,
    "agents/agent_loop/skills/graph_store.py::delete_skill": AGENT,
    "api/routes/chatbot.py::_rollback_attachment_records":
        "rolls back attachment records the same request just created: nothing could have been cached for them",
    "connectors/core/base/data_store/graph_data_store.py::delete_nodes_and_edges": STORE,
    "connectors/core/base/data_store/graph_data_store.py::delete_user_group_by_id": STORE,
    "connectors/core/base/data_store/graph_data_store.py::reap_stale_external_app_relations": STORE,
    "connectors/core/base/data_store/graph_data_store.py::remove_user_access_to_connector": STORE,
    "migrations/kb_apps_migration.py::_migrate_org":
        "one-off startup migration of legacy knowledge bases; a scope cached before it expires with its entry TTL",
    "scripts/connector_deletion_integration_test.py::run_deletion_test": "a manual test script, not run by a service",
    "connectors/sources/bookstack/connector.py::_handle_user_upsert_event": SYNC,
    "connectors/sources/bookstack/connector.py::_withdraw_user_roles": SYNC,
    "connectors/sources/notion/connector.py::_remove_departed_workspace_users": SYNC,
    "connectors/sources/notion/connector.py::remove_leavers": SYNC,
    "connectors/sources/salesforce/connector.py::write_permissions": "connector writer: the sync bumps for the orgs it narrowed",
    "migrations/nextcloud_user_app_edges_migration.py::migrate":
        "one-off startup repair of a connector's user edges; a scope cached before it expires with its entry TTL",
    "modules/transformers/edge_reconciler.py::reconcile": "record enrichment edges (topics, entities), not permissions",
    "modules/transformers/graphdb.py::_reconcile_edges": "record enrichment edges (topics, entities), not permissions",
}


def _flagged_delete_functions() -> dict[str, bool]:
    """Functions outside the graph providers that call an edge delete on PERMISSION or USER_APP_RELATION, or with a
    collection that is not a `CollectionNames.X` literal (a variable could be either). Value: whether it bumps."""
    flagged: dict[str, bool] = {}
    for path in APP.rglob("*.py"):
        rel = str(path.relative_to(APP))
        if rel.startswith("services/graph_db/"):
            continue
        source = path.read_text(encoding="utf-8-sig")
        if not any(name in source for name in (*DELETE_CALLS, *COMPOUND_DELETES, "delete_nodes_and_edges")):
            continue
        for fn in _enclosing_functions(ast.parse(source)):
            for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                text = ast.unparse(call)
                if call.func.attr in DELETE_CALLS:
                    hit = "PERMISSION" in text or "USER_APP_RELATION" in text or "CollectionNames." not in text
                elif call.func.attr in COMPOUND_DELETES:
                    receiver = ast.unparse(call.func.value)
                    hit = any(w in receiver for w in ("graph", "store", "provider"))     # not a service in front of it
                elif call.func.attr == "delete_nodes_and_edges":
                    hit = any(f"CollectionNames.{c}." in text for c in ACCESS_NODE_COLLECTIONS) or "CollectionNames." not in text
                else:
                    continue
                if hit:
                    flagged[f"{rel}::{fn.name}"] = bool((BUMPS | PROCESSOR_BUMPS) & _calls(fn))
    return flagged


def test_every_permission_edge_delete_outside_the_providers_bumps_or_is_listed_with_a_reason() -> None:
    flagged = _flagged_delete_functions()
    unlisted = sorted(k for k, bumps in flagged.items() if not bumps and k not in NO_BUMP)
    assert unlisted == [], f"bump (notify_principals_changed) or list with a reason in NO_BUMP: {unlisted}"
    stale = sorted(k for k in NO_BUMP if k not in flagged or flagged[k])
    assert stale == [], f"stale NO_BUMP entries (not flagged any more, or they bump now): {stale}"
