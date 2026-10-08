"""Strict OpenAPI audit of GET /api/v1/projects/:projectId."""

from __future__ import annotations

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.second_user import SecondUser
from projects_audit_support import (
    MALFORMED_PROJECT_ID,
    MISSING_PROJECT_ID,
    PROJECT_FIELDS,
    PROJECT_TEMPLATE,
    SeedProject,
    add_member,
    request_as,
    validation_fields,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = PROJECT_TEMPLATE


def test_owner_gets_project(projects_client: ProjectsClient, seed_project: SeedProject) -> None:
    project = seed_project(description="audit", knowledgeScope={"apps": ["a"]})

    resp = projects_client.get_project(project["_id"])

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()["project"]
    assert set(body) <= PROJECT_FIELDS
    assert body == {**project, "role": "owner"}


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_member_gets_project_with_their_role(
    projects_client: ProjectsClient,
    second_user: SecondUser,
    seed_project: SeedProject,
    role: str,
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, role)

    resp = request_as(second_user, "GET", f"/{project_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["project"]["role"] == role


def test_org_visible_project_is_readable_by_everyone_as_viewer(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    hidden = request_as(second_user, "GET", f"/{project_id}")
    assert hidden.status_code == 404, hidden.text[:500]
    assert_strict_openapi_exchange(hidden, ROUTE)

    assert projects_client.update_project(project_id, visibility="org").status_code == 200
    visible = request_as(second_user, "GET", f"/{project_id}")
    assert visible.status_code == 200, visible.text[:500]
    assert_strict_openapi_exchange(visible, ROUTE)
    assert visible.json()["project"]["role"] == "viewer"


def test_deleted_project_is_not_found(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    assert projects_client.delete_project(project_id).status_code == 200

    resp = projects_client.get_project(project_id)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_NOT_FOUND"


def test_missing_project_is_not_found(projects_client: ProjectsClient) -> None:
    resp = projects_client.get_project(MISSING_PROJECT_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_project_id_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.get_project(MALFORMED_PROJECT_ID)

    assert validation_fields(resp) == {"params.projectId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_project_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.get(f"/{MISSING_PROJECT_ID}", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
