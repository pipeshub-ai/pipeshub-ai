"""Strict OpenAPI audit of POST /api/v1/conversations/attachments/upload."""

from __future__ import annotations

import pytest
from conversations_audit_support import (
    MALFORMED_CONVERSATION_ID,
    ConversationsAuditClient,
    MultipartFiles,
    UploadAttachment,
    attachment_files,
    uploaded_record_ids,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/conversations/attachments/upload"

FILE_NAME = "spec-audit.txt"


def test_upload_text_attachment_returns_attachment_refs(
    upload_attachment: UploadAttachment,
) -> None:
    resp = upload_attachment(attachment_files(name=FILE_NAME))

    assert resp.status_code == 200, resp.text[:500]
    attachments = resp.json()["attachments"]
    assert len(attachments) == 1, resp.text[:500]
    assert attachments[0]["recordName"] == FILE_NAME
    assert attachments[0]["mimeType"] == "text/plain"
    assert uploaded_record_ids(resp) == [attachments[0]["recordId"]]
    assert_strict_openapi_response(resp, ROUTE)


def test_upload_without_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.upload_attachments(attachment_files(), auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("files", "conversation_id"),
    [
        pytest.param(
            attachment_files(name="spec-audit.zip", mimetype="application/zip"),
            None,
            id="unsupported-mimetype",
        ),
        pytest.param(attachment_files(), MALFORMED_CONVERSATION_ID, id="malformed-conversation-id"),
    ],
)
def test_upload_rejects_unusable_file_or_conversation_id(
    upload_attachment: UploadAttachment,
    files: MultipartFiles,
    conversation_id: str | None,
) -> None:
    # Through the fixture so a wrongly accepted upload is still cleaned up.
    resp = upload_attachment(files, conversation_id=conversation_id)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_upload_without_file_part_is_bad_request(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.upload_attachments(None)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
