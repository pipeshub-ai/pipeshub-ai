import pytest

from app.connectors.sources.linear.team_access import (
    WORKSPACE_MEMBERS_GROUP_ID,
    boundary_ancestors,
    is_full_member,
    team_permissions,
    visibility,
)
from app.models.permission import EntityType


def _groups(permissions) -> list:
    assert all(p.entity_type == EntityType.GROUP for p in permissions)
    return [p.external_id for p in permissions]


@pytest.mark.parametrize("user, expected", [
    ({"email": "a@x", "active": True}, True),
    ({"email": "a@x"}, True),
    ({"email": "a@x", "guest": True}, False),
    ({"email": "a@x", "active": False}, False),
    ({"email": "", "active": True}, False),
])
def test_full_member(user, expected) -> None:
    assert is_full_member(user) is expected


@pytest.mark.parametrize("team, expected", [
    ({"visibility": "public"}, "public"),
    ({"visibility": "PRIVATE"}, "private"),
    ({"visibility": "restricted"}, "restricted"),
    ({"private": True}, "private"),
    ({"private": False}, "public"),
    ({"visibility": "something-new", "private": True}, "private"),
])
def test_visibility(team, expected) -> None:
    assert visibility(team) == expected


def test_public_team_is_its_members_and_the_workspace_never_the_org() -> None:
    team = {"id": "t1", "visibility": "public"}
    assert _groups(team_permissions(team, {"t1": team})) == ["t1", WORKSPACE_MEMBERS_GROUP_ID]


def test_private_team_is_its_members_only() -> None:
    team = {"id": "t1", "visibility": "private"}
    assert _groups(team_permissions(team, {"t1": team})) == ["t1"]


def test_restricted_team_adds_parents_up_to_the_private_boundary() -> None:
    top = {"id": "top", "visibility": "public"}
    boundary = {"id": "b", "visibility": "private", "parent": {"id": "top"}}
    middle = {"id": "m", "visibility": "restricted", "parent": {"id": "b"}}
    leaf = {"id": "l", "visibility": "restricted", "parent": {"id": "m"}}
    teams = {t["id"]: t for t in (top, boundary, middle, leaf)}
    assert boundary_ancestors(leaf, teams) == ["m", "b"]
    assert _groups(team_permissions(leaf, teams)) == ["l", "m", "b"]


def test_ancestor_chain_with_an_unknown_parent_stops_there() -> None:
    leaf = {"id": "l", "visibility": "restricted", "parent": {"id": "missing"}}
    assert boundary_ancestors(leaf, {"l": leaf}) == ["missing"]


def test_a_cycle_does_not_loop_forever() -> None:
    a = {"id": "a", "visibility": "restricted", "parent": {"id": "b"}}
    b = {"id": "b", "visibility": "restricted", "parent": {"id": "a"}}
    assert boundary_ancestors(a, {"a": a, "b": b}) == ["b", "a"]
