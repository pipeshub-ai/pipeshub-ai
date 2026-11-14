"""A chat turn downloads and decodes each stored record once (get_record_from_storage)."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import BlobStorage
from app.utils import turn_memo

LOOKUP = {"record_doc_id": "doc-1", "fileSizeBytes": 10}


def _blob(fetch: AsyncMock) -> BlobStorage:
    blob = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())
    blob._get_auth_and_config = AsyncMock(return_value=({}, "http://storage", ""))
    blob._cached_signed_url = AsyncMock(return_value=None)
    blob._fetch_record_envelope = fetch
    return blob


@pytest.fixture(autouse=True)
def _no_session() -> Iterator[None]:
    with patch.object(bs_mod, "get_shared_session", return_value=MagicMock()):
        yield


def _envelope() -> dict:
    return {"record": {"id": "r1", "block_containers": {"blocks": [{"data": "x"}]}}}


async def _in_turn[T](coro_fn: Callable[[], Awaitable[T]]) -> T:
    async def run() -> T:
        turn_memo.begin_turn()
        return await coro_fn()
    return await asyncio.create_task(run())


@pytest.mark.asyncio
async def test_a_turn_downloads_each_record_once() -> None:
    fetch = AsyncMock(side_effect=lambda *a, **k: _envelope())
    blob = _blob(fetch)

    async def turn() -> list[dict | None]:
        first = await asyncio.gather(
            *(blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP) for _ in range(3))
        )
        later = await blob.get_record_from_storage("vr-1", "org-1", lookup_result=dict(LOOKUP))
        return [*first, later]

    records = await _in_turn(turn)
    assert fetch.await_count == 1
    # Callers set top-level keys on what they get; one caller's must not show in another's.
    records[0]["id"] = "other"
    assert [r["id"] for r in records[1:]] == ["r1", "r1", "r1"]
    assert len({id(r) for r in records}) == 4


@pytest.mark.asyncio
async def test_outside_a_turn_every_call_downloads() -> None:
    fetch = AsyncMock(side_effect=lambda *a, **k: _envelope())
    blob = _blob(fetch)
    await blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP)
    await blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP)
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_key_is_org_vrid_and_document() -> None:
    fetch = AsyncMock(side_effect=lambda *a, **k: _envelope())
    blob = _blob(fetch)

    async def turn() -> None:
        await blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP)
        await blob.get_record_from_storage("vr-1", "org-2", lookup_result=LOOKUP)
        await blob.get_record_from_storage("vr-1", "org-1", lookup_result={"record_doc_id": "doc-2"})
        await blob.get_record_from_storage("vr-2", "org-1", lookup_result=LOOKUP)

    await _in_turn(turn)
    assert fetch.await_count == 4


@pytest.mark.asyncio
async def test_a_failed_download_is_not_kept() -> None:
    fetch = AsyncMock(side_effect=[RuntimeError("storage down"), _envelope()])
    blob = _blob(fetch)

    async def turn() -> dict | None:
        with pytest.raises(RuntimeError):
            await blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP)
        return await blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP)

    record = await _in_turn(turn)
    assert record["id"] == "r1"
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_the_mapping_is_looked_up_when_not_given() -> None:
    fetch = AsyncMock(side_effect=lambda *a, **k: _envelope())
    blob = _blob(fetch)
    blob.get_document_id_by_virtual_record_id = AsyncMock(return_value=LOOKUP)

    async def turn() -> None:
        await blob.get_record_from_storage("vr-1", "org-1")
        await blob.get_record_from_storage("vr-1", "org-1", lookup_result=LOOKUP)

    await _in_turn(turn)
    assert blob.get_document_id_by_virtual_record_id.await_count == 1
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_no_mapping_returns_none_without_download() -> None:
    fetch = AsyncMock()
    blob = _blob(fetch)
    blob.get_document_id_by_virtual_record_id = AsyncMock(return_value=None)
    assert await _in_turn(lambda: blob.get_record_from_storage("vr-1", "org-1")) is None
    fetch.assert_not_awaited()
