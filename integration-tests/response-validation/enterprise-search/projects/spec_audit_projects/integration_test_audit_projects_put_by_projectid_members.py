"""Strict OpenAPI audit of PUT /api/v1/projects/:projectId/members."""

from __future__ import annotations

import uuid
from typing import Any, Iterator

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from projects_audit_support import (
    MALFORMED_PROJECT_ID,
    MEMBERS_TEMPLATE,
    MISSING_PROJECT_ID,
    UNKNOWN_USER_ID,
    SeedProject,
    add_member,
    request_as,
    validation_fields,
)
from strict_openapi import (
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = MEMBERS_TEMPLATE
MEMBER_FIELDS = {"principalType", "principalId", "role", "addedBy", "addedAt"}


@pytest.fixture
def team_id(teams_client: TeamsClient) -> Iterator[str]:
    resp = teams_client.create_team(f"spec-audit projects {uuid.uuid4().hex[:8]}")
    assert resp.status_code in (200, 201), resp.text[:500]
    body = resp.json()
    team = body.get("data") or body.get("team") or body
    new_id = team.get("_key") or team.get("id") or team.get("_id")
    assert isinstance(new_id, str) and new_id, body
    try:
        yield new_id
    finally:
        teams_client.delete_team(new_id)


def test_owner_adds_then_updates_a_member(
    projects_client: ProjectsClient,
    pipeshub_client: PipeshubClient,
    second_user: SecondUser,
    seed_project: SeedProject,
) -> None:
    project_id = seed_project()["_id"]

    added = projects_client.upsert_members(
        project_id, [{"principalId": second_user.user_id, "role": "viewer"}]
    )
    assert added.status_code == 200, added.text[:500]
    assert_strict_openapi_exchange(added, ROUTE)
    [member] = added.json()["members"]
    assert set(member) == MEMBER_FIELDS
    assert member["principalId"] == second_user.user_id
    assert member["principalType"] == "user"
    assert member["role"] == "viewer"
    assert member["addedBy"] == pipeshub_client.acting_user_id

    updated = projects_client.upsert_members(
        project_id,
        [{"principalId": second_user.user_id, "principalType": "user", "role": "editor"}],
    )
    assert updated.status_code == 200, updated.text[:500]
    assert_strict_openapi_exchange(updated, ROUTE)
    [member_after] = updated.json()["members"]
    assert member_after == {**member, "role": "editor"}


def test_owner_listed_as_a_member_is_skipped(
    projects_client: ProjectsClient, pipeshub_client: PipeshubClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.upsert_members(
        project_id, [{"principalId": pipeshub_client.acting_user_id, "role": "editor"}]
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"members": []}


def test_unknown_fields_are_dropped(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    with outside_request_contract("unknown body fields are stripped by the validator, not refused"):
        resp = projects_client.put(
            f"/{project_id}/members",
            json={
                "members": [{"principalId": second_user.user_id, "role": "viewer", "addedBy": "x"}],
                "replace": True,
            },
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["members"][0]["addedBy"] != "x"


def test_unknown_user_is_a_bad_request(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.upsert_members(project_id, [{"principalId": UNKNOWN_USER_ID, "role": "viewer"}])

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert error["message"] == f"User not found: {UNKNOWN_USER_ID}"


def test_unknown_team_is_a_bad_request(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.upsert_members(
        project_id, [{"principalId": UNKNOWN_USER_ID, "principalType": "team", "role": "viewer"}]
    )

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == f"Team not found: {UNKNOWN_USER_ID}"


def test_a_real_team_cannot_be_added(
    projects_client: ProjectsClient, seed_project: SeedProject, team_id: str
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.upsert_members(
        project_id, [{"principalId": team_id, "principalType": "team", "role": "viewer"}]
    )

    # Team ids are not ObjectIds, and the validator accepts only ObjectIds.
    assert validation_fields(resp) == {"body.members.0.principalId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_user_is_reported_before_the_project_is_looked_up(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "editor")
    unknown = [{"principalId": UNKNOWN_USER_ID, "role": "viewer"}]

    missing = projects_client.upsert_members(MISSING_PROJECT_ID, unknown)
    as_editor = request_as(second_user, "PUT", f"/{project_id}/members", json={"members": unknown})

    for resp in (missing, as_editor):
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["error"]["message"] == f"User not found: {UNKNOWN_USER_ID}"


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_members_cannot_manage_members(
    projects_client: ProjectsClient,
    second_user: SecondUser,
    seed_project: SeedProject,
    role: str,
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, role)

    resp = request_as(
        second_user,
        "PUT",
        f"/{project_id}/members",
        json={"members": [{"principalId": second_user.user_id, "role": "editor"}]},
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN"


def test_outsider_and_missing_project_are_not_found(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    members = [{"principalId": second_user.user_id, "role": "viewer"}]

    outsider = request_as(second_user, "PUT", f"/{project_id}/members", json={"members": members})
    missing = projects_client.upsert_members(MISSING_PROJECT_ID, members)

    for resp in (outsider, missing):
        assert resp.status_code == 404, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def _member(**fields: Any) -> dict[str, Any]:
    return {"principalId": UNKNOWN_USER_ID, "role": "viewer", **fields}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.members", id="members-missing"),
        pytest.param({"members": []}, "body.members", id="members-empty"),
        pytest.param({"members": [_member()] * 51}, "body.members", id="members-too-many"),
        pytest.param({"members": [{"role": "viewer"}]}, "body.members.0.principalId", id="principal-missing"),
        pytest.param({"members": [_member(principalId="abc")]}, "body.members.0.principalId", id="principal-malformed"),
        pytest.param({"members": [{"principalId": UNKNOWN_USER_ID}]}, "body.members.0.role", id="role-missing"),
        pytest.param({"members": [_member(role="owner")]}, "body.members.0.role", id="role-owner"),
        pytest.param({"members": [_member(principalType="group")]}, "body.members.0.principalType", id="principal-type-unknown"),
    ],
)
def test_upsert_rejects_invalid_body(
    projects_client: ProjectsClient, seed_project: SeedProject, body: dict[str, Any], field: str
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.put(f"/{project_id}/members", json=body)

    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upsert_malformed_project_id_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.upsert_members(MALFORMED_PROJECT_ID, [_member()])

    assert validation_fields(resp) == {"params.projectId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upsert_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.put(f"/{MISSING_PROJECT_ID}/members", auth=False, json={"members": [_member()]})

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

