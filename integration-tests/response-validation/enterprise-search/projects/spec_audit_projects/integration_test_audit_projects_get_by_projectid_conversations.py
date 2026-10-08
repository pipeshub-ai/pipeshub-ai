"""Strict OpenAPI audit of GET /api/v1/projects/:projectId/conversations."""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from helper.clients.conversations_client import ConversationsClient
from helper.clients.projects_client import ProjectsClient
from helper.conversation_seeds import seed_query
from helper.second_user import SecondUser
from projects_audit_support import (
    CONVERSATIONS_TEMPLATE,
    MALFORMED_PROJECT_ID,
    MISSING_PROJECT_ID,
    SeedProject,
    add_member,
    conversation_id_from_stream,
    request_as,
    validation_fields,
)
from strict_openapi import (
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = CONVERSATIONS_TEMPLATE
STREAM_TIMEOUT = 180


@pytest.fixture
def project_with_conversation(
    projects_client: ProjectsClient,
    conversations_client: ConversationsClient,
    seed_project: SeedProject,
) -> Iterator[tuple[str, str]]:
    """(project id, conversation id): one chat streamed inside a new private-chat project."""
    project_id = seed_project()["_id"]
    with conversations_client.stream_conversation(
        json={
            "query": seed_query(uuid.uuid4().hex),
            "chatMode": "internal_search",
            "projectId": project_id,
        },
        timeout=STREAM_TIMEOUT,
    ) as resp:
        conversation_id = conversation_id_from_stream(resp)
    try:
        yield project_id, conversation_id
    finally:
        conversations_client.delete_conversation(conversation_id)


def test_empty_project_lists_no_conversations(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.list_project_conversations(project_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "conversations": [],
        "pagination": {"page": 1, "limit": 20, "totalCount": 0, "totalPages": 0},
    }


def test_owner_lists_project_conversations_and_members_see_only_shared_ones(
    projects_client: ProjectsClient,
    conversations_client: ConversationsClient,
    second_user: SecondUser,
    project_with_conversation: tuple[str, str],
) -> None:
    project_id, conversation_id = project_with_conversation

    resp = projects_client.list_project_conversations(project_id, page=1, limit=5)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert [c["_id"] for c in body["conversations"]] == [conversation_id]
    assert body["pagination"] == {"page": 1, "limit": 5, "totalCount": 1, "totalPages": 1}
    row = body["conversations"][0]
    assert row["projectId"] == project_id
    assert row["projectVisibility"] == "private"

    past_the_end = projects_client.list_project_conversations(project_id, page=2, limit=1)
    assert past_the_end.status_code == 200, past_the_end.text[:500]
    assert_strict_openapi_exchange(past_the_end, ROUTE)
    assert past_the_end.json()["conversations"] == []

    add_member(projects_client, project_id, second_user.user_id, "viewer")
    private = request_as(second_user, "GET", f"/{project_id}/conversations")
    assert private.status_code == 200, private.text[:500]
    assert_strict_openapi_exchange(private, ROUTE)
    assert private.json()["conversations"] == []

    shared = conversations_client.set_project_visibility(conversation_id, "project")
    assert shared.status_code == 200, shared.text[:500]
    visible = request_as(second_user, "GET", f"/{project_id}/conversations")
    assert visible.status_code == 200, visible.text[:500]
    assert_strict_openapi_exchange(visible, ROUTE)
    assert [c["_id"] for c in visible.json()["conversations"]] == [conversation_id]


def test_list_accepts_fractional_page_and_limit(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.list_project_conversations(project_id, page="1.5", limit="2.5")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"] == {"page": 1.5, "limit": 2.5, "totalCount": 0, "totalPages": 0}


def test_list_accepts_exponent_notation_page(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    # "1e1" is a number per the spec; the checker only coerces plain integers and decimals.
    with outside_request_contract("the checker cannot coerce exponent notation in a query value"):
        resp = projects_client.list_project_conversations(project_id, page="1e1")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["page"] == 10


def test_list_ignores_unknown_query_parameters(
    projects_client: ProjectsClient, seed_project: SeedProject
) -> None:
    project_id = seed_project()["_id"]

    with outside_request_contract("unknown query parameters are stripped by the validator, not refused"):
        resp = projects_client.list_project_conversations(project_id, sort="title")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"page": "0"}, "query.page", id="page-zero"),
        pytest.param({"page": "x"}, "query.page", id="page-not-number"),
        pytest.param({"limit": "0"}, "query.limit", id="limit-zero"),
        pytest.param({"limit": "101"}, "query.limit", id="limit-too-big"),
    ],
)
def test_list_rejects_invalid_query(
    projects_client: ProjectsClient, seed_project: SeedProject, params: dict[str, str], field: str
) -> None:
    project_id = seed_project()["_id"]

    resp = projects_client.list_project_conversations(project_id, **params)

    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_outsider_is_not_found(second_user: SecondUser, seed_project: SeedProject) -> None:
    project_id = seed_project()["_id"]

    resp = request_as(second_user, "GET", f"/{project_id}/conversations")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_missing_project_is_not_found(projects_client: ProjectsClient) -> None:
    resp = projects_client.list_project_conversations(MISSING_PROJECT_ID)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_project_id_is_rejected(projects_client: ProjectsClient) -> None:
    resp = projects_client.list_project_conversations(MALFORMED_PROJECT_ID)

    assert validation_fields(resp) == {"params.projectId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_without_token_is_unauthorized(projects_client: ProjectsClient) -> None:
    resp = projects_client.get(f"/{MISSING_PROJECT_ID}/conversations", auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
