"""Strict OpenAPI audit of DELETE /api/v1/projects/:projectId/members/:memberUserId."""

from __future__ import annotations

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from projects_audit_support import (
    MALFORMED_PROJECT_ID,
    MEMBER_TEMPLATE,
    MISSING_PROJECT_ID,
    UNKNOWN_USER_ID,
    SeedProject,
    add_member,
    request_as,
    validation_fields,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = MEMBER_TEMPLATE


def test_owner_removes_a_member(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "viewer")

    resp = projects_client.remove_member(project_id, second_user.user_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"members": []}
    assert request_as(second_user, "GET", f"/{project_id}").status_code == 404


def test_removing_a_member_of_a_project_with_files_revokes_and_succeeds(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "editor")
    ensured = projects_client.ensure_knowledge_base(project_id)
    assert ensured.status_code == 200, ensured.text[:500]

    resp = projects_client.remove_member(project_id, second_user.user_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"members": []}


@pytest.mark.parametrize(
    ("member_id", "query"),
    [
        pytest.param(UNKNOWN_USER_ID, None, id="not-a-member"),
        pytest.param(None, {"principalType": "team"}, id="member-is-a-user-not-a-team"),
        pytest.param(None, {"principalType": "user"}, id="explicit-user"),
    ],
)
def test_removing_what_is_not_there_changes_nothing(
    projects_client: ProjectsClient,
    second_user: SecondUser,
    seed_project: SeedProject,
    member_id: str | None,
    query: dict[str, str] | None,
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "viewer")
    target = member_id or second_user.user_id

    resp = projects_client.delete(f"/{project_id}/members/{target}", params=query)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    members = resp.json()["members"]
    removed = query == {"principalType": "user"}
    assert [m["principalId"] for m in members] == ([] if removed else [second_user.user_id])


def test_owner_id_is_not_a_member_and_is_ignored(
    projects_client: ProjectsClient, pipeshub_client: PipeshubClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.remove_member(project_id, pipeshub_client.acting_user_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"members": []}
    assert projects_client.get_project(project_id).status_code == 200


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_members_cannot_remove_members_even_themselves(
    projects_client: ProjectsClient,
    second_user: SecondUser,
    seed_project: SeedProject,
    role: str,
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, role)

    resp = request_as(second_user, "DELETE", f"/{project_id}/members/{second_user.user_id}")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_FORBIDDEN"
    assert error["message"] == "Only the project owner can manage members"


def test_outsider_and_missing_project_are_not_found(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    outsider = request_as(second_user, "DELETE", f"/{project_id}/members/{second_user.user_id}")
    missing = projects_client.remove_member(MISSING_PROJECT_ID, second_user.user_id)

    for resp in (outsider, missing):
        assert resp.status_code == 404, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["error"]["code"] == "HTTP_NOT_FOUND"


@pytest.mark.parametrize(
    ("path", "query", "field"),
    [
        pytest.param(f"/{MALFORMED_PROJECT_ID}/members/{UNKNOWN_USER_ID}", None, "params.projectId", id="project-id"),
        pytest.param(f"/{MISSING_PROJECT_ID}/members/abc", None, "params.memberUserId", id="member-id"),
        pytest.param(
            f"/{MISSING_PROJECT_ID}/members/{UNKNOWN_USER_ID}",
            {"principalType": "group"},
            "query.principalType",
            id="principal-type",
        ),
    ],
)
def test_invalid_request_is_rejected(
    projects_client: ProjectsClient, path: str, query: dict[str, str] | None, field: str
) -> None:
    resp = projects_client.delete(path, params=query)

    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_remove_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.delete(f"/{MISSING_PROJECT_ID}/members/{UNKNOWN_USER_ID}", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
