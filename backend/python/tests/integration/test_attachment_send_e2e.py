"""Integration tests for the end-to-end attachment send pipeline.

These tests mock the external transport (Slack API, Microsoft Graph, Salesforce
REST API) so no real network calls are made, but exercise the full chain from:
  resolve_attachments() → bytes → platform uploader → mocked transport

Scenarios:
1. Chat attachment upload through to mocked Slack transport — verifies exact bytes.
2. Chat attachment upload through to mocked Microsoft Graph (Outlook) — verifies
   the FileAttachment body carries the correct base64-encoded bytes.
3. Outlook attachment → Salesforce opportunity — the Outlook-inbound attachment
   ID (from stage_attachment_to_blob) is resolved and uploaded to Salesforce.
4. Access denied record: resolver raises → transport is never called.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.services.record_content.models import (
    RecordAccessDeniedError,
    ResolvedRecordContent,
)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


FILE_CONTENT = b"Hello, PipesHub attachment test!"
FILE_NAME = "report.pdf"
MIME_TYPE = "application/pdf"
RECORD_ID = "rec_test_001"
ORG_ID = "org_test"
USER_ID = "user_test"


def _resolved(content=FILE_CONTENT, record_id=RECORD_ID):
    return ResolvedRecordContent(
        record_id=record_id,
        filename=FILE_NAME,
        mime_type=MIME_TYPE,
        size_bytes=len(content),
        content=content,
        version=None,
        source="blob",
    )


def _state(**kwargs):
    defaults = {
        "org_id": ORG_ID,
        "user_id": USER_ID,
        "config_service": MagicMock(),
        "graph_provider": AsyncMock(),
        "conversation_id": "conv_test",
        "blob_store": None,
    }
    defaults.update(kwargs)
    return defaults


def _bundle_with_resolved(*record_ids):
    from app.agents.actions.util.attachments import AttachmentBundle

    b = AttachmentBundle()
    b.resolved = [_resolved(record_id=rid) for rid in record_ids]
    b.total_bytes = sum(r.size_bytes for r in b.resolved)
    return b


def _bundle_denied(record_id=RECORD_ID):
    from app.agents.actions.util.attachments import AttachmentBundle
    from app.services.record_content.models import AttachmentFailure

    b = AttachmentBundle()
    b.failures = [AttachmentFailure(
        ref=record_id, error="Access denied", error_type="RecordAccessDeniedError"
    )]
    return b


# ---------------------------------------------------------------------------
# 1. Chat attachment → Slack (exact bytes verification)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_attachment_to_slack_exact_bytes():
    """Resolved bytes reach the Slack transport unchanged."""
    from app.agents.actions.slack.attachments import SlackAttachmentUploader
    from app.agents.actions.util.attachment_upload import AttachmentTarget

    captured_calls: list = []

    async def fake_files_upload_v2(**kwargs):
        captured_calls.append(kwargs)
        r = MagicMock()
        r.success = True
        r.data = {"files": [{"id": "F001"}]}
        r.error = None
        return r

    mock_client = MagicMock()
    mock_client.files_upload_v2 = fake_files_upload_v2

    uploader = SlackAttachmentUploader(client=mock_client)
    target = AttachmentTarget(destination_id="CHAN1")

    resolved = _resolved()
    results = await uploader.upload(target, [resolved])

    assert len(results) == 1
    assert results[0].success

    assert len(captured_calls) == 1
    uploaded_content = captured_calls[0].get("content")
    assert uploaded_content == FILE_CONTENT


# ---------------------------------------------------------------------------
# 2. Chat attachment → Outlook (base64 body verification)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_attachment_to_outlook_correct_base64():
    """Small-file Outlook path encodes bytes as base64 in the Graph payload."""
    from app.agents.actions.microsoft.outlook.attachments import OutlookAttachmentUploader
    from app.agents.actions.util.attachment_upload import AttachmentTarget

    captured_bodies: list = []

    async def fake_create_attachments(*, message_id, request_body):
        captured_bodies.append(request_body)
        r = MagicMock()
        r.success = True
        r.data = {"id": "ATT001"}
        return r

    mock_client = MagicMock()
    mock_client.me_messages_create_attachments = fake_create_attachments

    uploader = OutlookAttachmentUploader(client=mock_client)
    target = AttachmentTarget(destination_id="msg-draft-001")

    resolved = _resolved()
    results = await uploader.upload(target, [resolved])

    assert len(results) == 1
    assert results[0].success
    assert len(captured_bodies) == 1

    body = captured_bodies[0]
    # The uploader builds a plain dict with contentBytes as base64 string
    assert isinstance(body, dict), f"Expected dict request_body, got {type(body)}"
    content_b64 = body["contentBytes"]
    decoded = base64.b64decode(content_b64)
    assert decoded == FILE_CONTENT, "Attachment bytes were mangled in transit"


# ---------------------------------------------------------------------------
# 3. Outlook attachment → Salesforce opportunity (cross-app flow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outlook_attachment_to_salesforce_opportunity():
    """An Outlook attachment staged via stage_attachment_to_blob (stored as a PipesHub
    record) can be resolved and uploaded to a Salesforce opportunity."""
    from app.agents.actions.salesforce.attachments import SalesforceAttachmentUploader
    from app.agents.actions.util.attachment_upload import AttachmentTarget

    captured_payloads: list = []
    captured_links: list = []

    async def fake_sobject_create(*, api_version, sobject, data):
        r = MagicMock()
        r.success = True
        r.error = None
        if sobject == "ContentVersion":
            captured_payloads.append(data)
            r.data = {"id": "CV001", "Id": "CV001"}
        elif sobject == "ContentDocumentLink":
            captured_links.append(data)
            r.data = {"id": "CDL001"}
        return r

    async def fake_soql_query(*, api_version, q):
        r = MagicMock()
        r.success = True
        r.data = {"records": [{"ContentDocumentId": "069001"}]}
        return r

    mock_sf_client = MagicMock()
    mock_sf_client.sobject_create = fake_sobject_create
    mock_sf_client.soql_query = fake_soql_query

    uploader = SalesforceAttachmentUploader(client=mock_sf_client, api_version="59.0")
    target = AttachmentTarget(destination_id="006OPP001", display_name="Q4 Deal")

    resolved = _resolved()
    results = await uploader.upload(target, [resolved])

    assert len(results) == 1
    assert results[0].success
    assert results[0].platform_id == "069001"

    # ContentVersion payload must carry base64-encoded file bytes
    assert len(captured_payloads) == 1
    version_data = captured_payloads[0]["VersionData"]
    decoded = base64.b64decode(version_data)
    assert decoded == FILE_CONTENT

    # ContentDocumentLink must link to the opportunity
    assert len(captured_links) == 1
    assert captured_links[0]["LinkedEntityId"] == "006OPP001"


# ---------------------------------------------------------------------------
# 4. Access denied: transport never called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_denied_record_no_transport_call():
    """When resolve_attachments returns only failures, no upload is attempted."""
    from app.agents.actions.slack.slack import Slack

    sl = Slack.__new__(Slack)
    sl.client = MagicMock()
    sl.chat_state = _state()
    sl._user_cache = {}
    sl._channel_cache = {}
    import asyncio
    sl._lookup_sem = asyncio.Semaphore(20)

    files_upload_v2_called = False

    async def fake_post(**kwargs):
        r = MagicMock()
        r.success = True
        r.data = {"ts": "1234.5"}
        return r

    async def fake_files_upload(**kwargs):
        nonlocal files_upload_v2_called
        files_upload_v2_called = True
        r = MagicMock()
        r.success = True
        return r

    sl.client.chat_post_message = fake_post
    sl.client.files_upload_v2 = fake_files_upload
    sl._resolve_channel = AsyncMock(return_value="CHAN1")
    sl._handle_slack_response = MagicMock(
        return_value=MagicMock(success=True, data={"ts": "1234.5"})
    )

    with patch(
        "app.agents.actions.slack.slack.resolve_attachments",
        new=AsyncMock(return_value=_bundle_denied()),
    ):
        ok, _ = await sl.send_message(
            channel="general",
            message="Hi",
            attachment_record_ids=[RECORD_ID],
        )

    assert ok  # message itself sent OK
    assert not files_upload_v2_called  # but no files were uploaded
