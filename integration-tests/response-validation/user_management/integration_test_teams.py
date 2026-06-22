"""
Teams API – Response Validation Integration Tests
===================================================

Validates the six workspace Teams routes against ``pipeshub-openapi.yaml``.
Classes run in dependency order (``pytest-order``): create → list → get → update → users → delete.

  POST   /api/v1/teams                  — createTeam
  GET    /api/v1/teams/user/teams       — getUserTeams
  GET    /api/v1/teams/{teamId}         — getTeamById
  PUT    /api/v1/teams/{teamId}         — updateTeam
  GET    /api/v1/teams/{teamId}/users   — getTeamUsers
  DELETE /api/v1/teams/{teamId}         — deleteTeam

Requires:
  - PIPESHUB_BASE_URL in .env / .env.local
  - Valid OAuth credentials or test-user login (team:read + team:write)
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)

logger = logging.getLogger("teams-integration-test")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BASE_PATH = "/api/v1/teams"
_MALFORMED_TEAM_ID = "not-a-uuid"
_NONEXISTENT_TEAM_ID = "00000000-0000-4000-8000-000000000001"
_NONEXISTENT_MONGO_USER_ID = "ffffffffffffffffffffffff"
_GRAPH_USER_WAIT_TIMEOUT = 60.0
_GRAPH_USER_POLL_INTERVAL = 2.0


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _url(client: PipeshubClient, path: str = "") -> str:
    return f"{client.base_url}{_BASE_PATH}{path}"


def _request(
    client: PipeshubClient,
    method: str,
    path: str = "",
    *,
    json: object = None,
    params: dict[str, Any] | None = None,
    auth: bool = True,
) -> requests.Response:
    """Unified HTTP request helper for teams API."""
    headers = client._headers() if auth else {}
    return requests.request(
        method,
        _url(client, path),
        headers=headers,
        json=json,
        params=params,
        timeout=client.timeout_seconds,
    )


def _get(client: PipeshubClient, path: str = "", **kw: Any) -> requests.Response:
    return _request(client, "GET", path, **kw)


def _post(client: PipeshubClient, path: str = "", **kw: Any) -> requests.Response:
    return _request(client, "POST", path, **kw)


def _put(client: PipeshubClient, path: str = "", **kw: Any) -> requests.Response:
    return _request(client, "PUT", path, **kw)


def _delete_request(client: PipeshubClient, path: str = "", **kw: Any) -> requests.Response:
    return _request(client, "DELETE", path, **kw)


# ---------------------------------------------------------------------------
# User management helpers
# ---------------------------------------------------------------------------
def _list_graph_mongo_user_ids(
    client: PipeshubClient,
    *,
    min_count: int | None = None,
    active_only: bool = True,
) -> list[str]:
    """Return distinct Mongo userIds from org graph list (team APIs require graph nodes)."""
    url = f"{client.base_url}/api/v1/users/graph/list"
    seen: list[str] = []
    page = 1
    while True:
        resp = requests.get(
            url,
            headers=client._headers(),
            params={"page": page, "limit": 100},
            timeout=client.timeout_seconds,
        )
        if resp.status_code != 200:
            break
        body = resp.json()
        users = body.get("users") or []
        if not users:
            break
        for u in users:
            if not isinstance(u, dict):
                continue
            if active_only and u.get("isActive") is False:
                continue
            uid = str(u.get("userId") or "")
            if uid and uid not in seen:
                seen.append(uid)
                if min_count is not None and len(seen) >= min_count:
                    return seen[:min_count]
        if not body.get("pagination", {}).get("hasNext"):
            break
        page += 1
    return seen


def _available_team_member_pool(client: PipeshubClient) -> list[str]:
    """Active graph users usable as team members (excludes implicit OWNER / auth)."""
    excluded = _excluded_team_member_ids(client)
    return [
        uid for uid in _list_graph_mongo_user_ids(client, active_only=True)
        if uid not in excluded
    ]


def _excluded_team_member_ids(client: PipeshubClient) -> set[str]:
    """User ids that must not be used as removable team members in tests.
    
    The authenticated user is always OWNER on created teams, so tests
    cannot remove them without triggering owner-removal validation.
    """
    excluded: set[str] = set()
    if auth_user_id := client.user_id:
        excluded.add(auth_user_id)
    # OAuth client-credentials tokens use a service UUID as userId;
    # the human account's Mongo id is on createdBy claim.
    if created_by := client._claims().get("createdBy"):
        excluded.add(str(created_by))
    return excluded


def _create_test_user(client: PipeshubClient) -> str:
    """Create a disposable test user, returns Mongo userId."""
    unique = uuid.uuid4().hex[:8]
    resp = requests.post(
        f"{client.base_url}/api/v1/users",
        headers=client._headers(),
        json={
            "fullName": f"RV Teams Test {unique}",
            "email": f"rv-teams-{unique}@test-pipeshub.com",
        },
        timeout=client.timeout_seconds,
    )
    assert resp.status_code == 201, f"Failed to create test user: {resp.status_code}: {resp.text}"
    body = resp.json()
    user_id = str(body.get("_id") or body.get("id") or "")
    assert user_id, f"createUser returned no id: {body}"
    return user_id


def _activate_test_user(client: PipeshubClient, mongo_user_id: str) -> None:
    """Mark user logged-in so graph sync activates the User node."""
    resp = requests.put(
        f"{client.base_url}/api/v1/users/{mongo_user_id}",
        headers=client._headers(),
        json={"hasLoggedIn": True},
        timeout=client.timeout_seconds,
    )
    assert resp.status_code == 200, (
        f"Failed to activate test user {mongo_user_id}: {resp.status_code}: {resp.text}"
    )


def _wait_for_user_in_graph(
    client: PipeshubClient,
    mongo_user_id: str,
    timeout: float = _GRAPH_USER_WAIT_TIMEOUT,
) -> None:
    """Poll until user appears in org graph."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if mongo_user_id in _list_graph_mongo_user_ids(client):
            return
        time.sleep(_GRAPH_USER_POLL_INTERVAL)
    pytest.fail(f"User {mongo_user_id!r} did not appear in org graph within {timeout}s")


