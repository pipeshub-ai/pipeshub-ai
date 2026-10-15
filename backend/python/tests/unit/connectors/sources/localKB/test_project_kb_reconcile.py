"""Planner table and orchestration for the project -> hidden KB reconcile."""

import logging
from collections.abc import Awaitable, Callable
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


def grant(kind: PrincipalType, pid: str, role: str | None = None) -> KbGrant:
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
        KbPermissions(users={"A": "OWNER"}, teams={"T1", "T2", "T3"}, created_by="A"),
        KbDesired(users={"A": "OWNER"}, teams={"T1", "T2", "T4"}, created_by="A"),
        [grant(T, "T4")], [ref(T, "T3")], None,
        id="team-rows",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER"}, created_by="A"),
        KbDesired(users={"A": "OWNER"}, teams={"all_org"}, created_by="A"),
        [grant(T, "all_org")], [], None,
        id="org-visibility-on",
    ),
    pytest.param(
        KbPermissions(users={"A": "OWNER"}, teams={"all_org"}, created_by="A"),
        KbDesired(users={"A": "OWNER"}, created_by="A"),
        [], [ref(T, "all_org")], None,
        id="org-visibility-off",
    ),
    pytest.param(
        KbPermissions(users={"E": "OWNER", "M": "READER"}, teams={"T"}, created_by="E"),
        KbDesired(users={"A": "OWNER", "E": "WRITER"}, teams={"T"}, created_by="A"),
        [grant(U, "A", "OWNER"), grant(U, "E", "WRITER")], [ref(U, "M")], "A",
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
        KbPermissions(users={"A": "OWNER", "E": "WRITER"}, teams={"T"}, created_by="A"),
        KbDesired(users={"A": "OWNER", "E": "WRITER"}, teams={"T"}, created_by="A"),
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
    users, teams = dict(current.users), set(current.teams)
    for g in ops.grants:
        if g.principal_type is U:
            users[g.principal_id] = g.role
        else:
            teams.add(g.principal_id)
    for r in ops.removals:
        if r.principal_type is U:
            users.pop(r.principal_id)
        else:
            teams.remove(r.principal_id)
    after = KbPermissions(users=users, teams=teams, created_by=ops.created_by or current.created_by)
    assert "OWNER" in users.values()
    assert plan_kb_permission_ops(after, desired).is_noop


def test_desired_without_owner_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KbDesired(users={"E": "WRITER"}, created_by="E")


def granted(graph: MagicMock) -> set[tuple[str, str, str | None]]:
    edges = graph.batch_create_edges.call_args.args[0]
    assert graph.batch_create_edges.call_args.args[1] == "permission"
    return {(e["type"].lower(), e["from_id"], e.get("role")) for e in edges}


ORG, KB, PROJECT = "org1", "kb1", "p1"
HIDDEN_KB = {"id": KB, "type": "KB", "isHidden": True, "name": f"project:{PROJECT}", "orgId": ORG, "createdBy": "mongo-E"}


def desired(**overrides) -> ProjectKbDesired:
    fields = {"project_id": PROJECT, "owner_user_id": "mongo-A", "editor_user_ids": ["mongo-E"], "org_visible": True}
    return ProjectKbDesired(**{**fields, **overrides})


def user_keys_in_graph(known: dict[str, str]) -> Callable[..., Awaitable[dict[str, str]]]:
    """Behaves like both providers: any unknown id raises unless ``allow_missing``."""

    async def lookup(
        user_ids: list[str], org_id: str | None = None, *, chunk_size: int, allow_missing: bool = False
    ) -> dict[str, str]:
        missing = [u for u in user_ids if u not in known]
        if missing and not allow_missing:
            raise ValueError(f"Users not found in graph: {missing}")
        return {u: known[u] for u in user_ids if u in known}

    return lookup


def teams_in_graph(teams: list[dict] | Exception) -> Callable[..., Awaitable[list[dict]]]:
    """A provider read that, like the real ones, returns ``[]`` on failure unless asked to raise."""

    async def lookup(*args: object, raise_on_error: bool = False, **kwargs: object) -> list[dict]:
        if isinstance(teams, Exception):
            if raise_on_error:
                raise teams
            return []
        return teams

    return lookup


def make_graph(kb=HIDDEN_KB, permissions=None) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=kb)
    graph.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
        side_effect=user_keys_in_graph({"mongo-A": "A", "mongo-E": "E", "mongo-V": "V"})
    )
    graph.get_nodes_by_field_in = AsyncMock(side_effect=teams_in_graph([{"id": "all_org1", "orgId": ORG}]))
    rows = permissions if permissions is not None else [{"id": "E", "type": "USER", "role": "OWNER"}]

    async def list_kb_permissions(*args: object, **kwargs: object) -> list[dict]:
        removed = {c.args[0] for c in graph.delete_edge.await_args_list}
        return [r for r in rows if r["id"] not in removed]

    graph.list_kb_permissions = AsyncMock(side_effect=list_kb_permissions)
    graph.batch_create_edges = AsyncMock(return_value=True)
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
    graph.batch_create_edges.assert_not_called()
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
    graph.batch_create_edges.side_effect = lambda *a, **k: order.append("grant") or True
    graph.update_node.side_effect = lambda *a, **k: order.append("creator") or True
    graph.delete_edge.side_effect = lambda *a, **k: order.append("remove") or True

    await reconcile_project_kb(graph, ORG, KB, desired())

    assert order == ["grant", "creator", "remove", "remove"]
    assert granted(graph) == {
        ("user", "A", "OWNER"),
        ("user", "E", "WRITER"),
        ("team", "all_org1", None),
    }
    graph.update_node.assert_awaited_once_with(KB, "apps", {"createdBy": "mongo-A"})
    removed = {call.args[0] for call in graph.delete_edge.await_args_list}
    assert removed == {"M", "tx"}


