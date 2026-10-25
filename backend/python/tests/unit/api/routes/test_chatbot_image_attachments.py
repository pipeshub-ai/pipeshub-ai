"""Image chat attachments: the stored file is the upload as it came, and only
the copy in the parsed record, which the model reads, is shrunk."""

import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.api.routes import chatbot


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _record_uri(png: bytes) -> str:
    return chatbot._build_image_blocks(png, "image/png").blocks[0].data["uri"]


class TestRecordCopy:
    def test_an_image_over_the_limits_is_shrunk_for_the_record(self) -> None:
        uri = _record_uri(_png(3000, 20))
        assert uri.startswith("data:image/jpeg;base64,")
        record_copy = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
        assert max(record_copy.size) == 2048

    def test_an_image_within_the_limits_is_kept_byte_for_byte(self) -> None:
        png = _png(100, 80)
        assert _record_uri(png) == "data:image/png;base64," + base64.b64encode(png).decode()


@pytest.mark.asyncio
async def test_an_uploaded_image_is_stored_with_its_own_bytes_name_and_type() -> None:
    png = _png(3000, 20)
    req = MagicMock()
    req.json = AsyncMock(return_value={"attachments": [{
        "fileName": "wide.png", "mimeType": "image/png", "size": len(png),
        "contentBase64": base64.b64encode(png).decode("ascii"),
    }]})
    req.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
    req.app.container = MagicMock()
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk"})
    records = []
    build = chatbot._build_image_blocks

    def _keep_record_copy(content: bytes, mime: str) -> object:
        records.append(build(content, mime))
        return records[-1]

    with patch("app.api.routes.chatbot.BlobStorage") as blob_storage_cls, patch(
        "app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock(),
    ), patch("app.api.routes.chatbot.SinkOrchestrator", return_value=AsyncMock()), patch(
        "app.api.routes.chatbot.convert_record_dict_to_record", side_effect=lambda _doc: MagicMock(),
    ), patch(
        "app.api.routes.chatbot.TransformContext", MagicMock(side_effect=lambda **_: MagicMock()),
    ), patch("app.api.routes.chatbot._build_image_blocks", side_effect=_keep_record_copy):
        blob_storage = AsyncMock()
        blob_storage.save_binary_to_storage = AsyncMock(return_value=("ext", None))
        blob_storage_cls.return_value = blob_storage
        out = await chatbot.upload_chat_attachments(req, gp, AsyncMock())

    saved = blob_storage.save_binary_to_storage.await_args.kwargs
    assert saved["binary_data"] == png
    assert (saved["file_name"], saved["content_type"]) == ("wide.png", "image/png")
    attachment = out["attachments"][0]
    assert (attachment["recordName"], attachment["mimeType"]) == ("wide.png", "image/png")
    assert records[0].blocks[0].data["uri"].startswith("data:image/jpeg;base64,")
