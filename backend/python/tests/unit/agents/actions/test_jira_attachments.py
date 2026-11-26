"""Attaching PipesHub records to Jira issues (PP-434).

Covers the uploader, the description/comment rendering, and the Jira Cloud tools that
accept attachment_record_ids. Calls without attachment_record_ids must send exactly the
requests they sent before this feature.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.agents.actions.jira.attachments import (
    JiraAttachmentUploader,
    JiraIssueAttachments,
    adf_append_attachment_links,
    adf_with_attachment_links,
    wiki_with_attachments,
)
from app.agents.actions.jira.jira import Jira
from app.agents.actions.jira_data_center.jira_data_center import JiraDataCenter
from app.agents.actions.util.attachment_delivery import DeliveryReport
from app.agents.actions.util.attachment_upload import (
    AttachmentTarget,
    AttachmentUploadResult,
)
from app.agents.actions.util.attachments import AttachmentBundle
from app.services.record_content.models import AttachmentFailure, ResolvedRecordContent

if TYPE_CHECKING:
    from collections.abc import Iterator

_RESOLVE = "app.agents.actions.util.attachment_delivery.resolve_attachments"
_SLEEP = "app.agents.actions.util.attachment_delivery.asyncio.sleep"

STATE = {"org_id": "org1", "user_id": "user1", "conversation_id": "conv1"}


def _resp(status: int, body=None, headers=None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.is_json = True
    resp.headers = headers or {}
    resp.json = MagicMock(return_value=body if body is not None else {})
    resp.text = MagicMock(return_value=json.dumps(body) if body is not None else "")
    return resp


def _resolved(record_id="rec1", filename="bug.png", mime="image/png", size=4) -> ResolvedRecordContent:
    return ResolvedRecordContent(
        record_id=record_id, filename=filename, mime_type=mime, size_bytes=size,
        content=b"x" * size, version=None, source="blob",
    )


def _bundle(resolved=(), failures=()) -> AttachmentBundle:
    bundle = AttachmentBundle()
    bundle.resolved = list(resolved)
    bundle.failures = list(failures)
    return bundle


def _uploaded(record_id="rec1", filename="bug.png", url="https://acme.atlassian.net/rest/api/3/attachment/content/10", mime="image/png") -> AttachmentUploadResult:
    return AttachmentUploadResult(
        record_id=record_id, filename=filename, success=True, platform_id="10", platform_url=url, mime_type=mime,
    )


def _datasource(**methods) -> MagicMock:
    ds = MagicMock()
    ds.get_attachment_settings = AsyncMock(return_value=_resp(200, {"enabled": True, "uploadLimit": 10_485_760}))
    ds.upload_issue_attachment = AsyncMock(
        return_value=_resp(200, [{"id": "10", "filename": "bug.png", "content": "https://acme.atlassian.net/rest/api/3/attachment/content/10"}])
    )
    for name, value in methods.items():
        setattr(ds, name, value)
    return ds


def _jira(**methods) -> Jira:
    jira = Jira.__new__(Jira)
    jira.client = _datasource(**methods)
    jira._site_url = None
    jira._field_schema_cache = None
    jira._create_fields_cache = {}
    jira.chat_state = STATE
    jira._attachments = JiraIssueAttachments(jira.client, STATE, flavor="cloud")
    return jira


# ---------------------------------------------------------------------------
# Uploader
# ---------------------------------------------------------------------------


class TestJiraAttachmentUploader:
    @pytest.mark.asyncio
    async def test_preflight_adopts_site_limit_once(self) -> None:
        ds = _datasource()
        uploader = JiraAttachmentUploader(ds)
        assert await uploader.preflight() is None
        assert await uploader.preflight() is None
        assert uploader.max_single_bytes == 10_485_760
        ds.get_attachment_settings.assert_awaited_once_with(api_version="3")

    @pytest.mark.asyncio
    async def test_preflight_attachments_disabled(self) -> None:
        ds = _datasource(get_attachment_settings=AsyncMock(return_value=_resp(200, {"enabled": False})))
        assert await JiraAttachmentUploader(ds).preflight() == "Attachments are turned off on this Jira site."

    @pytest.mark.asyncio
    @pytest.mark.parametrize("settings", [_resp(403), RuntimeError("down"), _resp(200, ["not", "a", "dict"])])
    async def test_preflight_unknown_does_not_block(self, settings) -> None:
        mock = AsyncMock(side_effect=settings) if isinstance(settings, Exception) else AsyncMock(return_value=settings)
        assert await JiraAttachmentUploader(_datasource(get_attachment_settings=mock)).preflight() is None

    @pytest.mark.asyncio
    async def test_upload_success(self) -> None:
        ds = _datasource()
        results = await JiraAttachmentUploader(ds).upload(AttachmentTarget(destination_id="KAN-1"), [_resolved()])
        assert results[0].success
        assert results[0].platform_id == "10"
        assert results[0].platform_url.endswith("/attachment/content/10")
        assert results[0].stored_filename is None
        ds.upload_issue_attachment.assert_awaited_once_with("KAN-1", "bug.png", b"xxxx", "image/png", api_version="3")

    @pytest.mark.asyncio
    async def test_one_request_per_file_and_failures_are_per_file(self) -> None:
        ds = _datasource(upload_issue_attachment=AsyncMock(side_effect=[
            _resp(200, [{"id": "1", "filename": "a.png"}]),
            _resp(403, {"errorMessages": ["You do not have permission to create attachments for this issue."]}),
        ]))
        results = await JiraAttachmentUploader(ds).upload(
            AttachmentTarget(destination_id="KAN-1"), [_resolved("a", "a.png"), _resolved("b", "b.png")]
        )
        assert [r.success for r in results] == [True, False]
        assert "Create Attachments permission" in results[1].error
        assert "You do not have permission" in results[1].error

    @pytest.mark.asyncio
    async def test_413_message(self) -> None:
        ds = _datasource(upload_issue_attachment=AsyncMock(return_value=_resp(413)))
        results = await JiraAttachmentUploader(ds).upload(AttachmentTarget(destination_id="KAN-1"), [_resolved()])
        assert results[0].error == "The file is larger than this Jira site allows."

    @pytest.mark.asyncio
    async def test_retries_only_on_429(self) -> None:
        ds = _datasource(upload_issue_attachment=AsyncMock(side_effect=[
            _resp(429, headers={"Retry-After": "1"}),
            _resp(200, [{"id": "1", "filename": "bug.png"}]),
        ]))
        with patch(_SLEEP, new=AsyncMock()) as sleep:
            results = await JiraAttachmentUploader(ds).upload(AttachmentTarget(destination_id="KAN-1"), [_resolved()])
        assert results[0].success
        sleep.assert_awaited_once_with(1.0)

    @pytest.mark.asyncio
    async def test_server_error_is_not_retried(self) -> None:
        ds = _datasource(upload_issue_attachment=AsyncMock(return_value=_resp(500)))
        results = await JiraAttachmentUploader(ds).upload(AttachmentTarget(destination_id="KAN-1"), [_resolved()])
        assert not results[0].success
        ds.upload_issue_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_is_not_retried(self) -> None:
        ds = _datasource(upload_issue_attachment=AsyncMock(side_effect=httpx.ReadTimeout("slow")))
        results = await JiraAttachmentUploader(ds).upload(AttachmentTarget(destination_id="KAN-1"), [_resolved()])
        assert "did not answer in time" in results[0].error
        ds.upload_issue_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_data_center_uses_v2_and_wiki_safe_name(self) -> None:
        ds = _datasource(upload_issue_attachment=AsyncMock(return_value=_resp(200, [{"id": "1", "filename": "a_b_.png"}])))
        results = await JiraAttachmentUploader(ds, api_version="2").upload(
            AttachmentTarget(destination_id="OPS-1"), [_resolved(filename="a|b!.png")]
        )
        assert ds.upload_issue_attachment.await_args.args[1] == "a_b_.png"
        assert ds.upload_issue_attachment.await_args.kwargs == {"api_version": "2"}
        assert results[0].stored_filename == "a_b_.png"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRendering:
    def test_adf_placeholder_becomes_link_and_rest_listed(self) -> None:
        report = DeliveryReport(uploaded=[_uploaded("r1", "one.png"), _uploaded("r2", "two.pdf", url=None)])
        doc = adf_with_attachment_links("Steps: {{attachment:r1}} done", report)
        first, second = doc["content"]
        assert [n["text"] for n in first["content"]] == ["Steps: ", "one.png", " done"]
        assert first["content"][1]["marks"][0]["attrs"]["href"].endswith("/content/10")
        assert second["content"][0]["text"] == "Attachments: "
        assert second["content"][1] == {"type": "text", "text": "two.pdf"}

    def test_adf_empty_text_only_lists_files(self) -> None:
        doc = adf_with_attachment_links("", DeliveryReport(uploaded=[_uploaded()]))
        assert len(doc["content"]) == 1
        assert doc["content"][0]["content"][0]["text"] == "Attachments: "

    def test_adf_append_keeps_existing_content(self) -> None:
        existing = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "old"}]}]}
        doc = adf_append_attachment_links(existing, DeliveryReport(uploaded=[_uploaded()]))
        assert doc["content"][0]["content"][0]["text"] == "old"
        assert doc["content"][1]["content"][0]["text"] == "Attachments: "
        assert len(existing["content"]) == 1

    def test_wiki_images_inline_and_files_linked(self) -> None:
        report = DeliveryReport(uploaded=[_uploaded("r1", "s.png"), _uploaded("r2", "log.txt", mime="text/plain")])
        assert wiki_with_attachments("Before {{attachment:s.png}}", report) == "Before !s.png|thumbnail!\n[^log.txt]"


# ---------------------------------------------------------------------------
# Jira Cloud tools
# ---------------------------------------------------------------------------


@pytest.fixture
def site_url() -> Iterator[None]:
    with patch.object(Jira, "_get_site_url", AsyncMock(return_value="https://acme.atlassian.net")):
        yield


class TestBackwardCompatibility:
    def test_constructor_with_and_without_state(self) -> None:
        client = MagicMock()
        client.get_client.return_value = MagicMock(get_base_url=MagicMock(return_value="https://jira.test"))
        assert Jira(client).chat_state is None
        assert Jira(client, state=STATE).chat_state is STATE

    @pytest.mark.asyncio
    async def test_create_issue_without_attachments_sends_the_same_request(self, site_url) -> None:
        jira = _jira(create_issue=AsyncMock(return_value=_resp(201, {"key": "KAN-1"})))
        ok, _ = await jira.create_issue("KAN", "Bug", "Bug", description="Broken {{attachment:x}}")
        assert ok
        fields = jira.client.create_issue.await_args.kwargs["fields"]
        assert fields["description"]["content"][0]["content"][0]["text"] == "Broken {{attachment:x}}"
        jira.client.get_attachment_settings.assert_not_awaited()
        jira.client.upload_issue_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_comment_without_attachments_unchanged(self) -> None:
        jira = _jira(add_comment=AsyncMock(return_value=_resp(201, {"id": "c1"})))
        ok, _ = await jira.add_comment("KAN-1", "hello")
        assert ok
        body = jira.client.add_comment.await_args.kwargs["body_body"]
        assert body["content"][0]["content"][0]["text"] == "hello"


class TestCreateIssueWithAttachments:
    @pytest.mark.asyncio
    async def test_creates_then_attaches(self, site_url) -> None:
        jira = _jira(create_issue=AsyncMock(return_value=_resp(201, {"key": "KAN-1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.create_issue("KAN", "Bug", "Bug", description="See {{attachment:rec1}}", attachment_record_ids=["rec1"])
        data = json.loads(payload)
        assert ok
        assert data["data"]["key"] == "KAN-1"
        assert data["attachments"]["uploaded"][0]["record_id"] == "rec1"
        assert "partial" not in data
        sent = jira.client.create_issue.await_args.kwargs["fields"]["description"]
        assert sent["content"][0]["content"][0]["text"] == "See "
        assert jira.client.upload_issue_attachment.await_args.args[0] == "KAN-1"

    @pytest.mark.asyncio
    async def test_nothing_created_when_no_file_resolves(self, site_url) -> None:
        jira = _jira(create_issue=AsyncMock())
        denied = _bundle(failures=[AttachmentFailure("rec1", "denied", "RecordAccessDeniedError")])
        with patch(_RESOLVE, new=AsyncMock(return_value=denied)):
            ok, payload = await jira.create_issue("KAN", "Bug", "Bug", attachment_record_ids=["rec1"])
        assert not ok
        assert "Nothing was created" in json.loads(payload)["guidance"]
        jira.client.create_issue.assert_not_awaited()
        jira.client.upload_issue_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_created_when_attachments_disabled(self, site_url) -> None:
        jira = _jira(
            create_issue=AsyncMock(),
            get_attachment_settings=AsyncMock(return_value=_resp(200, {"enabled": False})),
        )
        with patch(_RESOLVE, new=AsyncMock()) as resolve:
            ok, payload = await jira.create_issue("KAN", "Bug", "Bug", attachment_record_ids=["rec1"])
        assert not ok
        assert "turned off" in json.loads(payload)["error"]
        resolve.assert_not_awaited()
        jira.client.create_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_upload_keeps_success_with_partial(self, site_url) -> None:
        jira = _jira(
            create_issue=AsyncMock(return_value=_resp(201, {"key": "KAN-1"})),
            upload_issue_attachment=AsyncMock(return_value=_resp(413)),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.create_issue("KAN", "Bug", "Bug", attachment_record_ids=["rec1"])
        data = json.loads(payload)
        assert ok
        assert data["partial"] is True
        assert data["attachments"]["failed"][0]["error"] == "The file is larger than this Jira site allows."
        assert "0 of 1 file(s) attached" in data["message"]

    @pytest.mark.asyncio
    async def test_create_failure_skips_upload(self, site_url) -> None:
        jira = _jira(create_issue=AsyncMock(return_value=_resp(403, {"errorMessages": ["no"]})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await jira.create_issue("KAN", "Bug", "Bug", attachment_record_ids=["rec1"])
        assert not ok
        jira.client.upload_issue_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inline_puts_links_in_description(self, site_url) -> None:
        jira = _jira(
            create_issue=AsyncMock(return_value=_resp(201, {"key": "KAN-1"})),
            edit_issue=AsyncMock(return_value=_resp(204)),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.create_issue(
                "KAN", "Bug", "Bug", description="Look {{attachment:bug.png}}",
                attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok and "warning" not in json.loads(payload)
        description = jira.client.edit_issue.await_args.kwargs["fields"]["description"]
        nodes = description["content"][0]["content"]
        assert nodes[0]["text"] == "Look "
        assert nodes[1]["text"] == "bug.png" and nodes[1]["marks"][0]["type"] == "link"

    @pytest.mark.asyncio
    async def test_inline_edit_failure_is_a_warning(self, site_url) -> None:
        jira = _jira(
            create_issue=AsyncMock(return_value=_resp(201, {"key": "KAN-1"})),
            edit_issue=AsyncMock(return_value=_resp(400)),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.create_issue(
                "KAN", "Bug", "Bug", attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok
        assert "could not be added to the description" in json.loads(payload)["warning"]

    @pytest.mark.asyncio
    async def test_audit_names_jira(self, site_url) -> None:
        jira = _jira(create_issue=AsyncMock(return_value=_resp(201, {"key": "KAN-1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))), \
             patch("app.agents.actions.util.attachment_delivery.emit_attachment_audit") as audit:
            await jira.create_issue("KAN", "Bug", "Bug", attachment_record_ids=["rec1"])
        assert audit.call_args.kwargs["target_app"] == "jira"
        assert audit.call_args.kwargs["destination"] == "KAN-1"


class TestUpdateAndAttach:
    @pytest.mark.asyncio
    async def test_attach_only_skips_the_edit(self) -> None:
        jira = _jira(edit_issue=AsyncMock(), get_issue=AsyncMock())
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_attachments("KAN-1", ["rec1"])
        assert ok
        assert json.loads(payload)["attachments"]["uploaded"][0]["platform_id"] == "10"
        jira.client.edit_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attach_only_all_uploads_failed_is_failure(self) -> None:
        jira = _jira(upload_issue_attachment=AsyncMock(return_value=_resp(404)))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_attachments("KAN-404", ["rec1"])
        assert not ok
        assert "couldn't find the issue" in json.loads(payload)["attachments"]["failed"][0]["error"]

    @pytest.mark.asyncio
    async def test_inline_without_new_description_appends(self) -> None:
        current = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "keep me"}]}]}
        jira = _jira(
            get_issue=AsyncMock(return_value=_resp(200, {"fields": {"description": current}})),
            edit_issue=AsyncMock(return_value=_resp(204)),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await jira.add_attachments("KAN-1", ["rec1"], attachment_display="inline")
        assert ok
        description = jira.client.edit_issue.await_args.kwargs["fields"]["description"]
        assert description["content"][0]["content"][0]["text"] == "keep me"
        assert description["content"][1]["content"][0]["text"] == "Attachments: "

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read", [_resp(503), _resp(200, {"fields": None}), RuntimeError("down")])
    async def test_unreadable_description_is_never_overwritten(self, read) -> None:
        get_issue = AsyncMock(side_effect=read) if isinstance(read, Exception) else AsyncMock(return_value=read)
        jira = _jira(get_issue=get_issue, edit_issue=AsyncMock(return_value=_resp(204)))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_attachments("KAN-1", ["rec1"], attachment_display="inline")
        assert ok
        jira.client.edit_issue.assert_not_awaited()
        data = json.loads(payload)
        assert data["attachments"]["uploaded"][0]["record_id"] == "rec1"
        assert "could not be added to the description" in data["warning"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("description", ["{{attachment:rec1}}", "  ", ""])
    async def test_placeholder_only_description_still_sends_the_files(self, description) -> None:
        jira = _jira(edit_issue=AsyncMock(return_value=_resp(204)), get_issue=AsyncMock())
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.update_issue("KAN-1", description=description, attachment_record_ids=["rec1"])
        assert ok
        jira.client.upload_issue_attachment.assert_awaited_once()
        jira.client.edit_issue.assert_not_awaited()
        assert json.loads(payload)["attachments"]["uploaded"][0]["record_id"] == "rec1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("empty", [{"summary": ""}, {"labels": []}, {"components": []}, {"custom_fields": {}}])
    async def test_empty_field_values_still_send_the_files(self, empty) -> None:
        jira = _jira(edit_issue=AsyncMock(return_value=_resp(204)), get_issue=AsyncMock())
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.update_issue("KAN-1", attachment_record_ids=["rec1"], **empty)
        assert ok
        jira.client.upload_issue_attachment.assert_awaited_once()
        jira.client.edit_issue.assert_not_awaited()
        assert json.loads(payload)["attachments"]["uploaded"][0]["record_id"] == "rec1"

    @pytest.mark.asyncio
    async def test_placeholder_only_description_inline_appends_to_the_current_one(self) -> None:
        current = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "keep me"}]}]}
        jira = _jira(
            get_issue=AsyncMock(return_value=_resp(200, {"fields": {"description": current}})),
            edit_issue=AsyncMock(return_value=_resp(204)),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await jira.update_issue(
                "KAN-1", description="{{attachment:rec1}}", attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok
        jira.client.edit_issue.assert_awaited_once()
        description = jira.client.edit_issue.await_args.kwargs["fields"]["description"]
        assert description["content"][0]["content"][0]["text"] == "keep me"


class TestCommentWithAttachments:
    @pytest.mark.asyncio
    async def test_files_go_on_issue_and_comment_links_them(self) -> None:
        jira = _jira(add_comment=AsyncMock(return_value=_resp(201, {"id": "c1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_comment(
                "KAN-1", "Repro: {{attachment:rec1}}", attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok
        body = jira.client.add_comment.await_args.kwargs["body_body"]
        assert body["content"][0]["content"][1]["text"] == "bug.png"
        assert json.loads(payload)["attachments"]["uploaded"]

    @pytest.mark.asyncio
    async def test_default_display_strips_placeholders(self) -> None:
        jira = _jira(add_comment=AsyncMock(return_value=_resp(201, {"id": "c1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            await jira.add_comment("KAN-1", "Repro: {{attachment:rec1}}", attachment_record_ids=["rec1"])
        body = jira.client.add_comment.await_args.kwargs["body_body"]
        assert body["content"][0]["content"][0]["text"] == "Repro: "

    @pytest.mark.asyncio
    @pytest.mark.parametrize("comment", ["", "   ", "{{attachment:rec1}}"])
    async def test_empty_comment_is_refused_before_anything_is_uploaded(self, comment) -> None:
        jira = _jira(add_comment=AsyncMock(return_value=_resp(201, {"id": "c1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_comment("KAN-1", comment, attachment_record_ids=["rec1"])
        assert not ok
        assert "add_attachments" in json.loads(payload)["guidance"]
        jira.client.upload_issue_attachment.assert_not_awaited()
        jira.client.add_comment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inline_comment_may_be_only_the_files(self) -> None:
        jira = _jira(add_comment=AsyncMock(return_value=_resp(201, {"id": "c1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await jira.add_comment("KAN-1", "", attachment_record_ids=["rec1"], attachment_display="inline")
        assert ok
        body = jira.client.add_comment.await_args.kwargs["body_body"]
        assert body["content"][0]["content"][1]["text"] == "bug.png"


class TestOAuthLinks:
    _GATEWAY_URL = "https://api.atlassian.com/ex/jira/cloud-1/rest/api/3/attachment/content/10"

    def _jira_over_oauth(self, **methods) -> Jira:
        jira = _jira(
            upload_issue_attachment=AsyncMock(
                return_value=_resp(200, [{"id": "10", "filename": "bug.png", "content": self._GATEWAY_URL}])
            ),
            **methods,
        )
        jira._attachments = JiraIssueAttachments(
            jira.client, STATE, flavor="cloud", site_url=AsyncMock(return_value="https://acme.atlassian.net"),
        )
        return jira

    @pytest.mark.asyncio
    async def test_gateway_link_points_at_the_site(self) -> None:
        jira = self._jira_over_oauth(add_comment=AsyncMock(return_value=_resp(201, {"id": "c1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_comment(
                "KAN-1", "Repro: {{attachment:rec1}}", attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok
        site_link = "https://acme.atlassian.net/rest/api/3/attachment/content/10"
        link = jira.client.add_comment.await_args.kwargs["body_body"]["content"][0]["content"][1]["marks"][0]
        assert link["attrs"]["href"] == site_link
        assert json.loads(payload)["attachments"]["uploaded"][0]["url"] == site_link

    @pytest.mark.asyncio
    @pytest.mark.parametrize("lookup", [AsyncMock(return_value=None), AsyncMock(side_effect=RuntimeError("down"))])
    async def test_gateway_link_kept_when_the_site_is_unknown(self, lookup) -> None:
        jira = self._jira_over_oauth()
        jira._attachments._site_url_lookup = lookup
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_attachments("KAN-1", ["rec1"])
        assert ok
        assert json.loads(payload)["attachments"]["uploaded"][0]["url"] == self._GATEWAY_URL

    @pytest.mark.asyncio
    async def test_site_links_need_no_lookup(self) -> None:
        jira = _jira()
        lookup = AsyncMock(return_value="https://acme.atlassian.net")
        jira._attachments = JiraIssueAttachments(jira.client, STATE, flavor="cloud", site_url=lookup)
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            await jira.add_attachments("KAN-1", ["rec1"])
        lookup.assert_not_awaited()


# ---------------------------------------------------------------------------
# Jira Data Center tools
# ---------------------------------------------------------------------------


def _jira_dc(**methods) -> "JiraDataCenter":
    jira = JiraDataCenter.__new__(JiraDataCenter)
    jira.client = _datasource(**methods)
    jira._site_url = None
    jira._field_schema_cache = None
    jira._create_fields_cache = {}
    jira.chat_state = STATE
    jira._attachments = JiraIssueAttachments(jira.client, STATE, flavor="data_center")
    return jira


@pytest.fixture
def dc_site_url() -> Iterator[None]:
    with patch.object(JiraDataCenter, "_get_site_url", AsyncMock(return_value="https://jira.corp.example")):
        yield


class TestJiraDataCenter:
    def test_constructor_with_and_without_state(self) -> None:
        client = MagicMock()
        client.get_client.return_value = MagicMock(get_base_url=MagicMock(return_value="https://jira.corp.example"))
        assert JiraDataCenter(client).chat_state is None
        assert JiraDataCenter(client, state=STATE).chat_state is STATE

    @pytest.mark.asyncio
    async def test_create_without_attachments_unchanged(self, dc_site_url) -> None:
        jira = _jira_dc(create_issue_v2=AsyncMock(return_value=_resp(201, {"key": "OPS-1"})))
        ok, _ = await jira.create_issue("OPS", "Bug", "Bug", description="as is {{attachment:x}}")
        assert ok
        assert jira.client.create_issue_v2.await_args.kwargs["fields"]["description"] == "as is {{attachment:x}}"
        jira.client.upload_issue_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_inline_uses_wiki_markup_and_v2(self, dc_site_url) -> None:
        jira = _jira_dc(
            create_issue_v2=AsyncMock(return_value=_resp(201, {"key": "OPS-1"})),
            edit_issue_v2=AsyncMock(return_value=_resp(204)),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.create_issue(
                "OPS", "Bug", "Bug", description="Screen:\n{{attachment:rec1}}",
                attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok and json.loads(payload)["attachments"]["uploaded"]
        assert jira.client.create_issue_v2.await_args.kwargs["fields"]["description"] == "Screen:\n"
        assert jira.client.upload_issue_attachment.await_args.kwargs == {"api_version": "2"}
        assert jira.client.edit_issue_v2.await_args.kwargs["fields"]["description"] == "Screen:\n!bug.png|thumbnail!"

    @pytest.mark.asyncio
    async def test_add_attachments_inline_appends_to_existing_description(self) -> None:
        jira = _jira_dc(
            get_issue_v2=AsyncMock(return_value=_resp(200, {"fields": {"description": "Existing text"}})),
            edit_issue_v2=AsyncMock(return_value=_resp(204)),
        )
        log = _resolved("rec2", "app.log", "text/plain")
        jira.client.upload_issue_attachment = AsyncMock(return_value=_resp(200, [{"id": "2", "filename": "app.log"}]))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([log]))):
            ok, _ = await jira.add_attachments("OPS-1", ["rec2"], attachment_display="inline")
        assert ok
        assert jira.client.edit_issue_v2.await_args.kwargs["fields"]["description"] == "Existing text\n\n[^app.log]"

    @pytest.mark.asyncio
    async def test_comment_inline_wiki(self, dc_site_url) -> None:
        jira = _jira_dc(add_comment_v2=AsyncMock(return_value=_resp(201, {"id": "9"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_comment(
                "OPS-1", "See {{attachment:bug.png}}", attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok
        assert jira.client.add_comment_v2.await_args.kwargs["body"] == "See !bug.png|thumbnail!"
        assert json.loads(payload)["attachments"]["uploaded"][0]["record_id"] == "rec1"

    @pytest.mark.asyncio
    async def test_placeholder_only_description_still_sends_the_files(self) -> None:
        jira = _jira_dc(edit_issue_v2=AsyncMock(return_value=_resp(204)), get_issue_v2=AsyncMock())
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await jira.update_issue("OPS-1", description="{{attachment:bug.png}}", attachment_record_ids=["rec1"])
        assert ok
        jira.client.upload_issue_attachment.assert_awaited_once()
        jira.client.edit_issue_v2.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("empty", [{"summary": ""}, {"labels": []}, {"custom_fields": {}}])
    async def test_empty_field_values_still_send_the_files(self, empty) -> None:
        jira = _jira_dc(edit_issue_v2=AsyncMock(return_value=_resp(204)), get_issue_v2=AsyncMock())
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await jira.update_issue("OPS-1", attachment_record_ids=["rec1"], **empty)
        assert ok
        jira.client.upload_issue_attachment.assert_awaited_once()
        jira.client.edit_issue_v2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unreadable_description_is_never_overwritten(self) -> None:
        jira = _jira_dc(get_issue_v2=AsyncMock(return_value=_resp(429)), edit_issue_v2=AsyncMock(return_value=_resp(204)))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await jira.add_attachments("OPS-1", ["rec1"], attachment_display="inline")
        assert ok
        jira.client.edit_issue_v2.assert_not_awaited()
        assert "warning" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_empty_comment_is_refused_before_anything_is_uploaded(self) -> None:
        jira = _jira_dc(add_comment_v2=AsyncMock(return_value=_resp(201, {"id": "9"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await jira.add_comment("OPS-1", "{{attachment:bug.png}}", attachment_record_ids=["rec1"])
        assert not ok
        jira.client.upload_issue_attachment.assert_not_awaited()
        jira.client.add_comment_v2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_audit_names_jira_data_center(self, dc_site_url) -> None:
        jira = _jira_dc(create_issue_v2=AsyncMock(return_value=_resp(201, {"key": "OPS-1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))), \
             patch("app.agents.actions.util.attachment_delivery.emit_attachment_audit") as audit:
            await jira.create_issue("OPS", "Bug", "Bug", attachment_record_ids=["rec1"])
        assert audit.call_args.kwargs["target_app"] == "jira_data_center"