async def test_reconcile_skips_a_team_outside_the_org() -> None:
    graph = make_graph()
    graph.get_nodes_by_field_in.side_effect = teams_in_graph([{"id": "all_org1", "orgId": "other"}])
    await reconcile_project_kb(graph, ORG, KB, desired())
    assert all(kind == "user" for kind, _, _ in granted(graph))


async def test_reconcile_fails_when_the_owner_is_not_in_the_graph() -> None:
    graph = make_graph()
    graph.get_graph_user_keys_by_mongo_user_ids.side_effect = user_keys_in_graph({"mongo-E": "E"})
    with pytest.raises(ProjectKbReconcileError):
        await reconcile_project_kb(graph, ORG, KB, desired())
    graph.delete_edge.assert_not_called()


async def test_reconcile_drops_a_member_with_no_graph_user_and_applies_the_rest() -> None:
    graph = make_graph()
    await reconcile_project_kb(graph, ORG, KB, desired(editor_user_ids=["mongo-E", "mongo-new"]))
    assert {(pid, role) for kind, pid, role in granted(graph) if kind == "user"} == {
        ("A", "OWNER"), ("E", "WRITER"),
    }


async def test_reconcile_fails_without_removing_team_edges_when_the_teams_cannot_be_read() -> None:
    graph = make_graph(
        permissions=[{"id": "A", "type": "USER", "role": "OWNER"}, {"id": "all_org1", "type": "TEAM", "role": None}]
    )
    graph.get_nodes_by_field_in.side_effect = teams_in_graph(RuntimeError("graph down"))
    with pytest.raises(RuntimeError):
        await reconcile_project_kb(graph, ORG, KB, desired(editor_user_ids=[]))
    graph.delete_edge.assert_not_called()


