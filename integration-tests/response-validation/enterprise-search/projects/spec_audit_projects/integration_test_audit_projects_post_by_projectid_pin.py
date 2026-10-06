"""Strict OpenAPI audit of POST /api/v1/projects/:projectId/pin."""

from __future__ import annotations

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.second_user import SecondUser
from projects_audit_support import (
    PIN_TEMPLATE,
    MALFORMED_PROJECT_ID,
    MISSING_PROJECT_ID,
    SeedProject,
    add_member,
    request_as,
    validation_fields,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = PIN_TEMPLATE


def test_owner_pins_project_and_repeating_it_is_harmless(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    for _ in range(2):
        resp = projects_client.post(f"/{project_id}/pin")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        project = resp.json()["project"]
        assert project["isPinned"] is True
        assert project["role"] == "owner"


def test_editor_can_pin(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "editor")

    resp = request_as(second_user, "POST", f"/{project_id}/pin")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["project"]["isPinned"] is True
    assert resp.json()["project"]["role"] == "editor"


def test_viewer_cannot_pin(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "viewer")

    resp = request_as(second_user, "POST", f"/{project_id}/pin")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN"


def test_outsider_pin_is_not_found(second_user: SecondUser, seed_project: SeedProject) -> None:
    project_id = seed_project()["_id"]

    resp = request_as(second_user, "POST", f"/{project_id}/pin")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_pin_missing_project_is_not_found(projects_client: ProjectsClient) -> None:
    resp = projects_client.post(f"/{MISSING_PROJECT_ID}/pin")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_pin_malformed_project_id_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.post(f"/{MALFORMED_PROJECT_ID}/pin")

    assert validation_fields(resp) == {"params.projectId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_pin_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.post(f"/{MISSING_PROJECT_ID}/pin", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
