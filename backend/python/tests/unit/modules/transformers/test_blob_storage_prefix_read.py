"""The production reader of a stored record's first bytes: it never downloads a record the gateway answers inline."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
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


class _Response:
    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        self.status, self.headers = status, headers or {}
        self.content = MagicMock()
        chunks = [body, b""]
        self.content.read = AsyncMock(side_effect=lambda _n: chunks.pop(0) if chunks else b"")

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_a: object) -> None:
        return None


def _session(responses: dict[str, _Response]) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(side_effect=lambda url, **_kw: responses[str(url)])
    return session


@pytest.mark.asyncio
async def test_a_cached_url_that_is_refused_is_replaced_by_one_gateway_refresh() -> None:
    blob = _blob("s3")
    blob._cached_signed_url = AsyncMock(return_value="https://old/x")
    blob._fetch_record_envelope = AsyncMock(return_value={"signedUrl": "https://new/x"})
    blob._store_signed_url = AsyncMock()
    session = _session({"https://old/x": _Response(403), "https://new/x": _Response(206, b"%PDF", {"Content-Range": "bytes 0-3/99"})})
    with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=session):
        assert await blob.read_stored_prefix("v-1", "org-1", 4) == (b"%PDF", 99)
    blob._fetch_record_envelope.assert_awaited_once()
    blob._store_signed_url.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_cached_url_that_works_is_read_directly() -> None:
    blob = _blob("s3")
    blob._cached_signed_url = AsyncMock(return_value="https://old/x")
    session = _session({"https://old/x": _Response(206, b"%PDF", {"Content-Range": "bytes 0-3/99"})})
    with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=session):
        assert await blob.read_stored_prefix("v-1", "org-1", 4) == (b"%PDF", 99)
    blob._fetch_record_envelope.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_fresh_url_that_is_refused_is_not_retried() -> None:
    blob = _blob("s3")
    blob._cached_signed_url = AsyncMock(return_value=None)
    blob._fetch_record_envelope = AsyncMock(return_value={"signedUrl": "https://new/x"})
    blob._store_signed_url = AsyncMock()
    session = _session({"https://new/x": _Response(403)})
    with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=session), pytest.raises(aiohttp.ClientError):
        await blob.read_stored_prefix("v-1", "org-1", 4)
    blob._fetch_record_envelope.assert_awaited_once()
