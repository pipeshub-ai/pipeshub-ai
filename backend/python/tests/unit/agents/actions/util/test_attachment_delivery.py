"""Tests for app.agents.actions.util.attachment_delivery."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.actions.util.attachment_delivery import (
    DeliveryReport,
    PreparedAttachments,
    deliver_attachments,
    prepare_attachments,
    replace_placeholders,
    split_on_placeholders,
    strip_placeholders,
    wants_inline,
)
from app.agents.actions.util.attachment_upload import (
    AttachmentTarget,
    AttachmentUploadResult,
)
from app.agents.actions.util.attachments import AttachmentBundle
from app.core.org_context import OrgContext, bind
from app.services.record_content.models import AttachmentFailure, ResolvedRecordContent

_RESOLVE = "app.agents.actions.util.attachment_delivery.resolve_attachments"
_AUDIT = "app.agents.actions.util.attachment_delivery.emit_attachment_audit"


def _resolved(record_id="rec1", filename=None, mime="image/png", size=10) -> ResolvedRecordContent:
    return ResolvedRecordContent(
        record_id=record_id,
        filename=filename or f"{record_id}.png",
        mime_type=mime,
        size_bytes=size,
        content=b"x" * size,
        version=None,
        source="blob",
    )


def _bundle(resolved=(), failures=()) -> AttachmentBundle:
    bundle = AttachmentBundle()
    bundle.resolved = list(resolved)
    bundle.failures = list(failures)
    bundle.total_bytes = sum(r.size_bytes for r in bundle.resolved)
    return bundle


def _ok(record_id, filename=None, url=None, stored=None, mime="image/png") -> AttachmentUploadResult:
    return AttachmentUploadResult(
        record_id=record_id,
        filename=filename or f"{record_id}.png",
        success=True,
        platform_id=f"att-{record_id}",
        platform_url=url,
        stored_filename=stored,
        mime_type=mime,
    )


class _Uploader:
    max_single_bytes = 1000
    max_total_bytes = 5000

    def __init__(self, results=None) -> None:
        self.upload = AsyncMock(return_value=results or [])


class _PreflightUploader(_Uploader):
    def __init__(self, reason=None) -> None:
        super().__init__()
        self.preflight = AsyncMock(return_value=reason)


STATE = {"org_id": "org1", "user_id": "user1"}


class TestPrepareAttachments:
    @pytest.mark.asyncio
    async def test_no_refs_is_inactive_and_resolves_nothing(self) -> None:
        with patch(_RESOLVE, new=AsyncMock()) as resolve:
            prepared = await prepare_attachments(STATE, None, _Uploader())
        assert not prepared.active
        assert prepared.abort_reason is None
        resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolves_within_uploader_limits(self) -> None:
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))) as resolve:
            prepared = await prepare_attachments(STATE, ["rec1"], _Uploader())
        assert prepared.active and prepared.abort_reason is None
        assert resolve.await_args.kwargs == {"max_total_bytes": 5000, "max_single_bytes": 1000}

    @pytest.mark.asyncio
    async def test_aborts_when_nothing_resolves(self) -> None:
        bundle = _bundle(failures=[AttachmentFailure("rec1", "denied", "RecordAccessDeniedError")])
        with patch(_RESOLVE, new=AsyncMock(return_value=bundle)):
            prepared = await prepare_attachments(STATE, ["rec1"], _Uploader())
        assert prepared.abort_reason
        payload = json.loads(prepared.abort_payload())
        assert payload["attachment_failures"][0]["ref"] == "rec1"
        assert "Nothing was created" in payload["guidance"]

    @pytest.mark.asyncio
    async def test_partial_resolution_does_not_abort(self) -> None:
        bundle = _bundle([_resolved("a")], [AttachmentFailure("b", "missing", "RecordNotFoundError")])
        with patch(_RESOLVE, new=AsyncMock(return_value=bundle)):
            prepared = await prepare_attachments(STATE, ["a", "b"], _Uploader())
        assert prepared.abort_reason is None

    @pytest.mark.asyncio
    async def test_preflight_failure_aborts_before_resolving(self) -> None:
        uploader = _PreflightUploader(reason="Attachments are turned off on this Jira site.")
        with patch(_RESOLVE, new=AsyncMock()) as resolve:
            prepared = await prepare_attachments(STATE, ["rec1"], uploader)
        assert prepared.abort_reason == "Attachments are turned off on this Jira site."
        resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preflight_ok_continues(self) -> None:
        uploader = _PreflightUploader(reason=None)
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            prepared = await prepare_attachments(STATE, ["rec1"], uploader)
        uploader.preflight.assert_awaited_once()
        assert prepared.abort_reason is None


class TestDeliverAttachments:
    @pytest.mark.asyncio
    async def test_uploads_and_audits_each_file(self) -> None:
        prepared = PreparedAttachments(requested=2, bundle=_bundle([_resolved("a"), _resolved("b", size=20)]))
        failed = AttachmentUploadResult(record_id="b", filename="b.png", success=False, error="HTTP 413")
        uploader = _Uploader([_ok("a"), failed])

        with patch(_AUDIT) as audit, bind(OrgContext("org1", "user1", "user", "jwt", "req-test")):
            report = await deliver_attachments(
                STATE, prepared, uploader, AttachmentTarget(destination_id="KAN-1"), target_app="jira"
            )

        assert [r.record_id for r in report.uploaded] == ["a"]
        assert report.failed == [{"ref": "b", "filename": "b.png", "error": "HTTP 413"}]
        assert audit.call_count == 2
        first = audit.call_args_list[0].kwargs
        assert first["target_app"] == "jira"
        assert first["destination"] == "KAN-1"
        assert first["org_id"] == "org1" and first["user_id"] == "user1"
        assert audit.call_args_list[1].kwargs["size_bytes"] == 20

    @pytest.mark.asyncio
    async def test_audit_takes_the_org_from_the_request_not_the_chat_state(self) -> None:
        prepared = PreparedAttachments(requested=1, bundle=_bundle([_resolved("a")]))
        with patch(_AUDIT) as audit:
            report = await deliver_attachments(
                {"org_id": "org-from-state", "user_id": "user1"}, prepared, _Uploader([_ok("a")]),
                AttachmentTarget(destination_id="KAN-1"), target_app="jira",
            )
        assert [r.record_id for r in report.uploaded] == ["a"]
        assert audit.call_args.kwargs["org_id"] == ""

    @pytest.mark.asyncio
    async def test_resolution_failures_are_reported(self) -> None:
        prepared = PreparedAttachments(
            requested=2,
            bundle=_bundle([_resolved("a")], [AttachmentFailure("b", "denied", "RecordAccessDeniedError")]),
        )
        with patch(_AUDIT):
            report = await deliver_attachments(
                STATE, prepared, _Uploader([_ok("a")]), AttachmentTarget(destination_id="1"), target_app="x"
            )
        assert report.failed == [{"ref": "b", "error": "denied", "error_type": "RecordAccessDeniedError"}]
        assert report.partial

    @pytest.mark.asyncio
    async def test_nothing_resolved_skips_upload(self) -> None:
        uploader = _Uploader()
        report = await deliver_attachments(
            STATE, PreparedAttachments(requested=1), uploader, AttachmentTarget(destination_id="1"), target_app="x"
        )
        uploader.upload.assert_not_awaited()
        assert report.uploaded == []


class TestDeliveryReport:
    def test_merge_keeps_existing_keys_and_adds_outcome(self) -> None:
        report = DeliveryReport(uploaded=[_ok("a", url="https://x/att/1")])
        merged = json.loads(report.merge_into(json.dumps({"message": "Issue created successfully", "data": {"key": "KAN-1"}})))
        assert merged["data"] == {"key": "KAN-1"}
        assert merged["attachments"]["uploaded"][0]["url"] == "https://x/att/1"
        assert "partial" not in merged
        assert merged["message"] == "Issue created successfully. 1 of 1 file(s) attached"

    def test_merge_marks_partial(self) -> None:
        report = DeliveryReport(uploaded=[_ok("a")], failed=[{"ref": "b", "filename": "b.pdf", "error": "too large"}])
        merged = json.loads(report.merge_into(json.dumps({"message": "Page created"})))
        assert merged["partial"] is True
        assert "Not attached: b.pdf: too large" in merged["message"]

    def test_merge_tolerates_non_json(self) -> None:
        merged = json.loads(DeliveryReport().merge_into("plain text"))
        assert merged["result"] == "plain text"


class TestPlaceholders:
    def test_wants_inline(self) -> None:
        assert wants_inline("inline") and wants_inline(" Inline ")
        assert not wants_inline(None) and not wants_inline("attachment")

    def test_strip(self) -> None:
        assert strip_placeholders("a {{attachment:rec1}} b {{ attachment : x.png }}") == "a  b "

    def test_split(self) -> None:
        assert split_on_placeholders("a{{attachment:r1}}b") == [("a", "r1"), ("b", None)]

    def test_replace_by_record_id_and_filename_then_append_rest(self) -> None:
        report = DeliveryReport(uploaded=[_ok("r1", "one.png"), _ok("r2", "two.png"), _ok("r3", "three.png")])
        body = replace_placeholders(
            "<p>{{attachment:two.png}}</p><p>{{attachment:r1}}</p>",
            report,
            lambda r: f"[{r.display_name}]",
            separator="\n",
        )
        assert body == "<p>[two.png]</p><p>[one.png]</p>\n[three.png]"

    def test_unknown_placeholder_removed_and_file_placed_once(self) -> None:
        report = DeliveryReport(uploaded=[_ok("r1", "one.png")])
        body = replace_placeholders(
            "{{attachment:gone}}x{{attachment:r1}}y{{attachment:one.png}}",
            report,
            lambda r: "[IMG]",
        )
        assert body == "x[IMG]y"

    def test_render_uses_stored_name(self) -> None:
        report = DeliveryReport(uploaded=[_ok("r1", "d.png", stored="d (2).png")])
        assert replace_placeholders("", report, lambda r: r.display_name) == "d (2).png"


class TestUploadResultCompat:
    def test_to_dict_unchanged_for_existing_uploaders(self) -> None:
        result = AttachmentUploadResult(record_id="r", filename="f", success=True, platform_id="F1")
        assert result.to_dict() == {"record_id": "r", "filename": "f", "success": True, "platform_id": "F1"}

    def test_is_image(self) -> None:
        assert _ok("r").is_image
        assert not _ok("r", mime="application/pdf").is_image
        assert not AttachmentUploadResult(record_id="r", filename="f", success=True).is_image
