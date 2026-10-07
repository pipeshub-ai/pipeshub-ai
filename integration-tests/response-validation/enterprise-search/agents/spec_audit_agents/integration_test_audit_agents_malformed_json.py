"""Every /api/v1/agents route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and authentication, and its failure is
reported as an internal error rather than a 400.
"""

from __future__ import annotations

import pytest
from agents_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_CONVERSATION_ID,
    MISSING_MESSAGE_ID,
    MISSING_AGENT_KEY,
    MISSING_RECORD_ID,
    SEED_AGENT_KEY,
    UNKNOWN_MODEL_KEY,
    UNKNOWN_WEB_SEARCH_PROVIDER,
    AgentsAuditClient,
    error_of,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

_C = f"{SEED_AGENT_KEY}/conversations/{MISSING_CONVERSATION_ID}"

OPERATIONS = [
    ("GET", "/conversations/show/archives", "/agents/conversations/show/archives"),
    ("POST", f"/{SEED_AGENT_KEY}/conversations", "/agents/:agentKey/conversations"),
    ("POST", f"/{SEED_AGENT_KEY}/conversations/stream", "/agents/:agentKey/conversations/stream"),
    ("POST", f"/{_C}/messages", "/agents/:agentKey/conversations/:conversationId/messages"),
    ("POST", f"/{_C}/messages/stream", "/agents/:agentKey/conversations/:conversationId/messages/stream"),
    (
        "POST",
        f"/{SEED_AGENT_KEY}/conversations/attachments/upload",
        "/agents/:agentKey/conversations/attachments/upload",
    ),
    (
        "DELETE",
        f"/{SEED_AGENT_KEY}/conversations/attachments/{MISSING_RECORD_ID}",
        "/agents/:agentKey/conversations/attachments/:recordId",
    ),
    (
        "POST",
        f"/{_C}/message/{MISSING_MESSAGE_ID}/regenerate",
        "/agents/:agentKey/conversations/:conversationId/message/:messageId/regenerate",
    ),
    ("POST", f"/{_C}/cancel", "/agents/:agentKey/conversations/:conversationId/cancel"),
    (
        "POST",
        f"/{_C}/message/{MISSING_MESSAGE_ID}/feedback",
        "/agents/:agentKey/conversations/:conversationId/message/:messageId/feedback",
    ),
    ("GET", f"/{SEED_AGENT_KEY}/conversations", "/agents/:agentKey/conversations"),
    ("GET", f"/{_C}", "/agents/:agentKey/conversations/:conversationId"),
    ("DELETE", f"/{_C}", "/agents/:agentKey/conversations/:conversationId"),
    ("PATCH", f"/{_C}/title", "/agents/:agentKey/conversations/:conversationId/title"),
    ("PUT", f"/{_C}/project", "/agents/:agentKey/conversations/:conversationId/project"),
    (
        "PATCH",
        f"/{_C}/project-visibility",
        "/agents/:agentKey/conversations/:conversationId/project-visibility",
    ),
    ("POST", f"/{_C}/archive", "/agents/:agentKey/conversations/:conversationId/archive"),
    ("POST", f"/{_C}/unarchive", "/agents/:agentKey/conversations/:conversationId/unarchive"),
    (
        "GET",
        f"/{SEED_AGENT_KEY}/conversations/show/archives",
        "/agents/:agentKey/conversations/show/archives",
    ),
    ("POST", "/create", "/agents/create"),
    ("GET", f"/{MISSING_AGENT_KEY}", "/agents/:agentKey"),
    ("PUT", f"/{MISSING_AGENT_KEY}", "/agents/:agentKey"),
    ("DELETE", f"/{MISSING_AGENT_KEY}", "/agents/:agentKey"),
    ("GET", "", "/agents"),
    (
        "GET",
        f"/web-search-usage/{UNKNOWN_WEB_SEARCH_PROVIDER}",
        "/agents/web-search-usage/:provider",
    ),
    ("GET", f"/model-usage/{UNKNOWN_MODEL_KEY}", "/agents/model-usage/:model_key"),
]


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    OPERATIONS,
    ids=[f"{m} {r.removeprefix('/agents') or '/'}" for m, _, r in OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error(
    agents_audit_client: AgentsAuditClient, method: str, sub_path: str, route: str
) -> None:
    resp = agents_audit_client._client.request(
        method,
        f"/api/v1/agents{sub_path}",
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )

    assert resp.status_code == 500, resp.text[:500]
    assert error_of(resp)["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
