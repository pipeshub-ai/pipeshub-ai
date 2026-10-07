"""Strict OpenAPI audit of POST /api/v1/projects/:projectId/knowledge-base."""

from __future__ import annotations

import re

import pytest
from helper.clients.kb_client import KBClient
from helper.clients.projects_client import ProjectsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from projects_audit_support import (
    KNOWLEDGE_BASE_TEMPLATE,
    MALFORMED_PROJECT_ID,
    MISSING_PROJECT_ID,
    SeedProject,
    add_member,
    request_as,
    validation_fields,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = KNOWLEDGE_BASE_TEMPLATE
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def test_owner_creates_it_once_and_gets_the_same_id_after(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    first = projects_client.ensure_knowledge_base(project_id)
    assert first.status_code == 200, first.text[:500]
    assert_strict_openapi_exchange(first, ROUTE)
    kb_id = first.json()["kbId"]
    assert UUID.match(kb_id), kb_id

    again = projects_client.ensure_knowledge_base(project_id)
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert again.json() == {"kbId": kb_id}
    assert projects_client.get_project(project_id).json()["project"]["linkedKnowledgeBaseId"] == kb_id


def test_editor_gets_the_owner_s_collection(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "editor")

    by_editor = request_as(second_user, "POST", f"/{project_id}/knowledge-base")
    assert by_editor.status_code == 200, by_editor.text[:500]
    assert_strict_openapi_exchange(by_editor, ROUTE)

    by_owner = projects_client.ensure_knowledge_base(project_id)
    assert by_owner.json() == by_editor.json()


def test_a_collection_deleted_elsewhere_is_replaced(
    projects_client: ProjectsClient, pipeshub_client: PipeshubClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    first = projects_client.ensure_knowledge_base(project_id)
    assert first.status_code == 200, first.text[:500]
    old_kb_id = first.json()["kbId"]
    KBClient(pipeshub_client).delete_kb(old_kb_id)

    resp = projects_client.ensure_knowledge_base(project_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["kbId"] != old_kb_id


def test_viewer_cannot_create_it(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "viewer")

    resp = request_as(second_user, "POST", f"/{project_id}/knowledge-base")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN"
    assert projects_client.get_project(project_id).json()["project"].get("linkedKnowledgeBaseId") is None


def test_outsider_and_missing_project_are_not_found(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    outsider = request_as(second_user, "POST", f"/{project_id}/knowledge-base")
    missing = projects_client.ensure_knowledge_base(MISSING_PROJECT_ID)

    for resp in (outsider, missing):
        assert resp.status_code == 404, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["error"]["code"] == "HTTP_NOT_FOUND"


def test_malformed_project_id_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.ensure_knowledge_base(MALFORMED_PROJECT_ID)

    assert validation_fields(resp) == {"params.projectId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.ensure_knowledge_base(MISSING_PROJECT_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
