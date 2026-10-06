"""The /api/v1/conversations routes answer 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches authentication or validation.
"""

from __future__ import annotations

import pytest
from conversations_audit_support import (
    BY_ID_ROUTE,
    CREATE_ROUTE,
    DELETE_ATTACHMENT_ROUTE,
    JSON_HEADERS,
    LIST_ROUTE,
    MALFORMED_JSON_BODY,
    MESSAGES_ROUTE,
    MESSAGES_STREAM_ROUTE,
    MISSING_CONVERSATION_ID,
    MISSING_RECORD_ID,
    STREAM_ROUTE,
    UPLOAD_ROUTE,
    ConversationsAuditClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("POST", "/create", CREATE_ROUTE),
        ("POST", "/attachments/upload", UPLOAD_ROUTE),
        ("DELETE", f"/attachments/{MISSING_RECORD_ID}", DELETE_ATTACHMENT_ROUTE),
        ("POST", "/stream", STREAM_ROUTE),
        ("POST", f"/{MISSING_CONVERSATION_ID}/messages", MESSAGES_ROUTE),
        ("POST", f"/{MISSING_CONVERSATION_ID}/messages/stream", MESSAGES_STREAM_ROUTE),
        ("GET", "/", LIST_ROUTE),
        ("GET", f"/{MISSING_CONVERSATION_ID}", BY_ID_ROUTE),
        ("DELETE", f"/{MISSING_CONVERSATION_ID}", BY_ID_ROUTE),
    ],
    ids=[
        "create",
        "upload_attachments",
        "delete_attachment",
        "stream",
        "add_message",
        "add_message_stream",
        "list",
        "get_by_id",
        "delete_by_id",
    ],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    conversations_audit_client: ConversationsAuditClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    send = {
        "GET": conversations_audit_client.get,
        "POST": conversations_audit_client.post,
        "DELETE": conversations_audit_client.delete,
    }[method]
    resp = send(sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS)
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
