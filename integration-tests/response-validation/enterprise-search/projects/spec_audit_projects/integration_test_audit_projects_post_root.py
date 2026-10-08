"""Strict OpenAPI audit of POST /api/v1/projects."""

from __future__ import annotations

import uuid
from typing import Any, Iterator

import pytest
from helper.clients.projects_client import ProjectsClient
from helper.pipeshub_client import PipeshubClient
from projects_audit_support import (
    FILTER_NODE,
    PROJECT_FIELDS,
    ROOT_TEMPLATE,
    validation_fields,
)
from strict_openapi import (
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = ROOT_TEMPLATE


@pytest.fixture
def created(projects_client: ProjectsClient) -> Iterator[list[str]]:
    """Ids of projects a test created; each is deleted on teardown."""
    ids: list[str] = []
    try:
        yield ids
    finally:
        for project_id in ids:
            projects_client.delete_project(project_id)


def _name() -> str:
    return f"spec-audit {uuid.uuid4().hex[:8]}"


def test_create_minimal_project(
    projects_client: ProjectsClient, pipeshub_client: PipeshubClient, created: list[str]
) -> None:
    name = _name()
    resp = projects_client.post("/", json={"name": f"  {name}  "})

    if resp.status_code == 201:
        created.append(resp.json()["project"]["_id"])
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    project = resp.json()["project"]
    assert set(project) <= PROJECT_FIELDS
    assert project["name"] == name, "the name is stored trimmed"
    assert project["role"] == "owner"
    assert project["userId"] == pipeshub_client.acting_user_id
    assert project["visibility"] == "private"
    assert project["chatSharing"] == "private"
    assert project["members"] == []
    assert project["tools"] == []
    assert project["linkedKnowledgeBaseId"] is None
    assert project["isPinned"] is project["isArchived"] is project["isDeleted"] is False


def test_create_project_with_every_documented_field(
    projects_client: ProjectsClient, created: list[str]
) -> None:
    body: dict[str, Any] = {
        "name": _name(),
        "description": "d" * 1000,
        "icon": "i" * 100,
        "color": "c" * 50,
        "instructions": "x" * 8000,
        "knowledgeScope": {"apps": ["a" * 200], "kb": ["kb-1"]},
        "appliedFilters": {"apps": [FILTER_NODE], "kb": [{**FILTER_NODE, "id": "kb-1"}]},
        "tools": [f"toolset.tool_{i}" for i in range(200)],
    }
    resp = projects_client.post("/", json=body)

    if resp.status_code == 201:
        created.append(resp.json()["project"]["_id"])
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    project = resp.json()["project"]
    for key, value in body.items():
        assert project[key] == value, key


def test_create_project_drops_unknown_fields(
    projects_client: ProjectsClient, created: list[str]
) -> None:
    with outside_request_contract("unknown body fields are stripped by the validator, not refused"):
        resp = projects_client.post(
            "/",
            json={
                "name": _name(),
                "visibility": "org",
                "isPinned": True,
                "knowledgeScope": {"apps": [], "extra": ["x"]},
                "appliedFilters": {"apps": [{**FILTER_NODE, "extra": 1}]},
                "spec_audit_unknown": 1,
            },
        )
        if resp.status_code == 201:
            created.append(resp.json()["project"]["_id"])
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    project = resp.json()["project"]
    # Create cannot set owner-only or state fields: they keep their defaults.
    assert project["visibility"] == "private"
    assert project["isPinned"] is False
    assert "spec_audit_unknown" not in project
    assert "extra" not in project["knowledgeScope"]
    assert project["appliedFilters"]["apps"] == [FILTER_NODE]


def _node_without(field: str) -> dict[str, str]:
    return {k: v for k, v in FILTER_NODE.items() if k != field}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.name", id="name-missing"),
        pytest.param({"name": ""}, "body.name", id="name-empty"),
        pytest.param({"name": "   "}, "body.name", id="name-blank"),
        pytest.param({"name": 7}, "body.name", id="name-not-string"),
        pytest.param({"name": "n" * 101}, "body.name", id="name-too-long"),
        pytest.param({"description": "d" * 1001}, "body.description", id="description-too-long"),
        pytest.param({"icon": "i" * 101}, "body.icon", id="icon-too-long"),
        pytest.param({"color": "c" * 51}, "body.color", id="color-too-long"),
        pytest.param({"instructions": "x" * 8001}, "body.instructions", id="instructions-too-long"),
        pytest.param({"tools": ["t"] * 201}, "body.tools", id="tools-too-many"),
        pytest.param({"tools": [""]}, "body.tools.0", id="tool-empty"),
        pytest.param({"tools": ["t" * 201]}, "body.tools.0", id="tool-too-long"),
        pytest.param({"knowledgeScope": {"apps": [""]}}, "body.knowledgeScope.apps.0", id="scope-app-empty"),
        pytest.param({"knowledgeScope": {"kb": ["k" * 201]}}, "body.knowledgeScope.kb.0", id="scope-kb-too-long"),
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
def test_create_project_rejects_invalid_body(
    projects_client: ProjectsClient, created: list[str], body: dict[str, Any], field: str
) -> None:
    body = {"name": _name(), **body} if field != "body.name" else body
    resp = projects_client.post("/", json=body)

    if resp.status_code == 201:
        created.append(resp.json()["project"]["_id"])
    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_project_without_body_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.post("/")

    assert validation_fields(resp) == {"body.name"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_project_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.post("/", auth=False, json={"name": _name()})

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
