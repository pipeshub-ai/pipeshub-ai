"""The production reader of a stored record's first bytes: it never downloads a record the gateway answers inline."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers.blob_storage import BlobStorage


def _blob(storage_type: str) -> BlobStorage:
    blob = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())
    blob._get_auth_and_config = AsyncMock(return_value=({"Authorization": "x"}, "http://node:3000", storage_type))
    blob.get_document_id_by_virtual_record_id = AsyncMock(return_value={"record_doc_id": "doc-1"})
    blob._cached_signed_url = AsyncMock(return_value=None)
    blob._fetch_record_envelope = AsyncMock(return_value={"signedUrl": None})
    return blob


@pytest.mark.asyncio
async def test_local_storage_is_not_downloaded_to_read_a_prefix() -> None:
    blob = _blob("local")
    with patch("app.modules.transformers.blob_storage.get_shared_session") as session:
        assert await blob.read_stored_prefix("v-1", "org-1", 1024) is None
    blob._fetch_record_envelope.assert_not_awaited()
    session.assert_not_called()


@pytest.mark.asyncio
async def test_object_storage_still_asks_the_gateway_for_a_signed_url() -> None:
    blob = _blob("s3")
    with patch("app.modules.transformers.blob_storage.get_shared_session"):
        assert await blob.read_stored_prefix("v-1", "org-1", 1024) is None
    blob._fetch_record_envelope.assert_awaited_once()
