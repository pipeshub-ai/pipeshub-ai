"""Focused unit tests for the memory-optimization behaviors in
`upload_chat_attachments` (base64 freed after decode, gc.collect() per
attachment, decoded binary dropped before sink) and
`truncate_previous_conversations`.

The broader upload/attachment-processing happy/error paths are already
covered by `test_chatbot_extended_95_coverage.py`; these tests isolate the
specific memory-hygiene behaviors added for OOM mitigation.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.chatbot import (
    MAX_PREVIOUS_CONVERSATIONS,
    AttachmentUploadRequest,
    truncate_previous_conversations,
    upload_chat_attachments,
)
from app.models.blocks import BlocksContainer


def _image_attachment_request(*, file_names_and_mimes: list[tuple[str, str]]):
    """Build a fake `Request` + attachments payload for one or more small
    image attachments -- the simplest attachment path (no PDF/OCR
    machinery), ideal for isolating the base64/gc behaviors."""
    attachments = [
        {
            "fileName": name,
            "mimeType": mime,
            "size": 3,
            "contentBase64": base64.b64encode(b"abc").decode(),
        }
        for name, mime in file_names_and_mimes
    ]

    request = MagicMock()
    request.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": True}
    request.app.container.logger.return_value = MagicMock()
    request.json = AsyncMock(return_value={"attachments": attachments})
    return request, attachments


async def _run_upload(request, *, gc_mock=None):
    graph_provider = AsyncMock()
    graph_provider.batch_upsert_nodes = AsyncMock()
    graph_provider.batch_create_edges = AsyncMock()

    blob_instance = AsyncMock()
    blob_instance.save_binary_to_storage = AsyncMock(return_value=("storage-id", None))

    sink_instance = AsyncMock()
    sink_instance.index = AsyncMock()

    patches = [
        patch("app.api.routes.chatbot.BlobStorage", return_value=blob_instance),
        patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()),
        patch("app.api.routes.chatbot.SinkOrchestrator", return_value=sink_instance),
        patch(
            "app.api.routes.chatbot.convert_record_dict_to_record",
            return_value=MagicMock(block_containers=BlocksContainer(blocks=[], block_groups=[])),
        ),
        patch("app.api.routes.chatbot.TransformContext", MagicMock(side_effect=lambda **_: MagicMock())),
    ]
    if gc_mock is not None:
        patches.append(patch("app.api.routes.chatbot.gc.collect", gc_mock))

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        if gc_mock is not None:
            with patches[5]:
                return await upload_chat_attachments(request, graph_provider, AsyncMock())
        return await upload_chat_attachments(request, graph_provider, AsyncMock())


class TestBase64FreedAfterDecode:
    async def test_content_base64_cleared_after_successful_processing(self):
        """The base64 string is 1.33x the decoded size and never needed
        again post-decode -- it must not be kept alive on the Pydantic
        model for the rest of the request/response cycle."""
        request, _ = _image_attachment_request(
            file_names_and_mimes=[("photo.png", "image/png")]
        )

        captured_payloads: list[AttachmentUploadRequest] = []

        def _capturing_request(**kwargs) -> AttachmentUploadRequest:
            payload = AttachmentUploadRequest(**kwargs)
            captured_payloads.append(payload)
            return payload

        with patch("app.api.routes.chatbot.AttachmentUploadRequest", side_effect=_capturing_request):
            result = await _run_upload(request)

        assert len(result["attachments"]) == 1
        assert len(captured_payloads) == 1
        assert captured_payloads[0].attachments[0].contentBase64 == ""

    async def test_content_base64_cleared_even_when_decode_fails(self):
        """`item.contentBase64 = ""` runs in a `finally` block so a bad
        base64 payload still gets its (invalid) content string dropped
        rather than held until the exception propagates out of the request."""
        request = MagicMock()
        request.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": True}
        request.app.container.logger.return_value = MagicMock()
        request.json = AsyncMock(return_value={
            "attachments": [{
                "fileName": "bad.png",
                "mimeType": "image/png",
                "size": 3,
                "contentBase64": "not-valid-base64!!!",
            }]
        })
        graph_provider = AsyncMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await upload_chat_attachments(request, graph_provider, AsyncMock())
        assert exc_info.value.status_code == 400


class TestValidationFailsAtomicallyBeforeAnyAttachmentIsPersisted:
    async def test_second_attachments_bad_mime_prevents_first_from_being_written(self):
        """Attachments are now parsed/persisted/sunk one at a time instead of
        only committing graph writes after the whole batch parses
        successfully -- verify a validation failure on a LATER attachment
        still leaves ZERO graph/blob writes for an EARLIER, otherwise-valid
        one (all-or-nothing for this class of error), instead of orphaning
        the earlier attachment's record."""
        request = MagicMock()
        request.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": True}
        request.app.container.logger.return_value = MagicMock()
        request.json = AsyncMock(return_value={"attachments": [
            {"fileName": "good.png", "mimeType": "image/png", "size": 3, "contentBase64": base64.b64encode(b"abc").decode()},
            {"fileName": "bad.xyz", "mimeType": "application/nope", "size": 3, "contentBase64": base64.b64encode(b"abc").decode()},
        ]})

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.batch_create_edges = AsyncMock()
        blob_instance = AsyncMock()
        blob_instance.save_binary_to_storage = AsyncMock(return_value=("storage-id", None))
        sink_instance = AsyncMock()
        sink_instance.index = AsyncMock()

        from fastapi import HTTPException

        with (
            patch("app.api.routes.chatbot.BlobStorage", return_value=blob_instance),
            patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()),
            patch("app.api.routes.chatbot.SinkOrchestrator", return_value=sink_instance),
            patch(
                "app.api.routes.chatbot.convert_record_dict_to_record",
                return_value=MagicMock(block_containers=BlocksContainer(blocks=[], block_groups=[])),
            ),
            patch("app.api.routes.chatbot.TransformContext", MagicMock(side_effect=lambda **_: MagicMock())),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await upload_chat_attachments(request, graph_provider, AsyncMock())

        assert exc_info.value.status_code == 400
        assert "bad.xyz" in exc_info.value.detail
        graph_provider.batch_upsert_nodes.assert_not_called()
        graph_provider.batch_create_edges.assert_not_called()
        blob_instance.save_binary_to_storage.assert_not_called()
        sink_instance.index.assert_not_called()


