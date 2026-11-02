"""Attaching PipesHub records to Confluence pages (PP-434).

Covers the uploader (v1 endpoint, never overwriting an existing file), storage-format
placement, and the Confluence Cloud and Data Center tools that accept attachment_record_ids.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.confluence.attachments import (
    ConfluenceAttachmentUploader,
    ConfluencePageAttachments,
    storage_reference,
)
from app.agents.actions.confluence.confluence import Confluence
from app.agents.actions.confluence_data_center.confluence_data_center import (
    ConfluenceDataCenter,
)
from app.agents.actions.util.attachment_upload import (
    AttachmentTarget,
    AttachmentUploadResult,
)
from app.agents.actions.util.attachments import AttachmentBundle
from app.services.record_content.models import AttachmentFailure, ResolvedRecordContent

if TYPE_CHECKING:
    from collections.abc import Iterator

_RESOLVE = "app.agents.actions.util.attachment_delivery.resolve_attachments"

STATE = {"org_id": "org1", "user_id": "user1", "conversation_id": "conv1"}

_DUPLICATE = "Cannot add a new attachment with same file name as an existing attachment: diagram.png"


def _resp(status: int, body=None, text=None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.is_json = True
    resp.headers = {}
    resp.json = MagicMock(return_value=body if body is not None else {})
    resp.text = MagicMock(return_value=text if text is not None else json.dumps(body or {}))
    return resp


def _uploaded_body(att_id="att1", title="diagram.png") -> dict:
    return {
        "results": [{"id": att_id, "title": title, "_links": {"download": f"/download/attachments/123/{title}"}}],
        "_links": {"base": "https://acme.atlassian.net/wiki"},
    }


def _resolved(record_id="rec1", filename="diagram.png", mime="image/png") -> ResolvedRecordContent:
    return ResolvedRecordContent(
        record_id=record_id, filename=filename, mime_type=mime, size_bytes=3,
        content=b"abc", version=None, source="blob",
    )


def _bundle(resolved=(), failures=()) -> AttachmentBundle:
    bundle = AttachmentBundle()
    bundle.resolved = list(resolved)
    bundle.failures = list(failures)
    return bundle


def _datasource(**methods) -> MagicMock:
    ds = MagicMock()
    ds.upload_content_attachment = AsyncMock(return_value=_resp(200, _uploaded_body()))
    for name, value in methods.items():
        setattr(ds, name, value)
    return ds


# ---------------------------------------------------------------------------
# Uploader
# ---------------------------------------------------------------------------


class TestConfluenceAttachmentUploader:
    @pytest.mark.asyncio
    async def test_upload_success(self) -> None:
        ds = _datasource()
        results = await ConfluenceAttachmentUploader(ds).upload(AttachmentTarget(destination_id="123"), [_resolved()])
        assert results[0].success
        assert results[0].platform_id == "att1"
        assert results[0].platform_url == "https://acme.atlassian.net/wiki/download/attachments/123/diagram.png"
        ds.upload_content_attachment.assert_awaited_once_with("123", "diagram.png", b"abc", "image/png")

    @pytest.mark.asyncio
    async def test_existing_name_is_never_overwritten(self) -> None:
        ds = _datasource(upload_content_attachment=AsyncMock(side_effect=[
            _resp(400, text=_DUPLICATE),
            _resp(200, _uploaded_body(title="diagram (2).png")),
        ]))
        results = await ConfluenceAttachmentUploader(ds).upload(AttachmentTarget(destination_id="123"), [_resolved()])
        names = [call.args[1] for call in ds.upload_content_attachment.await_args_list]
        assert names == ["diagram.png", "diagram (2).png"]
        assert results[0].success
        assert results[0].stored_filename == "diagram (2).png"
        assert results[0].to_dict()["stored_as"] == "diagram (2).png"

    @pytest.mark.asyncio
    async def test_gives_up_after_three_names(self) -> None:
        ds = _datasource(upload_content_attachment=AsyncMock(return_value=_resp(400, text=_DUPLICATE)))
        results = await ConfluenceAttachmentUploader(ds).upload(AttachmentTarget(destination_id="123"), [_resolved()])
        assert ds.upload_content_attachment.await_count == 3
        assert "already has files with this name" in results[0].error

    @pytest.mark.asyncio
    async def test_other_400_is_not_retried(self) -> None:
        ds = _datasource(upload_content_attachment=AsyncMock(return_value=_resp(400, {"message": "bad"}, text="bad")))
        results = await ConfluenceAttachmentUploader(ds).upload(AttachmentTarget(destination_id="123"), [_resolved()])
        ds.upload_content_attachment.assert_awaited_once()
        assert not results[0].success

    @pytest.mark.asyncio
    async def test_401_mentions_oauth_scope(self) -> None:
        ds = _datasource(upload_content_attachment=AsyncMock(return_value=_resp(401)))
        results = await ConfluenceAttachmentUploader(ds).upload(AttachmentTarget(destination_id="123"), [_resolved()])
        assert "write:attachment:confluence" in results[0].error


class TestStorageReference:
    def test_image_and_file(self) -> None:
        image = AttachmentUploadResult(record_id="r", filename="a.png", success=True, mime_type="image/png")
        doc = AttachmentUploadResult(record_id="r", filename="spec.pdf", success=True, mime_type="application/pdf")
        assert storage_reference(image) == '<ac:image><ri:attachment ri:filename="a.png" /></ac:image>'
        assert storage_reference(doc) == '<ac:link><ri:attachment ri:filename="spec.pdf" /></ac:link>'

    def test_filename_is_escaped(self) -> None:
        result = AttachmentUploadResult(record_id="r", filename='x"><script>.png', success=True, mime_type="image/png")
        assert '"><script>' not in storage_reference(result)
        assert "&quot;&gt;&lt;script&gt;" in storage_reference(result)


# ---------------------------------------------------------------------------
# Confluence Cloud tools
# ---------------------------------------------------------------------------


def _cloud(**methods) -> Confluence:
    conf = Confluence.__new__(Confluence)
    conf.client = _datasource(**methods)
    conf._site_url = None
    conf.chat_state = STATE
    conf._attachments = ConfluencePageAttachments(conf.client, STATE, flavor="cloud")
    return conf


def _current_page(version=1, body="") -> MagicMock:
    return _resp(200, {
        "id": "123", "status": "current", "spaceId": "9", "title": "Runbook",
        "version": {"number": version}, "body": {"storage": {"value": body}},
    })


@pytest.fixture
def cloud_helpers() -> Iterator[None]:
    with patch.object(Confluence, "_resolve_space_id", AsyncMock(return_value="9")), \
         patch.object(Confluence, "_get_site_url", AsyncMock(return_value=None)):
        yield


class TestConfluenceCloudTools:
    def test_constructor_with_and_without_state(self) -> None:
        client = MagicMock()
        client.get_client.return_value = MagicMock(get_base_url=MagicMock(return_value="https://acme.atlassian.net/wiki/api/v2"))
        assert Confluence(client).chat_state is None
        assert Confluence(client, state=STATE).chat_state is STATE

    @pytest.mark.asyncio
    async def test_create_page_without_attachments_unchanged(self, cloud_helpers) -> None:
        conf = _cloud(create_page=AsyncMock(return_value=_resp(200, {"id": "123"})))
        ok, _ = await conf.create_page("SD", "Runbook", "<p>{{attachment:x}}</p>")
        assert ok
        sent = conf.client.create_page.await_args.kwargs["body"]["body"]["storage"]["value"]
        assert sent == "<p>{{attachment:x}}</p>"
        conf.client.upload_content_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_page_inline_places_images(self, cloud_helpers) -> None:
        conf = _cloud(
            create_page=AsyncMock(return_value=_resp(200, {"id": "123"})),
            get_page_by_id=AsyncMock(return_value=_current_page(version=1, body="<h2>Steps</h2>")),
            update_page=AsyncMock(return_value=_resp(200, {"id": "123"})),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await conf.create_page(
                "SD", "Runbook", "<h2>Steps</h2>{{attachment:rec1}}",
                attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok and "warning" not in json.loads(payload)
        created = conf.client.create_page.await_args.kwargs["body"]["body"]["storage"]["value"]
        assert created == "<h2>Steps</h2>"
        update = conf.client.update_page.await_args.kwargs
        assert update["id"] == 123
        assert update["body"]["version"] == {"number": 2}
        assert update["body"]["body"]["storage"]["value"] == (
            '<h2>Steps</h2><ac:image><ri:attachment ri:filename="diagram.png" /></ac:image>'
        )
        assert conf.client.upload_content_attachment.await_args.args[0] == "123"

    @pytest.mark.asyncio
    async def test_conflict_on_placement_rereads_once(self, cloud_helpers) -> None:
        conf = _cloud(
            create_page=AsyncMock(return_value=_resp(200, {"id": "123"})),
            get_page_by_id=AsyncMock(side_effect=[_current_page(1), _current_page(2)]),
            update_page=AsyncMock(side_effect=[_resp(409), _resp(200, {"id": "123"})]),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await conf.create_page(
                "SD", "Runbook", "<p>x</p>", attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok and "warning" not in json.loads(payload)
        assert conf.client.update_page.await_args.kwargs["body"]["version"] == {"number": 3}

    @pytest.mark.asyncio
    async def test_nothing_created_when_no_file_resolves(self, cloud_helpers) -> None:
        conf = _cloud(create_page=AsyncMock())
        denied = _bundle(failures=[AttachmentFailure("rec1", "denied", "RecordAccessDeniedError")])
        with patch(_RESOLVE, new=AsyncMock(return_value=denied)):
            ok, _ = await conf.create_page("SD", "Runbook", "<p>x</p>", attachment_record_ids=["rec1"])
        assert not ok
        conf.client.create_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_attachments_inline_appends_to_current_body(self) -> None:
        conf = _cloud(
            get_page_by_id=AsyncMock(return_value=_current_page(4, "<p>Existing</p>")),
            update_page=AsyncMock(return_value=_resp(200, {"id": "123"})),
        )
        pdf = _resolved("rec2", "spec.pdf", "application/pdf")
        conf.client.upload_content_attachment = AsyncMock(return_value=_resp(200, _uploaded_body("att2", "spec.pdf")))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([pdf]))):
            ok, payload = await conf.add_attachments("123", ["rec2"], attachment_display="inline")
        assert ok
        body = conf.client.update_page.await_args.kwargs["body"]
        assert body["version"] == {"number": 5}
        assert body["body"]["storage"]["value"] == (
            '<p>Existing</p><p><ac:link><ri:attachment ri:filename="spec.pdf" /></ac:link></p>'
        )
        assert json.loads(payload)["attachments"]["uploaded"][0]["platform_id"] == "att2"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content", ["", "{{attachment:rec2}}"])
    async def test_blank_content_keeps_the_body_and_appends(self, content) -> None:
        conf = _cloud(
            get_page_by_id=AsyncMock(return_value=_current_page(4, "<p>Existing</p>")),
            update_page=AsyncMock(return_value=_resp(200, {"id": "123"})),
        )
        pdf = _resolved("rec2", "spec.pdf", "application/pdf")
        conf.client.upload_content_attachment = AsyncMock(return_value=_resp(200, _uploaded_body("att2", "spec.pdf")))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([pdf]))):
            ok, _ = await conf.update_page(
                "123", page_content=content, attachment_record_ids=["rec2"], attachment_display="inline",
            )
        assert ok
        conf.client.update_page.assert_awaited_once()
        assert conf.client.update_page.await_args.kwargs["body"]["body"]["storage"]["value"] == (
            '<p>Existing</p><p><ac:link><ri:attachment ri:filename="spec.pdf" /></ac:link></p>'
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("title", ["", "  "])
    async def test_blank_title_keeps_the_current_title(self, title) -> None:
        conf = _cloud(
            get_page_by_id=AsyncMock(return_value=_current_page(4, "<p>Existing</p>")),
            update_page=AsyncMock(return_value=_resp(200, {"id": "123"})),
        )
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await conf.update_page(
                "123", page_title=title, page_content="<p>New</p>", attachment_record_ids=["rec1"],
            )
        assert ok
        assert conf.client.update_page.await_args.kwargs["body"]["title"] == "Runbook"
        conf.client.upload_content_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blank_title_alone_only_attaches(self) -> None:
        conf = _cloud(get_page_by_id=AsyncMock(), update_page=AsyncMock())
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await conf.update_page("123", page_title="", attachment_record_ids=["rec1"])
        assert ok
        conf.client.update_page.assert_not_awaited()
        conf.client.upload_content_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_attachments_default_leaves_body_alone(self) -> None:
        conf = _cloud(get_page_by_id=AsyncMock(), update_page=AsyncMock())
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await conf.add_attachments("123", ["rec1"])
        assert ok
        conf.client.update_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_comment_attaches_to_page_and_names_files(self) -> None:
        conf = _cloud(create_footer_comment=AsyncMock(return_value=_resp(200, {"id": "c1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await conf.comment_on_page(
                "123", "Updated diagram {{attachment:rec1}}", attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok
        body = conf.client.create_footer_comment.await_args.kwargs["body_body"]["storage"]["value"]
        assert body == "<p>Updated diagram<br/><br/>Attached to this page: diagram.png</p>"
        assert json.loads(payload)["attachments"]["uploaded"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("comment", ["", "{{attachment:rec1}}"])
    async def test_empty_comment_is_refused_before_anything_is_uploaded(self, comment) -> None:
        conf = _cloud(create_footer_comment=AsyncMock(return_value=_resp(200, {"id": "c1"})))
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await conf.comment_on_page("123", comment, attachment_record_ids=["rec1"])
        assert not ok
        assert "add_attachments" in json.loads(payload)["guidance"]
        conf.client.upload_content_attachment.assert_not_awaited()
        conf.client.create_footer_comment.assert_not_awaited()


# ---------------------------------------------------------------------------
# Confluence Data Center tools
# ---------------------------------------------------------------------------


def _dc(**methods) -> ConfluenceDataCenter:
    tool = ConfluenceDataCenter.__new__(ConfluenceDataCenter)
    tool.client = _datasource(**methods)
    tool._site_url = None
    tool.chat_state = STATE
    tool._attachments = ConfluencePageAttachments(
        tool.client, STATE, flavor="data_center", to_storage=ConfluenceDataCenter._to_storage_body,
    )
    return tool


class TestConfluenceDataCenterTools:
    def test_constructor_with_and_without_state(self) -> None:
        client = MagicMock()
        client.get_client.return_value = MagicMock(get_base_url=MagicMock(return_value="https://wiki.corp.example"))
        assert ConfluenceDataCenter(client).chat_state is None
        assert ConfluenceDataCenter(client, state=STATE).chat_state is STATE

    @pytest.mark.asyncio
    async def test_plain_text_content_is_converted_then_image_placed(self) -> None:
        tool = _dc(
            create_content_v1=AsyncMock(return_value=_resp(200, {"id": "77", "title": "T", "version": {"number": 1}})),
            get_page_content_v1=AsyncMock(return_value=_resp(200, {"title": "T", "version": {"number": 1}, "body": {"storage": {"value": "<p>See </p>"}}})),
            update_content_v1=AsyncMock(return_value=_resp(200, {"id": "77"})),
        )
        with patch.object(ConfluenceDataCenter, "_get_site_url", AsyncMock(return_value=None)), \
             patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, payload = await tool.create_page(
                "ENG", "T", "See {{attachment:rec1}} & more",
                attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok and "warning" not in json.loads(payload)
        created = tool.client.create_content_v1.await_args.kwargs["body"]["body"]["storage"]["value"]
        assert created == "<p>See  &amp; more</p>"
        update = tool.client.update_content_v1.await_args.kwargs
        assert update["content_id"] == "77"
        assert update["body"]["version"] == {"number": 2}
        assert update["body"]["body"]["storage"]["value"] == (
            '<p>See <ac:image><ri:attachment ri:filename="diagram.png" /></ac:image> &amp; more</p>'
        )

    @pytest.mark.asyncio
    async def test_update_without_attachments_unchanged(self) -> None:
        tool = _dc(
            get_page_content_v1=AsyncMock(return_value=_resp(200, {"title": "T", "version": {"number": 3}})),
            update_content_v1=AsyncMock(return_value=_resp(200, {"id": "77"})),
        )
        with patch.object(ConfluenceDataCenter, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await tool.update_page("77", page_title="New")
        assert ok
        assert "body" not in tool.client.update_content_v1.await_args.kwargs["body"]
        tool.client.upload_content_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_content_inline_keeps_the_body(self) -> None:
        tool = _dc(
            get_page_content_v1=AsyncMock(return_value=_resp(200, {"title": "T", "version": {"number": 3}, "body": {"storage": {"value": "<p>Keep</p>"}}})),
            update_content_v1=AsyncMock(return_value=_resp(200, {"id": "77"})),
        )
        with patch.object(ConfluenceDataCenter, "_get_site_url", AsyncMock(return_value=None)), \
             patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await tool.update_page("77", page_content="", attachment_record_ids=["rec1"], attachment_display="inline")
        assert ok
        tool.client.update_content_v1.assert_awaited_once()
        assert tool.client.update_content_v1.await_args.kwargs["body"]["body"]["storage"]["value"] == (
            '<p>Keep</p><p><ac:image><ri:attachment ri:filename="diagram.png" /></ac:image></p>'
        )

    @pytest.mark.asyncio
    async def test_blank_title_keeps_the_current_title(self) -> None:
        tool = _dc(
            get_page_content_v1=AsyncMock(return_value=_resp(200, {"title": "T", "version": {"number": 3}})),
            update_content_v1=AsyncMock(return_value=_resp(200, {"id": "77"})),
        )
        with patch.object(ConfluenceDataCenter, "_get_site_url", AsyncMock(return_value=None)), \
             patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))):
            ok, _ = await tool.update_page("77", page_title="  ", page_content="<p>New</p>", attachment_record_ids=["rec1"])
        assert ok
        assert tool.client.update_content_v1.await_args.kwargs["body"]["title"] == "T"
        tool.client.upload_content_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_inline_comment_in_storage_format_escapes_file_names(self) -> None:
        tool = _dc(
            upload_content_attachment=AsyncMock(return_value=_resp(200, _uploaded_body(title="R&D <plan>.pdf"))),
            create_comment_v1=AsyncMock(return_value=_resp(200, {"id": "c1"})),
        )
        with patch.object(ConfluenceDataCenter, "_get_site_url", AsyncMock(return_value=None)), \
             patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved(filename="R&D <plan>.pdf", mime="application/pdf")]))):
            ok, _ = await tool.add_comment(
                "77", "<p>See the plan</p>", attachment_record_ids=["rec1"], attachment_display="inline",
            )
        assert ok
        assert tool.client.create_comment_v1.await_args.kwargs["body_value"] == (
            "<p>See the plan</p><p>Attached to this page: R&amp;D &lt;plan&gt;.pdf</p>"
        )

    @pytest.mark.asyncio
    async def test_inline_plain_comment_is_escaped_by_the_tool(self) -> None:
        tool = _dc(
            upload_content_attachment=AsyncMock(return_value=_resp(200, _uploaded_body(title="R&D.pdf"))),
            create_comment_v1=AsyncMock(return_value=_resp(200, {"id": "c1"})),
        )
        with patch.object(ConfluenceDataCenter, "_get_site_url", AsyncMock(return_value=None)), \
             patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved(filename="R&D.pdf", mime="application/pdf")]))):
            ok, _ = await tool.add_comment("77", "See this", attachment_record_ids=["rec1"], attachment_display="inline")
        assert ok
        assert tool.client.create_comment_v1.await_args.kwargs["body_value"] == (
            "<p>See this\n\nAttached to this page: R&amp;D.pdf</p>"
        )

    @pytest.mark.asyncio
    async def test_audit_names_confluence_data_center(self) -> None:
        tool = _dc()
        with patch(_RESOLVE, new=AsyncMock(return_value=_bundle([_resolved()]))), \
             patch("app.agents.actions.util.attachment_delivery.emit_attachment_audit") as audit:
            ok, _ = await tool.add_attachments("77", ["rec1"])
        assert ok
        assert audit.call_args.kwargs["target_app"] == "confluence_data_center"
        assert audit.call_args.kwargs["destination"] == "77"
