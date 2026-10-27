"""Unit tests for DOCX / XLSX / CSV chat attachment upload support."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.blocks import Block, BlockType, BlocksContainer, DataFormat


def test_supported_mime_types_include_office_formats():
    from app.api.routes.chatbot import _attachment_extension, _is_supported_attachment_mime
    from app.utils.attachment_mime_types import (
        DELIMITED_MIME_TYPES as _DELIMITED_MIME_TYPES,
        DOCX_MIME_TYPES as _DOCX_MIME_TYPES,
        SPREADSHEET_MIME_TYPES as _SPREADSHEET_MIME_TYPES,
    )

    assert _is_supported_attachment_mime(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert _is_supported_attachment_mime(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert _is_supported_attachment_mime("text/csv")
    assert _is_supported_attachment_mime("text/tab-separated-values")

    # Legacy formats remain unsupported in phase 1.
    assert not _is_supported_attachment_mime("application/msword")
    assert not _is_supported_attachment_mime("application/vnd.ms-excel")
    assert not _is_supported_attachment_mime(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    assert _attachment_extension(
        "x.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) == "docx"
    assert _attachment_extension(
        "x.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) == "xlsx"
    assert _attachment_extension("x.csv", "text/csv") == "csv"
    # No filename suffix → fall back to MIME mapping.
    assert _attachment_extension("upload", "text/tab-separated-values") == "tsv"
    assert _DOCX_MIME_TYPES
    assert _SPREADSHEET_MIME_TYPES
    assert _DELIMITED_MIME_TYPES


def test_doc_attachment_mime_types_in_resolver():
    from app.agents.chat_modes.attachments import _is_doc
    from app.utils.attachment_mime_types import DOC_ATTACHMENT_MIME_TYPES as _DOC_ATTACHMENT_MIME_TYPES

    assert _is_doc("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert _is_doc("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert _is_doc("text/csv")
    assert _is_doc("text/tab-separated-values")
    assert "application/pdf" in _DOC_ATTACHMENT_MIME_TYPES


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_xls():
    from app.api.routes.chatbot import upload_chat_attachments

    payload = {
        "attachments": [
            {
                "fileName": "legacy.xls",
                "mimeType": "application/vnd.ms-excel",
                "size": 10,
                "contentBase64": base64.b64encode(b"1234567890").decode("ascii"),
            }
        ]
    }
    req = MagicMock()
    req.json = AsyncMock(return_value=payload)
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    req.app.container = MagicMock()
    req.app.container.logger.return_value = MagicMock()

    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})

    with patch("app.api.routes.chatbot.BlobStorage"), patch(
        "app.api.routes.chatbot.PDFPlumberOpenCVProcessor"
    ), pytest.raises(HTTPException) as exc:
        await upload_chat_attachments(req, gp, AsyncMock())
    assert exc.value.status_code == 400
    assert "legacy.xls can't be attached" in str(exc.value.detail)
    assert "XLSX" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_upload_csv_dispatches_to_lightweight_parser():
    from app.api.routes.chatbot import upload_chat_attachments

    csv_bytes = b"name,amount\nAlice,10\n"
    payload = {
        "conversationId": "conv-1",
        "attachments": [
            {
                "fileName": "sales.csv",
                "mimeType": "text/csv",
                "size": len(csv_bytes),
                "contentBase64": base64.b64encode(csv_bytes).decode("ascii"),
            }
        ],
    }
    req = MagicMock()
    req.json = AsyncMock(return_value=payload)
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    cont = MagicMock()
    cont.logger.return_value = MagicMock()
    req.app.container = cont

    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
    gp.batch_upsert_nodes = AsyncMock()
    gp.batch_create_edges = AsyncMock()

    fake_blocks = BlocksContainer(
        blocks=[
            Block(
                index=0,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                data={"row_natural_language_text": "Alice amount 10"},
            )
        ],
        block_groups=[],
    )

    with patch("app.api.routes.chatbot.BlobStorage") as mock_bs_cls, patch(
        "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()
    ), patch("app.api.routes.chatbot.SinkOrchestrator") as mock_sink_cls, patch(
        "app.api.routes.chatbot.convert_record_dict_to_record",
        return_value=MagicMock(block_containers=fake_blocks),
    ), patch(
        "app.api.routes.chatbot.TransformContext",
        MagicMock(side_effect=lambda **_: MagicMock()),
    ), patch(
        "app.api.routes.chatbot._build_csv_blocks",
        new_callable=AsyncMock,
        return_value=fake_blocks,
    ) as mock_csv:
        bs = AsyncMock()
        bs.save_binary_to_storage = AsyncMock(return_value=("ext", None))
        mock_bs_cls.return_value = bs
        orch = AsyncMock()
        mock_sink_cls.return_value = orch

        out = await upload_chat_attachments(req, gp, AsyncMock())

    mock_csv.assert_awaited_once()
    assert len(out["attachments"]) == 1
    assert out["attachments"][0]["extension"] == "csv"
    assert out["attachments"][0]["parseMode"] == "csv_lightweight"
    assert out["attachments"][0]["ocrMode"] == "csv_lightweight"
    orch.index.assert_awaited()


def _csv_attachment(name: str, content: bytes) -> dict:
    return {
        "fileName": name,
        "mimeType": "text/csv",
        "size": len(content),
        "contentBase64": base64.b64encode(content).decode("ascii"),
    }


@pytest.mark.asyncio
async def test_files_in_one_upload_share_one_description_budget() -> None:
    """The Slack bot and the API send several files in one request, and the route
    indexes them one after another. Each record gets what is left of one budget,
    not a fresh one, or three slow files would spend three budgets."""
    from app.api.routes.chatbot import upload_chat_attachments
    from app.modules.transformers.transformer import IMAGE_DESCRIPTION_BUDGET_S

    csv_bytes = b"name,amount\nAlice,10\n"
    payload = {"attachments": [_csv_attachment("a.csv", csv_bytes), _csv_attachment("b.csv", csv_bytes)]}
    req = MagicMock()
    req.json = AsyncMock(return_value=payload)
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    req.app.container = MagicMock()

    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})

    fake_blocks = BlocksContainer(blocks=[], block_groups=[])
    contexts: list[dict] = []

    async def slow_index(_ctx: object) -> None:
        await asyncio.sleep(0.3)

    with patch("app.api.routes.chatbot.BlobStorage") as mock_bs_cls, patch(
        "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()
    ), patch("app.api.routes.chatbot.SinkOrchestrator") as mock_sink_cls, patch(
        "app.api.routes.chatbot.convert_record_dict_to_record",
        side_effect=lambda _doc: MagicMock(block_containers=fake_blocks),
    ), patch(
        "app.api.routes.chatbot.TransformContext",
        MagicMock(side_effect=lambda **kwargs: contexts.append(kwargs) or MagicMock()),
    ), patch(
        "app.api.routes.chatbot._build_csv_blocks", new_callable=AsyncMock, return_value=fake_blocks,
    ), patch("app.api.routes.chatbot._CHAT_ATTACHMENT_IMAGE_DESCRIPTION_BUDGET_S", 1.0):
        bs = AsyncMock()
        bs.save_binary_to_storage = AsyncMock(return_value=("ext", None))
        mock_bs_cls.return_value = bs
        mock_sink_cls.return_value = AsyncMock(index=AsyncMock(side_effect=slow_index))

        await upload_chat_attachments(req, gp, AsyncMock())

    first, second = (ctx["settings"][IMAGE_DESCRIPTION_BUDGET_S] for ctx in contexts)
    assert 0.9 <= first <= 1.0
    assert second <= first - 0.25


@pytest.mark.asyncio
async def test_upload_xlsx_dispatches_to_lightweight_parser():
    from app.api.routes.chatbot import upload_chat_attachments

    xlsx_bytes = b"PK\x03\x04fake"
    payload = {
        "attachments": [
            {
                "fileName": "budget.xlsx",
                "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "size": len(xlsx_bytes),
                "contentBase64": base64.b64encode(xlsx_bytes).decode("ascii"),
            }
        ],
    }
    req = MagicMock()
    req.json = AsyncMock(return_value=payload)
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    cont = MagicMock()
    cont.logger.return_value = MagicMock()
    req.app.container = cont

    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
    gp.batch_upsert_nodes = AsyncMock()
    gp.batch_create_edges = AsyncMock()

    fake_blocks = BlocksContainer(blocks=[], block_groups=[])

    with patch("app.api.routes.chatbot.BlobStorage") as mock_bs_cls, patch(
        "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()
    ), patch("app.api.routes.chatbot.SinkOrchestrator") as mock_sink_cls, patch(
        "app.api.routes.chatbot.convert_record_dict_to_record",
        return_value=MagicMock(block_containers=fake_blocks),
    ), patch(
        "app.api.routes.chatbot.TransformContext",
        MagicMock(side_effect=lambda **_: MagicMock()),
    ), patch(
        "app.api.routes.chatbot._build_excel_blocks",
        new_callable=AsyncMock,
        return_value=fake_blocks,
    ) as mock_xlsx:
        bs = AsyncMock()
        bs.save_binary_to_storage = AsyncMock(return_value=("ext", None))
        mock_bs_cls.return_value = bs
        orch = AsyncMock()
        mock_sink_cls.return_value = orch

        out = await upload_chat_attachments(req, gp, AsyncMock())

    mock_xlsx.assert_awaited_once()
    assert out["attachments"][0]["extension"] == "xlsx"
    assert out["attachments"][0]["parseMode"] == "excel_lightweight"
    assert out["attachments"][0]["ocrMode"] == "excel_lightweight"


@pytest.mark.asyncio
async def test_upload_docx_dispatches_to_docling():
    from app.api.routes.chatbot import upload_chat_attachments

    docx_bytes = b"PK\x03\x04fake"
    payload = {
        "attachments": [
            {
                "fileName": "brief.docx",
                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size": len(docx_bytes),
                "contentBase64": base64.b64encode(docx_bytes).decode("ascii"),
            }
        ],
    }
    req = MagicMock()
    req.json = AsyncMock(return_value=payload)
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    cont = MagicMock()
    cont.logger.return_value = MagicMock()
    req.app.container = cont

    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
    gp.batch_upsert_nodes = AsyncMock()
    gp.batch_create_edges = AsyncMock()

    fake_blocks = BlocksContainer(blocks=[], block_groups=[])

    with patch("app.api.routes.chatbot.BlobStorage") as mock_bs_cls, patch(
        "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()
    ), patch("app.api.routes.chatbot.SinkOrchestrator") as mock_sink_cls, patch(
        "app.api.routes.chatbot.convert_record_dict_to_record",
        return_value=MagicMock(block_containers=fake_blocks),
    ), patch(
        "app.api.routes.chatbot.TransformContext",
        MagicMock(side_effect=lambda **_: MagicMock()),
    ), patch(
        "app.api.routes.chatbot._build_docx_blocks",
        new_callable=AsyncMock,
        return_value=fake_blocks,
    ) as mock_docx:
        bs = AsyncMock()
        bs.save_binary_to_storage = AsyncMock(return_value=("ext", None))
        mock_bs_cls.return_value = bs
        orch = AsyncMock()
        mock_sink_cls.return_value = orch

        out = await upload_chat_attachments(req, gp, AsyncMock())

    mock_docx.assert_awaited_once()
    assert out["attachments"][0]["extension"] == "docx"
    assert out["attachments"][0]["parseMode"] == "docling"
    assert out["attachments"][0]["ocrMode"] == "docling"


def test_sink_orchestrator_accepts_the_attachment_noop_vector_store():
    """The upload path builds a real SinkOrchestrator around a stub vector store.

    Every other test here patches SinkOrchestrator out, so nothing else exercises
    its constructor against that stub — which is how a constructor that reached
    into `vector_store.config_service` shipped and 500'd every upload.
    """
    import logging

    from app.api.routes.chatbot import _AttachmentSinkNoopVectorStore
    from app.modules.transformers.sink_orchestrator import SinkOrchestrator

    orchestrator = SinkOrchestrator(
        graphdb=AsyncMock(),
        blob_storage=AsyncMock(),
        vector_store=_AttachmentSinkNoopVectorStore(),
        graph_provider=AsyncMock(),
        logger=logging.getLogger("test-attachment-sink"),
        config_service=MagicMock(),
    )

    assert orchestrator.vector_store is not None


@pytest.mark.asyncio
async def test_failed_sink_rolls_back_the_records_it_already_wrote():
    """Records, files and edges land before the sink runs, with no shared transaction.

    Without the rollback a sink failure strands a record the user can neither see
    in chat nor delete — the upload response that carries its recordId never
    arrives.
    """
    from app.api.routes.chatbot import upload_chat_attachments

    csv_bytes = b"name,amount\nAlice,10\n"
    payload = {
        "attachments": [
            {
                "fileName": "sales.csv",
                "mimeType": "text/csv",
                "size": len(csv_bytes),
                "contentBase64": base64.b64encode(csv_bytes).decode("ascii"),
            }
        ],
    }
    req = MagicMock()
    req.json = AsyncMock(return_value=payload)
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    cont = MagicMock()
    cont.logger.return_value = MagicMock()
    req.app.container = cont

    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
    gp.batch_upsert_nodes = AsyncMock()
    gp.batch_create_edges = AsyncMock()
    gp.delete_nodes_and_edges = AsyncMock()
    gp.delete_nodes = AsyncMock()

    fake_blocks = BlocksContainer(blocks=[], block_groups=[])

    with patch("app.api.routes.chatbot.BlobStorage") as mock_bs_cls, patch(
        "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()
    ), patch("app.api.routes.chatbot.SinkOrchestrator") as mock_sink_cls, patch(
        "app.api.routes.chatbot.convert_record_dict_to_record",
        return_value=MagicMock(block_containers=fake_blocks),
    ), patch(
        "app.api.routes.chatbot.TransformContext",
        MagicMock(side_effect=lambda **_: MagicMock()),
    ), patch(
        "app.api.routes.chatbot._build_csv_blocks",
        new_callable=AsyncMock,
        return_value=fake_blocks,
    ):
        bs = AsyncMock()
        bs.save_binary_to_storage = AsyncMock(return_value=("ext", None))
        mock_bs_cls.return_value = bs
        orch = AsyncMock()
        orch.index = AsyncMock(side_effect=RuntimeError("sink exploded"))
        mock_sink_cls.return_value = orch

        with pytest.raises(RuntimeError, match="sink exploded"):
            await upload_chat_attachments(req, gp, AsyncMock())

    # Both collections cleaned, each with the same single record key. FILES goes
    # through delete_nodes_and_edges too, so Arango drops the isOfType edge.
    collections = [c.args[1] for c in gp.delete_nodes_and_edges.await_args_list]
    assert collections == ["records", "files"]
    keys = {tuple(c.args[0]) for c in gp.delete_nodes_and_edges.await_args_list}
    assert len(keys) == 1 and len(next(iter(keys))) == 1
    gp.delete_nodes.assert_not_awaited()


@pytest.mark.asyncio
async def test_files_cleanup_still_runs_when_records_cleanup_fails():
    """A raise on the RECORDS delete must not skip the FILES delete.

    Each collection gets its own attempt, so half a rollback is still better
    than none, and the original upload error is what reaches the caller.
    """
    from app.api.routes.chatbot import _rollback_attachment_records

    gp = AsyncMock()
    gp.delete_nodes_and_edges = AsyncMock(
        side_effect=[RuntimeError("records delete failed"), None]
    )

    await _rollback_attachment_records(gp, ["rec-1"])

    collections = [c.args[1] for c in gp.delete_nodes_and_edges.await_args_list]
    assert collections == ["records", "files"]


@pytest.mark.asyncio
async def test_rollback_never_raises_over_the_original_error():
    """Both cleanups failing must still return, so `raise` re-surfaces the upload error."""
    from app.api.routes.chatbot import _rollback_attachment_records

    gp = AsyncMock()
    gp.delete_nodes_and_edges = AsyncMock(side_effect=RuntimeError("graph is down"))

    await _rollback_attachment_records(gp, ["rec-1"])

    assert gp.delete_nodes_and_edges.await_count == 2


def _pdf_upload_request(name: str = "deck.pdf") -> MagicMock:
    content = b"%PDF-1.7 fake"
    req = MagicMock()
    req.json = AsyncMock(return_value={"attachments": [{
        "fileName": name,
        "mimeType": "application/pdf",
        "size": len(content),
        "contentBase64": base64.b64encode(content).decode("ascii"),
    }]})
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    req.app.container = MagicMock()
    return req


class TestPdfAttachmentFailsOnItsOwn:
    """A 2.8 MB text PDF parsed in the query service's own thread grew it by
    gigabytes until it was OOM-killed, and the upload ran past the proxy's 100s.
    The parse now runs in a parse worker under a deadline, and the user is told."""

    @staticmethod
    async def _upload(pdf_processor: MagicMock, timeout_s: float = 50) -> HTTPException:
        from app.api.routes.chatbot import upload_chat_attachments

        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
        with patch("app.api.routes.chatbot.BlobStorage") as mock_bs_cls, patch(
            "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()
        ), patch(
            "app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pdf_processor
        ), patch(
            "app.api.routes.chatbot.needs_ocr_and_page_count", return_value=(False, 80)
        ), patch("app.api.routes.chatbot._CHAT_ATTACHMENT_UPLOAD_BUDGET_S", timeout_s), pytest.raises(HTTPException) as exc:
            mock_bs_cls.return_value = AsyncMock(save_binary_to_storage=AsyncMock(return_value=("ext", None)))
            await upload_chat_attachments(_pdf_upload_request(), gp, AsyncMock())
        gp.batch_upsert_nodes.assert_not_called()
        return exc.value

    @pytest.mark.asyncio
    async def test_a_parse_past_the_deadline_is_stopped_and_explained(self) -> None:
        async def never_finishes(*_: object) -> None:
            await asyncio.sleep(30)

        processor = MagicMock(parse_document=AsyncMock(side_effect=never_finishes))
        loop = asyncio.get_running_loop()
        started = loop.time()

        error = await self._upload(processor, timeout_s=0.2)

        assert loop.time() - started < 5
        assert error.status_code == 422
        assert error.detail.startswith("deck.pdf is too large or complex to read right now, so it wasn't attached.")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cause", ["timed_out", "crashed", "renderer died", "memory"])
    async def test_a_parse_worker_that_overran_or_died_fails_only_this_upload(self, cause: str) -> None:
        from concurrent.futures.process import BrokenProcessPool

        from app.modules.parsers.parse_pool import ParseWorkerError

        error = {
            "renderer died": BrokenProcessPool("a process in the process pool was terminated abruptly"),
            "memory": MemoryError(),
        }.get(cause) or ParseWorkerError("internal", cause)
        processor = MagicMock(parse_document=AsyncMock(side_effect=error))

        error = await self._upload(processor)

        assert error.status_code == 422
        assert "deck.pdf is too large or complex to read right now" in error.detail

    @pytest.mark.asyncio
    async def test_the_ocr_check_runs_in_a_parse_worker(self) -> None:
        """pdfplumber walks every page for the OCR decision; in a thread that is
        the query service's GIL and memory, with no way to stop it."""
        from app.api.routes.chatbot import upload_chat_attachments
        from app.modules.parsers.pdf.pdf_inspection import needs_ocr_and_page_count

        submitted: list[object] = []

        async def fake_submit(fn: object, *_args: object, **_kwargs: object) -> tuple[bool, int]:
            submitted.append(fn)
            raise RuntimeError("stop here")

        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
        with patch("app.api.routes.chatbot.BlobStorage") as mock_bs_cls, patch(
            "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()
        ), patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor"), patch(
            "app.api.routes.chatbot.parse_pool.submit", side_effect=fake_submit), pytest.raises(
            HTTPException
        ) as exc:
            mock_bs_cls.return_value = AsyncMock(save_binary_to_storage=AsyncMock(return_value=("ext", None)))
            await upload_chat_attachments(_pdf_upload_request(), gp, AsyncMock())

        assert submitted == [needs_ocr_and_page_count]
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_describing_images_never_runs_past_the_upload_budget() -> None:
    """A proxy drops an upload that stays silent past 60s, so reading the files
    and describing their images share one budget."""
    from app.api.routes.chatbot import upload_chat_attachments
    from app.modules.transformers.transformer import IMAGE_DESCRIPTION_BUDGET_S

    csv_bytes = b"name,amount\nAlice,10\n"
    req = MagicMock()
    req.json = AsyncMock(return_value={"attachments": [_csv_attachment("a.csv", csv_bytes)]})
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    req.app.container = MagicMock()
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
    fake_blocks = BlocksContainer(blocks=[], block_groups=[])
    contexts: list[dict] = []

    with patch("app.api.routes.chatbot.BlobStorage") as mock_bs_cls, patch(
        "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()
    ), patch("app.api.routes.chatbot.SinkOrchestrator") as mock_sink_cls, patch(
        "app.api.routes.chatbot.convert_record_dict_to_record",
        side_effect=lambda _doc: MagicMock(block_containers=fake_blocks),
    ), patch(
        "app.api.routes.chatbot.TransformContext",
        MagicMock(side_effect=lambda **kwargs: contexts.append(kwargs) or MagicMock()),
    ), patch(
        "app.api.routes.chatbot._build_csv_blocks", new_callable=AsyncMock, return_value=fake_blocks,
    ), patch("app.api.routes.chatbot._CHAT_ATTACHMENT_UPLOAD_BUDGET_S", 2.0):
        mock_bs_cls.return_value = AsyncMock(save_binary_to_storage=AsyncMock(return_value=("ext", None)))
        mock_sink_cls.return_value = AsyncMock()

        await upload_chat_attachments(req, gp, AsyncMock())

    assert 0 < contexts[0]["settings"][IMAGE_DESCRIPTION_BUDGET_S] <= 2.0
