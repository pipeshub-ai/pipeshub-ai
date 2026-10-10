"""Strict OpenAPI audit of DELETE /api/v1/agents/:agentKey/conversations/attachments/:recordId."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    MISSING_RECORD_ID,
    OVERLONG_RECORD_ID,
    SEED_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    UploadAttachment,
    request_as,
    uploaded_record_ids,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/attachments/:recordId"


def test_delete_uploaded_attachment_is_no_content(
    agents_audit_client: AgentsAuditClient,
    upload_attachment: UploadAttachment,
) -> None:
    uploaded = upload_attachment()
    assert uploaded.status_code == 200, uploaded.text[:500]
    (record_id,) = uploaded_record_ids(uploaded)

    resp = agents_audit_client.delete_attachment(SEED_AGENT_KEY, record_id)

    assert resp.status_code == 204, resp.text[:500]
    assert resp.content == b""
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_unknown_attachment_is_no_content(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.delete_attachment(SEED_AGENT_KEY, MISSING_RECORD_ID)

    assert resp.status_code == 204, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_another_users_attachment_is_not_found_with_empty_body(
    upload_attachment: UploadAttachment,
    second_user: SecondUser,
) -> None:
    uploaded = upload_attachment()
    assert uploaded.status_code == 200, uploaded.text[:500]
    (record_id,) = uploaded_record_ids(uploaded)

    resp = request_as(second_user, "DELETE", f"/{SEED_AGENT_KEY}/conversations/attachments/{record_id}")

    # The query service's 404 is forwarded as a bare status.
    assert resp.status_code == 404, resp.text[:500]
    assert resp.content == b""
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("agent_key", "record_id"),
    [
        pytest.param(SEED_AGENT_KEY, OVERLONG_RECORD_ID, id="overlong-record-id"),
        pytest.param(SEED_AGENT_KEY, "%20", id="blank-record-id"),
        pytest.param(UNSAFE_PATH_SEGMENT, MISSING_RECORD_ID, id="unsafe-agent-key"),
    ],
)
def test_delete_with_bad_path_is_rejected(
    agents_audit_client: AgentsAuditClient, agent_key: str, record_id: str
) -> None:
    resp = agents_audit_client.delete_attachment(agent_key, record_id)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.delete_attachment(SEED_AGENT_KEY, MISSING_RECORD_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    resp = agents_audit_client.delete_attachment(
        SEED_AGENT_KEY, MISSING_RECORD_ID, auth=False, headers=narrow_scope_headers
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
