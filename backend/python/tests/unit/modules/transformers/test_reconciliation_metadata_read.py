"""``BlobStorage.get_reconciliation_metadata``: absent versus unreadable, and what a failed read writes to the log."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers import blob_storage as blob_mod
from app.modules.transformers.blob_storage import (
    BlobStorage,
    ReconciliationMetadataUnavailable,
)

SECRET = "https://storage.example/obj?X-Signature=TOPSECRET"


class _Resp:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status, self._payload = status, payload or {}

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def json(self, **_kw: object) -> dict:
        return self._payload


class _Session:
    def __init__(self, *, status: int = 200, error: Exception | None = None, payload: dict | None = None) -> None:
        self.status, self.error, self.payload = status, error, payload

    def get(self, *_a: object, **_kw: object) -> _Resp:
        if self.error is not None:
            raise self.error
        return _Resp(self.status, self.payload)


def _store(mapping: object = None) -> BlobStorage:
    store = BlobStorage.__new__(BlobStorage)
    store.logger = MagicMock()
    store.graph_provider = MagicMock()
    store.graph_provider.get_document = AsyncMock(return_value={"record_metadata_doc_id": "doc-1"} if mapping is None else mapping)
    store._get_auth_and_config = AsyncMock(return_value=({}, "http://node", None))
    return store


def _logged(store: BlobStorage) -> str:
    return " ".join(str(c) for m in (store.logger.error, store.logger.warning) for c in m.call_args_list)


@pytest.mark.asyncio
async def test_a_record_with_no_metadata_is_none_even_when_errors_are_asked_for() -> None:
    store = _store(mapping={})
    assert await store.get_reconciliation_metadata("vr", "org", raise_on_error=True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("session", [_Session(status=500), _Session(error=RuntimeError("down"))])
async def test_a_failed_read_raises_when_errors_are_asked_for_and_is_none_otherwise(session: _Session) -> None:
    store = _store()
    with patch.object(blob_mod, "get_shared_session", return_value=session):
        assert await store.get_reconciliation_metadata("vr", "org") is None
        with pytest.raises(ReconciliationMetadataUnavailable):
            await store.get_reconciliation_metadata("vr", "org", raise_on_error=True)


@pytest.mark.asyncio
async def test_a_failed_mapping_lookup_raises_when_errors_are_asked_for() -> None:
    store = _store()
    store.graph_provider.get_document = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(ReconciliationMetadataUnavailable):
        await store.get_reconciliation_metadata("vr", "org", raise_on_error=True)
    assert await store.get_reconciliation_metadata("vr", "org") is None


@pytest.mark.asyncio
async def test_the_log_of_a_failed_read_never_carries_the_signed_url() -> None:
    store = _store()
    session = _Session(error=RuntimeError(f"Exceeded maximum redirects for {SECRET}"))
    with patch.object(blob_mod, "get_shared_session", return_value=session):
        assert await store.get_reconciliation_metadata("vr", "org") is None
    assert "TOPSECRET" not in _logged(store) and "RuntimeError" in _logged(store)
    store = _store()
    store.graph_provider.get_document = AsyncMock(side_effect=RuntimeError(f"lookup {SECRET}"))
    await store.get_reconciliation_metadata("vr", "org")
    assert "TOPSECRET" not in _logged(store)