def _create_test_user_synced_to_graph(client: PipeshubClient) -> str:
    """Create a disposable user and wait until they exist in the org graph."""
    user_id = _create_test_user(client)
    _activate_test_user(client, user_id)
    _wait_for_user_in_graph(client, user_id)
    return user_id


def _wait_for_user_removed_from_active_graph(
    client: PipeshubClient,
    mongo_user_id: str,
    *,
    timeout: float = _GRAPH_USER_WAIT_TIMEOUT,
) -> None:
    """Poll until user no longer appears in active org graph list (post-delete / Kafka sync)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if mongo_user_id not in _list_graph_mongo_user_ids(client, active_only=True):
            return
        time.sleep(_GRAPH_USER_POLL_INTERVAL)
    logger.warning(
        "User %s still active in org graph after delete within %ss "
        "(Kafka entity consumer may be down; Neo4j node may remain with isActive=true)",
        mongo_user_id,
        timeout,
    )


def _delete_test_users(client: PipeshubClient, user_ids: list[str]) -> None:
    """Soft-delete disposable users in Mongo and wait for active graph deactivation."""
    seen: set[str] = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        try:
            resp = requests.delete(
                f"{client.base_url}/api/v1/users/{user_id}",
                headers=client._headers(),
                timeout=client.timeout_seconds,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Delete test user %s returned %s: %s",
                    user_id,
                    resp.status_code,
                    resp.text,
                )
                continue
            _wait_for_user_removed_from_active_graph(client, user_id)
        except Exception:
            logger.warning(
                "Failed to delete disposable test user %s",
                user_id,
                exc_info=True,
            )


def _user_ids_for_team_tests(
    client: PipeshubClient,
    count: int,
) -> tuple[list[str], list[str]]:
    """
    Return ``count`` distinct Mongo userIds and disposable ids the test must delete.

    Reuses active org graph users first; creates new users only to fill the gap.
    """
    pool = list(_available_team_member_pool(client))
    disposable: list[str] = []
    while len(pool) < count:
        uid = _create_test_user_synced_to_graph(client)
        disposable.append(uid)
        pool.append(uid)
    return pool[:count], disposable


# ---------------------------------------------------------------------------
# Team CRUD helpers
# ---------------------------------------------------------------------------
def _unique_team_name() -> str:
    return f"rv-test-team-{uuid.uuid4().hex[:8]}"


def _team_id_from_response(body: dict[str, object]) -> str:
    """Extract team id from create or detail response."""
    if isinstance(data := body.get("data"), dict) and data.get("id"):
        return str(data["id"])
    if isinstance(team := body.get("team"), dict) and team.get("id"):
        return str(team["id"])
    if body.get("id"):
        return str(body["id"])
    raise AssertionError(f"Response missing team id: {body!r}")


def _create_team(
    client: PipeshubClient,
    name: str,
    **kwargs: object,
) -> dict[str, object]:
    """Create a team, assert 201, return response body."""
    resp = _post(client, json={"name": name, **kwargs})
    assert resp.status_code == 201, f"Failed to create team '{name}': {resp.status_code}: {resp.text}"
    return resp.json()


def _delete_team(client: PipeshubClient, team_id: str) -> None:
    """Delete team (best-effort, ignores errors)."""
    try:
        _delete_request(client, f"/{team_id}")
    except Exception:
        pass


def _team_member_roles_by_mongo_id(client: PipeshubClient, team_id: str) -> dict[str, str]:
    """Get team members and return {mongoUserId: role} mapping."""
    resp = _get(client, f"/{team_id}/users")
    assert resp.status_code == 200, f"Expected 200 loading team members, got {resp.status_code}: {resp.text}"
    body = resp.json()
    return {
        str(m["userId"]): str(m.get("role") or "")
        for m in body.get("team", {}).get("members", [])
        if isinstance(m, dict) and m.get("userId")
    }


# ---------------------------------------------------------------------------
# Resource management (context managers for cleanup)
# ---------------------------------------------------------------------------
@dataclass
class ResourceTracker:
    """Tracks teams and users created during a test for cleanup."""
    client: PipeshubClient
    team_ids: list[str] = field(default_factory=list)
    user_ids: list[str] = field(default_factory=list)

    def create_team(self, name: str | None = None, **kwargs: object) -> tuple[str, dict[str, object]]:
        """Create a team, track for cleanup, return (team_id, body)."""
        name = name or _unique_team_name()
        body = _create_team(self.client, name, **kwargs)
        team_id = _team_id_from_response(body)
        self.team_ids.append(team_id)
        return team_id, body

    def get_test_user(self) -> tuple[str, bool]:
        """Return one graph user for team membership; create only if pool is empty."""
        user_ids, disposable = _user_ids_for_team_tests(self.client, 1)
        user_id = user_ids[0]
        for uid in disposable:
            if uid not in self.user_ids:
                self.user_ids.append(uid)
        return user_id, user_id in disposable

    def get_test_users(self, count: int) -> list[str]:
        """Return ``count`` graph users; create only the minimum needed."""
        user_ids, disposable = _user_ids_for_team_tests(self.client, count)
        for uid in disposable:
            if uid not in self.user_ids:
                self.user_ids.append(uid)
        return user_ids

    def cleanup(self) -> None:
        """Delete all tracked teams and users."""
        for team_id in self.team_ids:
            _delete_team(self.client, team_id)
        _delete_test_users(self.client, self.user_ids)


@contextmanager
def managed_resources(client: PipeshubClient) -> Generator[ResourceTracker, None, None]:
    """Context manager for automatic test resource cleanup."""
    resources = ResourceTracker(client)
    try:
        yield resources
    finally:
        resources.cleanup()


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------
def _creator_mongo_user_id(team: dict[str, object]) -> str | None:
    """Extract creator Mongo userId from team object."""
    if isinstance(created_by := team.get("createdByUser"), dict) and created_by.get("userId"):
        return str(created_by["userId"])
    if isinstance(members := team.get("members"), list):
        for m in members:
            if isinstance(m, dict) and m.get("role") == "OWNER" and m.get("userId"):
                return str(m["userId"])
    return None


def _team_created_at_ms(team: dict[str, object]) -> int:
    """Extract createdAtTimestamp from team object."""
    ts = team.get("createdAtTimestamp")
    assert isinstance(ts, (int, float)), f"Team {team.get('id')!r} missing createdAtTimestamp"
    return int(ts)


def _team_from_detail_response(body: dict[str, object]) -> dict[str, object]:
    """Extract team object from detail response."""
    if isinstance(team := body.get("team"), dict):
        return team
    return body


def _assert_create_team_success(
    body: dict[str, object],
    *,
    name: str,
    description: str | None = None,
    extra_member: tuple[str, str] | None = None,
) -> None:
    """Assert successful team creation response structure."""
    assert body.get("status") == "success"
    assert body.get("message") == "Team created successfully"
    data = body.get("data")
    assert isinstance(data, dict), f"Expected data object, got {body!r}"
    assert data.get("name") == name
    assert data.get("id"), "createTeam data must include team id"
    if description is not None:
        assert data.get("description") == description
    members = data.get("members")
    assert isinstance(members, list), "createTeam data.members must be a list"
    assert any(m.get("role") == "OWNER" for m in members if isinstance(m, dict)), (
        "Creator should appear as OWNER on the team"
    )
    if extra_member:
        mongo_id, role = extra_member
        assert any(
            isinstance(m, dict) and m.get("userId") == mongo_id and m.get("role") == role
            for m in members
        ), f"Expected member {mongo_id!r} with role {role!r} in {members!r}"


def _assert_validation_error(body: dict[str, object], operation: str) -> None:
    """Assert 400 validation error response."""
    assert_response_matches_openapi_operation(body, operation, status_code="400")
    assert body["error"]["code"] == "VALIDATION_ERROR"


def _assert_unauthorized(body: dict[str, object], operation: str) -> None:
    """Assert 401 unauthorized response."""
    assert_response_matches_openapi_operation(body, operation, status_code="401")
    assert body["error"]["code"] == "HTTP_UNAUTHORIZED"
    assert body["error"]["message"] == "No token provided"


def _assert_not_found(body: dict[str, object], operation: str) -> None:
    """Assert 404 not found response."""
    assert_response_matches_openapi_operation(body, operation, status_code="404")
    assert body["error"]["code"] == "HTTP_NOT_FOUND"


def _assert_backend_bad_request(body: dict[str, object], operation: str, msg_part: str) -> None:
    """Assert 400 HTTP_BAD_REQUEST from backend (not Zod validation)."""
    assert_response_matches_openapi_operation(body, operation, status_code="400")
    assert body["error"]["code"] == "HTTP_BAD_REQUEST"
    assert msg_part.lower() in str(body["error"]["message"]).lower()


def _assert_teams_match_search(teams: list[dict[str, object]], term: str) -> None:
    """Assert all teams match search term in name or description."""
    term_lower = term.lower()
    for team in teams:
        name = str(team.get("name") or "").lower()
        desc = str(team.get("description") or "").lower()
        assert term_lower in name or term_lower in desc, (
            f"search {term!r} must match team name or description (got name={team.get('name')!r})"
        )


def _assert_teams_match_creator(teams: list[dict[str, object]], creator_id: str) -> None:
    """Assert all teams were created by the given user."""
    for team in teams:
        actual = _creator_mongo_user_id(team)
        assert actual == creator_id, (
            f"created_by filter: expected {creator_id!r}, got {actual!r} for team {team.get('id')!r}"
        )


def _assert_teams_in_time_range(
    teams: list[dict[str, object]],
    *,
    after_ms: int | None = None,
    before_ms: int | None = None,
) -> None:
    """Assert all teams have createdAtTimestamp within the given range."""
    for team in teams:
        ts = _team_created_at_ms(team)
        if after_ms is not None:
            assert ts >= after_ms, f"Team {team.get('id')!r} ts={ts} < after_ms={after_ms}"
        if before_ms is not None:
            assert ts <= before_ms, f"Team {team.get('id')!r} ts={ts} > before_ms={before_ms}"


def _get_user_teams_body(client: PipeshubClient, params: dict[str, Any]) -> dict[str, object]:
    """Get user teams with params, assert 200 and schema."""
    resp = _get(client, "/user/teams", params=params)
    assert resp.status_code == 200, f"getUserTeams failed {resp.status_code}: {resp.text}"
    body = resp.json()
    assert_response_matches_openapi_operation(body, "getUserTeams")
    return body


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TeamsTestBase:
    """Base class with common setup for teams integration tests."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client


