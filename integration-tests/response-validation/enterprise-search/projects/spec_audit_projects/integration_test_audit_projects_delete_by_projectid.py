"""Strict OpenAPI audit of DELETE /api/v1/projects/:projectId."""

from __future__ import annotations

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.second_user import SecondUser
from projects_audit_support import (
    MALFORMED_PROJECT_ID,
    MISSING_PROJECT_ID,
    PROJECT_TEMPLATE,
    SeedProject,
    add_member,
    request_as,
    validation_fields,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = PROJECT_TEMPLATE
DELETED_BODY = {"message": "Project deleted successfully"}


def test_owner_deletes_project_and_delete_is_idempotent(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    first = projects_client.delete_project(project_id)
    assert first.status_code == 200, first.text[:500]
    assert_strict_openapi_exchange(first, ROUTE)
    assert first.json() == DELETED_BODY
    assert projects_client.get_project(project_id).status_code == 404

    again = projects_client.delete_project(project_id)
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert again.json() == DELETED_BODY


def test_delete_removes_the_projects_hidden_collection(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    ensured = projects_client.ensure_knowledge_base(project_id)
    assert ensured.status_code == 200, ensured.text[:500]

    resp = projects_client.delete_project(project_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == DELETED_BODY


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_member_cannot_delete(
    projects_client: ProjectsClient,
    second_user: SecondUser,
    seed_project: SeedProject,
    role: str,
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, role)

    resp = request_as(second_user, "DELETE", f"/{project_id}")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert projects_client.get_project(project_id).status_code == 200


def test_outsider_delete_is_not_found_even_after_the_owner_deleted_it(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    live = request_as(second_user, "DELETE", f"/{project_id}")
    assert live.status_code == 404, live.text[:500]
    assert_strict_openapi_exchange(live, ROUTE)

    assert projects_client.delete_project(project_id).status_code == 200
    deleted = request_as(second_user, "DELETE", f"/{project_id}")
    assert deleted.status_code == 404, deleted.text[:500]
    assert_strict_openapi_exchange(deleted, ROUTE)


def test_delete_missing_project_is_not_found(projects_client: ProjectsClient) -> None:
    resp = projects_client.delete_project(MISSING_PROJECT_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_malformed_project_id_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.delete_project(MALFORMED_PROJECT_ID)

    assert validation_fields(resp) == {"params.projectId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.delete(f"/{MISSING_PROJECT_ID}", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
