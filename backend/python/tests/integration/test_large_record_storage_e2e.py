"""End-to-end: large records stored and read through real HTTP.

The real ``BlobStorage`` and the real shared aiohttp session against a
stand-in for the Node storage gateway (``tests/support/fake_storage_gateway``),
in both of its storage modes:

- local storage enforces the platform upload limit. A deck that repeats one
  image on every slide used to compress to nearly its full size and be refused;
  with the long-window compressor it is stored and reads back intact.
- S3/Azure has no limit, so large records already reach readers there. Several
  readers at once decode them one at a time, off the loop, and a record over
  the decode ceiling is refused after one download, without stopping others.

No Docker: the gateway is an in-process aiohttp server on its own thread.
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from app.exceptions.storage_exceptions import (
    StorageRequestRejectedError,
    StoredRecordTooLargeError,
)
from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import BlobStorage, close_shared_session
from app.utils.user_errors import FILE_TOO_LARGE, to_user_reason, too_large_refusal
from tests.support.fake_storage_gateway import FakeStorageGateway

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = pytest.mark.integration

MiB = 1024 * 1024


@pytest.fixture(autouse=True)
async def _fresh_state(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    for env in (bs_mod._COMPRESSION_THRESHOLD_ENV, bs_mod._MAX_DECODED_BYTES_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("PIPESHUB_SIGNED_URL_CACHE_SECONDS", raising=False)
    bs_mod._decode_slots.clear()
    yield
    await close_shared_session()
    bs_mod._decode_slots.clear()


@pytest.fixture(scope="module")
def deck_record() -> dict:
    """~36 MiB: one incompressible ~1 MiB image on six slides, 4.5 MiB apart (past a 4 MiB window)."""
    image = "data:image/png;base64," + base64.b64encode(os.urandom(MiB)).decode()
    blocks: list[dict] = []
    for slide in range(6):
        blocks.append({"type": "image", "index": len(blocks), "data": {"uri": image},
                       "citation_metadata": {"page_number": slide + 1}})
        blocks.append({"type": "text", "index": len(blocks), "data": f"slide {slide} " + "notes " * (750 * 1024)})
    return {"record_name": "deck.pptx", "block_containers": {"blocks": blocks, "block_groups": []}}


def _storage(gateway: FakeStorageGateway) -> BlobStorage:
    storage = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=None)
    storage._get_auth_and_config = AsyncMock(return_value=({}, gateway.base_url, gateway.storage_type))
    return storage


async def _store(storage: BlobStorage, record: dict, vrid: str) -> dict:
    document_id, size = await storage.save_record_to_storage("org-1", f"rec-{vrid}", vrid, record)
    return {"record_doc_id": document_id, "fileSizeBytes": size}


@pytest.fixture
def local_gateway() -> Iterator[FakeStorageGateway]:
    with FakeStorageGateway("local", upload_limit_bytes=5 * MiB) as gateway:
        yield gateway


@pytest.fixture
def s3_gateway() -> Iterator[FakeStorageGateway]:
    with FakeStorageGateway("s3") as gateway:
        yield gateway


class TestLocalStorageUploadLimit:
    async def test_deck_record_is_stored_under_the_limit_and_reads_back(
        self, local_gateway: FakeStorageGateway, deck_record: dict
    ) -> None:
        packed = len(msgspec.msgpack.encode(deck_record))
        storage = _storage(local_gateway)

        lookup = await _store(storage, deck_record, "vr-deck")

        assert lookup["fileSizeBytes"] < local_gateway.upload_limit_bytes < packed
        assert local_gateway.rejected_uploads == 0
        assert await storage.get_record_from_storage("vr-deck", "org-1", lookup) == deck_record

    async def test_a_record_that_does_not_compress_is_still_refused(
        self, local_gateway: FakeStorageGateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The limit is Node's to enforce; this change only makes repeats smaller.

        The refusal must reach the indexing handler as a too-large rejection: that
        is what sets the record's reason and the too-large counter.
        """
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, "0")
        photos = {"blocks": [{"type": "image", "data": base64.b64encode(os.urandom(MiB)).decode()}
                             for _ in range(6)]}
        with pytest.raises(StorageRequestRejectedError) as refused:
            await _store(_storage(local_gateway), photos, "vr-photos")
        assert refused.value.too_large
        assert too_large_refusal(refused.value) is refused.value
        assert to_user_reason(refused.value) == FILE_TOO_LARGE
        assert local_gateway.rejected_uploads == 1, "a refusal is not retried"


class TestCloudStorageReads:
    async def test_concurrent_reads_decode_one_at_a_time_and_all_succeed(
        self, s3_gateway: FakeStorageGateway, deck_record: dict
    ) -> None:
        storage = _storage(s3_gateway)
        lookup = await _store(storage, deck_record, "vr-deck")

        inside = peak = 0
        threads: set[int] = set()
        real = storage._decompressed_payload

        def counting(data: dict) -> bytes:
            nonlocal inside, peak
            threads.add(threading.get_ident())
            inside += 1
            peak = max(peak, inside)
            try:
                return real(data)
            finally:
                inside -= 1

        with patch.object(storage, "_decompressed_payload", side_effect=counting):
            results = await asyncio.gather(
                *(storage.get_record_from_storage("vr-deck", "org-1", lookup) for _ in range(4))
            )

        assert all(r == deck_record for r in results)
        assert peak == 1
        assert threading.get_ident() not in threads, "decoded on the event loop"
        assert s3_gateway.downloads == 4

    async def test_record_over_the_ceiling_is_refused_after_one_download(
        self, s3_gateway: FakeStorageGateway, deck_record: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        storage = _storage(s3_gateway)
        big = await _store(storage, deck_record, "vr-deck")
        small_record = {"record_name": "notes.txt", "blocks": [{"data": "hello"}]}
        small = await _store(storage, small_record, "vr-small")
        monkeypatch.setenv(bs_mod._MAX_DECODED_BYTES_ENV, str(30 * MiB))

        refused, read = await asyncio.gather(
            storage.get_record_from_storage("vr-deck", "org-1", big),
            storage.get_record_from_storage("vr-small", "org-1", small),
            return_exceptions=True,
        )

        assert isinstance(refused, StoredRecordTooLargeError)
        assert read == small_record
        assert s3_gateway.downloads == 2