async def test_reconcile_stops_before_removals_when_the_grant_fails() -> None:
    graph = make_graph(permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.batch_create_edges.side_effect = RuntimeError("boom")
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
    graph.batch_create_edges.assert_not_called()
    graph.update_node.assert_not_called()
    graph.delete_edge.assert_not_called()


async def test_reconcile_treats_an_already_missing_edge_as_removed() -> None:
    graph = make_graph(permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.delete_edge.return_value = False
    result = await reconcile_project_kb(graph, ORG, KB, desired(editor_user_ids=[], org_visible=False))
    assert [r.principal_id for r in result.ops.removals] == ["M"]


async def test_reconcile_fails_and_keeps_the_version_when_a_removed_edge_is_still_there() -> None:
    graph = make_graph(kb={**HIDDEN_KB, "projectSyncVersion": 100}, permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.list_kb_permissions = AsyncMock(return_value=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.delete_edge.return_value = False
    with pytest.raises(ProjectKbReconcileError):
        await reconcile_project_kb(graph, ORG, KB, desired(editor_user_ids=[], org_visible=False, version=200))
    graph.delete_edge.assert_awaited_once()
    assert graph.list_kb_permissions.await_count == 2
    assert version_writes(graph) == []


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
    assert not hasattr(parsed.teams[0], "role")


def make_service(graph) -> EntityEventService:
    return EntityEventService(logging.getLogger("test"), graph, MagicMock())


async def test_handler_returns_true_after_a_successful_sync() -> None:
    graph = make_graph()
    assert await make_service(graph).process_event("projectKbSync", event_payload()) is True
    graph.batch_create_edges.assert_awaited()


async def test_handler_returns_false_when_the_graph_write_fails_and_a_replay_converges() -> None:
    graph = make_graph(permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    graph.batch_create_edges.side_effect = RuntimeError("boom")
    service = make_service(graph)
    assert await service.process_event("projectKbSync", event_payload()) is False
    graph.batch_create_edges.side_effect = None
    assert await service.process_event("projectKbSync", event_payload()) is True
    graph.delete_edge.assert_awaited_once()


async def test_handler_acks_a_malformed_payload_without_touching_the_graph() -> None:
    graph = make_graph()
    assert await make_service(graph).process_event("projectKbSync", {"kbId": KB}) is True
    graph.get_document.assert_not_called()


def test_payload_carries_the_snapshot_version() -> None:
    assert ProjectKbSyncPayload.model_validate(event_payload(version=1700)).desired.version == 1700
    assert ProjectKbSyncPayload.model_validate(event_payload()).desired.version is None


def version_writes(graph: MagicMock) -> list:
    return [c.args[2]["projectSyncVersion"] for c in graph.update_node.await_args_list if "projectSyncVersion" in c.args[2]]


async def test_reconcile_skips_a_snapshot_older_than_the_applied_one() -> None:
    graph = make_graph(kb={**HIDDEN_KB, "projectSyncVersion": 200}, permissions=[{"id": "M", "type": "USER", "role": "READER"}])
    result = await reconcile_project_kb(graph, ORG, KB, desired(version=100))
    assert result.stale
    graph.batch_create_edges.assert_not_called()
    graph.update_node.assert_not_called()
    graph.delete_edge.assert_not_called()


@pytest.mark.parametrize("stored", [None, 100, 200])
async def test_reconcile_applies_a_newer_or_equal_snapshot_and_records_it_last(stored) -> None:
    kb = {**HIDDEN_KB, "projectSyncVersion": stored} if stored is not None else HIDDEN_KB
    graph = make_graph(kb=kb, permissions=[{"id": "E", "type": "USER", "role": "OWNER"}, {"id": "M", "type": "USER", "role": "READER"}])
    order = []
    graph.batch_create_edges.side_effect = lambda *a, **k: order.append("grant") or True
    graph.update_node.side_effect = lambda *a, **k: order.append(next(iter(a[2]))) or True
    graph.delete_edge.side_effect = lambda *a, **k: order.append("remove") or True

    result = await reconcile_project_kb(graph, ORG, KB, desired(version=200))

    assert not result.stale
    assert order[-1] == ("projectSyncVersion" if stored != 200 else "remove")
    assert version_writes(graph) == ([] if stored == 200 else [200])


async def test_reconcile_records_the_version_even_when_nothing_changes() -> None:
    graph = make_graph(
        kb={**HIDDEN_KB, "createdBy": "mongo-A", "projectSyncVersion": 100},
        permissions=[{"id": "A", "type": "USER", "role": "OWNER"}],
    )
    await reconcile_project_kb(graph, ORG, KB, desired(editor_user_ids=[], org_visible=False, version=150))
    graph.update_node.assert_awaited_once_with(KB, "apps", {"projectSyncVersion": 150})


async def test_reconcile_without_a_version_never_lowers_the_recorded_one() -> None:
    graph = make_graph(kb={**HIDDEN_KB, "projectSyncVersion": 200})
    result = await reconcile_project_kb(graph, ORG, KB, desired())
    assert not result.stale
    graph.batch_create_edges.assert_awaited()
    assert version_writes(graph) == []


async def test_reconcile_does_not_record_the_version_when_the_apply_fails() -> None:
    graph = make_graph(kb={**HIDDEN_KB, "projectSyncVersion": 100})
    graph.batch_create_edges.side_effect = RuntimeError("boom")
    with pytest.raises(ProjectKbReconcileError):
        await reconcile_project_kb(graph, ORG, KB, desired(version=200))
    assert version_writes(graph) == []


async def test_reconcile_fails_when_the_version_cannot_be_recorded() -> None:
    graph = make_graph()
    graph.update_node.side_effect = lambda *a, **k: "projectSyncVersion" not in a[2]
    with pytest.raises(ProjectKbReconcileError):
        await reconcile_project_kb(graph, ORG, KB, desired(version=200))


async def test_handler_acks_a_stale_event() -> None:
    graph = make_graph(kb={**HIDDEN_KB, "projectSyncVersion": 200})
    assert await make_service(graph).process_event("projectKbSync", event_payload(version=100)) is True
    graph.batch_create_edges.assert_not_called()
