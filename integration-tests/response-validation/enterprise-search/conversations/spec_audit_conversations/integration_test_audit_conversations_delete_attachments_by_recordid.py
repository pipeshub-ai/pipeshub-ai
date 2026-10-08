"""Strict OpenAPI audit of DELETE /api/v1/conversations/attachments/:recordId."""

from __future__ import annotations

import pytest
import requests
from conversations_audit_support import (
    MISSING_RECORD_ID,
    OVERLONG_RECORD_ID,
    UNSAFE_RECORD_ID,
    ConversationsAuditClient,
    UploadAttachment,
    request_as,
    uploaded_record_ids,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/conversations/attachments/:recordId"


def _uploaded_record_id(upload_attachment: UploadAttachment) -> str:
    uploaded = upload_attachment()
    assert uploaded.status_code == 200, uploaded.text[:500]
    record_ids = uploaded_record_ids(uploaded)
    assert record_ids, f"upload returned no recordId: {uploaded.text[:500]}"
    return record_ids[0]


def test_delete_own_attachment_is_no_content_and_repeatable(
    conversations_audit_client: ConversationsAuditClient,
    upload_attachment: UploadAttachment,
) -> None:
    record_id = _uploaded_record_id(upload_attachment)

    resp = conversations_audit_client.delete_attachment(record_id)
    assert resp.status_code == 204, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.content == b""

    # The query service treats a record that is already gone as success.
    again = conversations_audit_client.delete_attachment(record_id)
    assert again.status_code == 204, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert again.content == b""


def test_member_cannot_delete_another_users_attachment(
    conversations_audit_client: ConversationsAuditClient,
    second_user: SecondUser,
    upload_attachment: UploadAttachment,
) -> None:
    record_id = _uploaded_record_id(upload_attachment)

    resp = request_as(second_user, "DELETE", f"/attachments/{record_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # Node relays only the upstream status, never its JSON detail.
    assert resp.content == b""

    # Still there: the owner's delete of a missing record would also be 204, so
    # prove survival through the member's second attempt staying 404.
    still_refused = request_as(second_user, "DELETE", f"/attachments/{record_id}")
    assert still_refused.status_code == 404, still_refused.text[:500]


@pytest.mark.parametrize(
    ("record_id", "code"),
    [
        pytest.param(UNSAFE_RECORD_ID, "HTTP_BAD_REQUEST", id="unsafe-path-segment"),
        pytest.param(OVERLONG_RECORD_ID, "VALIDATION_ERROR", id="over-256-chars"),
        # Decodes to two spaces. assert_spec_forbids_request does not read path parameters,
        # so the code asserted here is what ties this refusal to the path schema's pattern.
        pytest.param("%20%20", "HTTP_BAD_REQUEST", id="only-spaces"),
    ],
)
def test_delete_invalid_record_id_is_rejected(
    conversations_audit_client: ConversationsAuditClient, record_id: str, code: str
) -> None:
    resp = conversations_audit_client.delete_attachment(record_id)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == code, resp.text[:500]


def test_delete_without_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.delete_attachment(MISSING_RECORD_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = requests.delete(
        f"{pipeshub_client.base_url}/api/v1/conversations/attachments/{MISSING_RECORD_ID}",
        headers=narrow_scope_headers,
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
