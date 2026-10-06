"""Strict OpenAPI audit of POST /api/v1/agents/:agentKey/conversations/attachments/upload."""

from __future__ import annotations

import io

import pytest
from agents_audit_support import (
    MALFORMED_CONVERSATION_ID,
    SEED_AGENT_KEY,
    UNSAFE_PATH_SEGMENT,
    AgentsAuditClient,
    MultipartFiles,
    SeedAgentConversation,
    UploadAttachment,
    attachment_files,
    uploaded_record_ids,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/:agentKey/conversations/attachments/upload"

FILE_NAME = "spec-audit.txt"


@pytest.mark.parametrize("with_conversation", [False, True], ids=["unlinked", "linked-to-conversation"])
def test_upload_text_attachment_returns_attachment_refs(
    upload_attachment: UploadAttachment,
    seed_agent_conversation: SeedAgentConversation,
    with_conversation: bool,
) -> None:
    conversation_id = seed_agent_conversation() if with_conversation else None

    resp = upload_attachment(attachment_files(name=FILE_NAME), conversation_id=conversation_id)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    attachments = resp.json()["attachments"]
    assert len(attachments) == 1, resp.text[:500]
    assert attachments[0]["recordName"] == FILE_NAME
    assert attachments[0]["mimeType"] == "text/plain"
    assert uploaded_record_ids(resp) == [attachments[0]["recordId"]]


def test_empty_conversation_id_is_read_as_unset(upload_attachment: UploadAttachment) -> None:
    resp = upload_attachment(conversation_id="")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("files", "conversation_id"),
    [
        pytest.param(
            attachment_files(name="spec-audit.zip", mimetype="application/zip"),
            None,
            id="unsupported-mimetype",
        ),
    ],
)
def test_upload_rejects_unsupported_file(
    upload_attachment: UploadAttachment,
    files: MultipartFiles,
    conversation_id: str | None,
) -> None:
    # Through the fixture so a wrongly accepted upload is still cleaned up.
    resp = upload_attachment(files, conversation_id=conversation_id)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "files",
    [
        pytest.param(
            [("files", (f"spec-audit-{i}.txt", io.BytesIO(b"x\n"), "text/plain")) for i in range(11)],
            id="eleven-files",
        ),
        pytest.param(
            attachment_files(content=b"x" * (5 * 1024 * 1024 + 1)), id="file-over-5-mib"
        ),
        pytest.param(
            [("attachment", ("spec-audit.txt", io.BytesIO(b"x\n"), "text/plain"))],
            id="file-part-not-named-files",
        ),
    ],
)
def test_multer_rejection_is_an_internal_error(
    upload_attachment: UploadAttachment, files: MultipartFiles
) -> None:
    # Multer's own errors reach the error middleware unmapped.
    resp = upload_attachment(files)

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_with_malformed_conversation_id_is_a_validation_error(
    upload_attachment: UploadAttachment,
) -> None:
    # The checker reads JSON bodies only; the spec's multipart `conversationId`
    # pattern is what forbids this value.
    with outside_request_contract("a malformed conversationId is sent in a multipart body"):
        resp = upload_attachment(attachment_files(), conversation_id=MALFORMED_CONVERSATION_ID)
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]


def test_upload_without_file_part_is_bad_request(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.upload(SEED_AGENT_KEY, None)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_with_unsafe_agent_key_is_rejected(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.upload(UNSAFE_PATH_SEGMENT, attachment_files())

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_without_token_is_unauthorized(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.upload(SEED_AGENT_KEY, attachment_files(), auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_without_agent_execute_scope_is_forbidden(
    agents_audit_client: AgentsAuditClient,
    narrow_scope_headers: dict[str, str],
) -> None:
    headers = {"Authorization": narrow_scope_headers["Authorization"]}
    resp = agents_audit_client.upload(SEED_AGENT_KEY, attachment_files(), auth=False, headers=headers)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