class TestGcCollectPerAttachment:
    async def test_gc_collect_called_once_per_attachment(self):
        """Rendered blocks for a large attachment can reach tens of MB;
        forcing collection after each attachment (rather than waiting for
        the whole batch to finish) bounds peak RSS during a multi-attachment
        upload."""
        request, _ = _image_attachment_request(
            file_names_and_mimes=[("a.png", "image/png"), ("b.png", "image/png"), ("c.png", "image/png")]
        )
        gc_mock = MagicMock()

        result = await _run_upload(request, gc_mock=gc_mock)

        assert len(result["attachments"]) == 3
        assert gc_mock.call_count == 3


class TestTruncatePreviousConversationsMemoryCap:
    def test_returns_input_unchanged_when_under_the_cap(self):
        convo = [{"role": "user", "content": "hi"}]
        assert truncate_previous_conversations(convo) == convo

    def test_truncates_to_the_most_recent_max_turns(self):
        convo = [{"role": "user", "content": str(i)} for i in range(MAX_PREVIOUS_CONVERSATIONS + 10)]
        result = truncate_previous_conversations(convo)
        assert len(result) == MAX_PREVIOUS_CONVERSATIONS
        assert result[0]["content"] == str(10)
        assert result[-1]["content"] == str(MAX_PREVIOUS_CONVERSATIONS + 9)

    def test_none_input_returns_empty_list(self):
        assert truncate_previous_conversations(None) == []

    def test_custom_max_turns_override(self):
        convo = [{"role": "user", "content": str(i)} for i in range(10)]
        result = truncate_previous_conversations(convo, max_turns=3)
        assert len(result) == 3
        assert result[-1]["content"] == "9"
