"""Planner table and orchestration for the project -> hidden KB reconcile (H7)."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.connectors.sources.localKB.handlers.project_kb_reconcile import (
    KbDesired,
    KbGrant,
    KbPermissions,
    KbPrincipalRef,
    PrincipalType,
    ProjectKbDesired,
    ProjectKbReconcileError,
    ProjectKbSyncPayload,
    plan_kb_permission_ops,
    reconcile_project_kb,
)
from app.services.messaging.kafka.handlers.entity import EntityEventService

U, T = PrincipalType.USER, PrincipalType.TEAM


def grant(kind: PrincipalType, pid: str, role: str) -> KbGrant:
    return KbGrant(principal_type=kind, principal_id=pid, role=role)


def ref(kind: PrincipalType, pid: str) -> KbPrincipalRef:
    return KbPrincipalRef(principal_type=kind, principal_id=pid)


PLAN_CASES = [
    pytest.param(
        KbPermissions(users={"A": "OWNER"}, created_by="A"),
        KbDesired(users={"A": "OWNER", "E": "WRITER", "V": "READER"}, created_by="A"),
        [grant(U, "E", "WRITER"), grant(U, "V", "READER")], [], None,
        id="add",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER", "M": "READER"}, created_by="A"),
        KbDesired(users={"A": "OWNER"}, created_by="A"),
        [], [ref(U, "M")], None,
        id="remove",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER", "E": "READER"}, created_by="A"),
        KbDesired(users={"A": "OWNER", "E": "WRITER"}, created_by="A"),
        [grant(U, "E", "WRITER")], [], None,
        id="role-change",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER"}, teams={"T1": None, "T2": "WRITER", "T3": "READER"}, created_by="A"),
        KbDesired(users={"A": "OWNER"}, teams={"T1": "WRITER", "T2": "WRITER", "T4": "READER"}, created_by="A"),
        [grant(T, "T1", "WRITER"), grant(T, "T4", "READER")], [ref(T, "T3")], None,
        id="team-rows",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER"}, created_by="A"),
        KbDesired(users={"A": "OWNER"}, teams={"all_org": "READER"}, created_by="A"),
        [grant(T, "all_org", "READER")], [], None,
        id="org-visibility-on",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER"}, teams={"all_org": "READER"}, created_by="A"),
        KbDesired(users={"A": "OWNER"}, created_by="A"),
        [], [ref(T, "all_org")], None,
        id="org-visibility-off",
    ),
    pytest.param(
        KbPermissions(users={"E": "OWNER", "M": "READER"}, teams={"T": None}, created_by="E"),
        KbDesired(users={"A": "OWNER", "E": "WRITER"}, teams={"T": "WRITER"}, created_by="A"),
        [grant(U, "A", "OWNER"), grant(U, "E", "WRITER"), grant(T, "T", "WRITER")], [ref(U, "M")], "A",
        id="creator-residue",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER", "O2": "OWNER"}, created_by="A"),
        KbDesired(users={"A": "OWNER", "O2": "WRITER"}, created_by="A"),
        [grant(U, "O2", "WRITER")], [], None,
        id="extra-owner-demoted-while-owner-remains",
    ),
    pytest.param(
        KbPermissions(users={"OLD": "OWNER"}, created_by="OLD"),
        KbDesired(users={"NEW": "OWNER"}, created_by="NEW"),
        [grant(U, "NEW", "OWNER")], [ref(U, "OLD")], "NEW",
        id="owner-handover-grants-before-removal",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER", "E": "WRITER"}, teams={"T": "READER"}, created_by="A"),
        KbDesired(users={"A": "OWNER", "E": "WRITER"}, teams={"T": "READER"}, created_by="A"),
        [], [], None,
        id="no-op",
    ),
]


@pytest.mark.parametrize(("current", "desired", "grants", "removals", "created_by"), PLAN_CASES)
def test_plan_kb_permission_ops(current, desired, grants, removals, created_by) -> None:
    ops = plan_kb_permission_ops(current, desired)
    assert ops.grants == grants
    assert ops.removals == removals
    assert ops.created_by == created_by
    assert ops.is_noop is (not grants and not removals and created_by is None)


@pytest.mark.parametrize(("current", "desired"), [pytest.param(p.values[0], p.values[1], id=p.id) for p in PLAN_CASES])
def test_applying_the_plan_converges_and_keeps_an_owner(current, desired) -> None:
    ops = plan_kb_permission_ops(current, desired)
    users, teams = dict(current.users), dict(current.teams)
    for g in ops.grants:
        (users if g.principal_type is U else teams)[g.principal_id] = g.role
    for r in ops.removals:
        (users if r.principal_type is U else teams).pop(r.principal_id)
    after = KbPermissions(users=users, teams=teams, created_by=ops.created_by or current.created_by)
    assert "OWNER" in users.values()
    assert plan_kb_permission_ops(after, desired).is_noop


def test_desired_without_owner_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KbDesired(users={"E": "WRITER"}, created_by="E")


def test_team_cannot_be_granted_owner() -> None:
    with pytest.raises(ValidationError):
        KbDesired(users={"A": "OWNER"}, teams={"T": "OWNER"}, created_by="A")


ORG, KB, PROJECT = "org1", "kb1", "p1"
HIDDEN_KB = {"id": KB, "type": "KB", "isHidden": True, "name": f"project:{PROJECT}", "orgId": ORG, "createdBy": "mongo-E"}


def desired(**overrides) -> ProjectKbDesired:
    fields = {"project_id": PROJECT, "owner_user_id": "mongo-A", "editor_user_ids": ["mongo-E"], "org_visible": True}
    return ProjectKbDesired(**{**fields, **overrides})


def make_graph(kb=HIDDEN_KB, permissions=None) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=kb)
    graph.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
        return_value={"mongo-A": "A", "mongo-E": "E", "mongo-V": "V"}
    )
    graph.get_nodes_by_field_in = AsyncMock(return_value=[{"id": "all_org1", "orgId": ORG}])
    graph.list_kb_permissions = AsyncMock(
        return_value=permissions if permissions is not None else [{"id": "E", "type": "USER", "role": "OWNER"}]
    )
    graph.create_kb_principal_permissions = AsyncMock(return_value={"success": True})
    graph.update_node = AsyncMock(return_value=True)
    graph.count_kb_owners = AsyncMock(return_value=1)
    graph.delete_edge = AsyncMock(return_value=False)
    return graph


@pytest.mark.parametrize(
    "kb",
    [
        pytest.param({**HIDDEN_KB, "isHidden": False}, id="not-hidden"),
        pytest.param({**HIDDEN_KB, "name": "project:other"}, id="other-project-name"),
        pytest.param({**HIDDEN_KB, "name": "Engineering docs"}, id="user-collection"),
        pytest.param({**HIDDEN_KB, "orgId": "org2"}, id="other-org"),
        pytest.param({**HIDDEN_KB, "type": "DRIVE"}, id="not-a-kb"),
        pytest.param(None, id="missing"),
    ],
)
async def test_reconcile_refuses_anything_but_the_projects_hidden_kb(kb) -> None:
    graph = make_graph(kb=kb)
    result = await reconcile_project_kb(graph, ORG, KB, desired())
    assert result.skipped_reason
    assert result.ops.is_noop
    graph.create_kb_principal_permissions.assert_not_called()
    graph.delete_edge.assert_not_called()
    graph.update_node.assert_not_called()


async def test_reconcile_writes_grants_then_creator_then_removals() -> None:
    graph = make_graph(
        permissions=[
            {"id": "E", "type": "USER", "role": "OWNER"},
            {"id": "M", "type": "USER", "role": "READER"},
            {"id": "tx", "type": "TEAM", "role": None},
        ]
    )
    order = []
    graph.create_kb_principal_permissions.side_effect = lambda *a, **k: order.append("grant") or {"success": True}
    graph.update_node.side_effect = lambda *a, **k: order.append("creator") or True
    graph.delete_edge.side_effect = lambda *a, **k: order.append("remove") or True

    await reconcile_project_kb(graph, ORG, KB, desired())

    assert order == ["grant", "creator", "remove", "remove"]
    grants = graph.create_kb_principal_permissions.call_args.args[1]
    assert {(g["principalType"], g["principalId"], g["role"]) for g in grants} == {
        ("user", "A", "OWNER"),
        ("user", "E", "WRITER"),
        ("team", "all_org1", "READER"),
    }
    graph.update_node.assert_awaited_once_with(KB, "apps", {"createdBy": "mongo-A"})
    removed = {call.args[0] for call in graph.delete_edge.await_args_list}
    assert removed == {"M", "tx"}


async def test_reconcile_skips_a_team_outside_the_org() -> None:
    graph = make_graph()
    graph.get_nodes_by_field_in.return_value = [{"id": "all_org1", "orgId": "other"}]
    await reconcile_project_kb(graph, ORG, KB, desired())
    grants = graph.create_kb_principal_permissions.call_args.args[1]
    assert all(g["principalType"] == "user" for g in grants)


async def test_reconcile_fails_when_the_owner_is_not_in_the_graph() -> None:
    graph = make_graph()
    graph.get_graph_user_keys_by_mongo_user_ids.return_value = {"mongo-E": "E"}
    with pytest.raises(ProjectKbReconcileError):
        await reconcile_project_kb(graph, ORG, KB, desired())
    graph.delete_edge.assert_not_called()


async def test_reconcile_stops_before_removals_when_the_grant_fails() -> None:
    graph = make_graph(permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.create_kb_principal_permissions.return_value = {"success": False, "reason": "boom"}
    with pytest.raises(ProjectKbReconcileError):
        await reconcile_project_kb(graph, ORG, KB, desired())
    graph.delete_edge.assert_not_called()


async def test_reconcile_never_removes_when_no_owner_would_remain() -> None:
    graph = make_graph(permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.count_kb_owners.return_value = 0
    with pytest.raises(ProjectKbReconcileError):
        await reconcile_project_kb(graph, ORG, KB, desired())
    graph.delete_edge.assert_not_called()


async def test_reconcile_fails_without_writes_when_the_kb_edges_cannot_be_read() -> None:
    graph = make_graph()
    graph.list_kb_permissions.side_effect = RuntimeError("graph down")
    with pytest.raises(RuntimeError):
        await reconcile_project_kb(graph, ORG, KB, desired())
    assert graph.list_kb_permissions.await_args.kwargs == {"raise_on_error": True}
    graph.create_kb_principal_permissions.assert_not_called()
    graph.update_node.assert_not_called()
    graph.delete_edge.assert_not_called()


async def test_reconcile_treats_an_already_missing_edge_as_removed() -> None:
    graph = make_graph(permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.delete_edge.return_value = False
    result = await reconcile_project_kb(graph, ORG, KB, desired(editor_user_ids=[], org_visible=False))
    assert [r.principal_id for r in result.ops.removals] == ["M"]


def event_payload(**overrides) -> dict:
    payload = {
        "orgId": ORG, "projectId": PROJECT, "kbId": KB, "ownerUserId": "mongo-A",
        "editorUserIds": ["mongo-E"], "viewerUserIds": [], "teams": [{"teamId": "t1", "role": "WRITER"}],
        "orgVisible": False,
    }
    return {**payload, **overrides}


def test_payload_parses_the_camel_case_wire_shape() -> None:
    parsed = ProjectKbSyncPayload.model_validate(event_payload()).desired
    assert parsed.owner_user_id == "mongo-A"
    assert parsed.teams[0].team_id == "t1"


def make_service(graph) -> EntityEventService:
    return EntityEventService(logging.getLogger("test"), graph, MagicMock())


async def test_handler_returns_true_after_a_successful_sync() -> None:
    graph = make_graph()
    assert await make_service(graph).process_event("projectKbSync", event_payload()) is True
    graph.create_kb_principal_permissions.assert_awaited()


async def test_handler_returns_false_when_the_graph_write_fails_and_a_replay_converges() -> None:
    graph = make_graph(permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.create_kb_principal_permissions.return_value = {"success": False, "reason": "boom"}
    service = make_service(graph)
    assert await service.process_event("projectKbSync", event_payload()) is False
    graph.create_kb_principal_permissions.return_value = {"success": True}
    assert await service.process_event("projectKbSync", event_payload()) is True
    graph.delete_edge.assert_awaited_once()


async def test_handler_returns_false_for_a_malformed_payload() -> None:
    assert await make_service(make_graph()).process_event("projectKbSync", {"kbId": KB}) is False
