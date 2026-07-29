"""Integration test for the memory characteristics of a real (scanned/OCR)
PDF attachment upload -- exercises the actual `_build_pdf_image_blocks` /
`iter_pages_as_pil_from_bytes` rasterization path (the specific piece
reworked to process pages incrementally instead of rendering an entire PDF
into memory up front) against a genuine multi-page PDF, and asserts on the
real process RSS delta rather than a mocked call count.

`ProcessPoolExecutor.submit()` itself is short-circuited to run in-process
(same convention `tests/unit/modules/parsers/test_pdf_rasterizer.py` uses)
so the test doesn't depend on `multiprocessing.get_context("spawn")` being
usable in the sandbox/CI worker -- everything else in the rasterization
path (pdfplumber parsing, PIL rendering, PNG encoding) runs for real.
"""

from __future__ import annotations

import base64
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.routes.chatbot import AttachmentUploadRequest, upload_chat_attachments
from app.models.blocks import BlocksContainer
from app.modules.parsers.pdf import pdf_rasterizer
from app.utils.memory_monitor import get_process_memory_mb

# Generous but meaningful: a 20-page scanned PDF processed one page at a
# time should never come close to this. Old (render-everything-up-front)
# behavior for a similarly sized PDF was estimated at 150-300 MB; this
# threshold is set well above normal single-page-at-a-time overhead so the
# test only fails on a genuine regression back to bulk rendering.
_MAX_RSS_DELTA_MB = 300.0
_PAGE_COUNT = 20


def _build_scanned_pdf(page_count: int) -> bytes:
    """A real multi-page PDF with no extractable text on any page, so
    `_pdf_has_any_ocr_page` (>= 50% of pages needing OCR) is true without
    having to mock it -- exercising the actual OCR-page-count heuristic
    against real pdfplumber-parsed content."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(page_count):
        # No drawString/text content: pdfplumber extracts no text from a
        # blank page, so OCRStrategy.needs_ocr() treats it as scanned.
        c.showPage()
    c.save()
    return buf.getvalue()


def _attachment_upload_request(pdf_bytes: bytes):
    request = MagicMock()
    request.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": True}
    request.app.container.logger.return_value = MagicMock()
    request.json = AsyncMock(return_value={
        "attachments": [{
            "fileName": "scanned.pdf",
            "mimeType": "application/pdf",
            "size": len(pdf_bytes),
            "contentBase64": base64.b64encode(pdf_bytes).decode(),
        }]
    })
    return request


class TestAttachmentUploadMemory:
    async def test_twenty_page_scanned_pdf_upload_stays_under_rss_budget(self):
        pdf_bytes = _build_scanned_pdf(_PAGE_COUNT)
        request = _attachment_upload_request(pdf_bytes)

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock()
        graph_provider.batch_create_edges = AsyncMock()

        blob_instance = AsyncMock()
        blob_instance.save_binary_to_storage = AsyncMock(return_value=("storage-id", None))

        sink_instance = AsyncMock()
        sink_instance.index = AsyncMock()

        before = get_process_memory_mb()
        assert before is not None, "psutil must be available for this test to be meaningful"

        with (
            patch("app.api.routes.chatbot.BlobStorage", return_value=blob_instance),
            patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()),
            patch("app.api.routes.chatbot.SinkOrchestrator", return_value=sink_instance),
            patch(
                "app.api.routes.chatbot.convert_record_dict_to_record",
                return_value=MagicMock(block_containers=BlocksContainer(blocks=[], block_groups=[])),
            ),
            patch("app.api.routes.chatbot.TransformContext", MagicMock(side_effect=lambda **_: MagicMock())),
            # Run the "worker" function directly instead of submitting it to
            # a real spawned subprocess -- see module docstring.
            patch.object(pdf_rasterizer, "_run_in_pool", side_effect=lambda fn, *args: fn(*args)),
        ):
            result = await upload_chat_attachments(request, graph_provider, AsyncMock())

        after = get_process_memory_mb()
        assert after is not None

        assert len(result["attachments"]) == 1
        assert result["attachments"][0]["ocrMode"] == "image_direct"

        delta_rss_mb = after[0] - before[0]
        assert delta_rss_mb < _MAX_RSS_DELTA_MB, (
            f"20-page scanned PDF upload grew RSS by {delta_rss_mb:.1f} MB, "
            f"exceeding the {_MAX_RSS_DELTA_MB} MB budget for incremental "
            "page-at-a-time rasterization"
        )

    async def test_content_base64_freed_before_rasterization_completes(self):
        """Regression guard for the base64-drop fix landing on the SAME
        request path this test's memory budget depends on: if a future
        change stops clearing `contentBase64` before parsing, the freed-byte
        accounting this test's budget assumes silently stops applying."""
        pdf_bytes = _build_scanned_pdf(3)
        request = _attachment_upload_request(pdf_bytes)
        graph_provider = AsyncMock()

        captured: list[AttachmentUploadRequest] = []

        def _capturing(**kwargs) -> AttachmentUploadRequest:
            payload = AttachmentUploadRequest(**kwargs)
            captured.append(payload)
            return payload

        with (
            patch("app.api.routes.chatbot.AttachmentUploadRequest", side_effect=_capturing),
            patch("app.api.routes.chatbot.BlobStorage", return_value=AsyncMock(save_binary_to_storage=AsyncMock(return_value=("id", None)))),
            patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()),
            patch("app.api.routes.chatbot.SinkOrchestrator", return_value=AsyncMock(index=AsyncMock())),
            patch(
                "app.api.routes.chatbot.convert_record_dict_to_record",
                return_value=MagicMock(block_containers=BlocksContainer(blocks=[], block_groups=[])),
            ),
            patch("app.api.routes.chatbot.TransformContext", MagicMock(side_effect=lambda **_: MagicMock())),
            patch.object(pdf_rasterizer, "_run_in_pool", side_effect=lambda fn, *args: fn(*args)),
        ):
            await upload_chat_attachments(request, graph_provider, AsyncMock())

        assert len(captured) == 1
        assert captured[0].attachments[0].contentBase64 == ""
