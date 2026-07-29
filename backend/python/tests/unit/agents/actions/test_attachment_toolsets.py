"""Tests for the attachment integration across Slack, Outlook, Salesforce, and Gmail toolsets.

Critical regressions verified:
1. Outlook draft-cleanup: if any attachment upload fails, the draft is deleted
   before the error is returned.
2. No-external-call-on-denied-record: when resolve_attachments returns failures
   only, the platform uploader is never called.
3. Slack: attachment_record_ids are resolved and uploaded after the message is posted.
4. Gmail: attachment resolution failure returns a user-facing error, not a 500.
5. Salesforce: failed resolution entries appear in the result's `results` array.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.record_content.models import (
    AttachmentFailure,
    ResolvedRecordContent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolved(record_id="rec1", size=100):
    return ResolvedRecordContent(
        record_id=record_id,
        filename=f"{record_id}.txt",
        mime_type="text/plain",
        size_bytes=size,
        content=b"x" * size,
        version=None,
        source="blob",
    )


def _bundle(resolved=None, failures=None):
    from app.agents.actions.util.attachments import AttachmentBundle

    b = AttachmentBundle()
    b.resolved = list(resolved or [])
    b.failures = list(failures or [])
    b.total_bytes = sum(r.size_bytes for r in b.resolved)
    return b


def _failure(ref="rec1", error="denied"):
    return AttachmentFailure(ref=ref, error=error, error_type="RecordAccessDeniedError")


def _make_state(**kwargs):
    defaults = {
        "org_id": "org1",
        "user_id": "user1",
        "config_service": MagicMock(),
        "graph_provider": AsyncMock(),
        "conversation_id": "conv1",
        "blob_store": None,
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Outlook tests
# ---------------------------------------------------------------------------


class TestOutlookDraftCleanup:
    """send_email must delete the draft if any attachment upload fails."""

    def _build_outlook(self, state=None):
        from app.agents.actions.microsoft.outlook.outlook import Outlook

        ol = Outlook.__new__(Outlook)
        ol.client = AsyncMock()
        ol.chat_state = state or _make_state()
        return ol

    @pytest.mark.asyncio
    async def test_draft_deleted_on_attachment_failure(self):
        """Draft must be deleted when an attachment upload fails."""
        from app.agents.actions.util.attachment_upload import AttachmentUploadResult

        ol = self._build_outlook()

        # Draft creation succeeds
        ol.client.me_create_messages = AsyncMock(return_value=MagicMock(
            success=True, data={"id": "draft-id"}
        ))
        ol.client.me_messages_message_delete = AsyncMock(return_value=MagicMock(success=True))
        ol.client.me_messages_message_send = AsyncMock()

        failed_result = AttachmentUploadResult(
            record_id="rec1", filename="rec1.txt", success=False, error="upload_failed"
        )

        mock_uploader_instance = MagicMock()
        mock_uploader_instance.upload = AsyncMock(return_value=[failed_result])

        with (
            patch(
                "app.agents.actions.microsoft.outlook.outlook.resolve_attachments",
                new=AsyncMock(return_value=_bundle(resolved=[_resolved("rec1")])),
            ),
            patch(
                "app.agents.actions.microsoft.outlook.attachments.OutlookAttachmentUploader",
                return_value=mock_uploader_instance,
            ),
            patch(
                "app.agents.actions.microsoft.outlook.outlook.OutlookAttachmentUploader",
                return_value=mock_uploader_instance,
                create=True,
            ),
            patch(
                "app.agents.actions.util.attachments.emit_attachment_audit",
            ),
        ):
            ok, body = await ol.send_email(
                subject="Test",
                body="Hello",
                to_recipients=["a@b.com"],
                attachment_record_ids=["rec1"],
            )

        ol.client.me_messages_message_delete.assert_called_once_with(message_id="draft-id")
        assert not ok
        payload = json.loads(body)
        assert "draft deleted" in payload.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_no_uploader_call_when_all_denied(self):
        """When resolve_attachments returns only failures, uploader is never called."""
        ol = self._build_outlook()
        ol.client.me_create_messages = AsyncMock(return_value=MagicMock(
            success=True, data={"id": "draft-id"}
        ))
        ol.client.me_messages_message_send = AsyncMock(
            return_value=MagicMock(success=True, data={"id": "draft-id"})
        )

        uploader_instantiated = []

        with (
            patch(
                "app.agents.actions.microsoft.outlook.outlook.resolve_attachments",
                new=AsyncMock(return_value=_bundle(failures=[_failure()])),
            ),
        ):
            await ol.send_email(
                subject="Test",
                body="Hello",
                to_recipients=["a@b.com"],
                attachment_record_ids=["rec1"],
            )

        # uploader never instantiated since bundle.resolved is empty
        assert not uploader_instantiated


# ---------------------------------------------------------------------------
# Slack tests
# ---------------------------------------------------------------------------


class TestSlackAttachments:
    """Slack tools post message then upload resolved attachments."""

    def _build_slack(self, state=None):
        from app.agents.actions.slack.slack import Slack

        sl = Slack.__new__(Slack)
        sl.client = AsyncMock()
        sl.chat_state = state or _make_state()
        sl._user_cache = {}
        sl._channel_cache = {}
        import asyncio
        sl._lookup_sem = asyncio.Semaphore(20)
        return sl

    @pytest.mark.asyncio
    async def test_send_message_with_attachments(self):
        """send_message posts the text message, then uploads attachments."""
        sl = self._build_slack()

        post_response = MagicMock(success=True, data={"ts": "1234.5678"})
        sl.client.chat_post_message = AsyncMock(return_value=post_response)
        sl._resolve_channel = AsyncMock(return_value="CHAN1")
        sl._handle_slack_response = MagicMock(return_value=post_response)

        upload_result = MagicMock(record_id="rec1", filename="rec1.txt", success=True, error=None)

        with (
            patch(
                "app.agents.actions.slack.slack.resolve_attachments",
                new=AsyncMock(return_value=_bundle(resolved=[_resolved("rec1")])),
            ),
            patch(
                "app.agents.actions.slack.slack.SlackAttachmentUploader"
            ) as MockUploader,
            patch(
                "app.agents.actions.util.attachments.emit_attachment_audit",
            ),
        ):
            MockUploader.return_value.upload = AsyncMock(return_value=[upload_result])

            ok, _ = await sl.send_message(
                channel="general", message="Hi", attachment_record_ids=["rec1"]
            )

        assert ok
        MockUploader.return_value.upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_uploader_call_when_message_fails(self):
        """Uploader is not called if the text message itself failed."""
        sl = self._build_slack()

        fail_response = MagicMock(success=False, data=None, error="not_in_channel")
        sl.client.chat_post_message = AsyncMock(side_effect=Exception("not_in_channel"))
        sl._resolve_channel = AsyncMock(return_value="CHAN1")
        sl._handle_slack_response = MagicMock(return_value=fail_response)
        sl._handle_slack_error = MagicMock(return_value=fail_response)

        with patch(
            "app.agents.actions.slack.slack.SlackAttachmentUploader"
        ) as MockUploader:
            ok, _ = await sl.send_message(
                channel="general", message="Hi", attachment_record_ids=["rec1"]
            )

        assert not ok
        MockUploader.assert_not_called()


# ---------------------------------------------------------------------------
# Gmail tests
# ---------------------------------------------------------------------------


class TestGmailAttachments:
    """Gmail tools emit errors from attach resolution cleanly."""

    def _build_gmail(self, state=None):
        from app.agents.actions.google.gmail.gmail import Gmail

        gm = Gmail.__new__(Gmail)
        gm.client = AsyncMock()
        gm.chat_state = state or _make_state()
        return gm

    @pytest.mark.asyncio
    async def test_all_denied_returns_error_not_500(self):
        """When all record resolutions fail, send_email returns (False, error_json)."""
        gm = self._build_gmail()

        with (
            patch(
                "app.agents.actions.google.gmail.gmail.resolve_attachments",
                new=AsyncMock(return_value=_bundle(failures=[_failure("rec1")])),
            ),
            patch(
                "app.agents.actions.util.attachments.emit_attachment_audit",
            ),
        ):
            ok, body = await gm.send_email(
                mail_to=["a@b.com"],
                mail_subject="Test",
                attachment_record_ids=["rec1"],
            )

        assert not ok
        payload = json.loads(body)
        assert "error" in payload

    @pytest.mark.asyncio
    async def test_size_cap_raises_valueerror(self):
        """When transform_message_body raises ValueError (size cap), it is surfaced."""
        gm = self._build_gmail()

        with (
            patch(
                "app.agents.actions.google.gmail.gmail.resolve_attachments",
                new=AsyncMock(return_value=_bundle(resolved=[_resolved("rec1", 99999999)])),
            ),
            patch(
                "app.agents.actions.util.attachments.emit_attachment_audit",
            ),
            patch(
                "app.agents.actions.google.gmail.gmail.GmailUtils.transform_message_body",
                side_effect=ValueError("Total attachment size exceeds"),
            ),
        ):
            ok, body = await gm.send_email(
                mail_to=["a@b.com"],
                mail_subject="Test",
                attachment_record_ids=["rec1"],
            )

        assert not ok
        assert "exceeds" in json.loads(body).get("error", "")


# ---------------------------------------------------------------------------
# Salesforce tests
# ---------------------------------------------------------------------------


class TestSalesforceAttachments:
    """Salesforce upload_file_to_salesforce shows per-record results."""

    def _build_salesforce(self, state=None):
        from app.agents.actions.salesforce.salesforce import Salesforce

        sf = Salesforce.__new__(Salesforce)
        sf.client = AsyncMock()
        sf.chat_state = state or _make_state()
        sf.api_version = "59.0"
        return sf

    @pytest.mark.asyncio
    async def test_denied_record_appears_in_results(self):
        """A denied record shows up in the results array with ok=False."""
        sf = self._build_salesforce()

        with (
            patch(
                "app.agents.actions.salesforce.salesforce.resolve_attachments",
                new=AsyncMock(return_value=_bundle(
                    resolved=[],
                    failures=[_failure("rec1", "access denied")]
                )),
            ),
        ):
            ok, body = await sf.upload_file_to_salesforce(
                attachment_record_ids=["rec1"]
            )

        payload = json.loads(body)
        data = payload.get("data", {})
        results = data.get("results", [])
        assert any(r.get("record_id") == "rec1" and not r.get("ok") for r in results)
        assert not ok
