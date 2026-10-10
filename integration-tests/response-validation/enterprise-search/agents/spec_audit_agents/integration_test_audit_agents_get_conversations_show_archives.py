"""Strict OpenAPI audit of GET /api/v1/agents/conversations/show/archives."""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from agents_audit_support import (
    AgentsAuditClient,
    SeedAgentConversation,
    request_as,
)
from bson import ObjectId
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/conversations/show/archives"


def _seed_archived(seed: SeedAgentConversation, owner: str, agent_key: str) -> str:
    # A day ahead so this agent's group sorts first, ahead of other runs' archives.
    return seed(
        agent_key=agent_key,
        owner=owner,
        isArchived=True,
        archivedBy=ObjectId(owner),
        lastActivityAt=int(time.time() * 1000) + 86_400_000,
    )


def _group(body: dict[str, Any], agent_key: str) -> dict[str, Any] | None:
    return next((g for g in body["groups"] if g["agentKey"] == agent_key), None)


def test_archived_conversation_is_listed_under_its_agent(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    admin_user_id: str,
) -> None:
    agent_key = f"spec-audit-archived-{uuid.uuid4().hex[:8]}"
    conversation_id = _seed_archived(seed_agent_conversation, admin_user_id, agent_key)

    resp = agents_audit_client.list_grouped_archives(agentPage=1, agentLimit=100)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    group = _group(body, agent_key)
    assert group is not None, body["groups"][:3]
    assert [c["_id"] for c in group["conversations"]] == [conversation_id]
    assert group["conversations"][0]["archivedBy"] == admin_user_id
    assert body["agentPagination"]["page"] == 1
    assert body["agentPagination"]["limit"] == 100


def test_unarchived_and_other_users_conversations_are_not_listed(
    agents_audit_client: AgentsAuditClient,
    seed_agent_conversation: SeedAgentConversation,
    second_user: SecondUser,
) -> None:
    agent_key = f"spec-audit-archived-{uuid.uuid4().hex[:8]}"
    seed_agent_conversation(agent_key=agent_key)
    _seed_archived(seed_agent_conversation, second_user.user_id, agent_key)

    resp = agents_audit_client.list_grouped_archives(agentLimit=100)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _group(resp.json(), agent_key) is None


@pytest.mark.parametrize(
    ("params", "page", "limit"),
    [
        pytest.param({"agentPage": "0", "agentLimit": "999"}, 1, 100, id="out-of-range-clamped"),
        pytest.param({"agentPage": "abc", "agentLimit": "xyz"}, 1, 5, id="non-numeric-defaulted"),
    ],
)
def test_out_of_contract_paging_is_normalised_not_refused(
    agents_audit_client: AgentsAuditClient,
    params: dict[str, str],
    page: int,
    limit: int,
) -> None:
    with outside_request_contract("the validator clamps or defaults paging values the spec bounds"):
        resp = agents_audit_client.list_grouped_archives(**params)
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    pagination = resp.json()["agentPagination"]
    assert (pagination["page"], pagination["limit"]) == (page, limit)


def test_defaults_apply_without_query(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_grouped_archives()

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["agentPagination"]
    assert (pagination["page"], pagination["limit"]) == (1, 5)


def test_member_sees_own_archives(
    second_user: SecondUser,
    seed_agent_conversation: SeedAgentConversation,
) -> None:
    agent_key = f"spec-audit-archived-{uuid.uuid4().hex[:8]}"
    conversation_id = _seed_archived(seed_agent_conversation, second_user.user_id, agent_key)

    resp = request_as(second_user, "GET", "/conversations/show/archives", params={"agentLimit": "100"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    group = _group(resp.json(), agent_key)
    assert group is not None and [c["_id"] for c in group["conversations"]] == [conversation_id]


def test_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.list_grouped_archives(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_agent_read_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.list_grouped_archives(auth=False, headers=narrow_scope_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
