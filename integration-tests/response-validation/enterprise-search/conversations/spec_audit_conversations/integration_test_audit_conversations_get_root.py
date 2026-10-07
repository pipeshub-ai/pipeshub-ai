"""Strict OpenAPI audit of GET /api/v1/conversations (list)."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from bson import ObjectId
from conversations_audit_support import (
    INVALID_AUTH_HEADERS,
    LIST_ROUTE,
    MALFORMED_CONVERSATION_ID,
    REASONING_EFFORTS,
    ConversationsAuditClient,
    SeedConversation,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = LIST_ROUTE


def _list(client: ConversationsAuditClient, **params: Any) -> requests.Response:
    return client.get("/", params=params)


def _ids(resp: requests.Response) -> list[str]:
    return [c["_id"] for c in resp.json()["conversations"]]


def test_list_owned_conversations_by_default(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation
) -> None:
    conversation_id = seed_conversation()
    resp = _list(conversations_audit_client)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["source"] == "owned"
    assert body["pagination"]["page"] == 1
    # The validator's default page size, not the one the controller would pick.
    assert body["pagination"]["limit"] == 10, body["pagination"]
    assert conversation_id in _ids(_list(conversations_audit_client, conversationId=conversation_id))


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"source": "owned"}, id="source-owned"),
        pytest.param({"page": 1, "limit": 100}, id="page-and-max-limit"),
        pytest.param({"sortBy": "createdAt", "sortOrder": "asc"}, id="sort-created-asc"),
        pytest.param({"sortBy": "lastActivityAt", "sortOrder": "desc"}, id="sort-activity-desc"),
        pytest.param({"sortBy": "title"}, id="sort-title"),
        pytest.param({"search": "spec-audit"}, id="search"),
        pytest.param(
            {"startDate": "2020-01-01T00:00:00Z", "endDate": "2099-01-01T00:00:00+05:30"},
            id="date-range",
        ),
        *[pytest.param({"shared": value}, id=f"shared-{value}") for value in ("true", "false", "1", "0")],
        pytest.param({"projectId": "unassigned"}, id="project-unassigned"),
        pytest.param({"projectId": "0123456789abcdef01234567"}, id="project-id"),
    ],
)
def test_list_accepts_every_documented_filter(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    params: dict[str, Any],
) -> None:
    seed_conversation()
    resp = _list(conversations_audit_client, **params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["source"] == params.get("source", "owned")


def test_list_ignores_unknown_query_parameters(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    with outside_request_contract("an undocumented query parameter is sent on purpose"):
        resp = _list(conversations_audit_client, specAuditExtra="1")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_list_finds_a_conversation_by_its_title(
    conversations_audit_client: ConversationsAuditClient, seed_conversation: SeedConversation
) -> None:
    title = f"spec-audit-search {ObjectId()}"
    conversation_id = seed_conversation(title=title)
    resp = _list(conversations_audit_client, search=title, sortBy="title", sortOrder="asc")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _ids(resp) == [conversation_id]
    applied = resp.json()["filters"]["applied"]
    assert "search" in applied["filters"], applied


@pytest.mark.parametrize("effort", REASONING_EFFORTS)
def test_list_reports_the_reasoning_effort_of_a_conversation(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    effort: str,
) -> None:
    model_info = {
        "modelKey": "spec-audit-model",
        "modelName": "spec-audit",
        "modelFriendlyName": "Spec audit",
        "modelProvider": "azureOpenAI",
        "chatMode": "internal_search",
        "reasoningEffort": effort,
    }
    conversation_id = seed_conversation(modelInfo=model_info)
    resp = _list(conversations_audit_client, conversationId=conversation_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["conversations"][0]["modelInfo"] == model_info


def test_list_shared_with_me(
    conversations_audit_client: ConversationsAuditClient,
    seed_conversation: SeedConversation,
    second_user: SecondUser,
    admin_user_id: str,
) -> None:
    conversation_id = seed_conversation(
        owner=second_user.user_id,
        isShared=True,
        sharedWith=[{"userId": ObjectId(admin_user_id), "accessLevel": "read", "_id": ObjectId()}],
    )
    resp = _list(conversations_audit_client, source="shared", conversationId=conversation_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["source"] == "shared"
    assert _ids(resp) == [conversation_id]
    assert "sharedWith" not in resp.json()["conversations"][0]


@pytest.mark.parametrize(
    ("params", "page", "limit"),
    [
        pytest.param({"page": "1.5"}, 1, 10, id="fractional-page"),
        pytest.param({"page": "2.9", "limit": "5.5"}, 2, 5, id="fractional-page-and-limit"),
    ],
)
def test_list_truncates_fractional_page_and_limit(
    conversations_audit_client: ConversationsAuditClient,
    params: dict[str, Any],
    page: int,
    limit: int,
) -> None:
    resp = _list(conversations_audit_client, **params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (page, limit), pagination


@pytest.mark.parametrize(
    ("params", "named"),
    [
        pytest.param({"source": "everyone"}, "query.source", id="unknown-source"),
        pytest.param({"page": 0}, "query.page", id="page-zero"),
        pytest.param({"page": "first"}, "query.page", id="page-not-a-number"),
        pytest.param({"limit": 0}, "query.limit", id="limit-zero"),
        pytest.param({"limit": 101}, "query.limit", id="limit-over-100"),
        pytest.param({"sortBy": "owner"}, "query.sortBy", id="unknown-sort-field"),
        pytest.param({"sortOrder": "up"}, "query.sortOrder", id="unknown-sort-order"),
        pytest.param({"conversationId": MALFORMED_CONVERSATION_ID}, "query.conversationId", id="malformed-conversation-id"),
        pytest.param({"search": "a" * 1001}, "query.search", id="search-over-1000-chars"),
        pytest.param({"startDate": "2026-01-01"}, "query.startDate", id="start-date-without-time"),
        pytest.param({"endDate": "yesterday"}, "query.endDate", id="end-date-not-iso"),
        pytest.param({"shared": "yes"}, "query.shared", id="unknown-shared-value"),
        pytest.param({"projectId": "not-a-project"}, "query.projectId", id="malformed-project-id"),
    ],
)
def test_list_rejects_invalid_query(
    conversations_audit_client: ConversationsAuditClient, params: dict[str, Any], named: str
) -> None:
    resp = _list(conversations_audit_client, **params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert named in validation_fields(resp), resp.text[:500]


@pytest.mark.parametrize(
    "headers",
    [pytest.param(None, id="no_token"), pytest.param(INVALID_AUTH_HEADERS, id="invalid_token")],
)
def test_list_without_valid_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient, headers: dict[str, str] | None
) -> None:
    kwargs: dict[str, Any] = {"headers": headers} if headers else {}
    resp = conversations_audit_client.get("/", auth=False, **kwargs)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_with_token_lacking_read_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.get(
        f"{pipeshub_client.base_url}{ROUTE}",
        headers=narrow_scope_headers,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