# ====================================================================
# POST /api/v1/teams
# ====================================================================
@pytest.mark.order(1)
class TestCreateTeam(TeamsTestBase):
    """POST /api/v1/teams — create team."""

    def test_create_team_response_schema(self) -> None:
        """Positive: create team with name only, with description, and with members."""
        with managed_resources(self.client) as res:
            # Name only
            name = _unique_team_name()
            body = _create_team(self.client, name)
            res.team_ids.append(_team_id_from_response(body))
            assert_response_matches_openapi_operation(body, "createTeam", status_code="201")
            _assert_create_team_success(body, name=name)

            # With description
            name_desc = _unique_team_name()
            body_desc = _create_team(self.client, name_desc, description="RV teams IT description-only")
            res.team_ids.append(_team_id_from_response(body_desc))
            assert_response_matches_openapi_operation(body_desc, "createTeam", status_code="201")
            _assert_create_team_success(body_desc, name=name_desc, description="RV teams IT description-only")

            # With member
            member_id, is_disposable = res.get_test_user()
            name_roles = _unique_team_name()
            body_roles = _create_team(
                self.client,
                name_roles,
                description="Integration test team",
                userRoles=[{"userId": member_id, "role": "READER"}],
            )
            res.team_ids.append(_team_id_from_response(body_roles))
            assert_response_matches_openapi_operation(body_roles, "createTeam", status_code="201")
            _assert_create_team_success(
                body_roles,
                name=name_roles,
                description="Integration test team",
                extra_member=(member_id, "READER"),
            )

    def test_create_team_negative_tests(self) -> None:
        """Negative: 401 no auth, 400 validation errors, 400 backend errors."""
        # 401 - No authentication
        resp = _post(self.client, json={"name": "rv-test-no-auth"}, auth=False)
        assert resp.status_code == 401
        _assert_unauthorized(resp.json(), "createTeam")

        # 400 - Validation errors (Zod)
        validation_cases = [
            ("missing name", {}),
            ("empty name", {"name": ""}),
            ("name too long", {"name": "x" * 101}),
            ("description too long", {"name": _unique_team_name(), "description": "x" * 501}),
            ("invalid userRoles userId", {"name": _unique_team_name(), "userRoles": [{"userId": "bad-id", "role": "READER"}]}),
            ("invalid userRoles role", {"name": _unique_team_name(), "userRoles": [{"userId": _NONEXISTENT_MONGO_USER_ID, "role": "ADMIN"}]}),
        ]
        for label, payload in validation_cases:
            resp = _post(self.client, json=payload)
            assert resp.status_code == 400, f"[{label}] Expected 400, got {resp.status_code}: {resp.text}"
            _assert_validation_error(resp.json(), "createTeam")

        # 400 - Backend errors (user not in graph)
        resp = _post(self.client, json={
            "name": _unique_team_name(),
            "userRoles": [{"userId": _NONEXISTENT_MONGO_USER_ID, "role": "READER"}],
        })
        assert resp.status_code == 400
        _assert_backend_bad_request(resp.json(), "createTeam", "not found")


