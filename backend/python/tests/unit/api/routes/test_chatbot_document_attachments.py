"""Unit tests for DOCX / XLSX / CSV chat attachment upload support."""

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
    assert "Unsupported" in str(exc.value.detail)


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
