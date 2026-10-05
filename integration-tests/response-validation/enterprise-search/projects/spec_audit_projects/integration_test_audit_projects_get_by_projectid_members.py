"""Strict OpenAPI audit of GET /api/v1/projects/:projectId/members."""

from __future__ import annotations

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.second_user import SecondUser
from projects_audit_support import (
    MALFORMED_PROJECT_ID,
    MEMBERS_TEMPLATE,
    MISSING_PROJECT_ID,
    SeedProject,
    request_as,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit


def test_owner_and_added_member_list_members(
    projects_client: ProjectsClient,
    second_user: SecondUser,
    seed_project: SeedProject,
) -> None:
    project_id = seed_project()["_id"]

    empty = projects_client.list_members(project_id)
    assert empty.status_code == 200, empty.text[:500]
    assert_strict_openapi_response(empty, MEMBERS_TEMPLATE)
    # The owner is implicit and never stored as a member row.
    assert empty.json() == {"members": []}

    added = projects_client.upsert_members(
        project_id, [{"principalId": second_user.user_id, "role": "viewer"}]
    )
    assert added.ok, f"could not add the member: {added.status_code} {added.text[:300]}"

    resp = projects_client.list_members(project_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, MEMBERS_TEMPLATE)
    members = resp.json()["members"]
    assert len(members) == 1
    assert members[0]["principalId"] == second_user.user_id
    assert members[0]["principalType"] == "user"
    assert members[0]["role"] == "viewer"

    as_member = request_as(second_user, "GET", f"/{project_id}/members")
    assert as_member.status_code == 200, as_member.text[:500]
    assert_strict_openapi_response(as_member, MEMBERS_TEMPLATE)
    assert as_member.json()["members"] == members


def test_outsider_gets_not_found_until_project_is_org_visible(
    projects_client: ProjectsClient,
    second_user: SecondUser,
    seed_project: SeedProject,
) -> None:
    project_id = seed_project()["_id"]

    # 404 rather than 403, so a non-member cannot probe which project ids exist.
    hidden = request_as(second_user, "GET", f"/{project_id}/members")
    assert hidden.status_code == 404, hidden.text[:500]
    assert_strict_openapi_response(hidden, MEMBERS_TEMPLATE)

    shared = projects_client.update_project(project_id, visibility="org")
    assert shared.ok, f"could not share the project: {shared.status_code} {shared.text[:300]}"

    visible = request_as(second_user, "GET", f"/{project_id}/members")
    assert visible.status_code == 200, visible.text[:500]
    assert_strict_openapi_response(visible, MEMBERS_TEMPLATE)
    assert visible.json() == {"members": []}


@pytest.mark.parametrize(
    ("project_id", "expected_status"),
    [
        pytest.param(MISSING_PROJECT_ID, 404, id="missing-project"),
        pytest.param(MALFORMED_PROJECT_ID, 400, id="malformed-id"),
    ],
)
def test_list_members_rejects_bad_project_id(
    projects_client: ProjectsClient, project_id: str, expected_status: int
) -> None:
    resp = projects_client.list_members(project_id)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, MEMBERS_TEMPLATE)


def test_list_members_without_token_is_unauthorized(
    projects_client: ProjectsClient,
) -> None:
    resp = projects_client.get(f"/{MISSING_PROJECT_ID}/members", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, MEMBERS_TEMPLATE)