# ====================================================================
# GET /api/v1/teams/user/teams
# ====================================================================
@pytest.mark.order(2)
class TestGetUserTeams(TeamsTestBase):
    """GET /api/v1/teams/user/teams — primary workspace list API."""

    def test_get_user_teams_response_schema(self) -> None:
        """Positive: list teams, pagination, and search."""
        # Basic list
        resp = _get(self.client, "/user/teams")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "teams" in body and "pagination" in body
        assert_response_matches_openapi_operation(body, "getUserTeams")

        # Pagination
        resp = _get(self.client, "/user/teams", params={"page": 1, "limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserTeams")
        assert body["pagination"]["limit"] == 5
        assert len(body["teams"]) <= 5

        # Search
        with managed_resources(self.client) as res:
            team_id, _ = res.create_team()
            detail = _get(self.client, f"/{team_id}").json()
            team_name = _team_from_detail_response(detail).get("name", "")
            search_term = team_name.rsplit("-", 1)[-1]
            search_body = _get_user_teams_body(self.client, {"search": search_term})
            assert any(t.get("id") == team_id for t in search_body["teams"]), (
                f"Expected team {team_id} in search results for {search_term!r}"
            )
            _assert_teams_match_search(search_body["teams"], search_term)

            # Empty search is ignored (same as no filter), not 400
            for empty_search in ("", "   "):
                resp = _get(self.client, "/user/teams", params={"search": empty_search})
                assert resp.status_code == 200, (
                    f"Expected 200 for empty search {empty_search!r}, "
                    f"got {resp.status_code}: {resp.text}"
                )
                assert_response_matches_openapi_operation(resp.json(), "getUserTeams")

    def test_get_user_teams_filter_params(self) -> None:
        """Positive: created_by, created_after, created_before, and combined filters."""
        before_ms = int(time.time() * 1000) - 60_000

        with managed_resources(self.client) as res:
            team_id, _ = res.create_team()
            detail = _get(self.client, f"/{team_id}").json()
            team = _team_from_detail_response(detail)
            creator_id = _creator_mongo_user_id(team)
            assert creator_id, "Team detail must include creator Mongo userId"
            created_at = _team_created_at_ms(team)
            created_before_ms = created_at + 60_000
            search_term = str(team.get("name", "")).rsplit("-", 1)[-1]

            # Filter by creator
            by_creator = _get_user_teams_body(self.client, {"created_by": creator_id})
            assert any(t.get("id") == team_id for t in by_creator["teams"])
            _assert_teams_match_creator(by_creator["teams"], creator_id)

            # Filter by created_after
            by_after = _get_user_teams_body(self.client, {"created_after": before_ms})
            assert any(t.get("id") == team_id for t in by_after["teams"])
            _assert_teams_in_time_range(by_after["teams"], after_ms=before_ms)

            # Filter by created_before
            by_before = _get_user_teams_body(self.client, {"created_before": created_before_ms})
            assert any(t.get("id") == team_id for t in by_before["teams"])
            _assert_teams_in_time_range(by_before["teams"], before_ms=created_before_ms)

            # Combined filters
            combined = _get_user_teams_body(self.client, {
                "search": search_term,
                "created_by": creator_id,
                "created_after": before_ms,
                "created_before": created_before_ms,
            })
            assert any(t.get("id") == team_id for t in combined["teams"])
            _assert_teams_match_search(combined["teams"], search_term)
            _assert_teams_match_creator(combined["teams"], creator_id)
            _assert_teams_in_time_range(combined["teams"], after_ms=before_ms, before_ms=created_before_ms)

    def test_get_user_teams_negative_tests(self) -> None:
        """Negative: 401 no auth, 400 invalid created_by."""
        # 401 - No authentication
        resp = _get(self.client, "/user/teams", auth=False)
        assert resp.status_code == 401
        _assert_unauthorized(resp.json(), "getUserTeams")

        # 400 - Invalid created_by format
        resp = _get(self.client, "/user/teams", params={"created_by": "not-a-mongo-id"})
        assert resp.status_code == 400
        _assert_validation_error(resp.json(), "getUserTeams")


# ====================================================================
# GET /api/v1/teams/{teamId}
# ====================================================================
@pytest.mark.order(3)
class TestGetTeamById(TeamsTestBase):
    """GET /api/v1/teams/{teamId}"""

    def test_get_team_by_id_response_schema(self) -> None:
        """Positive: get team by id returns correct schema and data."""
        with managed_resources(self.client) as res:
            name = _unique_team_name()
            team_id, _ = res.create_team(name)
            resp = _get(self.client, f"/{team_id}")
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()
            assert_response_matches_openapi_operation(body, "getTeamById")
            assert body["team"]["id"] == team_id
            assert body["team"]["name"] == name

    def test_get_team_by_id_negative_tests(self) -> None:
        """Negative: 401 no auth, 400 malformed id, 404 not found."""
        # 401 - No authentication
        resp = _get(self.client, f"/{_NONEXISTENT_TEAM_ID}", auth=False)
        assert resp.status_code == 401
        _assert_unauthorized(resp.json(), "getTeamById")

        # 400 - Malformed team id
        resp = _get(self.client, f"/{_MALFORMED_TEAM_ID}")
        assert resp.status_code == 400
        _assert_validation_error(resp.json(), "getTeamById")

        # 404 - Team not found
        resp = _get(self.client, f"/{_NONEXISTENT_TEAM_ID}")
        assert resp.status_code == 404
        _assert_not_found(resp.json(), "getTeamById")


# ====================================================================
# PUT /api/v1/teams/{teamId}
# ====================================================================
@pytest.mark.order(4)
class TestUpdateTeam(TeamsTestBase):
    """PUT /api/v1/teams/{teamId} — metadata and member management."""

    def test_update_team_integrated_flow_response_schema(self) -> None:
        """Positive: rename, add member, update role, remove member (sequential)."""
        with managed_resources(self.client) as res:
            name = _unique_team_name()
            team_id, _ = res.create_team(name)

            # Update name and description
            resp = _put(self.client, f"/{team_id}", json={
                "name": f"{name}-renamed",
                "description": "Updated by integration test",
            })
            assert resp.status_code == 200
            body = resp.json()
            assert_response_matches_openapi_operation(body, "updateTeam")
            assert body["team"]["name"] == f"{name}-renamed"

            # Add member
            extra_user_id, _ = res.get_test_user()
            if extra_user_id:
                resp = _put(self.client, f"/{team_id}", json={
                    "addUserRoles": [{"userId": extra_user_id, "role": "READER"}],
                })
                assert resp.status_code == 200
                assert_response_matches_openapi_operation(resp.json(), "updateTeam")

                # Update member role
                resp = _put(self.client, f"/{team_id}", json={
                    "updateUserRoles": [{"userId": extra_user_id, "role": "WRITER"}],
                })
                assert resp.status_code == 200
                assert_response_matches_openapi_operation(resp.json(), "updateTeam")

                # Remove member
                resp = _put(self.client, f"/{team_id}", json={"removeUserIds": [extra_user_id]})
                assert resp.status_code == 200
                assert_response_matches_openapi_operation(resp.json(), "updateTeam")

    def test_update_team_combined_member_mutations_response_schema(self) -> None:
        """Positive: single PUT with addUserRoles, updateUserRoles, and removeUserIds."""
        with managed_resources(self.client) as res:
            users = res.get_test_users(3)
            user_a, user_b, user_c = users[0], users[1], users[2]
            name = _unique_team_name()
            team_id, _ = res.create_team(name, userRoles=[
                {"userId": user_a, "role": "READER"},
                {"userId": user_b, "role": "READER"},
            ])
            combined_name = f"{name}-combined"

            resp = _put(self.client, f"/{team_id}", json={
                "name": combined_name,
                "description": "Combined add, update, and remove",
                "addUserRoles": [{"userId": user_c, "role": "READER"}],
                "updateUserRoles": [{"userId": user_a, "role": "WRITER"}],
                "removeUserIds": [user_b],
            })
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()
            assert_response_matches_openapi_operation(body, "updateTeam")
            assert body["team"]["name"] == combined_name

            # Verify member state
            roles = _team_member_roles_by_mongo_id(self.client, team_id)
            assert roles.get(user_a) == "WRITER", f"Expected {user_a!r} promoted to WRITER"
            assert roles.get(user_c) == "READER", f"Expected {user_c!r} added as READER"
            assert user_b not in roles, f"Expected {user_b!r} removed from team"

    def test_update_team_negative_tests(self) -> None:
        """Negative: 401 no auth, 400 malformed id, 400 empty name, 403/404 not found."""
        with managed_resources(self.client) as res:
            team_id, _ = res.create_team()

            # 401 - No authentication
            resp = _put(self.client, f"/{team_id}", json={"name": "no-auth"}, auth=False)
            assert resp.status_code == 401
            _assert_unauthorized(resp.json(), "updateTeam")

            # 400 - Malformed team id
            resp = _put(self.client, f"/{_MALFORMED_TEAM_ID}", json={"name": "x"})
            assert resp.status_code == 400
            _assert_validation_error(resp.json(), "updateTeam")

            # 400 - Empty name
            resp = _put(self.client, f"/{team_id}", json={"name": ""})
            assert resp.status_code == 400
            _assert_validation_error(resp.json(), "updateTeam")

            # 403/404 - Team not found
            resp = _put(self.client, f"/{_NONEXISTENT_TEAM_ID}", json={"name": "nope"})
            assert resp.status_code in (403, 404), f"Expected 403 or 404, got {resp.status_code}"
            assert_response_matches_openapi_operation(resp.json(), "updateTeam", status_code=str(resp.status_code))


# ====================================================================
# GET /api/v1/teams/{teamId}/users
# ====================================================================
@pytest.mark.order(5)
class TestGetTeamUsers(TeamsTestBase):
    """GET /api/v1/teams/{teamId}/users"""

    def test_get_team_users_response_schema(self) -> None:
        """Positive: list team members with pagination."""
        with managed_resources(self.client) as res:
            team_id, _ = res.create_team()

            # Basic list
            resp = _get(self.client, f"/{team_id}/users")
            assert resp.status_code == 200
            body = resp.json()
            assert_response_matches_openapi_operation(body, "getTeamUsers")
            assert isinstance(body["team"]["members"], list)
            assert "totalCount" in body["pagination"]

            # With pagination params
            resp = _get(self.client, f"/{team_id}/users", params={"page": 1, "limit": 10})
            assert resp.status_code == 200
            assert_response_matches_openapi_operation(resp.json(), "getTeamUsers")

    def test_get_team_users_negative_tests(self) -> None:
        """Negative: 401 no auth, 400 malformed id, 404 not found."""
        # 401 - No authentication
        resp = _get(self.client, f"/{_NONEXISTENT_TEAM_ID}/users", auth=False)
        assert resp.status_code == 401
        _assert_unauthorized(resp.json(), "getTeamUsers")

        # 400 - Malformed team id
        resp = _get(self.client, f"/{_MALFORMED_TEAM_ID}/users")
        assert resp.status_code == 400
        _assert_validation_error(resp.json(), "getTeamUsers")

        # 404 - Team not found
        resp = _get(self.client, f"/{_NONEXISTENT_TEAM_ID}/users")
        assert resp.status_code == 404
        _assert_not_found(resp.json(), "getTeamUsers")


# ====================================================================
# DELETE /api/v1/teams/{teamId}
# ====================================================================
@pytest.mark.order(6)
class TestDeleteTeam(TeamsTestBase):
    """DELETE /api/v1/teams/{teamId}"""

    def test_delete_team_response_schema(self) -> None:
        """Positive: delete team and verify it's gone."""
        # Create without context manager since we're testing delete
        name = _unique_team_name()
        created = _create_team(self.client, name)
        team_id = _team_id_from_response(created)

        resp = _delete_request(self.client, f"/{team_id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteTeam")

        # Verify team is gone
        resp = _get(self.client, f"/{team_id}")
        assert resp.status_code == 404

    def test_delete_team_negative_tests(self) -> None:
        """Negative: 401 no auth, 400 malformed id, 403/404 not found."""
        # 401 - No authentication
        resp = _delete_request(self.client, f"/{_NONEXISTENT_TEAM_ID}", auth=False)
        assert resp.status_code == 401
        _assert_unauthorized(resp.json(), "deleteTeam")

        # 400 - Malformed team id
        resp = _delete_request(self.client, f"/{_MALFORMED_TEAM_ID}")
        assert resp.status_code == 400
        _assert_validation_error(resp.json(), "deleteTeam")

        # 403/404 - Team not found
        resp = _delete_request(self.client, f"/{_NONEXISTENT_TEAM_ID}")
        assert resp.status_code in (403, 404), f"Expected 403 or 404, got {resp.status_code}"
        assert_response_matches_openapi_operation(resp.json(), "deleteTeam", status_code=str(resp.status_code))
