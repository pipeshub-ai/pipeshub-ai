"""Strict OpenAPI audit of POST /api/v1/projects/:projectId/archive."""

from __future__ import annotations

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from projects_audit_support import (
    ARCHIVE_TEMPLATE,
    MALFORMED_PROJECT_ID,
    MISSING_PROJECT_ID,
    SeedProject,
    add_member,
    request_as,
    validation_fields,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = ARCHIVE_TEMPLATE


def test_owner_archives_project_and_repeating_it_is_harmless(
    projects_client: ProjectsClient, seed_project: SeedProject, pipeshub_client: PipeshubClient
) -> None:
    acting_user_id = pipeshub_client.acting_user_id
    project_id = seed_project()["_id"]

    for _ in range(2):
        resp = projects_client.post(f"/{project_id}/archive")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        project = resp.json()["project"]
        assert project["isArchived"] is True
        assert project["role"] == "owner"
    assert project["archivedBy"] == acting_user_id


def test_editor_can_archive(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "editor")

    resp = request_as(second_user, "POST", f"/{project_id}/archive")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["project"]["isArchived"] is True
    assert resp.json()["project"]["role"] == "editor"
    assert resp.json()["project"]["archivedBy"] == second_user.user_id


def test_viewer_cannot_archive(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "viewer")

    resp = request_as(second_user, "POST", f"/{project_id}/archive")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN"


def test_outsider_archive_is_not_found(second_user: SecondUser, seed_project: SeedProject) -> None:
    project_id = seed_project()["_id"]

    resp = request_as(second_user, "POST", f"/{project_id}/archive")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_missing_project_is_not_found(projects_client: ProjectsClient) -> None:
    resp = projects_client.post(f"/{MISSING_PROJECT_ID}/archive")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_malformed_project_id_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.post(f"/{MALFORMED_PROJECT_ID}/archive")

    assert validation_fields(resp) == {"params.projectId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.post(f"/{MISSING_PROJECT_ID}/archive", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
