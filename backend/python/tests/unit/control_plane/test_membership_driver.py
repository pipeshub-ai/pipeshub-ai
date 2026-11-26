"""CPD11-55 (Python half): the team membership Node's MembershipPort reads.

``GET /api/v1/control-plane/drivers/directory/users/{userId}/teams`` answers the user's teams in the
token's org with their role; an unknown user is in none; an unreadable graph is 503, never an empty list.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException


class Graph:
    def __init__(self, teams: dict[tuple[str, str], list[dict[str, Any]]], *, failing: bool = False) -> None:
        self.teams = teams
        self.failing = failing
        self.asked: list[tuple[str, str]] = []

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> dict[str, Any] | None:
        assert raise_on_error, "a failed lookup must not read as an unknown user"
        if self.failing:
            raise ConnectionError("graph down")
        return {"_key": f"key-{user_id}"} if user_id != "nobody" else None

    async def get_user_team_roles(self, user_key: str, org_id: str) -> list[dict[str, Any]]:
        self.asked.append((user_key, org_id))
        return self.teams.get((user_key, org_id), [])


async def _route(user_id: str, claims: dict[str, Any], graph: Graph) -> dict[str, Any]:
    import app.edition_config  # noqa: F401 - binds the routes in the order their imports need
    from app.api.routes.control_plane_drivers import user_teams

    return await user_teams(user_id, claims, graph)  # type: ignore[arg-type]


async def test_cpd11_55_teams_of_the_token_org_with_their_role() -> None:
    graph = Graph({("key-u3", "org-a"): [{"teamId": "t1", "role": "OWNER"}, {"teamId": "t2", "role": "MEMBER"}]})
    answered = await _route("u3", {"orgId": "org-a"}, graph)
    assert answered == {"teams": [{"teamId": "t1", "role": "OWNER"}, {"teamId": "t2", "role": "MEMBER"}]}
    assert graph.asked == [("key-u3", "org-a")]
    assert await _route("u3", {"orgId": "org-b"}, graph) == {"teams": []}


async def test_cpd11_55_an_unknown_user_is_in_no_team() -> None:
    assert await _route("nobody", {"orgId": "org-a"}, Graph({})) == {"teams": []}


async def test_cpd11_55_an_unreadable_graph_is_503() -> None:
    with pytest.raises(HTTPException) as refused:
        await _route("u3", {"orgId": "org-a"}, Graph({}, failing=True))
    assert refused.value.status_code == 503


async def test_cpd11_55_only_the_directory_scope_passes() -> None:
    from unittest.mock import MagicMock

    from app.api.middlewares.auth import require_service_token
    from app.config.constants.service import TokenScopes

    check = require_service_token(TokenScopes.DIRECTORY_READ)
    request = MagicMock()
    request.state.user = {"token_type": "scoped", "orgId": "org-a", "scopes": ["capabilities:read"]}
    with pytest.raises(HTTPException):
        await check(request)
    request.state.user = {"token_type": "scoped", "orgId": "org-a", "scopes": ["directory:read"]}
    assert (await check(request))["orgId"] == "org-a"


class OwnerGraph:
    def __init__(self, teams: dict[str, dict[str, Any]], *, failing: bool = False) -> None:
        self.teams = teams
        self.failing = failing

    async def get_team_permissions_and_owner_count(self, team_id: str, user_ids: list[str]) -> dict[str, Any]:
        assert user_ids == []
        if self.failing:
            raise ConnectionError("graph down")
        found = self.teams.get(team_id)
        if found is None:
            return {"team": None, "permissions": {}, "owner_count": 0}
        return {"team": {"orgId": found["orgId"]}, "permissions": {}, "owner_count": found["owners"]}


async def _owners(team_id: str, claims: dict[str, Any], graph: OwnerGraph) -> dict[str, Any]:
    import app.edition_config  # noqa: F401 - binds the routes in the order their imports need
    from app.api.routes.control_plane_drivers import team_owners

    return await team_owners(team_id, claims, graph)  # type: ignore[arg-type]


async def test_ap08_team_owners_of_a_team_of_the_token_org() -> None:
    graph = OwnerGraph({"t1": {"orgId": "org-a", "owners": 2}})
    assert await _owners("t1", {"orgId": "org-a"}, graph) == {"owners": 2}


async def test_ap08_another_orgs_team_or_none_has_no_owners() -> None:
    graph = OwnerGraph({"t1": {"orgId": "org-a", "owners": 2}})
    assert await _owners("t1", {"orgId": "org-b"}, graph) == {"owners": 0}
    assert await _owners("missing", {"orgId": "org-a"}, graph) == {"owners": 0}


async def test_ap08_team_owners_unreadable_graph_is_503() -> None:
    with pytest.raises(HTTPException) as refused:
        await _owners("t1", {"orgId": "org-a"}, OwnerGraph({}, failing=True))
    assert refused.value.status_code == 503


class OwnerIdsGraph(OwnerGraph):
    """`get_team_users` pages a team's members the way both graph providers answer it (WP-54, AP-08)."""

    def __init__(
        self, teams: dict[str, dict[str, Any]], members: list[dict[str, Any]], *, page_fails: bool = False
    ) -> None:
        super().__init__(teams)
        self.members = members
        self.page_fails = page_fails
        self.pages: list[int] = []

    async def get_team_users(
        self, team_id: str, org_id: str, user_key: str, search: str | None = None, page: int = 1, limit: int = 100
    ) -> dict[str, Any] | None:
        self.pages.append(page)
        if self.page_fails:
            return None
        start = (page - 1) * limit
        return {"orgId": org_id, "members": self.members[start : start + limit], "memberCount": len(self.members)}


async def _owner_ids(team_id: str, claims: dict[str, Any], graph: OwnerGraph, *, ids: bool = True) -> dict[str, Any]:
    import app.edition_config  # noqa: F401 - binds the routes in the order their imports need
    from app.api.routes.control_plane_drivers import team_owners

    return await team_owners(team_id, claims, graph, ids=ids)  # type: ignore[arg-type]


async def test_wp54_ap08_team_owner_ids_across_every_page() -> None:
    members = [{"userId": f"u{n}", "isOwner": n % 50 == 0} for n in range(150)]
    graph = OwnerIdsGraph({"t1": {"orgId": "org-a", "owners": 3}}, members)
    assert await _owner_ids("t1", {"orgId": "org-a"}, graph) == {"owners": 3, "ownerIds": ["u0", "u50", "u100"]}
    assert graph.pages == [1, 2]


async def test_wp54_ap08_another_orgs_team_names_no_owner() -> None:
    graph = OwnerIdsGraph({"t1": {"orgId": "org-a", "owners": 1}}, [{"userId": "u1", "isOwner": True}])
    assert await _owner_ids("t1", {"orgId": "org-b"}, graph) == {"owners": 0, "ownerIds": []}
    assert graph.pages == []


async def test_wp54_ap08_owner_ids_unreadable_members_is_503_never_empty() -> None:
    graph = OwnerIdsGraph({"t1": {"orgId": "org-a", "owners": 1}}, [], page_fails=True)
    with pytest.raises(HTTPException) as refused:
        await _owner_ids("t1", {"orgId": "org-a"}, graph)
    assert refused.value.status_code == 503


async def test_wp54_ap08_the_count_alone_unless_ids_are_asked() -> None:
    graph = OwnerIdsGraph({"t1": {"orgId": "org-a", "owners": 1}}, [{"userId": "u1", "isOwner": True}])
    assert await _owner_ids("t1", {"orgId": "org-a"}, graph, ids=False) == {"owners": 1}
    assert graph.pages == []
