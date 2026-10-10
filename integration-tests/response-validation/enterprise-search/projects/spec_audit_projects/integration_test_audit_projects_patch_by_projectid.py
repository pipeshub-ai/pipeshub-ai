"""Strict OpenAPI audit of PATCH /api/v1/projects/:projectId."""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.second_user import SecondUser
from projects_audit_support import (
    FILTER_NODE,
    MALFORMED_PROJECT_ID,
    MISSING_PROJECT_ID,
    PROJECT_TEMPLATE,
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

ROUTE = PROJECT_TEMPLATE


def test_owner_updates_every_field(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project = seed_project()
    patch: dict[str, Any] = {
        "name": "  spec-audit renamed  ",
        "description": "new description",
        "icon": "rocket",
        "color": "#ff0000",
        "instructions": "Cite sources.",
        "knowledgeScope": {"apps": ["app-1"], "kb": []},
        "appliedFilters": {"apps": [FILTER_NODE]},
        "tools": ["toolset.tool"],
        "visibility": "org",
        "chatSharing": "members",
    }

    resp = projects_client.patch(f"/{project['_id']}", json=patch)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    updated = resp.json()["project"]
    assert updated["name"] == "spec-audit renamed"
    for key in patch.keys() - {"name", "appliedFilters"}:
        assert updated[key] == patch[key], key
    assert updated["appliedFilters"]["apps"] == [FILTER_NODE]
    assert updated["role"] == "owner"


@pytest.mark.parametrize("send_body", [True, False], ids=["empty-object", "no-body"])
def test_update_with_nothing_to_change_succeeds(
    projects_client: ProjectsClient, seed_project: SeedProject, send_body: bool
) -> None:
    project = seed_project()

    resp = projects_client.patch(f"/{project['_id']}", **({"json": {}} if send_body else {}))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["project"]["name"] == project["name"]


def test_update_drops_unknown_fields(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project = seed_project()

    with outside_request_contract("unknown body fields are stripped by the validator, not refused"):
        resp = projects_client.patch(
            f"/{project['_id']}", json={"isPinned": True, "userId": "x", "members": []}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["project"]["isPinned"] is False


def test_editor_updates_metadata(
    projects_client: ProjectsClient, second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]
    add_member(projects_client, project_id, second_user.user_id, "editor")

    resp = request_as(second_user, "PATCH", f"/{project_id}", json={"description": "by an editor"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["project"]["description"] == "by an editor"
    assert resp.json()["project"]["role"] == "editor"


@pytest.mark.parametrize(
    ("role", "body"),
    [
        pytest.param("viewer", {"description": "x"}, id="viewer"),
        pytest.param("editor", {"visibility": "org"}, id="editor-visibility"),
        pytest.param("editor", {"chatSharing": "members"}, id="editor-chat-sharing"),
    ],
)
def test_update_without_the_needed_role_is_forbidden(
    projects_client: ProjectsClient,
    second_user: SecondUser,
    seed_project: SeedProject,
    role: str,
    body: dict[str, str],
) -> None:
    project = seed_project()
    add_member(projects_client, project["_id"], second_user.user_id, role)

    resp = request_as(second_user, "PATCH", f"/{project['_id']}", json=body)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN"
    unchanged = projects_client.get_project(project["_id"]).json()["project"]
    assert {k: unchanged.get(k) for k in body} == {k: project.get(k) for k in body}


def test_update_by_outsider_is_not_found(
    second_user: SecondUser, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    resp = request_as(second_user, "PATCH", f"/{project_id}", json={"description": "x"})

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_missing_project_is_not_found(projects_client: ProjectsClient) -> None:
    resp = projects_client.patch(f"/{MISSING_PROJECT_ID}", json={"description": "x"})

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def _node_without(field: str) -> dict[str, str]:
    return {k: v for k, v in FILTER_NODE.items() if k != field}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"name": ""}, "body.name", id="name-empty"),
        pytest.param({"name": "   "}, "body.name", id="name-blank"),
        pytest.param({"name": "n" * 101}, "body.name", id="name-too-long"),
        pytest.param({"description": 5}, "body.description", id="description-not-string"),
        pytest.param({"color": "c" * 51}, "body.color", id="color-too-long"),
        pytest.param({"tools": [""]}, "body.tools.0", id="tool-empty"),
        pytest.param({"knowledgeScope": {"apps": [""]}}, "body.knowledgeScope.apps.0", id="scope-app-empty"),
        pytest.param({"visibility": "public"}, "body.visibility", id="visibility-unknown"),
        pytest.param({"chatSharing": "everyone"}, "body.chatSharing", id="chat-sharing-unknown"),
        *[
            pytest.param(
                {"appliedFilters": {part: [_node_without(field)]}},
                f"body.appliedFilters.{part}.0.{field}",
                id=f"filter-{part}-without-{field}",
            )
            for part in ("apps", "kb")
            for field in ("id", "name", "nodeType", "connector")
        ],
    ],
)
def test_update_rejects_invalid_body(
    projects_client: ProjectsClient, seed_project: SeedProject, body: dict[str, Any], field: str
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.patch(f"/{project_id}", json=body)

    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_malformed_project_id_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.patch(f"/{MALFORMED_PROJECT_ID}", json={"description": "x"})

    assert validation_fields(resp) == {"params.projectId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.patch(f"/{MISSING_PROJECT_ID}", auth=False, json={"description": "x"})

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
