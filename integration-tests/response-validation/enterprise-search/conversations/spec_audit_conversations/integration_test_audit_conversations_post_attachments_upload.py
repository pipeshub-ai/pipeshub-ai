"""Strict OpenAPI audit of POST /api/v1/conversations/attachments/upload.

The gate reads JSON request bodies only, so it cannot see that the multipart
``conversationId`` field breaks the documented pattern; calls that send one on
purpose say so with ``outside_request_contract``.
"""

from __future__ import annotations

import io

import pytest
import requests
from conversations_audit_support import (
    MALFORMED_CONVERSATION_ID,
    MISSING_CONVERSATION_ID,
    UPLOAD_ROUTE,
    ConversationsAuditClient,
    MultipartFiles,
    UploadAttachment,
    attachment_files,
    uploaded_record_ids,
    validation_fields,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = UPLOAD_ROUTE

FILE_NAME = "spec-audit.txt"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 10


def test_upload_text_attachment_returns_attachment_refs(
    upload_attachment: UploadAttachment,
) -> None:
    resp = upload_attachment(attachment_files(name=FILE_NAME))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["conversationId"] is None
    attachments = body["attachments"]
    assert len(attachments) == 1, resp.text[:500]
    assert attachments[0]["recordName"] == FILE_NAME
    assert attachments[0]["mimeType"] == "text/plain"
    assert uploaded_record_ids(resp) == [attachments[0]["recordId"]]


@pytest.mark.parametrize(
    ("conversation_id", "echoed"),
    [
        pytest.param("", None, id="empty-means-unset"),
        pytest.param(MISSING_CONVERSATION_ID, MISSING_CONVERSATION_ID, id="any-object-id-is-echoed"),
    ],
)
def test_upload_with_conversation_id(
    upload_attachment: UploadAttachment, conversation_id: str, echoed: str | None
) -> None:
    resp = upload_attachment(attachment_files(), conversation_id=conversation_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["conversationId"] == echoed


def test_upload_several_files_at_once(upload_attachment: UploadAttachment) -> None:
    files = attachment_files(name="one.txt") + attachment_files(name="two.md", mimetype="text/markdown")
    resp = upload_attachment(files)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [a["recordName"] for a in resp.json()["attachments"]] == ["one.txt", "two.md"]


def test_upload_without_token_is_unauthorized(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.upload_attachments(attachment_files(), auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_with_token_lacking_chat_scope_is_forbidden(
    pipeshub_client: PipeshubClient, narrow_scope_headers: dict[str, str]
) -> None:
    headers = {"Authorization": narrow_scope_headers["Authorization"]}
    resp = requests.post(
        f"{pipeshub_client.base_url}{ROUTE}",
        headers=headers,
        files=attachment_files(),
        timeout=pipeshub_client.timeout_seconds,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]


def test_upload_unsupported_mimetype_is_bad_request(upload_attachment: UploadAttachment) -> None:
    # Through the fixture so a wrongly accepted upload is still cleaned up.
    resp = upload_attachment(attachment_files(name="spec-audit.zip", mimetype="application/zip"))

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]


def test_upload_malformed_conversation_id_is_rejected_by_the_validator(
    upload_attachment: UploadAttachment,
) -> None:
    with outside_request_contract("conversationId breaks the documented pattern on purpose"):
        resp = upload_attachment(attachment_files(), conversation_id=MALFORMED_CONVERSATION_ID)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert validation_fields(resp) == ["body.conversationId"]


def test_upload_without_file_part_is_bad_request(
    conversations_audit_client: ConversationsAuditClient,
) -> None:
    resp = conversations_audit_client.upload_attachments(None)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def _too_many_files() -> MultipartFiles:
    return [
        ("files", (f"spec-audit-{i}.txt", io.BytesIO(b"x"), "text/plain")) for i in range(MAX_FILES + 1)
    ]


@pytest.mark.parametrize(
    "files",
    [
        pytest.param(
            attachment_files(content=b"\0" * (MAX_FILE_BYTES + 1)), id="file-over-5-mib"
        ),
        pytest.param(_too_many_files(), id="more-than-10-files"),
        pytest.param(
            [("attachment", ("spec-audit.txt", io.BytesIO(b"x"), "text/plain"))], id="file-under-another-field"
        ),
    ],
)
def test_upload_refused_by_multer_is_an_internal_error(
    upload_attachment: UploadAttachment, files: MultipartFiles
) -> None:
    # API bug: multer's limit errors are caller mistakes, yet they reach the
    # error middleware as unknown errors.
    resp = upload_attachment(files)
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
